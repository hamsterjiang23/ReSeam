from pathlib import Path

import numpy as np
import pytest

from reseam.joint_benchmark import verify_frozen
from reseam.joint_state import accept_joint, load_joint, save_joint
from reseam.joint_types import JointProfile, V2State
from reseam.mesh import make_mesh, sha256
from reseam.types import MeshState


def test_frozen_checks_assets_and_backend_identity(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    asset = tmp_path / "assets/v1/mesh.obj"
    asset.parent.mkdir(parents=True)
    asset.write_text("mesh")
    backend = tmp_path / "backend.exe"
    backend.write_bytes(b"backend")
    monkeypatch.setattr("reseam.joint_benchmark.read_runtime", lambda _: {"blender": str(backend)})
    info = {
        "source": {},
        "assets": {"mesh.obj": sha256(asset)},
        "backend_paths": {"blender": str(backend.resolve())},
        "backend_sha256": {"blender": sha256(backend)},
    }
    verify_frozen(tmp_path, info)
    backend.write_bytes(b"modified")
    with pytest.raises(ValueError, match="BACKEND_CHANGED"):
        verify_frozen(tmp_path, info)
    asset.write_text("modified")
    with pytest.raises(ValueError, match="ASSET_CHANGED"):
        verify_frozen(tmp_path, info)


def test_atomic_rejection_and_serialization(tmp_path: Path) -> None:
    vertices = np.asarray([[0, 0, 0], [1, 0, 0], [0, 1, 0]], dtype=float)
    faces = np.asarray([[0, 1, 2]], dtype=np.int64)
    state = V2State(
        MeshState(make_mesh(vertices, faces), frozenset(), vertices[:, :2] * 0.2 + 0.1, faces), version=2
    )
    before = state.surface.uv.copy()
    proposed = state.copy()
    proposed.surface.uv[2] = proposed.surface.uv[0]
    returned, decision = accept_joint(state.surface, state, proposed, 2, JointProfile())
    assert returned is state and decision["status"] == "QUALITY_REJECTED"
    assert state.version == 2 and np.array_equal(state.surface.uv, before)
    returned, decision = accept_joint(state.surface, state, state.copy(), 1, JointProfile())
    assert returned is state and decision["status"] == "STALE_STATE"
    save_joint(tmp_path / "state.json", state)
    restored = load_joint(tmp_path / "state.json")
    assert restored.version == 2 and np.array_equal(restored.surface.uv, before)
