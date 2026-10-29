"""Semantic labels cannot silently change mesh, UV, or original identity."""

import time
from pathlib import Path

import numpy as np
import pytest

from reseam.agent_tools.regions import define_region
from reseam.agent_tools.state import load_snapshot, save_snapshot
from reseam.budget_state import BudgetState
from reseam.mesh import make_mesh, state_from_corner_uv


def _snapshot(path: Path) -> Path:
    mesh = make_mesh(np.array([[0., 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0]]),
                     np.array([[0, 1, 2], [0, 2, 3]]))
    corners = np.array([[[.1, .1], [.9, .1], [.9, .9]], [[.1, .1], [.9, .9], [.1, .9]]])
    return save_snapshot(BudgetState(state_from_corner_uv(mesh, corners,
                         corner_ids=np.arange(6).reshape(2, 3))), path, {}).root


def test_region_can_cross_uv_seam_without_modifying_arrays(tmp_path: Path) -> None:
    parent = _snapshot(tmp_path / "parent")
    output = tmp_path / "region"
    output.mkdir()
    before = load_snapshot(parent)
    result = define_region({"snapshot": str(parent), "name": "wing", "chart_ids": [0, 1],
                            "protected_faces": [1], "rationale": "continuous membrane",
                            "evidence": "front view"}, output, time.monotonic() + 10)
    child = load_snapshot(Path(str(result["snapshot"])))
    assert result["face_count"] == 2
    assert result["protected_face_count"] == 1
    assert child.state_hash != before.state_hash
    assert np.array_equal(child.state.surface.mesh.faces, before.state.surface.mesh.faces)
    assert np.array_equal(child.state.surface.uv_faces, before.state.surface.uv_faces)
    assert np.array_equal(child.state.surface.uv, before.state.surface.uv)
    assert load_snapshot(parent).state_hash == before.state_hash


@pytest.mark.parametrize("fields", [{"faces": [0, 0]}, {"faces": [2]}, {"faces": [True]},
                                    {"faces": [0], "protected_faces": [1]},
                                    {"faces": [0], "chart_ids": [0]}])
def test_invalid_region_never_writes_child(tmp_path: Path, fields: dict[str, object]) -> None:
    parent = _snapshot(tmp_path / "parent")
    output = tmp_path / "invalid"
    output.mkdir()
    with pytest.raises(ValueError):
        define_region({"snapshot": str(parent), "name": "invalid", **fields}, output, time.monotonic() + 5)
    assert not (output / "snapshot").exists()


def test_stale_region_request_is_rejected(tmp_path: Path) -> None:
    parent = _snapshot(tmp_path / "parent")
    with pytest.raises(ValueError, match="STALE"):
        define_region({"snapshot": str(parent), "state_hash": "old", "name": "wing", "faces": [0]},
                      tmp_path / "invalid", time.monotonic() + 5)
