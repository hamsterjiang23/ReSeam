"""Density restoration must preserve shapes, face identities, and parent UVs."""

import time

import numpy as np
import pytest

from reseam.agent_tools.density import balance_density, scale_selected_owners
from reseam.agent_tools.geometry import geometry_report
from reseam.budget_state import BudgetState
from reseam.mesh import make_mesh, state_from_corner_uv
from reseam.validation import jacobians


def test_owner_scaling_fixes_density_without_fragmentation() -> None:
    mesh = make_mesh(np.array([[0., 0, 0], [1., 0, 0], [0., 1, 0], [1., 1, 0]]),
                     np.array([[0, 1, 2], [1, 3, 2]]))
    corner = np.array([[[0., 0], [.1, 0], [0, .1]], [[.5, .5], [.5, 1.5], [-.5, 1.5]]])
    surface = state_from_corner_uv(mesh, corner, corner_ids=np.arange(6).reshape(2, 3))
    state = BudgetState(surface)
    original = surface.uv.copy()
    result, record = balance_density(state, 2, 128, 4, 4, time.monotonic() + 10)
    assert record["status"] == "SCALED"
    assert np.array_equal(state.surface.uv, original)
    assert np.array_equal(result.surface.mesh.faces, mesh.faces)
    report = geometry_report(result, {"resolution": 128, "maximum_anisotropy": 3,
                                      "mean_shape_limit": 2, "tail_shape_limit": 2})
    assert report["valid"] is True
    assert report["chart_count"] == 2
    before = np.linalg.svd(jacobians(surface), compute_uv=False)
    after = np.linalg.svd(jacobians(result.surface), compute_uv=False)
    np.testing.assert_allclose(before[:, 0] / before[:, 1], after[:, 0] / after[:, 1])


def test_density_rejects_unsolvable_same_owner_without_changes() -> None:
    mesh = make_mesh(np.array([[0., 0, 0], [1., 0, 0], [0., 1, 0], [1., 1, 0]]),
                     np.array([[0, 1, 2], [1, 3, 2]]))
    corner = np.array([[[0., 0], [1., 0], [0, 1]], [[1., 0], [10., 10], [0, 1]]])
    surface = state_from_corner_uv(mesh, corner, corner_ids=mesh.faces)
    state = BudgetState(surface)
    result, record = balance_density(state, 2, 128, 4, 4, time.monotonic() + 10)
    assert record["status"] == "REJECTED"
    assert record["same_owner_conflicts"] == [[0, 1]]
    assert np.array_equal(result.surface.uv, surface.uv)


def test_explicit_density_prior_is_uniform_and_keeps_parent() -> None:
    mesh = make_mesh(np.array([[0., 0, 0], [1., 0, 0], [0., 1, 0], [1., 1, 0]]),
                     np.array([[0, 1, 2], [1, 3, 2]]))
    surface = state_from_corner_uv(mesh, mesh.vertices[mesh.faces, :2], corner_ids=mesh.faces)
    state = BudgetState(surface)
    before = surface.uv.copy()
    scaled = scale_selected_owners(state, {"0": 1.5})
    np.testing.assert_allclose(jacobians(scaled.surface), jacobians(surface) * 1.5)
    np.testing.assert_array_equal(surface.uv, before)
    with pytest.raises(ValueError, match="CONFLICTING"):
        scale_selected_owners(state, {"0": 1.5, "1": 2.})
