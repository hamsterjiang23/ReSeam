"""Similarity is a persisted hypothesis, not permission to change or distort geometry."""

import time
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

from reseam.agent_tools.geometry import apply_reuse, check_pair, geometry_report, pack_uv
from reseam.agent_tools.material import bake_material_tool
from reseam.agent_tools.state import load_snapshot, object_hash, read_object, save_snapshot, write_json
from reseam.budget_correspondence import update_uv_correspondences
from reseam.budget_state import BudgetState, exclusive_faces, fingerprint
from reseam.joint_types import JointProfile, ReuseGroup, V2State
from reseam.joint_validation import validate_joint_geometry
from reseam.mesh import make_mesh, state_from_corner_uv
from reseam.reuse_transform import mapped_points, similarity_transform
from reseam.types import FloatArray


def _part(parity: int = 1, different: bool = False) -> tuple[BudgetState, FloatArray]:
    first = np.asarray([[0., 0, 0], [1.3, 0, 0], [1., .7, 0], [0, .5, 0]])
    q = np.asarray([[0., 0, 1], [1., 0, 0], [0, 1., 0]]) @ np.diag([parity, 1, 1])
    matrix = np.column_stack((q, [3., 2., -1.]))
    vertices = np.concatenate((first, first @ q.T+matrix[:, 3]))
    source_faces = np.asarray([[0, 1, 2], [0, 2, 3]], dtype=np.int64)
    target_faces = np.asarray([[0, 1, 3], [1, 2, 3]], dtype=np.int64) if different else source_faces.copy()
    if parity < 0:
        target_faces = target_faces[:, [0, 2, 1]]
    faces = np.concatenate((source_faces, target_faces+4))
    first_uv = first[:, :2]*.2+[.1, .1]
    second_uv = first[:, :2]*[parity*.2, .2]+[.7, .6]
    uv = np.concatenate((first_uv, second_uv))
    return BudgetState(state_from_corner_uv(make_mesh(vertices, faces), uv[faces], seam_ids=frozenset())), matrix


def _request(snapshot: Path, matrix: FloatArray | None = None) -> dict[str, object]:
    request: dict[str, object] = {"snapshot": str(snapshot), "first_faces": [0, 1], "second_faces": [2, 3],
        "mode": "similarity", "profile": {"resolution": 128, "gap_pixels": 2, "border_pixels": 2}}
    if matrix is not None:
        request["first_to_second"] = matrix.tolist()
    return request


@pytest.mark.parametrize("parity", [1, -1])
@pytest.mark.parametrize("explicit", [False, True])
def test_rotated_translated_parts_reject_world_mirror_accept_similarity(
    tmp_path: Path, parity: int, explicit: bool,
) -> None:
    initial, matrix = _part(parity)
    source = save_snapshot(initial, tmp_path / "source", {})
    before = fingerprint(initial)
    legacy = check_pair({**_request(source.root), "mode": "auto"}, tmp_path / "legacy", time.monotonic()+30)
    assert legacy["reason"] == "SURFACE_CORRESPONDENCE_REJECTED"
    checked = check_pair(_request(source.root, matrix if explicit else None),
                         tmp_path / "check", time.monotonic()+30)
    assert checked["status"] == "OK", checked
    assert checked["geometry_passed"] is True
    result = apply_reuse({"snapshot": str(source.root), "proposal": checked["proposal"]},
                         tmp_path / "applied", time.monotonic()+30)
    assert result["status"] == "OK"
    shared = load_snapshot(Path(str(result["snapshot"])))
    group = shared.state.groups[0]
    assert group.first_to_second is not None
    assert group.first_orientation*group.second_orientation == parity
    report = geometry_report(shared.state, {"resolution": 128, "gap_pixels": 2, "border_pixels": 2})
    assert report["valid"] and report["active_reuse_groups"] == 1, report
    assert float(str(report["mean_shape_distortion"])) < 1e-12
    assert float(str(report["max_overlap_geometry_error"])) < 1e-12
    assert np.array_equal(shared.state.surface.mesh.vertices, initial.surface.mesh.vertices)
    assert np.array_equal(shared.state.surface.mesh.faces, initial.surface.mesh.faces)
    assert fingerprint(load_snapshot(source.root).state) == before
    with pytest.raises(ValueError, match="SIMILARITY_PBR_UNSUPPORTED"):
        bake_material_tool({"snapshot": str(shared.root)}, tmp_path / "pbr", time.monotonic()+30)
    correspondence = update_uv_correspondences(shared.state)
    assert correspondence.status == "PROPOSED_NOT_VALIDATED", correspondence.diagnostics
    assert correspondence.diagnostics["maximum_position_error_fraction"] == pytest.approx(0, abs=1e-12)
    released = exclusive_faces(shared.state, (0,))
    assert released.groups[0].first_to_second == group.first_to_second
    assert validate_joint_geometry(V2State(shared.state.surface, shared.state.groups), JointProfile())["errors"] == [
        "SIMILARITY_REQUIRES_BUDGET_VALIDATOR"]
    repacked = pack_uv({"snapshot": str(source.root)}, tmp_path / "repacked", time.monotonic()+30)
    with pytest.raises(ValueError, match="STALE_PROPOSAL"):
        apply_reuse({"snapshot": repacked["snapshot"], "proposal": checked["proposal"]},
                    tmp_path / "stale", time.monotonic()+30)


def test_similarity_different_triangulations_and_inverse_member_order(tmp_path: Path) -> None:
    state, matrix = _part(different=True)
    inverse = np.column_stack((matrix[:, :3].T, -matrix[:, :3].T @ matrix[:, 3]))
    source = save_snapshot(state, tmp_path / "source", {})
    request = {**_request(source.root, inverse), "first_faces": [2, 3], "second_faces": [0, 1]}
    checked = check_pair(request, tmp_path / "check", time.monotonic()+30)
    assert checked["status"] == "OK" and checked["geometry_passed"] is True, checked
    assert checked["selected_method"] == "surface_barycentric_transfer"
    proposal = read_object(Path(str(checked["proposal"])))
    shared = load_snapshot(Path(str(proposal["candidate_snapshot"])))
    assert shared.state.groups[0].first_faces == (2, 3)
    assert geometry_report(shared.state, request["profile"])["valid"]  # type: ignore[arg-type]
    assert update_uv_correspondences(shared.state).status == "PROPOSED_NOT_VALIDATED"


@pytest.mark.parametrize("linear", [np.diag([1., 2., 1.]), np.zeros((3, 3)),
                                    np.asarray([[1., .2, 0], [0, 1., 0], [0, 0, 1.]])])
def test_nonuniform_singular_and_sheared_transforms_rejected_without_writes(
    tmp_path: Path, linear: FloatArray,
) -> None:
    state, _ = _part()
    source = save_snapshot(state, tmp_path / "source", {})
    with pytest.raises(ValueError, match="TRANSFORM"):
        check_pair(_request(source.root, np.column_stack((linear, [3., 2., -1.]))),
                   tmp_path / "invalid", time.monotonic()+30)
    assert not (tmp_path / "invalid").exists()
    assert fingerprint(load_snapshot(source.root).state) == fingerprint(state)


def test_similarity_is_not_automatic_overlap_permission(tmp_path: Path) -> None:
    state, matrix = _part()
    source = save_snapshot(state, tmp_path / "source", {})
    checked = check_pair(_request(source.root, matrix), tmp_path / "check", time.monotonic()+30)
    proposal = read_object(Path(str(checked["proposal"])))
    shared = load_snapshot(Path(str(proposal["candidate_snapshot"]))).state
    wrong = matrix.copy()
    wrong[:, 3] += .2
    shared.groups = (replace(shared.groups[0], first_to_second=similarity_transform(wrong)),)
    report = geometry_report(shared, {"resolution": 128, "gap_pixels": 2, "border_pixels": 2})
    assert not report["hard_valid"] and "OVERLAP_CORRESPONDENCE" in str(report["errors"])
    shared.groups = (replace(shared.groups[0], first_to_second=similarity_transform(matrix), second_orientation=-1),)
    assert "REUSE_SIMILARITY_ORIENTATION_MISMATCH" in str(geometry_report(shared)["errors"])
    shared.groups = (replace(shared.groups[0], first_to_second=((1., 0, 0, 3.), (0, 2., 0, 2.), (0, 0, 1., -1.)),
                             second_orientation=1),)
    assert "INVALID_REUSE_SIMILARITY" in str(geometry_report(shared)["errors"])


def test_old_group_json_without_transform_loads_as_legacy(tmp_path: Path) -> None:
    state, matrix = _part()
    source = save_snapshot(state, tmp_path / "source", {})
    checked = check_pair(_request(source.root, matrix), tmp_path / "check", time.monotonic()+30)
    proposal = read_object(Path(str(checked["proposal"])))
    manifest = Path(str(proposal["candidate_snapshot"]))
    record = read_object(manifest)
    groups = record["groups"]
    assert isinstance(groups, list) and isinstance(groups[0], dict)
    groups[0].pop("first_to_second")
    record.pop("state_hash")
    write_json(manifest, {**record, "state_hash": object_hash(record)})
    assert load_snapshot(manifest).state.groups[0].first_to_second is None


def test_uniform_scale_and_inverse_are_exact_and_do_not_mutate_positions() -> None:
    points = np.asarray([[1., 2, 3], [-1., 4, 0]])
    before = points.copy()
    transform = similarity_transform([[0, -2, 0, 3], [2, 0, 0, 4], [0, 0, 2, -5]])
    normal = np.asarray([1., 0, 0])
    mapped = mapped_points(points, normal, 0, transform)
    assert np.allclose(mapped_points(mapped, normal, 0, transform, inverse=True), points)
    assert np.array_equal(points, before)


def test_nonunit_scale_full_cell_limit_is_invariant_to_face_order() -> None:
    first = np.asarray([[0., 0, 0], [1., 0, 0], [1., 1., 0], [0, 1., 0]])
    second = 2*first+[3., 0, .04]
    vertices = np.concatenate((first, second))
    faces = np.asarray([[0, 1, 2], [0, 2, 3], [4, 5, 6], [4, 6, 7]], dtype=np.int64)
    uv = first[:, :2]*.7+.1
    corners = np.concatenate((uv[faces[:2]], uv[faces[2:]-4]))
    transform = similarity_transform([[2., 0, 0, 3.], [0, 2., 0, 0], [0, 0, 2., 0]])
    diagonal = float(np.linalg.norm(np.ptp(vertices, axis=0)))
    reports = []
    for reverse in (False, True):
        order = [2, 3, 0, 1] if reverse else [0, 1, 2, 3]
        surface = state_from_corner_uv(make_mesh(vertices, faces[order]), corners[order], seam_ids=frozenset())
        a, b = ((2, 3), (0, 1)) if reverse else ((0, 1), (2, 3))
        state = BudgetState(surface, (ReuseGroup("scaled", a, b, (1., 0, 0), 0, 1, 1, transform),))
        report = geometry_report(state, {"resolution": 128, "gap_pixels": 0, "border_pixels": 0})
        assert not report["hard_valid"] and "OVERLAP_CORRESPONDENCE" in str(report["errors"])
        assert report["maximum_forward_overlap_error_fraction"] == pytest.approx(.04/diagonal)
        assert report["maximum_inverse_overlap_error_fraction"] == pytest.approx(.02/diagonal)
        assert .02/diagonal < .005 < .04/diagonal
        reports.append(report)
    assert reports[0]["max_overlap_geometry_error"] == pytest.approx(reports[1]["max_overlap_geometry_error"])
