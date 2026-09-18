import numpy as np
import pytest

from reseam.ground_truth import uv_reference_metrics


def test_reference_union_does_not_double_count_stacked_uv() -> None:
    vertices = np.array([[0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0]], dtype=float)
    faces = np.array([[0, 1, 2], [0, 2, 3], [0, 1, 2], [0, 2, 3]], dtype=np.int64)
    metrics = uv_reference_metrics(vertices, faces, vertices[faces, :2])
    assert metrics["unique_atlas_coverage"] == pytest.approx(1)
    assert metrics["summed_uv_triangle_area"] == pytest.approx(2)
    assert metrics["redundant_uv_area"] == pytest.approx(1)
    assert metrics["sd_mean_nondegenerate"] == pytest.approx(0)
    assert metrics["face_density_area_weighted_cv"] == pytest.approx(0)


def test_reference_degenerate_subset_is_explicit() -> None:
    vertices = np.array([[0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0]], dtype=float)
    faces = np.array([[0, 1, 2], [0, 2, 3]], dtype=np.int64)
    uv = vertices[faces, :2].copy()
    uv[1] = 0
    metrics = uv_reference_metrics(vertices, faces, uv)
    assert metrics["degenerate_uv_triangles"] == 1
    assert metrics["distortion_status"] == "NONDEGENERATE_SUBSET_ONLY"
    assert metrics["measured_source_area_fraction"] == pytest.approx(0.5)


def test_mirrored_reuse_is_reported_without_declaring_a_chart_invalid() -> None:
    square = np.array([[0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0]], dtype=float)
    vertices = np.vstack((square, square + [0, 0, 1]))
    faces = np.array([[0, 1, 2], [0, 2, 3], [4, 5, 6], [4, 6, 7]], dtype=np.int64)
    uv = vertices[faces, :2].copy()
    uv[2:, :, 0] = 1 - uv[2:, :, 0]
    metrics = uv_reference_metrics(vertices, faces, uv)
    assert metrics["negative_uv_triangles"] == 2
    assert metrics["unique_atlas_coverage"] == pytest.approx(1)
    assert metrics["sd_mean_nondegenerate"] == pytest.approx(0)
    assert metrics["distortion_status"] == "FULL_SURFACE"
