"""Dimensionless, frozen constraint linearizations for Eq. (19) restoration.

These rows are local proxies, not decoded material or geometry certificates.
Every material row belongs to one original face and one requested mip. The
sample-feature derivative is sparse; the image/UV Jacobian and its transpose
are applied without constructing a constraint-by-texture matrix.
"""

from dataclasses import dataclass
from typing import Literal, Protocol

import numpy as np
from scipy.sparse import csr_matrix, eye, kron

from .budget_material import (
    MaterialSamples,
    MaterialThresholds,
    frame_arrays,
    interpolated_frames,
    shade_material,
)
from .budget_optimize import (
    FeatureSamples,
    _bilinear_stencil,
    _box_stencil,
    _edge_matrices,
    _inverse_rest,
    _mip_adjoint,
    linear_mip_chain,
)
from .types import FloatArray, IntArray, MeshState

Block = Literal["texture", "uv"]


class ConstraintOperator(Protocol):
    """Minimal matrix-free interface accepted by a restoration subproblem."""

    @property
    def shape(self) -> tuple[int, int]: ...

    def matvec(self, direction: FloatArray) -> FloatArray: ...

    def rmatvec(self, multipliers: FloatArray) -> FloatArray: ...


@dataclass(frozen=True)
class ConstraintValues:
    """Nonlinear proxy row values without constructing a local derivative.

    This is deliberately a different type from ``ConstraintLinearization``:
    a values-only trial cannot accidentally supply a missing/zero Jacobian.
    """

    g: FloatArray
    labels: tuple[str, ...]
    metadata: dict[str, object]


@dataclass(frozen=True)
class ConstraintLinearization:
    """Rows in one original variable identity, optionally texture-compressed.

    ``variable_shape`` always describes the full original U or HWC texture.
    When ``variable_indices`` is present, operator columns are exactly those
    full flattened entries. Extract with ``full.ravel()[variable_indices]``;
    scatter a delta back to the same indices, leaving every other entry zero.
    """

    g: FloatArray
    operator: ConstraintOperator
    labels: tuple[str, ...]
    metadata: dict[str, object]
    variable_indices: IntArray | None = None
    variable_shape: tuple[int, ...] | None = None

    @property
    def g0(self) -> FloatArray:
        """Alias for the base-point inequality values."""
        return self.g


@dataclass(frozen=True)
class _SampleStencil:
    ids: IntArray
    rows: IntArray
    columns: IntArray
    weights: FloatArray


class FrozenFeatureJacobian:
    """Exact J/J^T of linear BOX+mip and bilinear sampling at frozen U or T.

    UV derivatives freeze mip levels, texture texel cells and addressing. The
    full original-face corner map is used; there is no nearest-neighbor map.
    """

    def __init__(
        self, state: MeshState, samples: FeatureSamples, texture: FloatArray,
        block: Block = "texture",
    ) -> None:
        if block not in ("texture", "uv"):
            raise ValueError("Constraint block must be texture or uv")
        if int(samples.face_ids.max()) >= len(state.mesh.faces):
            raise ValueError("Feature sample references missing original face")
        if texture.ndim != 3 or texture.shape[2] != samples.values.shape[1]:
            raise ValueError("Texture channel count differs from feature samples")
        assert samples.mip_levels is not None
        self.block = block
        self.variable_shape = texture.shape if block == "texture" else state.uv.shape
        self.sample_shape = samples.values.shape
        self.uv_corners = state.uv_faces[samples.face_ids].copy()
        self.barycentric = samples.barycentric.copy()
        self.levels = linear_mip_chain(texture.copy(), int(samples.mip_levels.max()))
        self.stencils: list[_SampleStencil] = []
        self.values = np.zeros(self.sample_shape, dtype=np.float64)
        self.uv_derivative = np.zeros(self.sample_shape + (2,), dtype=np.float64)
        uv = np.einsum("nc,ncd->nd", self.barycentric, state.uv[self.uv_corners])
        for level, image in enumerate(self.levels):
            ids = np.flatnonzero(samples.mip_levels == level)
            if not len(ids):
                continue
            rows, columns, weights, dx, dy = _bilinear_stencil(uv[ids], *image.shape[:2])
            texels = image[rows, columns]
            self.values[ids] = np.einsum("nk,nkc->nc", weights, texels)
            self.uv_derivative[ids, :, 0] = np.einsum("nk,nkc->nc", dx, texels)
            self.uv_derivative[ids, :, 1] = np.einsum("nk,nkc->nc", dy, texels)
            self.stencils.append(_SampleStencil(ids, rows, columns, weights))
        self._sample_levels = samples.mip_levels.copy()

    @property
    def shape(self) -> tuple[int, int]:
        return int(np.prod(self.sample_shape)), int(np.prod(self.variable_shape))

    def forward(self, direction: FloatArray) -> FloatArray:
        delta = np.asarray(direction, dtype=np.float64).reshape(self.variable_shape)
        if not np.isfinite(delta).all():
            raise ValueError("Finite feature direction required")
        if self.block == "uv":
            points = np.einsum("nc,ncd->nd", self.barycentric, delta[self.uv_corners])
            return np.einsum("ncd,nd->nc", self.uv_derivative, points)
        levels = linear_mip_chain(delta, len(self.levels) - 1)
        output = np.zeros(self.sample_shape, dtype=np.float64)
        for stencil in self.stencils:
            level = int(self._sample_levels[stencil.ids[0]])
            texels = levels[level][stencil.rows, stencil.columns]
            output[stencil.ids] = np.einsum("nk,nkc->nc", stencil.weights, texels)
        return output

    def adjoint(self, sample_derivative: FloatArray) -> FloatArray:
        derivative = np.asarray(sample_derivative, dtype=np.float64).reshape(self.sample_shape)
        if not np.isfinite(derivative).all():
            raise ValueError("Finite sample derivative required")
        if self.block == "uv":
            points = np.einsum("nc,ncd->nd", derivative, self.uv_derivative)
            result = np.zeros(self.variable_shape, dtype=np.float64)
            for corner in range(3):
                np.add.at(result, self.uv_corners[:, corner], self.barycentric[:, corner, None] * points)
            return result
        gradients = [np.zeros_like(image) for image in self.levels]
        for stencil in self.stencils:
            level = int(self._sample_levels[stencil.ids[0]])
            for tap in range(4):
                np.add.at(
                    gradients[level], (stencil.rows[:, tap], stencil.columns[:, tap]),
                    stencil.weights[:, tap, None] * derivative[stencil.ids],
                )
        return _mip_adjoint(gradients, self.levels)

    def matvec(self, direction: FloatArray) -> FloatArray:
        return self.forward(direction).ravel()

    def rmatvec(self, multipliers: FloatArray) -> FloatArray:
        return self.adjoint(multipliers).ravel()


def _axis_mip_matrices(size: int, maximum_level: int) -> tuple[csr_matrix, ...]:
    """Sparse level-axis to original-axis maps, including odd BOX support."""
    matrices = [csr_matrix(eye(size, format="csr"))]
    current_size = size
    for _ in range(maximum_level):
        indices, weights = _box_stencil(current_size)
        output_size = len(indices)
        rows = np.repeat(np.arange(output_size), 3)
        reduction = csr_matrix((weights.ravel(), (rows, indices.ravel())),
                               shape=(output_size, current_size))
        reduction.eliminate_zeros()
        matrices.append(csr_matrix(reduction @ matrices[-1]))
        current_size = output_size
    return tuple(matrices)


class SparseFrozenTextureJacobian:
    """Exact sparse mip/bilinear map restricted to active original pixels.

    The nonnegative scalar map B is formed before any signed feature
    derivative, so zero residuals or gradient cancellation cannot discard
    texture variables. Its columns are ascending original pixel IDs. Each
    active pixel keeps every channel in HWC order.
    """

    def __init__(self, state: MeshState, samples: FeatureSamples, texture: FloatArray) -> None:
        if (texture.ndim != 3 or texture.shape[2] != samples.values.shape[1]
                or min(texture.shape) < 1 or not np.isfinite(texture).all()
                or int(samples.face_ids.max()) >= len(state.mesh.faces)):
            raise ValueError("Finite HWC texture matching original-face feature samples required")
        assert samples.mip_levels is not None
        maximum = int(samples.mip_levels.max())
        height, width, channels = texture.shape
        if maximum > int(np.log2(max(height, width))):
            raise ValueError("Requested mip level unavailable")
        horizontal = _axis_mip_matrices(width, maximum)
        vertical = _axis_mip_matrices(height, maximum)
        points = np.einsum("nc,ncd->nd", samples.barycentric,
                           state.uv[state.uv_faces[samples.face_ids]])
        scalar = csr_matrix((len(points), height * width), dtype=np.float64)
        for level in np.unique(samples.mip_levels):
            level = int(level)
            ids = np.flatnonzero(samples.mip_levels == level)
            horizontal_shape, vertical_shape = horizontal[level].shape, vertical[level].shape
            assert horizontal_shape is not None and vertical_shape is not None
            level_height, level_width = vertical_shape[0], horizontal_shape[0]
            rows, columns, weights, _, _ = _bilinear_stencil(points[ids], level_height, level_width)
            interpolation = csr_matrix(
                (weights.ravel(), (np.repeat(ids, 4), (rows * level_width + columns).ravel())),
                shape=(len(points), level_height * level_width),
            )
            interpolation.eliminate_zeros()
            if level == 0:
                scalar += interpolation
            else:
                scalar += interpolation @ kron(vertical[level], horizontal[level], format="csr")
        scalar.sum_duplicates()
        scalar.eliminate_zeros()
        active_pixels = np.unique(scalar.indices).astype(np.int64)
        self.pixel_map = csr_matrix(
            (scalar.data.copy(), np.searchsorted(active_pixels, scalar.indices), scalar.indptr.copy()),
            shape=(len(points), len(active_pixels)),
        )
        self.active_pixel_ids = active_pixels
        self.variable_indices = (channels * active_pixels[:, None] + np.arange(channels)).ravel()
        self.variable_indices.setflags(write=False)
        self.variable_shape = (len(active_pixels), channels)
        self.full_variable_shape = texture.shape
        self.sample_shape = samples.values.shape
        self.values = np.asarray(self.pixel_map @ texture.reshape(-1, channels)[active_pixels],
                                 dtype=np.float64)

    @property
    def shape(self) -> tuple[int, int]:
        return int(np.prod(self.sample_shape)), int(np.prod(self.variable_shape))

    def forward(self, direction: FloatArray) -> FloatArray:
        compact = np.asarray(direction, dtype=np.float64).reshape(self.variable_shape)
        if not np.isfinite(compact).all():
            raise ValueError("Finite compact texture direction required")
        return np.asarray(self.pixel_map @ compact, dtype=np.float64)

    def adjoint(self, sample_derivative: FloatArray) -> FloatArray:
        derivative = np.asarray(sample_derivative, dtype=np.float64).reshape(self.sample_shape)
        if not np.isfinite(derivative).all():
            raise ValueError("Finite sample derivative required")
        return np.asarray(self.pixel_map.T @ derivative, dtype=np.float64)

    def matvec(self, direction: FloatArray) -> FloatArray:
        return self.forward(direction).ravel()

    def rmatvec(self, multipliers: FloatArray) -> FloatArray:
        return self.adjoint(multipliers).ravel()


@dataclass(frozen=True)
class _SampleConstraintOperator:
    sample_gradient: csr_matrix
    sampler: FrozenFeatureJacobian | SparseFrozenTextureJacobian

    @property
    def shape(self) -> tuple[int, int]:
        assert self.sample_gradient.shape is not None
        return self.sample_gradient.shape[0], self.sampler.shape[1]

    def matvec(self, direction: FloatArray) -> FloatArray:
        return np.asarray(self.sample_gradient @ self.sampler.matvec(direction), dtype=np.float64).ravel()

    def rmatvec(self, multipliers: FloatArray) -> FloatArray:
        sample_gradient = np.asarray(self.sample_gradient.T @ multipliers, dtype=np.float64)
        return self.sampler.rmatvec(sample_gradient)


def upper_tail_mean(
    values: FloatArray, weights: FloatArray | None = None, quantile: float = 0.95,
) -> tuple[float, FloatArray]:
    """Weighted upper CVaR and a valid subgradient, splitting threshold ties.

    CVaR integrates the worst ``1-quantile`` probability mass. At a tie all
    threshold samples receive the same fraction of their probability mass;
    it does not depend on sample ordering. Zero-weight observations never
    affect a weighted mean or CVaR (the separate max row still observes them).
    """
    values = np.asarray(values, dtype=np.float64)
    mass = np.ones(len(values)) if weights is None else np.asarray(weights, dtype=np.float64)
    if (values.ndim != 1 or len(values) == 0 or mass.shape != values.shape
            or not np.isfinite(values).all() or not np.isfinite(mass).all()
            or np.any(mass < 0) or mass.sum() <= 0 or not 0 <= quantile < 1):
        raise ValueError("Finite values, nonnegative positive-total mass and quantile in [0,1) required")
    mass = mass / mass.sum()
    tail = 1 - quantile
    positive = np.flatnonzero(mass > 0)
    order = positive[np.argsort(-values[positive], kind="stable")]
    index = min(int(np.searchsorted(np.cumsum(mass[order]), tail, side="left")), len(values) - 1)
    threshold = values[order[index]]
    above, tied = values > threshold, values == threshold
    selected = np.where(above, mass, 0.0)
    remaining = max(0.0, tail - float(selected.sum()))
    selected[tied] = mass[tied] * (remaining / float(mass[tied].sum()))
    derivative = selected / tail
    return float(derivative @ values), derivative


def _aggregate(
    values: FloatArray, weights: FloatArray, aggregate: str,
) -> tuple[float, FloatArray]:
    if aggregate == "mean":
        derivative = weights / weights.sum()
        return float(derivative @ values), derivative
    if aggregate == "cvar95":
        return upper_tail_mean(values, weights)
    maximum = float(values.max())
    tied = values == maximum
    derivative = tied / tied.sum()
    return maximum, np.asarray(derivative, dtype=np.float64)


def normal_chord_bound(degrees: float) -> float:
    """Pointwise unit-vector chord corresponding to an angular threshold."""
    if not np.isfinite(degrees) or not 0 < degrees <= 180:
        raise ValueError("Normal angle must lie in (0,180] degrees")
    return float(2 * np.sin(np.deg2rad(degrees) / 2))


def _unit_with_jacobian(values: FloatArray) -> tuple[FloatArray, FloatArray]:
    # Smooth at zero so an infeasible shared average still has a restoration
    # derivative. This epsilon and its approximation are recorded in metadata.
    lengths = np.sqrt(np.sum(values**2, axis=1) + 1e-16)
    unit = values / lengths[:, None]
    jac = (np.eye(3)[None] - unit[:, :, None] * unit[:, None, :]) / lengths[:, None, None]
    return unit, jac


def _unit_values(values: FloatArray) -> FloatArray:
    """Identical epsilon-normalized values without allocating their Jacobian."""
    return values / np.sqrt(np.sum(values**2, axis=1) + 1e-16)[:, None]


def _norm_with_gradient(residual: FloatArray) -> tuple[FloatArray, FloatArray]:
    norm = np.linalg.norm(residual, axis=1)
    derivative = np.divide(residual, norm[:, None], out=np.zeros_like(residual), where=norm[:, None] > 0)
    return norm, derivative


def _render_values(values: FloatArray, matrix: FloatArray, samples: FeatureSamples) -> FloatArray:
    local = _unit_values(values[:, 4:7])
    material = MaterialSamples(
        values[:, :4], np.einsum("nij,nj->ni", matrix, local), values[:, 7:10],
        np.ones(len(values), dtype=np.bool_), np.zeros(len(values)), samples.face_ids, samples.barycentric,
    )
    lights = (np.asarray([0.6, -0.2, 1.0]), np.asarray([-0.5, 0.7, 0.6]))
    return np.stack([shade_material(material, light) for light in lights], axis=1)


def _material_errors(
    state: MeshState, samples: FeatureSamples, predicted: FloatArray, include_render: bool,
    *, derivatives: bool = True,
) -> dict[str, tuple[FloatArray, FloatArray]]:
    count = len(predicted)
    errors: dict[str, tuple[FloatArray, FloatArray]] = {}
    if not derivatives:
        # Empty gradients are internal sentinels, never exposed as a usable
        # constraint operator. Row aggregation below skips them entirely.
        empty = np.empty((0, 10), dtype=np.float64)
        errors["color"] = np.linalg.norm(predicted[:, :3] - samples.values[:, :3], axis=1), empty
        normal, target_normal = _unit_values(predicted[:, 4:7]), _unit_values(samples.values[:, 4:7])
        errors["normal"] = np.linalg.norm(normal - target_normal, axis=1), empty
        errors["orm"] = np.abs(predicted[:, 7:10] - samples.values[:, 7:10]).max(axis=1), empty
        if include_render:
            matrix, valid = interpolated_frames(*frame_arrays(state), samples.face_ids, samples.barycentric)
            if not valid.all():
                raise ValueError("Frozen render frame is invalid")
            actual = _render_values(predicted, matrix, samples)
            target = _render_values(samples.values, matrix, samples)
            errors["render"] = np.linalg.norm(actual - target, axis=2).max(axis=1), empty
        return errors
    color, color_derivative = _norm_with_gradient(predicted[:, :3] - samples.values[:, :3])
    gradient = np.zeros((count, 10))
    gradient[:, :3] = color_derivative
    errors["color"] = color, gradient
    normal, normal_jac = _unit_with_jacobian(predicted[:, 4:7])
    target_normal, _ = _unit_with_jacobian(samples.values[:, 4:7])
    chord, chord_derivative = _norm_with_gradient(normal - target_normal)
    gradient = np.zeros((count, 10))
    gradient[:, 4:7] = np.einsum("ni,nij->nj", chord_derivative, normal_jac)
    errors["normal"] = chord, gradient
    residual = predicted[:, 7:10] - samples.values[:, 7:10]
    absolute = np.abs(residual)
    orm = absolute.max(axis=1)
    tied = absolute == orm[:, None]
    gradient = np.zeros((count, 10))
    gradient[:, 7:10] = np.sign(residual) * tied / tied.sum(axis=1)[:, None]
    errors["orm"] = orm, gradient
    if include_render:
        matrix, valid = interpolated_frames(*frame_arrays(state), samples.face_ids, samples.barycentric)
        if not valid.all():
            raise ValueError("Frozen render frame is invalid")
        actual = _render_values(predicted, matrix, samples)
        target = _render_values(samples.values, matrix, samples)
        light_error, light_gradient = _norm_with_gradient((actual - target).reshape(-1, 3))
        light_error = light_error.reshape(count, 2)
        light_gradient = light_gradient.reshape(count, 2, 3)
        render = light_error.max(axis=1)
        tied_lights = light_error == render[:, None]
        light_mass = tied_lights / tied_lights.sum(axis=1)[:, None]
        gradient = np.zeros((count, 10))
        for channel in (0, 1, 2, 4, 5, 6, 7, 8, 9):
            plus, minus = predicted.copy(), predicted.copy()
            plus[:, channel] += 1e-6
            minus[:, channel] -= 1e-6
            derivative = (_render_values(plus, matrix, samples) - _render_values(minus, matrix, samples)) / 2e-6
            gradient[:, channel] = np.sum(light_mass * np.sum(light_gradient * derivative, axis=2), axis=1)
        errors["render"] = render, gradient
    return errors


def _sample_feature_values(state: MeshState, samples: FeatureSamples, texture: FloatArray) -> FloatArray:
    """The same unquantized BOX/bilinear samples without a sampler operator."""
    if (texture.ndim != 3 or texture.shape[2] != samples.values.shape[1] or min(texture.shape) < 1
            or not np.isfinite(texture).all() or int(samples.face_ids.max()) >= len(state.mesh.faces)):
        raise ValueError("Finite HWC texture matching original-face feature samples required")
    assert samples.mip_levels is not None
    levels = linear_mip_chain(texture, int(samples.mip_levels.max()))
    points = np.einsum("nc,ncd->nd", samples.barycentric, state.uv[state.uv_faces[samples.face_ids]])
    values = np.zeros(samples.values.shape, dtype=np.float64)
    for level, image in enumerate(levels):
        ids = np.flatnonzero(samples.mip_levels == level)
        if len(ids):
            rows, columns, weights, _, _ = _bilinear_stencil(points[ids], *image.shape[:2])
            values[ids] = np.einsum("nk,nkc->nc", weights, image[rows, columns])
    return values


def _material_constraints(
    state: MeshState, samples: FeatureSamples, texture: FloatArray, *, block: Block = "texture",
    thresholds: MaterialThresholds | None = None, important_faces: tuple[int, ...] = (),
    include_render: bool = True, alpha_minimum: float = 0.99,
    require_all_faces: bool = True, compressed_texture: bool = False,
    derivatives: bool = True,
) -> ConstraintLinearization | ConstraintValues:
    """Per-face/mip normalized mean, upper-CVaR95, max and opacity rows.

    CVaR95 is a conservative upper-tail replacement for a weighted 95th
    percentile. The normal chord bound is pointwise-equivalent to degrees
    for unit vectors, but averaging chords is not averaging angles. Render
    uses the existing CPU shader, frozen TBN, and a central-difference local
    shader derivative. Encoded/mip/raster validation remains independent.
    ``compressed_texture=True`` preserves this mathematical subproblem and
    restricts texture variables to the nonnegative sampler's exact support.
    """
    if samples.values.shape[1] != 10:
        raise ValueError("Material constraints require RGBA + signed tangent normal + ORM (10 channels)")
    if np.any(np.linalg.norm(samples.values[:, 4:7], axis=1) <= 1e-10):
        raise ValueError("Fixed normal reference must have a nonzero direction")
    if not 0 < alpha_minimum < 1:
        raise ValueError("Opacity threshold must lie in (0,1)")
    limits = thresholds or MaterialThresholds()
    assert samples.mip_levels is not None
    assert samples.weights is not None
    faces, levels = np.unique(samples.face_ids), np.unique(samples.mip_levels)
    if require_all_faces and not np.array_equal(faces, np.arange(len(state.mesh.faces))):
        raise ValueError("Material constraint samples must cover every original face")
    if compressed_texture and block != "texture":
        raise ValueError("Texture variable compression requires the texture block")
    sampler = ((SparseFrozenTextureJacobian(state, samples, texture) if compressed_texture
                else FrozenFeatureJacobian(state, samples, texture, block)) if derivatives else None)
    predicted = sampler.values if sampler is not None else _sample_feature_values(state, samples, texture)
    errors = _material_errors(state, samples, predicted, include_render, derivatives=derivatives)
    values: list[float] = []
    labels: list[str] = []
    row_indices: list[int] = []
    columns: list[int] = []
    data: list[float] = []

    def append(label: str, value: float, ids: IntArray, derivative: FloatArray | None) -> None:
        row = len(values)
        values.append(value)
        labels.append(label)
        if derivative is None:
            return
        local_rows, channels = np.nonzero(derivative)
        row_indices.extend([row] * len(local_rows))
        columns.extend((10 * ids[local_rows] + channels).tolist())
        data.extend(derivative[local_rows, channels].tolist())

    for face in faces:
        multiplier = limits.important_multiplier if int(face) in important_faces else 1.0
        for level in levels:
            ids = np.flatnonzero((samples.face_ids == face) & (samples.mip_levels == level))
            if not len(ids) or samples.weights[ids].sum() <= 0:
                raise ValueError("Each original face needs positive sample mass at every requested mip")
            for name, (error, sample_gradient) in errors.items():
                for aggregate, threshold_aggregate in (("mean", "mean"), ("cvar95", "p95"), ("max", "max")):
                    suffix = "_degrees" if name == "normal" else ""
                    limit = float(getattr(limits, f"{name}_{threshold_aggregate}{suffix}")) * multiplier
                    if name == "normal":
                        limit = normal_chord_bound(limit)
                    quality, mass = _aggregate(error[ids], samples.weights[ids], aggregate)
                    append(f"face={int(face)};mip={int(level)};{name}_{aggregate}", quality / limit - 1,
                           ids, sample_gradient[ids] * mass[:, None] / limit if derivatives else None)
            deficit = alpha_minimum - predicted[ids, 3]
            maximum, mass = _aggregate(deficit, samples.weights[ids], "max")
            derivative = None
            if derivatives:
                derivative = np.zeros((len(ids), 10))
                derivative[:, 3] = -mass / (1 - alpha_minimum)
            append(f"face={int(face)};mip={int(level)};alpha_min", maximum / (1 - alpha_minimum),
                   ids, derivative)
    metadata: dict[str, object] = {
         "scope": "FROZEN_LOCAL_PROXY_NOT_MATERIAL_CERTIFICATE", "block": block,
         "normal_proxy": "unit signed-linear chord; epsilon=1e-8 smooth normalization; fixed TBN",
         "normal_bound": "2*sin(degrees*pi/360); pointwise unit equivalence, aggregate angular gate separate",
         "tail_proxy": "area-mass weighted upper CVaR95, not interpolated empirical p95",
         "mean_proxy": "within-face normalized sample area mass; uniform mass equals sample mean",
         "render_proxy": ("existing two-light Lambert+GGX; fixed frame; "
                          + ("local central FD 1e-6" if derivatives else "values only; no shader FD"))
                         if include_render else "OMITTED_EXPLICITLY",
         "frozen": ["reference", "original-face probes", "mip levels", "TBN", "address cells"],
         "alpha_minimum": alpha_minimum,
         "nonlinear_encoded_gate_required": True,
         "variable_schema": "NO_DERIVATIVE_VARIABLES_VALUES_ONLY" if not derivatives else
                            "flat original HWC indices; ascending active pixels, all channels" if compressed_texture
                             else "full original HWC texture" if block == "texture" else "full original UV copies",
         "source_variable_count": int(texture.size if block == "texture" else state.uv.size),
         "evaluation_mode": "FULL_LINEARIZATION" if derivatives else "VALUES_ONLY",
         "derivative_available": derivatives,
         "shader_finite_difference_passes": 18 if derivatives and include_render else 0,
    }
    if sampler is None:
        return ConstraintValues(np.asarray(values, dtype=np.float64), tuple(labels), metadata)
    sparse_gradient = csr_matrix((data, (row_indices, columns)), shape=(len(values), sampler.shape[0]))
    metadata.update({"feature_gradient_nnz": int(sparse_gradient.nnz), "active_variable_count": sampler.shape[1]})
    return ConstraintLinearization(
        np.asarray(values, dtype=np.float64), _SampleConstraintOperator(sparse_gradient, sampler), tuple(labels),
        metadata,
        sampler.variable_indices if isinstance(sampler, SparseFrozenTextureJacobian) else None,
        texture.shape if block == "texture" else state.uv.shape,
    )


def linearize_material_constraints(
    state: MeshState, samples: FeatureSamples, texture: FloatArray, *, block: Block = "texture",
    thresholds: MaterialThresholds | None = None, important_faces: tuple[int, ...] = (),
    include_render: bool = True, alpha_minimum: float = 0.99,
    require_all_faces: bool = True, compressed_texture: bool = False,
) -> ConstraintLinearization:
    """Build the unchanged full frozen material model and its exact adjoint."""
    result = _material_constraints(
        state, samples, texture, block=block, thresholds=thresholds, important_faces=important_faces,
        include_render=include_render, alpha_minimum=alpha_minimum, require_all_faces=require_all_faces,
        compressed_texture=compressed_texture,
    )
    assert isinstance(result, ConstraintLinearization)
    return result


def evaluate_material_constraints(
    state: MeshState, samples: FeatureSamples, texture: FloatArray, *,
    thresholds: MaterialThresholds | None = None, important_faces: tuple[int, ...] = (),
    include_render: bool = True, alpha_minimum: float = 0.99, require_all_faces: bool = True,
) -> ConstraintValues:
    """Evaluate the identical nonlinear proxy rows without sparse/FD derivatives.

    This builds actual unquantized mip samples and the current TBN/render
    values. It is neither a frozen Jacobian nor an encoded material gate.
    """
    result = _material_constraints(
        state, samples, texture, thresholds=thresholds, important_faces=important_faces,
        include_render=include_render, alpha_minimum=alpha_minimum, require_all_faces=require_all_faces,
        derivatives=False,
    )
    assert isinstance(result, ConstraintValues)
    return result


@dataclass(frozen=True)
class _SparseConstraintOperator:
    matrix: csr_matrix

    @property
    def shape(self) -> tuple[int, int]:
        assert self.matrix.shape is not None
        return self.matrix.shape

    def matvec(self, direction: FloatArray) -> FloatArray:
        return np.asarray(self.matrix @ np.asarray(direction).ravel(), dtype=np.float64).ravel()

    def rmatvec(self, multipliers: FloatArray) -> FloatArray:
        return np.asarray(self.matrix.T @ multipliers, dtype=np.float64).ravel()


@dataclass(frozen=True)
class _StackedConstraintOperator:
    operators: tuple[ConstraintOperator, ...]

    @property
    def shape(self) -> tuple[int, int]:
        return sum(operator.shape[0] for operator in self.operators), self.operators[0].shape[1]

    def matvec(self, direction: FloatArray) -> FloatArray:
        return np.concatenate([operator.matvec(direction) for operator in self.operators])

    def rmatvec(self, multipliers: FloatArray) -> FloatArray:
        result = np.zeros(self.shape[1])
        start = 0
        for operator in self.operators:
            stop = start + operator.shape[0]
            result += operator.rmatvec(multipliers[start:stop])
            start = stop
        return result


def stack_constraint_linearizations(*parts: ConstraintLinearization) -> ConstraintLinearization:
    """Stack raw-variable operators before any motion-tie or owner reduction."""
    if not parts or len({part.operator.shape[1] for part in parts}) != 1:
        raise ValueError("Nonempty constraint parts with equal variable sizes required")
    first_indices = parts[0].variable_indices
    if any(part.variable_shape != parts[0].variable_shape
           or (part.variable_indices is None) != (first_indices is None)
           or (part.variable_indices is not None and first_indices is not None
               and not np.array_equal(part.variable_indices, first_indices)) for part in parts):
        raise ValueError("Stacked constraints must use the same original variable identity and compression")
    return ConstraintLinearization(
        np.concatenate([part.g for part in parts]), _StackedConstraintOperator(tuple(p.operator for p in parts)),
        tuple(label for part in parts for label in part.labels),
        {"scope": "STACKED_FROZEN_LOCAL_PROXIES", "parts": [part.metadata for part in parts]},
        parts[0].variable_indices, parts[0].variable_shape,
    )


def _anisotropy_with_gradient(jacobian: FloatArray) -> tuple[FloatArray, FloatArray]:
    left, singular, right = np.linalg.svd(jacobian)
    ratio = singular[:, 0] / singular[:, 1]
    diagonal = np.zeros_like(jacobian)
    diagonal[:, 0, 0] = 1 / singular[:, 1]
    diagonal[:, 1, 1] = -singular[:, 0] / singular[:, 1]**2
    gradient = left @ diagonal @ right
    # At isotropy any singular basis is arbitrary. Zero is the symmetric
    # Clarke subgradient; choosing an SVD axis would bias a rotation.
    gradient[np.isclose(singular[:, 0], singular[:, 1], rtol=1e-12, atol=0)] = 0
    return ratio, gradient


def _geometry_constraints(
    state: MeshState, *, orientations: IntArray | None = None, member_ids: IntArray | None = None,
    mean_shape_limit: float = 0.025, tail_shape_limit: float = 0.1,
    maximum_anisotropy: float = 2.0, maximum_neighbor_density_ratio: float = 2.0,
    pixel_dimensions: tuple[int, int] | None = None,
    density_scope: Literal["all_original_adjacency", "same_member"] = "all_original_adjacency",
    derivatives: bool = True,
) -> ConstraintLinearization | ConstraintValues:
    """Eq. (24)/(17) rows in raw exported UV-copy coordinates.

    Shape mean and CVaR95 use original surface area. Anisotropy is constrained
    independently for every face, and density for every original interior
    edge. Positive determinant, collisions, coverage and atlas borders belong
    to the separate hard admissible set H and are not softened by these rows.
    ``pixel_dimensions`` additionally constrains rectangular pixel anisotropy.
    """
    count = len(state.mesh.faces)
    inverse_rest = _inverse_rest(state)
    jacobian = _edge_matrices(state, state.uv) @ inverse_rest
    signs = np.sign(np.linalg.det(jacobian)) if orientations is None else np.asarray(orientations)
    members = np.zeros(count, dtype=np.int64) if member_ids is None else np.asarray(member_ids)
    limits = (mean_shape_limit, tail_shape_limit, maximum_anisotropy, maximum_neighbor_density_ratio)
    if (signs.shape != (count,) or not np.isin(signs, (-1, 1)).all() or members.shape != (count,)
            or not np.isfinite(jacobian).all() or not all(np.isfinite(value) for value in limits)
            or min(mean_shape_limit, tail_shape_limit) < 0
            or min(maximum_anisotropy, maximum_neighbor_density_ratio) < 1
            or density_scope not in ("all_original_adjacency", "same_member")):
        raise ValueError("Invalid geometry constraint profile, orientations or members")
    jacobian[:, 0] *= signs[:, None]
    determinant = np.linalg.det(jacobian)
    if np.any(determinant <= 0):
        raise ValueError("Geometry linearization requires positive oriented determinants")
    scale = np.sqrt(determinant)
    normalized = jacobian / scale[:, None, None]
    inverse = np.linalg.inv(normalized)
    shape = 0.25 * (np.sum(normalized**2, axis=(1, 2)) + np.sum(inverse**2, axis=(1, 2))) - 1
    inverse_t, shape_gradient, ratio_gradient = (np.empty((0, 2, 2)) for _ in range(3))
    if derivatives:
        inverse_t = np.linalg.inv(jacobian).swapaxes(1, 2)
        normalized_inverse_t = inverse.swapaxes(1, 2)
        sd_gradient = 0.5 * (normalized - normalized_inverse_t @ inverse @ normalized_inverse_t)
        shape_gradient = sd_gradient / scale[:, None, None]
        shape_gradient -= 0.5 * np.sum(sd_gradient * normalized, axis=(1, 2))[:, None, None] * inverse_t
        ratio, ratio_gradient = _anisotropy_with_gradient(jacobian)
    else:
        singular = np.linalg.svd(jacobian, compute_uv=False)
        ratio = singular[:, 0] / singular[:, 1]
    values: list[float] = []
    labels: list[str] = []
    row_indices: list[int] = []
    columns: list[int] = []
    data: list[float] = []

    def append(label: str, quality: float, limit: float, ids: IntArray, gradient: FloatArray | None) -> None:
        normalization = limit if limit > 0 else 1.0
        row = len(values)
        values.append((quality - limit) / normalization)
        labels.append(label)
        if gradient is None:
            return
        local = gradient.copy() / normalization
        local[:, 0] *= signs[ids, None]
        edges = local @ inverse_rest[ids].swapaxes(1, 2)
        corner_gradients = np.stack((-edges.sum(axis=2), edges[:, :, 0], edges[:, :, 1]), axis=1)
        uv_ids = state.uv_faces[ids]
        flat_columns = (2 * uv_ids[..., None] + np.arange(2)).ravel()
        flat_gradient = corner_gradients.ravel()
        nonzero = flat_gradient != 0
        row_indices.extend([row] * int(nonzero.sum()))
        columns.extend(flat_columns[nonzero].tolist())
        data.extend(flat_gradient[nonzero].tolist())

    face_ids = np.arange(count, dtype=np.int64)
    weights = state.mesh.areas / state.mesh.areas.sum()
    append("geometry;shape_mean", float(weights @ shape), mean_shape_limit, face_ids,
           shape_gradient * weights[:, None, None] if derivatives else None)
    tail, tail_mass = upper_tail_mean(shape, weights)
    append("geometry;shape_cvar95", tail, tail_shape_limit, face_ids,
           shape_gradient * tail_mass[:, None, None] if derivatives else None)
    for face in range(count):
        append(f"face={face};geometry;kappa", float(ratio[face]), maximum_anisotropy,
               face_ids[face:face + 1], ratio_gradient[face:face + 1] if derivatives else None)
    if pixel_dimensions is not None:
        if any(type(value) is not int or value <= 0 for value in pixel_dimensions):
            raise ValueError("Positive integer pixel width and height required")
        metric = np.asarray(pixel_dimensions, dtype=np.float64)
        pixel_gradient = np.empty((0, 2, 2))
        if derivatives:
            pixel_ratio, pixel_gradient = _anisotropy_with_gradient(metric[None, :, None] * jacobian)
            pixel_gradient *= metric[None, :, None]
        else:
            singular = np.linalg.svd(metric[None, :, None] * jacobian, compute_uv=False)
            pixel_ratio = singular[:, 0] / singular[:, 1]
        for face in range(count):
            append(f"face={face};geometry;pixel_kappa", float(pixel_ratio[face]), maximum_anisotropy,
                   face_ids[face:face + 1], pixel_gradient[face:face + 1] if derivatives else None)
    log_density = 0.5 * np.log(determinant)
    density_limit = float(np.log(maximum_neighbor_density_ratio))
    for edge, entries in enumerate(state.mesh.edge_faces):
        if len(entries) != 2:
            continue
        first, second = entries[0][0], entries[1][0]
        if density_scope == "same_member" and members[first] != members[second]:
            continue
        delta = log_density[first] - log_density[second]
        ids = np.asarray([first, second], dtype=np.int64)
        density_gradient = None
        if derivatives:
            density_gradient = 0.5 * np.sign(delta) * inverse_t[ids]
            density_gradient[1] *= -1
        append(f"edge={edge};geometry;neighbor_log_density", abs(float(delta)), density_limit,
               ids, density_gradient)
    metadata: dict[str, object] = {
         "scope": "FROZEN_GEOMETRY_LOCAL_LINEARIZATION_NOT_CERTIFICATE", "block": "uv",
         "shape_gradient": "scale-normalized symmetric Dirichlet Eq24, original-area weighting",
         "tail_proxy": "weighted upper CVaR95, not interpolated empirical p95",
         "anisotropy_gradient": "SVD analytic gradient; zero symmetric Clarke subgradient at isotropy",
         "density_gradient": "absolute neighboring log density Eq17; zero subgradient at ties",
         "density_scope": density_scope,
         "zero_limits": "exact-zero rows remain unscaled; positive limits dimensionless",
         "pixel_dimensions": pixel_dimensions, "evaluation_mode": "FULL_LINEARIZATION" if derivatives
         else "VALUES_ONLY", "derivative_available": derivatives, "hard_admissibility_checks_required": True,
    }
    if not derivatives:
        return ConstraintValues(np.asarray(values, dtype=np.float64), tuple(labels), metadata)
    matrix = csr_matrix((data, (row_indices, columns)), shape=(len(values), state.uv.size))
    metadata["gradient_nnz"] = int(matrix.nnz)
    return ConstraintLinearization(
        np.asarray(values, dtype=np.float64), _SparseConstraintOperator(matrix), tuple(labels), metadata,
        None, state.uv.shape,
    )


def linearize_geometry_constraints(
    state: MeshState, *, orientations: IntArray | None = None, member_ids: IntArray | None = None,
    mean_shape_limit: float = 0.025, tail_shape_limit: float = 0.1,
    maximum_anisotropy: float = 2.0, maximum_neighbor_density_ratio: float = 2.0,
    pixel_dimensions: tuple[int, int] | None = None,
    density_scope: Literal["all_original_adjacency", "same_member"] = "all_original_adjacency",
) -> ConstraintLinearization:
    """Build the unchanged full Eq24/Eq17 geometry model and its adjoint."""
    result = _geometry_constraints(
        state, orientations=orientations, member_ids=member_ids, mean_shape_limit=mean_shape_limit,
        tail_shape_limit=tail_shape_limit, maximum_anisotropy=maximum_anisotropy,
        maximum_neighbor_density_ratio=maximum_neighbor_density_ratio,
        pixel_dimensions=pixel_dimensions, density_scope=density_scope,
    )
    assert isinstance(result, ConstraintLinearization)
    return result


def evaluate_geometry_constraints(
    state: MeshState, *, orientations: IntArray | None = None, member_ids: IntArray | None = None,
    mean_shape_limit: float = 0.025, tail_shape_limit: float = 0.1,
    maximum_anisotropy: float = 2.0, maximum_neighbor_density_ratio: float = 2.0,
    pixel_dimensions: tuple[int, int] | None = None,
    density_scope: Literal["all_original_adjacency", "same_member"] = "all_original_adjacency",
) -> ConstraintValues:
    """Evaluate the same quality rows, preserving orientation and row identity."""
    result = _geometry_constraints(
        state, orientations=orientations, member_ids=member_ids, mean_shape_limit=mean_shape_limit,
        tail_shape_limit=tail_shape_limit, maximum_anisotropy=maximum_anisotropy,
        maximum_neighbor_density_ratio=maximum_neighbor_density_ratio,
        pixel_dimensions=pixel_dimensions, density_scope=density_scope, derivatives=False,
    )
    assert isinstance(result, ConstraintValues)
    return result
