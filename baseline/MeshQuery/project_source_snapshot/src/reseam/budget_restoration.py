"""Joint sequential epigraph restoration with a fixed Eq. (19) trust center.

The inner primal/dual gap certifies only its frozen convex model. Nonlinear
proxy values are rebuilt after every trial, and the caller supplies the full
hard admissibility set H. Encoded/export quality remains a separate gate.
"""

import math
import time
from collections.abc import Callable
from dataclasses import dataclass, field, replace

import numpy as np
from numpy.typing import NDArray

from .budget_constraints import (
    ConstraintLinearization,
    ConstraintOperator,
    ConstraintValues,
    evaluate_geometry_constraints,
    evaluate_material_constraints,
    linearize_geometry_constraints,
    linearize_material_constraints,
)
from .budget_material import MaterialThresholds
from .budget_optimize import FeatureSamples, LegalUV, QualityCheck, TargetUpdate, safe_uv_step
from .budget_restore import solve_epigraph_restoration
from .types import FloatArray, IntArray, MeshState

BoolArray = NDArray[np.bool_]


@dataclass(frozen=True)
class RestorationOptions:
    """Prelocked scales, constraints and finite work limits for one candidate.

    ``trust_weight`` uses the core's ``w/2`` convention, hence equals twice
    the coefficient in Eq. (19). UV trust is measured in actual atlas pixels;
    texture trust is in signed-linear channel units. No trust recenter occurs.
    """

    seconds: float = 30.0
    iterations: int = 30
    local_iterations: int = 500
    trust_weight: float = 1e-4
    uv_trust_pixels: float = 0.35
    texture_trust_radius: float = 0.1
    optimize_uv: bool = True
    include_render: bool = True
    thresholds: MaterialThresholds = field(default_factory=MaterialThresholds)
    important_faces: tuple[int, ...] = ()
    alpha_minimum: float = 0.99
    mean_shape_limit: float = 0.025
    tail_shape_limit: float = 0.1
    maximum_anisotropy: float = 2.0
    maximum_neighbor_density_ratio: float = 2.0
    maximum_backtracks: int = 20
    armijo: float = 1e-4
    minimum_improvement: float = 1e-10
    step_tolerance: float = 1e-10
    local_absolute_tolerance: float = 1e-7
    local_relative_tolerance: float = 1e-6
    quality_check_interval: int = 5

    def __post_init__(self) -> None:
        nonnegative = (self.seconds, self.minimum_improvement, self.step_tolerance,
                       self.local_absolute_tolerance, self.local_relative_tolerance,
                       self.mean_shape_limit, self.tail_shape_limit)
        positive = (self.trust_weight, self.uv_trust_pixels, self.texture_trust_radius)
        if (not all(math.isfinite(x) and x >= 0 for x in nonnegative)
                or not all(math.isfinite(x) and x > 0 for x in positive)
                or not all(math.isfinite(x) and x >= 1 for x in (
                    self.maximum_anisotropy, self.maximum_neighbor_density_ratio))
                or not 0 < self.alpha_minimum < 1
                or not 0 < self.armijo < 1
                or type(self.iterations) is not int or self.iterations < 0
                or type(self.local_iterations) is not int or self.local_iterations < 0
                or type(self.maximum_backtracks) is not int or self.maximum_backtracks < 1
                or type(self.quality_check_interval) is not int or self.quality_check_interval < 1):
            raise ValueError("Invalid sequential restoration protocol")


@dataclass
class BudgetRestorationResult:
    """Best actual nonlinear-proxy state, never an encoded-quality certificate."""

    status: str
    state: MeshState
    texture: FloatArray
    initial_proxy: float
    final_proxy: float
    xi: float
    history: list[dict[str, object]]
    wall_seconds: float
    constraint_values: FloatArray
    constraint_labels: tuple[str, ...]
    metadata: dict[str, object]
    quality_valid: bool = False
    best_quality_state: MeshState | None = None
    best_quality_texture: FloatArray | None = None
    best_quality_violation: float | None = None
    best_xi: float = math.inf
    best_xi_state: MeshState | None = None
    best_xi_texture: FloatArray | None = None
    # Compatibility with the old optimizer's return contract. Only the outer
    # complete archive gate is allowed to populate a validated candidate.
    best_valid_state: MeshState | None = None
    best_valid_texture: FloatArray | None = None
    reason: str | None = None


@dataclass(frozen=True)
class _UVReduction:
    first: IntArray
    inverse: IntArray
    counts: IntArray
    lower: FloatArray
    upper: FloatArray
    active: BoolArray
    resolution: FloatArray

    @property
    def size(self) -> int:
        return 2 * len(self.first)

    def expand(self, pixel_delta: FloatArray) -> FloatArray:
        return pixel_delta.reshape((-1, 2))[self.inverse] / self.resolution

    def adjoint(self, gradient: FloatArray) -> FloatArray:
        reduced = np.zeros((len(self.first), 2), dtype=np.float64)
        np.add.at(reduced, self.inverse, gradient.reshape((-1, 2)) / self.resolution)
        return reduced.ravel()


@dataclass(frozen=True)
class JointRestorationOperator:
    """A_U dU + A_T dT for material rows, A_G dU for geometry rows.

    UV input columns are explicitly tied pixel coordinates; texture columns
    preserve the compressed operator's original flat HWC identities.
    """

    material_uv: ConstraintOperator
    material_texture: ConstraintOperator
    geometry_uv: ConstraintOperator
    reduction: _UVReduction

    @property
    def shape(self) -> tuple[int, int]:
        return (self.material_uv.shape[0] + self.geometry_uv.shape[0],
                self.reduction.size + self.material_texture.shape[1])

    def matvec(self, vector: FloatArray) -> FloatArray:
        uv = self.reduction.expand(vector[:self.reduction.size]).ravel()
        texture = vector[self.reduction.size:]
        return np.concatenate((self.material_uv.matvec(uv) + self.material_texture.matvec(texture),
                               self.geometry_uv.matvec(uv)))

    def rmatvec(self, vector: FloatArray) -> FloatArray:
        count = self.material_uv.shape[0]
        uv = (self.material_uv.rmatvec(vector[:count])
              + self.geometry_uv.rmatvec(vector[count:]))
        return np.concatenate((self.reduction.adjoint(uv),
                               self.material_texture.rmatvec(vector[:count])))


@dataclass(frozen=True)
class _Proxy:
    material: ConstraintLinearization | ConstraintValues
    geometry: ConstraintLinearization | ConstraintValues
    g: FloatArray
    labels: tuple[str, ...]
    xi: float
    trust: float
    objective: float


def _uv_reduction(
    initial: MeshState, texture: FloatArray, options: RestorationOptions,
    uv_bounds: tuple[FloatArray, FloatArray] | None, motion_ties: IntArray | None,
    active_vertices: BoolArray | None, fixed_vertices: BoolArray | None,
) -> _UVReduction:
    count = len(initial.uv)
    active = np.full(count, options.optimize_uv, dtype=bool)
    for flags, invert in ((active_vertices, False), (fixed_vertices, True)):
        if flags is not None:
            value = np.asarray(flags, dtype=bool)
            if value.shape != (count,):
                raise ValueError("One activity/fixed flag required per original UV copy")
            active &= ~value if invert else value
    lo, hi = ((np.zeros_like(initial.uv), np.ones_like(initial.uv))
              if uv_bounds is None else uv_bounds)
    lo, hi = np.asarray(lo, dtype=np.float64), np.asarray(hi, dtype=np.float64)
    if (lo.shape != initial.uv.shape or hi.shape != initial.uv.shape
            or not np.isfinite(lo).all() or not np.isfinite(hi).all()
            or np.any(lo < 0) or np.any(hi > 1) or np.any(lo > hi)):
        raise ValueError("UV owner bounds must be finite boxes within [0,1]")
    labels = np.arange(count, dtype=np.int64) if motion_ties is None else np.asarray(motion_ties)
    if (labels.shape != (count,) or not np.issubdtype(labels.dtype, np.integer) or np.any(labels < 0)):
        raise ValueError("motion_ties requires nonnegative integer labels for every UV copy")
    _, first, inverse = np.unique(labels, return_index=True, return_inverse=True)
    first, inverse = np.asarray(first, dtype=np.int64), np.asarray(inverse, dtype=np.int64)
    if not np.array_equal(initial.uv, initial.uv[first][inverse]):
        raise ValueError("Motion tie initial UV positions must be exactly equal")
    counts = np.asarray(np.bincount(inverse), dtype=np.int64)
    group_lo, group_hi = np.full((len(first), 2), -np.inf), np.full((len(first), 2), np.inf)
    np.maximum.at(group_lo, inverse, lo)
    np.minimum.at(group_hi, inverse, hi)
    if np.any(group_lo > group_hi):
        raise ValueError("INCOMPATIBLE_MOTION_TIE_BOUNDS: empty group bound intersection")
    if np.any(initial.uv[first] < group_lo) or np.any(initial.uv[first] > group_hi):
        raise ValueError("UV owner bounds must contain the initial UV state")
    group_active = np.ones(len(first), dtype=bool)
    np.logical_and.at(group_active, inverse, active)
    resolution = np.asarray([texture.shape[1], texture.shape[0]], dtype=np.float64)
    return _UVReduction(first, inverse, counts, group_lo.copy(), group_hi.copy(), group_active, resolution)


def _refresh_targets(
    state: MeshState, original: FeatureSamples, update: TargetUpdate | None,
) -> FeatureSamples:
    if update is None:
        return original
    refreshed = update(state.copy())
    for name in ("face_ids", "barycentric", "weights", "mip_levels", "channel_weights"):
        if not np.array_equal(getattr(original, name), getattr(refreshed, name)):
            raise ValueError("Target refresh cannot change fixed original-face probe identities or weights")
    if (refreshed.values.shape != original.values.shape
            or not np.array_equal(refreshed.values[:, :4], original.values[:, :4])
            or not np.array_equal(refreshed.values[:, 7:], original.values[:, 7:])):
        raise ValueError("Target refresh may change only localNormal targets from the fixed world reference")
    return refreshed


def _geometry(
    state: MeshState, orientations: IntArray, image: FloatArray, options: RestorationOptions,
    *, values_only: bool = False,
) -> ConstraintLinearization | ConstraintValues:
    evaluate = evaluate_geometry_constraints if values_only else linearize_geometry_constraints
    return evaluate(
        state, orientations=orientations, mean_shape_limit=options.mean_shape_limit,
        tail_shape_limit=options.tail_shape_limit, maximum_anisotropy=options.maximum_anisotropy,
        maximum_neighbor_density_ratio=options.maximum_neighbor_density_ratio,
        pixel_dimensions=(image.shape[1], image.shape[0]), density_scope="all_original_adjacency",
    )


def _material(
    state: MeshState, samples: FeatureSamples, image: FloatArray, options: RestorationOptions,
    *, uv: bool = False,
) -> ConstraintLinearization:
    return linearize_material_constraints(
        state, samples, image, block="uv" if uv else "texture", thresholds=options.thresholds,
        important_faces=options.important_faces, include_render=options.include_render,
        alpha_minimum=options.alpha_minimum, compressed_texture=not uv,
    )


def _evaluate_proxy(
    state: MeshState, samples: FeatureSamples, image: FloatArray, initial: MeshState,
    initial_texture: FloatArray, reduction: _UVReduction, orientations: IntArray,
    options: RestorationOptions,
    *, derivatives: bool = True,
) -> _Proxy:
    material = (_material(state, samples, image, options) if derivatives else evaluate_material_constraints(
        state, samples, image, thresholds=options.thresholds, important_faces=options.important_faces,
        include_render=options.include_render, alpha_minimum=options.alpha_minimum,
    ))
    geometry = _geometry(state, orientations, image, options, values_only=not derivatives)
    values = np.concatenate((material.g, geometry.g))
    if not np.isfinite(values).all():
        raise FloatingPointError("Nonfinite nonlinear restoration proxy")
    xi = max(0.0, float(np.max(values, initial=0.0)))
    trust = 0.5 * options.trust_weight * float(
        np.sum(((state.uv - initial.uv) * reduction.resolution)**2)
        + np.sum((image - initial_texture)**2))
    objective = xi + trust
    if not math.isfinite(objective):
        raise FloatingPointError("Nonfinite fixed-center trust objective")
    return _Proxy(material, geometry, values, material.labels + geometry.labels, xi, trust, objective)


def _local_box(
    current: MeshState, image: FloatArray, initial: MeshState, initial_texture: FloatArray,
    reduction: _UVReduction, indices: IntArray, channel_bounds: tuple[FloatArray, FloatArray],
    options: RestorationOptions,
) -> tuple[FloatArray, FloatArray, FloatArray, FloatArray, float]:
    group_uv = current.uv[reduction.first]
    uv_lo = np.maximum((reduction.lower - group_uv) * reduction.resolution, -options.uv_trust_pixels)
    uv_hi = np.minimum((reduction.upper - group_uv) * reduction.resolution, options.uv_trust_pixels)
    uv_lo[~reduction.active], uv_hi[~reduction.active] = 0.0, 0.0
    offset_uv = ((group_uv - initial.uv[reduction.first]) * reduction.resolution).ravel()
    values = image.ravel()[indices]
    channels = indices % image.shape[2]
    texture_lo = np.maximum(channel_bounds[0][channels] - values, -options.texture_trust_radius)
    texture_hi = np.minimum(channel_bounds[1][channels] - values, options.texture_trust_radius)
    offset_texture = values - initial_texture.ravel()[indices]
    diagonal = np.concatenate((np.repeat(reduction.counts, 2).astype(float), np.ones(len(indices))))
    offset = np.concatenate((offset_uv, offset_texture))
    omitted = max(0.0, float(np.sum((image - initial_texture)**2) - np.sum(offset_texture**2)))
    return (np.concatenate((uv_lo.ravel(), texture_lo)), np.concatenate((uv_hi.ravel(), texture_hi)),
            diagonal, offset, 0.5 * options.trust_weight * omitted)


def restore_budget_feasibility(
    initial: MeshState, samples: FeatureSamples, initial_texture: FloatArray, *,
    options: RestorationOptions | None = None, target_update: TargetUpdate | None = None,
    legal_uv: LegalUV | None = None, uv_bounds: tuple[FloatArray, FloatArray] | None = None,
    motion_ties: IntArray | None = None, active_vertices: BoolArray | None = None,
    fixed_vertices: BoolArray | None = None, channel_bounds: tuple[FloatArray, FloatArray] | None = None,
    quality_check: QualityCheck | None = None, deadline: float | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> BudgetRestorationResult:
    """Restore the actual maximum normalized proxy violation jointly in U,T.

    ``legal_uv`` must implement H, including complete topology, injectivity,
    authorized overlap and borders. It must permit temporary shape/density
    quality violations; those are restored by the epigraph rows. When H is
    omitted only bounds and orientation are checked, explicitly recorded as
    incomplete. A quality callback produces development snapshots only and
    cannot set ``quality_valid`` or populate the outer delivered archive.

    Every accepted step lowers ``xi + w/2 ||y-y0||_P²`` after target/TBN
    refresh. Trial arrays, callback inputs, and returned archives own copies.
    Deadlines use the same explicitly supplied monotonic clock as the core.
    """
    config = options or RestorationOptions()
    started = float(clock())
    if not math.isfinite(started) or (deadline is not None and not math.isfinite(deadline)):
        raise ValueError("Finite monotonic clock and deadline required")
    stop = min(started + config.seconds, math.inf if deadline is None else deadline)
    origin, current = initial.copy(), initial.copy()
    image = np.asarray(initial_texture, dtype=np.float64).copy()
    origin_texture = image.copy()
    if (image.ndim != 3 or image.shape[2] != 10 or min(image.shape) < 1
            or not np.isfinite(image).all() or samples.values.shape[1] != 10
            or current.uv.ndim != 2 or current.uv.shape[1] != 2 or not np.isfinite(current.uv).all()):
        raise ValueError("Finite UV and RGBA4/signed localNormal3/ORM3 texture and targets required")
    channel_lo, channel_hi = ((np.asarray([0.] * 4 + [-1.] * 3 + [0.] * 3), np.ones(10))
                              if channel_bounds is None else channel_bounds)
    channel_lo, channel_hi = np.asarray(channel_lo, dtype=float), np.asarray(channel_hi, dtype=float)
    if (channel_lo.shape != (10,) or channel_hi.shape != (10,)
            or not np.isfinite(channel_lo).all() or not np.isfinite(channel_hi).all()
            or np.any(channel_lo > channel_hi) or np.any(image < channel_lo) or np.any(image > channel_hi)):
        raise ValueError("Initial texture must lie in its real signed-linear channel bounds")
    reduction = _uv_reduction(origin, image, config, uv_bounds, motion_ties, active_vertices, fixed_vertices)
    edges = current.uv[current.uv_faces[:, 1:]] - current.uv[current.uv_faces[:, :1]]
    determinants = np.linalg.det(edges)
    if not np.isfinite(determinants).all() or np.any(determinants == 0):
        raise ValueError("Nondegenerate finite initial oriented UV faces required")
    orientations = np.sign(determinants).astype(np.int64)
    if legal_uv is not None and not legal_uv(current.copy()):
        raise ValueError("INIT_FAILED: initial candidate rejected by hard admissibility H")
    ties = reduction.counts > 1
    history: list[dict[str, object]] = []
    metadata: dict[str, object] = {
        "scope": "EQ19_SEQUENTIAL_JOINT_FROZEN_PROXY_RESTORATION",
        "trust_center": "FIXED_INITIAL_Y0", "trust_weight": config.trust_weight,
        "trust_convention": "trust_weight/2 * squared_norm; Eq19 lambda=trust_weight/2",
        "uv_units": "actual atlas pixels [width,height]", "texture_units": "signed-linear channels",
        "hard_admissibility_callback_supplied": legal_uv is not None,
        "full_final_validation": False, "encoded_quality_gate_required": True,
        "frozen_inner_quantities": ["localNormal targets", "TBN", "texel address cells", "mip levels"],
        "nonlinear_proposals_rebuild_frames_and_rows": True,
        "motion_tie_groups": int(ties.sum()), "motion_tied_copies": int(reduction.counts[ties].sum()),
        "motion_locked_tie_groups": int(np.count_nonzero(ties & ~reduction.active)),
        "motion_groups_total": len(reduction.first), "h_rejections": 0,
        "quality_callback_scope": "DEVELOPMENT_SNAPSHOTS_ONLY",
        "proxy_evaluated": False,
        "nonlinear_trial_evaluation": "VALUES_ONLY_SAME_ROWS_NO_SHADER_FD_OR_SPARSE_JACOBIAN",
        "full_texture_linearizations": 0, "full_uv_linearizations": 0,
        "nonlinear_trial_value_evaluations": 0,
    }
    best_quality_state: MeshState | None = None
    best_quality_texture: FloatArray | None = None
    best_quality_violation: float | None = None
    best_xi = math.inf
    best_xi_state: MeshState | None = None
    best_xi_texture: FloatArray | None = None

    def expired() -> bool:
        now = float(clock())
        if not math.isfinite(now):
            raise ValueError("Monotonic clock returned a nonfinite value")
        return now >= stop

    def quality_snapshot(iteration: int) -> None:
        nonlocal best_quality_state, best_quality_texture, best_quality_violation
        if quality_check is None or expired():
            return
        passed, violation = quality_check(current.copy(), image.copy())
        if not isinstance(passed, (bool, np.bool_)) or not math.isfinite(violation) or violation < 0:
            raise ValueError("Development quality callback requires bool and finite nonnegative violation")
        late = expired()
        history.append({"event": "development_quality_snapshot", "iteration": iteration,
                        "callback_passed": bool(passed), "violation": violation,
                        "full_final_validation": False, "crossed_deadline": late})
        if not late and (best_quality_violation is None or violation < best_quality_violation):
            best_quality_state, best_quality_texture = current.copy(), image.copy()
            best_quality_violation = violation

    def result(status: str, initial_objective: float, proxy: _Proxy | None, reason: str) -> BudgetRestorationResult:
        return BudgetRestorationResult(
            status, current.copy(), image.copy(), initial_objective,
            math.inf if proxy is None else proxy.objective, math.inf if proxy is None else proxy.xi,
            history, max(0.0, float(clock()) - started),
            np.empty(0) if proxy is None else proxy.g.copy(), () if proxy is None else proxy.labels,
            metadata, best_quality_state=best_quality_state, best_quality_texture=best_quality_texture,
            best_quality_violation=best_quality_violation, best_xi=best_xi,
            best_xi_state=best_xi_state, best_xi_texture=best_xi_texture, reason=reason,
        )

    if expired():
        return result("TIMEOUT_WITH_BEST", math.inf, None, "Initial H-safe state retained; proxy not evaluated")
    features = _refresh_targets(current, samples, target_update)
    history.append({"event": "target_refresh", "iteration": 0, "location": "initial",
                    "updated": target_update is not None, "frozen_during_inner_solve": True})
    current_proxy = _evaluate_proxy(current, features, image, origin, origin_texture,
                                    reduction, orientations, config)
    initial_objective = current_proxy.objective
    full_texture_linearizations = 1
    full_uv_linearizations = 0
    value_evaluations = 0
    metadata["full_texture_linearizations"] = full_texture_linearizations
    best_xi, best_xi_state, best_xi_texture = current_proxy.xi, current.copy(), image.copy()
    total_h_rejections = 0
    metadata["material_approximation"] = current_proxy.material.metadata
    metadata["geometry_approximation"] = current_proxy.geometry.metadata
    history.append({"event": "initial_proxy", "xi": current_proxy.xi, "proxy_objective": initial_objective,
                    "maximum_normalized_constraint": float(np.max(current_proxy.g)), "trust_cost": 0.0,
                    "full_final_validation": False})
    quality_snapshot(0)
    status, reason = "ITERATION_LIMIT", "Outer iteration limit reached; no global convergence claim"
    for iteration in range(1, config.iterations + 1):
        if expired():
            status, reason = "TIMEOUT_WITH_BEST", "Deadline reached; best actual proxy state retained"
            break
        if not isinstance(current_proxy.material, ConstraintLinearization):
            material_rows = _material(current, features, image, config)
            geometry_rows = _geometry(current, orientations, image, config)
            assert isinstance(geometry_rows, ConstraintLinearization)
            rebuilt = np.concatenate((material_rows.g, geometry_rows.g))
            labels = material_rows.labels + geometry_rows.labels
            if (labels != current_proxy.labels
                    or not np.allclose(rebuilt, current_proxy.g, atol=1e-10, rtol=1e-12)):
                raise ValueError("Values-only and rebuilt full model must retain identical nonlinear rows")
            current_proxy = replace(current_proxy, material=material_rows, geometry=geometry_rows)
            full_texture_linearizations += 1
            metadata["full_texture_linearizations"] = full_texture_linearizations
        if expired():
            status, reason = "TIMEOUT_WITH_BEST", "Model rebuild exhausted deadline; actual best retained"
            break
        uv_rows = _material(current, features, image, config, uv=True)
        full_uv_linearizations += 1
        metadata["full_uv_linearizations"] = full_uv_linearizations
        material = current_proxy.material
        assert isinstance(material, ConstraintLinearization)
        assert isinstance(current_proxy.geometry, ConstraintLinearization)
        if uv_rows.labels != material.labels or not np.allclose(uv_rows.g, material.g, atol=1e-12, rtol=1e-12):
            raise ValueError("UV and texture frozen material rows must have identical constraint identities")
        indices = material.variable_indices
        if indices is None:
            raise ValueError("Compressed texture linearization must preserve original flat HWC indices")
        operator = JointRestorationOperator(uv_rows.operator, material.operator, current_proxy.geometry.operator,
                                            reduction)
        lower, upper, diagonal, offset, constant = _local_box(
            current, image, origin, origin_texture, reduction, indices, (channel_lo, channel_hi), config,
        )
        local = solve_epigraph_restoration(
            current_proxy.g, operator, lower, upper, trust_weight=config.trust_weight,
            trust_diagonal=diagonal, trust_offset=offset, max_iterations=config.local_iterations,
            absolute_tolerance=config.local_absolute_tolerance,
            relative_tolerance=config.local_relative_tolerance, deadline=stop, clock=clock,
        )
        event: dict[str, object] = {
            "event": "joint_epigraph_step", "iteration": iteration, "model_status": local.status,
            "model_xi": local.xi, "model_primal_objective": local.primal_objective + constant,
            "model_dual_objective": local.dual_objective + constant, "model_duality_gap": local.duality_gap,
            "model_iterations": local.iterations, "model_gap_scope": "FROZEN_CONVEX_MODEL_ONLY",
            "trust_center": "FIXED_INITIAL_Y0", "trust_offset_squared_norm": float(diagonal @ offset**2),
            "trust_omitted_constant": constant, "uv_variables": reduction.size,
            "active_texture_variables": len(indices), "texture_full_variables": image.size,
            "accepted": False, "accepted_eta": 0.0, "h_rejections": 0,
            "nonlinear_proxy_rejections": 0, "numerical_proxy_rejections": 0, "backtracks": 0,
            "xi": current_proxy.xi, "proxy_objective": current_proxy.objective,
            "maximum_normalized_constraint": float(np.max(current_proxy.g)),
            "full_final_validation": False,
        }
        history.append(event)
        if expired():
            status, reason = "TIMEOUT_WITH_BEST", "Frozen solve exhausted deadline; actual best retained"
            break
        if float(np.max(np.abs(local.step), initial=0.0)) <= config.step_tolerance:
            status, reason = "LOCAL_STATIONARY", "No useful frozen-model step; this does not prove infeasibility"
            break
        uv_direction = reduction.expand(local.step[:reduction.size])
        texture_direction = np.zeros(image.size, dtype=np.float64)
        texture_direction[indices] = local.step[reduction.size:]
        texture_direction = texture_direction.reshape(image.shape)
        eta = safe_uv_step(current, uv_direction)
        accepted = False
        h_rejections = 0
        nonlinear_rejections = 0
        numerical_rejections = 0
        for backtrack in range(config.maximum_backtracks):
            event["backtracks"] = backtrack
            if expired():
                status, reason = "TIMEOUT_WITH_BEST", "Backtracking exhausted deadline; actual best retained"
                break
            proposal = current.copy()
            # Scatter from one group coordinate to preserve *exact* tie identity.
            group_uv = current.uv[reduction.first] + eta * local.step[:reduction.size].reshape((-1, 2)) / (
                reduction.resolution)
            proposal.uv = group_uv[reduction.inverse].copy()
            proposal_image = image + eta * texture_direction
            in_box = (np.all(group_uv >= reduction.lower) and np.all(group_uv <= reduction.upper)
                      and np.all(proposal_image >= channel_lo) and np.all(proposal_image <= channel_hi))
            if not in_box or (legal_uv is not None and not legal_uv(proposal.copy())):
                h_rejections += 1
                total_h_rejections += 1
                event["h_rejections"] = h_rejections
                metadata["h_rejections"] = total_h_rejections
                eta *= 0.5
                continue
            if expired():
                event["trial_crossed_deadline"] = True
                status, reason = "TIMEOUT_WITH_BEST", "Hard gate crossed deadline; actual best retained"
                break
            proposal_features = _refresh_targets(proposal, samples, target_update)
            history.append({"event": "target_refresh", "iteration": iteration, "location": "trial",
                            "eta": eta, "updated": target_update is not None,
                            "frozen_during_inner_solve": True})
            value_evaluations += 1
            metadata["nonlinear_trial_value_evaluations"] = value_evaluations
            try:
                proposal_proxy = _evaluate_proxy(proposal, proposal_features, proposal_image, origin,
                                                 origin_texture, reduction, orientations, config,
                                                 derivatives=False)
            except (FloatingPointError, np.linalg.LinAlgError):
                numerical_rejections += 1
                event["numerical_proxy_rejections"] = numerical_rejections
                eta *= 0.5
                continue
            if expired():
                event["trial_crossed_deadline"] = True
                status, reason = "TIMEOUT_WITH_BEST", "Trial reevaluation crossed deadline; actual best retained"
                break
            event["last_trial_proxy_objective"] = proposal_proxy.objective
            if proposal_proxy.xi < best_xi:
                best_xi, best_xi_state, best_xi_texture = proposal_proxy.xi, proposal.copy(), proposal_image.copy()
            model_xi = max(0.0, float(np.max(current_proxy.g + operator.matvec(eta * local.step))))
            model_objective = model_xi + 0.5 * config.trust_weight * float(
                diagonal @ (eta * local.step + offset)**2) + constant
            predicted_improvement = max(0.0, current_proxy.objective - model_objective)
            required = max(config.minimum_improvement * max(1.0, abs(current_proxy.objective)),
                           config.armijo * predicted_improvement)
            if (proposal_proxy.objective < current_proxy.objective
                    and proposal_proxy.objective <= current_proxy.objective - required):
                current, image, features, current_proxy = proposal, proposal_image, proposal_features, proposal_proxy
                accepted = True
                event.update({"accepted": True, "accepted_eta": eta, "backtracks": backtrack,
                              "xi": current_proxy.xi, "proxy_objective": current_proxy.objective,
                              "trust_cost": current_proxy.trust,
                              "maximum_normalized_constraint": float(np.max(current_proxy.g)),
                              "model_xi_at_accepted_eta": model_xi,
                              "predicted_improvement_at_eta": predicted_improvement})
                break
            nonlinear_rejections += 1
            event["nonlinear_proxy_rejections"] = nonlinear_rejections
            eta *= 0.5
        if status == "TIMEOUT_WITH_BEST":
            break
        if not accepted:
            status = "MODEL_NUMERICAL_FAILURE" if local.status == "numerical_failure" else "LOCAL_STALLED"
            reason = "No nonlinear-proxy descent passed H and backtracking; no infeasibility claim"
            break
        if iteration % config.quality_check_interval == 0:
            quality_snapshot(iteration)
    if expired():
        status, reason = "TIMEOUT_WITH_BEST", "Deadline reached; best actual proxy state retained"
    metadata["nonlinear_proxy_satisfied"] = bool(np.max(current_proxy.g) <= config.local_absolute_tolerance)
    metadata["proxy_evaluated"] = True
    return result(status, initial_objective, current_proxy, reason)
