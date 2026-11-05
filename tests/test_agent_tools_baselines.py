"""Baseline imports must preserve face/corner identity and never mutate the parent."""

import time
from pathlib import Path

import numpy as np
import pytest

from reseam.agent_tools.registry import describe
from reseam.agent_tools.state import load_snapshot, save_snapshot
from reseam.budget_state import BudgetState
from reseam.mesh import make_mesh, state_from_corner_uv


def parent_snapshot(tmp_path: Path) -> Path:
    mesh = make_mesh(np.array([[0., 0., 0.], [1., 0., 0.], [1., 1., 0.], [0., 1., 0.]]),
                     np.array([[0, 1, 2], [0, 2, 3]]))
    state = state_from_corner_uv(mesh, mesh.vertices[mesh.faces, :2], corner_ids=mesh.faces)
    return save_snapshot(BudgetState(state), tmp_path / "parent", {}).root / "snapshot.json"


def test_xatlas_baseline_preserves_parent(tmp_path: Path) -> None:
    from reseam.agent_tools.baselines import run_baseline

    path = parent_snapshot(tmp_path)
    before = path.read_bytes()
    out = tmp_path / "child"
    out.mkdir()
    result = run_baseline({"snapshot": str(path), "method": "xatlas"}, out, time.monotonic() + 20)
    child = load_snapshot(Path(str(result["snapshot"])))
    parent = load_snapshot(path)
    assert path.read_bytes() == before
    np.testing.assert_array_equal(child.state.surface.mesh.vertices, parent.state.surface.mesh.vertices)
    np.testing.assert_array_equal(child.state.surface.mesh.faces, parent.state.surface.mesh.faces)
    assert child.state.groups == ()
    assert "vt " not in (out / "native/input.obj").read_text()
    assert result["quality_accepted"] is None
    assert result["geometry_unchanged"] is True


def test_unknown_baseline_does_not_create_child(tmp_path: Path) -> None:
    from reseam.agent_tools.baselines import run_baseline

    path = parent_snapshot(tmp_path)
    out = tmp_path / "child"
    out.mkdir()
    with pytest.raises(ValueError, match="UNKNOWN_PAPER_BASELINE"):
        run_baseline({"snapshot": str(path), "method": "invented"}, out, time.monotonic() + 20)
    assert not (out / "snapshot").exists()
    assert "flatten_anything" in str(describe("uv.baseline"))
