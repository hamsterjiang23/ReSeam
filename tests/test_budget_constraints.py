"""Independent finite differences for Eq19 row values and matrix-free adjoints."""

import numpy as np
import pytest

import reseam.budget_constraints as constraints
from reseam.budget_constraints import (
    ConstraintLinearization,
    ConstraintValues,
    FrozenFeatureJacobian,
    SparseFrozenTextureJacobian,
    _material_errors,
    evaluate_geometry_constraints,
    evaluate_material_constraints,
    linearize_geometry_constraints,
    linearize_material_constraints,
    normal_chord_bound,
    stack_constraint_linearizations,
    upper_tail_mean,
)
from reseam.budget_material import MaterialThresholds
from reseam.budget_optimize import FeatureSamples
from reseam.mesh import make_mesh
from reseam.types import FloatArray, MeshState


def triangle() -> MeshState:
    vertices = np.asarray([[0, 0, 0], [1, 0, 0], [0, 1, 0]], dtype=float)
    faces = np.asarray([[0, 1, 2]], dtype=np.int64)
    uv = np.asarray([[0.16, 0.18], [0.83, 0.21], [0.23, 0.85]], dtype=float)
    return MeshState(make_mesh(vertices, faces), frozenset(), uv, faces.copy())


def feature(values: FloatArray) -> FeatureSamples:
    bary = np.asarray([[0.19, 0.37, 0.44], [0.47, 0.29, 0.24], [0.23, 0.61, 0.16]])
    return FeatureSamples(np.zeros(6, dtype=np.int64), np.tile(bary, (2, 1)), values,
                          weights=np.asarray([0.2, 0.5, 0.3, 0.2, 0.5, 0.3]),
                          mip_levels=np.repeat(np.arange(2, dtype=np.int64), 3))


def material_texture(seed: int = 17) -> FloatArray:
    rng = np.random.default_rng(seed)
    texture = rng.uniform(0.2, 0.7, size=(5, 7, 10))
    texture[..., 3] = rng.uniform(0.97, 1.0, size=(5, 7))
    texture[..., 4:6] = rng.uniform(-0.15, 0.15, size=(5, 7, 2))
    texture[..., 6] = rng.uniform(0.7, 1, size=(5, 7))
    texture[..., 8] = rng.uniform(0.6, 0.8, size=(5, 7))
    return texture


@pytest.mark.parametrize("block", ["texture", "uv"])
def test_sampler_adjoint_and_finite_difference_include_odd_mips(block: str) -> None:
    state = triangle()
    rng = np.random.default_rng(29)
    image = material_texture()
    probes = feature(np.zeros((6, 10)))
    # Add the terminal 1x1 mip of the odd 7x5 image.
    probes = FeatureSamples(probes.face_ids, probes.barycentric, probes.values,
                           mip_levels=np.asarray([0, 1, 2, 0, 1, 2], dtype=np.int64))
    sampler = FrozenFeatureJacobian(state, probes, image, block)  # type: ignore[arg-type]
    direction = rng.normal(size=sampler.variable_shape)
    derivative = rng.normal(size=probes.values.shape)
    forward = sampler.forward(direction)
    assert float(np.sum(forward * derivative)) == pytest.approx(
        float(np.sum(direction * sampler.adjoint(derivative))), abs=2e-12,
    )
    plus_state, minus_state = state.copy(), state.copy()
    plus_image, minus_image = image.copy(), image.copy()
    if block == "texture":
        plus_image += 1e-7 * direction
        minus_image -= 1e-7 * direction
    else:
        plus_state.uv += 1e-7 * direction
        minus_state.uv -= 1e-7 * direction
    numeric = (FrozenFeatureJacobian(plus_state, probes, plus_image).values
               - FrozenFeatureJacobian(minus_state, probes, minus_image).values) / 2e-7
    assert np.allclose(forward, numeric, atol=2e-8, rtol=2e-6)


def test_weighted_cvar_fractional_ties_order_invariance_and_subgradient() -> None:
    values = np.asarray([3., 2., 2., 0.])
    weights = np.asarray([0.02, 0.03, 0.05, 0.90])
    result, derivative = upper_tail_mean(values, weights)
    assert result == pytest.approx(2.4)
    assert derivative == pytest.approx(np.asarray([0.4, 0.225, 0.375, 0.]))
    order = np.asarray([2, 0, 3, 1])
    shuffled, shuffled_derivative = upper_tail_mean(values[order], weights[order])
    assert shuffled == pytest.approx(result)
    assert shuffled_derivative == pytest.approx(derivative[order])
    rng = np.random.default_rng(43)
    for _ in range(30):
        delta = rng.normal(size=4) * 0.2
        changed = upper_tail_mean(values + delta, weights)[0]
        assert changed >= result + float(derivative @ delta) - 1e-12


@pytest.mark.parametrize("block", ["texture", "uv"])
def test_material_all_rows_fd_and_adjoint_including_render(block: str) -> None:
    state = triangle()
    rng = np.random.default_rng(43)
    image = material_texture()
    base = FrozenFeatureJacobian(state, feature(np.zeros((6, 10))), image).values
    target = base.copy()
    target[:, :3] -= rng.uniform(0.02, 0.04, size=(6, 3))
    target[:, 4:7] += rng.uniform(-0.04, 0.04, size=(6, 3))
    target[:, 7:10] -= rng.uniform(0.01, 0.05, size=(6, 3))
    probes = feature(target)
    rows = linearize_material_constraints(state, probes, image, block=block)  # type: ignore[arg-type]
    direction = rng.normal(size=image.shape if block == "texture" else state.uv.shape) * 0.01
    multipliers = rng.normal(size=len(rows.g))
    assert float(rows.operator.matvec(direction.ravel()) @ multipliers) == pytest.approx(
        float(direction.ravel() @ rows.operator.rmatvec(multipliers)), abs=1e-11,
    )
    plus_state, minus_state = state.copy(), state.copy()
    plus_image, minus_image = image.copy(), image.copy()
    if block == "texture":
        plus_image += 1e-5 * direction
        minus_image -= 1e-5 * direction
    else:
        plus_state.uv += 1e-5 * direction
        minus_state.uv -= 1e-5 * direction
    numeric = (linearize_material_constraints(plus_state, probes, plus_image).g
               - linearize_material_constraints(minus_state, probes, minus_image).g) / 2e-5
    # Render rows deliberately freeze the base TBN. For UV perturbations the
    # function rebuilds TBN; remove that frame change in the independent FD by
    # selecting the frame-independent color/normal/ORM/alpha rows instead.
    selected = np.asarray([block == "texture" or "render_" not in label for label in rows.labels])
    assert np.allclose(rows.operator.matvec(direction.ravel())[selected], numeric[selected],
                       atol=4e-7, rtol=3e-5)
    assert "local central FD" in str(rows.metadata["render_proxy"])
    assert rows.metadata["nonlinear_encoded_gate_required"] is True


def test_normal_chord_threshold_mapping_and_alpha_infeasibility() -> None:
    state = triangle()
    angle = np.deg2rad(10)
    target = np.tile(np.asarray([0.3, 0.4, 0.5, 1, 0, 0, 1, 1, 0.7, 0.2]), (6, 1))
    image = np.tile(target[0], (4, 4, 1))
    image[..., 4:7] = [np.sin(angle), 0, np.cos(angle)]
    image[..., 3] = .98
    thresholds = MaterialThresholds(normal_mean_degrees=10, normal_p95_degrees=10, normal_max_degrees=10)
    rows = linearize_material_constraints(state, feature(target), image, thresholds=thresholds,
                                         include_render=False)
    assert normal_chord_bound(10) == pytest.approx(np.linalg.norm(image[0, 0, 4:7] - target[0, 4:7]))
    normal = [value for label, value in zip(rows.labels, rows.g, strict=True) if "normal_" in label]
    assert np.asarray(normal) == pytest.approx(np.zeros(6), abs=1e-12)
    alpha = [value for label, value in zip(rows.labels, rows.g, strict=True) if "alpha_min" in label]
    assert alpha == pytest.approx([1., 1.])
    direction = np.zeros_like(image)
    direction[..., 3] = .005
    alpha_derivative = rows.operator.matvec(direction.ravel())[[i for i, x in enumerate(rows.labels)
                                                             if "alpha_min" in x]]
    assert alpha_derivative == pytest.approx([-.5, -.5])


def test_all_sample_error_derivatives_use_the_same_frozen_render_frame() -> None:
    state = triangle()
    image = material_texture()
    probes = feature(np.tile(np.asarray([.3, .4, .3, 1, .1, -.1, 1, .8, .7, .2]), (6, 1)))
    predicted = FrozenFeatureJacobian(state, probes, image).values
    rng = np.random.default_rng(29)
    direction = rng.normal(size=predicted.shape) * .01
    errors = _material_errors(state, probes, predicted, True)
    plus = _material_errors(state, probes, predicted + 1e-5 * direction, True)
    minus = _material_errors(state, probes, predicted - 1e-5 * direction, True)
    for name, (_, derivative) in errors.items():
        numeric = (plus[name][0] - minus[name][0]) / 2e-5
        assert np.sum(derivative * direction, axis=1) == pytest.approx(numeric, abs=1e-8, rel=3e-5)


def test_tiny_face_violation_cannot_be_hidden_by_global_area_mean() -> None:
    vertices = np.asarray([[0, 0, 0], [1, 0, 0], [0, 1, 0],
                           [2, 0, 0], [2.001, 0, 0], [2, .001, 0]], dtype=float)
    faces = np.asarray([[0, 1, 2], [3, 4, 5]], dtype=np.int64)
    uv = np.asarray([[.1, .1], [.4, .1], [.1, .4], [.6, .6], [.9, .6], [.6, .9]])
    state = MeshState(make_mesh(vertices, faces), frozenset(), uv, faces.copy())
    image = np.zeros((4, 4, 10))
    image[..., 3], image[..., 6], image[..., 8] = 1, 1, .7
    target = np.tile(image[0, 0], (2, 1))
    target[1, 0] = .1
    probes = FeatureSamples(np.asarray([0, 1], dtype=np.int64), np.full((2, 3), 1 / 3), target,
                           weights=state.mesh.areas.copy())
    rows = linearize_material_constraints(state, probes, image, include_render=False)
    assert float(state.mesh.areas[1] / state.mesh.areas.sum() * .1) < .025
    index = rows.labels.index("face=1;mip=0;color_mean")
    assert rows.g[index] == pytest.approx(3)
    assert rows.g[rows.labels.index("face=0;mip=0;color_mean")] == -1


def square() -> MeshState:
    vertices = np.asarray([[0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0]], dtype=float)
    faces = np.asarray([[0, 1, 2], [0, 2, 3]], dtype=np.int64)
    uv = np.asarray([[.1, .15], [.8, .2], [.7, .9], [.2, .65]])
    return MeshState(make_mesh(vertices, faces), frozenset(), uv, faces.copy())


@pytest.mark.parametrize("mirrored", [False, True])
@pytest.mark.parametrize("pixels", [None, (32, 16)])
def test_geometry_all_rows_fd_and_adjoint(mirrored: bool, pixels: tuple[int, int] | None) -> None:
    state = square()
    if mirrored:
        state.uv[:, 0] = 1 - state.uv[:, 0]
    signs = np.full(2, -1 if mirrored else 1, dtype=np.int64)
    rows = linearize_geometry_constraints(state, orientations=signs, pixel_dimensions=pixels)
    rng = np.random.default_rng(17)
    direction = rng.normal(size=state.uv.shape) * .01
    multipliers = rng.normal(size=len(rows.g))
    assert float(rows.operator.matvec(direction.ravel()) @ multipliers) == pytest.approx(
        float(direction.ravel() @ rows.operator.rmatvec(multipliers)), abs=1e-12,
    )
    plus, minus = state.copy(), state.copy()
    plus.uv += 1e-5 * direction
    minus.uv -= 1e-5 * direction
    numeric = (linearize_geometry_constraints(plus, orientations=signs, pixel_dimensions=pixels).g
               - linearize_geometry_constraints(minus, orientations=signs, pixel_dimensions=pixels).g) / 2e-5
    assert np.allclose(rows.operator.matvec(direction.ravel()), numeric, atol=2e-8, rtol=2e-5)
    assert any("neighbor_log_density" in label for label in rows.labels)


def test_geometry_density_scope_is_explicit_and_shape_is_scale_invariant() -> None:
    state = square()
    members = np.asarray([0, 1], dtype=np.int64)
    full = linearize_geometry_constraints(state, member_ids=members)
    assert any("neighbor_log_density" in label for label in full.labels)
    assert full.metadata["density_scope"] == "all_original_adjacency"
    rows = linearize_geometry_constraints(state, member_ids=members, density_scope="same_member")
    assert not any("neighbor_log_density" in label for label in rows.labels)
    direction = state.uv - state.uv.mean(axis=0)
    assert rows.operator.matvec(direction.ravel())[:2] == pytest.approx([0, 0], abs=1e-12)
    scaled = state.copy()
    scaled.uv = .3 * state.uv
    changed = linearize_geometry_constraints(scaled, member_ids=members, density_scope="same_member")
    assert changed.g == pytest.approx(rows.g, abs=1e-12)


def test_stack_preserves_all_rows_and_sum_of_adjoints() -> None:
    state = triangle()
    image = material_texture()
    probes = feature(np.tile(np.asarray([.3, .3, .3, 1, 0, 0, 1, 1, .7, .2]), (6, 1)))
    material = linearize_material_constraints(state, probes, image, block="uv", include_render=False)
    geometry = linearize_geometry_constraints(state)
    stacked = stack_constraint_linearizations(material, geometry)
    multipliers = np.arange(len(stacked.g), dtype=float)
    expected = (material.operator.rmatvec(multipliers[:len(material.g)])
                + geometry.operator.rmatvec(multipliers[len(material.g):]))
    assert stacked.g == pytest.approx(np.r_[material.g, geometry.g])
    assert stacked.operator.rmatvec(multipliers) == pytest.approx(expected)
    assert stacked.operator.shape == (len(stacked.g), state.uv.size)


def test_missing_face_or_mip_coverage_fails_explicitly() -> None:
    state = square()
    image = material_texture()
    values = np.tile(image[0, 0], (2, 1))
    with pytest.raises(ValueError, match="every original face"):
        linearize_material_constraints(state, FeatureSamples(np.zeros(2, dtype=np.int64),
                                       np.full((2, 3), 1 / 3), values), image)
    with pytest.raises(ValueError, match="every requested mip"):
        linearize_material_constraints(state, FeatureSamples(np.asarray([0, 1], dtype=np.int64),
                                       np.full((2, 3), 1 / 3), values,
                                       mip_levels=np.asarray([0, 1], dtype=np.int64)), image)


def test_invalid_zero_reference_normal_cannot_be_restored_as_a_soft_constraint() -> None:
    state = triangle()
    with pytest.raises(ValueError, match="Fixed normal reference"):
        linearize_material_constraints(state, feature(np.zeros((6, 10))), material_texture())


@pytest.mark.parametrize("include_render", [False, True])
@pytest.mark.parametrize("compressed", [False, True])
def test_values_only_material_rows_equal_full_model_including_odd_mips_and_opacity(
    include_render: bool, compressed: bool,
) -> None:
    state, image = triangle(), material_texture()
    target = np.tile(np.asarray([.3, .4, .3, 1., .1, -.1, 1., .8, .7, .2]), (6, 1))
    probes = feature(target)
    limits = MaterialThresholds(important_multiplier=.7)
    full = linearize_material_constraints(state, probes, image, include_render=include_render,
                                         compressed_texture=compressed, important_faces=(0,), thresholds=limits)
    values = evaluate_material_constraints(state, probes, image, include_render=include_render,
                                           important_faces=(0,), thresholds=limits)
    assert isinstance(values, ConstraintValues) and not hasattr(values, "operator")
    assert values.labels == full.labels
    assert values.g == pytest.approx(full.g, abs=2e-12, rel=1e-13)
    assert values.metadata["evaluation_mode"] == "VALUES_ONLY"
    assert values.metadata["derivative_available"] is False
    assert values.metadata["shader_finite_difference_passes"] == 0
    assert values.metadata["nonlinear_encoded_gate_required"] is True
    assert "feature_gradient_nnz" not in values.metadata
    assert full.metadata["evaluation_mode"] == "FULL_LINEARIZATION"


def test_values_only_material_constructs_neither_sparse_operator_nor_render_fd(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state, image = triangle(), material_texture()
    probes = feature(np.tile(np.asarray([.3, .4, .3, 1., .1, -.1, 1., .8, .7, .2]), (6, 1)))
    original_render = constraints._render_values
    render_calls = [0]

    def render(values: FloatArray, matrix: FloatArray, samples: FeatureSamples) -> FloatArray:
        render_calls[0] += 1
        return original_render(values, matrix, samples)

    def forbidden(*args: object, **kwargs: object) -> None:
        del args, kwargs
        raise AssertionError("Values-only evaluation must not construct sparse sampler/constraint operators")

    monkeypatch.setattr(constraints, "_render_values", render)
    monkeypatch.setattr(constraints, "FrozenFeatureJacobian", forbidden)
    monkeypatch.setattr(constraints, "SparseFrozenTextureJacobian", forbidden)
    monkeypatch.setattr(constraints, "_SampleConstraintOperator", forbidden)
    monkeypatch.setattr(constraints, "csr_matrix", forbidden)
    values = evaluate_material_constraints(state, probes, image)
    assert render_calls[0] == 2  # Current shader and fixed reference only, no +/- channel probes.
    assert np.isfinite(values.g).all()


@pytest.mark.parametrize("mirrored", [False, True])
@pytest.mark.parametrize("same_member", [False, True])
def test_values_only_geometry_rows_equal_full_model_for_mirrors_zero_limits_and_members(
    mirrored: bool, same_member: bool,
) -> None:
    state = square()
    if mirrored:
        state.uv[:, 0] = 1 - state.uv[:, 0]
    signs = np.full(2, -1 if mirrored else 1, dtype=np.int64)
    members = np.asarray([0, 1], dtype=np.int64)
    scope = "same_member" if same_member else "all_original_adjacency"
    full = linearize_geometry_constraints(state, orientations=signs, member_ids=members, density_scope=scope,
                                         pixel_dimensions=(16, 8), mean_shape_limit=0,
                                         maximum_neighbor_density_ratio=1.)
    values = evaluate_geometry_constraints(state, orientations=signs, member_ids=members, density_scope=scope,
                                           pixel_dimensions=(16, 8), mean_shape_limit=0,
                                           maximum_neighbor_density_ratio=1.)
    assert values.labels == full.labels
    assert values.g == pytest.approx(full.g, abs=2e-12, rel=1e-13)
    assert values.metadata["evaluation_mode"] == "VALUES_ONLY" and not hasattr(values, "operator")
    assert "gradient_nnz" not in values.metadata


def test_values_only_geometry_never_builds_sparse_gradient(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*args: object, **kwargs: object) -> None:
        del args, kwargs
        raise AssertionError("Values-only geometry must not construct sparse gradients")

    monkeypatch.setattr(constraints, "csr_matrix", forbidden)
    values = evaluate_geometry_constraints(square(), pixel_dimensions=(16, 8))
    assert np.isfinite(values.g).all()


@pytest.mark.parametrize("evaluate", [evaluate_material_constraints, linearize_material_constraints])
def test_values_and_full_material_share_reference_and_probe_coverage_validation(evaluate: object) -> None:
    assert callable(evaluate)
    state, image = square(), material_texture()
    values = np.tile(image[0, 0], (2, 1))
    with pytest.raises(ValueError, match="every original face"):
        evaluate(state, FeatureSamples(np.zeros(2, dtype=np.int64), np.full((2, 3), 1 / 3), values), image)
    with pytest.raises(ValueError, match="Fixed normal reference"):
        evaluate(triangle(), feature(np.zeros((6, 10))), image)


@pytest.mark.parametrize("dimensions,mips", [
    ((5, 7), [0, 1, 2, 0, 1, 2]),
    ((17, 23), [0, 1, 1, 0, 1, 1]),
    ((1, 7), [0, 1, 2, 0, 1, 2]),
    ((8, 16), [0, 1, 2, 1, 2, 3]),
])
def test_sparse_texture_map_matches_full_odd_mips_and_both_adjoint_forms(
    dimensions: tuple[int, int], mips: list[int],
) -> None:
    state = triangle()
    rng = np.random.default_rng(17)
    image = rng.normal(size=dimensions + (10,))
    probes = feature(np.zeros((6, 10)))
    probes = FeatureSamples(probes.face_ids, probes.barycentric, probes.values,
                           mip_levels=np.asarray(mips, dtype=np.int64))
    full = FrozenFeatureJacobian(state, probes, image)
    compact = SparseFrozenTextureJacobian(state, probes, image)
    assert compact.values == pytest.approx(full.values, abs=1e-14)
    direction = rng.normal(size=image.shape)
    compressed_direction = direction.ravel()[compact.variable_indices]
    assert compact.forward(compressed_direction) == pytest.approx(full.forward(direction), abs=1e-14)
    derivative = rng.normal(size=probes.values.shape)
    scattered = np.zeros(image.size)
    scattered[compact.variable_indices] = compact.adjoint(derivative).ravel()
    assert scattered.reshape(image.shape) == pytest.approx(full.adjoint(derivative), abs=1e-14)
    assert float(compact.matvec(compressed_direction) @ derivative.ravel()) == pytest.approx(
        float(compressed_direction @ compact.rmatvec(derivative.ravel())), abs=1e-13,
    )
    plus = image.copy().ravel()
    minus = image.copy().ravel()
    plus[compact.variable_indices] += 1e-7 * compressed_direction
    minus[compact.variable_indices] -= 1e-7 * compressed_direction
    numeric = (SparseFrozenTextureJacobian(state, probes, plus.reshape(image.shape)).values
               - SparseFrozenTextureJacobian(state, probes, minus.reshape(image.shape)).values) / 2e-7
    assert compact.forward(compressed_direction) == pytest.approx(numeric, abs=2e-8, rel=2e-6)


def test_compressed_material_operator_preserves_variable_identity_and_zero_gradient_support() -> None:
    state = triangle()
    rng = np.random.default_rng(29)
    image = np.tile(material_texture()[0, 0], (17, 23, 1))
    probes = feature(np.zeros((6, 10)))
    target = FrozenFeatureJacobian(state, probes, image).values
    probes = feature(target)
    full = linearize_material_constraints(state, probes, image, include_render=False)
    compact = linearize_material_constraints(state, probes, image, compressed_texture=True,
                                            include_render=False)
    assert compact.variable_indices is not None
    assert compact.variable_shape == image.shape
    assert 0 < len(compact.variable_indices) < image.size
    assert compact.operator.shape[1] == len(compact.variable_indices)
    assert np.all(np.diff(compact.variable_indices) > 0)
    # All channels of every active pixel survive, even when normal, color and
    # ORM residual derivatives vanish. Support comes from B, never from GJ.
    assert compact.variable_indices.reshape(-1, 10) % 10 == pytest.approx(np.tile(np.arange(10),
                                                                        (len(compact.variable_indices) // 10, 1)))
    assert compact.g == pytest.approx(full.g, abs=2e-7)
    # Exact-zero norm residuals and constant-alpha maxima have many valid
    # subgradients; compare composed constraint derivatives away from those
    # ties, while the zero-gradient support check above remains independent.
    image += rng.uniform(-.001, .001, size=image.shape)
    target = FrozenFeatureJacobian(state, probes, image).values
    target[:, :3] -= rng.uniform(.02, .04, size=(6, 3))
    target[:, 4:7] += rng.uniform(-.03, .03, size=(6, 3))
    target[:, 7:10] -= rng.uniform(.01, .03, size=(6, 3))
    probes = feature(target)
    full = linearize_material_constraints(state, probes, image, include_render=True)
    compact = linearize_material_constraints(state, probes, image, compressed_texture=True,
                                            include_render=True)
    assert compact.variable_indices is not None
    assert compact.g == pytest.approx(full.g, abs=1e-11)
    direction = rng.normal(size=image.size)
    assert compact.operator.matvec(direction[compact.variable_indices]) == pytest.approx(
        full.operator.matvec(direction), abs=3e-9,
    )
    multipliers = rng.normal(size=len(full.g))
    scattered = np.zeros(image.size)
    scattered[compact.variable_indices] = compact.operator.rmatvec(multipliers)
    assert scattered == pytest.approx(full.operator.rmatvec(multipliers), abs=3e-9)
    assert "flat original HWC indices" in str(compact.metadata["variable_schema"])
    assert all(not isinstance(value, np.ndarray) for value in compact.metadata.values())
    with pytest.raises(ValueError, match="same original variable identity"):
        # Equal column counts alone cannot justify stacking different full
        # source variable identities.
        other = linearize_geometry_constraints(state)
        fake = ConstraintLinearization(other.g, other.operator, other.labels, other.metadata,
                                       np.arange(state.uv.size, dtype=np.int64), state.uv.shape)
        stack_constraint_linearizations(other, fake)
