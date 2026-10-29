"""Independent geometric solver contracts, not real-model or texture-quality acceptance."""

import math

import numpy as np
import pytest

from reseam.budget_pair import BudgetPairProblem, first_positive_determinant_root, solve_budget_pair
from reseam.joint_types import Correspondence
from reseam.mesh import make_mesh
from reseam.types import FloatArray, MeshState


def panels() -> tuple[MeshState, tuple[Correspondence, ...]]:
    vertices = np.asarray(
        [
            [-1, 0, 0],
            [-1, 1, 0],
            [-1, 1, 1],
            [-1, 0, 1],
            [1, 0, 0],
            [1, 1, 0],
            [1, 1, 1],
            [1, 0, 1],
            [1, 0.5, 0.5],
        ],
        dtype=float,
    )
    faces = np.asarray([[0, 2, 1], [0, 3, 2], [4, 5, 8], [5, 6, 8], [6, 7, 8], [7, 4, 8]], dtype=np.int64)
    state = MeshState(make_mesh(vertices, faces), frozenset(), vertices[:, 1:].copy(), faces.copy())
    correspondences: list[Correspondence] = []
    samples = np.asarray([[1, 0, 0], [0, 1, 0], [0, 0, 1], [1 / 3, 1 / 3, 1 / 3]], dtype=float)
    for source, face in enumerate(faces):
        targets = range(2, 6) if source < 2 else range(2)
        for sample in samples:
            point = sample @ state.uv[face]
            for target in targets:
                tri = state.uv[faces[target]]
                local = np.linalg.solve(np.column_stack((tri[1] - tri[0], tri[2] - tri[0])), point - tri[0])
                bary = (float(1 - local.sum()), float(local[0]), float(local[1]))
                if min(bary) >= -1e-12:
                    correspondences.append(
                        Correspondence(
                            source,
                            (float(sample[0]), float(sample[1]), float(sample[2])),
                            target,
                            bary,
                            float(state.mesh.areas[source]) / 4,
                        )
                    )
                    break
            else:
                raise AssertionError("Fixture point omitted by opposite triangulation")
    return state, tuple(correspondences)


def problem(state: MeshState, correspondence: tuple[Correspondence, ...]) -> BudgetPairProblem:
    return BudgetPairProblem(state, np.asarray([-1, -1, 1, 1, 1, 1], dtype=np.int64), correspondence)


def test_shape_and_coupling_gradient_with_mirrored_different_triangulation() -> None:
    state, correspondence = panels()
    state.uv[8] += [0.04, -0.025]
    state.uv[4:] += [0.1, -0.02]
    item = problem(state, correspondence)
    _, analytic = item.energy_gradient(state.uv)
    numeric = np.zeros_like(analytic)
    for index in np.ndindex(state.uv.shape):
        plus, minus = state.uv.copy(), state.uv.copy()
        plus[index] += 1e-6
        minus[index] -= 1e-6
        numeric[index] = (item.energy_gradient(plus)[0] - item.energy_gradient(minus)[0]) / 2e-6
    assert np.allclose(analytic, numeric, rtol=2e-5, atol=2e-7)
    # Shape component is scale invariant, coupling is normalized by a fixed common gauge length.
    assert np.isfinite(analytic).all()


def test_solver_reduces_residual_and_preserves_common_gauge_and_inputs() -> None:
    state, correspondence = panels()
    state.uv[4:] += [0.15, 0.05]
    original = state.uv.copy()
    item = problem(state, correspondence)
    before = item.coupling_rms(state.uv)
    result = solve_budget_pair(item, seconds=5, iterations=150)
    assert result.geometry_valid
    assert result.coupling_rms < before * 0.15
    assert result.material_status == "NOT_EVALUATED"
    assert not item.geometry_errors(result.state.uv)
    a, b = item.anchors
    assert np.allclose(result.state.uv[[a, b]], original[[a, b]], atol=1e-12)
    assert np.array_equal(state.uv, original)
    assert np.array_equal(result.state.mesh.vertices, state.mesh.vertices)
    assert np.array_equal(result.state.mesh.faces, state.mesh.faces)
    assert np.array_equal(result.state.uv_faces, state.uv_faces)
    assert result.history


@pytest.mark.parametrize("seconds,iterations", [(0.0, 100), (1.0, 0)])
def test_empty_budget_returns_exact_initial_state(seconds: float, iterations: int) -> None:
    state, correspondence = panels()
    item = problem(state, correspondence)
    result = solve_budget_pair(item, seconds=seconds, iterations=iterations)
    assert np.array_equal(result.state.uv, state.uv)
    assert result.geometry_valid
    assert result.status == ("TIMEOUT_WITH_BEST" if seconds == 0 else "ITERATION_LIMIT")


def test_determinant_path_finds_first_root_even_when_endpoint_positive() -> None:
    initial = np.diag([1.0, 2.0])
    direction = np.diag([-2.0, -1.0])
    root = first_positive_determinant_root(initial, direction)
    assert root == pytest.approx(0.5)
    assert np.linalg.det(initial + 3 * direction) > 0
    assert np.linalg.det(initial + 0.51 * direction) < 0
    assert first_positive_determinant_root(np.eye(2), -np.eye(2)) == pytest.approx(1.0)
    assert first_positive_determinant_root(np.eye(2), np.eye(2)) == math.inf
    assert first_positive_determinant_root(np.eye(2), np.diag([-2.0, 0.0])) == pytest.approx(0.5)


def test_member_identity_is_not_orientation_sign() -> None:
    state, correspondence = panels()
    state.uv[:4, 0] = 1 - state.uv[:4, 0]
    item = BudgetPairProblem(state, np.ones(6, dtype=np.int64), correspondence)
    assert len(np.unique(item.members)) == 2
    assert not item.geometry_errors(state.uv)
    with pytest.raises(ValueError, match="Initial member self-intersects"):
        BudgetPairProblem(
            state, np.ones(6, dtype=np.int64), correspondence, member_ids=np.zeros(6, dtype=np.int64)
        )


def test_incomplete_or_rank_deficient_evidence_is_rejected() -> None:
    state, correspondence = panels()
    with pytest.raises(ValueError, match="CORRESPONDENCE_INCOMPLETE"):
        problem(state, correspondence[:1])
    center_only = tuple(
        Correspondence(c.source_face, (1.0, 0, 0), c.target_face, c.target_barycentric, c.weight)
        for c in correspondence
    )
    # Every source face is present, but all first-member source corners coincide at vertex 0.
    with pytest.raises(ValueError, match="CORRESPONDENCE_RANK_DEFICIENT"):
        problem(state, center_only)


def test_invalid_geometry_and_coefficient_are_rejected() -> None:
    state, correspondence = panels()
    folded = state.copy()
    folded.uv[8] = [2, 2]
    with pytest.raises(ValueError, match="Flipped or degenerate"):
        problem(folded, correspondence)
    with pytest.raises(ValueError, match="Positive finite objective"):
        BudgetPairProblem(state, np.asarray([-1, -1, 1, 1, 1, 1]), correspondence, coupling_weight=-1)
    with pytest.raises(ValueError, match="Anisotropy"):
        BudgetPairProblem(state, np.asarray([-1, -1, 1, 1, 1, 1]), correspondence, maximum_anisotropy=0.5)


def test_trial_validated_after_deadline_is_not_archived(monkeypatch: pytest.MonkeyPatch) -> None:
    state, correspondence = panels()
    state.uv[4:] += [0.15, 0.05]
    item = problem(state, correspondence)
    clock = [0.0]
    checked = [False]

    def delayed_validation(points: FloatArray) -> list[str]:
        if not np.array_equal(points, item.initial.uv) and not checked[0]:
            clock[0] = 2.0
            checked[0] = True
        return []

    monkeypatch.setattr("reseam.budget_pair.time.monotonic", lambda: clock[0])
    monkeypatch.setattr(item, "geometry_errors", delayed_validation)
    result = solve_budget_pair(item, seconds=1, iterations=100)
    assert result.status == "TIMEOUT_WITH_BEST"
    assert np.array_equal(result.state.uv, state.uv)
