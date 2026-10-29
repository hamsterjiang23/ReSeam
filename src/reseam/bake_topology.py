"""Conservative upstream edge flips for independently identified bake variants.

No UV, material or geometric correspondence is transferred by this module.
Local quality and manifold checks do not certify global self-intersection or
downstream bake quality. Coincident vertices and short-edge collapse remain
unchanged and are reported explicitly.
"""

import hashlib
import math
import time
from dataclasses import asdict, dataclass

import numpy as np

from .mesh import make_mesh
from .types import FloatArray, Mesh


@dataclass(frozen=True)
class BakeTopologyOptions:
    max_passes: int = 4
    operation_budget: int = 100
    minimum_angle_goal_degrees: float = 20
    feature_angle_degrees: float = 15
    normal_change_degrees: float = 15
    minimum_improvement_degrees: float = 1e-6
    relative_area_floor: float = 1e-12
    maximum_local_area_change_fraction: float = .01

    def __post_init__(self) -> None:
        values = (self.minimum_angle_goal_degrees, self.feature_angle_degrees,
                  self.normal_change_degrees, self.minimum_improvement_degrees,
                  self.relative_area_floor, self.maximum_local_area_change_fraction)
        if (type(self.max_passes) is not int or self.max_passes < 0
                or type(self.operation_budget) is not int or self.operation_budget < 0
                or not all(math.isfinite(value) for value in values)
                or not 0 < self.minimum_angle_goal_degrees <= 60
                or not 0 <= self.feature_angle_degrees < 90
                or not 0 <= self.normal_change_degrees < 90
                or self.minimum_improvement_degrees <= 0 or self.relative_area_floor <= 0
                or not 0 <= self.maximum_local_area_change_fraction < 1):
            raise ValueError("INVALID_BAKE_TOPOLOGY_OPTIONS")


@dataclass(frozen=True)
class BakeTopologyResult:
    mesh: Mesh
    operations: tuple[dict[str, object], ...]
    before: dict[str, object]
    after: dict[str, object]
    diagnostics: dict[str, object]
    runtime_seconds: float


_DEFAULT_OPTIONS = BakeTopologyOptions()


@dataclass(frozen=True)
class _TriangleQuality:
    minimum_angles: FloatArray
    shape_r: FloatArray
    areas: FloatArray
    normals: FloatArray


def _triangle_quality(triangles: FloatArray) -> _TriangleQuality:
    edges = np.roll(triangles, -1, axis=1) - triangles
    cross = np.cross(triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0])
    lengths = np.linalg.norm(cross, axis=1)
    areas = lengths / 2
    normals = np.divide(cross, lengths[:, None], out=np.zeros_like(cross), where=lengths[:, None] > 0)
    angles = []
    for corner in range(3):
        first = triangles[:, (corner + 1) % 3] - triangles[:, corner]
        second = triangles[:, (corner + 2) % 3] - triangles[:, corner]
        angles.append(np.degrees(np.arctan2(np.linalg.norm(np.cross(first, second), axis=1),
                                           np.sum(first * second, axis=1))))
    numerator = np.sum(edges**2, axis=(1, 2))
    shape_r = np.divide(numerator, 4 * np.sqrt(3) * areas,
                        out=np.full(len(areas), np.inf), where=areas > 0)
    return _TriangleQuality(np.min(angles, axis=0), shape_r, areas, normals)


def bake_mesh_quality(mesh: Mesh, minimum_angle_goal_degrees: float = 20) -> dict[str, object]:
    """Report original-position triangle quality; shape_R is inverse mean ratio."""
    if not math.isfinite(minimum_angle_goal_degrees) or not 0 < minimum_angle_goal_degrees <= 60:
        raise ValueError("INVALID_MINIMUM_ANGLE_GOAL")
    quality = _triangle_quality(mesh.vertices[mesh.faces] / mesh.scale)
    weights = mesh.areas / mesh.areas.sum()
    order = np.argsort(quality.shape_r, kind="stable")
    p95_index = min(int(np.searchsorted(np.cumsum(weights[order]), .95)), len(order) - 1)
    return {
        "minimum_angle_degrees": float(quality.minimum_angles.min()),
        "sliver_count": int(np.sum(quality.minimum_angles < minimum_angle_goal_degrees)),
        "minimum_angle_goal_degrees": minimum_angle_goal_degrees,
        "shape_R_max": float(quality.shape_r.max()),
        "shape_R_area_mean": float(weights @ quality.shape_r),
        "shape_R_area_p95": float(quality.shape_r[order[p95_index]]),
        "shape_R_definition": "sum(edge_length squared) / (4 * sqrt(3) * triangle_area); equilateral=1",
        "minimum_face_area": float(mesh.areas.min()), "total_face_area": float(mesh.areas.sum()),
        "face_count": len(mesh.faces), "vertex_count": len(mesh.vertices),
    }


def _mesh_digest(mesh: Mesh) -> str:
    digest = hashlib.sha256()
    for value in (mesh.vertices, mesh.faces):
        digest.update(str(value.shape).encode("ascii"))
        digest.update(str(value.dtype).encode("ascii"))
        digest.update(value.tobytes())
    return digest.hexdigest()


def _manifold_vertex_link(mesh: Mesh, vertex: int) -> bool:
    """A vertex star must be one disk or one closed fan, never a bow tie."""
    faces = mesh.faces[np.any(mesh.faces == vertex, axis=1)]
    neighbors: dict[int, set[int]] = {}
    for face in faces:
        others = [int(value) for value in face if value != vertex]
        if len(others) != 2:
            return False
        first, second = others
        neighbors.setdefault(first, set()).add(second)
        neighbors.setdefault(second, set()).add(first)
    if not neighbors or any(len(values) not in (1, 2) for values in neighbors.values()):
        return False
    if sum(len(values) == 1 for values in neighbors.values()) not in (0, 2):
        return False
    pending = [next(iter(neighbors))]
    visited: set[int] = set()
    while pending:
        current = pending.pop()
        if current not in visited:
            visited.add(current)
            pending.extend(neighbors[current] - visited)
    return len(visited) == len(neighbors)


def _angle_degrees(first: FloatArray, second: FloatArray) -> FloatArray:
    return np.degrees(np.arctan2(np.linalg.norm(np.cross(first, second), axis=-1),
                                np.sum(first * second, axis=-1)))


def _try_flip(
    mesh: Mesh, edge_id: int, options: BakeTopologyOptions, normalization_scale: float,
) -> tuple[Mesh | None, dict[str, object]]:
    """Validate an atomic proposal without writing into its parent mesh."""
    edge = mesh.edges[edge_id]
    log: dict[str, object] = {"operation": "edge_flip", "edge": list(edge), "status": "REJECTED"}

    def reject(reason: str) -> tuple[None, dict[str, object]]:
        log["reason"] = reason
        return None, log

    entries = mesh.edge_faces[edge_id]
    if len(entries) != 2:
        return reject("BOUNDARY_OR_NONMANIFOLD_EDGE")
    (first, corner), (second, _) = entries
    u, v, w = (int(mesh.faces[first, (corner + offset) % 3]) for offset in range(3))
    other = [int(value) for value in mesh.faces[second] if value not in (u, v)]
    if len(other) != 1 or len({u, v, w, other[0]}) != 4:
        return reject("INVALID_EDGE_LINK")
    x = other[0]
    diagonal = (min(w, x), max(w, x))
    if diagonal in mesh.edges:
        return reject("OPPOSITE_EDGE_ALREADY_EXISTS")
    if not all(_manifold_vertex_link(mesh, vertex) for vertex in (u, v, w, x)):
        return reject("NONMANIFOLD_VERTEX_LINK")
    old_faces = mesh.faces[[first, second]]
    new_faces = np.asarray(((w, u, x), (w, x, v)), dtype=np.int64)
    old = _triangle_quality(mesh.vertices[old_faces] / normalization_scale)
    new = _triangle_quality(mesh.vertices[new_faces] / normalization_scale)
    fold = float(_angle_degrees(old.normals[:1], old.normals[1:])[0])
    if fold > options.feature_angle_degrees + 1e-10:
        return reject("SHARP_FEATURE_GUARD")
    if np.any(new.areas <= options.relative_area_floor):
        return reject("DEGENERATE_OR_TOO_SMALL_PROPOSAL")
    orientation_dots = np.sum(old.normals * new.normals, axis=1)
    if np.any(orientation_dots <= 0):
        return reject("ORIENTATION_FLIP")
    normal_change = float(_angle_degrees(old.normals, new.normals).max())
    if normal_change > options.normal_change_degrees + 1e-10:
        return reject("NORMAL_CHANGE_GUARD")
    area_change = float(abs(new.areas.sum() / old.areas.sum() - 1))
    if area_change > options.maximum_local_area_change_fraction + 1e-12:
        return reject("LOCAL_SURFACE_AREA_CHANGE_GUARD")
    if float(new.minimum_angles.min()) <= float(old.minimum_angles.min()) + options.minimum_improvement_degrees:
        return reject("NO_WORST_ANGLE_IMPROVEMENT")
    if float(new.shape_r.max()) > float(old.shape_r.max()) + 1e-12:
        return reject("WORST_SHAPE_RATIO_REGRESSION")
    faces = mesh.faces.copy()
    faces[[first, second]] = new_faces
    try:
        proposal = make_mesh(mesh.vertices, faces)
    except ValueError as error:
        log["validation_error"] = str(error)
        return reject("MAKE_MESH_REJECTED")
    if not all(_manifold_vertex_link(proposal, vertex) for vertex in (u, v, w, x)):
        return reject("PROPOSAL_VERTEX_LINK_REJECTED")
    nonplanarity = float(abs((mesh.vertices[x] - mesh.vertices[u]) @ old.normals[0]) / normalization_scale)
    log.update(
        status="ACCEPTED", reason="WORST_LOCAL_ANGLE_IMPROVED", face_slots=[first, second],
        old_faces=old_faces.tolist(), new_faces=new_faces.tolist(), new_edge=list(diagonal),
        minimum_angle_before_degrees=float(old.minimum_angles.min()),
        minimum_angle_after_degrees=float(new.minimum_angles.min()),
        maximum_shape_R_before=float(old.shape_r.max()), maximum_shape_R_after=float(new.shape_r.max()),
        input_fold_degrees=fold, maximum_normal_change_degrees=normal_change,
        minimum_orientation_dot=float(orientation_dots.min()), minimum_normalized_area=float(new.areas.min()),
        local_surface_area_change_fraction=area_change, nonplanarity_fraction=nonplanarity,
        surface_geometry_changed=nonplanarity > 0,
    )
    return proposal, log


def optimize_bake_topology(
    mesh: Mesh, options: BakeTopologyOptions = _DEFAULT_OPTIONS,
) -> BakeTopologyResult:
    """Create an independent variant with fixed positions and deterministic flips.

    The operation budget counts attempted interior-edge proposals; boundary and
    already satisfactory local patches are counted as skips. Every accepted
    proposal passes make_mesh and local vertex-link checks before commitment.
    """
    started = time.perf_counter()
    # Canonical reconstruction also detaches writable/noncanonical input arrays.
    current = make_mesh(mesh.vertices, mesh.faces)
    before = bake_mesh_quality(current, options.minimum_angle_goal_degrees)
    parent_hash = _mesh_digest(current)
    normalization_scale = current.scale
    lineage = [{index} for index in range(len(current.faces))]
    operations: list[dict[str, object]] = []
    skipped: dict[str, int] = {}
    accepted = 0
    passes = 0
    status = "PASS_BUDGET_EXHAUSTED"
    for _ in range(options.max_passes):
        if not np.any(_triangle_quality(current.vertices[current.faces] / normalization_scale).minimum_angles
                      < options.minimum_angle_goal_degrees):
            status = "ANGLE_GOAL_REACHED"
            break
        if len(operations) >= options.operation_budget:
            status = "OPERATION_BUDGET_EXHAUSTED"
            break
        passes += 1
        accepted_this_pass = 0
        snapshot = current.edges
        lookup = {edge: index for index, edge in enumerate(current.edges)}
        for edge in snapshot:
            edge_id = lookup.get(edge)
            if edge_id is None:
                continue
            entries = current.edge_faces[edge_id]
            if len(entries) != 2:
                skipped["BOUNDARY_OR_NONMANIFOLD_EDGE"] = skipped.get("BOUNDARY_OR_NONMANIFOLD_EDGE", 0) + 1
                continue
            face_ids = [entry[0] for entry in entries]
            quality = _triangle_quality(current.vertices[current.faces[face_ids]] / normalization_scale)
            if float(quality.minimum_angles.min()) >= options.minimum_angle_goal_degrees:
                skipped["LOCAL_ANGLE_GOAL_ALREADY_MET"] = skipped.get("LOCAL_ANGLE_GOAL_ALREADY_MET", 0) + 1
                continue
            if len(operations) >= options.operation_budget:
                status = "OPERATION_BUDGET_EXHAUSTED"
                break
            proposal, log = _try_flip(current, edge_id, options, normalization_scale)
            log["pass"] = passes
            operations.append(log)
            if proposal is None:
                continue
            parents = lineage[face_ids[0]] | lineage[face_ids[1]]
            log["original_parent_face_ids"] = sorted(parents)
            for face in face_ids:
                lineage[face] = parents.copy()
            current = proposal
            lookup = {candidate: index for index, candidate in enumerate(current.edges)}
            accepted += 1
            accepted_this_pass += 1
        if status == "OPERATION_BUDGET_EXHAUSTED":
            break
        if not accepted_this_pass:
            status = "NO_ACCEPTABLE_IMPROVING_FLIP"
            break
    after = bake_mesh_quality(current, options.minimum_angle_goal_degrees)
    if after["sliver_count"] == 0:
        status = "ANGLE_GOAL_REACHED"
    elif len(operations) >= options.operation_budget:
        status = "OPERATION_BUDGET_EXHAUSTED"
    unique_positions = len(np.unique(current.vertices, axis=0))
    diagnostics: dict[str, object] = {
        "status": status, "attempted_operations": len(operations), "accepted_operations": accepted,
        "options": asdict(options),
        "passes": passes, "skipped_edge_counts": skipped, "parent_mesh_sha256": parent_hash,
        "variant_mesh_sha256": _mesh_digest(current), "fixed_vertex_positions": True,
        "face_count_preserved": len(current.faces) == len(mesh.faces),
        "face_connectivity_changed": accepted > 0,
        "surface_geometry_changed": any(operation.get("surface_geometry_changed") is True for operation in operations),
        "original_face_corner_identity_preserved": accepted == 0,
        "face_ancestry": [sorted(parents) for parents in lineage],
        "ancestry_semantics": "parent patch face ids only; no UV or surface correspondence",
        "coincident_vertex_count": len(current.vertices) - unique_positions,
        "coincident_vertices_repaired": False, "collapse_implemented": False,
        "global_self_intersection_checked": False, "bake_quality_verified": False,
        "normalization_scale": normalization_scale,
        "normalization_definition": "fixed parent mesh.scale = sqrt(total original triangle area)",
        "normalized_area_floor": options.relative_area_floor,
        "area_floor_units": "original-position area / parent mesh.scale squared",
        "angular_comparison_tolerance_degrees": 1e-10,
        "shape_R_comparison_tolerance": 1e-12,
        "local_area_fraction_comparison_tolerance": 1e-12,
        "nonplanarity_marking_threshold": 0.0,
        "nonplanarity_units": "distance to old face plane / parent mesh.scale; floating precision",
        "normal_guard_scope": "per accepted local replacement, relative to the two preceding face normals",
        "operation_budget_definition": "attempted interior-edge proposals, including rejection",
    }
    return BakeTopologyResult(current, tuple(operations), before, after, diagnostics,
                              time.perf_counter() - started)
