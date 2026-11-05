"""Exportable piecewise-affine UV and texture block optimization.

The continuous objective is a differentiable, frozen-target surrogate. Encoded
material quality is deliberately delegated to a separate callback. In particular,
normal targets can be refreshed between blocks, but their frame is frozen while
computing each block gradient. No optimizer status is a material certificate.
"""

import math
import time
from collections.abc import Callable
from dataclasses import dataclass
from functools import lru_cache

import numpy as np
from numpy.typing import NDArray

from .budget_pair import first_positive_determinant_root
from .types import FloatArray, IntArray, MeshState

LegalUV = Callable[[MeshState], bool]
TargetUpdate = Callable[[MeshState], "FeatureSamples"]
QualityCheck = Callable[[MeshState, FloatArray], tuple[bool, float]]
UVObjective = Callable[[MeshState], tuple[float, FloatArray]]
BoolArray = NDArray[np.bool_]


@dataclass(frozen=True)
class FeatureSamples:
    """Fixed original-face probes with linear, possibly signed, channel targets."""

    face_ids: IntArray
    barycentric: FloatArray
    values: FloatArray
    weights: FloatArray | None = None
    mip_levels: IntArray | None = None
    channel_weights: FloatArray | None = None

    def __post_init__(self) -> None:
        faces = np.asarray(self.face_ids, dtype=np.int64).copy()
        bary = np.asarray(self.barycentric, dtype=np.float64).copy()
        values = np.asarray(self.values, dtype=np.float64).copy()
        count = len(faces)
        weights = (
            np.ones(count, dtype=np.float64)
            if self.weights is None
            else np.asarray(self.weights, dtype=np.float64).copy()
        )
        levels = (
            np.zeros(count, dtype=np.int64)
            if self.mip_levels is None
            else np.asarray(self.mip_levels, dtype=np.int64).copy()
        )
        channels = (
            np.ones(values.shape[1], dtype=np.float64)
            if self.channel_weights is None and values.ndim == 2
            else np.asarray(self.channel_weights, dtype=np.float64).copy()
        )
        if (
            faces.ndim != 1
            or count == 0
            or np.any(faces < 0)
            or bary.shape != (count, 3)
            or values.ndim != 2
            or values.shape[0] != count
            or values.shape[1] == 0
            or weights.shape != (count,)
            or levels.shape != (count,)
            or channels.shape != (values.shape[1],)
            or not np.isfinite(bary).all()
            or not np.isfinite(values).all()
            or not np.isfinite(weights).all()
            or not np.isfinite(channels).all()
            or np.any(bary < -1e-10)
            or not np.allclose(bary.sum(axis=1), 1, atol=1e-9, rtol=0)
            or np.any(weights < 0)
            or float(weights.sum()) <= 0
            or np.any(levels < 0)
            or np.any(channels < 0)
            or not np.any(channels > 0)
        ):
            raise ValueError("Invalid fixed original-face feature samples")
        weights /= weights.sum()
        for name, value in (
            ("face_ids", faces),
            ("barycentric", bary),
            ("values", values),
            ("weights", weights),
            ("mip_levels", levels),
            ("channel_weights", channels),
        ):
            value.setflags(write=False)
            object.__setattr__(self, name, value)


@lru_cache(maxsize=64)
def _box_stencil(size: int) -> tuple[IntArray, FloatArray]:
    """Separable area BOX weights for floor-halved, including odd, dimensions."""
    output = max(1, size // 2)
    starts = np.arange(output, dtype=float) * size / output
    ends = np.arange(1, output + 1, dtype=float) * size / output
    indices = np.floor(starts).astype(np.int64)[:, None] + np.arange(3, dtype=np.int64)
    weights = np.maximum(
        0,
        np.minimum(indices + 1, ends[:, None]) - np.maximum(indices, starts[:, None]),
    ) / (size / output)
    indices = np.clip(indices, 0, size - 1)
    indices.setflags(write=False)
    weights.setflags(write=False)
    return indices, weights


def _box_axis(image: FloatArray, axis: int) -> FloatArray:
    indices, weights = _box_stencil(image.shape[axis])
    moved = np.moveaxis(image, axis, 0)
    weight_shape = weights.shape + (1,) * (moved.ndim - 1)
    result = (moved[indices] * weights.reshape(weight_shape)).sum(axis=1)
    return np.asarray(np.moveaxis(result, 0, axis), dtype=np.float64)


def _box_axis_adjoint(gradient: FloatArray, size: int, axis: int) -> FloatArray:
    indices, weights = _box_stencil(size)
    moved = np.moveaxis(gradient, axis, 0)
    output = np.zeros((size,) + moved.shape[1:], dtype=np.float64)
    for tap in range(3):
        w = weights[:, tap].reshape((len(weights),) + (1,) * (moved.ndim - 1))
        np.add.at(output, indices[:, tap], moved * w)
    return np.asarray(np.moveaxis(output, 0, axis), dtype=np.float64)


def linear_mip_chain(texture: FloatArray, maximum_level: int | None = None) -> tuple[FloatArray, ...]:
    """Unquantized linear BOX proxy, never an encoded-mip quality certificate."""
    image = np.asarray(texture, dtype=np.float64)
    if image.ndim != 3 or min(image.shape) < 1 or not np.isfinite(image).all():
        raise ValueError("Finite nonempty HWC texture required")
    available = int(math.log2(max(image.shape[:2])))
    count = available if maximum_level is None else maximum_level
    if type(count) is not int or count < 0 or count > available:
        raise ValueError("Requested mip level unavailable")
    levels = [image]
    for _ in range(count):
        levels.append(_box_axis(_box_axis(levels[-1], 1), 0))
    return tuple(levels)


def _mip_adjoint(gradients: list[FloatArray], levels: tuple[FloatArray, ...]) -> FloatArray:
    total = [g.copy() for g in gradients]
    for level in range(len(total) - 1, 0, -1):
        height, width = levels[level - 1].shape[:2]
        total[level - 1] += _box_axis_adjoint(_box_axis_adjoint(total[level], height, 0), width, 1)
    return total[0]


def _bilinear_stencil(
    uv: FloatArray, height: int, width: int
) -> tuple[IntArray, IntArray, FloatArray, FloatArray, FloatArray]:
    raw_x, raw_y = uv[:, 0] * width - 0.5, (1 - uv[:, 1]) * height - 0.5
    x, y = np.clip(raw_x, 0, width - 1), np.clip(raw_y, 0, height - 1)
    x0, y0 = np.floor(x).astype(np.int64), np.floor(y).astype(np.int64)
    x1, y1 = np.minimum(x0 + 1, width - 1), np.minimum(y0 + 1, height - 1)
    tx, ty = x - x0, y - y0
    columns = np.stack((x0, x1, x0, x1), axis=1)
    rows = np.stack((y0, y0, y1, y1), axis=1)
    weights = np.stack(((1 - tx) * (1 - ty), tx * (1 - ty), (1 - tx) * ty, tx * ty), axis=1)
    dx = np.stack((-(1 - ty), 1 - ty, -ty, ty), axis=1) * width
    dy = np.stack((-(1 - tx), -tx, 1 - tx, tx), axis=1) * -height
    dx *= ((raw_x > 0) & (raw_x < width - 1))[:, None]
    dy *= ((raw_y > 0) & (raw_y < height - 1))[:, None]
    return rows, columns, weights, dx, dy


def _objective_gradient(
    state: MeshState, samples: FeatureSamples, texture: FloatArray
) -> tuple[float, FloatArray, FloatArray, FloatArray]:
    if int(samples.face_ids.max()) >= len(state.mesh.faces):
        raise ValueError("Feature sample references missing original face")
    if texture.ndim != 3 or texture.shape[2] != samples.values.shape[1]:
        raise ValueError("Texture channel count differs from feature samples")
    assert samples.mip_levels is not None
    assert samples.weights is not None
    assert samples.channel_weights is not None
    levels = linear_mip_chain(texture, int(samples.mip_levels.max()))
    gradients = [np.zeros_like(image) for image in levels]
    majorizers = [np.zeros_like(image) for image in levels]
    uv_gradient = np.zeros_like(state.uv)
    energy = 0.0
    uv_corners = state.uv_faces[samples.face_ids]
    uv = np.einsum("nc,ncd->nd", samples.barycentric, state.uv[uv_corners])
    for level, image in enumerate(levels):
        ids = np.flatnonzero(samples.mip_levels == level)
        if not len(ids):
            continue
        rows, columns, weights, dx, dy = _bilinear_stencil(uv[ids], *image.shape[:2])
        texels = image[rows, columns]
        predicted = np.einsum("nk,nkc->nc", weights, texels)
        residual = predicted - samples.values[ids]
        coefficients = samples.weights[ids, None] * samples.channel_weights[None]
        energy += float(np.sum(coefficients * residual**2))
        derivative = 2 * coefficients * residual
        for tap in range(4):
            np.add.at(gradients[level], (rows[:, tap], columns[:, tap]), weights[:, tap, None] * derivative)
            np.add.at(
                majorizers[level],
                (rows[:, tap], columns[:, tap]),
                weights[:, tap, None] * 2 * coefficients,
            )
        gradient_u = np.sum(derivative * np.einsum("nk,nkc->nc", dx, texels), axis=1)
        gradient_v = np.sum(derivative * np.einsum("nk,nkc->nc", dy, texels), axis=1)
        sample_gradient = np.column_stack((gradient_u, gradient_v))
        for corner in range(3):
            np.add.at(
                uv_gradient,
                uv_corners[ids, corner],
                samples.barycentric[ids, corner, None] * sample_gradient,
            )
    # Positive, row-stochastic interpolation gives a diagonal Hessian majorizer:
    # diag(2 A.T W 1) - 2 A.T W A is positive semidefinite.
    return energy, uv_gradient, _mip_adjoint(gradients, levels), _mip_adjoint(majorizers, levels)


def feature_loss_gradient(
    state: MeshState, samples: FeatureSamples, texture: FloatArray
) -> tuple[float, FloatArray, FloatArray]:
    """L2 proxy and exact fixed-target UV/texture gradients, including mip adjoint."""
    energy, uv_gradient, texture_gradient, _ = _objective_gradient(state, samples, texture)
    return energy, uv_gradient, texture_gradient


def _inverse_rest(state: MeshState) -> FloatArray:
    triangles = state.mesh.vertices[state.mesh.faces] / state.mesh.scale
    edge0, edge1 = triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0]
    lengths = np.linalg.norm(edge0, axis=1)
    rest = np.zeros((len(triangles), 2, 2))
    rest[:, 0, 0] = lengths
    rest[:, 0, 1] = np.sum(edge0 * edge1, axis=1) / lengths
    rest[:, 1, 1] = np.linalg.norm(np.cross(edge0, edge1), axis=1) / lengths
    return np.asarray(np.linalg.inv(rest), dtype=np.float64)


def _edge_matrices(state: MeshState, uv: FloatArray) -> FloatArray:
    triangles = uv[state.uv_faces]
    return np.stack((triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0]), axis=2)


def _shape_gradient(
    state: MeshState, inverse_rest: FloatArray, orientations: FloatArray
) -> tuple[float, FloatArray]:
    jacobian = _edge_matrices(state, state.uv) @ inverse_rest
    jacobian[:, 0] *= orientations[:, None]
    determinant = np.linalg.det(jacobian)
    if np.any(determinant <= 0):
        raise ValueError("Shape gradient requires the initial face orientations")
    scale = np.sqrt(determinant)
    normalized = jacobian / scale[:, None, None]
    inverse = np.linalg.inv(normalized)
    inverse_t = inverse.swapaxes(1, 2)
    weights = state.mesh.areas / state.mesh.areas.sum()
    density = 0.25 * (np.sum(normalized**2, axis=(1, 2)) + np.sum(inverse**2, axis=(1, 2))) - 1
    derivative = 0.5 * (normalized - inverse_t @ inverse @ inverse_t)
    contraction = np.sum(derivative * normalized, axis=(1, 2))
    gradient_jacobian = derivative / scale[:, None, None]
    gradient_jacobian -= 0.5 * contraction[:, None, None] * np.linalg.inv(jacobian).swapaxes(1, 2)
    gradient_jacobian *= weights[:, None, None]
    gradient_jacobian[:, 0] *= orientations[:, None]
    edges_gradient = gradient_jacobian @ inverse_rest.swapaxes(1, 2)
    gradient = np.zeros_like(state.uv)
    for corner, values in (
        (1, edges_gradient[:, :, 0]),
        (2, edges_gradient[:, :, 1]),
        (0, -edges_gradient.sum(axis=2)),
    ):
        np.add.at(gradient, state.uv_faces[:, corner], values)
    return float(weights @ density), gradient


def safe_uv_step(state: MeshState, direction: FloatArray, maximum: float = 1.0) -> float:
    """Guard the UV-space line segment against its first zero-area face."""
    if direction.shape != state.uv.shape or not np.isfinite(direction).all():
        raise ValueError("Finite direction for each UV copy required")
    if not math.isfinite(maximum) or maximum < 0:
        raise ValueError("Nonnegative finite maximum step required")
    matrices, delta = _edge_matrices(state, state.uv), _edge_matrices(state, direction)
    orientations = np.sign(np.linalg.det(matrices))
    if np.any(orientations == 0):
        raise ValueError("Cannot guard a degenerate initial state")
    matrices[:, 0] *= orientations[:, None]
    delta[:, 0] *= orientations[:, None]
    root = min(
        (first_positive_determinant_root(a, b) for a, b in zip(matrices, delta, strict=True)),
        default=math.inf,
    )
    return min(maximum, 0.95 * root)


def _project_box_gradient(
    uv: FloatArray, gradient: FloatArray, lower: FloatArray, upper: FloatArray
) -> FloatArray:
    """Project the negative-gradient direction into the box's feasible cone."""
    result = gradient.copy()
    tolerance = 64 * np.finfo(float).eps
    result[(uv <= lower + tolerance) & (gradient > 0)] = 0
    result[(uv >= upper - tolerance) & (gradient < 0)] = 0
    return result


def _first_box_step(
    uv: FloatArray, direction: FloatArray, lower: FloatArray, upper: FloatArray, maximum: float
) -> float:
    """Stop the UV-space segment at its first box boundary, rounding inward."""
    positive, negative = direction > 0, direction < 0
    roots = np.concatenate((
        (upper[positive] - uv[positive]) / direction[positive],
        (lower[negative] - uv[negative]) / direction[negative],
    ))
    root = float(roots.min()) if len(roots) else math.inf
    if root <= maximum:
        return max(0.0, float(np.nextafter(root, 0.0)))
    return maximum


@dataclass
class OptimizationResult:
    status: str
    state: MeshState
    texture: FloatArray
    history: list[dict[str, object]]
    wall_seconds: float
    initial_proxy: float
    final_proxy: float
    quality_valid: bool
    best_valid_state: MeshState | None = None
    best_valid_texture: FloatArray | None = None
    best_quality_state: MeshState | None = None
    best_quality_texture: FloatArray | None = None
    best_quality_violation: float | None = None
    reason: str | None = None


def optimize_uv_texture(
    initial: MeshState,
    samples: FeatureSamples,
    initial_texture: FloatArray,
    *,
    seconds: float,
    iterations: int = 60,
    optimize_uv: bool = True,
    active_vertices: BoolArray | None = None,
    fixed_vertices: BoolArray | None = None,
    legal_uv: LegalUV | None = None,
    target_update: TargetUpdate | None = None,
    quality_check: QualityCheck | None = None,
    quality_check_interval: int = 10,
    texture_steps: int = 3,
    uv_step_pixels: float = 0.35,
    uv_regularization: float = 1e-4,
    shape_weight: float = 0.01,
    channel_bounds: tuple[FloatArray, FloatArray] | None = None,
    uv_objective: UVObjective | None = None,
    maximum_displacement: float | None = None,
    uv_bounds: tuple[FloatArray, FloatArray] | None = None,
    motion_ties: IntArray | None = None,
    deadline: float | None = None,
) -> OptimizationResult:
    """Alternate bounded texture updates and guarded, exportable UV updates.

    ``quality_check`` must evaluate real encoded material and return validity plus
    a nonnegative violation. Without it, ``quality_valid`` is always false. All
    archival states own deep copies. A decreasing proxy does not imply validity.
    ``legal_uv`` is the caller's full topology/overlap/geometry gate; local tests
    here protect orientation and atlas bounds but do not prove global injectivity.
    Optional ``uv_bounds`` freeze a per-UV-copy axis-aligned owner box. Outward
    gradient coordinates at an active bound are projected away, while all steps
    retain the same full geometry gate. Equal bounds freeze that coordinate.
    Optional ``motion_ties`` supply nonnegative integer labels for explicitly
    authorized equal-displacement groups. Initial positions must match exactly;
    no groups are inferred from proximity. UV-copy identities remain distinct.
    Any inactive/fixed group member locks the group; bounds use their intersection.
    """
    started = time.monotonic()
    if (
        not math.isfinite(seconds)
        or seconds < 0
        or type(iterations) is not int
        or iterations < 0
        or type(texture_steps) is not int
        or texture_steps < 0
        or type(quality_check_interval) is not int
        or quality_check_interval < 1
        or not all(math.isfinite(v) and v >= 0 for v in (uv_regularization, shape_weight))
        or not math.isfinite(uv_step_pixels)
        or uv_step_pixels <= 0
        or (deadline is not None and not math.isfinite(deadline))
        or (maximum_displacement is not None and (
            not math.isfinite(maximum_displacement) or maximum_displacement <= 0
        ))
    ):
        raise ValueError("Invalid continuous optimization protocol")
    stop = min(started + seconds, math.inf if deadline is None else deadline)
    current, texture = initial.copy(), np.asarray(initial_texture, dtype=np.float64).copy()
    if texture.ndim != 3 or not np.isfinite(texture).all() or min(texture.shape) < 1:
        raise ValueError("Finite nonempty HWC initial texture required")
    channels = texture.shape[2]
    lower, upper = (np.zeros(channels), np.ones(channels)) if channel_bounds is None else channel_bounds
    lower, upper = np.asarray(lower, dtype=float), np.asarray(upper, dtype=float)
    if (
        lower.shape != (channels,)
        or upper.shape != (channels,)
        or not np.isfinite(lower).all()
        or not np.isfinite(upper).all()
        or np.any(lower >= upper)
        or np.any(texture < lower)
        or np.any(texture > upper)
    ):
        raise ValueError("Initial texture and channel bounds are inconsistent")
    active = np.ones(len(initial.uv), dtype=bool)
    if active_vertices is not None:
        candidate_active = np.asarray(active_vertices, dtype=bool)
        if candidate_active.shape != active.shape:
            raise ValueError("One activity flag required per UV copy")
        active &= candidate_active
    if fixed_vertices is not None:
        fixed = np.asarray(fixed_vertices, dtype=bool)
        if fixed.shape != active.shape:
            raise ValueError("One fixed flag required per UV copy")
        active &= ~fixed
    if not optimize_uv:
        active[:] = False
    box_lower, box_upper = (
        (np.zeros_like(initial.uv), np.ones_like(initial.uv)) if uv_bounds is None else uv_bounds
    )
    box_lower, box_upper = np.asarray(box_lower, dtype=float), np.asarray(box_upper, dtype=float)
    if (
        box_lower.shape != initial.uv.shape
        or box_upper.shape != initial.uv.shape
        or not np.isfinite(box_lower).all()
        or not np.isfinite(box_upper).all()
        or np.any(box_lower < 0)
        or np.any(box_upper > 1)
        or np.any(box_lower > box_upper)
    ):
        raise ValueError("UV owner bounds must contain the initial state within [0, 1]")
    labels = np.arange(len(initial.uv), dtype=np.int64) if motion_ties is None else np.asarray(motion_ties)
    if (
        labels.shape != (len(initial.uv),)
        or not np.issubdtype(labels.dtype, np.integer)
        or np.any(labels < 0)
    ):
        raise ValueError("motion_ties requires one nonnegative integer label per UV copy")
    _, group_first, inverse_groups = np.unique(labels, return_index=True, return_inverse=True)
    group_first = np.asarray(group_first, dtype=np.int64)
    inverse_groups = np.asarray(inverse_groups, dtype=np.int64)
    group_counts = np.bincount(inverse_groups)
    if motion_ties is not None and not np.array_equal(initial.uv, initial.uv[group_first][inverse_groups]):
        raise ValueError("Motion tie initial UV positions must be exactly equal")
    group_lower = np.full((len(group_counts), 2), -np.inf)
    group_upper = np.full((len(group_counts), 2), np.inf)
    np.maximum.at(group_lower, inverse_groups, box_lower)
    np.minimum.at(group_upper, inverse_groups, box_upper)
    if np.any(group_lower > group_upper):
        raise ValueError("INCOMPATIBLE_MOTION_TIE_BOUNDS: empty group bound intersection")
    box_lower, box_upper = group_lower[inverse_groups], group_upper[inverse_groups]
    if np.any(initial.uv < box_lower) or np.any(initial.uv > box_upper):
        raise ValueError("UV owner bounds must contain the initial state within [0, 1]")
    group_active = np.ones(len(group_counts), dtype=bool)
    np.logical_and.at(group_active, inverse_groups, active)
    active = group_active[inverse_groups]
    tied_groups = group_counts > 1
    motion_summary: dict[str, object] = {
        "motion_tie_groups": int(np.count_nonzero(tied_groups)),
        "motion_tied_copies": int(group_counts[tied_groups].sum()),
        "motion_locked_tie_groups": int(np.count_nonzero(tied_groups & ~group_active)),
        "motion_groups_total": len(group_counts),
    }
    rest = _inverse_rest(initial)
    initial_determinants = np.linalg.det(_edge_matrices(initial, initial.uv))
    orientations = np.sign(initial_determinants)
    if np.any(orientations == 0) or not np.isfinite(initial_determinants).all():
        raise ValueError("Nondegenerate finite initial UV faces required")
    determinant_floor = np.abs(initial_determinants) * 1e-10
    if np.any(initial.uv < 0) or np.any(initial.uv > 1):
        raise ValueError("Initial atlas UV must be within [0, 1]")
    if legal_uv is not None and not legal_uv(current.copy()):
        raise ValueError("INIT_FAILED: initial candidate rejected by geometry gate")
    history: list[dict[str, object]] = []

    def energy_gradient(candidate: MeshState, feature: FeatureSamples, image: FloatArray) -> tuple[
        float, FloatArray, FloatArray, FloatArray
    ]:
        loss, guv, gtexture, majorizer = _objective_gradient(candidate, feature, image)
        if shape_weight:
            shape, shape_gradient = _shape_gradient(candidate, rest, orientations)
            loss += shape_weight * shape
            guv += shape_weight * shape_gradient
        if uv_regularization:
            delta = candidate.uv - initial.uv
            loss += uv_regularization * float(np.sum(delta**2)) / len(delta)
            guv += 2 * uv_regularization * delta / len(delta)
        if uv_objective is not None:
            extra, derivative = uv_objective(candidate.copy())
            if derivative.shape != guv.shape or not math.isfinite(extra) or not np.isfinite(derivative).all():
                raise ValueError("Invalid caller UV objective")
            loss += extra
            guv += derivative
        if not math.isfinite(loss) or not np.isfinite(guv).all() or not np.isfinite(gtexture).all():
            raise FloatingPointError("Nonfinite continuous objective or gradient")
        return loss, guv, gtexture, majorizer

    def refresh_targets(candidate: MeshState, previous: FeatureSamples) -> FeatureSamples:
        if target_update is None:
            return previous
        refreshed = target_update(candidate.copy())
        assert previous.mip_levels is not None and refreshed.mip_levels is not None
        assert previous.weights is not None and refreshed.weights is not None
        assert previous.channel_weights is not None and refreshed.channel_weights is not None
        if not (
            np.array_equal(refreshed.face_ids, previous.face_ids)
            and np.array_equal(refreshed.barycentric, previous.barycentric)
            and np.array_equal(refreshed.mip_levels, previous.mip_levels)
            and np.allclose(refreshed.weights, previous.weights, atol=1e-14, rtol=1e-12)
            and np.allclose(refreshed.channel_weights, previous.channel_weights, atol=1e-14, rtol=1e-12)
            and refreshed.values.shape == previous.values.shape
        ):
            raise ValueError("Target refresh cannot change fixed probe identities, channels, or weights")
        return refreshed

    samples = refresh_targets(current, samples)
    initial_energy, _, _, _ = energy_gradient(current, samples, texture)
    best_energy = initial_energy
    best_state, best_texture = current.copy(), texture.copy()
    best_valid_state: MeshState | None = None
    best_valid_texture: FloatArray | None = None
    best_quality_state: MeshState | None = None
    best_quality_texture: FloatArray | None = None
    best_quality_violation: float | None = None
    valid_proxy = math.inf

    def check_quality(candidate: MeshState, image: FloatArray, iteration: int, proxy: float) -> None:
        nonlocal best_valid_state, best_valid_texture, best_quality_state, best_quality_texture
        nonlocal best_quality_violation, valid_proxy
        if quality_check is None or time.monotonic() >= stop:
            return
        valid, violation = quality_check(candidate.copy(), image.copy())
        if not isinstance(valid, (bool, np.bool_)) or not math.isfinite(violation) or violation < 0:
            raise ValueError("Quality callback requires validity and nonnegative finite violation")
        late = time.monotonic() >= stop
        history.append({
            "event": "encoded_quality_check",
            "iteration": iteration,
            "valid": bool(valid),
            "violation": violation,
            "crossed_deadline": late,
        })
        if late:
            return
        if best_quality_violation is None or violation < best_quality_violation:
            best_quality_violation = violation
            best_quality_state, best_quality_texture = candidate.copy(), image.copy()
        if valid and proxy < valid_proxy:
            valid_proxy = proxy
            best_valid_state, best_valid_texture = candidate.copy(), image.copy()

    check_quality(current, texture, -1, initial_energy)
    if motion_ties is not None:
        history.append({"event": "motion_tie_constraints", **motion_summary})
    status = "ITERATION_LIMIT"
    reason: str | None = None
    for iteration in range(iterations):
        if time.monotonic() >= stop:
            status = "TIME_BUDGET_EXHAUSTED"
            break
        try:
            # Rebuild targets (e.g. normal TBN) between blocks, then freeze them.
            samples = refresh_targets(current, samples)
            before, gradient_uv, gradient_texture, majorizer = energy_gradient(current, samples, texture)
            accepted_texture = 0
            for _ in range(texture_steps):
                if time.monotonic() >= stop:
                    break
                direction_texture = -gradient_texture / np.maximum(majorizer, 1e-30)
                step = 1.0
                for _ in range(15):
                    proposed_texture = np.clip(texture + step * direction_texture, lower, upper)
                    trial_loss, trial_guv, trial_gt, trial_majorizer = energy_gradient(
                        current, samples, proposed_texture
                    )
                    if trial_loss <= before + 1e-14:
                        texture = proposed_texture
                        before, gradient_uv = trial_loss, trial_guv
                        gradient_texture, majorizer = trial_gt, trial_majorizer
                        accepted_texture += int(np.any(np.abs(step * direction_texture) > 1e-14))
                        break
                    step *= 0.5
                    if time.monotonic() >= stop:
                        break
            accepted_step, backtracks = 0.0, 0
            gradient_uv[~active] = 0
            raw_gradient_uv = gradient_uv.copy()
            group_gradient = np.zeros((len(group_counts), 2))
            np.add.at(group_gradient, inverse_groups, gradient_uv)
            group_gradient /= group_counts[:, None]
            tied_gradient = group_gradient[inverse_groups]
            group_gradient = _project_box_gradient(
                current.uv[group_first], group_gradient, group_lower, group_upper
            )
            gradient_uv = group_gradient[inverse_groups]
            blocked_coordinates = int(np.count_nonzero(tied_gradient != gradient_uv))
            motion_projected_coordinates = int(np.count_nonzero(raw_gradient_uv != tied_gradient))
            norm = float(np.linalg.norm(gradient_uv))
            if active.any() and norm > 1e-12 and time.monotonic() < stop:
                direction_uv = -gradient_uv
                maximum_norm = float(np.linalg.norm(direction_uv, axis=1).max())
                maximum_step = min(1.0, uv_step_pixels / max(texture.shape[:2]) / maximum_norm)
                maximum_step = _first_box_step(
                    current.uv, direction_uv, box_lower, box_upper, maximum_step
                )
                step = safe_uv_step(current, direction_uv, maximum_step)
                slope = float(np.sum(gradient_uv * direction_uv))
                for backtracks in range(25):
                    if time.monotonic() >= stop:
                        break
                    proposed = current.copy()
                    proposed.uv += step * direction_uv
                    determinant = np.linalg.det(_edge_matrices(proposed, proposed.uv)) * orientations
                    locally_legal = bool(
                        np.all(proposed.uv >= box_lower)
                        and np.all(proposed.uv <= box_upper)
                        and np.all(determinant > determinant_floor)
                        and (motion_ties is None or np.array_equal(
                            proposed.uv, proposed.uv[group_first][inverse_groups]
                        ))
                        and (maximum_displacement is None or np.all(
                            np.linalg.norm(proposed.uv - initial.uv, axis=1) <= maximum_displacement
                        ))
                    )
                    if locally_legal:
                        trial_loss, _, _, _ = energy_gradient(proposed, samples, texture)
                        if trial_loss <= before + 1e-4 * step * slope and (
                            legal_uv is None or legal_uv(proposed.copy())
                        ):
                            if time.monotonic() < stop:
                                current, before, accepted_step = proposed, trial_loss, step
                            break
                    step *= 0.5
            # A refreshed normal-frame objective can differ from the frozen block.
            canonical_samples = refresh_targets(current, samples)
            canonical, _, _, _ = energy_gradient(current, canonical_samples, texture)
            if canonical < best_energy and time.monotonic() < stop:
                best_energy = canonical
                best_state, best_texture = current.copy(), texture.copy()
            history.append({
                "event": "block_update",
                "iteration": iteration,
                "frozen_target_proxy": before,
                "refreshed_target_proxy": canonical,
                "best_proxy": best_energy,
                "texture_updates": accepted_texture,
                "uv_step": accepted_step,
                "uv_backtracks": backtracks,
                "uv_gradient_norm": norm,
                "uv_box_projected_coordinates": blocked_coordinates,
                "uv_owner_bounds_supplied": uv_bounds is not None,
                "motion_projected_coordinates": motion_projected_coordinates,
                **motion_summary,
                "normal_frame_derivative": "frozen_target_block",
                "material_validated_by_proxy": False,
            })
            if (iteration + 1) % quality_check_interval == 0:
                check_quality(current, texture, iteration, canonical)
            if not active.any() and abs(canonical - best_energy) <= 1e-14 and accepted_texture == 0:
                status = "PROXY_STATIONARY"
                break
        except (FloatingPointError, np.linalg.LinAlgError) as error:
            status, reason = "NUMERICAL_FAILURE_WITH_ARCHIVE", str(error)
            break
    if time.monotonic() >= stop:
        status = "TIME_BUDGET_EXHAUSTED"
    check_quality(best_state, best_texture, iterations, best_energy)
    return OptimizationResult(
        status,
        best_state.copy(),
        best_texture.copy(),
        history,
        time.monotonic() - started,
        initial_energy,
        best_energy,
        best_valid_state is not None,
        None if best_valid_state is None else best_valid_state.copy(),
        None if best_valid_texture is None else best_valid_texture.copy(),
        None if best_quality_state is None else best_quality_state.copy(),
        None if best_quality_texture is None else best_quality_texture.copy(),
        best_quality_violation,
        reason,
    )
