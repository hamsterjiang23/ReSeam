import numpy as np

from reseam.mesh import make_mesh
from reseam.surface_match import barycentric_coordinates, match_surfaces, project_surface


def test_different_triangulations_match_surface_interior() -> None:
    # Negative side: two triangles. Positive side: four triangles with a new centre vertex.
    vertices = np.asarray(
        [
            [-1, 0, 0],
            [-1, 1, 0],
            [-1, 1, 1],
            [-1, 0, 1],
            [1, 0, 0],
            [1, 1, 0],
            [1, 1, 1],
            [1, 0, 1],
            [1, 0.5, 0.5],
        ],
        dtype=float,
    )
    faces = np.asarray([[0, 2, 1], [0, 3, 2], [4, 5, 8], [5, 6, 8], [6, 7, 8], [7, 4, 8]], dtype=np.int64)
    mesh = make_mesh(vertices, faces)
    result = match_surfaces(mesh, np.asarray([1.0, 0, 0]), 0)
    center = int(np.flatnonzero(result.source_ids == 8)[0])
    assert result.surface_error.max() < 1e-12
    assert result.cycle_error.max() < 1e-12
    assert result.vertex_error[center] > 0.1
    assert np.count_nonzero(result.barycentric[center] > 1e-8) >= 2
    assert result.normal_angle_degrees.max() < 1e-6


def test_projection_barycentric_and_asymmetric_surface() -> None:
    triangles = np.asarray([[[0, 0, 0], [1, 0, 0], [0, 1, 0]]], dtype=float)
    points, faces, distances = project_surface(np.asarray([[0.2, 0.3, 0.05], [2, 0, 0]]), triangles)
    bary = barycentric_coordinates(triangles[faces], points)
    assert np.allclose(bary.sum(axis=1), 1)
    assert np.all(bary >= -1e-12)
    assert np.allclose(np.einsum("fc,fcd->fd", bary, triangles[faces]), points)
    assert np.allclose(distances, [0.05, 1.0])
