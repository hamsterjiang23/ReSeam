"""Immutable, evidence-producing UV inspection and reflection reuse operations.

Nearest-point matches propose a map. Only the complete piecewise affine UV
intersection check authorizes an overlap; material acceptance remains separate.
"""

import math
import time
from dataclasses import asdict
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
from ..joint_types import Correspondence, ReuseGroup
from ..mesh import chart_labels
from ..surface_match import barycentric_coordinates, project_surface, reflect
from ..types import FloatArray, IntArray, MeshState
from ..validation import jacobians, topology_errors
from .correspondence import (
    common_harmonic_pair,
    face_corner_assignment,
    local_pair,
    normal_corner_transfer,
    topology_copy,
)
from .state import (
    Snapshot,
    check_deadline,
    load_snapshot,
    object_hash,
    read_object,
    require_snapshot,
    save_snapshot,
    snapshot_result,
    write_json,
)

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
    write_json(artifact, {**snapshot_result(snapshot), "geometry": report, "regions": regions,
                          "chart_adjacency": adjacency,
                          "reuse_groups": [asdict(group) for group in snapshot.state.groups],
                          "footprint_definition": "area of actual UV triangle union, not occupied color pixels"})
    return {**snapshot_result(snapshot), "status": "OK", "execution_success": True,
            "geometry_passed": report["valid"], "final_quality_passed": None,
            "report": str(artifact.resolve()), "chart_count": len(regions),
            "shared_group_count": len(snapshot.state.groups), "unique_footprint": report.get("unique_coverage")}


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
) -> tuple[FloatArray, IntArray] | None:
    mesh = surface.mesh
    a, b = np.unique(mesh.faces[list(first)]), np.unique(mesh.faces[list(second)])
    if len(a) != len(b) or len(first) != len(second):
        return None
    distance, raw_indices = KDTree(mesh.vertices[a]).query(reflect(mesh.vertices[b], normal, offset))
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
        points, faces, _ = project_surface(reflect(mesh.vertices[ids], normal, offset), target_triangles)
        coordinates = barycentric_coordinates(target_triangles[faces], points)
        uv = np.einsum("nc,ncd->nd", coordinates, source_uv[faces])
        sampled.update((int(vertex), position) for vertex, position in zip(ids, uv, strict=True))
    corners = np.asarray([[sampled[int(vertex)] for vertex in mesh.faces[face]] for face in second])
    return corners, surface.uv_faces[list(second)] + len(surface.uv)


def _surface_constraints(
    state: BudgetState, first: tuple[int, ...], second: tuple[int, ...], normal: FloatArray, offset: float,
    group_id: str, profile: dict[str, object], deadline: float,
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
        target = triangles[list(target_ids)]
        target_normals = normals[list(target_ids)]
        for source in source_ids:
            check_deadline(deadline)
            original = samples @ triangles[source]
            angles = np.degrees(np.arccos(np.clip(
                target_normals @ reflect(normals[source], normal, 0), -1, 1)))
            compatible = np.flatnonzero(angles <= angle_limit + 1e-8)
            # When the compatible domain is empty, keep the old nearest-face
            # measurements only as rejection evidence; never emit constraints.
            query_faces = compatible if len(compatible) else np.arange(len(target_ids))
            projected, matched, distances = project_surface(
                reflect(original, normal, offset), target[query_faces])
            matched = query_faces[matched]
            bary = barycentric_coordinates(target[matched], projected)
            returned, _, _ = project_surface(reflect(projected, normal, offset), triangles[list(source_ids)])
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
                    "target_selection": "nearest_surface_within_reflected_face_normal_gate",
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
    for key in ("bake", "bake_result", "quality", "quality_result", "validation"):
        metadata.pop(key, None)
    metadata.update(last_operation=operation, geometry_profile=profile, requires_rebake=True)
    return metadata


def _rejected(output: Path, report: dict[str, object]) -> dict[str, object]:
    path = output / "check.json"
    write_json(path, report)
    return {"status": "REJECTED", "execution_success": True, "geometry_passed": False,
            "final_quality_passed": None, "report": str(path.resolve()), "reason": report.get("reason")}


def check_pair(request: dict[str, object], output: Path, deadline: float) -> dict[str, object]:
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
    if mode not in ("auto", "exact", "approximate", "topology", "common_domain", "normal_corner"):
        raise ValueError("INVALID_CORRESPONDENCE_MODE")
    group_id = "reuse:" + object_hash({"state": snapshot.state_hash, "first": first, "second": second,
                                       "normal": normal.tolist(), "offset": offset})[:16]
    constraints, records, failed = _surface_constraints(base, first, second, normal, offset,
                                                        group_id, profile, deadline)
    if failed:
        return _rejected(output, {"reason": "SURFACE_CORRESPONDENCE_REJECTED", "failed_samples": failed,
                                  "sample_count": len(records), "conclusion": "this selected pair and plane only"})
    copied = _exact_copy(base.surface, first, second, normal, offset) if mode in ("auto", "exact") else None
    if copied is None and mode == "exact":
        return _rejected(output, {"reason": "EXACT_TOPOLOGY_NOT_MATCHED",
                                  "conclusion": "approximate surface sharing remains unevaluated"})
    ids = first + second
    split = len(first)
    iterations = _integer(request, "refine_iterations", 0)
    diagnostics: dict[str, object] = {"query_count": len(records), "failed_samples": [],
                                    "exceptions_applied_before_mapping": list(exceptions)}
    choices: list[tuple[float, BudgetState, dict[str, object], str]] = []
    attempt_reports: list[dict[str, object]] = []
    names = (["exact_reflection_copy"] if copied is not None else
             ["surface_barycentric_transfer", "topology_copy", "normal_corner_transfer", "face_corner_assignment"]
             if mode == "auto" else ["surface_barycentric_transfer"] if mode == "approximate" else
             ["topology_copy"] if mode == "topology" else ["normal_corner_transfer"] if mode == "normal_corner" else
             ["common_harmonic_domain"])
    for name in names:
        check_deadline(deadline)
        try:
            if name == "common_harmonic_domain":
                local = common_harmonic_pair(base.surface, first, second, normal, offset)
            else:
                mapped = copied if name == "exact_reflection_copy" else (
                    _transfer_surface_uv(base.surface,first,second,normal,offset,deadline)
                    if name == "surface_barycentric_transfer" else
                    topology_copy(base.surface,first,second,normal,offset,_number(profile,"correspondence_fraction",.005),
                                  min(deadline,time.monotonic()+3)) if name == "topology_copy" else
                    normal_corner_transfer(base.surface,first,second,normal,offset,_number(profile,"normal_angle_degrees",45),deadline)
                    if name == "normal_corner_transfer" else
                    face_corner_assignment(base.surface,first,second,normal,offset,
                                          _number(profile,"correspondence_fraction",.005),_number(profile,"normal_angle_degrees",45),deadline))
                if mapped is None:
                    attempt_reports.append({"method": name, "reason": "NO_BIJECTIVE_ASSIGNMENT"})
                    continue
                local = local_pair(base.surface, first, second, mapped)
            signs = np.sign(np.linalg.det(jacobians(local))).astype(np.int64)
            orientation = 1 if np.count_nonzero(signs[:split] > 0) >= split/2 else -1
            bad = np.concatenate((np.flatnonzero(signs[:split] != orientation),
                                  split+np.flatnonzero(signs[split:] != -orientation)))
            if len(bad):
                attempt_reports.append({"method":name,"reason":"INCONSISTENT_MEMBER_ORIENTATION",
                                        "face_signs":signs.tolist(),"suggested_exclusive_faces":[ids[int(i)] for i in bad]})
                continue
            group = ReuseGroup(group_id,first,second,(float(normal[0]),float(normal[1]),float(normal[2])),
                               offset,orientation,-orientation)
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
                candidate.correspondence_records += records
                candidate = _packed(candidate,profile,deadline=deadline)
                report = geometry_report(candidate,profile)
                attempt_reports.append({"method":variant,"geometry":report})
                active = report.get("active_reuse_group_ids",[])
                if report["hard_valid"] and isinstance(active,list) and group_id in active:
                    score = _number(report,"mean_shape_distortion",0)+_number(report,"max_overlap_geometry_error",0)
                    choices.append((score,candidate,report,variant))
        except (ValueError, RecursionError) as error:
            attempt_reports.append({"method": name, "reason": str(error)})
            continue
        if choices:
            break
    diagnostics["attempts"] = attempt_reports
    if not choices:
        return _rejected(output, {"reason": "AFFINE_UV_OR_PACKING_REJECTED", "diagnostics": diagnostics,
                                  "conclusion": "finite candidates rejected; not mathematical infeasibility"})
    _, candidate, report, selected = min(choices, key=lambda choice: choice[0])
    check_deadline(deadline)
    checked_snapshot = save_snapshot(candidate, output / "checked_snapshot",
                                     _edited_metadata(snapshot, "reuse.check_pair", profile), snapshot)
    proposal: dict[str, object] = {
        "schema_version": 1, "asset_id": snapshot.asset_id, "base_state_hash": snapshot.state_hash,
        "candidate_state_hash": checked_snapshot.state_hash,
        "candidate_snapshot": str(checked_snapshot.root / "snapshot.json"),
        "first_faces": list(first), "second_faces": list(second), "exclusive_faces": list(exceptions),
        "profile": profile, "selected_method": selected, "geometry": report,
        "diagnostics": diagnostics, "final_quality_passed": None,
    }
    proposal["proposal_hash"] = object_hash(proposal)
    path = output / "proposal.json"
    write_json(path, proposal)
    return {"status": "OK", "execution_success": True, "proposal": str(path.resolve()),
            "geometry_passed": report["valid"], "hard_geometry_passed": True, "final_quality_passed": None,
            "selected_method": selected, "shared_group_id": group_id, "report": str(path.resolve())}


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
    action = request.get("action", "share")
    profile = _profile(request)
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
    elif action == "exclusive":
        candidate = _packed(exclusive_faces(snapshot.state, _face_list(request.get("exclusive_faces"), snapshot.state)),
                            profile,deadline=deadline)
    elif action == "unshare":
        candidate = _packed(_unshare(snapshot.state, request.get("group_ids")), profile,deadline=deadline)
    else:
        raise ValueError("UNKNOWN_REUSE_ACTION")
    check_deadline(deadline)
    report = geometry_report(candidate, profile)
    if not report["hard_valid"]:
        return _rejected(output, {"reason": "FULL_STATE_REVALIDATION_REJECTED", "geometry": report})
    candidate.invalidate()
    check_deadline(deadline)
    result = save_snapshot(candidate, output / "snapshot", _edited_metadata(snapshot, f"reuse.{action}", profile), snapshot)
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
    candidate = _packed(snapshot.state, profile, normalize,deadline)
    check_deadline(deadline)
    report = geometry_report(candidate, profile)
    if not report["hard_valid"]:
        return _rejected(output, {"reason": "PACKED_GEOMETRY_REJECTED", "geometry": report})
    check_deadline(deadline)
    result = save_snapshot(candidate, output / "snapshot", _edited_metadata(snapshot, "uv.pack", profile), snapshot)
    write_json(output / "geometry.json", report)
    return {**snapshot_result(result), "status": "OK", "execution_success": True,
            "geometry_passed": report["valid"], "hard_geometry_passed": True, "final_quality_passed": None,
            "requires_rebake": True, "report": str((output / "geometry.json").resolve())}
