"""Analytic topology/shape cases and independent AO visibility regressions."""

import math

import numpy as np
import pytest

from baseline.paper_metrics import AOScene, ao_score, chart_metrics, resampled_loop
from reseam.mesh import make_mesh, state_from_corner_uv
from reseam.types import MeshState


def square() -> MeshState:
    vertices = np.array([[0., 0., 0.], [1., 0., 0.], [1., 1., 0.], [0., 1., 0.]])
    faces = np.array([[0, 1, 2], [0, 2, 3]], dtype=np.int64)
    mesh = make_mesh(vertices, faces)
    return state_from_corner_uv(mesh, vertices[faces, :2], seam_ids=frozenset())


def test_square_analytic_shape_and_jaggedness() -> None:
    result = chart_metrics(square())
    assert result["chart_count"] == 1
    assert result["non_disk_fraction"] == 0
    assert result["compactness"] == pytest.approx(math.pi / 4)
    assert result["convexity"] == 1
    assert result["angular_quality"] == pytest.approx(1)
    assert result["jaggedness"] == pytest.approx(4 * math.sqrt(2) * .005 / 800)
    assert result["invalid_boundary_components"] == 0


def test_annulus_euler_and_shape() -> None:
    xy = np.array([[-2., -2.], [2., -2.], [2., 2.], [-2., 2.],
                   [-1., -1.], [1., -1.], [1., 1.], [-1., 1.]])
    faces = np.array([face for i in range(4) for face in
                      [(i, (i + 1) % 4, 4 + (i + 1) % 4), (i, 4 + (i + 1) % 4, 4 + i)]])
    mesh = make_mesh(np.c_[xy, np.zeros(8)], faces)
    result = chart_metrics(state_from_corner_uv(mesh, xy[faces], seam_ids=frozenset()))
    assert result["non_disk_fraction"] == 1
    assert result["compactness"] == pytest.approx(math.pi / 12)
    assert result["convexity"] == pytest.approx(.75)
    charts = result["charts"]
    assert isinstance(charts, list)
    assert charts[0]["boundary_loops"] == 2 and charts[0]["euler_characteristic"] == 0


def test_overlapping_copies_preserve_independent_uv_identity() -> None:
    source = square()
    vertices = np.r_[source.mesh.vertices, source.mesh.vertices + [2, 0, 0]]
    faces = np.r_[source.mesh.faces, source.mesh.faces + 4]
    mesh = make_mesh(vertices, faces)
    uv = np.r_[source.uv[source.uv_faces], source.uv[source.uv_faces]]
    result = chart_metrics(state_from_corner_uv(mesh, uv, seam_ids=frozenset()))
    assert result["chart_count"] == 2
    assert result["non_disk_fraction"] == 0
    assert result["compactness"] == pytest.approx(math.pi / 4)


def test_closed_chart_has_no_invented_boundary_metrics() -> None:
    v = np.array([[0., 0., 0.], [1., 0., 0.], [0., 1., 0.], [0., 0., 1.]])
    f = np.array([[0, 2, 1], [0, 1, 3], [1, 2, 3], [2, 0, 3]])
    mesh = make_mesh(v, f)
    result = chart_metrics(state_from_corner_uv(mesh, v[f, :2], seam_ids=frozenset()))
    assert result["non_disk_fraction"] == 1
    assert result["compactness"] is None and result["convexity"] is None
    assert result["jaggedness"] is None
    assert result["shape_valid_charts"] == 0


def test_resampling_handles_zero_edges_and_point_cap() -> None:
    points = np.array([[0., 0.], [0., 0.], [100., 0.], [100., 100.], [0., 100.]])
    assert resampled_loop(points).shape == (4096, 2)
    assert resampled_loop(np.zeros((4, 2))).shape == (0, 2)


def test_ao_comparison_ties_and_order() -> None:
    assert ao_score(np.zeros(5, dtype=np.int64), np.zeros(7, dtype=np.int64)) == .5
    assert ao_score(np.array([8, 8]), np.array([0, 1])) == 1
    assert ao_score(np.array([0, 0]), np.array([7, 8])) == 0
    assert ao_score(np.array([0, 4, 8]), np.array([0, 4, 8])) == .5


def test_open_plane_has_half_ao_and_empty_cut_is_na() -> None:
    surface = square()
    scene = AOScene(surface.mesh, samples=24)
    result = scene.evaluate(surface)
    assert result["seam_ao"] is None
    assert result["seam_ao_status"] == "NOT_APPLICABLE_NO_EXPLICIT_CUTS"
    assert result["boundary_ao"] == .5
    assert result["reference_mean_occlusion"] == 0


def test_ao_detects_occluding_sheet_and_prefers_hidden_seam() -> None:
    xy = np.array([[-10., -10.], [10., -10.], [10., 10.], [-10., 10.]])
    vertices = np.r_[np.c_[xy, np.zeros(4)], np.c_[xy, np.ones(4)]]
    faces = np.array([[0, 1, 2], [0, 2, 3], [4, 6, 5], [4, 7, 6]])
    mesh = make_mesh(vertices, faces)
    scene = AOScene(mesh, samples=64)
    cuts = tuple(i for i, edge in enumerate(mesh.edges) if edge in {(0, 2), (4, 6)})
    seam = scene.hits(cuts)
    reference = scene.hits(tuple(range(len(mesh.edges))))
    assert float(seam.mean()) / 8 > .75
    assert ao_score(seam, reference) > .6
