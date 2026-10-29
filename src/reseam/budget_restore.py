"""Local box-constrained epigraph restoration for a frozen constraint model.

This solves ``min xi + w/2 * (d+offset).T @ P @ (d+offset)`` subject
to ``g + A d <= xi``, ``xi >= 0`` and a finite box containing zero.  It
neither certifies the original nonlinear constraints nor labels failure of
the numerical iteration as mathematical infeasibility.
"""

from collections.abc import Callable
from dataclasses import dataclass
from time import perf_counter
from typing import Literal, Protocol

import numpy as np
from numpy.typing import NDArray

from .types import FloatArray

RestorationStatus = Literal[
    "converged", "max_iterations", "deadline", "numerical_failure", "line_search_failure"
]


class LinearizedConstraintOperator(Protocol):
    """A real matrix and its adjoint, without requiring a dense matrix."""

    @property
    def shape(self) -> tuple[int, int]: ...

    def matvec(self, vector: FloatArray) -> FloatArray: ...

    def rmatvec(self, vector: FloatArray) -> FloatArray: ...


@dataclass(frozen=True)
class DenseConstraintOperator:
    """Small dense convenience adapter; production operators can be matrix-free."""

    matrix: FloatArray

    @property
    def shape(self) -> tuple[int, int]:
        return (int(self.matrix.shape[0]), int(self.matrix.shape[1]))

    def matvec(self, vector: FloatArray) -> FloatArray:
        return np.asarray(self.matrix @ vector, dtype=np.float64)

    def rmatvec(self, vector: FloatArray) -> FloatArray:
        return np.asarray(self.matrix.T @ vector, dtype=np.float64)


@dataclass(frozen=True)
class RestorationIteration:
    """Best primal and feasible dual archives after one accepted dual step."""

    iteration: int
    primal_objective: float
    dual_objective: float
    duality_gap: float
    xi: float
    lipschitz: float
    backtracks: int
    elapsed_seconds: float


@dataclass(frozen=True)
class EpigraphRestorationResult:
    step: FloatArray
    xi: float
    primal_objective: float
    dual_objective: float
    duality_gap: float
    dual_weights: FloatArray
    status: RestorationStatus
    iterations: int
    history: tuple[RestorationIteration, ...]
    diagnostics: dict[str, float | int | str]


@dataclass(frozen=True)
class _Evaluation:
    step: FloatArray
    residual: FloatArray
    adjoint: FloatArray
    xi: float
    primal: float
    dual: float


class _NumericalIssue(ArithmeticError):
    """A representability/operator failure, rather than an infeasibility result."""


def _finite_scalar(value: float | np.longdouble, name: str) -> float:
    if not np.isfinite(value) or abs(value) > np.finfo(np.float64).max:
        raise _NumericalIssue(f"{name} is not a finite float64 value")
    return float(value)


def _dot(left: FloatArray, right: FloatArray) -> np.longdouble:
    return np.sum(left.astype(np.longdouble) * right.astype(np.longdouble), dtype=np.longdouble)


def _max_abs(vector: FloatArray) -> float:
    return float(np.max(np.abs(vector), initial=0.0))


def project_nonnegative_l1_ball(values: FloatArray | NDArray[np.longdouble]) -> FloatArray:
    """Euclidean projection onto ``{lambda >= 0, sum(lambda) <= 1}``.

    Shifting by the largest coordinate before the simplex calculation also
    handles equal coordinates far larger than one without losing the unit
    simplex radius to floating-point cancellation.
    """
    vector = np.asarray(values, dtype=np.longdouble)
    if vector.ndim != 1 or not np.all(np.isfinite(vector)):
        raise ValueError("projection input must be a finite vector")
    positive = np.maximum(vector, np.longdouble(0.0))
    if np.sum(positive, dtype=np.longdouble) <= 1.0:
        return np.asarray(positive, dtype=np.float64)
    shifted = positive - np.max(positive)
    ordered = np.sort(shifted)[::-1]
    count = np.arange(1, len(ordered) + 1, dtype=np.longdouble)
    thresholds = (np.cumsum(ordered, dtype=np.longdouble) - 1.0) / count
    active = np.flatnonzero(ordered > thresholds)
    threshold = thresholds[int(active[-1])]
    projected = np.asarray(np.maximum(shifted - threshold, 0.0), dtype=np.float64)
    # Account for a last-bit conversion/summation excess while preserving feasibility.
    total = float(np.sum(projected))
    if total > 1.0:
        projected /= total
    return projected


def _operator_vector(value: FloatArray, size: int, name: str) -> FloatArray:
    vector = np.asarray(value, dtype=np.float64)
    if vector.shape != (size,) or not np.all(np.isfinite(vector)):
        raise _NumericalIssue(f"{name} must return a finite vector of shape ({size},)")
    return vector


def _evaluate(
    weights: FloatArray,
    operator: LinearizedConstraintOperator,
    g: FloatArray,
    curvature: FloatArray,
    offset: FloatArray,
    lower: FloatArray,
    upper: FloatArray,
) -> _Evaluation:
    try:
        adjoint = _operator_vector(operator.rmatvec(weights), len(lower), "operator.rmatvec")
        # Overflow of the unconstrained minimizer is harmless when a finite box clips it.
        with np.errstate(over="ignore", divide="ignore", invalid="raise"):
            step = np.clip(-offset - adjoint / curvature, lower, upper)
        mapped = _operator_vector(operator.matvec(step), len(g), "operator.matvec")
        residual = np.asarray(mapped.astype(np.longdouble) + g.astype(np.longdouble), dtype=np.float64)
        if not np.all(np.isfinite(residual)):
            raise _NumericalIssue("linearized constraint residual is not finite")
        xi = max(0.0, float(np.max(residual, initial=0.0)))
        quadratic = _trust_cost(step, curvature, offset)
        primal = _finite_scalar(np.longdouble(xi) + quadratic, "primal objective")
        dual = _finite_scalar(_dot(weights, g) + _dot(adjoint, step) + quadratic, "dual objective")
    except (FloatingPointError, OverflowError, ValueError) as error:
        raise _NumericalIssue(str(error)) from error
    return _Evaluation(step.copy(), residual.copy(), adjoint.copy(), xi, primal, dual)


def _trust_cost(step: FloatArray, curvature: FloatArray, offset: FloatArray) -> np.longdouble:
    shifted = step.astype(np.longdouble) + offset.astype(np.longdouble)
    return np.sum(curvature.astype(np.longdouble) * shifted * shifted, dtype=np.longdouble) / 2.0


def _dual_model_remainder(
    point: _Evaluation,
    trial: _Evaluation,
    curvature: FloatArray,
    offset: FloatArray,
    lower: FloatArray,
    upper: FloatArray,
) -> np.longdouble:
    """Compute q(trial)-q(point)-grad(q(point)) dot delta without cancellation.

    For the separable box quadratic this is ``n_trial dot (d_trial-d_point)
    - .5 * ||d_trial-d_point||_H**2``, where ``n_trial=c_trial+H*d_trial``
    is zero in the interior and has the appropriate sign on an active bound.
    This identity avoids subtracting almost equal, order-one dual objectives
    when the required backtracking curvature is measured by a tiny step.
    """
    change = trial.step.astype(np.longdouble) - point.step.astype(np.longdouble)
    normal = trial.adjoint.astype(np.longdouble) + curvature.astype(np.longdouble) * (
        trial.step.astype(np.longdouble) + offset.astype(np.longdouble)
    )
    normal = np.where(
        trial.step == lower,
        np.maximum(normal, 0.0),
        np.where(trial.step == upper, np.minimum(normal, 0.0), 0.0),
    )
    return np.sum(
        normal * change - .5 * curvature.astype(np.longdouble) * change * change, dtype=np.longdouble,
    )


def _gap(primal: float, dual: float) -> float:
    # A tiny negative value is possible from arithmetic in the dual objective.
    return max(0.0, _finite_scalar(np.longdouble(primal) - dual, "duality gap"))


def _diagnostics(
    primal: _Evaluation,
    dual: _Evaluation,
    weights: FloatArray,
    trust_weight: float,
    offset: FloatArray,
    elapsed: float,
    evaluations: int,
    numerical_failures: int,
    reason: str,
) -> dict[str, float | int | str]:
    objective_scale = max(1.0, abs(primal.primal), abs(dual.dual))
    constraint_scale = max(1.0, primal.xi, _max_abs(primal.residual))
    variable_scale = max(1.0, _max_abs(primal.step), _max_abs(dual.step))
    normalized_slack = primal.xi / constraint_scale - primal.residual / constraint_scale
    total = float(np.sum(weights))
    stationarity = _max_abs(primal.step / variable_scale - dual.step / variable_scale)
    constraint_complementarity = _max_abs(weights * normalized_slack)
    epigraph_complementarity = abs(primal.xi / constraint_scale * (1.0 - total))
    dual_feasibility = max(0.0, -float(np.min(weights, initial=0.0)), total - 1.0)
    primal_feasibility = max(0.0, -float(np.min(normalized_slack, initial=0.0)))
    return {
        "problem": "linearized_box_epigraph",
        "nonlinear_feasibility": "not_assessed",
        "trust_weight": trust_weight,
        "trust_center": "fixed_origin_with_current_offset",
        "trust_offset_linf": _max_abs(offset),
        "constraint_count": len(primal.residual),
        "variable_count": len(primal.step),
        "elapsed_seconds": elapsed,
        "operator_evaluations": evaluations,
        "numerical_failures": numerical_failures,
        "relative_duality_gap": _gap(primal.primal, dual.dual) / objective_scale,
        "normalized_primal_feasibility": primal_feasibility,
        "dual_feasibility_residual": dual_feasibility,
        "normalized_box_stationarity": stationarity,
        "normalized_constraint_complementarity": constraint_complementarity,
        "normalized_epigraph_complementarity": epigraph_complementarity,
        "normalized_kkt_residual": max(
            primal_feasibility,
            dual_feasibility,
            stationarity,
            constraint_complementarity,
            epigraph_complementarity,
        ),
        "reason": reason,
    }


def solve_epigraph_restoration(
    g: FloatArray,
    operator: FloatArray | LinearizedConstraintOperator,
    lower: FloatArray,
    upper: FloatArray,
    *,
    trust_weight: float,
    trust_diagonal: FloatArray | None = None,
    trust_offset: FloatArray | None = None,
    max_iterations: int = 1000,
    absolute_tolerance: float = 1e-8,
    relative_tolerance: float = 1e-7,
    initial_lipschitz: float = 1.0,
    max_backtracks: int = 120,
    deadline: float | None = None,
    clock: Callable[[], float] = perf_counter,
) -> EpigraphRestorationResult:
    """Solve a frozen Eq.19 epigraph subproblem using its smooth concave dual.

    The exact dual domain is the nonnegative unit l1 ball.  For its feasible
    ``lambda``, ``d(lambda)=clip(-offset-A.T lambda/(trust_weight*P), lower, upper)``.
    Accelerated projected ascent uses a checked quadratic lower model of the
    dual with backtracking, plus a monotonic restart.  No estimated spectral
    bound is taken as a guarantee.  A finite best primal/dual archive survives
    deadlines and numerical failures.  ``deadline`` is absolute in ``clock``
    units; a caller using monotonic time should pass ``clock=time.monotonic``.

    ``trust_weight`` here multiplies one half of the squared trust norm; thus
    it is twice Eq.19's coefficient when that equation omits the one half.
    All inputs remain unchanged.  The operator must implement a true adjoint.
    ``trust_offset=current_y-fixed_y0`` preserves Eq.19's fixed original trust
    center across successive local linearizations; zero recovers the centered
    special case.  Neither the objective nor constraints are replaced by a
    weighted sum of squared hinge losses.
    """
    started = float(clock())
    values = np.array(g, dtype=np.float64, copy=True)
    lo = np.array(lower, dtype=np.float64, copy=True)
    hi = np.array(upper, dtype=np.float64, copy=True)
    diagonal = np.ones_like(lo) if trust_diagonal is None else np.array(
        trust_diagonal, dtype=np.float64, copy=True
    )
    offset = np.zeros_like(lo) if trust_offset is None else np.array(trust_offset, dtype=np.float64, copy=True)
    for vector, name in (
        (values, "g"), (lo, "lower"), (hi, "upper"), (diagonal, "trust_diagonal"), (offset, "trust_offset"),
    ):
        if vector.ndim != 1 or not np.all(np.isfinite(vector)):
            raise ValueError(f"{name} must be a finite vector")
    if lo.shape != hi.shape or diagonal.shape != lo.shape or offset.shape != lo.shape:
        raise ValueError("lower, upper, trust_diagonal, and trust_offset must have the same shape")
    if np.any(lo > 0.0) or np.any(hi < 0.0) or np.any(lo > hi):
        raise ValueError("the finite box must contain the current step zero")
    if not np.isfinite(trust_weight) or trust_weight <= 0.0 or np.any(diagonal <= 0.0):
        raise ValueError("trust_weight and every trust_diagonal entry must be strictly positive")
    with np.errstate(over="ignore", under="ignore"):
        curvature = trust_weight * diagonal
    if not np.all(np.isfinite(curvature)) or np.any(curvature <= 0.0):
        raise ValueError("trust_weight * trust_diagonal must be representable and strictly positive")
    if max_iterations < 0 or max_backtracks < 1:
        raise ValueError("max_iterations must be nonnegative and max_backtracks must be positive")
    if any(not np.isfinite(value) or value < 0.0 for value in (absolute_tolerance, relative_tolerance)):
        raise ValueError("tolerances must be finite and nonnegative")
    if not np.isfinite(initial_lipschitz) or initial_lipschitz <= 0.0:
        raise ValueError("initial_lipschitz must be finite and strictly positive")
    if deadline is not None and not np.isfinite(deadline):
        raise ValueError("deadline must be finite")
    if not np.isfinite(started):
        raise ValueError("clock must return finite values")
    if isinstance(operator, np.ndarray):
        matrix = np.array(operator, dtype=np.float64, copy=True)
        if matrix.ndim != 2 or not np.all(np.isfinite(matrix)):
            raise ValueError("dense operator must be a finite matrix")
        linear: LinearizedConstraintOperator = DenseConstraintOperator(matrix)
    else:
        linear = operator
    if linear.shape != (len(values), len(lo)):
        raise ValueError("operator shape must equal (len(g), len(lower))")

    zero = np.zeros_like(lo)
    initial_xi = max(0.0, float(np.max(values, initial=0.0)))
    initial_primal = _finite_scalar(np.longdouble(initial_xi) + _trust_cost(zero, curvature, offset), "initial objective")
    minimum_trust_step = np.clip(-offset, lo, hi)
    initial_dual = _finite_scalar(_trust_cost(minimum_trust_step, curvature, offset), "initial dual")
    best_primal = _Evaluation(zero, values.copy(), zero.copy(), initial_xi, initial_primal, initial_dual)
    # The residual at this trust minimizer is evaluated lazily, after the deadline
    # check. Its step and dual value already give an exact lambda=0 lower bound.
    best_dual = _Evaluation(
        minimum_trust_step, values.copy(), zero.copy(), initial_xi, initial_primal, initial_dual,
    )
    best_weights = np.zeros_like(values)
    weights = best_weights.copy()
    current = best_dual
    current_evaluated = np.array_equal(minimum_trust_step, zero)
    extrapolated = weights.copy()
    momentum = 1.0
    lipschitz = initial_lipschitz
    history: list[RestorationIteration] = [
        RestorationIteration(
            0, initial_primal, initial_dual, _gap(initial_primal, initial_dual), initial_xi, lipschitz, 0, 0.0,
        )
    ]
    status: RestorationStatus = "max_iterations"
    reason = "iteration limit reached; local convex optimality remains uncertified"
    iterations = 0
    evaluations = 0
    numerical_failures = 0
    elapsed = 0.0

    def expired() -> bool:
        nonlocal elapsed
        now = float(clock())
        if not np.isfinite(now):
            raise _NumericalIssue("clock returned a nonfinite value")
        elapsed = max(0.0, _finite_scalar(np.longdouble(now) - started, "elapsed time"))
        return deadline is not None and now >= deadline

    def converged() -> bool:
        scale = max(1.0, abs(best_primal.primal), abs(best_dual.dual))
        return _gap(best_primal.primal, best_dual.dual) <= absolute_tolerance + relative_tolerance * scale

    if converged():
        status = "converged"
        reason = "zero step already solves the frozen convex subproblem within the gap tolerance"
    else:
        try:
            for iteration in range(1, max_iterations + 1):
                if expired():
                    status = "deadline"
                    reason = "deadline reached; best finite primal and feasible dual were retained"
                    break
                at_current = np.array_equal(extrapolated, weights)
                if at_current and current_evaluated:
                    point = current
                else:
                    point = _evaluate(extrapolated, linear, values, curvature, offset, lo, hi)
                    evaluations += 1
                    if at_current:
                        current, current_evaluated = point, True
                        if point.dual >= best_dual.dual:
                            best_dual, best_weights = point, weights.copy()
                if point.primal < best_primal.primal:
                    best_primal = point
                accepted = False
                restarted = False
                backtracks = 0
                last_numeric_reason = ""
                # Trying a larger step is safe because every trial is checked below.
                trial_lipschitz = max(lipschitz / 2.0, np.finfo(np.float64).tiny)
                while backtracks < max_backtracks:
                    if expired():
                        status = "deadline"
                        reason = "deadline reached during backtracking; best archive was retained"
                        break
                    with np.errstate(over="ignore", invalid="ignore"):
                        trial_argument = extrapolated.astype(np.longdouble) + (
                            point.residual.astype(np.longdouble) / trial_lipschitz
                        )
                    trial_weights = project_nonnegative_l1_ball(trial_argument)
                    delta = trial_weights - extrapolated
                    squared_distance = _dot(delta, delta)
                    try:
                        trial = _evaluate(trial_weights, linear, values, curvature, offset, lo, hi)
                        evaluations += 1
                    except _NumericalIssue as error:
                        numerical_failures += 1
                        last_numeric_reason = str(error)
                        trial_lipschitz = _finite_scalar(np.longdouble(trial_lipschitz) * 2.0, "Lipschitz trial")
                        backtracks += 1
                        continue
                    if trial.primal < best_primal.primal:
                        best_primal = trial
                    if trial.dual > best_dual.dual:
                        best_dual, best_weights = trial, trial_weights.copy()
                    if deadline is not None and expired():
                        status = "deadline"
                        reason = "operator evaluation crossed the deadline; best archive was retained"
                        break
                    remainder = _dual_model_remainder(point, trial, curvature, offset, lo, hi)
                    model_remainder = -np.longdouble(trial_lipschitz) * squared_distance / 2.0
                    roundoff = 32.0 * np.finfo(np.float64).eps * max(
                        np.finfo(np.float64).tiny, abs(remainder), abs(model_remainder)
                    )
                    if remainder + roundoff >= model_remainder:
                        monotonic_roundoff = 32 * np.finfo(np.float64).eps * max(
                            np.finfo(np.float64).tiny, abs(trial.dual), abs(current.dual),
                        )
                        if not at_current and trial.dual + monotonic_roundoff < current.dual and not restarted:
                            extrapolated = weights.copy()
                            point = current
                            momentum = 1.0
                            at_current = restarted = True
                            continue
                        accepted = True
                        break
                    required = -2.0 * remainder
                    if squared_distance > 0.0:
                        required /= squared_distance
                    trial_lipschitz = _finite_scalar(
                        max(np.longdouble(trial_lipschitz) * 2.0, required * 1.05), "Lipschitz trial"
                    )
                    backtracks += 1
                if status == "deadline":
                    break
                if not accepted:
                    status = "numerical_failure" if last_numeric_reason else "line_search_failure"
                    reason = last_numeric_reason or "backtracking limit reached; no infeasibility claim"
                    break
                previous_weights = weights
                weights, current = trial_weights, trial
                lipschitz = trial_lipschitz
                iterations = iteration
                history.append(RestorationIteration(
                    iteration,
                    best_primal.primal,
                    best_dual.dual,
                    _gap(best_primal.primal, best_dual.dual),
                    best_primal.xi,
                    lipschitz,
                    backtracks,
                    elapsed,
                ))
                if converged():
                    status = "converged"
                    reason = "primal-dual gap satisfies the frozen convex subproblem tolerance"
                    break
                next_momentum = (1.0 + np.sqrt(1.0 + 4.0 * momentum * momentum)) / 2.0
                extrapolated = weights + ((momentum - 1.0) / next_momentum) * (weights - previous_weights)
                momentum = float(next_momentum)
        except _NumericalIssue as error:
            status = "numerical_failure"
            reason = str(error)
            numerical_failures += 1
    return EpigraphRestorationResult(
        best_primal.step.copy(),
        best_primal.xi,
        best_primal.primal,
        best_dual.dual,
        _gap(best_primal.primal, best_dual.dual),
        best_weights.copy(),
        status,
        iterations,
        tuple(history),
        _diagnostics(
            best_primal, best_dual, best_weights, trust_weight, offset, elapsed,
            evaluations, numerical_failures, reason,
        ),
    )
