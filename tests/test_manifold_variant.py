"""Connectivity repairs preserve geometry and never invent coordinate welding."""

import numpy as np
import pytest

from reseam.agent_tools.manifold_variant import split_manifold_fans
from reseam.mesh import make_mesh


def test_nonmanifold_sheets_keep_exact_triangle_positions() -> None:
    vertices = np.array([[0., 0., 0.], [1., 0., 0.], [0., 1., 0.], [0., -1., 0.], [0., 0., 1.]])
    faces = np.array([[0, 1, 2], [1, 0, 3], [0, 1, 4]])
    result_v, result_f, record = split_manifold_fans(vertices, faces)
    assert len(record["cut_edges"]) == 1
    assert np.array_equal(result_v[result_f], vertices[faces])
    assert len(result_f) == len(faces)
    assert max(map(len, make_mesh(result_v, result_f).edge_faces)) <= 2
    assert record["original_face_for_new_face"] == [0, 1, 2]


def test_valid_adjacency_and_disconnected_coincident_vertices_preserved() -> None:
    vertices = np.array([[0., 0., 0.], [1., 0., 0.], [1., 1., 0.], [0., 1., 0.],
                         [0., 0., 0.], [1., 0., 0.], [1., 1., 0.]])
    faces = np.array([[0, 1, 2], [0, 2, 3], [4, 5, 6]])
    result_v, result_f, record = split_manifold_fans(vertices, faces)
    assert record["cut_edges"] == []
    assert result_f[0, 0] == result_f[1, 0]
    assert set(result_f[0]).isdisjoint(result_f[2])
    assert np.array_equal(result_v[result_f], vertices[faces])


def test_inconsistent_orientation_is_cut_without_flipping_face() -> None:
    vertices = np.array([[0., 0., 0.], [1., 0., 0.], [0., 1., 0.], [0., -1., 0.]])
    faces = np.array([[0, 1, 2], [0, 1, 3]])
    result_v, result_f, record = split_manifold_fans(vertices, faces)
    assert record["cut_edges"][0]["reason"] == "orientation"
    assert np.array_equal(result_v[result_f], vertices[faces])


def test_degenerate_geometry_is_not_silently_removed() -> None:
    with pytest.raises(ValueError, match="Degenerate input faces"):
        split_manifold_fans(np.zeros((3, 3)), np.array([[0, 1, 2]]))
