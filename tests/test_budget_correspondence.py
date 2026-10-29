"""Finite correspondence evidence cannot substitute for full UV validation."""

import time

import numpy as np
import pytest

from reseam.budget_correspondence import update_uv_correspondences
from reseam.budget_state import BudgetState, fingerprint
from reseam.joint_types import ReuseGroup
from reseam.mesh import make_mesh, state_from_corner_uv


def _errors(diagnostics: dict[str, object]) -> list[str]:
    errors = diagnostics["errors"]
    assert isinstance(errors, list)
    assert all(isinstance(error, str) for error in errors)
    return errors


def mirrored_panels() -> BudgetState:
    vertices = np.asarray([
        [-1., 0, 0], [-1, 1, 0], [-1, 1, 1], [-1, 0, 1],
        [1., 0, 0], [1, 1, 0], [1, 1, 1], [1, 0, 1], [1, .5, .5],
    ])
    faces = np.asarray([[0, 2, 1], [0, 3, 2], [4, 5, 8], [5, 6, 8], [6, 7, 8], [7, 4, 8]])
    mesh = make_mesh(vertices, faces)
    surface = state_from_corner_uv(mesh, vertices[faces, 1:], corner_ids=faces)
    return BudgetState(surface, (ReuseGroup("panels", (0, 1), (2, 3, 4, 5), (1., 0, 0), 0,
                                          first_orientation=-1, second_orientation=1),))


def test_uv_induced_correspondences_support_different_triangulations_and_identity() -> None:
    state = mirrored_panels()
    state.texture = np.ones((4, 4, 9))
    state.evidence["prior_gate"] = True
    before = fingerprint(state)
    result = update_uv_correspondences(state)
    assert result.status == "PROPOSED_NOT_VALIDATED"
    assert result.state is not None
    assert len(result.state.correspondences) == 42
    assert result.diagnostics["source_faces_covered"] == [0, 1, 2, 3, 4, 5]
    assert result.diagnostics["maximum_position_error_fraction"] == pytest.approx(0)
    assert result.diagnostics["maximum_cycle_error_fraction"] == pytest.approx(0)
    assert result.diagnostics["global_bijection_proven"] is False
    assert result.diagnostics["independent_pair_count"] is None
    for item in result.state.correspondences:
        a = np.asarray(item.source_barycentric) @ state.surface.uv[state.surface.uv_faces[item.source_face]]
        b = np.asarray(item.target_barycentric) @ state.surface.uv[state.surface.uv_faces[item.target_face]]
        assert np.allclose(a, b)
    assert result.records[0]["source_vertex_id"] == 0
    assert result.records[3]["source_vertex_id"] is None
    assert result.state.texture is None and result.state.evidence == {}
    assert result.state.version == state.version + 1
    assert fingerprint(state) == before


def test_bad_uv_induced_geometry_rejects_complete_update_without_mutation() -> None:
    state = mirrored_panels()
    second_vertices = np.unique(state.surface.uv_faces[2:])
    state.surface.uv[second_vertices, 0] += .1
    before = fingerprint(state)
    result = update_uv_correspondences(state, position_fraction=.005)
    assert result.state is None
    assert result.status == "CORRESPONDENCE_REJECTED"
    assert "SAMPLED_GEOMETRY_INCOMPATIBLE" in _errors(result.diagnostics)
    unmatched = result.diagnostics["unmatched_domain_samples"]
    assert isinstance(unmatched, int) and unmatched > 0
    assert fingerprint(state) == before


def test_outside_domain_has_no_nearest_fallback_and_faces_remain_original() -> None:
    state = mirrored_panels()
    state.surface.uv[np.unique(state.surface.uv_faces[2:]), 0] += 2
    before = fingerprint(state)
    result = update_uv_correspondences(state)
    assert result.state is None
    assert result.diagnostics["association_count"] == 0
    assert result.diagnostics["unmatched_domain_samples"] == 42
    assert all(r["target_face"] is None for r in result.records)
    assert "INCOMPLETE_SOURCE_FACE_COVERAGE" in _errors(result.diagnostics)
    assert fingerprint(state) == before


def test_expired_deadline_and_invalid_limits_do_not_mutate_state() -> None:
    state = mirrored_panels()
    before = fingerprint(state)
    with pytest.raises(TimeoutError, match="BUDGET_EXHAUSTED"):
        update_uv_correspondences(state, deadline=time.monotonic() - 1)
    with pytest.raises(ValueError, match="Positive finite"):
        update_uv_correspondences(state, position_fraction=0)
    assert fingerprint(state) == before


def test_multiple_distinct_surface_hits_are_not_resolved_by_nearest_selection() -> None:
    initial = mirrored_panels()
    vertices = np.vstack((initial.surface.mesh.vertices,
                          np.array([[1., 3, 0], [1, 4, 0], [1, 4, 1], [1, 3, 1]])))
    faces = np.vstack((initial.surface.mesh.faces, np.array([[9, 10, 11], [9, 11, 12]])))
    mesh = make_mesh(vertices, faces)
    uv = vertices[:, 1:].copy()
    uv[9:, 0] -= 3
    surface = state_from_corner_uv(mesh, uv[faces], corner_ids=faces)
    state = BudgetState(surface, (ReuseGroup("ambiguous", (0, 1), tuple(range(2, 8)),
                                          (1., 0, 0), 0, -1, 1),))
    before = fingerprint(state)
    result = update_uv_correspondences(state)
    assert result.state is None
    assert "AMBIGUOUS_TARGET_UV" in _errors(result.diagnostics)
    assert fingerprint(state) == before
