"""Complete, reversible fixed-face states and packing for the budget solver."""

import copy
import hashlib
import logging
import math
from dataclasses import dataclass, field

import numpy as np
from shapely import GeometryCollection, LineString, MultiPolygon, Point, Polygon, STRtree, union_all
from shapely.geometry.base import BaseGeometry

from .joint_types import Correspondence, ReuseGroup
from .joint_validation import uv_barycentric
from .mesh import chart_labels, state_from_corner_uv
from .reuse_transform import mapped_normals, mapped_points, similarity_transform, transform_parity
from .types import FloatArray, IntArray, MeshState
from .validation import jacobians, topology_errors

_LOGGER = logging.getLogger(__name__)


def _positive_cell_vertices(cell: BaseGeometry) -> FloatArray:
    """All positive-area cell vertices, including GEOS mixed-dimensional results.

    Do not discard small polygon components. Points/lines are zero-area contacts;
    their authorization is checked by the caller before entering this function.
    """
    if isinstance(cell, Polygon):
        if cell.area <= 0:
            return np.empty((0, 2))
        rings = [np.asarray(cell.exterior.coords, dtype=float)[:-1]]
        rings.extend(np.asarray(ring.coords, dtype=float)[:-1] for ring in cell.interiors)
        return np.concatenate(rings)
    if isinstance(cell, (GeometryCollection, MultiPolygon)):
        parts = [_positive_cell_vertices(part) for part in cell.geoms]
        return np.concatenate(parts) if parts else np.empty((0, 2))
    if cell.area == 0:
        return np.empty((0, 2))
    raise ValueError("UNSUPPORTED_POSITIVE_INTERSECTION_GEOMETRY")


@dataclass
class BudgetState:
    surface: MeshState
    groups: tuple[ReuseGroup, ...] = ()
    correspondences: tuple[Correspondence, ...] = ()
    texture: FloatArray | None = None
    evidence: dict[str, object] = field(default_factory=dict)
    correspondence_records: tuple[dict[str, object], ...] = ()
    version: int = 0

    def copy(self) -> "BudgetState":
        return BudgetState(
            self.surface.copy(), self.groups, self.correspondences,
            None if self.texture is None else self.texture.copy(),
            copy.deepcopy(self.evidence), copy.deepcopy(self.correspondence_records), self.version,
        )

    def invalidate(self) -> None:
        self.texture = None
        self.evidence.clear()
        self.version += 1
        self.surface.version = self.version


def fingerprint(state: BudgetState) -> str:
    digest = hashlib.sha256()
    for array in (state.surface.mesh.vertices, state.surface.mesh.faces,
                  state.surface.uv, state.surface.uv_faces):
        digest.update(array.tobytes())
    digest.update(repr((sorted(state.surface.seams), state.groups, state.correspondences,
                        state.correspondence_records, state.version)).encode())
    if state.texture is not None:
        digest.update(state.texture.tobytes())
    return digest.hexdigest()


def owners_and_members(state: BudgetState) -> tuple[IntArray, IntArray]:
    members = chart_labels(state.surface)
    owners = members.copy()
    next_id = int(members.max()) + 1
    for group in state.groups:
        owners[list(group.first_faces + group.second_faces)] = next_id
        next_id += 1
    return owners, members


def contact_motion_ties(state: BudgetState) -> IntArray:
    """Authorize equal motion using original identity AND exact initial contact.

    This is an equality constraint on distinct UV copies, never a change of chi.
    Different owners or merely nearby UVs never authorize a tie.
    """
    owners, _ = owners_and_members(state)
    origin = np.full(len(state.surface.uv), -1, dtype=np.int64)
    owner = np.full(len(origin), -1, dtype=np.int64)
    for fi, face in enumerate(state.surface.uv_faces):
        for ci, uv_id in enumerate(face):
            original = int(state.surface.mesh.faces[fi, ci])
            if origin[uv_id] != -1 and (origin[uv_id] != original or owner[uv_id] != owners[fi]):
                raise ValueError("UV_COPY_ORIGINAL_IDENTITY_OR_OWNER_CONFLICT")
            origin[uv_id], owner[uv_id] = original, owners[fi]
    if np.any(origin < 0):
        raise ValueError("UNREFERENCED_UV_COPY")
    labels: dict[tuple[int, int, float, float], int] = {}
    return np.asarray([
        labels.setdefault((int(owner[i]), int(origin[i]), float(uv[0]), float(uv[1])), len(labels))
        for i, uv in enumerate(state.surface.uv)
    ], dtype=np.int64)


def orientations(state: BudgetState) -> IntArray:
    result = np.ones(len(state.surface.mesh.faces), dtype=np.int64)
    for group in state.groups:
        result[list(group.first_faces)] = group.first_orientation
        result[list(group.second_faces)] = group.second_orientation
    return result


def repair_independent_initialization(
    initial: MeshState, *, maximum_anisotropy: float = 2,
) -> tuple[BudgetState, dict[str, object]]:
    """Shared initializer contract: orient islands, isolate failed whole faces.

    xatlas may mirror complete charts or assign zero UVs to tiny triangles.
    Repairs are explicit and identical for every comparison method.
    """
    result = BudgetState(initial.copy())
    labels = chart_labels(result.surface)
    determinants = np.linalg.det(jacobians(result.surface))
    reflected = []
    for label in np.unique(labels):
        ids = np.flatnonzero(labels == label)
        if np.all(determinants[ids] < 0):
            vertices = np.unique(result.surface.uv_faces[ids])
            result.surface.uv[vertices, 0] *= -1
            reflected.append(int(label))
    jac = jacobians(result.surface)
    singular = np.linalg.svd(jac, compute_uv=False)
    ratio = singular[:, 0] / np.maximum(singular[:, 1], 1e-30)
    bad = tuple(map(int, np.flatnonzero((np.linalg.det(jac) <= 1e-12)
                                      | (ratio > maximum_anisotropy))))
    if bad:
        result = exclusive_faces(result, bad)
    return result, {"reflected_chart_ids": reflected, "isometric_exception_faces": list(bad),
                    "original_faces": len(initial.mesh.faces),
                    "geometry_changed": False, "initialization_contract_shared_by_all_methods": True}


def _exact_original_contact(surface: MeshState, first: int, second: int, cell: BaseGeometry) -> bool:
    """A zero-area contact must be contained in explicit identity contacts.

    No buffer, snap, coordinate rounding or distance tolerance is used. The
    original vertex identities survive a cut even when its UV copies differ.
    """
    allowed: list[BaseGeometry] = []
    shared_vertices = np.intersect1d(surface.mesh.faces[first], surface.mesh.faces[second])
    shared_points: list[FloatArray] = []
    for original in shared_vertices:
        a = int(np.flatnonzero(surface.mesh.faces[first] == original)[0])
        b = int(np.flatnonzero(surface.mesh.faces[second] == original)[0])
        uv_a = surface.uv[surface.uv_faces[first, a]]
        uv_b = surface.uv[surface.uv_faces[second, b]]
        if np.array_equal(uv_a, uv_b):
            allowed.append(Point(uv_a))
            shared_points.append(uv_a)
    if len(shared_vertices) == 2 and len(shared_points) == 2:
        allowed.append(LineString(shared_points))
    return bool(allowed) and cell.difference(union_all(allowed)).is_empty


def _budget_geometry_protocol() -> dict[str, object]:
    """Numerical conventions of the new budget contract, never retroactive."""
    return {
        "contract": "budget_geometry_exact_positive_area_cvar95_v1",
        "determinant_floor": 1e-12,
        "determinant_units": "UV squared per original area normalized by mesh.scale squared",
        "positive_overlap_area_floor": 0.0,
        "overlap_area_units": "normalized atlas UV squared",
        "contact_coordinate_tolerance": 0.0,
        "contact_coordinate_units": "normalized atlas UV; exact original identity required",
        "group_normal_length_tolerance": 1e-8,
        "correspondence_additive_tolerance": 1e-10,
        "correspondence_normalization": "original all-vertex bounding-box diagonal",
        "normal_angle_additive_tolerance_degrees": 1e-8,
        "packing_additive_tolerance_pixels": 1e-5,
        "quality_additive_tolerance": 1e-9,
        "quality_units": "dimensionless",
        "shape_definition": "0.5 * (kappa + 1 / kappa) - 1",
        "shape_weights": "original 3D face area / total original 3D face area",
        "shape_p95_definition": "left weighted quantile at cumulative original area 0.95",
        "shape_tail_definition": "area-weighted worst 0.05 mass; fractional boundary face",
        "shape_tail_mass": 0.05,
        "density_scope": "all_original_adjacency",
        "density_definition": "sqrt(abs(det J)); pairwise larger / smaller",
        "intersection_arithmetic": "GEOS floating precision; no snapping or area cutoff",
        "guarantee_scope": "checked finite-precision state; no exact-arithmetic or global bijection proof",
    }


def validate_budget_safety(
    state: BudgetState, resolution: int, *, gap_pixels: float = 4,
    border_pixels: float = 4, correspondence_fraction: float = 0.005,
    normal_angle_degrees: float = 45, independent_orientations: IntArray | None = None,
) -> dict[str, object]:
    """Hard safety H, independent of recoverable shape/anisotropy/density.

    Every positive-area triangle intersection is checked at its convex cell's
    vertices. Same-member and cross-owner overlap always fails, however small.
    The legacy V2 checker and its historical tolerances remain unchanged.
    """
    protocol = _budget_geometry_protocol()
    protocol.update(resolution=resolution, required_gap_pixels=gap_pixels,
                    required_border_pixels=border_pixels,
                    correspondence_fraction_limit=correspondence_fraction,
                    normal_angle_limit_degrees=normal_angle_degrees)

    def invalid(errors: list[str]) -> dict[str, object]:
        return {"valid": False, "errors": errors, "geometry_protocol": protocol,
                "quality_constraints_checked": False, "geometric_scope": "all_original_faces"}

    if (type(resolution) is not int or resolution <= 0
            or not all(math.isfinite(value) for value in
                       (gap_pixels, border_pixels, correspondence_fraction, normal_angle_degrees))
            or min(gap_pixels, border_pixels, correspondence_fraction) < 0
            or not 0 <= normal_angle_degrees <= 180 or 2 * border_pixels >= resolution):
        return invalid(["INVALID_BUDGET_GEOMETRY_PROFILE"])
    surface = state.surface
    errors = topology_errors(surface)
    if len(np.unique(surface.uv_faces)) != len(surface.uv):
        errors.append("UNREFERENCED_UV_COPY")
    if errors:
        return invalid(errors)
    count = len(surface.mesh.faces)
    labels = chart_labels(surface)
    owner = labels.copy()
    member = np.full(count, -1, dtype=np.int64)
    orientation = np.ones(count, dtype=np.int64)
    if independent_orientations is not None:
        if (independent_orientations.shape != (count,)
                or not np.isin(independent_orientations, (-1, 1)).all()
                or any(len(np.unique(independent_orientations[labels == label])) != 1
                       for label in np.unique(labels))):
            return invalid(["INVALID_INDEPENDENT_ORIENTATION"])
        orientation = independent_orientations.copy()
    assigned: set[int] = set()
    group_by_owner: dict[int, ReuseGroup] = {}
    next_owner = int(labels.max()) + 1
    ids: set[str] = set()
    for group in state.groups:
        all_faces = group.first_faces + group.second_faces
        normal = np.asarray(group.plane_normal, dtype=float)
        if group.first_to_second is not None:
            try:
                similarity_transform(group.first_to_second)
            except (ValueError, TypeError):
                return invalid(["INVALID_REUSE_SIMILARITY"])
            if group.second_orientation != group.first_orientation * transform_parity(group.first_to_second):
                return invalid(["REUSE_SIMILARITY_ORIENTATION_MISMATCH"])
        if (group.group_id in ids or not group.first_faces or not group.second_faces
                or len(set(all_faces)) != len(all_faces) or min(all_faces) < 0 or max(all_faces) >= count
                or assigned.intersection(all_faces) or normal.shape != (3,)
                or not np.isfinite(normal).all() or abs(float(np.linalg.norm(normal)) - 1) > 1e-8
                or not np.isfinite(group.plane_offset) or group.first_orientation not in (-1, 1)
                or group.second_orientation not in (-1, 1)):
            return invalid(["INVALID_REUSE_GROUP"])
        ids.add(group.group_id)
        assigned.update(all_faces)
        for side, (faces, sign) in enumerate(((group.first_faces, group.first_orientation),
                                              (group.second_faces, group.second_orientation))):
            chart_faces = set(map(int, np.flatnonzero(np.isin(labels, np.unique(labels[list(faces)])))))
            if chart_faces != set(faces):
                return invalid(["REUSE_MEMBER_NOT_ISOLATED"])
            owner[list(faces)] = next_owner
            member[list(faces)] = side
            orientation[list(faces)] = sign
        group_by_owner[next_owner] = group
        next_owner += 1
    jac = jacobians(surface)
    determinant = np.linalg.det(jac) * orientation
    if not np.isfinite(jac).all() or np.any(determinant <= 1e-12):
        return invalid(["FLIPPED_OR_DEGENERATE"])
    corners = surface.uv[surface.uv_faces]
    polygons = [Polygon(triangle) for triangle in corners]
    pairs = STRtree(polygons).query(polygons, predicate="intersects")
    triangles = surface.mesh.vertices[surface.mesh.faces]
    normals = np.cross(triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0])
    normals /= np.linalg.norm(normals, axis=1)[:, None]
    diagonal = float(np.linalg.norm(np.ptp(surface.mesh.vertices, axis=0)))
    if not math.isfinite(diagonal) or diagonal <= 0:
        return invalid(["INVALID_ORIGINAL_BOUNDING_BOX"])
    max_error, max_angle = 0.0, 0.0
    max_forward_error, max_inverse_error = 0.0, 0.0
    authorized_area = 0.0
    overlap_cells = 0
    positive_cells = 0
    group_areas = {group.group_id: 0.0 for group in state.groups}
    for first_raw, second_raw in zip(*pairs, strict=True):
        first, second = int(first_raw), int(second_raw)
        if first >= second:
            continue
        cell = polygons[first].intersection(polygons[second])
        authorized_members = (owner[first] == owner[second] and member[first] >= 0
                              and member[second] >= 0 and member[first] != member[second])
        if cell.area == 0:
            if not (authorized_members or _exact_original_contact(surface, first, second, cell)):
                errors.append(f"UNAUTHORIZED_CONTACT:{first}:{second}")
            continue
        positive_cells += 1
        if not authorized_members:
            errors.append(f"UNAUTHORIZED_OVERLAP:{first}:{second}")
            continue
        uv = _positive_cell_vertices(cell)
        if not len(uv):
            errors.append(f"INVALID_INTERSECTION_CELL:{first}:{second}")
            continue
        group = group_by_owner[int(owner[first])]
        point_a = uv_barycentric(corners[first], uv) @ triangles[first]
        point_b = uv_barycentric(corners[second], uv) @ triangles[second]
        normal = np.asarray(group.plane_normal)
        inverse = member[first] == 1
        error = float(np.linalg.norm(mapped_points(point_a, normal, group.plane_offset,
            group.first_to_second, inverse=bool(inverse)) - point_b, axis=1).max())
        reverse_error = error
        if group.first_to_second is not None:
            reverse_error = float(np.linalg.norm(mapped_points(point_b, normal, group.plane_offset,
                group.first_to_second, inverse=not bool(inverse)) - point_a, axis=1).max())
        forward_error, inverse_error = (reverse_error, error) if inverse else (error, reverse_error)
        max_forward_error = max(max_forward_error, forward_error / diagonal)
        max_inverse_error = max(max_inverse_error, inverse_error / diagonal)
        error = max(error, reverse_error)
        error /= diagonal
        angle = float(np.degrees(np.arccos(np.clip(
            mapped_normals(normals[first], normal, group.first_to_second,
                           inverse=bool(inverse)) @ normals[second], -1, 1))))
        max_error, max_angle = max(max_error, error), max(max_angle, angle)
        if error > correspondence_fraction + 1e-10 or angle > normal_angle_degrees + 1e-8:
            errors.append(f"OVERLAP_CORRESPONDENCE:{first}:{second}")
        else:
            area = float(cell.area)
            authorized_area += area
            group_areas[group.group_id] += area
            overlap_cells += 1
    shapes = [union_all([polygons[int(i)] for i in np.flatnonzero(owner == label)])
              for label in np.unique(owner)]
    gap = float("inf")
    for i, shape in enumerate(shapes):
        for other in shapes[i + 1:]:
            gap = min(gap, float(shape.distance(other)) * resolution)
    border = float(min(surface.uv.min(), (1 - surface.uv).min()) * resolution)
    if gap < gap_pixels - 1e-5 or border < border_pixels - 1e-5:
        errors.append("PACKING_GAP_OR_BORDER")
    unique = float(union_all(polygons).area)
    a, b = corners[:, 1] - corners[:, 0], corners[:, 2] - corners[:, 0]
    total = float(np.abs(a[:, 0] * b[:, 1] - a[:, 1] * b[:, 0]).sum() / 2)
    active = sorted(group_id for group_id, area in group_areas.items() if area > 0)
    return {
        "valid": not errors, "errors": errors, "geometry_protocol": protocol,
        "quality_constraints_checked": False, "geometric_scope": "all_original_faces",
        "unique_coverage": unique, "summed_uv_area": total,
        "reuse_factor": total / unique if unique else None,
        "authorized_overlap_area": authorized_area, "overlap_cells": overlap_cells,
        "positive_overlap_cells": positive_cells, "has_authorized_shared_overlap": authorized_area > 0,
        "nominal_reuse_groups": len(state.groups), "active_reuse_groups": len(active),
        "active_reuse_group_ids": active, "group_authorized_overlap_area": group_areas,
        "max_overlap_geometry_error": max_error, "maximum_overlap_normal_angle_degrees": max_angle,
        "maximum_forward_overlap_error_fraction": max_forward_error,
        "maximum_inverse_overlap_error_fraction": max_inverse_error,
        "minimum_oriented_determinant": float(determinant.min()),
        "minimum_gap_pixels": gap if math.isfinite(gap) else None,
        "minimum_border_pixels": border, "packing_groups": len(shapes),
    }


def validate_budget_geometry(
    state: BudgetState, resolution: int, *, gap_pixels: float = 4,
    border_pixels: float = 4, maximum_anisotropy: float = 2,
    mean_shape_limit: float = 0.025, maximum_density_ratio: float = 2,
    correspondence_fraction: float = 0.005, normal_angle_degrees: float = 45,
    tail_shape_limit: float = 0.1, quality_constraints: bool = True,
    independent_orientations: IntArray | None = None,
) -> dict[str, object]:
    """Final budget geometry: hard safety plus area-weighted quality limits."""
    geometry = validate_budget_safety(
        state, resolution, gap_pixels=gap_pixels, border_pixels=border_pixels,
        correspondence_fraction=correspondence_fraction, normal_angle_degrees=normal_angle_degrees,
        independent_orientations=independent_orientations)
    if not quality_constraints:
        return geometry
    errors = geometry["errors"]
    assert isinstance(errors, list)
    if (not all(math.isfinite(value) for value in
                (maximum_anisotropy, mean_shape_limit, maximum_density_ratio, tail_shape_limit))
            or min(maximum_anisotropy, maximum_density_ratio) < 1
            or min(mean_shape_limit, tail_shape_limit) < 0):
        errors.append("INVALID_BUDGET_QUALITY_PROFILE")
        geometry.update(valid=False)
        return geometry
    # Early safety failures have no meaningful nondegenerate Jacobian metrics.
    if "minimum_oriented_determinant" not in geometry:
        return geometry
    singular = np.linalg.svd(jacobians(state.surface), compute_uv=False)
    ratio = singular[:, 0] / singular[:, 1]
    shape = (ratio + 1 / ratio) / 2 - 1
    weights = state.surface.mesh.areas / state.surface.mesh.areas.sum()
    mean_shape = float(weights @ shape)
    ordered = np.argsort(shape, kind="stable")
    index = min(int(np.searchsorted(np.cumsum(weights[ordered]), 0.95)), len(shape) - 1)
    p95 = float(shape[ordered[index]])
    descending = ordered[::-1]
    before = np.cumsum(weights[descending]) - weights[descending]
    tail_weights = np.minimum(weights[descending], np.maximum(0.05 - before, 0))
    cvar95 = float(tail_weights @ shape[descending] / 0.05)
    failed = set(map(int, np.flatnonzero(ratio > maximum_anisotropy + 1e-9)))
    if failed:
        errors.append("ANISOTROPY_LIMIT")
    if mean_shape > mean_shape_limit + 1e-9:
        errors.append("MEAN_SHAPE_LIMIT")
    if cvar95 > tail_shape_limit + 1e-9:
        errors.append("TAIL_SHAPE_LIMIT")
        failed.update(map(int, descending[tail_weights > 0]))
    determinant = np.abs(np.linalg.det(jacobians(state.surface)))
    density_ratio = 1.0
    for entries in state.surface.mesh.edge_faces:
        if len(entries) == 2:
            a, b = entries[0][0], entries[1][0]
            value = float(np.sqrt(max(determinant[a], determinant[b]) / min(determinant[a], determinant[b])))
            density_ratio = max(density_ratio, value)
            if value > maximum_density_ratio + 1e-9:
                failed.update((a, b))
    if density_ratio > maximum_density_ratio + 1e-9:
        errors.append("NEIGHBOR_DENSITY_LIMIT")
    geometry.update(valid=not errors, errors=errors, maximum_anisotropy=float(ratio.max()),
                    mean_shape_distortion=mean_shape, shape_p95=p95, shape_cvar95=cvar95,
                    shape_max=float(shape.max()), face_shape_distortion=shape.tolist(),
                    face_anisotropy=ratio.tolist(), maximum_neighbor_density_ratio=density_ratio,
                    failed_faces=sorted(failed), quality_constraints_checked=True,
                    mean_shape_limit=mean_shape_limit, tail_shape_limit=tail_shape_limit,
                    anisotropy_limit=maximum_anisotropy, neighbor_density_limit=maximum_density_ratio)
    protocol = geometry["geometry_protocol"]
    assert isinstance(protocol, dict)
    protocol.update(mean_shape_limit=mean_shape_limit, tail_shape_limit=tail_shape_limit,
                    maximum_anisotropy=maximum_anisotropy, maximum_density_ratio=maximum_density_ratio)
    return geometry


def embed_region(
    base: BudgetState, local: MeshState, face_ids: tuple[int, ...],
    group: ReuseGroup | None, constraints: tuple[Correspondence, ...] = (),
) -> BudgetState:
    """Replace only UV of identified original faces, recutting on original edges."""
    ids = np.asarray(face_ids, dtype=np.int64)
    if (len(ids) != len(local.mesh.faces) or len(set(face_ids)) != len(ids) or len(ids) == 0
            or ids.min() < 0 or ids.max() >= len(base.surface.mesh.faces)
            or not np.array_equal(base.surface.mesh.faces[ids], local.mesh.faces)
            or not np.array_equal(base.surface.mesh.vertices, local.mesh.vertices)):
        raise ValueError("REGION_ORIGINAL_IDENTITY_MISMATCH")
    if any(set(face_ids).intersection(g.first_faces + g.second_faces) for g in base.groups):
        raise ValueError("REGION_CROSSES_EXISTING_GROUP")
    corners = base.surface.uv[base.surface.uv_faces].copy()
    identity = base.surface.uv_faces.copy()
    corners[ids] = local.uv[local.uv_faces]
    identity[ids] = local.uv_faces + len(base.surface.uv)
    result = base.copy()
    result.surface = state_from_corner_uv(base.surface.mesh, corners, corner_ids=identity)
    if group is not None:
        if set(group.first_faces + group.second_faces) != set(face_ids):
            raise ValueError("GROUP_FACE_COVERAGE_MISMATCH")
        result.groups += (group,)
        result.correspondences += tuple(
            Correspondence(face_ids[c.source_face], c.source_barycentric,
                           face_ids[c.target_face], c.target_barycentric, c.weight)
            for c in constraints
        )
    result.invalidate()
    return result


def exclusive_faces(state: BudgetState, face_ids: tuple[int, ...]) -> BudgetState:
    """Original whole-face exceptions; no original face is removed or added."""
    released = set(face_ids)
    if not released or min(released) < 0 or max(released) >= len(state.surface.mesh.faces):
        raise ValueError("INVALID_EXCLUSIVE_FACE_IDS")
    result = state.copy()
    corners = result.surface.uv[result.surface.uv_faces].copy()
    identity = result.surface.uv_faces.copy()
    next_id = int(identity.max()) + 1
    old_signs = orientations(result)
    # Each released face is an isometric island; subsequent original-edge merges
    # may restore a larger chart. This is a conservative, finite exception move.
    for fi in sorted(released):
        triangle = result.surface.mesh.vertices[result.surface.mesh.faces[fi]]
        a, b = triangle[1] - triangle[0], triangle[2] - triangle[0]
        length = float(np.linalg.norm(a))
        corners[fi] = ((0, 0), (length, 0),
                       (float(a @ b / length), float(np.linalg.norm(np.cross(a, b)) / length)))
        identity[fi] = np.arange(next_id, next_id + 3)
        next_id += 3
    groups = []
    removed_group_faces: set[int] = set()
    for group in result.groups:
        first = tuple(f for f in group.first_faces if f not in released)
        second = tuple(f for f in group.second_faces if f not in released)
        if first and second:
            groups.append(ReuseGroup(group.group_id, first, second, group.plane_normal,
                                     group.plane_offset, group.first_orientation, group.second_orientation,
                                     group.first_to_second))
        else:
            removed_group_faces.update(first + second)
    # Dissolved mirrored members become independent, positive-orientation charts.
    for fi in removed_group_faces:
        if old_signs[fi] < 0:
            corners[fi, :, 0] *= -1
    result.surface = state_from_corner_uv(result.surface.mesh, corners, corner_ids=identity)
    result.groups = tuple(groups)
    result.correspondences = tuple(c for c in result.correspondences
                                   if c.source_face not in released | removed_group_faces
                                   and c.target_face not in released | removed_group_faces)
    records, cleanup = _retained_correspondence_records(result, state.groups)
    result.correspondence_records = records
    result.invalidate()
    if cleanup["removed_count"]:
        # This is an invalidation log, not reusable material/geometry evidence.
        result.evidence["correspondence_record_cleanup"] = cleanup
        _LOGGER.info("exclusive_faces correspondence record invalidation: %s", cleanup)
    return result


def _retained_correspondence_records(
    state: BudgetState, previous_groups: tuple[ReuseGroup, ...],
) -> tuple[tuple[dict[str, object], ...], dict[str, object]]:
    """Keep associations only when their complete group domain is unchanged.

    Both recorded association schemas are recognized. A membership edit can
    change UV containment or reverse projection without moving the remaining
    endpoints, so all records for that edited domain require reconstruction.
    """
    current = {group.group_id: group for group in state.groups}
    unchanged = {group.group_id for group in previous_groups if current.get(group.group_id) == group}
    associations = {(item.source_face, item.target_face) for item in state.correspondences}
    records: list[dict[str, object]] = []
    reasons: dict[str, int] = {}
    for record in state.correspondence_records:
        group_id = record.get("group_id", record.get("group"))
        source = record.get("source_face", record.get("source_original_face_id"))
        target = record.get("target_face", record.get("target_original_face_id"))
        reason: str | None = None
        if not isinstance(group_id, str) or type(source) is not int or type(target) is not int:
            reason = "UNSUPPORTED_ASSOCIATION_RECORD_SCHEMA"
        elif group_id not in unchanged:
            reason = "REUSE_GROUP_DOMAIN_CHANGED_OR_DISSOLVED"
        else:
            group = current[group_id]
            source_member = group.first_faces if source in group.first_faces else group.second_faces
            target_member = group.second_faces if source in group.first_faces else group.first_faces
            round_trip = record.get("round_trip_source_face", source)
            alternate_targets = record.get("target_face_ids", [target])
            if (source not in source_member or target not in target_member
                    or (source, target) not in associations or type(round_trip) is not int
                    or round_trip not in source_member or not isinstance(alternate_targets, (list, tuple))
                    or any(type(face) is not int or face not in target_member for face in alternate_targets)):
                reason = "ASSOCIATION_NOT_SUPPORTED_BY_SURVIVING_DOMAIN"
        if reason is None:
            records.append(record)
        else:
            reasons[reason] = reasons.get(reason, 0) + 1
    return tuple(records), {
        "operation": "exclusive_faces", "removed_count": sum(reasons.values()),
        "retained_count": len(records), "reason_counts": reasons,
        "rebuild_requirement": "recompute associations for changed reuse group domains",
    }


def planar_merge(state: BudgetState, edge_id: int) -> BudgetState:
    """Delete one original seam and reparameterize both incident exclusive charts."""
    mesh = state.surface.mesh
    if edge_id not in state.surface.seams or len(mesh.edge_faces[edge_id]) != 2:
        raise ValueError("NOT_A_REMOVABLE_SEAM")
    labels = chart_labels(state.surface)
    a, b = (entry[0] for entry in mesh.edge_faces[edge_id])
    faces = tuple(map(int, np.flatnonzero(np.isin(labels, (labels[a], labels[b])))))
    if any(set(faces).intersection(g.first_faces + g.second_faces) for g in state.groups):
        raise ValueError("MERGE_CROSSES_REUSE_MEMBER")
    corners = state.surface.uv[state.surface.uv_faces].copy()
    points = mesh.vertices[mesh.faces[list(faces)]]
    normals = np.cross(points[:, 1] - points[:, 0], points[:, 2] - points[:, 0])
    normal = normals.sum(axis=0)
    if np.linalg.norm(normal) <= 1e-15:
        raise ValueError("NO_COMMON_PLANE")
    normal /= np.linalg.norm(normal)
    axis = np.eye(3)[int(np.argmin(np.abs(normal)))]
    axis -= normal * (axis @ normal)
    axis /= np.linalg.norm(axis)
    basis = np.column_stack((axis, np.cross(normal, axis)))
    corners[list(faces)] = (points - points.mean(axis=(0, 1))) @ basis / mesh.scale
    # Remove all internal seams of the union; boundary seams stay stable IDs.
    selected = set(faces)
    seams = frozenset(e for e in state.surface.seams
                      if not all(f in selected for f, _ in mesh.edge_faces[e]))
    result = state.copy()
    result.surface = state_from_corner_uv(mesh, corners, seam_ids=seams)
    result.invalidate()
    return result


def pack_budget_state(
    state: BudgetState, resolution: int, *, gap_pixels: float = 4,
    border_pixels: float = 4, density_normalize: bool = True,
) -> BudgetState:
    """Deterministic common-scale guillotine rectangles; no quality claims."""
    if resolution <= 2 * border_pixels or min(gap_pixels, border_pixels) < 0:
        raise ValueError("INVALID_PACKING_PROFILE")
    result = state.copy()
    owner, _ = owners_and_members(result)
    groups = [np.unique(result.surface.uv_faces[owner == o]) for o in np.unique(owner)]
    if sum(map(len, groups)) != len(result.surface.uv):
        raise ValueError("UV_IDENTITIES_CROSS_PACKING_GROUPS")
    canonical = result.surface.uv.copy()
    for o, ids in zip(np.unique(owner), groups, strict=True):
        if density_normalize:
            faces = result.surface.uv_faces[owner == o]
            tri = canonical[faces]
            p, q = tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0]
            area = float(np.abs(p[:, 0] * q[:, 1] - p[:, 1] * q[:, 0]).sum() / 2)
            canonical[ids] *= np.sqrt(result.surface.mesh.areas[owner == o].sum() / max(area, 1e-30))
        canonical[ids] -= canonical[ids].min(axis=0)
    sizes = [np.ptp(canonical[ids], axis=0) for ids in groups]
    if any(np.any(size <= 1e-15) for size in sizes):
        raise ValueError("DEGENERATE_PACKING_GROUP")
    order = sorted(range(len(groups)), key=lambda i: (-float(np.prod(sizes[i])), -float(max(sizes[i])), i))
    interior = resolution - 2 * border_pixels

    def fit(scale: float) -> dict[int, tuple[float, float, bool]] | None:
        free = [(0.0, 0.0, interior + gap_pixels, interior + gap_pixels)]
        poses: dict[int, tuple[float, float, bool]] = {}
        for i in order:
            choices = []
            for j, (x, y, w, h) in enumerate(free):
                for rotate in (False, True):
                    extent = sizes[i][::-1] if rotate else sizes[i]
                    width, height = scale * extent + gap_pixels
                    if width <= w + 1e-9 and height <= h + 1e-9:
                        choices.append((w * h - width * height, min(w - width, h - height),
                                        j, rotate, float(width), float(height)))
            if not choices:
                return None
            _, _, j, rotate, width, height = min(choices)
            x, y, w, h = free.pop(j)
            poses[i] = (x + border_pixels, y + border_pixels, rotate)
            # Disjoint guillotine free rectangles.
            if w - width > 1e-9:
                free.append((x + width, y, w - width, height))
            if h - height > 1e-9:
                free.append((x, y + height, w, h - height))
        return poses

    upper = interior / max(float(max(s)) for s in sizes)
    best: tuple[float, dict[int, tuple[float, float, bool]]] | None = None
    # Finite scale scan; no monotonicity or optimality of this heuristic assumed.
    for scale in upper * np.geomspace(1.0, 1e-4, 90):
        poses = fit(float(scale))
        if poses is not None:
            best = (float(scale), poses)
            break
    if best is None:
        raise ValueError("PACKING_NUMERICAL_FAILURE")
    scale, poses = best
    for i, ids in enumerate(groups):
        x, y, rotate = poses[i]
        coordinates = canonical[ids].copy()
        if rotate:
            coordinates = np.column_stack((sizes[i][1] - coordinates[:, 1], coordinates[:, 0]))
        result.surface.uv[ids] = (coordinates * scale + (x, y)) / resolution
    result.invalidate()
    return result
