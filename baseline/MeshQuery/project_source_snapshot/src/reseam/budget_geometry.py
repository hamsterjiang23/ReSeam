"""Independent geometric acceptance for the budget-driven UV prototype.

This module checks actual exported corner UVs. It does not apply legacy density
floors, optimize UVs, or certify texture, mip, or material compatibility.
"""

import math

import numpy as np
from shapely import Polygon, STRtree, union_all

from .joint_contacts import sheet_intersections
from .joint_validation import uv_barycentric
from .mesh import chart_labels
from .surface_match import reflect
from .types import IntArray, MeshState
from .validation import jacobians, topology_errors


def _invalid(errors: list[str]) -> dict[str, object]:
    return {"valid": False, "errors": errors, "quality_scope": "GEOMETRY_ONLY"}


def validate_pair_geometry(
    state: MeshState,
    orientations: IntArray,
    member_ids: IntArray,
    *,
    shared: bool,
    plane_normal: tuple[float, float, float],
    plane_offset: float,
    resolution: int,
    gap_pixels: float = 2,
    border_pixels: float = 2,
    correspondence_fraction: float = 0.005,
    normal_angle_degrees: float = 45,
    maximum_anisotropy: float = 2,
    mean_shape_limit: float = 0.025,
    maximum_neighbor_density_ratio: float = 2,
    contact_classes: tuple[int, ...] | None = None,
) -> dict[str, object]:
    """Validate two members, allowing only explicitly compatible shared cells.

    The model bounding box includes all original vertices, including unused ROI
    vertices. This preserves the same normalized correspondence scale as the
    parent mesh. ``shared=True`` is permission to test cross-member overlaps,
    not evidence that any usable shared overlap exists.
    """
    count = len(state.mesh.faces)
    signs = np.asarray(orientations)
    members = np.asarray(member_ids)
    normal = np.asarray(plane_normal, dtype=float)
    scalar_values = (
        gap_pixels,
        border_pixels,
        correspondence_fraction,
        normal_angle_degrees,
        maximum_anisotropy,
        mean_shape_limit,
        maximum_neighbor_density_ratio,
        plane_offset,
    )
    if (
        signs.shape != (count,)
        or not np.isin(signs, (-1, 1)).all()
        or members.shape != (count,)
        or not np.issubdtype(members.dtype, np.integer)
        or len(np.unique(members)) != 2
        or normal.shape != (3,)
        or not np.isfinite(normal).all()
        or abs(float(np.linalg.norm(normal)) - 1) > 1e-8
        or type(shared) is not bool
        or type(resolution) is not int
        or resolution <= 0
        or not all(math.isfinite(float(value)) for value in scalar_values)
        or min(gap_pixels, border_pixels, correspondence_fraction, mean_shape_limit) < 0
        or not 0 <= normal_angle_degrees <= 180
        or maximum_anisotropy < 1
        or maximum_neighbor_density_ratio < 1
        or border_pixels * 2 >= resolution
    ):
        return _invalid(["INVALID_PAIR_GEOMETRY_PROFILE_OR_MEMBERS"])

    errors = topology_errors(state)
    if errors:
        return _invalid(errors)
    labels = chart_labels(state)
    for label in np.unique(labels):
        if len(np.unique(members[labels == label])) != 1:
            errors.append(f"MEMBER_NOT_ISOLATED:{int(label)}")
    for member in np.unique(members):
        if len(np.unique(signs[members == member])) != 1:
            errors.append(f"INCONSISTENT_MEMBER_ORIENTATION:{int(member)}")
    if errors:
        return _invalid(errors)

    jac = jacobians(state)
    determinants = np.linalg.det(jac)
    if not np.isfinite(jac).all() or np.any(determinants * signs <= 0):
        return _invalid(["FLIPPED_OR_DEGENERATE"])
    singular = np.linalg.svd(jac, compute_uv=False)
    if np.any(singular[:, 1] <= np.finfo(float).tiny):
        return _invalid(["FLIPPED_OR_DEGENERATE"])
    anisotropy = singular[:, 0] / singular[:, 1]
    shape = 0.5 * (anisotropy + 1 / anisotropy) - 1
    weights = state.mesh.areas / state.mesh.areas.sum()
    mean_shape = float(weights @ shape)
    max_anisotropy = float(anisotropy.max())
    if max_anisotropy > maximum_anisotropy + 1e-10:
        errors.append("ANISOTROPY_LIMIT")
    if mean_shape > mean_shape_limit + 1e-10:
        errors.append("MEAN_SHAPE_LIMIT")
    errors.extend(sheet_intersections(state, members, contact_classes))

    corners = state.uv[state.uv_faces]
    edge_a, edge_b = corners[:, 1] - corners[:, 0], corners[:, 2] - corners[:, 0]
    uv_cross = edge_a[:, 0] * edge_b[:, 1] - edge_a[:, 1] * edge_b[:, 0]
    uv_areas = 0.5 * np.abs(uv_cross)
    density = resolution * np.sqrt(uv_areas / state.mesh.areas)
    max_density_ratio = 1.0
    density_failed_faces: set[int] = set()
    for entries in state.mesh.edge_faces:
        if len(entries) != 2:
            continue
        first, second = entries[0][0], entries[1][0]
        if members[first] != members[second]:
            continue
        ratio = float(max(density[first], density[second]) / min(density[first], density[second]))
        max_density_ratio = max(max_density_ratio, ratio)
        if ratio > maximum_neighbor_density_ratio + 1e-10:
            density_failed_faces.update((first, second))
    if density_failed_faces:
        errors.append("NEIGHBOR_DENSITY_LIMIT")

    polygons = [Polygon(triangle) for triangle in corners]
    pairs = STRtree(polygons).query(polygons, predicate="intersects")
    triangles = state.mesh.vertices[state.mesh.faces]
    face_normals = np.cross(triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0])
    face_normals /= np.linalg.norm(face_normals, axis=1)[:, None]
    diagonal = float(np.linalg.norm(np.ptp(state.mesh.vertices, axis=0)))
    if not math.isfinite(diagonal) or diagonal <= 0:
        return _invalid(["INVALID_ORIGINAL_BOUNDING_BOX"])
    overlap_cells = 0
    authorized_area = 0.0
    max_overlap_error = 0.0
    max_overlap_angle = 0.0
    for first_raw, second_raw in zip(*pairs, strict=True):
        first, second = int(first_raw), int(second_raw)
        if first >= second or members[first] == members[second]:
            continue
        cell = polygons[first].intersection(polygons[second])
        if not shared:
            errors.append(f"UNAUTHORIZED_MEMBER_CONTACT:{first}:{second}")
            continue
        if cell.area <= 0:
            continue
        if not isinstance(cell, Polygon):
            errors.append(f"INVALID_OVERLAP_CELL:{first}:{second}")
            continue
        overlap_cells += 1
        points = np.asarray(cell.exterior.coords, dtype=float)[:-1]
        point_a = uv_barycentric(corners[first], points) @ triangles[first]
        point_b = uv_barycentric(corners[second], points) @ triangles[second]
        distance = float(np.linalg.norm(reflect(point_a, normal, plane_offset) - point_b, axis=1).max())
        error = distance / diagonal
        angle = float(
            np.degrees(
                np.arccos(np.clip(reflect(face_normals[first], normal, 0) @ face_normals[second], -1, 1))
            )
        )
        max_overlap_error = max(max_overlap_error, error)
        max_overlap_angle = max(max_overlap_angle, angle)
        if error > correspondence_fraction + 1e-10 or angle > normal_angle_degrees + 1e-8:
            errors.append(f"OVERLAP_CORRESPONDENCE:{first}:{second}")
        else:
            authorized_area += float(cell.area)

    border = float(min(corners.min(), (1 - corners).min()) * resolution)
    if border < border_pixels - 1e-8:
        errors.append("ATLAS_BORDER_LIMIT")
    member_shapes = [
        union_all([polygons[int(i)] for i in np.flatnonzero(members == member)])
        for member in np.unique(members)
    ]
    gap = None if shared else float(member_shapes[0].distance(member_shapes[1]) * resolution)
    if gap is not None and gap < gap_pixels - 1e-8:
        errors.append("PACKING_GAP_LIMIT")
    unique_area = float(union_all(polygons).area)
    return {
        "valid": not errors,
        "errors": errors,
        "quality_scope": "GEOMETRY_ONLY",
        "mean_shape_distortion": mean_shape,
        "maximum_anisotropy": max_anisotropy,
        "face_shape_distortion": shape.tolist(),
        "face_anisotropy": anisotropy.tolist(),
        "face_pixel_density": density.tolist(),
        "maximum_neighbor_density_ratio": max_density_ratio,
        "neighbor_density_failed_faces": sorted(density_failed_faces),
        "minimum_oriented_determinant": float((determinants * signs).min()),
        "minimum_gap_pixels": gap,
        "minimum_border_pixels": border,
        "unique_uv_area": unique_area,
        "summed_uv_area": float(uv_areas.sum()),
        "reuse_factor": float(uv_areas.sum() / unique_area) if unique_area > 0 else None,
        "overlap_cells": overlap_cells,
        "authorized_overlap_area": authorized_area,
        "has_authorized_shared_overlap": authorized_area > 0,
        "maximum_overlap_geometry_error": max_overlap_error,
        "maximum_overlap_normal_angle_degrees": max_overlap_angle,
        "packing_groups": 1 if shared else 2,
    }
