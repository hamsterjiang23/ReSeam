"""Authored conflict preservation must not hide newly introduced UV damage."""

import time
from pathlib import Path

import numpy as np
import pytest

from reseam.agent_tools.color_reuse import _geometry_profile
from reseam.agent_tools.geometry import geometry_report, inspect_uv, pack_uv
from reseam.agent_tools.state import Snapshot, read_object, save_snapshot
from reseam.budget_state import BudgetState
from reseam.mesh import chart_labels, make_mesh, state_from_corner_uv, write_obj


def source(tmp_path: Path) -> Snapshot:
    vertices = np.array([[0., 0, 0], [1., 0, 0], [0., 1, 0],
                         [2., 0, 0], [3., 0, 0], [2., 1, 0],
                         [4., 0, 0], [5., 0, 0], [4., 1, 0]])
    faces = np.arange(9, dtype=np.int64).reshape(3, 3)
    triangle = np.array([[.1, .1], [.3, .1], [.1, .3]])
    corners = np.array([triangle, triangle + [.05, .05], triangle + [.5, .5]])
    surface = state_from_corner_uv(make_mesh(vertices, faces), corners,
                                   corner_ids=np.arange(9, dtype=np.int64).reshape(3, 3))
    return save_snapshot(BudgetState(surface), tmp_path / "source", {})


def policy(snapshot: Snapshot) -> dict[str, object]:
    return {"resolution": 64, "gap_pixels": 0, "border_pixels": 0, "quality_constraints": False,
            "preserve_source_uv": {"snapshot": str(snapshot.root), "state_hash": snapshot.state_hash}}


def test_existing_conflict_is_preserved_and_not_counted_as_reuse(tmp_path: Path) -> None:
    snapshot = source(tmp_path)
    settings = policy(snapshot)
    state = snapshot.state.copy()
    state.surface.uv = .8 * state.surface.uv + .05
    child = save_snapshot(state, tmp_path / "candidate", {})
    assert geometry_report(state, {"quality_constraints": False})["hard_valid"] is False
    report = geometry_report(state, settings)
    assert report["hard_valid"] is True
    assert report["strict_hard_valid"] is False
    assert len(report["preserved_source_conflicts"]) == 1
    output = tmp_path / "inspect"
    output.mkdir()
    result = inspect_uv({"snapshot": str(child.root), "profile": settings}, output, time.monotonic() + 20)
    metrics = read_object(Path(str(result["metrics_report"])))
    assert metrics["actual_independent_tiles"] == 3
    assert metrics["authorized_positive_overlap_cells"] == 0
    assert metrics["strict_hard_geometry_passed"] is False


def test_changed_original_pair_is_rejected(tmp_path: Path) -> None:
    snapshot = source(tmp_path)
    state = snapshot.state.copy()
    state.surface.uv[3] += [.02, .01]
    report = geometry_report(state, policy(snapshot))
    assert report["hard_valid"] is False
    assert report["preserved_source_conflicts"] == []


def test_new_conflict_pair_is_rejected(tmp_path: Path) -> None:
    snapshot = source(tmp_path)
    state = snapshot.state.copy()
    state.surface.uv[6:] -= [.5, .5]
    report = geometry_report(state, policy(snapshot))
    assert report["hard_valid"] is False
    assert any("0:2" in error for error in report["hard_errors"])


def test_wrong_source_identity_is_rejected(tmp_path: Path) -> None:
    snapshot = source(tmp_path)
    settings = policy(snapshot)
    settings["preserve_source_uv"] = {"snapshot": str(snapshot.root), "state_hash": "wrong"}
    with pytest.raises(ValueError, match="SOURCE_UV_STATE_HASH_MISMATCH"):
        geometry_report(snapshot.state, settings)
    state = snapshot.state.copy()
    vertices = state.surface.mesh.vertices.copy()
    vertices[0, 0] += .1
    state.surface.mesh = make_mesh(vertices, state.surface.mesh.faces)
    with pytest.raises(ValueError, match="SOURCE_UV_GEOMETRY_MISMATCH"):
        geometry_report(state, policy(snapshot))


def test_pack_never_splits_faces_and_profile_is_bound_to_color_target(tmp_path: Path) -> None:
    snapshot = source(tmp_path)
    settings = policy(snapshot)
    result = pack_uv({"snapshot": str(snapshot.root), "profile": {**settings, "gap_pixels": 2}},
                     tmp_path / "pack", time.monotonic() + 20)
    assert result["status"] == "OK", result
    assert len(np.unique(chart_labels(snapshot.state.surface))) == 3
    bound = _geometry_profile({"geometry_profile": settings})
    assert _geometry_profile({}, bound)["preserve_source_uv"] == settings["preserve_source_uv"]
    changed = {**settings, "preserve_source_uv": {"snapshot": str(snapshot.root), "state_hash": "other"}}
    with pytest.raises(ValueError, match="BASECOLOR_GEOMETRY_PROFILE_MISMATCH"):
        _geometry_profile({"geometry_profile": changed}, bound)


def test_geometry_initialization_discards_invalid_uv_without_cutting_asset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from reseam.agent_tools import reparameterize as module
    snapshot = source(tmp_path)
    binary = tmp_path / "native-binary"
    binary.write_bytes(b"mock backend")

    def runtime(root: Path) -> dict[str, str]:
        return {"optcuts_binary": str(binary)}

    def command(*args: object, **kwargs: object) -> list[str]:
        return []

    def backend(command: object, folder: Path, seconds: float) -> None:
        text = (folder / "input.obj").read_text(encoding="utf-8")
        assert "vt " not in text
        rows = text.splitlines()
        assert all("/" not in row for row in rows if row.startswith("f "))
        vertices = np.array([[float(x) for x in row.split()[1:]] for row in rows if row.startswith("v ")])
        faces = np.array([[int(x) - 1 for x in row.split()[1:]] for row in rows if row.startswith("f ")])
        mesh = make_mesh(vertices, faces)
        surface = state_from_corner_uv(mesh, .1 + .2 * vertices[faces, :2], corner_ids=faces)
        write_obj(folder / "finalResult_mesh.obj", surface)

    monkeypatch.setattr(module, "read_runtime", runtime)
    monkeypatch.setattr(module, "wsl_command", command)
    monkeypatch.setattr(module, "run_process", backend)
    result = module.reparameterize({"snapshot": str(snapshot.root), "profile": {**policy(snapshot), "gap_pixels": 2},
        "backend": "optcuts", "initial_uv": "geometry", "faces": [0]},
        tmp_path / "result", time.monotonic() + 20)
    assert result["status"] == "OK"
    assert len(snapshot.state.surface.mesh.faces) == 3
