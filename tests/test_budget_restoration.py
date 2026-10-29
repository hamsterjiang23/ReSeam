"""Small actual-feature handshakes for the sequential joint Eq19 controller."""

from collections.abc import Callable
from dataclasses import replace

import numpy as np
import pytest

import reseam.budget_restoration as controller
from reseam.budget_constraints import (
    ConstraintLinearization,
    FrozenFeatureJacobian,
    linearize_geometry_constraints,
    linearize_material_constraints,
)
from reseam.budget_material import MaterialThresholds
from reseam.budget_optimize import FeatureSamples
from reseam.budget_restoration import (
    JointRestorationOperator,
    RestorationOptions,
    _uv_reduction,
    restore_budget_feasibility,
)
from reseam.budget_restore import EpigraphRestorationResult
from reseam.mesh import make_mesh
from reseam.types import FloatArray, IntArray, MeshState


def triangle() -> MeshState:
    faces = np.asarray([[0, 1, 2]], dtype=np.int64)
    vertices = np.asarray([[0., 0, 0], [1., 0, 0], [0., 1, 0]])
    uv = np.asarray([[.2, .2], [.8, .2], [.2, .8]])
    return MeshState(make_mesh(vertices, faces), frozenset(), uv, faces.copy())


def constant_texture(size: int = 8, red: float = .3) -> FloatArray:
    value = np.asarray([red, .2, .1, 1., 0., 0., 1., 1., .7, .1])
    return np.tile(value, (size, size, 1))


def probes(state: MeshState, texture: FloatArray, red_offset: float = 0.) -> FeatureSamples:
    bary = np.tile(np.asarray([[.2, .3, .5], [.4, .4, .2], [.5, .2, .3]]), (len(state.mesh.faces), 1))
    ids = np.repeat(np.arange(len(state.mesh.faces), dtype=np.int64), 3)
    initial = FeatureSamples(ids, bary, np.tile(texture[0, 0], (len(ids), 1)))
    values = FrozenFeatureJacobian(state, initial, texture).values.copy()
    values[:, 0] += red_offset
    return FeatureSamples(ids, bary, values)


def options(**updates: object) -> RestorationOptions:
    return replace(RestorationOptions(seconds=10., iterations=12, local_iterations=1000,
                                      include_render=False), **updates)  # type: ignore[arg-type]


def hard_h(state: MeshState) -> bool:
    edges = state.uv[state.uv_faces[:, 1:]] - state.uv[state.uv_faces[:, :1]]
    return bool(np.all(np.linalg.det(edges) > 0) and np.all(state.uv >= 0) and np.all(state.uv <= 1))


def test_real_per_face_constraints_choose_minmax_not_sample_weighted_average() -> None:
    base = triangle()
    xyz = np.vstack((base.mesh.vertices, base.mesh.vertices + [2., 0, 0]))
    faces = np.asarray([[0, 1, 2], [3, 4, 5]], dtype=np.int64)
    state = MeshState(make_mesh(xyz, faces), frozenset(), np.tile(base.uv, (2, 1)), faces.copy())
    image = constant_texture(1)
    ids = np.asarray([0] * 9 + [1], dtype=np.int64)
    bary = np.tile(np.asarray([1 / 3] * 3), (10, 1))
    targets = np.tile(image[0, 0], (10, 1))
    targets[:9, 0], targets[9, 0] = .2, .8
    features = FeatureSamples(ids, bary, targets)
    limits = MaterialThresholds(color_mean=.1, color_p95=.1, color_max=.1)
    result = restore_budget_feasibility(state, features, image, legal_uv=hard_h,
                                       options=options(optimize_uv=False, thresholds=limits))
    actual_red = float(result.texture[0, 0, 0])
    # Independent closed-form scalar maximum; repeated face probes cannot
    # hide the high-error face behind nine low-error samples.
    actual_xi = max(0., abs(actual_red - .2) / .1 - 1, abs(actual_red - .8) / .1 - 1)
    scalar_objective = actual_xi + .5e-4 * (actual_red - .3)**2
    assert actual_red == pytest.approx(.5, abs=4e-5)
    assert result.xi == pytest.approx(actual_xi, abs=1e-10)
    assert result.final_proxy == pytest.approx(scalar_objective, abs=1e-10)
    assert scalar_objective < 2.0001 < result.initial_proxy
    average_red = .9 * .2 + .1 * .8
    assert abs(actual_red - average_red) > .23
    assert not result.quality_valid and result.best_valid_state is None


def test_joint_operator_has_adjoint_and_matches_independent_nonlinear_fd() -> None:
    state = triangle()
    state.uv[1, 0] -= .1
    image = constant_texture()
    image[..., 0] = np.linspace(.1, .8, image.shape[1])[None, :]
    features = probes(state, image, .07)
    targets = features.values.copy()
    targets[:, 0] += np.asarray([.0, .01, .02])
    features = FeatureSamples(features.face_ids, features.barycentric, targets)
    config = options()
    reduction = _uv_reduction(state, image, config, None, None, None, None)
    uv = linearize_material_constraints(state, features, image, block="uv", include_render=False)
    tex = linearize_material_constraints(state, features, image, compressed_texture=True, include_render=False)
    geometry = linearize_geometry_constraints(state, pixel_dimensions=(8, 8))
    operator = JointRestorationOperator(uv.operator, tex.operator, geometry.operator, reduction)
    assert tex.variable_indices is not None
    rng = np.random.default_rng(17)
    direction = rng.normal(size=operator.shape[1]) * .01
    multipliers = rng.normal(size=operator.shape[0])
    assert float(operator.matvec(direction) @ multipliers) == pytest.approx(
        float(direction @ operator.rmatvec(multipliers)), abs=1e-11,
    )

    def values(eta: float) -> FloatArray:
        candidate = state.copy()
        candidate.uv += eta * reduction.expand(direction[:reduction.size])
        texture = image.copy()
        assert tex.variable_indices is not None
        texture.ravel()[tex.variable_indices] += eta * direction[reduction.size:]
        return np.concatenate((linearize_material_constraints(candidate, features, texture,
                                                             include_render=False).g,
                               linearize_geometry_constraints(candidate, pixel_dimensions=(8, 8)).g))

    # Exact-zero ORM/normal errors and tied aggregates are nonsmooth. Choose
    # the red feature and nonisotropic geometry rows for a central derivative.
    selected = np.asarray(["color_" in label or "geometry" in label for label in uv.labels + geometry.labels])
    numeric = (values(1e-5) - values(-1e-5)) / 2e-5
    assert np.allclose(operator.matvec(direction)[selected], numeric[selected], atol=2e-7, rtol=2e-5)


def test_joint_actual_descent_moves_uv_and_texture_and_preserves_inputs() -> None:
    state = triangle()
    image = constant_texture()
    image[..., 0] = np.linspace(.1, .8, image.shape[1])[None, :]
    features = probes(state, image, .2)
    original_uv, original_image, original_faces = state.uv.copy(), image.copy(), state.uv_faces.copy()
    result = restore_budget_feasibility(state, features, image, legal_uv=hard_h,
                                       options=options(iterations=4))
    assert np.max(np.abs(result.state.uv - original_uv)) > 1e-4
    assert np.max(np.abs(result.texture - original_image)) > 1e-3
    assert result.final_proxy < result.initial_proxy * .2
    assert hard_h(result.state)
    assert np.array_equal(state.uv, original_uv) and np.array_equal(image, original_image)
    assert np.array_equal(result.state.uv_faces, original_faces)
    assert result.metadata["full_final_validation"] is False


def test_default_render_proxy_is_present_in_the_actual_joint_restoration() -> None:
    state, image = triangle(), constant_texture()
    features = probes(state, image, .1)
    result = restore_budget_feasibility(state, features, image, legal_uv=hard_h,
                                       options=options(iterations=2, include_render=True))
    assert result.final_proxy < result.initial_proxy
    assert any("render_mean" in label for label in result.constraint_labels)
    approximation = result.metadata["material_approximation"]
    assert isinstance(approximation, dict)
    assert "fixed frame" in str(approximation["render_proxy"])
    assert not result.quality_valid


def test_rejected_joint_step_rolls_back_texture_and_all_original_identities() -> None:
    state = triangle()
    image = constant_texture()
    image[..., 0] = np.linspace(.1, .8, image.shape[1])[None, :]
    features = probes(state, image, .2)

    def reject_uv_change(candidate: MeshState) -> bool:
        return bool(np.array_equal(candidate.uv, state.uv))

    result = restore_budget_feasibility(state, features, image, legal_uv=reject_uv_change,
                                       options=options(iterations=3, maximum_backtracks=8))
    assert result.status == "LOCAL_STALLED"
    assert result.metadata["h_rejections"] == 8
    assert np.array_equal(result.state.uv, state.uv)
    assert np.array_equal(result.state.uv_faces, state.uv_faces)
    assert result.state.seams == state.seams and result.state.version == state.version
    assert np.array_equal(result.texture, image)
    assert result.final_proxy == result.initial_proxy


def test_fixed_y0_trust_offset_accumulates_across_outer_steps(monkeypatch: pytest.MonkeyPatch) -> None:
    state, image = triangle(), constant_texture()
    features = probes(state, image, .3)
    original_solve = controller.solve_epigraph_restoration
    offsets: list[FloatArray] = []

    def record_solve(*args: object, **kwargs: object) -> EpigraphRestorationResult:
        offset = kwargs["trust_offset"]
        assert isinstance(offset, np.ndarray)
        offsets.append(offset.copy())
        return original_solve(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(controller, "solve_epigraph_restoration", record_solve)
    result = restore_budget_feasibility(state, features, image, legal_uv=hard_h,
                                       options=options(iterations=4, optimize_uv=False, texture_trust_radius=.03))
    assert len(offsets) == 4
    assert np.all(offsets[0] == 0)
    assert np.max(offsets[1]) > .029 and np.max(offsets[3]) > .089
    actual = linearize_material_constraints(result.state, features, result.texture, include_render=False).g
    xi = max(0., float(np.max(actual)))
    fixed_center_objective = xi + .5e-4 * float(np.sum((result.texture - image)**2))
    assert result.final_proxy == pytest.approx(fixed_center_objective, abs=1e-10)
    assert np.array_equal(result.state.uv, state.uv)


def tied_triangle_state() -> MeshState:
    base = triangle()
    vertices = np.vstack((base.mesh.vertices, base.mesh.vertices + [2., 0, 0]))
    faces = np.asarray([[0, 1, 2], [3, 4, 5]], dtype=np.int64)
    return MeshState(make_mesh(vertices, faces), frozenset(), np.tile(base.uv, (2, 1)), faces.copy())


@pytest.mark.parametrize("lock", ["none", "fixed", "inactive", "equal_bounds"])
def test_explicit_tie_identity_bounds_and_group_lock_propagation(lock: str) -> None:
    state, image = tied_triangle_state(), constant_texture()
    image[..., 0] = np.linspace(.1, .8, image.shape[1])[None, :]
    features = probes(state, image, .2)
    labels = np.asarray([10, 11, 12, 10, 11, 12], dtype=np.int64)
    fixed = np.zeros(6, dtype=bool)
    active = np.ones(6, dtype=bool)
    lower, upper = np.zeros((6, 2)), np.ones((6, 2))
    if lock == "fixed":
        fixed[3] = True
    elif lock == "inactive":
        active[3] = False
    elif lock == "equal_bounds":
        lower[3] = upper[3] = state.uv[3]
    # A one-sided owner box on one copy must constrain its partner.
    upper[4, 0] = state.uv[4, 0] + .01
    result = restore_budget_feasibility(
        state, features, image, options=options(iterations=3), legal_uv=hard_h,
        motion_ties=labels, fixed_vertices=fixed, active_vertices=active, uv_bounds=(lower, upper),
    )
    assert np.array_equal(result.state.uv[:3], result.state.uv[3:])
    assert np.array_equal(result.state.uv_faces, state.uv_faces)
    assert result.state.uv[1, 0] <= upper[4, 0]
    if lock != "none":
        assert np.array_equal(result.state.uv[0], state.uv[0])
    assert result.metadata["motion_tie_groups"] == 3
    assert result.metadata["motion_tied_copies"] == 6


def test_ties_refuse_even_tiny_initial_coordinate_mismatch() -> None:
    state, image = tied_triangle_state(), constant_texture()
    state.uv[3, 0] += 1e-15
    with pytest.raises(ValueError, match="exactly equal"):
        restore_budget_feasibility(state, probes(state, image), image,
                                   motion_ties=np.asarray([0, 1, 2, 0, 1, 2], dtype=np.int64))


def test_geometry_quality_can_start_invalid_in_h_and_is_restored() -> None:
    state, image = triangle(), constant_texture()
    state.uv[1, 0], state.uv[2, 1] = .85, .4
    features = probes(state, image)
    assert hard_h(state)
    initial_kappa = .65 / .2
    initial_shape = .5 * (initial_kappa + 1 / initial_kappa) - 1
    result = restore_budget_feasibility(
        state, features, image, options=options(iterations=15, mean_shape_limit=.05), legal_uv=hard_h,
        channel_bounds=(image[0, 0].copy(), image[0, 0].copy()),
    )
    jacobian = np.column_stack((result.state.uv[1] - result.state.uv[0],
                               result.state.uv[2] - result.state.uv[0]))
    singular = np.linalg.svd(jacobian, compute_uv=False)
    kappa = singular[0] / singular[1]
    shape = .5 * (kappa + 1 / kappa) - 1
    assert initial_shape > .7
    assert shape < .051
    assert kappa < 2. and hard_h(result.state)
    assert result.final_proxy < result.initial_proxy * .01


def test_normal_targets_refresh_between_models_but_are_frozen_inside_solve() -> None:
    state, image = triangle(), constant_texture()
    image[..., 0] = np.linspace(.1, .8, image.shape[1])[None, :]
    features = probes(state, image, .16)
    refreshed_uv: list[FloatArray] = []

    def update(candidate: MeshState) -> FeatureSamples:
        refreshed_uv.append(candidate.uv.copy())
        values = features.values.copy()
        angle = float(candidate.uv[0, 0] - state.uv[0, 0])
        values[:, 4:7] = [np.sin(angle), 0., np.cos(angle)]
        return FeatureSamples(features.face_ids, features.barycentric, values)

    result = restore_budget_feasibility(state, features, image, options=options(iterations=3),
                                       legal_uv=hard_h, target_update=update)
    steps = [event for event in result.history if event["event"] == "joint_epigraph_step"]
    refreshes = [event for event in result.history if event["event"] == "target_refresh"]
    assert len(refreshed_uv) == len(refreshes) >= 2
    assert all(event["frozen_during_inner_solve"] is True for event in refreshes)
    assert any(not np.array_equal(uv, state.uv) for uv in refreshed_uv)
    assert all(event["model_gap_scope"] == "FROZEN_CONVEX_MODEL_ONLY" for event in steps)
    assert not result.quality_valid


def test_target_refresh_rejects_reference_material_or_identity_mutation() -> None:
    state, image = triangle(), constant_texture()
    features = probes(state, image)

    def mutate_reference(candidate: MeshState) -> FeatureSamples:
        del candidate
        values = features.values.copy()
        values[:, 0] += .1
        return FeatureSamples(features.face_ids, features.barycentric, values)

    with pytest.raises(ValueError, match="only localNormal"):
        restore_budget_feasibility(state, features, image, target_update=mutate_reference)


@pytest.mark.parametrize("cross_at", ["initial", "quality"])
def test_timeout_retains_state_without_quality_or_full_certificate(cross_at: str) -> None:
    state, image = triangle(), constant_texture()
    features = probes(state, image, .2)
    now = [0.]

    def clock() -> float:
        return now[0]

    def quality(candidate: MeshState, texture: FloatArray) -> tuple[bool, float]:
        candidate.uv[:] = 0
        texture[:] = 0
        now[0] = 11.
        return True, 0.

    callback: Callable[[MeshState, FloatArray], tuple[bool, float]] | None = quality
    if cross_at == "initial":
        callback = None
    result = restore_budget_feasibility(state, features, image, legal_uv=hard_h, clock=clock,
                                       deadline=0. if cross_at == "initial" else None,
                                       options=options(), quality_check=callback)
    assert result.status == "TIMEOUT_WITH_BEST"
    assert np.array_equal(result.state.uv, state.uv) and np.array_equal(result.texture, image)
    assert result.best_valid_state is None and not result.quality_valid
    assert result.best_quality_state is None and result.metadata["full_final_validation"] is False


def test_passing_development_callback_cannot_create_delivered_archive() -> None:
    state, image = triangle(), constant_texture()

    def quality(candidate: MeshState, texture: FloatArray) -> tuple[bool, float]:
        del candidate, texture
        return True, 0.

    result = restore_budget_feasibility(state, probes(state, image), image, legal_uv=hard_h,
                                       options=options(iterations=0), quality_check=quality)
    assert result.best_quality_state is not None and result.best_quality_texture is not None
    assert result.best_quality_violation == 0.
    assert result.best_valid_state is None and result.best_valid_texture is None
    assert not result.quality_valid and result.metadata["full_final_validation"] is False


@pytest.mark.parametrize("mirrored", [False, True])
def test_joint_controller_guards_even_determinant_root_before_positive_endpoint(
    mirrored: bool, monkeypatch: pytest.MonkeyPatch,
) -> None:
    state, image = triangle(), constant_texture()
    if mirrored:
        state.uv[:, 0] = 1 - state.uv[:, 0]
    features = probes(state, image, .2)
    original_solve = controller.solve_epigraph_restoration

    def cross_double_root(*args: object, **kwargs: object) -> EpigraphRestorationResult:
        actual = original_solve(*args, **kwargs)  # type: ignore[arg-type]
        direction = actual.step.copy()
        # Both UV axes reverse at eta=1, with a double zero-area root at
        # eta=.5. The endpoint has the initial orientation and valid borders.
        direction[:6] = (-2 * (state.uv - .5) * 8).ravel()
        return replace(actual, step=direction)

    monkeypatch.setattr(controller, "solve_epigraph_restoration", cross_double_root)
    sign = -1 if mirrored else 1

    def oriented_h(candidate: MeshState) -> bool:
        edges = candidate.uv[candidate.uv_faces[:, 1:]] - candidate.uv[candidate.uv_faces[:, :1]]
        return bool(np.all(sign * np.linalg.det(edges) > 0)
                    and np.all(candidate.uv >= 0) and np.all(candidate.uv <= 1))

    result = restore_budget_feasibility(state, features, image, legal_uv=oriented_h,
                                       options=options(iterations=1, uv_trust_pixels=10.))
    steps = [event for event in result.history if event["event"] == "joint_epigraph_step"]
    assert len(steps) == 1 and steps[0]["accepted"] is True
    assert steps[0]["accepted_eta"] == pytest.approx(.475)
    assert oriented_h(result.state)


def test_deadline_after_accepted_joint_step_retains_that_complete_best_state() -> None:
    state, image = triangle(), constant_texture()
    image[..., 0] = np.linspace(.1, .8, image.shape[1])[None, :]
    features = probes(state, image, .4)
    now = [0.]
    proposals: list[MeshState] = []

    def clock() -> float:
        return now[0]

    def gate(candidate: MeshState) -> bool:
        if not np.array_equal(candidate.uv, state.uv):
            proposals.append(candidate.copy())
            if len(proposals) == 2:
                now[0] = 11.
        return hard_h(candidate)

    result = restore_budget_feasibility(state, features, image, legal_uv=gate, clock=clock,
                                       options=options(iterations=5))
    assert result.status == "TIMEOUT_WITH_BEST" and len(proposals) == 2
    assert np.array_equal(result.state.uv, proposals[0].uv)
    assert np.max(np.abs(result.texture - image)) >= .099
    assert result.final_proxy < result.initial_proxy
    assert result.best_xi <= result.xi
    assert result.best_xi_state is not None and result.best_xi_texture is not None
    assert not result.quality_valid and result.metadata["full_final_validation"] is False


def test_accepted_value_only_trials_rebuild_full_texture_operator_once_per_next_step(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state, image = triangle(), constant_texture()
    features = probes(state, image, .3)
    original_linearize = controller.linearize_material_constraints
    blocks: list[str] = []
    texture_indices: list[IntArray] = []

    def record(*args: object, **kwargs: object) -> ConstraintLinearization:
        blocks.append(str(kwargs.get("block", "texture")))
        rows = original_linearize(*args, **kwargs)  # type: ignore[arg-type]
        if kwargs.get("block") == "texture":
            assert rows.variable_indices is not None
            texture_indices.append(rows.variable_indices.copy())
        return rows

    monkeypatch.setattr(controller, "linearize_material_constraints", record)
    result = restore_budget_feasibility(state, features, image, legal_uv=hard_h,
                                       options=options(iterations=4, optimize_uv=False, texture_trust_radius=.03))
    assert blocks.count("texture") == 4 and blocks.count("uv") == 4
    assert result.metadata["full_texture_linearizations"] == 4
    assert result.metadata["full_uv_linearizations"] == 4
    assert result.metadata["nonlinear_trial_value_evaluations"] == 4
    assert all(np.array_equal(indices, texture_indices[0]) for indices in texture_indices)
    assert result.final_proxy < result.initial_proxy


def test_nonlinear_rejected_trials_use_values_only_without_rebuilding_derivatives(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state, image = triangle(), constant_texture()
    features = probes(state, image, .2)
    original_solve = controller.solve_epigraph_restoration
    original_linearize = controller.linearize_material_constraints
    blocks: list[str] = []

    def adverse_step(*args: object, **kwargs: object) -> EpigraphRestorationResult:
        actual = original_solve(*args, **kwargs)  # type: ignore[arg-type]
        direction = np.zeros_like(actual.step)
        # Lowering red moves strictly away from the fixed .5 reference.
        direction[6::10] = -.05
        return replace(actual, step=direction)

    def record(*args: object, **kwargs: object) -> ConstraintLinearization:
        blocks.append(str(kwargs.get("block", "texture")))
        return original_linearize(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(controller, "solve_epigraph_restoration", adverse_step)
    monkeypatch.setattr(controller, "linearize_material_constraints", record)
    result = restore_budget_feasibility(state, features, image, legal_uv=hard_h,
                                       options=options(iterations=4, maximum_backtracks=5))
    assert result.status == "LOCAL_STALLED"
    assert blocks == ["texture", "uv"]
    assert result.metadata["nonlinear_trial_value_evaluations"] == 5
    assert result.metadata["full_texture_linearizations"] == 1
    assert np.array_equal(result.texture, image) and np.array_equal(result.state.uv, state.uv)
    assert result.final_proxy == result.initial_proxy
