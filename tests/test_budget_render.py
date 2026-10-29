"""Real pixel visibility, identical-light comparisons, and regression boundaries."""

import time
from pathlib import Path

import numpy as np
import pytest

from reseam.budget_material import MaterialReference, bake_material, frame_arrays
from reseam.budget_render import evaluate_render, prepare_render_reference
from reseam.budget_texture import ReferenceSurface
from reseam.mesh import make_mesh
from reseam.types import MeshState


def triangle(two_layers: bool = False) -> tuple[MeshState, MaterialReference]:
    vertices = np.asarray([[0., 0, 0], [1, 0, 0], [0, 1, 0]])
    faces = np.asarray([[0, 1, 2]], dtype=np.int64)
    uv = np.asarray([[.15, .15], [.85, .15], [.15, .85]])
    if two_layers:
        vertices = np.vstack((vertices, vertices + [0, 0, 1]))
        faces = np.asarray([[0, 1, 2], [3, 4, 5]], dtype=np.int64)
        uv = np.tile(uv, (2, 1))
    mesh = make_mesh(vertices, faces)
    state = MeshState(mesh, frozenset(), uv, faces.copy())
    geometry = ReferenceSurface(vertices, faces, uv[faces],
                                np.full((4, 4, 4), [128, 64, 32, 255], dtype=np.uint8),
                                source_has_normal=True, source_has_orm=True)
    reference = MaterialReference(geometry,
        np.full((4, 4, 4), [128, 128, 255, 255], dtype=np.uint8),
        np.full((4, 4, 4), [255, 200, 30, 255], dtype=np.uint8), *frame_arrays(state))
    return state, reference


def test_zbuffer_selects_front_triangle_and_keeps_original_face_barycentrics() -> None:
    state, reference = triangle(two_layers=True)
    cache = prepare_render_reference(state.mesh, reference, size=20, azimuths=(0,), elevation_degrees=0)
    view = cache.views[0]
    assert view.mask.any()
    assert set(view.face_ids.tolist()) == {1}
    assert set(view.image_face_ids[view.mask].tolist()) == {1}
    assert np.allclose(view.barycentric.sum(axis=1), 1)
    assert np.all(view.barycentric >= -1e-10)
    assert view.reference.target_faces.tolist() == [1] * len(view.face_ids)
    assert np.allclose(view.zbuffer[view.mask], 1)
    assert not view.face_ids.flags.writeable


def test_matching_material_passes_true_view_regression_and_saves_images(tmp_path: Path) -> None:
    state, reference = triangle()
    atlas = bake_material(state, reference, 32, padding_pixels=4)
    cache = prepare_render_reference(state.mesh, reference, size=20, azimuths=(17, 197))
    result = evaluate_render(state, atlas, cache, tmp_path / "renders", mip_levels=(0, 1))
    assert result["valid"], result
    assert result["reference_coverage"] == 1
    maximum = result["max"]
    assert isinstance(maximum, (int, float)) and maximum < .01
    per_view = result["per_view_light_mip"]
    assert isinstance(per_view, list) and len(per_view) == 8
    assert len(list((tmp_path / "renders").glob("*.png"))) == 24
    assert result["gpu_validated"] is False
    scope = result["geometry_scope"]
    assert isinstance(scope, str) and "silhouette_error_not_scored" in scope
    with pytest.raises(FileExistsError):
        evaluate_render(state, atlas, cache, tmp_path / "renders")


def test_wrong_material_fails_even_with_identical_geometry() -> None:
    state, reference = triangle()
    atlas = bake_material(state, reference, 32, padding_pixels=4)
    cache = prepare_render_reference(state.mesh, reference, size=20, azimuths=(17,))
    bad_color = tuple(np.full_like(image, [255, 255, 255, 255]) for image in atlas.base_color)
    bad = type(atlas)(bad_color, atlas.normal, atlas.orm, atlas.coverage, atlas.metadata)
    result = evaluate_render(state, bad, cache, mip_levels=(0,))
    assert not result["valid"]
    mean = result["mean"]
    assert isinstance(mean, (int, float)) and mean > .04


def test_missing_reference_coverage_cannot_be_hidden_by_small_shader_error() -> None:
    state, reference = triangle()
    atlas = bake_material(state, reference, 32, padding_pixels=4)
    reference.geometry.base_factor[3] = 0
    cache = prepare_render_reference(state.mesh, reference, size=20, azimuths=(17,))
    result = evaluate_render(state, atlas, cache, mip_levels=(0,))
    assert not result["valid"]
    assert result["reference_coverage"] == 0


def test_cache_geometry_binding_and_deadline_are_explicit() -> None:
    state, reference = triangle()
    atlas = bake_material(state, reference, 16, padding_pixels=4)
    cache = prepare_render_reference(state.mesh, reference, size=12, azimuths=(17,))
    other = MeshState(make_mesh(state.mesh.vertices + [0, 0, .01], state.mesh.faces),
                      state.seams, state.uv, state.uv_faces)
    with pytest.raises(ValueError, match="GEOMETRY_CACHE_MISMATCH"):
        evaluate_render(other, atlas, cache)
    with pytest.raises(TimeoutError, match="RENDER_BUDGET_EXHAUSTED"):
        prepare_render_reference(state.mesh, reference, deadline=time.monotonic() - 1)
    with pytest.raises(TimeoutError, match="RENDER_BUDGET_EXHAUSTED"):
        evaluate_render(state, atlas, cache, deadline=time.monotonic() - 1)
    with pytest.raises(ValueError, match="thresholds"):
        evaluate_render(state, atlas, cache, mip_levels=(99,))
