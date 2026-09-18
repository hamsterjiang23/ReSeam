import json
from pathlib import Path

import numpy as np
import pytest

from reseam.constraints import load_constraints, mesh_identity
from reseam.initialization import initialize
from reseam.mesh import chart_labels, corner_map, make_mesh, state_from_corner_uv
from reseam.runtime import read_runtime
from reseam.solver import solve
from reseam.types import Mesh, MeshState
from reseam.validation import distortion, validate


def square() -> Mesh:
    return make_mesh(
        np.array([[0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0]], dtype=float),
        np.array([[0, 1, 2], [0, 2, 3]], dtype=np.int64),
    )


def flat_state(mesh: Mesh) -> MeshState:
    return state_from_corner_uv(mesh, mesh.vertices[mesh.faces, :2] / mesh.scale, corner_ids=mesh.faces)


def test_isometry_and_scale_invariance() -> None:
    mesh = square()
    assert distortion(flat_state(mesh))["sd_mean"] == pytest.approx(0)
    scaled = make_mesh(mesh.vertices * 27, mesh.faces)
    assert distortion(flat_state(scaled))["sd_mean"] == pytest.approx(0)
    assert validate(flat_state(mesh))["valid"]


def test_cut_join_corner_identity() -> None:
    mesh = square()
    edge = mesh.edges.index((0, 2))
    assert len(np.unique(corner_map(mesh, frozenset()))) == 4
    assert len(np.unique(corner_map(mesh, frozenset({edge})))) == 6
    split = initialize(mesh, frozenset({edge}))
    assert len(set(chart_labels(split))) == 2
    assert validate(split, tau=None)["valid"]
    assert np.array_equal(corner_map(mesh, frozenset()), flat_state(mesh).uv_faces)


def test_flip_and_overlap_rejected() -> None:
    state = flat_state(square())
    state.uv[3] = [1.0, 0.0]
    assert not validate(state)["valid"]


def test_unconfirmed_hard_constraints_are_soft(tmp_path: Path) -> None:
    mesh = square()
    edge = mesh.edges.index((0, 2))
    path = tmp_path / "constraints.json"
    path.write_text(json.dumps({"must_cut": [edge], "confirmed": False}))
    result = load_constraints(path, mesh)
    assert not result.must_cut
    assert result.weights[edge] == 0.5
    path.write_text(
        json.dumps(
            {
                "must_cut": [edge],
                "confirmed": True,
                "confirmed_by": "test_artist",
                "mesh_identity": mesh_identity(mesh),
            }
        )
    )
    assert load_constraints(path, mesh).must_cut == frozenset({edge})


def test_conflicting_constraints_fail(tmp_path: Path) -> None:
    mesh = square()
    edge = mesh.edges.index((0, 2))
    path = tmp_path / "constraints.json"
    path.write_text(json.dumps({"must_cut": [edge], "must_join": [edge]}))
    with pytest.raises(ValueError, match="Conflicting"):
        load_constraints(path, mesh)


def test_native_frozen_vertices_and_energy(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    if not (root / "configs/runtime.local.json").exists():
        pytest.skip("Explicit native runtime not configured")
    mesh = make_mesh(
        np.array([[0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0], [0.5, 0.5, 0]]),
        np.array([[0, 1, 4], [1, 2, 4], [2, 3, 4], [3, 0, 4]], dtype=np.int64),
    )
    state = flat_state(mesh)
    center = int(state.uv_faces[0, 2])
    state.uv[center] = [0.63, 0.42]
    before = state.uv.copy()
    # Windows temp folders remain visible to WSL through /mnt/c.
    result = solve(state, frozenset({center}), read_runtime(root), tmp_path / "native", 20)
    assert result.state is not None, result
    fixed = sorted(set(range(len(state.uv))) - {center})
    np.testing.assert_array_equal(result.state.uv[fixed], before[fixed])
    np.testing.assert_array_equal(result.state.uv_faces, state.uv_faces)
    assert distortion(result.state)["sd_mean"] < distortion(state)["sd_mean"]
    assert result.metadata["energy_native"] == pytest.approx(4 * (distortion(result.state)["sd_mean"] + 1))
