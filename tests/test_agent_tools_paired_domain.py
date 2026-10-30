"""Whole paired chart reconstruction uses anchored original corners."""

import time
from pathlib import Path

import numpy as np
import pytest

from reseam.agent_tools.geometry import apply_reuse, check_pair, geometry_report
from reseam.agent_tools.state import Snapshot, load_snapshot, read_object, save_snapshot
from reseam.budget_state import BudgetState, fingerprint
from reseam.mesh import chart_labels, make_mesh, state_from_corner_uv


def _panels(path: Path, *, center_vertex: bool = False, shift: float = 0, fragmented: bool = False) -> Snapshot:
    vertices = np.asarray([[-1., 0, 0], [-1, 1, 0], [-1, 1, 1], [-1, 0, 1],
                           [1., 0, 0], [1, 1, 0], [1, 1, 1], [1, 0, 1]])
    if center_vertex:
        vertices = np.concatenate((vertices, [[1., .5, .5]]))
        target = [[4, 8, 5], [5, 8, 6], [6, 8, 7], [7, 8, 4]]
    else:
        target = [[4, 7, 5], [5, 7, 6]]
    vertices[4:, 1] += shift
    faces = np.asarray([[0, 1, 2], [0, 2, 3], *target], dtype=np.int64)
    mesh = make_mesh(vertices, faces)
    corners = .1 + .25 * vertices[faces, 1:]
    corners[2:, :, 0] += .5
    corners[2:, :, 1] = .45 - corners[2:, :, 1]
    ids = np.arange(len(faces)*3).reshape(-1, 3) if fragmented else faces
    return save_snapshot(BudgetState(state_from_corner_uv(mesh, corners, corner_ids=ids)), path, {})


def _request(snapshot: Snapshot, *, center_vertex: bool = False) -> dict[str, object]:
    return {
        "snapshot": str(snapshot.root), "mode": "paired_domain", "domain": "convex",
        "first_faces": [0, 1], "second_faces": list(range(2, len(snapshot.state.surface.mesh.faces))),
        "boundary_anchors": [
            {"first": [0, 0], "second": [2, 0]},
            {"first": [0, 1], "second": [2, 2]},
            {"first": [0, 2], "second": [3, 2]},
            {"first": [1, 2], "second": [4, 2] if center_vertex else [2, 1]},
        ],
        "profile": {"resolution": 64, "gap_pixels": 2, "border_pixels": 2,
                    "quality_constraints": False, "normal_angle_degrees": 180,
                    "correspondence_fraction": .1},
    }


@pytest.mark.parametrize("center_vertex", [False, True])
@pytest.mark.parametrize("master", ["first", "second"])
def test_paired_domain_rebuilds_fragmented_uv_and_preserves_original_geometry(
    tmp_path: Path, center_vertex: bool, master: str,
) -> None:
    source = _panels(tmp_path / "source", center_vertex=center_vertex, fragmented=True)
    request = {**_request(source, center_vertex=center_vertex), "master_member": master}
    before = fingerprint(source.state)
    result = check_pair(request, tmp_path / "check", time.monotonic()+30)
    assert result["status"] == "OK", read_object(Path(str(result["report"])))
    applied = apply_reuse({"snapshot": str(source.root), "proposal": result["proposal"]},
                          tmp_path / "apply", time.monotonic()+30)
    child = load_snapshot(Path(str(applied["snapshot"])))
    assert len(np.unique(chart_labels(child.state.surface))) == 2
    assert len(child.state.groups) == 1
    assert geometry_report(child.state, request["profile"])["hard_valid"]
    assert np.array_equal(child.state.surface.mesh.vertices, source.state.surface.mesh.vertices)
    assert np.array_equal(child.state.surface.mesh.faces, source.state.surface.mesh.faces)
    assert fingerprint(load_snapshot(source.root).state) == before
    assert all(record["association"] == "final_piecewise_affine_UV_containment"
               for record in child.state.correspondence_records)
    assert child.state.correspondences


def test_new_mode_uses_uv_induced_correspondence_before_nearest_precheck(tmp_path: Path) -> None:
    source = _panels(tmp_path / "source", shift=.05)
    request = _request(source)
    legacy = check_pair({**request, "mode": "approximate", "profile": {"quality_constraints": False}},
                        tmp_path / "legacy", time.monotonic()+30)
    assert legacy["status"] == "REJECTED"
    assert legacy["reason"] == "SURFACE_CORRESPONDENCE_REJECTED"
    result = check_pair(request, tmp_path / "paired", time.monotonic()+30)
    assert result["status"] == "OK", read_object(Path(str(result["report"])))
    proposal = read_object(Path(str(result["proposal"])))
    assert proposal["diagnostics"]["nearest_point_precheck_used"] is False


def test_wrong_boundary_order_has_no_per_face_exception_fallback(tmp_path: Path) -> None:
    source = _panels(tmp_path / "source")
    request = _request(source)
    anchors = request["boundary_anchors"]
    anchors[1]["second"], anchors[2]["second"] = anchors[2]["second"], anchors[1]["second"]
    before = fingerprint(source.state)
    result = check_pair(request, tmp_path / "check", time.monotonic()+30)
    assert result["status"] == "REJECTED"
    assert "PAIRED_ANCHORS_NOT_CYCLICALLY_MONOTONE" in str(read_object(Path(str(result["report"]))))
    assert not (tmp_path / "check" / "checked_snapshot").exists()
    assert fingerprint(load_snapshot(source.root).state) == before


def test_master_uv_and_recorded_convex_fallback(tmp_path: Path) -> None:
    source = _panels(tmp_path / "source")
    request = {**_request(source), "domain": "master_uv"}
    result = check_pair(request, tmp_path / "master", time.monotonic()+30)
    assert result["status"] == "OK", read_object(Path(str(result["report"])))
    assert result["selected_method"] == "paired_domain_master_uv"
    # A genuine old UV split prevents using one continuous master chart.
    modified = source.state.copy()
    corners = modified.surface.uv[modified.surface.uv_faces].copy()
    corners[1] += .01
    modified.surface = state_from_corner_uv(modified.surface.mesh, corners,
        corner_ids=np.arange(corners.size//2).reshape(-1, 3))
    fragmented = save_snapshot(modified, tmp_path / "fragmented", {})
    request["snapshot"] = str(fragmented.root)
    result = check_pair(request, tmp_path / "fallback", time.monotonic()+30)
    assert result["status"] == "OK", read_object(Path(str(result["report"])))
    assert result["selected_method"] == "paired_domain_convex"
    proposal = read_object(Path(str(result["proposal"])))
    assert proposal["diagnostics"]["attempts"][-1]["fallback_used"] is True
    assert "MASTER_UV_HAS_OLD_INTERNAL_SEAMS" in str(proposal["diagnostics"]["attempts"][0])


def test_protection_timeout_and_exceptions_leave_parent_unchanged(tmp_path: Path) -> None:
    source = _panels(tmp_path / "source")
    before = fingerprint(source.state)
    for extra, reason in (({"protected_faces": [0]}, "PROTECTED_FACES"),
                          ({"exclusive_faces": [0]}, "WHOLE_REGIONS")):
        with pytest.raises(ValueError, match=reason):
            check_pair({**_request(source), **extra}, tmp_path / "reject", time.monotonic()+30)
    with pytest.raises(TimeoutError):
        check_pair(_request(source), tmp_path / "timeout", time.monotonic()-1)
    assert fingerprint(load_snapshot(source.root).state) == before
    assert not (tmp_path / "reject" / "checked_snapshot").exists()
    assert not (tmp_path / "timeout").exists()
