"""Gradient, archival, and guarded-step contracts of the continuous proxy."""

import numpy as np
import pytest

from reseam.budget_optimize import (
    FeatureSamples,
    _first_box_step,
    _inverse_rest,
    _project_box_gradient,
    _shape_gradient,
    feature_loss_gradient,
    linear_mip_chain,
    optimize_uv_texture,
    safe_uv_step,
)
from reseam.joint_contacts import sheet_intersections
from reseam.mesh import make_mesh
from reseam.types import FloatArray, MeshState


def triangle() -> MeshState:
    vertices = np.asarray([[0, 0, 0], [1, 0, 0], [0, 1, 0]], dtype=float)
    faces = np.asarray([[0, 1, 2]], dtype=np.int64)
    uv = np.asarray([[0.16, 0.18], [0.83, 0.21], [0.23, 0.85]], dtype=float)
    return MeshState(make_mesh(vertices, faces), frozenset(), uv, faces.copy())


def samples(values: FloatArray, levels: list[int] | None = None) -> FeatureSamples:
    bary = np.asarray([[0.19, 0.37, 0.44], [0.47, 0.29, 0.24], [0.23, 0.61, 0.16]], dtype=float)
    return FeatureSamples(
        np.zeros(3, dtype=np.int64),
        bary,
        values,
        weights=np.asarray([0.2, 0.5, 0.3]),
        mip_levels=None if levels is None else np.asarray(levels, dtype=np.int64),
        channel_weights=np.asarray([0.7, 1.2]),
    )


@pytest.mark.parametrize("mips", [[0, 0, 0], [0, 1, 2]])
def test_texture_and_uv_gradients_match_finite_differences(mips: list[int]) -> None:
    state = triangle()
    rng = np.random.default_rng(17)
    image = rng.uniform(0.1, 0.9, size=(5, 7, 2))
    feature = samples(rng.uniform(0.1, 0.9, size=(3, 2)), mips)
    _, analytic_uv, analytic_image = feature_loss_gradient(state, feature, image)
    numeric_uv, numeric_image = np.zeros_like(state.uv), np.zeros_like(image)
    for index in np.ndindex(state.uv.shape):
        plus, minus = state.copy(), state.copy()
        plus.uv[index] += 1e-6
        minus.uv[index] -= 1e-6
        numeric_uv[index] = (
            feature_loss_gradient(plus, feature, image)[0] - feature_loss_gradient(minus, feature, image)[0]
        ) / 2e-6
    for index in np.ndindex(image.shape):
        plus, minus = image.copy(), image.copy()
        plus[index] += 1e-6
        minus[index] -= 1e-6
        numeric_image[index] = (
            feature_loss_gradient(state, feature, plus)[0] - feature_loss_gradient(state, feature, minus)[0]
        ) / 2e-6
    assert np.allclose(analytic_uv, numeric_uv, atol=2e-8, rtol=2e-5)
    assert np.allclose(analytic_image, numeric_image, atol=2e-8, rtol=2e-5)


def test_odd_complete_box_mips_preserve_constant_and_full_support() -> None:
    constant = np.full((5, 7, 2), 0.37)
    levels = linear_mip_chain(constant)
    assert [image.shape[:2] for image in levels] == [(5, 7), (2, 3), (1, 1)]
    assert all(np.allclose(image, 0.37) for image in levels)
    ramp = np.arange(35, dtype=float).reshape(5, 7, 1)
    assert linear_mip_chain(ramp)[-1][0, 0, 0] == pytest.approx(float(ramp.mean()))


@pytest.mark.parametrize("mirrored", [False, True])
def test_scale_normalized_shape_gradient_for_both_uv_orientations(mirrored: bool) -> None:
    state = triangle()
    if mirrored:
        state.uv[:, 0] = 1 - state.uv[:, 0]
    inverse = _inverse_rest(state)
    orientation = np.asarray([-1.0 if mirrored else 1.0])
    _, analytic = _shape_gradient(state, inverse, orientation)
    numeric = np.zeros_like(analytic)
    for index in np.ndindex(state.uv.shape):
        plus, minus = state.copy(), state.copy()
        plus.uv[index] += 1e-6
        minus.uv[index] -= 1e-6
        numeric[index] = (
            _shape_gradient(plus, inverse, orientation)[0]
            - _shape_gradient(minus, inverse, orientation)[0]
        ) / 2e-6
    assert np.allclose(analytic, numeric, rtol=2e-5, atol=2e-8)


def test_texture_blocks_reduce_loss_without_moving_uv_or_claiming_quality() -> None:
    state = triangle()
    feature = samples(np.asarray([[0.2, 0.8], [0.25, 0.7], [0.15, 0.9]]))
    texture = np.full((8, 8, 2), 0.5)
    result = optimize_uv_texture(
        state, feature, texture, seconds=3, iterations=12, optimize_uv=False, shape_weight=0,
    )
    assert result.final_proxy < result.initial_proxy * 0.01
    assert np.array_equal(result.state.uv, state.uv)
    assert not result.quality_valid
    assert result.best_valid_state is None and result.best_valid_texture is None
    assert np.array_equal(texture, np.full((8, 8, 2), 0.5))
    assert not np.shares_memory(result.texture, texture)


def test_direct_uv_blocks_reduce_exportable_sampling_error() -> None:
    state = triangle()
    x, y = np.meshgrid((np.arange(32) + 0.5) / 32, 1 - (np.arange(32) + 0.5) / 32)
    texture = np.stack((x, y), axis=-1)
    bary = np.asarray([[0.2, 0.3, 0.5], [0.4, 0.4, 0.2], [0.5, 0.1, 0.4]])
    targets = bary @ state.uv + [0.03, -0.02]
    feature = FeatureSamples(np.zeros(3, dtype=np.int64), bary, targets)
    result = optimize_uv_texture(
        state, feature, texture, seconds=3, iterations=100, texture_steps=0,
        shape_weight=0, uv_regularization=0,
    )
    assert result.final_proxy < result.initial_proxy * 0.01
    assert not np.array_equal(result.state.uv, state.uv)
    assert np.array_equal(result.texture, texture)
    assert np.array_equal(result.state.mesh.faces, state.mesh.faces)
    assert np.array_equal(result.state.uv_faces, state.uv_faces)


def test_fixed_vertices_and_illegal_candidate_are_never_moved() -> None:
    state = triangle()
    image = np.ones((8, 8, 2)) * 0.8
    feature = samples(np.zeros((3, 2)))
    original = state.uv.copy()

    def no_uv_change(candidate: MeshState) -> bool:
        return bool(np.array_equal(candidate.uv, original))

    result = optimize_uv_texture(
        state, feature, image, seconds=3, iterations=4,
        fixed_vertices=np.asarray([True, False, True]), legal_uv=no_uv_change,
    )
    assert np.array_equal(result.state.uv, original)
    assert result.final_proxy <= result.initial_proxy


def test_signed_normal_feature_channel_bounds_are_preserved() -> None:
    state = triangle()
    feature = samples(np.asarray([[-0.8, 0.7], [-0.6, 0.8], [-0.9, 0.9]]))
    texture = np.zeros((8, 8, 2))
    result = optimize_uv_texture(
        state, feature, texture, seconds=3, iterations=10, optimize_uv=False,
        channel_bounds=(np.asarray([-1.0, 0.0]), np.ones(2)),
    )
    assert np.min(result.texture[..., 0]) < -0.5
    assert np.min(result.texture[..., 1]) >= 0
    assert np.max(result.texture) <= 1


def test_quality_archive_is_independent_of_proxy_and_complete_copies() -> None:
    state = triangle()
    feature = samples(np.zeros((3, 2)))
    texture = np.full((8, 8, 2), 0.5)

    def initial_only_quality(candidate: MeshState, image: FloatArray) -> tuple[bool, float]:
        assert np.array_equal(candidate.mesh.faces, state.mesh.faces)
        valid = bool(np.allclose(image, 0.5, atol=0, rtol=0))
        return valid, 0.0 if valid else 1.0

    result = optimize_uv_texture(
        state, feature, texture, seconds=3, iterations=4, optimize_uv=False,
        quality_check=initial_only_quality, quality_check_interval=1,
    )
    assert result.quality_valid
    assert result.final_proxy < result.initial_proxy
    assert result.best_valid_texture is not None and np.array_equal(result.best_valid_texture, texture)
    assert result.best_valid_state is not None
    result.texture[:] = 0.9
    result.state.uv[:] = 0.1
    assert np.array_equal(result.best_valid_texture, texture)
    assert np.array_equal(result.best_valid_state.uv, state.uv)


def test_target_refresh_may_change_frames_but_not_probe_identity() -> None:
    state = triangle()
    feature = samples(np.zeros((3, 2)))
    calls: list[int] = []

    def refresh(candidate: MeshState) -> FeatureSamples:
        calls.append(candidate.version)
        return samples(np.full((3, 2), 0.2))

    result = optimize_uv_texture(
        state, feature, np.full((8, 8, 2), 0.5), seconds=3, iterations=3,
        target_update=refresh, optimize_uv=False,
    )
    assert len(calls) >= 4
    assert result.final_proxy < result.initial_proxy

    def change_probe(candidate: MeshState) -> FeatureSamples:
        del candidate
        return FeatureSamples(np.zeros(1, dtype=np.int64), np.full((1, 3), 1 / 3), np.zeros((1, 2)))

    with pytest.raises(ValueError, match="probe identities"):
        optimize_uv_texture(state, feature, np.zeros((8, 8, 2)), seconds=1, target_update=change_probe)


@pytest.mark.parametrize("mirrored", [False, True])
def test_safe_step_catches_even_root_with_positive_endpoint(mirrored: bool) -> None:
    state = triangle()
    state.uv = np.asarray([[0.1, 0.1], [0.5, 0.1], [0.1, 0.5]])
    if mirrored:
        state.uv[:, 0] = 1 - state.uv[:, 0]
    direction = -2 * (state.uv - state.uv[0])
    step = safe_uv_step(state, direction)
    assert step == pytest.approx(0.475)
    assert step < 0.5


def test_zero_budget_returns_initial_without_quality_claim() -> None:
    state = triangle()
    feature = samples(np.zeros((3, 2)))
    texture = np.full((8, 8, 2), 0.5)
    result = optimize_uv_texture(state, feature, texture, seconds=0)
    assert result.status == "TIME_BUDGET_EXHAUSTED"
    assert np.array_equal(result.state.uv, state.uv)
    assert np.array_equal(result.texture, texture)
    assert not result.quality_valid


def test_invalid_initial_geometry_gate_is_distinct_from_math_infeasibility() -> None:
    state = triangle()
    feature = samples(np.zeros((3, 2)))
    with pytest.raises(ValueError, match="INIT_FAILED"):
        optimize_uv_texture(state, feature, np.zeros((8, 8, 2)), seconds=1, legal_uv=lambda _: False)


def test_quality_check_crossing_deadline_does_not_enter_valid_archive(monkeypatch: pytest.MonkeyPatch) -> None:
    state = triangle()
    feature = samples(np.zeros((3, 2)))
    clock = [0.0]
    monkeypatch.setattr("reseam.budget_optimize.time.monotonic", lambda: clock[0])

    def late_quality(candidate: MeshState, image: FloatArray) -> tuple[bool, float]:
        del candidate, image
        clock[0] = 11.0
        return True, 0.0

    result = optimize_uv_texture(
        state, feature, np.zeros((8, 8, 2)), seconds=10, quality_check=late_quality,
    )
    assert result.status == "TIME_BUDGET_EXHAUSTED"
    assert not result.quality_valid
    assert result.best_valid_state is None
    assert result.history[0]["crossed_deadline"] is True


@pytest.mark.parametrize("mirrored", [False, True])
def test_touching_owner_box_can_move_interior_without_outward_backtracking(mirrored: bool) -> None:
    state = triangle()
    if mirrored:
        state.uv[:, 0] = 1 - state.uv[:, 0]
    x, y = np.meshgrid((np.arange(32) + 0.5) / 32, 1 - (np.arange(32) + 0.5) / 32)
    texture = np.stack((x, y), axis=-1)
    bary = np.asarray([[0.2, 0.3, 0.5], [0.4, 0.4, 0.2], [0.5, 0.1, 0.4]])
    feature = FeatureSamples(np.zeros(3, dtype=np.int64), bary, bary @ state.uv + [0.03, -0.02])
    lower = np.tile(state.uv.min(axis=0), (len(state.uv), 1))
    upper = np.tile(state.uv.max(axis=0), (len(state.uv), 1))
    original = state.uv.copy()
    gates: list[bool] = []

    def legal_owner_box(candidate: MeshState) -> bool:
        passed = bool(np.all(candidate.uv >= lower) and np.all(candidate.uv <= upper))
        gates.append(passed)
        return passed

    result = optimize_uv_texture(
        state, feature, texture, seconds=3, iterations=4, texture_steps=0,
        shape_weight=0, uv_regularization=0, uv_bounds=(lower, upper), legal_uv=legal_owner_box,
    )
    assert result.final_proxy < result.initial_proxy * 0.8
    assert not np.array_equal(result.state.uv, original)
    assert np.all(result.state.uv >= lower) and np.all(result.state.uv <= upper)
    assert np.array_equal(state.uv, original)
    blocks = [row for row in result.history if row["event"] == "block_update"]
    assert blocks and all(row["uv_backtracks"] == 0 for row in blocks)
    for row in blocks:
        projected = row["uv_box_projected_coordinates"]
        assert isinstance(projected, int) and projected > 0
    assert all(row["uv_owner_bounds_supplied"] for row in blocks)
    assert len(gates) == len(blocks) + 1 and all(gates)


def test_projected_bound_direction_has_the_correct_one_sided_derivative() -> None:
    state = triangle()
    x, y = np.meshgrid((np.arange(32) + 0.5) / 32, 1 - (np.arange(32) + 0.5) / 32)
    texture = np.stack((x, y), axis=-1)
    bary = np.asarray([[0.2, 0.3, 0.5], [0.4, 0.4, 0.2], [0.5, 0.1, 0.4]])
    feature = FeatureSamples(np.zeros(3, dtype=np.int64), bary, bary @ state.uv + [0.03, -0.02])
    lower = np.tile(state.uv.min(axis=0), (len(state.uv), 1))
    upper = np.tile(state.uv.max(axis=0), (len(state.uv), 1))
    energy, gradient, _ = feature_loss_gradient(state, feature, texture)
    direction = -_project_box_gradient(state.uv, gradient, lower, upper)
    assert np.count_nonzero(direction != -gradient) > 0
    trial = state.copy()
    trial.uv += 1e-5 * direction
    assert np.all(trial.uv >= lower) and np.all(trial.uv <= upper)
    analytic = float(np.sum(gradient * direction))
    numeric = (feature_loss_gradient(trial, feature, texture)[0] - energy) / 1e-5
    assert analytic < 0
    assert numeric == pytest.approx(analytic, rel=2e-5, abs=1e-9)


def test_equal_owner_bounds_freeze_one_coordinate_and_preserve_input_boxes() -> None:
    state = triangle()
    x, y = np.meshgrid((np.arange(16) + 0.5) / 16, 1 - (np.arange(16) + 0.5) / 16)
    texture = np.stack((x, y), axis=-1)
    bary = np.asarray([[0.2, 0.3, 0.5], [0.4, 0.4, 0.2], [0.5, 0.1, 0.4]])
    feature = FeatureSamples(np.zeros(3, dtype=np.int64), bary, bary @ state.uv + [0.03, 0.02])
    lower, upper = np.zeros_like(state.uv), np.ones_like(state.uv)
    lower[0, 0] = upper[0, 0] = state.uv[0, 0]
    original_lower, original_upper = lower.copy(), upper.copy()
    result = optimize_uv_texture(
        state, feature, texture, seconds=3, iterations=8, texture_steps=0,
        shape_weight=0, uv_regularization=0, uv_bounds=(lower, upper),
    )
    assert result.final_proxy < result.initial_proxy
    assert result.state.uv[0, 0] == state.uv[0, 0]
    assert not np.array_equal(result.state.uv, state.uv)
    assert np.array_equal(lower, original_lower) and np.array_equal(upper, original_upper)


@pytest.mark.parametrize("kind", ["shape", "nan", "inverted", "outside", "misses_start"])
def test_invalid_owner_bounds_fail_before_updates(kind: str) -> None:
    state = triangle()
    lower, upper = np.zeros_like(state.uv), np.ones_like(state.uv)
    if kind == "shape":
        lower = np.zeros(2)
    elif kind == "nan":
        lower[0, 0] = np.nan
    elif kind == "inverted":
        lower[0, 0], upper[0, 0] = 0.8, 0.2
    elif kind == "outside":
        upper[0, 0] = 1.1
    else:
        lower[0, 0] = state.uv[0, 0] + 0.01
    with pytest.raises(ValueError, match="owner bounds"):
        optimize_uv_texture(
            state, samples(np.zeros((3, 2))), np.zeros((8, 8, 2)),
            seconds=1, uv_bounds=(lower, upper),
        )


def test_first_box_limit_is_rounded_inward_and_intersects_flip_guard() -> None:
    state = triangle()
    lower, upper = np.zeros_like(state.uv), np.ones_like(state.uv)
    direction = np.zeros_like(state.uv)
    direction[1, 0] = 0.4
    step = _first_box_step(state.uv, direction, lower, upper, 1)
    exact = (1 - state.uv[1, 0]) / 0.4
    assert 0 < step < exact
    safe = safe_uv_step(state, direction, step)
    assert safe <= step
    trial = state.uv + safe * direction
    assert np.all(trial >= lower) and np.all(trial <= upper)


def same_point_fans() -> tuple[MeshState, FeatureSamples, FloatArray]:
    """Two fans have an authorized same-origin contact and distinct UV copies."""
    vertices = np.asarray([[0, 0, 0], [1, 0, 0], [0, 1, 0], [-1, 0, 0], [0, -1, 0]], dtype=float)
    faces = np.asarray([[0, 1, 2], [0, 3, 4]], dtype=np.int64)
    uv = np.asarray([[.5, .5], [.8, .5], [.5, .8], [.5, .5], [.2, .5], [.5, .2]])
    uv_faces = np.asarray([[0, 1, 2], [3, 4, 5]], dtype=np.int64)
    state = MeshState(make_mesh(vertices, faces), frozenset(), uv, uv_faces)
    bary = np.asarray([[.2, .3, .5], [.4, .4, .2], [.5, .1, .4]])
    ids = np.asarray([0, 0, 0, 1, 1, 1], dtype=np.int64)
    bary = np.tile(bary, (2, 1))
    target = np.einsum("nc,ncd->nd", bary, uv[uv_faces[ids]])
    target[:3] -= .03
    target[3:] += .03
    feature = FeatureSamples(ids, bary, target)
    x, y = np.meshgrid((np.arange(32) + .5) / 32, 1 - (np.arange(32) + .5) / 32)
    return state, feature, np.stack((x, y), axis=-1)


def legal_same_point_fans(state: MeshState) -> bool:
    provenance = (0, 1, 2, 0, 3, 4)
    return not sheet_intersections(state, np.zeros(2, dtype=np.int64), provenance)


def test_motion_ties_enable_legal_progress_without_welding_uv_identities() -> None:
    state, feature, texture = same_point_fans()
    assert legal_same_point_fans(state)
    _, gradient, _ = feature_loss_gradient(state, feature, texture)
    trial = state.copy()
    trial.uv -= 1e-3 * gradient
    assert not legal_same_point_fans(trial)
    original_uv_faces, original_faces = state.uv_faces.copy(), state.mesh.faces.copy()
    independent = optimize_uv_texture(
        state, feature, texture, seconds=3, iterations=12, texture_steps=0,
        shape_weight=0, uv_regularization=0, legal_uv=legal_same_point_fans,
    )
    tied = optimize_uv_texture(
        state, feature, texture, seconds=3, iterations=12, texture_steps=0,
        shape_weight=0, uv_regularization=0, legal_uv=legal_same_point_fans,
        motion_ties=np.asarray([10, 11, 12, 10, 13, 14], dtype=np.int64),
    )
    assert tied.final_proxy < independent.final_proxy * .8
    assert np.max(np.abs(independent.state.uv - state.uv)) < 1e-7
    assert np.max(np.abs(tied.state.uv - state.uv)) > 1e-3
    assert legal_same_point_fans(tied.state)
    assert np.array_equal(tied.state.uv[0], tied.state.uv[3])
    assert np.array_equal(tied.state.uv_faces, original_uv_faces)
    assert np.array_equal(tied.state.mesh.faces, original_faces)
    assert tied.state.uv_faces[0, 0] != tied.state.uv_faces[1, 0]
    assert len(tied.state.uv) == len(state.uv) == 6
    blocks = [row for row in tied.history if row["event"] == "block_update"]
    assert all(row["motion_tie_groups"] == 1 and row["motion_tied_copies"] == 2 for row in blocks)
    for row in blocks:
        projected = row["motion_projected_coordinates"]
        assert isinstance(projected, int) and projected > 0
    assert np.array_equal(state.uv, np.asarray([[.5, .5], [.8, .5], [.5, .8], [.5, .5], [.2, .5], [.5, .2]]))


@pytest.mark.parametrize("lock", ["fixed", "inactive"])
def test_partial_fixed_or_inactive_motion_tie_member_locks_the_whole_group(lock: str) -> None:
    state, feature, texture = same_point_fans()
    values = np.einsum("nc,ncd->nd", feature.barycentric, state.uv[state.uv_faces[feature.face_ids]]) + [.03, .02]
    feature = FeatureSamples(feature.face_ids, feature.barycentric, values)
    active, fixed = np.ones(6, dtype=bool), np.zeros(6, dtype=bool)
    if lock == "fixed":
        fixed[0] = True
    else:
        active[3] = False
    tied = optimize_uv_texture(
        state, feature, texture, seconds=3, iterations=6, texture_steps=0,
        shape_weight=0, uv_regularization=0, legal_uv=legal_same_point_fans,
        active_vertices=active, fixed_vertices=fixed,
        motion_ties=np.asarray([0, 1, 2, 0, 4, 5], dtype=np.int64),
    )
    assert np.array_equal(tied.state.uv[[0, 3]], state.uv[[0, 3]])
    assert not np.array_equal(tied.state.uv[[1, 2, 4, 5]], state.uv[[1, 2, 4, 5]])
    blocks = [row for row in tied.history if row["event"] == "block_update"]
    assert all(row["motion_locked_tie_groups"] == 1 for row in blocks)


def test_motion_tie_box_intersection_is_applied_before_group_cone_projection() -> None:
    state, feature, texture = same_point_fans()
    values = np.einsum("nc,ncd->nd", feature.barycentric, state.uv[state.uv_faces[feature.face_ids]]) + [.03, .02]
    feature = FeatureSamples(feature.face_ids, feature.barycentric, values)
    lower, upper = np.zeros_like(state.uv), np.ones_like(state.uv)
    lower[0, 0] = upper[0, 0] = .5
    lower[3, 0], upper[3, 0] = .3, .9
    tied = optimize_uv_texture(
        state, feature, texture, seconds=3, iterations=6, texture_steps=0,
        shape_weight=0, uv_regularization=0,
        uv_bounds=(lower, upper), motion_ties=np.asarray([0, 1, 2, 0, 4, 5], dtype=np.int64),
    )
    assert tied.state.uv[0, 0] == tied.state.uv[3, 0] == .5
    assert np.array_equal(tied.state.uv[0], tied.state.uv[3])
    assert tied.state.uv[0, 1] > .5001
    assert tied.final_proxy < tied.initial_proxy


def test_motion_tie_incompatible_boxes_are_rejected_without_relaxation() -> None:
    state, feature, texture = same_point_fans()
    lower, upper = np.zeros_like(state.uv), np.ones_like(state.uv)
    lower[0, 0], upper[3, 0] = .6, .4
    with pytest.raises(ValueError, match="INCOMPATIBLE_MOTION_TIE_BOUNDS"):
        optimize_uv_texture(
            state, feature, texture, seconds=1, uv_bounds=(lower, upper),
            motion_ties=np.asarray([0, 1, 2, 0, 4, 5], dtype=np.int64),
        )


def test_motion_ties_require_exact_initial_positions_even_for_tiny_differences() -> None:
    state, feature, texture = same_point_fans()
    state.uv[3, 0] += 1e-12
    with pytest.raises(ValueError, match="exactly equal"):
        optimize_uv_texture(
            state, feature, texture, seconds=1,
            motion_ties=np.asarray([0, 1, 2, 0, 4, 5], dtype=np.int64),
        )


@pytest.mark.parametrize("labels", [
    np.asarray([0, 1, 2, -1, 4, 5]),
    np.asarray([0., 1., 2., 0., 4., 5.]),
    np.asarray([0, 1, 2]),
    np.asarray([False, True, True, False, True, True]),
])
def test_invalid_motion_labels_fail_explicitly(labels: np.ndarray) -> None:
    state, feature, texture = same_point_fans()
    with pytest.raises(ValueError, match="nonnegative integer label"):
        optimize_uv_texture(state, feature, texture, seconds=1, motion_ties=labels)


def test_rejected_tied_candidate_returns_complete_original_uv_and_texture() -> None:
    state, feature, texture = same_point_fans()
    original = state.uv.copy()

    def reject_any_motion(candidate: MeshState) -> bool:
        return bool(np.array_equal(candidate.uv, original))

    result = optimize_uv_texture(
        state, feature, texture, seconds=3, iterations=3, texture_steps=0,
        shape_weight=0, uv_regularization=0, legal_uv=reject_any_motion,
        motion_ties=np.asarray([0, 1, 2, 0, 4, 5], dtype=np.int64),
    )
    assert np.array_equal(result.state.uv, original)
    assert np.array_equal(result.texture, texture)
    assert np.array_equal(result.state.uv_faces, state.uv_faces)
    assert np.array_equal(result.state.mesh.faces, state.mesh.faces)
    assert not result.quality_valid


def test_tied_motion_is_the_gradient_mean_and_matches_a_feasible_directional_difference() -> None:
    state, feature, texture = same_point_fans()
    values = np.einsum("nc,ncd->nd", feature.barycentric, state.uv[state.uv_faces[feature.face_ids]])
    values[:3] += [.025, .01]
    values[3:] += [.015, .03]
    feature = FeatureSamples(feature.face_ids, feature.barycentric, values)
    before, gradient, _ = feature_loss_gradient(state, feature, texture)
    expected = -(gradient[0] + gradient[3]) / 2
    result = optimize_uv_texture(
        state, feature, texture, seconds=3, iterations=1, texture_steps=0,
        shape_weight=0, uv_regularization=0,
        motion_ties=np.asarray([0, 1, 2, 0, 4, 5], dtype=np.int64),
    )
    block = next(row for row in result.history if row["event"] == "block_update")
    step = block["uv_step"]
    assert isinstance(step, float) and step > 0
    direction = (result.state.uv - state.uv) / step
    assert np.allclose(direction[0], expected, rtol=1e-10, atol=1e-12)
    assert np.array_equal(direction[0], direction[3])
    trial = state.copy()
    trial.uv += 1e-6 * direction
    derivative = (feature_loss_gradient(trial, feature, texture)[0] - before) / 1e-6
    assert derivative == pytest.approx(float(np.sum(gradient * direction)), rel=2e-5, abs=1e-9)
