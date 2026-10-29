"""Real tiny UV states, persisted proposals, and transaction invariants."""

import time
from pathlib import Path

import numpy as np
import pytest

from reseam.agent_tools.geometry import (
    _surface_constraints,
    apply_reuse,
    check_pair,
    find_candidates,
    geometry_report,
    inspect_uv,
    pack_uv,
)
from reseam.agent_tools.state import Snapshot, load_snapshot, read_object, save_snapshot, write_json
from reseam.budget_state import BudgetState, fingerprint, validate_budget_safety
from reseam.mesh import chart_labels, make_mesh, state_from_corner_uv
from reseam.surface_match import project_surface, reflect


def _panels(different: bool = False, center_vertex: bool = False) -> BudgetState:
    vertices = np.asarray([[-1., 0, 0], [-1, 1, 0], [-1, 1, 1], [-1, 0, 1],
                           [1., 0, 0], [1, 1, 0], [1, 1, 1], [1, 0, 1]])
    if center_vertex:
        vertices = np.concatenate((vertices, [[1., .5, .5]]))
        target = [[4, 8, 5], [5, 8, 6], [6, 8, 7], [7, 8, 4]]
    else:
        target = [[4, 7, 5], [5, 7, 6]] if different else [[4, 6, 5], [4, 7, 6]]
    faces = np.asarray([[0, 1, 2], [0, 2, 3], *target], dtype=np.int64)
    mesh = make_mesh(vertices, faces)
    corners = .1 + .25 * vertices[faces, 1:]
    corners[2:, :, 0] += .5
    corners[2:, :, 1] = .45 - corners[2:, :, 1]
    surface = state_from_corner_uv(mesh, corners, seam_ids=frozenset())
    return BudgetState(surface)


def _source(tmp_path: Path, state: BudgetState | None = None) -> Snapshot:
    return save_snapshot(_panels() if state is None else state, tmp_path / "source", {})


def _deadline() -> float:
    return time.monotonic() + 30


def _request(source: Snapshot) -> dict[str, object]:
    return {"snapshot": str(source.root), "first_faces": [0, 1],
            "second_faces": list(range(2, len(source.state.surface.mesh.faces))),
            "plane_normal": [1., 0, 0], "plane_offset": 0,
            "profile": {"resolution": 64, "gap_pixels": 2, "border_pixels": 2}}


def _checked_shared(tmp_path: Path, state: BudgetState | None = None) -> tuple[Snapshot, Snapshot]:
    original = _source(tmp_path, state)
    checked = check_pair(_request(original), tmp_path / "check", _deadline())
    assert checked["status"] == "OK", checked
    applied = apply_reuse({"snapshot": str(original.root), "proposal": checked["proposal"]},
                          tmp_path / "apply", _deadline())
    assert applied["status"] == "OK", applied
    return original, load_snapshot(Path(str(applied["snapshot"])))


def test_exact_reflection_copy_is_real_and_parent_is_immutable(tmp_path: Path) -> None:
    original, shared = _checked_shared(tmp_path)
    assert len(shared.state.groups) == 1
    assert len(shared.state.correspondence_records) == 28
    report = geometry_report(shared.state, {"resolution": 64, "gap_pixels": 2, "border_pixels": 2})
    assert report["valid"], report
    assert report["reuse_factor"] == pytest.approx(2)
    assert report["active_reuse_groups"] == 1
    assert report["max_overlap_geometry_error"] == pytest.approx(0)
    assert np.array_equal(original.state.surface.mesh.vertices, shared.state.surface.mesh.vertices)
    assert np.array_equal(original.state.surface.mesh.faces, shared.state.surface.mesh.faces)
    assert fingerprint(load_snapshot(original.root).state) == fingerprint(original.state)
    assert not original.state.groups
    checked = read_object(tmp_path / "check" / "proposal.json")
    assert checked["selected_method"] == "exact_reflection_copy"
    assert checked["final_quality_passed"] is None


@pytest.mark.parametrize("center_vertex", [False, True])
def test_surface_transfer_accepts_different_triangulations_and_vertex_counts(
    tmp_path: Path, center_vertex: bool,
) -> None:
    original, shared = _checked_shared(tmp_path, _panels(different=True, center_vertex=center_vertex))
    checked = read_object(tmp_path / "check" / "proposal.json")
    assert checked["selected_method"] == "surface_barycentric_transfer"
    assert np.array_equal(shared.state.surface.mesh.faces, original.state.surface.mesh.faces)
    report = geometry_report(shared.state, {"resolution": 64, "gap_pixels": 2, "border_pixels": 2})
    assert report["valid"], report
    assert report["reuse_factor"] == pytest.approx(2)
    assert report["overlap_cells"] == 4


def test_vertex_matches_do_not_prove_interior_mapping(tmp_path: Path) -> None:
    state = _panels(different=True)
    corner = int(state.surface.uv_faces[0, 2])
    state.surface.uv[corner] += (.15, .02)
    source = _source(tmp_path, state)
    result = check_pair(_request(source), tmp_path / "check", _deadline())
    assert result["status"] == "REJECTED"
    diagnostic = read_object(Path(str(result["report"])))
    assert diagnostic["reason"] == "AFFINE_UV_OR_PACKING_REJECTED"
    assert "OVERLAP_CORRESPONDENCE" in str(diagnostic)
    assert not (tmp_path / "check" / "checked_snapshot").exists()
    assert fingerprint(load_snapshot(source.root).state) == fingerprint(state)


def test_stale_and_corrupt_proposals_do_not_write_snapshot(tmp_path: Path) -> None:
    source = _source(tmp_path)
    proposal = check_pair(_request(source), tmp_path / "check", _deadline())
    repacked = pack_uv({"snapshot": str(source.root)}, tmp_path / "pack", _deadline())
    with pytest.raises(ValueError, match="STALE_PROPOSAL"):
        apply_reuse({"snapshot": repacked["snapshot"], "proposal": proposal["proposal"]},
                    tmp_path / "stale", _deadline())
    assert not (tmp_path / "stale" / "snapshot").exists()
    path = Path(str(proposal["proposal"]))
    data = read_object(path)
    data["first_faces"] = [1]
    write_json(path, data)
    with pytest.raises(ValueError, match="PROPOSAL_HASH_MISMATCH"):
        apply_reuse({"snapshot": str(source.root), "proposal": str(path)}, tmp_path / "corrupt", _deadline())
    assert not (tmp_path / "corrupt" / "snapshot").exists()


def test_whole_face_exception_and_unshare_preserve_original_geometry(tmp_path: Path) -> None:
    original, shared = _checked_shared(tmp_path)
    before = fingerprint(shared.state)
    released = apply_reuse({"snapshot": str(shared.root), "action": "exclusive", "exclusive_faces": [0]},
                           tmp_path / "exclusive", _deadline())
    result = load_snapshot(Path(str(released["snapshot"])))
    assert result.state.groups[0].first_faces == (1,)
    assert result.state.groups[0].second_faces == (2, 3)
    assert result.state.surface.seams
    assert np.array_equal(original.state.surface.mesh.faces, result.state.surface.mesh.faces)
    assert np.array_equal(original.state.surface.mesh.vertices, result.state.surface.mesh.vertices)
    assert fingerprint(load_snapshot(shared.root).state) == before
    unshared = apply_reuse({"snapshot": str(shared.root), "action": "unshare",
                            "group_ids": [shared.state.groups[0].group_id]}, tmp_path / "unshare", _deadline())
    independent = load_snapshot(Path(str(unshared["snapshot"])))
    assert not independent.state.groups
    assert len(np.unique(chart_labels(independent.state.surface))) == 2
    assert geometry_report(independent.state)["valid"]
    assert not independent.state.correspondence_records


def test_unauthorized_overlap_and_wrong_plane_fail(tmp_path: Path) -> None:
    _, shared = _checked_shared(tmp_path)
    unauthorized = shared.state.copy()
    unauthorized.groups = ()
    report = geometry_report(unauthorized)
    assert not report["hard_valid"]
    assert "UNAUTHORIZED_OVERLAP" in str(report["errors"])
    result = pack_uv({"snapshot": str(shared.root)}, tmp_path / "repack", _deadline())
    assert result["status"] == "OK"
    repacked = load_snapshot(Path(str(result["snapshot"])))
    assert geometry_report(repacked.state)["reuse_factor"] == pytest.approx(2)
    source = _source(tmp_path / "another")
    request = _request(source)
    request["plane_offset"] = .2
    rejected = check_pair(request, tmp_path / "wrong_plane", _deadline())
    assert rejected["status"] == "REJECTED"
    assert rejected["reason"] == "SURFACE_CORRESPONDENCE_REJECTED"


def test_authored_mirror_kept_while_mixed_chart_fold_fails() -> None:
    state = _panels()
    first_vertices = np.unique(state.surface.uv_faces[:2])
    state.surface.uv[first_vertices, 0] = .45 - state.surface.uv[first_vertices, 0]
    before = fingerprint(state)
    report = geometry_report(state, {"resolution": 64, "gap_pixels": 2, "border_pixels": 2})
    assert report["valid"], report
    assert report["mirrored_chart_ids"] == [0]
    assert fingerprint(state) == before
    assert not validate_budget_safety(state, 64)["valid"]  # Historical default deliberately unchanged.
    vertex = int(state.surface.uv_faces[1, 2])
    state.surface.uv[vertex] = [.2, .1]
    assert not geometry_report(state)["hard_valid"]


def test_inspector_separates_charts_groups_and_unique_area(tmp_path: Path) -> None:
    _, shared = _checked_shared(tmp_path)
    result = inspect_uv({"snapshot": str(shared.root)}, tmp_path / "inspect", _deadline())
    assert result["chart_count"] == 2
    assert result["shared_group_count"] == 1
    report = read_object(Path(str(result["report"])))
    geometry = report["geometry"]
    assert isinstance(geometry, dict)
    assert geometry["reuse_factor"] == pytest.approx(2)


def test_discovery_is_a_proposal_only_and_exact_failure_keeps_approximate_option(tmp_path: Path) -> None:
    source = _source(tmp_path, _panels(different=True))
    found = find_candidates({"snapshot": str(source.root)}, tmp_path / "find", _deadline())
    assert found["candidate_count"] == 1
    document = read_object(Path(str(found["report"])))
    assert "\"authorized\": false" in (tmp_path / "find" / "candidates.json").read_text()
    assert document["total_coarse_candidates"] == 1
    request = _request(source)
    request["mode"] = "exact"
    result = check_pair(request, tmp_path / "exact", _deadline())
    assert result["reason"] == "EXACT_TOPOLOGY_NOT_MATCHED"


def test_timeout_does_not_touch_parent(tmp_path: Path) -> None:
    source = _source(tmp_path)
    before = fingerprint(source.state)
    with pytest.raises(TimeoutError):
        pack_uv({"snapshot": str(source.root)}, tmp_path / "timed_out", time.monotonic() - 1)
    assert fingerprint(load_snapshot(source.root).state) == before
    assert not (tmp_path / "timed_out").exists()


def test_optional_pair_solver_returns_checked_best_candidate(tmp_path: Path) -> None:
    source = _source(tmp_path, _panels(center_vertex=True))
    request = _request(source)
    request["refine_iterations"] = 2
    request["solve_seconds"] = 2
    result = check_pair(request, tmp_path / "refine", _deadline())
    assert result["status"] == "OK", result
    proposal = read_object(Path(str(result["proposal"])))
    assert "solver" in str(proposal["diagnostics"])
    assert proposal["final_quality_passed"] is None


def test_old_active_group_cannot_certify_a_dissolved_new_proposal(tmp_path: Path) -> None:
    initial = _panels()
    mesh = initial.surface.mesh
    combined = make_mesh(np.concatenate((mesh.vertices, mesh.vertices + [0, 0, 2])),
                         np.concatenate((mesh.faces, mesh.faces + len(mesh.vertices))))
    old_corners = initial.surface.uv[initial.surface.uv_faces]
    state = BudgetState(state_from_corner_uv(combined, np.concatenate((old_corners, old_corners)),
                                            seam_ids=frozenset()))
    source = _source(tmp_path, state)
    first_request = _request(source)
    first_request["second_faces"] = [2, 3]
    checked = check_pair(first_request, tmp_path / "first_check", _deadline())
    applied = apply_reuse({"snapshot": str(source.root), "proposal": checked["proposal"]},
                          tmp_path / "first_apply", _deadline())
    second_request = {**first_request, "snapshot": applied["snapshot"], "first_faces": [4, 5],
                      "second_faces": [6, 7], "exclusive_faces": [4, 5, 6, 7]}
    result = check_pair(second_request, tmp_path / "dissolved", _deadline())
    assert result["status"] == "REJECTED"
    assert not (tmp_path / "dissolved" / "checked_snapshot").exists()


def _folded_mirror(target_shift: float = 0) -> BudgetState:
    """Two 90-degree folded triangles, with the target face order reversed."""
    first = np.array([[-1., 0, 0], [-1, 1, 0], [-1, 0, 1], [-2, 0, 0]])
    vertices = np.concatenate((first, first * [-1, 1, 1]))
    faces = np.array([[0, 1, 2], [1, 0, 3], [5, 7, 4], [4, 6, 5]], dtype=np.int64)
    first_uv = np.array([[.2, .2], [.5, .2], [.2, .5], [.2, -.1]])
    uv = np.concatenate((first_uv, first_uv * [-1, 1] + [1.5, 0]))
    corners = uv[faces]
    if target_shift:
        # The compatible target triangle is slightly farther than the wrong
        # adjacent face at the shared vertex. Keep their original IDs separate.
        shifted = vertices[faces[3]] + [target_shift, 0, 0]
        faces[3] = np.arange(3) + len(vertices)
        vertices = np.concatenate((vertices, shifted))
    mesh = make_mesh(vertices, faces)
    return BudgetState(state_from_corner_uv(mesh, corners, seam_ids=frozenset()))


def test_folded_exact_mirror_does_not_reject_ambiguous_edge_vertices(tmp_path: Path) -> None:
    initial = _folded_mirror()
    triangles = initial.surface.mesh.vertices[initial.surface.mesh.faces]
    query = reflect(triangles[0, :1], np.array([1., 0, 0]), 0)
    _, naive_target, distance = project_surface(query, triangles[[2, 3]])
    assert naive_target.tolist() == [0]  # The earlier face is the wrong 90-degree side.
    assert distance[0] == pytest.approx(0)
    original, shared = _checked_shared(tmp_path, initial)
    records = shared.state.correspondence_records
    assert len(records) == 28
    assert all(float(str(record["normal_angle_degrees"])) < 1e-6 for record in records)
    assert {record["target_face"] for record in records if record["source_face"] == 0} == {3}
    assert geometry_report(shared.state, {"resolution": 64, "gap_pixels": 2, "border_pixels": 2})["valid"]
    assert fingerprint(load_snapshot(original.root).state) == fingerprint(initial)


def test_normal_gate_handles_near_tie_without_relaxing_distance_limit() -> None:
    for shift, accepted in ((1e-7, True), (.2, False)):
        state = _folded_mirror(shift)
        triangles = state.surface.mesh.vertices[state.surface.mesh.faces]
        _, naive_target, distance = project_surface(
            reflect(triangles[0, :1], np.array([1., 0, 0]), 0), triangles[[2, 3]])
        assert naive_target.tolist() == [0] and distance[0] == pytest.approx(0)
        constraints, records, failed = _surface_constraints(
            state, (0, 1), (2, 3), np.array([1., 0, 0]), 0, "fold", {}, _deadline())
        assert len(constraints) == 28 and len(records) == 28
        assert {record["target_face"] for record in records if record["source_face"] == 0} == {3}
        assert bool(failed) is not accepted
        if not accepted:
            assert any(float(str(record["surface_error_fraction"])) > .005 for record in failed)
            assert all(float(str(record["normal_angle_degrees"])) <= 45 for record in failed)


def test_no_normal_compatible_face_is_rejected_at_unchanged_angle_threshold(tmp_path: Path) -> None:
    state = _folded_mirror()
    constraints, records, failed = _surface_constraints(
        state, (0,), (2,), np.array([1., 0, 0]), 0, "fold", {}, _deadline())
    assert not constraints and len(failed) == len(records) == 14
    assert all(record["reason"] == "NO_NORMAL_COMPATIBLE_TARGET_FACE" for record in failed)
    assert all(record["normal_angle_limit_degrees"] == 45 for record in failed)
    assert all(float(str(record["normal_angle_degrees"])) == pytest.approx(90) for record in failed)
    source = _source(tmp_path, state)
    request = {**_request(source), "first_faces": [0], "second_faces": [2]}
    checked = check_pair(request, tmp_path / "check", _deadline())
    assert checked["status"] == "REJECTED"
    assert checked["reason"] == "SURFACE_CORRESPONDENCE_REJECTED"


def test_normal_compatible_surface_with_discontinuous_source_uv_still_fails_full_cells(tmp_path: Path) -> None:
    state = _panels(different=True)
    corners = state.surface.uv[state.surface.uv_faces].copy()
    corners[1, :, 0] -= .05
    ids = state.surface.uv_faces.copy()
    ids[1] += len(state.surface.uv)
    state.surface = state_from_corner_uv(state.surface.mesh, corners, corner_ids=ids)
    source = _source(tmp_path, state)
    checked = check_pair(_request(source), tmp_path / "check", _deadline())
    assert checked["status"] == "REJECTED"
    report = read_object(Path(str(checked["report"])))
    assert report["reason"] == "AFFINE_UV_OR_PACKING_REJECTED"
    assert "OVERLAP_CORRESPONDENCE" in str(report)
    assert not (tmp_path / "check" / "checked_snapshot").exists()
