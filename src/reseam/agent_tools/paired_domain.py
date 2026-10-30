"""Anchored paired charts, reconstructed independently of old UV fragments.

Cuts are temporary geometry identities. Only the resulting original face-corner
UV coordinates are embedded in the immutable parent mesh. UV containment defines
the proposed surface map; full overlap validation still decides authorization.
"""

import time
from collections.abc import Iterator
from pathlib import Path

import numpy as np

from ..budget_correspondence import update_uv_correspondences
from ..budget_pair import BudgetPairProblem, solve_budget_pair
from ..budget_state import BudgetState, embed_region
from ..joint_types import Correspondence, ReuseGroup, SimilarityTransform
from ..mesh import make_mesh, state_from_corner_uv
from ..reuse_transform import similarity_transform, transform_parity
from ..types import FloatArray, IntArray, MeshState
from ..validation import jacobians
from .regions import selected_faces
from .reparameterize import boundary_loop, harmonic_uv
from .state import (
    Snapshot,
    check_deadline,
    object_hash,
    require_snapshot,
    save_snapshot,
    write_json,
)


def _anchor_vertices(
    raw: object, faces: tuple[int, ...], cut_faces: IntArray, side: str,
) -> list[int]:
    if not isinstance(raw, list) or len(raw) < 3:
        raise ValueError("PAIRED_DOMAIN_REQUIRES_THREE_BOUNDARY_ANCHORS")
    rows = {face: index for index, face in enumerate(faces)}
    vertices: list[int] = []
    for pair in raw:
        value = pair.get(side) if isinstance(pair, dict) else None
        if (not isinstance(value, list) or len(value) != 2
                or any(type(item) is not int for item in value)
                or value[0] not in rows or not 0 <= value[1] < 3):
            raise ValueError(f"INVALID_PAIRED_BOUNDARY_CORNER:{side}")
        vertices.append(int(cut_faces[rows[value[0]], value[1]]))
    if len(set(vertices)) != len(vertices):
        raise ValueError(f"DUPLICATE_PAIRED_BOUNDARY_ANCHOR:{side}")
    return vertices


def _arc(loop: list[int], start: int, end: int, direction: int) -> list[int]:
    result = [start]
    while result[-1] != end:
        result.append((result[-1] + direction) % len(loop))
        if len(result) > len(loop):
            raise ValueError("INVALID_PAIRED_BOUNDARY_ARC")
    return result


def _arc_lengths(surface: MeshState, loop: list[int], indices: list[int]) -> FloatArray:
    points = surface.mesh.vertices[np.asarray(loop)[indices]]
    lengths = np.linalg.norm(np.diff(points, axis=0), axis=1)
    if np.any(lengths <= 0):
        raise ValueError("ZERO_LENGTH_PAIRED_BOUNDARY_EDGE")
    values = np.concatenate(([0.], np.cumsum(lengths)))
    return values / values[-1]


def _boundary_map(
    master: MeshState, slave: MeshState, master_loop: list[int], slave_loop: list[int],
    master_anchors: list[int], slave_anchors: list[int], boundary_uv: FloatArray, parity: int,
) -> tuple[FloatArray, dict[str, object]]:
    master_indices = [master_loop.index(vertex) for vertex in master_anchors]
    slave_indices = [slave_loop.index(vertex) for vertex in slave_anchors]
    order = np.argsort(master_indices)
    a = [master_indices[int(index)] for index in order]
    b = [slave_indices[int(index)] for index in order]
    forward = (np.roll(b, -1) - b) % len(slave_loop)
    reverse = (np.asarray(b) - np.roll(b, -1)) % len(slave_loop)
    direction = (1 if int(forward.sum()) == len(slave_loop) else
                 -1 if int(reverse.sum()) == len(slave_loop) else 0)
    if direction == 0:
        raise ValueError("PAIRED_ANCHORS_NOT_CYCLICALLY_MONOTONE")
    if direction != parity:
        raise ValueError("PAIRED_ANCHOR_DIRECTION_DISAGREES_WITH_TRANSFORM")
    result = np.empty((len(slave_loop), 2))
    arcs: list[dict[str, object]] = []
    for index in range(len(a)):
        next_index = (index + 1) % len(a)
        source_arc = _arc(master_loop, a[index], a[next_index], 1)
        target_arc = _arc(slave_loop, b[index], b[next_index], direction)
        source_s = _arc_lengths(master, master_loop, source_arc)
        target_s = _arc_lengths(slave, slave_loop, target_arc)
        source_uv = boundary_uv[source_arc]
        result[target_arc, 0] = np.interp(target_s, source_s, source_uv[:, 0])
        result[target_arc, 1] = np.interp(target_s, source_s, source_uv[:, 1])
        arcs.append({"master_boundary_indices": source_arc, "slave_boundary_indices": target_arc,
                     "parameter": "normalized original3D arc length between paired anchors"})
    return result, {"anchor_order_in_master_loop": order.tolist(),
                    "slave_boundary_direction": direction, "arcs": arcs}


def _master_uv(surface: MeshState, faces: tuple[int, ...], temporary: MeshState) -> FloatArray:
    coordinates = surface.uv[surface.uv_faces[list(faces)]]
    points = np.full((len(temporary.mesh.vertices), 2), np.nan)
    for index, triangle in enumerate(temporary.mesh.faces):
        for corner, vertex in enumerate(triangle):
            previous = points[vertex]
            current = coordinates[index, corner]
            if np.isfinite(previous).all() and not np.allclose(previous, current, atol=1e-12, rtol=1e-10):
                raise ValueError("MASTER_UV_HAS_OLD_INTERNAL_SEAMS:reparameterize_or_use_convex")
            points[vertex] = current
    if not np.isfinite(points).all():
        raise ValueError("MASTER_UV_UNREFERENCED_TEMPORARY_VERTEX")
    return points


def paired_layouts(
    surface: MeshState, first: tuple[int, ...], second: tuple[int, ...],
    request: dict[str, object], parity: int, deadline: float,
) -> Iterator[tuple[str, MeshState | None, dict[str, object]]]:
    """Yield explicit master-domain and optional convex-domain attempts."""
    from .regional_cut import cut_region

    check_deadline(deadline)
    cuts = [cut_region(surface, faces, request.get(f"{side}_seam_edges", []))
            for side, faces in (("first", first), ("second", second))]
    loops = [boundary_loop(cut.surface.mesh.faces) for cut in cuts]
    anchors = [_anchor_vertices(request.get("boundary_anchors"), cut.face_ids, cut.surface.mesh.faces, side)
               for cut, side in zip(cuts, ("first", "second"), strict=True)]
    if any(not set(values) <= set(loop) for values, loop in zip(anchors, loops, strict=True)):
        raise ValueError("PAIRED_ANCHOR_MUST_LIE_ON_CUT_BOUNDARY")
    master_name = request.get("master_member", "first")
    if master_name not in ("first", "second"):
        raise ValueError("INVALID_PAIRED_MASTER_MEMBER")
    master_index = 0 if master_name == "first" else 1
    slave_index = 1-master_index
    domain = request.get("domain", "master_uv")
    if domain not in ("master_uv", "convex"):
        raise ValueError("INVALID_PAIRED_DOMAIN")
    fallback = request.get("allow_convex_fallback", True)
    if not isinstance(fallback, bool):
        raise TypeError("BOOLEAN_REQUIRED:allow_convex_fallback")
    domains = [domain] + (["convex"] if domain == "master_uv" and fallback else [])
    for current in domains:
        check_deadline(deadline)
        record: dict[str, object] = {
            "domain": current, "requested_domain": domain, "fallback_used": current != domain,
            "master_member": master_name, "geometry_modified": False,
            "first_cut_edges": [list(edge) for edge in cuts[0].seam_edges],
            "second_cut_edges": [list(edge) for edge in cuts[1].seam_edges],
            "boundary_anchors": request.get("boundary_anchors"),
            "boundary_vertex_counts": [len(loop) for loop in loops],
            "association": "final_piecewise_affine_UV_containment",
            "nearest_point_precheck_used": False,
        }
        try:
            master, slave = cuts[master_index], cuts[slave_index]
            master_faces = tuple(range(len(master.face_ids)))
            if current == "master_uv":
                master_points = _master_uv(surface, master.face_ids, master.surface)
                master_corners = master_points[master.surface.mesh.faces]
                master_ids = master.surface.mesh.faces
            else:
                master_corners, master_ids, _ = harmonic_uv(master.surface, master_faces)
                master_points = np.empty((len(master.surface.mesh.vertices), 2))
                master_points[master.surface.mesh.faces] = master_corners
            boundary_values, mapping = _boundary_map(
                master.surface, slave.surface, loops[master_index], loops[slave_index],
                anchors[master_index], anchors[slave_index], master_points[loops[master_index]], parity)
            slave_corners, slave_ids, _ = harmonic_uv(
                slave.surface, tuple(range(len(slave.face_ids))), boundary_values)
            corners = [master_corners, slave_corners] if master_index == 0 else [slave_corners, master_corners]
            ids = [master_ids, slave_ids] if master_index == 0 else [slave_ids, master_ids]
            local = state_from_corner_uv(make_mesh(surface.mesh.vertices, surface.mesh.faces[list(first+second)]),
                                        np.concatenate(corners), corner_ids=np.concatenate(
                                            (ids[0], ids[1] + int(ids[0].max()) + 1)))
            determinants = np.linalg.det(jacobians(local))
            if (not np.isfinite(determinants).all() or np.any(determinants == 0)
                    or len(set(np.sign(determinants[:len(first)]))) != 1
                    or len(set(np.sign(determinants[len(first):]))) != 1):
                raise ValueError("PAIRED_DOMAIN_FOLDED_OR_DEGENERATE")
            if int(np.sign(determinants[0])*np.sign(determinants[len(first)])) != parity:
                raise ValueError("PAIRED_DOMAIN_ORIENTATION_PARITY_MISMATCH")
            record["boundary_mapping"] = mapping
            check_deadline(deadline)
            yield f"paired_domain_{current}", local, record
        except ValueError as error:
            record["reason"] = str(error)
            yield f"paired_domain_{current}", None, record


def _faces(request: dict[str, object], snapshot: Snapshot, side: str) -> tuple[int, ...]:
    from .geometry import _pair_faces
    region = request.get(f"{side}_region_id")
    if region is not None:
        if f"{side}_faces" in request or f"{side}_chart" in request:
            raise ValueError("SELECT_PAIR_REGION_OR_FACES_NOT_BOTH")
        return tuple(selected_faces({"region_id": region}, snapshot))
    return _pair_faces(request, snapshot.state, side)


def _protected(snapshot: Snapshot, request: dict[str, object]) -> set[int]:
    from .geometry import _face_list
    result = set(_face_list(request.get("protected_faces", []), snapshot.state, allow_empty=True))
    regions = snapshot.metadata.get("regions", [])
    if isinstance(regions, list):
        for region in regions:
            if isinstance(region, dict):
                value = region.get("faces", []) if region.get("role") == "protected" else region.get("protected_faces", [])
                result.update(_face_list(value, snapshot.state, allow_empty=True))
    return result


def _uv_correspondences(
    candidate: BudgetState, group: ReuseGroup, profile: dict[str, object], deadline: float,
) -> tuple[BudgetState | None, dict[str, object]]:
    from .geometry import _number
    # Existing groups keep their own evidence; this operation constructs only the
    # newly selected pair and cannot replace old group associations accidentally.
    selected = candidate.copy()
    selected.groups = (group,)
    updated = update_uv_correspondences(
        selected, position_fraction=_number(profile, "correspondence_fraction", .005),
        cycle_fraction=_number(profile, "correspondence_fraction", .005),
        normal_angle_degrees=_number(profile, "normal_angle_degrees", 45), deadline=deadline)
    if updated.state is None:
        return None, {**updated.diagnostics, "status": updated.status, "records": list(updated.records)}
    updated.state.groups = candidate.groups
    affected = set(group.first_faces+group.second_faces)
    updated.state.correspondences = tuple(c for c in candidate.correspondences
        if c.source_face not in affected and c.target_face not in affected) + updated.state.correspondences
    updated.state.correspondence_records = tuple(r for r in candidate.correspondence_records
        if r.get("source_face") not in affected and r.get("target_face") not in affected) + updated.records
    updated.state.evidence = candidate.evidence.copy()
    return updated.state, {**updated.diagnostics, "status": updated.status}


def check_paired_domain(request: dict[str, object], output: Path, deadline: float) -> dict[str, object]:
    """Produce the same hash-bound checked proposal contract as legacy modes."""
    from .geometry import (
        _edited_metadata,
        _face_list,
        _integer,
        _number,
        _packed,
        _plane,
        _profile,
        _rejected,
        geometry_report,
    )
    check_deadline(deadline)
    snapshot = require_snapshot(request)
    first, second = (_faces(request, snapshot, side) for side in ("first", "second"))
    affected = set(first+second)
    if set(first).intersection(second):
        raise ValueError("PAIR_MEMBERS_MUST_BE_DISJOINT")
    if any(affected.intersection(group.first_faces+group.second_faces) for group in snapshot.state.groups):
        raise ValueError("PAIR_CROSSES_EXISTING_GROUP:unshare first")
    if affected.intersection(_protected(snapshot, request)):
        raise ValueError("PAIRED_DOMAIN_INTERSECTS_PROTECTED_FACES")
    if _face_list(request.get("exclusive_faces", []), snapshot.state, allow_empty=True):
        raise ValueError("PAIRED_DOMAIN_REQUIRES_WHOLE_REGIONS:define_natural_subregions")
    normal, offset = _plane(request)
    profile = _profile(request)
    transform: SimilarityTransform | None = (None if request.get("first_to_second") is None else
                                              similarity_transform(request["first_to_second"]))
    group_id = "reuse:" + object_hash({"state": snapshot.state_hash, "mode": "paired_domain", "request": request})[:16]
    attempts: list[dict[str, object]] = []
    choices: list[tuple[float, BudgetState, dict[str, object], str, dict[str, object]]] = []
    for name, local, evidence in paired_layouts(snapshot.state.surface, first, second, request,
                                                transform_parity(transform), deadline):
        if local is None:
            attempts.append({"method": name, **evidence})
            continue
        signs = np.sign(np.linalg.det(jacobians(local))).astype(np.int64)
        group = ReuseGroup(group_id, first, second, (float(normal[0]), float(normal[1]), float(normal[2])),
                           offset, int(signs[0]), int(signs[len(first)]), transform)
        initial = embed_region(snapshot.state, local, first+second, group)
        initial, correspondence_evidence = _uv_correspondences(initial, group, profile, deadline)
        if initial is None:
            attempts.append({"method": name, **evidence, "reason": "UV_INDUCED_CORRESPONDENCE_REJECTED",
                             "correspondence": correspondence_evidence})
            continue
        alternatives = [(name, initial)]
        iterations = _integer(request, "refine_iterations", 0)
        if iterations:
            local_index = {face: index for index, face in enumerate(first+second)}
            constraints = tuple(Correspondence(local_index[c.source_face], c.source_barycentric,
                local_index[c.target_face], c.target_barycentric, c.weight)
                for c in initial.correspondences if c.source_face in affected and c.target_face in affected)
            members = np.concatenate((np.zeros(len(first), dtype=np.int64), np.ones(len(second), dtype=np.int64)))
            seconds = min(deadline-time.monotonic(), _number(request, "solve_seconds", 30))
            check_deadline(deadline)
            try:
                problem = BudgetPairProblem(local, signs, constraints, member_ids=members)
                solved = solve_budget_pair(problem, seconds, iterations)
                alternatives.append((name+"_refined", embed_region(snapshot.state, solved.state, first+second, group)))
                evidence["solver"] = {"status": solved.status, "history": solved.history, "coupling_rms": solved.coupling_rms}
            except ValueError as error:
                # A numerical refinement failure must not discard the complete
                # unrefined layout, which still receives independent validation.
                evidence["solver"] = {"status": "INITIALIZATION_REJECTED", "reason": str(error)}
        for variant, candidate in alternatives:
            check_deadline(deadline)
            candidate = _packed(candidate, profile, deadline=deadline)
            candidate, correspondence_evidence = _uv_correspondences(candidate, group, profile, deadline)
            if candidate is None:
                attempts.append({"method": variant, **evidence, "reason": "PACKED_CORRESPONDENCE_REJECTED",
                                 "correspondence": correspondence_evidence})
                continue
            report = geometry_report(candidate, profile)
            attempt = {"method": variant, **evidence, "geometry": report, "correspondence": correspondence_evidence}
            attempts.append(attempt)
            active = report.get("active_reuse_group_ids", [])
            if report["hard_valid"] and isinstance(active, list) and group_id in active:
                score = _number(report, "mean_shape_distortion", 0) + _number(report, "max_overlap_geometry_error", 0)
                choices.append((score, candidate, report, variant, evidence))
        if choices:
            break
    if not choices:
        return _rejected(output, {"reason": "PAIRED_DOMAIN_REJECTED", "attempts": attempts,
                                  "conclusion": "selected cuts and anchors only; not mathematical infeasibility"})
    _, candidate, report, method, evidence = min(choices, key=lambda item: item[0])
    check_deadline(deadline)
    metadata = _edited_metadata(snapshot, "reuse.check_pair:paired_domain", profile)
    metadata["paired_domain"] = evidence
    child = save_snapshot(candidate, output / "checked_snapshot", metadata, snapshot)
    proposal: dict[str, object] = {
        "schema_version": 1, "asset_id": snapshot.asset_id, "base_state_hash": snapshot.state_hash,
        "candidate_state_hash": child.state_hash, "candidate_snapshot": str(child.root / "snapshot.json"),
        "first_faces": list(first), "second_faces": list(second), "exclusive_faces": [],
        "profile": profile, "selected_method": method, "geometry": report, "first_to_second": transform,
        "semantic_group_id": group_id, "shared_group_ids": [group_id],
        "diagnostics": {"attempts": attempts, "nearest_point_precheck_used": False}, "final_quality_passed": None,
    }
    proposal["proposal_hash"] = object_hash(proposal)
    path = output / "proposal.json"
    write_json(path, proposal)
    return {"status": "OK", "execution_success": True, "proposal": str(path.resolve()),
            "geometry_passed": report["valid"], "hard_geometry_passed": True, "final_quality_passed": None,
            "selected_method": method, "shared_group_id": group_id, "shared_group_ids": [group_id],
            "report": str(path.resolve())}
