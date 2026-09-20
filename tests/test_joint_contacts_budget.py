import json
from pathlib import Path

import numpy as np
import pytest

from reseam.joint_budget import JointBudget
from reseam.joint_contacts import sheet_intersections
from reseam.mesh import make_mesh
from reseam.types import MeshState


def test_clipped_fan_contact_requires_original_identity_and_provenance() -> None:
    vertices = np.asarray([[0, 0, 0], [1, 0, 0], [0, 1, 0], [-1, 0, 0], [0, -1, 0], [0, 0, 0]], dtype=float)
    faces = np.asarray([[0, 1, 2], [0, 3, 4]], dtype=np.int64)
    uv = vertices[faces.reshape(-1), :2].copy()
    state = MeshState(make_mesh(vertices, faces), frozenset(), uv, np.arange(6).reshape(2, 3))
    classes = (0, 1, 2, 0, 3, 4)
    assert sheet_intersections(state, np.zeros(2, dtype=np.int64))
    assert not sheet_intersections(state, np.zeros(2, dtype=np.int64), classes)
    separate = faces.copy()
    separate[1, 0] = 5
    state.mesh = make_mesh(vertices, separate)
    assert sheet_intersections(state, np.zeros(2, dtype=np.int64), classes)
    state.mesh = make_mesh(vertices, faces)
    state.uv[4:] = [[0.8, 0], [0, 0.8]]
    assert sheet_intersections(state, np.zeros(2, dtype=np.int64), classes)


def test_budget_reservation_survives_interruption_and_writers_are_exclusive(tmp_path: Path) -> None:
    path = tmp_path / "budget.json"
    ledger = JointBudget(path)
    data = json.loads(path.read_text())
    data["runs"] = [{"label": "lost", "stage": "smoke", "status": "RESERVED", "charged_seconds": 3599}]
    path.write_text(json.dumps(data))
    with pytest.raises(TimeoutError), ledger.run("formal", "smoke", 100, require_full=True):
        pass
    assert len(json.loads(path.read_text())["runs"]) == 1
    with ledger.run("new", "smoke", 100):
        reserved = json.loads(path.read_text())
        assert reserved["runs"][-1]["reserved_seconds"] == 1
        with pytest.raises(OSError), JointBudget(path).run("concurrent", "smoke", 1):
            pass
    done = json.loads(path.read_text())
    assert done["runs"][0]["charged_seconds"] == 3599
    assert done["runs"][-1]["status"] == "COMPLETE"
    data["account"] = "v1"
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="account"):
        JointBudget(path)
