from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from reseam.budget_texture import (
    ReferenceSurface,
    bake_atlas,
    build_rgba8_mips,
    decode_rgba8,
    encode_rgba8,
    evaluate_atlas,
    mip_dimensions,
    sample_rgba8,
    shared_color_error,
    texture_payload_bytes,
)
from reseam.mesh import make_mesh
from reseam.surface_match import project_surface
from reseam.types import MeshState


def _reference(alpha: int = 255) -> ReferenceSurface:
    vertices = np.asarray([[0, 0, 0], [1, 0, 0], [0, 1, 0]], dtype=float)
    faces = np.asarray([[0, 1, 2]], dtype=np.int64)
    uv = np.asarray([[[0, 0], [1, 0], [0, 1]]], dtype=float)
    image = np.full((4, 4, 4), [128, 64, 32, alpha], dtype=np.uint8)
    return ReferenceSurface(vertices, faces, uv, image)


def _state() -> MeshState:
    reference = _reference()
    mesh = make_mesh(reference.vertices, reference.faces)
    return MeshState(mesh, frozenset(), reference.corner_uv[0].copy(), reference.faces.copy())


def test_complete_mip_block_payload() -> None:
    assert mip_dimensions(7, 5) == ((7, 5), (3, 2), (1, 1))
    assert texture_payload_bytes(1024, 1024, 4, 4, 16) == 1_398_128
    assert texture_payload_bytes(512, 512, 4, 4, 16) == 349_552
    assert texture_payload_bytes(7, 5, 4, 4, 16) == 96
    assert texture_payload_bytes(7, 5) == (35 + 6 + 1) * 4
    with pytest.raises(ValueError):
        texture_payload_bytes(0, 1)


def test_shared_color_conflict_is_conditional_variance() -> None:
    values = np.asarray([[0, 0, 0], [1, 0, 0]], dtype=float)
    weights = np.asarray([1, 3], dtype=float)
    mean, energy = shared_color_error(values, weights)
    assert np.allclose(mean, [0.75, 0, 0])
    assert energy == pytest.approx(0.75)
    assert np.sum(weights[:, None] * (values - [0, 0, 0]) ** 2) > energy


def test_rgba8_png_and_linear_mips(tmp_path: Path) -> None:
    linear = np.asarray([[[0, 0, 0, 1], [1, 1, 1, 1]], [[0, 0, 0, 1], [1, 1, 1, 1]]], dtype=float)
    encoded = encode_rgba8(linear)
    path = tmp_path / "encoded.png"
    Image.fromarray(encoded).save(path)
    with Image.open(path) as image:
        recovered = np.asarray(image)
    assert np.array_equal(encoded, recovered)
    mips = build_rgba8_mips(encoded)
    assert mips[-1].shape == (1, 1, 4)
    assert decode_rgba8(mips[-1])[0, 0, 0] == pytest.approx(0.5, abs=0.003)


def test_bilinear_interpolates_linear_rgb_before_filtering() -> None:
    image = np.asarray([[[0, 0, 0, 255], [255, 255, 255, 255]]], dtype=np.uint8)
    sample = sample_rgba8(image, np.asarray([[0.5, 0.5], [0.25, 0.5]], dtype=float))
    assert np.allclose(sample[0], [0.5, 0.5, 0.5, 1])
    assert np.allclose(sample[1], [0, 0, 0, 1])


def test_reference_projection_and_roi_keep_original_face_identity() -> None:
    reference = _reference()
    points = np.asarray([[0.2, 0.3, 0], [0.2, 0.3, 0.5]])
    result = reference.sample(points, 0.01)
    assert result.valid.tolist() == [True, False]
    assert result.distances.tolist() == pytest.approx([0, 0.5])
    subset = reference.restrict_bounds(np.asarray([[0, 0, 0], [0.5, 0.5, 0]]), 0.01)
    assert subset.sample(points[:1], 0.01).target_faces.tolist() == [0]


def test_missing_alpha_is_invalid_not_zero_error_success() -> None:
    result = _reference(alpha=0).sample(np.asarray([[0.2, 0.2, 0]]), 0.01)
    assert not result.valid.any()


def test_bake_and_evaluate_remains_base_color_only(tmp_path: Path) -> None:
    state, reference = _state(), _reference()
    atlas = bake_atlas(state, reference, 16, 16, tmp_path / "bake", padding_pixels=3)
    assert atlas.metadata["payload_bytes"] == texture_payload_bytes(16, 16)
    assert (tmp_path / "bake" / "BaseColor_mip4.png").exists()
    result = evaluate_atlas(state, reference, atlas, mip_levels=(0, 1))
    assert result["base_color_valid"]
    assert not result["valid"]
    assert result["normal_status"] == "NOT_MEASURED"
    assert result["status"] == "BASE_COLOR_DIAGNOSTIC"


def test_invalid_reference_prevents_base_color_pass() -> None:
    state, reference = _state(), _reference(alpha=0)
    atlas = bake_atlas(state, reference, 8, 8)
    result = evaluate_atlas(state, reference, atlas)
    assert not result["base_color_valid"]
    assert result["reference_invalid_faces"] == [0]


def test_bake_does_not_overwrite_existing_output(tmp_path: Path) -> None:
    with pytest.raises(FileExistsError):
        bake_atlas(_state(), _reference(), 4, 4, tmp_path)


def test_two_shared_members_compose_then_fail_conflicting_colors() -> None:
    vertices = np.asarray([[0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 1], [1, 0, 1], [0, 1, 1]], dtype=float)
    faces = np.asarray([[0, 1, 2], [3, 4, 5]], dtype=np.int64)
    corner_uv = np.asarray([[[0.25, 0.5]] * 3, [[0.75, 0.5]] * 3])
    image = np.asarray([[[255, 0, 0, 255], [0, 0, 255, 255]]], dtype=np.uint8)
    reference = ReferenceSurface(vertices, faces, corner_uv, image)
    uv_triangle = np.asarray([[0.1, 0.1], [0.9, 0.1], [0.1, 0.9]])
    state = MeshState(make_mesh(vertices, faces), frozenset(), np.tile(uv_triangle, (2, 1)), faces.copy())
    atlas = bake_atlas(state, reference, 16, 16)
    actual = sample_rgba8(atlas.levels[0], np.asarray([[0.3, 0.3]]))
    assert np.allclose(actual[0], [0.5, 0, 0.5, 1], atol=0.003)
    result = evaluate_atlas(state, reference, atlas)
    assert not result["base_color_valid"]
    assert result["failed_faces"] == [0, 1]


def test_centroid_broad_phase_does_not_miss_large_nearest_triangle() -> None:
    triangles = np.asarray(
        [[[-100, -100, 0], [100, -100, 0], [0, 100, 0]], [[0, 0, 1], [1, 0, 1], [0, 1, 1]]],
        dtype=float,
    )
    vertices = triangles.reshape(-1, 3)
    faces = np.arange(6, dtype=np.int64).reshape(-1, 3)
    reference = ReferenceSurface(
        vertices, faces, np.full((2, 3, 2), 0.5), np.full((1, 1, 4), 255, dtype=np.uint8)
    )
    points = np.asarray([[0.2, 0.3, 0.1], [0.8, 0.1, 0.9], [5, 0, 0.4]])
    result = reference.sample(points, 1.0)
    _, expected_faces, expected_distances = project_surface(points, triangles)
    assert np.array_equal(result.target_faces, expected_faces)
    assert np.allclose(result.distances, expected_distances)


def test_deadline_aborts_projection_and_baking() -> None:
    reference = _reference()
    with pytest.raises(TimeoutError):
        reference.sample(np.asarray([[0.2, 0.2, 0]]), 0.1, deadline=0)
    with pytest.raises(TimeoutError):
        bake_atlas(_state(), reference, 4, 4, deadline=0)
