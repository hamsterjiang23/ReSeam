"""Finite source rays reject close backsides and never silently use nearest points."""

import numpy as np

from reseam.agent_tools.pbr_projection import FiniteRayProjection
from reseam.mesh import make_mesh, state_from_corner_uv
from reseam.types import MeshState


def low_triangle() -> MeshState:
    vertices = np.array([[0., 0., 0.], [1., 0., 0.], [0., 1., 0.]])
    faces = np.array([[0, 1, 2]])
    surface = state_from_corner_uv(make_mesh(vertices, faces), vertices[faces, :2], corner_ids=faces)
    surface.corner_normals = np.tile([0., 0., 1.], (1, 3, 1))
    return surface


def test_thin_double_layer_chooses_front_not_closest_backside() -> None:
    low = low_triangle()
    vertices = np.concatenate((low.mesh.vertices + [0, 0, -.001], low.mesh.vertices + [0, 0, .01]))
    faces = np.array([[0, 2, 1], [3, 4, 5]])
    ray = FiniteRayProjection(low, vertices, faces)
    ids, bary, distances, valid = ray.query(np.array([0]), np.array([[.4, .3, .3]]))
    assert valid.tolist() == [True]
    assert ids.tolist() == [1]
    np.testing.assert_allclose(bary, [[.4, .3, .3]], atol=1e-7)
    np.testing.assert_allclose(distances, [.01], atol=1e-7)
    assert ray.audit()["nearest_point_fallback"] is False


def test_nearer_backface_hit_does_not_hide_later_front_hit() -> None:
    low = low_triangle()
    vertices = np.concatenate((low.mesh.vertices + [0, 0, .015], low.mesh.vertices + [0, 0, .01]))
    ray = FiniteRayProjection(low, vertices, np.array([[0, 2, 1], [3, 4, 5]]))
    ids, _, _, valid = ray.query(np.array([0]), np.array([[.4, .3, .3]]))
    assert valid.tolist() == [True] and ids.tolist() == [1]


def test_coincident_backface_does_not_remove_front_candidate() -> None:
    low = low_triangle()
    vertices = np.concatenate((low.mesh.vertices, low.mesh.vertices))
    ray = FiniteRayProjection(low, vertices, np.array([[0, 2, 1], [3, 4, 5]]))
    ids, _, _, valid = ray.query(np.array([0]), np.array([[.4, .3, .3]]))
    assert valid.tolist() == [True] and ids.tolist() == [1]


def test_backface_only_and_out_of_range_remain_invalid() -> None:
    low = low_triangle()
    for faces, offset, expected in ((np.array([[0, 2, 1]]), -.001, "backface_only"),
                                     (np.array([[0, 1, 2]]), -.2, "out_of_range")):
        ray = FiniteRayProjection(low, low.mesh.vertices + [0, 0, offset], faces)
        ids, _, _, valid = ray.query(np.array([0]), np.array([[.4, .3, .3]]))
        assert valid.tolist() == [False] and ids.tolist() == [-1]
        assert ray.counts[expected] == 1


def test_explicit_double_sided_material_keeps_first_back_hit_and_reports_side() -> None:
    low = low_triangle()
    vertices = np.concatenate((low.mesh.vertices + [0, 0, .015], low.mesh.vertices + [0, 0, .01]))
    ray = FiniteRayProjection(low, vertices, np.array([[0, 2, 1], [3, 4, 5]]), source_double_sided=True)
    ids, bary, distances, valid = ray.query(np.array([0]), np.array([[.4, .3, .3]]))
    assert valid.tolist() == [True] and ids.tolist() == [0]
    assert ray.last_hit_sides.tolist() == [-1]
    np.testing.assert_allclose(bary, [[.4, .3, .3]], atol=1e-7)
    np.testing.assert_allclose(distances, [.015], atol=1e-7)
    assert ray.audit()["two_sided_hit_count"] == 1
    assert ray.audit()["nearest_point_fallback"] is False


def test_double_sided_material_does_not_extend_finite_distance() -> None:
    low = low_triangle()
    ray = FiniteRayProjection(low, low.mesh.vertices + [0, 0, -.2], np.array([[0, 2, 1]]),
                              source_double_sided=True)
    ids, _, _, valid = ray.query(np.array([0]), np.array([[.4, .3, .3]]))
    assert valid.tolist() == [False] and ids.tolist() == [-1]
    assert ray.last_hit_sides.tolist() == [0]
    assert ray.counts["out_of_range"] == 1
