"""Baseline adapters preserve original identities and common ROI starting UV."""

import time

import numpy as np
import pytest

from reseam.budget_baselines import prepare_baseline, solve_roi_baseline
from reseam.joint_regions import RegionPair
from reseam.mesh import make_mesh
from reseam.types import Mesh
from reseam.validation import topology_errors


def panels() -> tuple[Mesh, RegionPair]:
    vertices = np.asarray(
        [
            [-1, 0, 0], [-1, 1, 0], [-1, 1, 1], [-1, 0, 1],
            [1, 0, 0], [1, 1, 0], [1, 1, 1], [1, 0, 1], [1, 0.5, 0.5],
        ],
        dtype=float,
    )
    faces = np.asarray([[0, 2, 1], [0, 3, 2], [4, 5, 8], [5, 6, 8], [6, 7, 8], [7, 4, 8]])
    return make_mesh(vertices, faces), RegionPair((0, 1), (2, 3, 4, 5), (1.0, 0.0, 0.0), 0.0)


def test_xatlas_full_mesh_is_killable_identity_preserving_and_deterministic() -> None:
    mesh, _ = panels()
    first = prepare_baseline(mesh, "independent", 17, time.monotonic() + 20)
    second = prepare_baseline(mesh, "xatlas", 29, time.monotonic() + 20)
    assert first.mesh is mesh
    assert np.array_equal(first.mesh.vertices, mesh.vertices)
    assert np.array_equal(first.mesh.faces, mesh.faces)
    assert np.array_equal(first.uv_faces, second.uv_faces)
    assert np.array_equal(first.uv, second.uv)
    assert not topology_errors(first)
    assert np.isfinite(first.uv).all()
    assert np.all((first.uv >= 0) & (first.uv <= 1))


def test_roi_baselines_use_exact_common_start_and_preserve_geometry() -> None:
    mesh, pair = panels()
    results = [
        solve_roi_baseline(mesh, pair, method, time.monotonic() + 10, iterations=6)
        for method in ("independent", "old_pair", "geometry_only")
    ]
    assert all(np.array_equal(item.initial.uv, results[0].initial.uv) for item in results)
    assert all(np.array_equal(item.initial.uv_faces, results[0].initial.uv_faces) for item in results)
    assert all(item.constraints == results[0].constraints for item in results)
    for item in results:
        assert np.array_equal(item.state.mesh.vertices, mesh.vertices)
        assert np.array_equal(item.state.mesh.faces, mesh.faces[list(item.original_face_ids)])
        assert item.original_face_ids == (0, 1, 2, 3, 4, 5)
        assert item.member_ids.tolist() == [0, 0, 1, 1, 1, 1]
        assert item.metadata["material_status"] == "NOT_EVALUATED"
        assert item.metadata["optcuts_used"] is False
        assert not topology_errors(item.state)
    assert results[0].status == "INITIALIZED"
    assert np.array_equal(results[0].state.uv, results[0].initial.uv)


def test_baseline_rejects_empty_budget_and_unsupported_method() -> None:
    mesh, pair = panels()
    with pytest.raises(TimeoutError, match="BUDGET_EXHAUSTED"):
        prepare_baseline(mesh, "independent", 17, time.monotonic() - 1)
    with pytest.raises(TimeoutError, match="BUDGET_EXHAUSTED"):
        solve_roi_baseline(mesh, pair, "old_pair", time.monotonic() - 1)
    with pytest.raises(ValueError, match="Unsupported"):
        prepare_baseline(mesh, "optcuts", 17, time.monotonic() + 10)
    with pytest.raises(ValueError, match="Unsupported"):
        solve_roi_baseline(mesh, pair, "invented", time.monotonic() + 10)
    with pytest.raises(ValueError, match="Nonnegative integer"):
        prepare_baseline(mesh, "independent", True, time.monotonic() + 10)
    with pytest.raises(ValueError, match="Nonnegative integer"):
        solve_roi_baseline(mesh, pair, "independent", time.monotonic() + 10, iterations=-1)


def test_xatlas_timeout_is_reported_not_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    import subprocess

    def timeout(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired("native_worker", 0.01)

    mesh, _ = panels()
    monkeypatch.setattr("reseam.budget_baselines.subprocess.run", timeout)
    with pytest.raises(TimeoutError, match="XATLAS_INITIALIZATION_TIMEOUT"):
        prepare_baseline(mesh, "independent", 17, time.monotonic() + 10)


@pytest.mark.parametrize("method", ["old_pair", "geometry_only"])
def test_zero_solver_iterations_does_not_mutate_common_start(method: str) -> None:
    mesh, pair = panels()
    result = solve_roi_baseline(mesh, pair, method, time.monotonic() + 10, iterations=0)
    assert result.status == "ITERATION_LIMIT"
    assert result.metadata["iterations"] == 0
    assert np.array_equal(result.state.uv, result.initial.uv)
