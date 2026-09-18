import json
from pathlib import Path

import numpy as np
import pytest

from reseam.candidates import regions, seam_chains
from reseam.constraints import Constraints
from reseam.initialization import initialize
from reseam.mesh import chart_labels, import_uv_obj, make_mesh, state_from_corner_uv, write_obj
from reseam.pipeline import run_unwrap
from reseam.search import accept
from reseam.types import MeshState
from reseam.validation import intersection_errors, topology_errors, validate


def grid(size: int = 4) -> MeshState:
    vertices = np.array([[x, y, 0] for y in range(size) for x in range(size)], dtype=float)
    faces = []
    for y in range(size - 1):
        for x in range(size - 1):
            a = y * size + x
            faces.extend([[a, a + 1, a + size + 1], [a, a + size + 1, a + size]])
    mesh = make_mesh(vertices, np.array(faces, dtype=np.int64))
    return state_from_corner_uv(mesh, mesh.vertices[mesh.faces, :2] / mesh.scale, corner_ids=mesh.faces)


def test_coincident_uv_copies_remain_separate(tmp_path: Path) -> None:
    state = grid(2)
    state = state_from_corner_uv(state.mesh, state.uv[state.uv_faces], corner_ids=np.arange(6).reshape(2, 3))
    path = tmp_path / "separate.obj"
    write_obj(path, state)
    restored = import_uv_obj(state.mesh, path)
    assert restored.seams == state.seams
    assert len(np.unique(chart_labels(restored))) == 2


def test_nonrealizable_isolated_cut_rejected() -> None:
    state = grid()
    state.seams = frozenset({state.mesh.edges.index((5, 10))})
    assert any("UNREALIZED_SEAM" in error for error in topology_errors(state))


def test_nonseparating_slit_exposes_single_chart_region() -> None:
    state = grid()
    mesh = state.mesh
    slit = frozenset({mesh.edges.index((1, 5)), mesh.edges.index((5, 9))})
    state = initialize(mesh, slit)
    assert len(np.unique(chart_labels(state))) == 1
    assert validate(state, None)["valid"]
    assert regions(state, 17)


def test_whole_seam_chain() -> None:
    state = grid()
    chain = {state.mesh.edges.index(edge) for edge in ((1, 5), (5, 9), (9, 13))}
    assert seam_chains(state, chain) == [chain]


def test_positive_triangles_can_self_overlap() -> None:
    angles = np.arange(5) * 2 * np.pi / 5
    vertices = np.vstack((np.column_stack((np.cos(angles), np.sin(angles), np.zeros(5))), [0, 0, 0]))
    faces = np.array([[i, (i + 1) % 5, 5] for i in range(5)], dtype=np.int64)
    mesh = make_mesh(vertices, faces)
    uv = np.vstack((np.column_stack((np.cos(angles * 2), np.sin(angles * 2))), [0, 0]))
    state = state_from_corner_uv(mesh, uv[faces], corner_ids=faces)
    assert intersection_errors(state)
    assert not validate(state, None)["valid"]


def test_failed_candidate_preserves_entire_state() -> None:
    initial = grid(2)
    before = initial.copy()
    invalid = initial.copy()
    invalid.uv[:] = 0
    result, reason = accept(initial, invalid, 0, Constraints(), 0.025, 0)
    assert reason == "QUALITY_INVALID"
    assert result is initial
    np.testing.assert_array_equal(initial.uv, before.uv)
    np.testing.assert_array_equal(initial.uv_faces, before.uv_faces)
    assert initial.seams == before.seams and initial.version == before.version


def test_stale_candidate_rejected() -> None:
    state = grid(2)
    result, reason = accept(state, state.copy(), -1, Constraints(), 0.025, 0)
    assert result is state and reason == "STALE_STATE"


def test_existing_output_rejected(tmp_path: Path) -> None:
    with pytest.raises(FileExistsError):
        run_unwrap(tmp_path, "energy_blade", "optcuts", tmp_path)


def test_checkpoint_recovery_after_numerical_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from reseam import solver

    state = grid(2)

    def fake_process(argv: list[str], folder: Path, timeout: float) -> float:
        good = {"uv": state.uv.tolist(), "topology_updates": 0, "iterations": 1, "energy_native": 4}
        bad = {
            "uv": np.zeros_like(state.uv).tolist(),
            "topology_updates": 0,
            "iterations": 2,
            "energy_native": 3,
        }
        (folder / "result.json").write_text(json.dumps(bad))
        (folder / "result.json.checkpoint_1.json").write_text(json.dumps(good))
        (folder / "result.json.checkpoint_2.json").write_text(json.dumps(bad))
        raise RuntimeError("simulated late factorization failure")

    monkeypatch.setattr(solver, "run_process", fake_process)
    result = solver.solve(
        state, frozenset(range(len(state.uv))), {"continuous_binary": "unused"}, tmp_path / "solver", 10
    )
    assert result.state is not None
    assert result.status == "NUMERICAL_FAILURE_WITH_CHECKPOINT"
    assert result.iterations == 1
    np.testing.assert_array_equal(result.state.uv, state.uv)


def test_annotation_cannot_target_other_mesh(tmp_path: Path) -> None:
    from reseam.constraints import load_constraints, suggestions

    first = grid(2)
    path = tmp_path / "annotation.json"
    data = suggestions(first.mesh)
    data.update(confirmed=True, confirmed_by="unit_test")
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="different mesh"):
        load_constraints(path, grid(3).mesh)


def test_invalid_hard_constraint_refuses_commit() -> None:
    state = grid(2)
    edge = state.mesh.edges.index((0, 3))
    result, reason = accept(
        state, state.copy(), state.version, Constraints(must_cut=frozenset({edge})), 0.025, 0
    )
    assert result is state and reason == "HARD_CONSTRAINT_VIOLATION"


def test_hole_bridge_builds_real_disk_cut() -> None:
    from reseam.candidates import bridge_holes

    angles = np.arange(8) * 2 * np.pi / 8
    ring = np.column_stack((np.cos(angles), np.sin(angles), np.zeros(8)))
    vertices = np.vstack((ring * 2, ring))
    faces = np.array(
        [
            [a, b, c]
            for i in range(8)
            for a, b, c in ((i, (i + 1) % 8, 8 + (i + 1) % 8), (i, 8 + (i + 1) % 8, 8 + i))
        ],
        dtype=np.int64,
    )
    mesh = make_mesh(vertices, faces)
    state = state_from_corner_uv(mesh, vertices[faces, :2] / mesh.scale, corner_ids=faces)
    assert validate(state, None)["valid"]
    seams = bridge_holes(state, frozenset(), tuple(range(len(faces))), Constraints())
    assert len(seams) >= 1
    cut = initialize(mesh, seams)
    assert validate(cut, None)["valid"]
    assert len(np.unique(chart_labels(cut))) == 1
