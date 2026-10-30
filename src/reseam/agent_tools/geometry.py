"""Immutable, evidence-producing UV inspection and reflection reuse operations.

Nearest-point matches propose a map. Only the complete piecewise affine UV
intersection check authorizes an overlap; material acceptance remains separate.
"""

import math
import time
from dataclasses import asdict, replace
from pathlib import Path

import numpy as np
from scipy.spatial import KDTree

from ..budget_pair import BudgetPairProblem, solve_budget_pair
from ..budget_state import (
    BudgetState,
    embed_region,
    exclusive_faces,
    pack_budget_state,
    validate_budget_geometry,
)
from ..joint_types import Correspondence, ReuseGroup, SimilarityTransform
from ..mesh import chart_labels
from ..reuse_transform import mapped_normals, mapped_points, similarity_transform, transform_parity
from ..surface_match import barycentric_coordinates, project_surface, reflect
from ..types import FloatArray, IntArray, MeshState
from ..validation import jacobians, topology_errors
from .correspondence import (
    chart_aware_transfer,
    common_harmonic_pair,
    face_corner_assignment,
    local_pair,
    normal_corner_transfer,
    topology_copy,
)
from .regions import selected_faces
from .state import (
    Snapshot,
    check_deadline,
    file_hash,
    load_snapshot,
    object_hash,
    read_object,
    require_snapshot,
    save_snapshot,
    snapshot_result,
    write_json,
)
from .uv_metrics import measure_uv_metrics

_DEFAULT_PROFILE: dict[str, object] = {
    "resolution": 256,
    "gap_pixels": 4.0,
    "border_pixels": 4.0,
    "correspondence_fraction": 0.005,
    "normal_angle_degrees": 45.0,
    "maximum_anisotropy": 2.0,
    "mean_shape_limit": 0.025,
    "tail_shape_limit": 0.1,
    "maximum_density_ratio": 2.0,
    "quality_constraints": True,
}
_QUALITY_ERRORS = {
    "ANISOTROPY_LIMIT", "MEAN_SHAPE_LIMIT", "TAIL_SHAPE_LIMIT",
    "NEIGHBOR_DENSITY_LIMIT", "INVALID_BUDGET_QUALITY_PROFILE",
}


def _number(values: dict[str, object], key: str, default: float) -> float:
    value = values.get(key, default)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"FINITE_NUMBER_REQUIRED:{key}")
    return float(value)


def _integer(values: dict[str, object], key: str, default: int, minimum: int = 0) -> int:
    value = values.get(key, default)
    if type(value) is not int or value < minimum:
        raise ValueError(f"INTEGER_OUT_OF_RANGE:{key}")
    return value


def _profile(request: dict[str, object]) -> dict[str, object]:
    value = request.get("profile", {})
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise ValueError("GEOMETRY_PROFILE_OBJECT_REQUIRED")
    return {**_DEFAULT_PROFILE, **value}


def geometry_report(state: BudgetState, profile: dict[str, object] | None = None) -> dict[str, object]:
    """Full-cell safety and optional shape gates, supporting authored mirrored charts.

The orientation of an independent chart may be either sign, but must be
consistent throughout that chart. A declared shared member must retain its
explicit orientation. Neither rule permits a fold or same-member overlap.
"""
    settings = {**_DEFAULT_PROFILE, **(profile or {})}
    checked = settings.get("quality_constraints")
    if not isinstance(checked, bool):
        raise TypeError("BOOLEAN_REQUIRED:quality_constraints")
    errors = topology_errors(state.surface)
    if errors:
        return {"valid": False, "errors": errors, "hard_valid": False, "hard_errors": errors,
                "quality_valid": None, "quality_errors": [], "quality_constraints_checked": False}
    labels = chart_labels(state.surface)
    determinants = np.linalg.det(jacobians(state.surface))
    signs = np.where(determinants >= 0, 1, -1).astype(np.int64)
    report = validate_budget_geometry(
        state, _integer(settings, "resolution", 256, 1),
        gap_pixels=_number(settings, "gap_pixels", 4),
        border_pixels=_number(settings, "border_pixels", 4),
        correspondence_fraction=_number(settings, "correspondence_fraction", 0.005),
        normal_angle_degrees=_number(settings, "normal_angle_degrees", 45),
        maximum_anisotropy=_number(settings, "maximum_anisotropy", 2),
        mean_shape_limit=_number(settings, "mean_shape_limit", 0.025),
        tail_shape_limit=_number(settings, "tail_shape_limit", 0.1),
        maximum_density_ratio=_number(settings, "maximum_density_ratio", 2),
        quality_constraints=checked, independent_orientations=signs,
    )
    raw_errors = report["errors"]
    assert isinstance(raw_errors, list)
    hard_errors = [error for error in raw_errors if error not in _QUALITY_ERRORS]
    quality_errors = [error for error in raw_errors if error in _QUALITY_ERRORS]
    report.update(
        hard_valid=not hard_errors, hard_errors=hard_errors,
        quality_valid=(not quality_errors) if report.get("quality_constraints_checked") else None,
        quality_errors=quality_errors, chart_count=len(np.unique(labels)),
        shared_group_count=len(state.groups),
        independent_orientation_policy="constant signed chart; declared group orientation enforced",
        mirrored_chart_ids=[int(label) for label in np.unique(labels) if np.all(signs[labels == label] < 0)],
        material_validated=False,
        density_balance=state.evidence.get("density_balance"),
    )
    if "preserve_source_uv" in settings:
        from .source_uv import preserve_source_conflicts
        return preserve_source_conflicts(state, report, settings)
    return report


def _regions(state: BudgetState) -> list[dict[str, object]]:
    mesh, surface = state.surface.mesh, state.surface
    labels = chart_labels(surface)
    corners = surface.uv[surface.uv_faces]
    determinants = np.linalg.det(jacobians(surface))
    regions: list[dict[str, object]] = []
    for label in np.unique(labels):
        ids = np.flatnonzero(labels == label)
        weights = mesh.areas[ids]
        centers = mesh.vertices[mesh.faces[ids]].mean(axis=1)
        regions.append({
            "region_id": f"chart:{int(label)}", "chart_id": int(label), "faces": ids.tolist(),
            "face_count": len(ids), "vertex_count": len(np.unique(mesh.faces[ids])),
            "surface_area": float(weights.sum()),
            "centroid": ((weights @ centers) / weights.sum()).tolist(),
            "uv_bounds": [corners[ids].min(axis=(0, 1)).tolist(), corners[ids].max(axis=(0, 1)).tolist()],
            "uv_area": float(np.sum(np.abs(determinants[ids]) * weights) / mesh.scale**2),
            "density_min": float(np.sqrt(np.abs(determinants[ids])).min()),
            "density_max": float(np.sqrt(np.abs(determinants[ids])).max()),
        })
    return regions


def inspect_uv(request: dict[str, object], output: Path, deadline: float) -> dict[str, object]:
    check_deadline(deadline)
    snapshot = require_snapshot(request)
    report = geometry_report(snapshot.state, _profile(request))
    metrics = measure_uv_metrics(snapshot, report, selected_faces(request, snapshot), deadline)
    regions = _regions(snapshot.state)
    labels = chart_labels(snapshot.state.surface)
    adjacency: list[dict[str, object]] = []
    for edge_id, entries in enumerate(snapshot.state.surface.mesh.edge_faces):
        if len(entries) == 2:
            first, second = entries[0][0], entries[1][0]
            if labels[first] != labels[second]:
                adjacency.append({"edge_id": edge_id, "faces": [first, second],
                                  "charts": [int(labels[first]), int(labels[second])]})
    check_deadline(deadline)
    artifact = output / "inspection.json"
    metrics_artifact = output / "uv_metrics.json"
    write_json(artifact, {**snapshot_result(snapshot), "geometry": report, "regions": regions,
                          "chart_adjacency": adjacency,
                          "reuse_groups": [asdict(group) for group in snapshot.state.groups],
                          "uv_metrics": metrics, "metrics_report": str(metrics_artifact.resolve()),
                          "footprint_definition": "area of actual UV triangle union, not occupied color pixels"})
    write_json(metrics_artifact, {**metrics, "inspection_report": str(artifact.resolve()),
                                "inspection_report_sha256": file_hash(artifact),
                                "geometry_report_json_pointer": "/geometry"})
    return {**snapshot_result(snapshot), "status": "OK", "execution_success": True,
            "geometry_passed": report["valid"], "final_quality_passed": None,
            "report": str(artifact.resolve()), "chart_count": len(regions),
            "shared_group_count": len(snapshot.state.groups), "unique_footprint": report.get("unique_coverage"),
            "metrics_report": str(metrics_artifact.resolve()), "metrics_verified": metrics["metrics_verified"],
            "uv_connected_charts": metrics["uv_connected_charts"], "packing_owners": metrics["packing_owners"],
            "actual_independent_tiles": metrics["actual_independent_tiles"]}


def find_candidates(request: dict[str, object], output: Path, deadline: float) -> dict[str, object]:
    """Cheap chart descriptors propose reflection pairs; no sharing is authorized."""
    check_deadline(deadline)
    snapshot = require_snapshot(request)
    state = snapshot.state
    regions = _regions(state)
    maximum = _integer(request, "max_candidates", 32, 1)
    cap = _integer(request, "max_regions", 512, 2)
    center_limit = _number(request, "center_fraction", 0.05)
    area_limit = _number(request, "minimum_area_ratio", 0.6)
    if center_limit < 0 or not 0 < area_limit <= 1:
        raise ValueError("INVALID_CANDIDATE_PROFILE")
    shared = {face for group in state.groups for face in group.first_faces + group.second_faces}
    eligible = [region for region in regions if not shared.intersection(_face_list(region["faces"], state))]
    eligible.sort(key=lambda region: -_number(region, "surface_area", 0))
    considered = eligible[:cap]
    vertices = state.surface.mesh.vertices
    diagonal = float(np.linalg.norm(np.ptp(vertices, axis=0)))
    center = (vertices.min(axis=0) + vertices.max(axis=0)) / 2
    candidates: list[tuple[float, dict[str, object]]] = []
    for i, first in enumerate(considered):
        check_deadline(deadline)
        for second in considered[i + 1:]:
            a, b = _number(first, "surface_area", 0), _number(second, "surface_area", 0)
            ratio = min(a, b) / max(a, b)
            if ratio < area_limit:
                continue
            ca, cb = np.asarray(first["centroid"], dtype=float), np.asarray(second["centroid"], dtype=float)
            for normal in np.eye(3):
                offset = float(center @ normal)
                distance = float(np.linalg.norm(reflect(ca, normal, offset) - cb) / diagonal)
                if distance > center_limit:
                    continue
                proposed: dict[str, object] = {
                    **snapshot_result(snapshot), "first_faces": first["faces"], "second_faces": second["faces"],
                    "plane_normal": normal.tolist(), "plane_offset": offset, "mode": "auto",
                }
                candidates.append((distance + (1 - ratio) * 0.01, {
                    "candidate_id": object_hash(proposed)[:16], "first_region": first["region_id"],
                    "second_region": second["region_id"], "check_request": proposed,
                    "evidence": {"centroid_reflection_error_fraction": distance, "area_ratio": ratio,
                                 "same_face_count": first["face_count"] == second["face_count"],
                                 "same_vertex_count": first["vertex_count"] == second["vertex_count"]},
                    "authorized": False,
                }))
    candidates.sort(key=lambda item: (item[0], str(item[1]["candidate_id"])))
    artifact = output / "candidates.json"
    write_json(artifact, {**snapshot_result(snapshot), "candidates": [item[1] for item in candidates[:maximum]],
                          "total_coarse_candidates": len(candidates), "considered_regions": len(considered),
                          "omitted_regions": [region["region_id"] for region in eligible[cap:]],
                          "method": "area and centroid reflection; X/Y/Z planes through object bounds center",
                          "limitation": "coarse chart candidates only; explicit face regions may cross old seams"})
    check_deadline(deadline)
    return {"status": "OK", "execution_success": True, "report": str(artifact.resolve()),
            "candidate_count": min(maximum, len(candidates)), "final_quality_passed": None}


def _face_list(value: object, state: BudgetState, allow_empty: bool = False) -> tuple[int, ...]:
    if (not isinstance(value, list) or any(type(face) is not int for face in value)
            or len(set(value)) != len(value) or (not value and not allow_empty)):
        raise ValueError("UNIQUE_WHOLE_FACE_IDS_REQUIRED")
    faces = tuple(sorted(value))
    if faces and (faces[0] < 0 or faces[-1] >= len(state.surface.mesh.faces)):
        raise ValueError("FACE_ID_OUT_OF_RANGE")
    return faces


def _pair_faces(request: dict[str, object], state: BudgetState, side: str) -> tuple[int, ...]:
    if f"{side}_faces" in request:
        return _face_list(request[f"{side}_faces"], state)
    chart = _integer(request, f"{side}_chart", -1)
    labels = chart_labels(state.surface)
    faces = np.flatnonzero(labels == chart).tolist()
    return _face_list(faces, state)


def _plane(request: dict[str, object]) -> tuple[FloatArray, float]:
    raw = request.get("plane_normal", [1.0, 0.0, 0.0])
    if not isinstance(raw, list) or len(raw) != 3:
        raise ValueError("PLANE_NORMAL_REQUIRES_THREE_NUMBERS")
    normal = np.asarray([_number({"v": value}, "v", 0) for value in raw], dtype=float)
    if abs(float(np.linalg.norm(normal)) - 1) > 1e-8:
        raise ValueError("UNIT_PLANE_NORMAL_REQUIRED")
    return normal, _number(request, "plane_offset", 0)


def _exact_copy(
    surface: MeshState, first: tuple[int, ...], second: tuple[int, ...], normal: FloatArray, offset: float,
    transform: SimilarityTransform | None = None,
) -> tuple[FloatArray, IntArray] | None:
    mesh = surface.mesh
    a, b = np.unique(mesh.faces[list(first)]), np.unique(mesh.faces[list(second)])
    if len(a) != len(b) or len(first) != len(second):
        return None
    distance, raw_indices = KDTree(mesh.vertices[a]).query(
        mapped_points(mesh.vertices[b], normal, offset, transform, inverse=True))
    indices = np.asarray(raw_indices, dtype=np.int64)
    tolerance = max(1e-12, float(np.linalg.norm(np.ptp(mesh.vertices, axis=0))) * 1e-10)
    if float(np.max(distance)) > tolerance or len(np.unique(indices)) != len(a):
        return None
    mapping = {int(target): int(a[index]) for target, index in zip(b, indices, strict=True)}
    face_lookup = {frozenset(map(int, mesh.faces[face])): face for face in first}
    if len(face_lookup) != len(first):
        return None
    corners = np.empty((len(second), 3, 2))
    identities = np.empty((len(second), 3), dtype=np.int64)
    for row, face in enumerate(second):
        mapped = [mapping[int(vertex)] for vertex in mesh.faces[face]]
        source = face_lookup.get(frozenset(mapped))
        if source is None:
            return None
        for corner, vertex in enumerate(mapped):
            source_corner = int(np.flatnonzero(mesh.faces[source] == vertex)[0])
            uv_id = int(surface.uv_faces[source, source_corner])
            corners[row, corner] = surface.uv[uv_id]
            identities[row, corner] = uv_id + len(surface.uv)
    return corners, identities


def _transfer_surface_uv(
    surface: MeshState, first: tuple[int, ...], second: tuple[int, ...],
    normal: FloatArray, offset: float, deadline: float,
    transform: SimilarityTransform | None = None,
) -> tuple[FloatArray, IntArray]:
    """Transfer vertex samples, then require complete affine face-cell validation."""
    mesh = surface.mesh
    target_triangles = mesh.vertices[mesh.faces[list(first)]]
    vertices = np.unique(mesh.faces[list(second)])
    sampled: dict[int, FloatArray] = {}
    source_uv = surface.uv[surface.uv_faces[list(first)]]
    for start in range(0, len(vertices), 16):
        check_deadline(deadline)
        ids = vertices[start:start + 16]
        points, faces, _ = project_surface(mapped_points(mesh.vertices[ids], normal, offset, transform,
                                                        inverse=True), target_triangles)
        coordinates = barycentric_coordinates(target_triangles[faces], points)
        uv = np.einsum("nc,ncd->nd", coordinates, source_uv[faces])
        sampled.update((int(vertex), position) for vertex, position in zip(ids, uv, strict=True))
    corners = np.asarray([[sampled[int(vertex)] for vertex in mesh.faces[face]] for face in second])
    return corners, surface.uv_faces[list(second)] + len(surface.uv)


def _surface_constraints(
    state: BudgetState, first: tuple[int, ...], second: tuple[int, ...], normal: FloatArray, offset: float,
    group_id: str, profile: dict[str, object], deadline: float,
    transform: SimilarityTransform | None = None,
) -> tuple[tuple[Correspondence, ...], tuple[dict[str, object], ...], list[dict[str, object]]]:
    """Project within each source face's reflected-normal-compatible surface.

    A shared edge or vertex has several equally near incident faces. Selecting
    one before checking its normal can reject an exact folded mirror solely
    because of face ordering. This gate changes the query domain, not the
    distance/cycle limits or the subsequent complete UV overlap-cell checks.
    """
    mesh = state.surface.mesh
    ids = first + second
    local = {face: index for index, face in enumerate(ids)}
    triangles = mesh.vertices[mesh.faces]
    diagonal = float(np.linalg.norm(np.ptp(mesh.vertices, axis=0)))
    normals = np.cross(triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0])
    normals /= np.linalg.norm(normals, axis=1)[:, None]
    samples = np.asarray([[1., 0, 0], [0, 1., 0], [0, 0, 1.], [1 / 3] * 3,
                          [.5, .5, 0], [0, .5, .5], [.5, 0, .5]])
    constraints: list[Correspondence] = []
    records: list[dict[str, object]] = []
    rejected: list[dict[str, object]] = []
    distance_limit = _number(profile, "correspondence_fraction", 0.005)
    angle_limit = _number(profile, "normal_angle_degrees", 45)
    for source_ids, target_ids in ((first, second), (second, first)):
        inverse = source_ids == second
        target = triangles[list(target_ids)]
        target_normals = normals[list(target_ids)]
        for source in source_ids:
            check_deadline(deadline)
            original = samples @ triangles[source]
            angles = np.degrees(np.arccos(np.clip(
                target_normals @ mapped_normals(normals[source], normal, transform, inverse=inverse), -1, 1)))
            compatible = np.flatnonzero(angles <= angle_limit + 1e-8)
            # When the compatible domain is empty, keep the old nearest-face
            # measurements only as rejection evidence; never emit constraints.
            query_faces = compatible if len(compatible) else np.arange(len(target_ids))
            projected, matched, distances = project_surface(
                mapped_points(original, normal, offset, transform, inverse=inverse), target[query_faces])
            matched = query_faces[matched]
            bary = barycentric_coordinates(target[matched], projected)
            returned, _, _ = project_surface(mapped_points(projected, normal, offset, transform,
                                                           inverse=not inverse), triangles[list(source_ids)])
            cycle = np.linalg.norm(returned - original, axis=1) / diagonal
            for index, (weights, target_local) in enumerate(zip(bary, matched, strict=True)):
                target_face = target_ids[int(target_local)]
                angle = float(angles[int(target_local)])
                record: dict[str, object] = {
                    "group_id": group_id, "source_face": source, "source_sample": index,
                    "source_vertex_id": int(mesh.faces[source, index]) if index < 3 else None,
                    "source_barycentric": samples[index].tolist(), "target_face": target_face,
                    "target_barycentric": weights.tolist(), "surface_error_fraction": float(distances[index] / diagonal),
                    "round_trip_error_fraction": float(cycle[index]), "normal_angle_degrees": angle,
                    "normal_angle_limit_degrees": angle_limit,
                    "compatible_target_face_count": len(compatible),
                    "target_selection": "nearest_surface_within_mapped_face_normal_gate" if transform else
                                        "nearest_surface_within_reflected_face_normal_gate",
                    "first_to_second": transform, "transform_direction": "inverse" if inverse else "forward",
                    "normalization": "original_mesh_bounding_box_diagonal", "normalization_scale": diagonal,
                }
                if not len(compatible):
                    record.update(reason="NO_NORMAL_COMPATIBLE_TARGET_FACE",
                                  target_selection="diagnostic_only_no_compatible_face")
                records.append(record)
                if (not len(compatible) or distances[index] / diagonal > distance_limit + 1e-10
                        or cycle[index] > distance_limit + 1e-10
                        or angle > angle_limit + 1e-8):
                    rejected.append(record)
                if not len(compatible):
                    continue
                source_weights = samples[index]
                constraints.append(Correspondence(
                    local[source], (float(source_weights[0]), float(source_weights[1]), float(source_weights[2])),
                    local[target_face], (float(weights[0]), float(weights[1]), float(weights[2])),
                    float(mesh.areas[source]) / len(samples),
                ))
    return tuple(constraints), tuple(records), rejected


def _packed(
    state: BudgetState, profile: dict[str, object], density_normalize: bool = True, deadline: float | None = None,
) -> BudgetState:
    """Explicit balancing preserves owner-relative scale instead of equalizing it.

    When requested it replaces density_normalize, because re-normalization would
    undo the solved adjacent-face inequalities. All comparison tracks must use
    the same choice and common parent.
    """
    balance = profile.get("balance_density", False)
    if not isinstance(balance, bool):
        raise TypeError("BOOLEAN_REQUIRED:balance_density")
    if balance:
        from .density import balance_density
        result, record = balance_density(
            state, _number(profile,"maximum_density_ratio",2),_integer(profile,"resolution",256,1),
            _number(profile,"gap_pixels",4),_number(profile,"border_pixels",4),
            time.monotonic()+30 if deadline is None else deadline)
        result.evidence["density_balance"] = record
        return result
    return pack_budget_state(state, _integer(profile, "resolution", 256, 1),
                             gap_pixels=_number(profile, "gap_pixels", 4),
                             border_pixels=_number(profile, "border_pixels", 4),
                             density_normalize=density_normalize)


def _edited_metadata(snapshot: Snapshot, operation: str, profile: dict[str, object]) -> dict[str, object]:
    metadata = dict(snapshot.metadata)
    for key in ("bake", "bake_result", "quality", "quality_result", "validation", "diagnostic_candidate"):
        metadata.pop(key, None)
    metadata.update(last_operation=operation, geometry_profile=profile, requires_rebake=True)
    return metadata


def _rejected(output: Path, report: dict[str, object]) -> dict[str, object]:
    path = output / "check.json"
    write_json(path, report)
    result = {"status": "REJECTED", "execution_success": True, "geometry_passed": False,
              "final_quality_passed": None, "report": str(path.resolve()), "reason": report.get("reason")}
    if "diagnostic_candidates" in report:
        result["diagnostic_candidates"] = report["diagnostic_candidates"]
    return result


def _split_master_chart_groups(
    candidate: BudgetState, aggregate: ReuseGroup, master_labels: IntArray,
    transfer: dict[str, object], records: tuple[dict[str, object], ...],
) -> tuple[tuple[str, ...], dict[str, object]]:
    """One packing owner per selected master chart, retaining semantic provenance.

    Surface samples crossing a master cut remain diagnostic evidence, not a
    constraint tying separate texture blocks together. Exact overlap checks
    still validate every positive cell of every subgroup after packing.
    """
    selected = transfer.get("selected_master_charts")
    targets = transfer.get("target_faces")
    if (not isinstance(selected, list) or not isinstance(targets, list)
            or tuple(targets) != aggregate.second_faces or len(selected) != len(targets)
            or any(type(label) is not int for label in selected)):
        raise ValueError("CHART_TRANSFER_GROUP_ASSIGNMENT_INVALID")
    assignments = dict(zip(aggregate.second_faces, selected, strict=True))
    groups: list[ReuseGroup] = []
    unmatched: list[int] = []
    for label in sorted({int(master_labels[face]) for face in aggregate.first_faces}):
        first = tuple(face for face in aggregate.first_faces if master_labels[face] == label)
        second = tuple(face for face in aggregate.second_faces if assignments[face] == label)
        if not second:
            unmatched.extend(first)
            continue
        groups.append(replace(aggregate, group_id=f"{aggregate.group_id}:chart:{label}",
                              first_faces=first, second_faces=second))
    membership = {face: group.group_id for group in groups for face in group.first_faces+group.second_faces}
    if not groups or not set(aggregate.second_faces) <= membership.keys():
        raise ValueError("CHART_TRANSFER_TARGET_WITHOUT_MASTER_GROUP")
    candidate.groups = tuple(group for group in candidate.groups if group.group_id != aggregate.group_id)+tuple(groups)
    affected = set(aggregate.first_faces+aggregate.second_faces)
    candidate.correspondences = tuple(
        item for item in candidate.correspondences
        if item.source_face not in affected and item.target_face not in affected
        or item.source_face in membership and membership[item.source_face] == membership.get(item.target_face))
    kept: list[dict[str, object]] = []
    omitted: list[dict[str, object]] = []
    for item in records:
        source, target = item.get("source_face"), item.get("target_face")
        group_id = membership.get(source) if isinstance(source, int) else None
        if group_id is not None and isinstance(target, int) and membership.get(target) == group_id:
            kept.append({**item, "group_id": group_id, "semantic_group_id": aggregate.group_id})
        else:
            omitted.append(item)
    candidate.correspondence_records += tuple(kept)
    evidence: dict[str, object] = {
        "semantic_group_id": aggregate.group_id, "packing_rule": "one_reuse_group_per_master_uv_chart",
        "groups": [{"group_id": group.group_id,
                    "master_chart": int(master_labels[group.first_faces[0]]),
                    "first_faces": list(group.first_faces), "second_faces": list(group.second_faces)}
                   for group in groups],
        "unmatched_master_faces_retained_exclusive": unmatched,
        "retained_correspondence_samples": len(kept),
        "cross_chart_samples_diagnostic_only": omitted,
        "source_geometry_and_uv_cuts_preserved": True,
    }
    candidate.invalidate()
    return tuple(group.group_id for group in groups), evidence


def _check_pair(
    request: dict[str, object], output: Path, deadline: float,
    transform: SimilarityTransform | None = None, alignment: dict[str, object] | None = None,
) -> dict[str, object]:
    check_deadline(deadline)
    snapshot = require_snapshot(request)
    base = snapshot.state
    first, second = _pair_faces(request, base, "first"), _pair_faces(request, base, "second")
    if set(first).intersection(second):
        raise ValueError("PAIR_MEMBERS_MUST_BE_DISJOINT")
    if any(set(first + second).intersection(group.first_faces + group.second_faces) for group in base.groups):
        raise ValueError("PAIR_CROSSES_EXISTING_GROUP:unshare first")
    exceptions = _face_list(request.get("exclusive_faces", []), base, allow_empty=True)
    if not set(exceptions) <= set(first + second):
        raise ValueError("EXCLUSIVE_FACES_OUTSIDE_PROPOSED_PAIR")
    first = tuple(fi for fi in first if fi not in exceptions)
    second = tuple(fi for fi in second if fi not in exceptions)
    if not first or not second:
        return _rejected(output, {"reason": "EXCEPTIONS_REMOVE_COMPLETE_MEMBER"})
    normal, offset = _plane(request)
    profile = _profile(request)
    mode = request.get("mode", "auto")
    if mode not in ("auto", "exact", "approximate", "topology", "common_domain", "normal_corner", "similarity", "chart_aware"):
        raise ValueError("INVALID_CORRESPONDENCE_MODE")
    preserve = request.get("preserve_rejected_candidate", False)
    if not isinstance(preserve, bool):
        raise TypeError("BOOLEAN_REQUIRED:preserve_rejected_candidate")
    if preserve and mode not in ("chart_aware", "similarity"):
        raise ValueError("DIAGNOSTIC_PRESERVATION_REQUIRES_CHART_AWARE_OR_SIMILARITY")
    group_id = "reuse:" + object_hash({"state": snapshot.state_hash, "first": first, "second": second,
                                       "normal": normal.tolist(), "offset": offset,
                                       "first_to_second": transform})[:16]
    constraints, records, failed = _surface_constraints(base, first, second, normal, offset,
                                                        group_id, profile, deadline, transform)
    if failed:
        return _rejected(output, {"reason": "SURFACE_CORRESPONDENCE_REJECTED", "failed_samples": failed,
                                  "sample_count": len(records), "alignment": alignment,
                                  "conclusion": "this selected pair and explicit map only"})
    copied = (_exact_copy(base.surface, first, second, normal, offset, transform)
              if mode in ("auto", "exact", "similarity") else None)
    if copied is None and mode == "exact":
        return _rejected(output, {"reason": "EXACT_TOPOLOGY_NOT_MATCHED",
                                  "conclusion": "approximate surface sharing remains unevaluated"})
    ids = first + second
    split = len(first)
    iterations = _integer(request, "refine_iterations", 0)
    diagnostics: dict[str, object] = {"query_count": len(records), "failed_samples": [],
                                    "exceptions_applied_before_mapping": list(exceptions), "alignment": alignment}
    choices: list[tuple[float, BudgetState, dict[str, object], str]] = []
    attempt_reports: list[dict[str, object]] = []
    diagnostic_candidates: list[dict[str, object]] = []
    exact_name = "exact_similarity_copy" if transform is not None else "exact_reflection_copy"
    names = ([exact_name] if copied is not None else
             ["surface_barycentric_transfer", "topology_copy", "normal_corner_transfer", "face_corner_assignment"]
             + (["common_harmonic_domain"] if mode == "similarity" else [])
             if mode in ("auto", "similarity") else ["surface_barycentric_transfer"] if mode == "approximate" else
             ["topology_copy"] if mode == "topology" else ["normal_corner_transfer"] if mode == "normal_corner" else
             ["chart_aware_transfer"] if mode == "chart_aware" else
             ["common_harmonic_domain"])
    for name in names:
        check_deadline(deadline)
        try:
            transfer_record: dict[str, object] | None = None
            if name == "common_harmonic_domain":
                local = common_harmonic_pair(base.surface, first, second, normal, offset, transform)
            elif name == "chart_aware_transfer":
                mapped, transfer_record = chart_aware_transfer(base.surface, first, second, normal, offset,
                                                               deadline, transform,
                                                               _number(profile, "normal_angle_degrees", 45))
                diagnostics["chart_transfer"] = transfer_record
                local = local_pair(base.surface, first, second, mapped)
            else:
                mapped = copied if name == exact_name else (
                    _transfer_surface_uv(base.surface,first,second,normal,offset,deadline,transform)
                    if name == "surface_barycentric_transfer" else
                    topology_copy(base.surface,first,second,normal,offset,_number(profile,"correspondence_fraction",.005),
                                  min(deadline,time.monotonic()+3),transform) if name == "topology_copy" else
                    normal_corner_transfer(base.surface,first,second,normal,offset,_number(profile,"normal_angle_degrees",45),deadline,transform)
                    if name == "normal_corner_transfer" else
                    face_corner_assignment(base.surface,first,second,normal,offset,
                                          _number(profile,"correspondence_fraction",.005),_number(profile,"normal_angle_degrees",45),deadline,transform))
                if mapped is None:
                    attempt_reports.append({"method": name, "reason": "NO_BIJECTIVE_ASSIGNMENT"})
                    continue
                local = local_pair(base.surface, first, second, mapped)
            signs = np.sign(np.linalg.det(jacobians(local))).astype(np.int64)
            orientation = 1 if np.count_nonzero(signs[:split] > 0) >= split/2 else -1
            parity = transform_parity(transform)
            bad = np.concatenate((np.flatnonzero(signs[:split] != orientation),
                                  split+np.flatnonzero(signs[split:] != parity*orientation)))
            if len(bad):
                attempt_reports.append({"method":name,"reason":"INCONSISTENT_MEMBER_ORIENTATION",
                                        "face_signs":signs.tolist(),"suggested_exclusive_faces":[ids[int(i)] for i in bad]})
                continue
            group = ReuseGroup(group_id,first,second,(float(normal[0]),float(normal[1]),float(normal[2])),
                               offset,orientation,parity*orientation,transform)
            alternatives = [(name,local)]
            if iterations:
                seconds = min(max(0,deadline-time.monotonic()),_number(request,"solve_seconds",30))
                members = np.concatenate((np.zeros(split,dtype=np.int64),np.ones(len(second),dtype=np.int64)))
                try:
                    problem = BudgetPairProblem(local,signs,constraints,member_ids=members)
                    solved = solve_budget_pair(problem,seconds,iterations)
                    diagnostics["solver"] = {"status":solved.status,"history":solved.history,"coupling_rms":solved.coupling_rms}
                    alternatives.append((name+"_refined",solved.state))
                except ValueError as error:
                    diagnostics["solver"] = {"status":"INITIALIZATION_REJECTED","reason":str(error)}
            for variant,proposed_local in alternatives:
                candidate = embed_region(base,proposed_local,ids,group,constraints)
                required_groups = (group_id,)
                if transfer_record is not None:
                    required_groups, grouping = _split_master_chart_groups(
                        candidate, group, chart_labels(base.surface), transfer_record, records)
                    diagnostics["chart_grouping"] = grouping
                else:
                    candidate.correspondence_records += records
                candidate = _packed(candidate,profile,deadline=deadline)
                report = geometry_report(candidate,profile)
                attempt_reports.append({"method":variant,"geometry":report})
                active = report.get("active_reuse_group_ids",[])
                if report["hard_valid"] and isinstance(active,list) and set(required_groups) <= set(active):
                    score = _number(report,"mean_shape_distortion",0)+_number(report,"max_overlap_geometry_error",0)
                    choices.append((score,candidate,report,variant))
                elif preserve:
                    metadata = _edited_metadata(snapshot, "reuse.check_pair:rejected_diagnostic", profile)
                    metadata["diagnostic_candidate"] = {"status": "REJECTED", "geometry": report,
                                                         "accepted": False, "method": variant}
                    if "chart_grouping" in diagnostics:
                        metadata["semantic_chart_groups"] = diagnostics["chart_grouping"]
                    rejected_snapshot = save_snapshot(candidate, output/"rejected_candidates"/variant, metadata, snapshot)
                    diagnostic_candidates.append({**snapshot_result(rejected_snapshot), "method": variant,
                                                  "accepted": False, "geometry_passed": False})
        except (ValueError, RecursionError) as error:
            attempt_reports.append({"method": name, "reason": str(error)})
            continue
        if choices:
            break
    diagnostics["attempts"] = attempt_reports
    if not choices:
        return _rejected(output, {"reason": "AFFINE_UV_OR_PACKING_REJECTED", "diagnostics": diagnostics,
                                  "diagnostic_candidates": diagnostic_candidates,
                                  "conclusion": "finite candidates rejected; not mathematical infeasibility"})
    _, candidate, report, selected = min(choices, key=lambda choice: choice[0])
    check_deadline(deadline)
    metadata = _edited_metadata(snapshot, "reuse.check_pair", profile)
    if "chart_grouping" in diagnostics:
        metadata["semantic_chart_groups"] = diagnostics["chart_grouping"]
    checked_snapshot = save_snapshot(candidate, output / "checked_snapshot", metadata, snapshot)
    shared_ids = [group.group_id for group in candidate.groups if group not in base.groups]
    proposal: dict[str, object] = {
        "schema_version": 1, "asset_id": snapshot.asset_id, "base_state_hash": snapshot.state_hash,
        "candidate_state_hash": checked_snapshot.state_hash,
        "candidate_snapshot": str(checked_snapshot.root / "snapshot.json"),
        "first_faces": list(first), "second_faces": list(second), "exclusive_faces": list(exceptions),
        "profile": profile, "selected_method": selected, "geometry": report,
        "first_to_second": transform,
        "semantic_group_id": group_id, "shared_group_ids": shared_ids,
        "diagnostics": diagnostics, "final_quality_passed": None,
    }
    proposal["proposal_hash"] = object_hash(proposal)
    path = output / "proposal.json"
    write_json(path, proposal)
    return {"status": "OK", "execution_success": True, "proposal": str(path.resolve()),
            "geometry_passed": report["valid"], "hard_geometry_passed": True, "final_quality_passed": None,
            "selected_method": selected, "shared_group_id": group_id, "shared_group_ids": shared_ids,
            "report": str(path.resolve())}


def check_pair(request: dict[str, object], output: Path, deadline: float) -> dict[str, object]:
    """Legacy reflection modes or bounded explicit similarity hypotheses."""
    if request.get("mode") == "paired_domain":
        from .paired_domain import check_paired_domain
        return check_paired_domain(request, output, deadline)
    if request.get("mode") == "chart_aware":
        transform = None if request.get("first_to_second") is None else similarity_transform(request["first_to_second"])
        return _check_pair(request, output, deadline, transform,
                           {"method": "fixed supplied similarity" if transform else "legacy world reflection"})
    if request.get("mode", "auto") != "similarity":
        if request.get("first_to_second") is not None:
            raise ValueError("EXPLICIT_TRANSFORM_REQUIRES_SIMILARITY_MODE")
        return _check_pair(request, output, deadline)
    from .alignment import fit_similarity_candidates
    check_deadline(deadline)
    snapshot = require_snapshot(request)
    first, second = (_pair_faces(request, snapshot.state, side) for side in ("first", "second"))
    exceptions = _face_list(request.get("exclusive_faces", []), snapshot.state, allow_empty=True)
    if not set(exceptions) <= set(first+second) or set(first).intersection(second):
        raise ValueError("INVALID_SIMILARITY_REGION_MEMBERS")
    first = tuple(face for face in first if face not in exceptions)
    second = tuple(face for face in second if face not in exceptions)
    if not first or not second:
        return _rejected(output, {"reason": "EXCEPTIONS_REMOVE_COMPLETE_MEMBER"})
    normal, offset = _plane(request)
    if request.get("first_to_second") is not None:
        candidates = [similarity_transform(request["first_to_second"])]
        alignment: dict[str, object] = {"method": "explicit supplied similarity; independently checked below"}
    else:
        candidates, alignment = fit_similarity_candidates(snapshot.state.surface, first, second, normal, offset,
                                                          min(deadline, time.monotonic()+15))
    attempts: list[dict[str, object]] = []
    fallback: dict[str, object] | None = None
    for index, transform in enumerate(candidates):
        check_deadline(deadline)
        evidence = {**alignment, "selected_hypothesis_index": index, "first_to_second": transform}
        result = _check_pair(request, output / f"alignment_{index}", deadline, transform, evidence)
        attempts.append({"hypothesis_index": index, "first_to_second": transform, "result": result})
        if result["status"] == "OK":
            fallback = fallback or result
            if result["geometry_passed"]:
                fallback = result
                break
    write_json(output / "alignment.json", {**alignment, "attempts": attempts,
                                           "final_quality_passed": None, "geometry_changed": False})
    if fallback is not None:
        return {**fallback, "alignment_report": str((output / "alignment.json").resolve())}
    return _rejected(output, {"reason": "SIMILARITY_HYPOTHESES_REJECTED", "alignment": alignment,
                              "attempts": attempts, "conclusion": "bounded hypotheses only; not impossibility"})


def _unshare(state: BudgetState, raw_ids: object) -> BudgetState:
    if not isinstance(raw_ids, list) or not raw_ids or any(not isinstance(group, str) for group in raw_ids):
        raise ValueError("GROUP_IDS_REQUIRED")
    selected = set(raw_ids)
    if not selected <= {group.group_id for group in state.groups}:
        raise ValueError("UNKNOWN_REUSE_GROUP")
    result = state.copy()
    released = {face for group in result.groups if group.group_id in selected
                for face in group.first_faces + group.second_faces}
    result.groups = tuple(group for group in result.groups if group.group_id not in selected)
    result.correspondences = tuple(item for item in result.correspondences
                                   if item.source_face not in released and item.target_face not in released)
    result.correspondence_records = tuple(record for record in result.correspondence_records
                                          if record.get("group_id", record.get("group")) not in selected)
    result.invalidate()
    return result


def apply_reuse(request: dict[str, object], output: Path, deadline: float) -> dict[str, object]:
    check_deadline(deadline)
    snapshot = require_snapshot(request)
    metadata_source = snapshot
    action = request.get("action", "share")
    profile = _profile(request)
    operation_evidence: dict[str, object] = {}
    prepack: BudgetState | None = None
    roundoff = request.get("repair_packing_roundoff", False)
    if not isinstance(roundoff, bool):
        raise TypeError("BOOLEAN_REQUIRED:repair_packing_roundoff")
    if action != "exclusive" and (roundoff or "exclusive_layout" in request):
        raise ValueError("EXCLUSIVE_OPTIONS_REQUIRE_EXCLUSIVE_ACTION")
    if action == "share":
        path = request.get("proposal")
        if not isinstance(path, str):
            raise ValueError("CHECKED_PROPOSAL_PATH_REQUIRED")
        proposal = read_object(Path(path))
        checksum = proposal.pop("proposal_hash", None)
        if checksum != object_hash(proposal) or proposal.get("schema_version") != 1:
            raise ValueError("PROPOSAL_HASH_MISMATCH")
        if proposal.get("asset_id") != snapshot.asset_id or proposal.get("base_state_hash") != snapshot.state_hash:
            raise ValueError("STALE_PROPOSAL")
        source, proposed_profile = proposal.get("candidate_snapshot"), proposal.get("profile")
        if not isinstance(source, str) or not isinstance(proposed_profile, dict):
            raise ValueError("INVALID_CHECKED_PROPOSAL")
        candidate_snapshot = load_snapshot(Path(source))
        record = read_object(candidate_snapshot.root / "snapshot.json")
        if (candidate_snapshot.state_hash != proposal.get("candidate_state_hash")
                or candidate_snapshot.asset_id != snapshot.asset_id or record.get("parent_hash") != snapshot.state_hash):
            raise ValueError("PROPOSAL_CANDIDATE_MISMATCH")
        if "profile" in request and profile != proposed_profile:
            raise ValueError("PROPOSAL_PROFILE_MISMATCH:use uv.pack to change packing profile")
        profile = proposed_profile
        candidate = candidate_snapshot.state.copy()
        metadata_source = candidate_snapshot
    elif action == "exclusive":
        faces = _face_list(request.get("exclusive_faces"), snapshot.state)
        layout = request.get("exclusive_layout", "intrinsic")
        if layout == "preserve_patches":
            from .release import release_patches
            prepack, release_record = release_patches(snapshot.state, faces)
            operation_evidence["release"] = release_record
        elif layout == "intrinsic":
            prepack = exclusive_faces(snapshot.state, faces)
        else:
            raise ValueError("INVALID_EXCLUSIVE_LAYOUT")
        candidate = _packed(prepack, profile, deadline=deadline)
    elif action == "unshare":
        candidate = _packed(_unshare(snapshot.state, request.get("group_ids")), profile,deadline=deadline)
    else:
        raise ValueError("UNKNOWN_REUSE_ACTION")
    check_deadline(deadline)
    report = geometry_report(candidate, profile)
    if roundoff:
        from .release import repair_packing_roundoff
        assert prepack is not None
        candidate, report, repair_record = repair_packing_roundoff(prepack, candidate, profile, deadline, geometry_report)
        operation_evidence["packing_roundoff"] = repair_record
    if operation_evidence:
        write_json(output/"operation.json", operation_evidence)
    if not report["hard_valid"]:
        return _rejected(output, {"reason": "FULL_STATE_REVALIDATION_REJECTED", "geometry": report,
                                  "operation": operation_evidence})
    candidate.invalidate()
    check_deadline(deadline)
    metadata = _edited_metadata(metadata_source, f"reuse.{action}", profile)
    if operation_evidence:
        metadata["exclusive_operation"] = operation_evidence
    result = save_snapshot(candidate, output / "snapshot", metadata, snapshot)
    write_json(output / "geometry.json", report)
    return {**snapshot_result(result), "status": "OK", "execution_success": True,
            "geometry_passed": report["valid"], "hard_geometry_passed": True, "final_quality_passed": None,
            "requires_rebake": True, "report": str((output / "geometry.json").resolve())}


def pack_uv(request: dict[str, object], output: Path, deadline: float) -> dict[str, object]:
    check_deadline(deadline)
    snapshot = require_snapshot(request)
    profile = _profile(request)
    normalize = request.get("density_normalize", True)
    if not isinstance(normalize, bool):
        raise TypeError("BOOLEAN_REQUIRED:density_normalize")
    roundoff = request.get("repair_packing_roundoff", False)
    if not isinstance(roundoff, bool):
        raise TypeError("BOOLEAN_REQUIRED:repair_packing_roundoff")
    source = snapshot.state
    scales = request.get("face_density_scale", {})
    if not isinstance(scales, dict):
        raise TypeError("FACE_DENSITY_SCALE_OBJECT_REQUIRED")
    if scales:
        if normalize and not profile.get("balance_density", False):
            raise ValueError("DENSITY_SCALE_REQUIRES_BALANCE_OR_DISABLED_NORMALIZATION")
        from .density import scale_selected_owners
        source = scale_selected_owners(source, scales)
    backend = request.get("backend", "rectangles")
    packing_record: dict[str, object] | None = None
    if backend == "rectangles":
        candidate = _packed(source, profile, normalize, deadline)
    elif backend == "blender_concave":
        from ..runtime import read_runtime
        from .compact_packing import compact_pack

        root = Path(__file__).resolve().parents[3]
        blender = Path(str(request["blender"])) if "blender" in request else Path(str(read_runtime(root)["blender"]))
        if normalize or profile.get("balance_density", False):
            source = _packed(source, profile, normalize, deadline)
        candidate, packing_record = compact_pack(source, _integer(profile, "resolution", 256, 1),
            gap_pixels=_number(profile, "gap_pixels", 4), border_pixels=_number(profile, "border_pixels", 4),
            blender=blender, worker=root / "scripts/agent_pack_worker.py", output=output / "blender",
            deadline=deadline)
        write_json(output / "packing.json", packing_record)
    else:
        raise ValueError("UNKNOWN_PACKING_BACKEND")
    check_deadline(deadline)
    report = geometry_report(candidate, profile)
    repair_record: dict[str, object] | None = None
    if roundoff:
        from .release import repair_packing_roundoff
        candidate, report, repair_record = repair_packing_roundoff(source, candidate, profile, deadline, geometry_report)
        write_json(output/"packing_roundoff.json", repair_record)
    if not report["hard_valid"]:
        return _rejected(output, {"reason": "PACKED_GEOMETRY_REJECTED", "geometry": report,
                                  "packing_roundoff": repair_record, "packing_backend": backend})
    check_deadline(deadline)
    metadata = _edited_metadata(snapshot, "uv.pack", profile)
    metadata["packing_backend"] = backend
    if packing_record is not None:
        metadata["packing_record"] = packing_record
    if repair_record is not None:
        metadata["packing_roundoff"] = repair_record
    result = save_snapshot(candidate, output / "snapshot", metadata, snapshot)
    write_json(output / "geometry.json", report)
    return {**snapshot_result(result), "status": "OK", "execution_success": True,
            "geometry_passed": report["valid"], "hard_geometry_passed": True, "final_quality_passed": None,
            "requires_rebake": True, "packing_backend": backend,
            "report": str((output / "geometry.json").resolve())}
