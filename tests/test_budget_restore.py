"""Independent convex references for local epigraph restoration, without assets."""

from collections.abc import Callable
from dataclasses import dataclass
from typing import cast

import numpy as np
import pytest
from scipy.optimize import minimize
from scipy.sparse.linalg import LinearOperator

from reseam.budget_restore import (
    LinearizedConstraintOperator,
    project_nonnegative_l1_ball,
    solve_epigraph_restoration,
)
from reseam.types import FloatArray


def _reference_solution(
    g: FloatArray,
    matrix: FloatArray,
    lower: FloatArray,
    upper: FloatArray,
    weight: float,
    diagonal: FloatArray,
    offset: FloatArray | None = None,
) -> tuple[FloatArray, float, float]:
    """SLSQP works in primal (d, xi), independently of the dual implementation."""
    count = len(lower)
    shift = np.zeros(count) if offset is None else offset

    def objective(state: FloatArray) -> float:
        return float(state[-1] + weight / 2 * np.sum(diagonal * (state[:count] + shift) ** 2))

    def gradient(state: FloatArray) -> FloatArray:
        return np.concatenate((weight * diagonal * (state[:count] + shift), [1.0]))

    def slack(state: FloatArray) -> FloatArray:
        return state[-1] - g - matrix @ state[:count]

    def jacobian(state: FloatArray) -> FloatArray:
        del state
        return np.column_stack((-matrix, np.ones(len(g))))

    initial = np.concatenate((np.zeros(count), [max(0.0, float(np.max(g, initial=0.0)))]))
    bounds = list(zip(lower, upper, strict=True)) + [(0.0, np.inf)]
    reference = minimize(
        objective, initial, jac=gradient, bounds=bounds, method="SLSQP",
        constraints={"type": "ineq", "fun": slack, "jac": jacobian},
        options={"ftol": 1e-12, "maxiter": 2000},
    )
    assert reference.success, reference.message
    return reference.x[:count], float(reference.x[-1]), float(reference.fun)


@pytest.mark.parametrize("seed", [17, 29, 43])
def test_tiny_epigraph_matches_independent_primal_slsqp(seed: int) -> None:
    rng = np.random.default_rng(seed)
    matrix = rng.normal(size=(7, 3))
    g = rng.uniform(-0.2, 1.1, 7)
    lower, upper = np.asarray([-.3, -.7, -.5]), np.asarray([.5, .4, .8])
    diagonal = np.asarray([.7, 2.1, 1.3])
    weight = .4
    reference_step, reference_xi, reference_objective = _reference_solution(
        g, matrix, lower, upper, weight, diagonal,
    )
    result = solve_epigraph_restoration(
        g, matrix, lower, upper, trust_weight=weight, trust_diagonal=diagonal,
        max_iterations=10000, absolute_tolerance=1e-11, relative_tolerance=1e-10,
    )
    assert result.status == "converged", result.diagnostics
    assert result.primal_objective == pytest.approx(reference_objective, abs=2e-9)
    assert result.xi == pytest.approx(reference_xi, abs=3e-6)
    assert np.allclose(result.step, reference_step, atol=3e-6)
    assert result.dual_objective <= reference_objective + 1e-11
    assert result.duality_gap < 2e-10
    assert result.diagnostics["normalized_primal_feasibility"] == 0.0
    dual_residual = result.diagnostics["dual_feasibility_residual"]
    kkt_residual = result.diagnostics["normalized_kkt_residual"]
    assert isinstance(dual_residual, (int, float)) and dual_residual < 1e-14
    assert isinstance(kkt_residual, (int, float)) and kkt_residual < 2e-5
    assert result.diagnostics["nonlinear_feasibility"] == "not_assessed"


def test_conflicting_constraints_use_maximum_not_unequal_weighted_squared_hinges() -> None:
    g, matrix = np.ones(2), np.asarray([[1.0], [-1.0]])
    result = solve_epigraph_restoration(g, matrix, np.asarray([-1.0]), np.ones(1), trust_weight=.1)
    # min max(1+d, 1-d) + .05*d**2 is uniquely minimized at zero.
    assert result.status == "converged"
    assert result.step[0] == pytest.approx(0.0, abs=1e-12)
    assert result.xi == pytest.approx(1.0)
    assert result.dual_weights == pytest.approx([.5, .5])
    # The competing 9:1 weighted squared hinge has a different analytic minimizer.
    weighted_hinge_step = -16.0 / (20.0 + .1)
    weighted_hinge_xi = float(np.max(g + matrix[:, 0] * weighted_hinge_step))
    assert abs(weighted_hinge_step - result.step[0]) > .79
    assert weighted_hinge_xi > result.xi + .79


def test_trust_coefficient_and_active_box_have_analytic_solution() -> None:
    # With d in [-.2,.4], g=1+d and wP=2, the clipped optimum is d=-.2.
    result = solve_epigraph_restoration(
        np.ones(1), np.ones((1, 1)), np.asarray([-.2]), np.asarray([.4]),
        trust_weight=.5, trust_diagonal=np.asarray([4.0]),
        absolute_tolerance=1e-12, relative_tolerance=0.0,
    )
    assert result.status == "converged"
    assert result.step == pytest.approx([-.2])
    assert result.xi == pytest.approx(.8)
    assert result.primal_objective == pytest.approx(.84)
    assert result.dual_objective == pytest.approx(.84)


@pytest.mark.parametrize("seed", [17, 29, 43])
def test_fixed_original_trust_center_matches_independent_shifted_epigraph(seed: int) -> None:
    rng = np.random.default_rng(seed)
    matrix = rng.normal(size=(5, 3))
    g = rng.uniform(-.2, .9, 5)
    offset = np.asarray([.4, -.7, .2])
    original_offset = offset.copy()
    lower, upper = np.asarray([-.5, -.3, -.7]), np.asarray([.4, .8, .2])
    diagonal = np.asarray([.8, 1.3, 2.1])
    reference_step, reference_xi, reference_objective = _reference_solution(
        g, matrix, lower, upper, .7, diagonal, offset,
    )
    result = solve_epigraph_restoration(
        g, matrix, lower, upper, trust_weight=.7, trust_diagonal=diagonal, trust_offset=offset,
        absolute_tolerance=1e-11, relative_tolerance=1e-10, max_iterations=3000,
    )
    assert result.status == "converged", result.diagnostics
    assert result.primal_objective == pytest.approx(reference_objective, abs=2e-9)
    assert result.xi == pytest.approx(reference_xi, abs=3e-6)
    assert np.allclose(result.step, reference_step, atol=3e-6)
    assert result.primal_objective == pytest.approx(
        result.xi + .7 / 2 * float(np.sum(diagonal * (result.step + offset) ** 2)), abs=1e-12,
    )
    assert result.diagnostics["trust_center"] == "fixed_origin_with_current_offset"
    assert np.array_equal(offset, original_offset)


def test_satisfied_constraints_still_allow_return_to_fixed_trust_origin() -> None:
    offset = np.asarray([.1, .2])
    result = solve_epigraph_restoration(
        np.asarray([-.1, -.2]), np.eye(2), -np.ones(2), np.ones(2),
        trust_weight=1.0, trust_offset=offset,
    )
    assert result.status == "converged"
    assert result.step == pytest.approx(-offset)
    assert result.primal_objective == result.dual_objective == result.xi == 0.0


def test_epigraph_zero_slack_uses_dual_ball_interior_not_forced_simplex() -> None:
    result = solve_epigraph_restoration(
        np.asarray([.2]), np.ones((1, 1)), -np.ones(1), np.ones(1), trust_weight=1.0,
        absolute_tolerance=1e-12, relative_tolerance=0.0,
    )
    assert result.status == "converged"
    assert result.step == pytest.approx([-.2], abs=1e-10)
    assert result.xi == pytest.approx(0.0, abs=1e-10)
    assert result.dual_weights == pytest.approx([.2], abs=1e-10)
    assert result.primal_objective == pytest.approx(.02, abs=1e-12)


@dataclass
class _RankOneOperator:
    left: FloatArray
    right: FloatArray
    matvec_calls: int = 0
    adjoint_calls: int = 0

    @property
    def shape(self) -> tuple[int, int]:
        return (len(self.left), len(self.right))

    def matvec(self, vector: FloatArray) -> FloatArray:
        self.matvec_calls += 1
        return self.left * float(self.right @ vector)

    def rmatvec(self, vector: FloatArray) -> FloatArray:
        self.adjoint_calls += 1
        return self.right * float(self.left @ vector)


def test_matrix_free_adjoint_and_dense_equivalence_preserve_inputs() -> None:
    operator = _RankOneOperator(np.asarray([1., -.7, .2]), np.asarray([.4, -.8, .5, .1]))
    probe, dual_probe = np.asarray([.2, .3, -.1, .7]), np.asarray([.4, -.2, .9])
    assert float(dual_probe @ operator.matvec(probe)) == pytest.approx(float(probe @ operator.rmatvec(dual_probe)))
    g, lower, upper = np.asarray([.7, .2, -.1]), np.full(4, -.5), np.full(4, .3)
    diagonal = np.asarray([.2, .7, 1.3, 2.1])
    matrix = np.outer(operator.left, operator.right)
    originals = [value.copy() for value in (g, lower, upper, diagonal, matrix, operator.left, operator.right)]
    kwargs = {"trust_weight": .3, "trust_diagonal": diagonal, "absolute_tolerance": 1e-11}
    implicit = solve_epigraph_restoration(g, operator, lower, upper, **kwargs)
    dense = solve_epigraph_restoration(g, matrix, lower, upper, **kwargs)
    # SciPy's __new__ dispatches to _CustomLinearOperator(shape, matvec,
    # rmatvec); its base constructor annotation describes (dtype, shape).
    factory = cast(Callable[
        [tuple[int, int], Callable[[FloatArray], FloatArray], Callable[[FloatArray], FloatArray]],
        LinearOperator,
    ], LinearOperator)
    scipy_operator = factory(matrix.shape, operator.matvec, operator.rmatvec)
    assert scipy_operator.shape == matrix.shape
    assert np.allclose(scipy_operator.matvec(probe), matrix @ probe)
    assert np.allclose(scipy_operator.rmatvec(dual_probe), matrix.T @ dual_probe)
    # The solver passes these callbacks positionally. SciPy names the vector
    # parameter x, while the local structural protocol names it vector.
    scipy_result = solve_epigraph_restoration(
        g, cast(LinearizedConstraintOperator, scipy_operator), lower, upper, **kwargs)
    assert implicit.status == dense.status == scipy_result.status == "converged"
    assert np.allclose(implicit.step, dense.step, atol=2e-7)
    assert np.allclose(scipy_result.step, dense.step, atol=2e-7)
    assert implicit.primal_objective == pytest.approx(dense.primal_objective, abs=1e-10)
    assert operator.matvec_calls > 0 and operator.adjoint_calls > 0
    for value, original in zip((g, lower, upper, diagonal, matrix, operator.left, operator.right), originals):
        assert np.array_equal(value, original)
    assert not np.shares_memory(implicit.step, lower)


@pytest.mark.parametrize("scale", [1e-10, 1e10, 1e100])
def test_objective_scale_and_backtracking_are_finite_without_spectral_bound(scale: float) -> None:
    # Multiplying the entire objective and inequalities by scale preserves d=-.5.
    result = solve_epigraph_restoration(
        np.asarray([scale]), np.asarray([[scale]]), np.asarray([-2.0]), np.ones(1),
        trust_weight=2 * scale, initial_lipschitz=1.0,
        absolute_tolerance=scale * 1e-9, relative_tolerance=0.0, max_iterations=1000,
    )
    assert result.status == "converged", result.diagnostics
    assert result.step[0] == pytest.approx(-.5, abs=2e-5)
    assert result.primal_objective / scale == pytest.approx(.75, abs=2e-9)
    for value in result.diagnostics.values():
        if isinstance(value, float):
            assert np.isfinite(value)
    assert all(np.isfinite(row.duality_gap) and np.isfinite(row.lipschitz) for row in result.history)
    if scale > 1:
        assert any(row.backtracks > 0 for row in result.history)


@pytest.mark.parametrize("g", [np.zeros(0), np.asarray([-.3, 0.0, -.7])])
def test_no_constraints_or_satisfied_constraints_have_zero_optimum(g: FloatArray) -> None:
    result = solve_epigraph_restoration(
        g, np.zeros((len(g), 3)), np.full(3, -.5), np.full(3, .7), trust_weight=2.0,
    )
    assert result.status == "converged" and result.iterations == 0
    assert result.xi == result.primal_objective == result.dual_objective == result.duality_gap == 0.0
    assert np.array_equal(result.step, np.zeros(3))
    assert result.diagnostics["normalized_kkt_residual"] == 0.0


def test_no_variables_still_optimizes_epigraph_dual() -> None:
    result = solve_epigraph_restoration(
        np.asarray([-.2, 1.3, .7]), np.zeros((3, 0)), np.zeros(0), np.zeros(0), trust_weight=1.0,
    )
    assert result.status == "converged"
    assert result.step.shape == (0,)
    assert result.xi == pytest.approx(1.3)
    assert result.dual_objective == pytest.approx(1.3)


def test_expired_deadline_retains_finite_zero_archive() -> None:
    result = solve_epigraph_restoration(
        np.ones(1), np.ones((1, 2)), -np.ones(2), np.ones(2),
        trust_weight=1.0, deadline=4.0, clock=lambda: 4.0,
    )
    assert result.status == "deadline" and result.iterations == 0
    assert result.step == pytest.approx([0., 0.])
    assert result.xi == result.primal_objective == result.duality_gap == 1.0


def test_immediate_deadline_retains_exact_shifted_dual_witness_without_operator_calls() -> None:
    operator = _RankOneOperator(np.ones(1), np.ones(1))
    result = solve_epigraph_restoration(
        np.ones(1), operator, np.asarray([-.5]), np.asarray([.5]),
        trust_weight=2.0, trust_offset=np.asarray([3.0]), deadline=1.0, clock=lambda: 1.0,
    )
    assert result.status == "deadline"
    assert result.step == pytest.approx([0.0])
    assert result.xi == 1.0 and result.primal_objective == 10.0
    assert result.dual_weights == pytest.approx([0.0])
    # The lambda=0 dual minimizes the shifted trust quadratic inside the box.
    assert result.dual_objective == 6.25 and result.duality_gap == 3.75
    assert operator.matvec_calls == operator.adjoint_calls == 0


def test_operator_crossing_deadline_keeps_progress_and_reports_deadline() -> None:
    now = [0.0]

    @dataclass
    class LateOperator:
        @property
        def shape(self) -> tuple[int, int]:
            return (1, 1)

        def matvec(self, vector: FloatArray) -> FloatArray:
            now[0] = 2.0
            return vector.copy()

        def rmatvec(self, vector: FloatArray) -> FloatArray:
            return vector.copy()

    result = solve_epigraph_restoration(
        np.ones(1), LateOperator(), -np.ones(1), np.ones(1), trust_weight=2.0,
        deadline=1.0, clock=lambda: now[0],
    )
    assert result.status == "deadline"
    assert result.primal_objective == pytest.approx(.75)
    assert result.dual_objective == pytest.approx(.75)
    assert result.step == pytest.approx([-.5])
    assert result.diagnostics["elapsed_seconds"] == 2.0


def test_deadline_after_progress_returns_best_not_last_dual_primal() -> None:
    ticks = [0]

    def clock() -> float:
        ticks[0] += 1
        return float(ticks[0])

    result = solve_epigraph_restoration(
        np.asarray([1.0, .7, .1]), np.asarray([[1., .2], [-.3, .8], [.6, -.4]]),
        -np.ones(2), np.ones(2), trust_weight=.3, max_iterations=1000,
        absolute_tolerance=0.0, relative_tolerance=0.0, deadline=15.0, clock=clock,
    )
    assert result.status == "deadline" and result.iterations > 0
    assert result.primal_objective < result.history[0].primal_objective
    assert result.primal_objective <= min(row.primal_objective for row in result.history)
    assert all(later.primal_objective <= earlier.primal_objective for earlier, later in zip(
        result.history, result.history[1:], strict=False,
    ))
    residual = np.asarray([1.0, .7, .1]) + np.asarray([[1., .2], [-.3, .8], [.6, -.4]]) @ result.step
    assert result.xi == pytest.approx(max(0.0, float(np.max(residual))))
    assert result.dual_objective <= result.primal_objective + 1e-12


def test_numerical_operator_failure_is_not_reported_as_infeasibility() -> None:
    @dataclass
    class BrokenOperator:
        @property
        def shape(self) -> tuple[int, int]:
            return (1, 1)

        def matvec(self, vector: FloatArray) -> FloatArray:
            del vector
            return np.asarray([np.nan])

        def rmatvec(self, vector: FloatArray) -> FloatArray:
            del vector
            return np.zeros(1)

    result = solve_epigraph_restoration(
        np.ones(1), BrokenOperator(), -np.ones(1), np.ones(1), trust_weight=1.0, max_backtracks=3,
    )
    assert result.status == "numerical_failure"
    assert result.step == pytest.approx([0.])
    assert result.primal_objective == 1.0 and result.dual_objective == 0.0
    assert result.diagnostics["numerical_failures"] == 3
    reason = result.diagnostics["reason"]
    assert isinstance(reason, str) and "finite vector" in reason


@pytest.mark.parametrize("values, expected", [
    ([-2., .2, .1], [0., .2, .1]),
    ([2., 2., 0.], [.5, .5, 0.]),
    ([1e300, 1e300, -1e300], [.5, .5, 0.]),
    ([1000., 999.5, -1.], [.75, .25, 0.]),
    ([], []),
])
def test_nonnegative_l1_projection(values: list[float], expected: list[float]) -> None:
    result = project_nonnegative_l1_ball(np.asarray(values))
    assert result == pytest.approx(expected)
    assert np.all(result >= 0) and float(np.sum(result)) <= 1.0


@pytest.mark.parametrize("weight, diagonal", [(0.0, 1.0), (-1.0, 1.0), (np.inf, 1.0), (1.0, 0.0)])
def test_invalid_trust_is_rejected_explicitly(weight: float, diagonal: float) -> None:
    with pytest.raises(ValueError, match="strictly positive"):
        solve_epigraph_restoration(
            np.ones(1), np.ones((1, 1)), -np.ones(1), np.ones(1),
            trust_weight=weight, trust_diagonal=np.asarray([diagonal]),
        )


@pytest.mark.parametrize("lower, upper", [([.1], [1.]), ([-1.], [-.1]), ([-np.inf], [1.])])
def test_box_must_be_finite_and_contain_zero(lower: list[float], upper: list[float]) -> None:
    with pytest.raises(ValueError, match="finite|contain"):
        solve_epigraph_restoration(
            np.ones(1), np.ones((1, 1)), np.asarray(lower), np.asarray(upper), trust_weight=1.0,
        )
