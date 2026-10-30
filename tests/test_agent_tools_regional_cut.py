"""Regional cuts split identities without changing any original triangle."""

import numpy as np
import pytest

from reseam.agent_tools.regional_cut import cut_region
from reseam.agent_tools.reparameterize import boundary_loop, harmonic_uv
from reseam.mesh import chart_labels, make_mesh, state_from_corner_uv
from reseam.types import MeshState


def cylinder() -> MeshState:
    angles = np.arange(4) * np.pi / 2
    ring = np.column_stack((np.cos(angles), np.sin(angles), np.zeros(4)))
    vertices = np.concatenate((ring, ring + [0, 0, 1]))
    faces = np.asarray([[vertex, (vertex + 1) % 4, vertex + 4] if side == 0
                        else [vertex + 4, (vertex + 1) % 4, (vertex + 1) % 4 + 4]
                        for vertex in range(4) for side in range(2)], dtype=np.int64)
    corners = np.tile(np.asarray([[0., 0.], [1., 0.], [0., 1.]]), (8, 1, 1))
    return state_from_corner_uv(make_mesh(vertices, faces), corners,
                                corner_ids=np.arange(24).reshape(8, 3))


def test_explicit_longitudinal_cut_turns_cylinder_into_disk() -> None:
    original = cylinder()
    before_faces = original.mesh.faces.copy()
    before_vertices = original.mesh.vertices.copy()
    cut = cut_region(original, tuple(range(8)), [[0, 4]])
    assert len(cut.surface.mesh.vertices) == 10
    assert len(boundary_loop(cut.surface.mesh.faces)) == 10
    assert np.array_equal(cut.original_vertex_ids[cut.surface.mesh.faces], original.mesh.faces)
    assert np.array_equal(cut.surface.mesh.vertices[cut.surface.mesh.faces],
                          original.mesh.vertices[original.mesh.faces])
    corners, _, _ = harmonic_uv(cut.surface, tuple(range(8)))
    areas = np.linalg.det(np.stack((corners[:, 1] - corners[:, 0], corners[:, 2] - corners[:, 0]), axis=1))
    assert np.all(areas > 0)
    assert np.array_equal(original.mesh.faces, before_faces)
    assert np.array_equal(original.mesh.vertices, before_vertices)


def test_empty_cuts_discard_old_uv_fragmentation() -> None:
    original = cylinder()
    assert len(np.unique(chart_labels(original))) == 8
    cut = cut_region(original, tuple(range(8)), [])
    assert len(cut.surface.mesh.vertices) == 8
    assert len(np.unique(chart_labels(cut.surface))) == 1
    with pytest.raises(ValueError, match="MULTIPLE_BOUNDARIES"):
        harmonic_uv(cut.surface, tuple(range(8)))


def test_coincident_original_vertices_are_never_welded() -> None:
    vertices = np.asarray([[0., 0, 0], [1, 0, 0], [0, 1, 0]] * 2)
    faces = np.arange(6).reshape(2, 3)
    original = state_from_corner_uv(make_mesh(vertices, faces), vertices[faces, :2],
                                    corner_ids=faces)
    cut = cut_region(original, (0, 1), [])
    assert len(cut.surface.mesh.vertices) == 6
    assert set(cut.original_vertex_ids) == set(range(6))
    assert len(np.unique(chart_labels(cut.surface))) == 2


@pytest.mark.parametrize("edges,reason", [([[0, 0]], "EDGE_PAIRS"), ([[0, 6]], "UNKNOWN"),
                                         ([[0, 4], [4, 0]], "DUPLICATE"),
                                         ([[True, 4]], "EDGE_PAIRS")])
def test_invalid_edge_identity_rejects(edges: object, reason: str) -> None:
    with pytest.raises(ValueError, match=reason):
        cut_region(cylinder(), tuple(range(8)), edges)


def test_cut_outside_selected_faces_rejects() -> None:
    with pytest.raises(ValueError, match="OUTSIDE_REGION"):
        cut_region(cylinder(), (0,), [[2, 6]])
