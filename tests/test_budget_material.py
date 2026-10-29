import json
import struct
from pathlib import Path

import numpy as np
import pytest

from reseam.budget_material import (
    MaterialReference,
    MaterialThresholds,
    atlas_from_arrays,
    bake_material,
    evaluate_material,
    export_material_glb,
    frame_arrays,
    interpolated_frames,
    load_material_reference,
    mikktspace_frames,
    prepare_material_samples,
    refresh_material_support,
    sample_linear_rgba8,
    sample_material,
    save_material,
)
from reseam.budget_texture import ReferenceSurface, _face_pixels, build_rgba8_mips, texture_payload_bytes
from reseam.mesh import make_mesh
from reseam.types import MeshState


def _metric(report: dict[str, object], *path: str) -> float:
    value: object = report
    for key in path:
        assert isinstance(value, dict)
        value = value[key]
    assert isinstance(value, (int, float)) and not isinstance(value, bool)
    return float(value)


def _state(mirror: bool = False) -> MeshState:
    vertices = np.asarray([[0, 0, 0], [1, 0, 0], [0, 1, 0]], dtype=float)
    faces = np.asarray([[0, 1, 2]], dtype=np.int64)
    uv = np.asarray([[0.15, 0.15], [0.85, 0.15], [0.15, 0.85]], dtype=float)
    if mirror:
        uv[:, 0] = 1 - uv[:, 0]
    return MeshState(make_mesh(vertices, faces), frozenset(), uv, faces.copy())


def _reference(state: MeshState | None = None) -> MaterialReference:
    state = _state() if state is None else state
    color = np.full((4, 4, 4), [128, 64, 32, 255], dtype=np.uint8)
    normal = np.full((4, 4, 4), [175, 110, 245, 255], dtype=np.uint8)
    orm = np.full((4, 4, 4), [63, 160, 90, 255], dtype=np.uint8)
    geometry = ReferenceSurface(state.mesh.vertices, state.mesh.faces,
                                state.uv[state.uv_faces], color, source_has_normal=True,
                                source_has_orm=True)
    return MaterialReference(geometry, normal, orm, *frame_arrays(state))


def test_data_sampler_is_linear_and_orm_factors_are_applied() -> None:
    reference = _reference()
    reference.metallic_factor = 0.6
    reference.roughness_factor = 0.7
    reference.normal_scale = 0.3
    reference.occlusion_image = np.full((1, 1, 4), [128, 0, 0, 255], dtype=np.uint8)
    reference.occlusion_strength = 0.5
    sample = reference.sample(np.asarray([[0.2, 0.3, 0]]), 0.001)
    assert sample.valid.all()
    assert sample.orm[0].tolist() == pytest.approx([1 + 0.5 * (128 / 255 - 1), 160 / 255 * 0.7,
                                                90 / 255 * 0.6])
    raw = sample_linear_rgba8(reference.normal_image, np.asarray([[0.5, 0.5]]))
    assert raw[0, 0] == pytest.approx(175 / 255)
    assert sample.base_color[0, 0] < 128 / 255


def test_mirrored_target_frame_encodes_then_decodes_world_normal() -> None:
    reference = _reference()
    for mirror in (False, True):
        state = _state(mirror)
        atlas = bake_material(state, reference, 32, padding_pixels=4)
        result = evaluate_material(state, reference, atlas, mip_levels=(0, 1))
        assert result["valid"], result
        assert _metric(result, "channels", "normal", "max") < 1
        sampled = sample_material(state, atlas, np.asarray([0], dtype=np.int64),
                                  np.asarray([[0.4, 0.3, 0.3]]))
        expected = reference.sample(np.asarray([[0.3, 0.3, 0]]), 0.001)
        assert np.linalg.norm(sampled.normal_world - expected.normal_world) < 0.012
    assert frame_arrays(_state())[2][0, 0] == -1
    assert frame_arrays(_state(True))[2][0, 0] == 1


def test_complete_material_payload_and_no_overwrite(tmp_path: Path) -> None:
    atlas = bake_material(_state(), _reference(), 16)
    assert atlas.payload_bytes == 3 * texture_payload_bytes(16, 16)
    assert len(atlas.normal) == len(atlas.orm) == len(atlas.base_color) == 5
    sizes = save_material(atlas, tmp_path / "material")
    assert len(sizes) == 15
    assert (tmp_path / "material/Normal_mip4.png").exists()
    with pytest.raises(FileExistsError):
        save_material(atlas, tmp_path / "material")


def test_correct_color_does_not_hide_wrong_normal_or_orm() -> None:
    state, reference = _state(), _reference()
    atlas = bake_material(state, reference, 32, padding_pixels=4)
    bad_normal = tuple(np.full_like(image, [128, 128, 0, 255]) for image in atlas.normal)
    bad = type(atlas)(atlas.base_color, bad_normal, atlas.orm, atlas.coverage, atlas.metadata)
    result = evaluate_material(state, reference, bad, mip_levels=(0,))
    assert not result["valid"]
    assert result["failed_faces"] == [0]
    assert _metric(result, "channels", "color", "max") < 0.005
    assert _metric(result, "channels", "normal", "mean") > 90
    bad_orm = tuple(np.full_like(image, [255, 0, 255, 255]) for image in atlas.orm)
    bad = type(atlas)(atlas.base_color, atlas.normal, bad_orm, atlas.coverage, atlas.metadata)
    result = evaluate_material(state, reference, bad, mip_levels=(0,))
    assert not result["valid"]
    assert _metric(result, "channels", "orm", "mean") > 0.5


def test_invalid_reference_and_source_projection_cannot_pass() -> None:
    state, reference = _state(), _reference()
    reference.geometry.base_factor[3] = 0
    atlas = bake_material(state, reference, 16)
    result = evaluate_material(state, reference, atlas, mip_levels=(0,))
    assert not result["valid"]
    assert result["reference_coverage"] == 0
    assert result["reference_invalid_faces"] == [0]
    assert not result["bake_reference_valid"]


def test_cache_has_independent_lattice_and_all_edge_near_probes() -> None:
    state, reference = _state(), _reference()
    evaluate = prepare_material_samples(state, reference, samples_per_face=16, phase=0.5)
    train = prepare_material_samples(state, reference, samples_per_face=16, phase=0.25)
    assert evaluate.count_per_face == 25
    assert evaluate.seam_samples.sum() == 9
    assert not np.array_equal(evaluate.barycentric[:16], train.barycentric[:16])
    assert not np.array_equal(evaluate.barycentric[16:], train.barycentric[16:])
    assert not any(np.all(np.isclose(point, train.barycentric), axis=1).any()
                   for point in evaluate.barycentric)
    atlas = bake_material(state, reference, 32, padding_pixels=4)
    result = evaluate_material(state, reference, atlas, mip_levels=(0,), prepared=evaluate)
    assert result["valid"]
    assert _metric(result, "channels", "render", "max") < 0.02


def test_source_roi_keeps_original_frame_identity() -> None:
    state = _state()
    vertices = np.vstack((state.mesh.vertices, state.mesh.vertices + [0, 0, 2]))
    faces = np.asarray([[0, 1, 2], [3, 4, 5]], dtype=np.int64)
    uv = np.tile(state.uv, (2, 1))
    surface = MeshState(make_mesh(vertices, faces), frozenset(), uv, faces.copy())
    ref = _reference(surface)
    subset = ref.restrict_bounds(np.asarray([[0, 0, 1.9], [1, 1, 2.1]]), 0.1)
    sample = subset.sample(np.asarray([[0.2, 0.3, 2]]), 0.001)
    assert sample.target_faces.tolist() == [1]
    assert sample.valid.all()


def test_mikk_native_matches_flat_gltf_uv_and_requires_locked_source() -> None:
    state = _state()
    normals, tangents, signs = frame_arrays(state)
    uv = state.uv[state.uv_faces].copy()
    uv[..., 1] = 1 - uv[..., 1]
    result_tangents, result_signs, hashes = mikktspace_frames(state.mesh.vertices[state.mesh.faces], normals, uv)
    assert np.allclose(result_tangents, tangents)
    assert np.array_equal(result_signs, signs)
    assert hashes["source_url"] == "https://github.com/mmikk/MikkTSpace"
    with pytest.raises(FileNotFoundError):
        mikktspace_frames(state.mesh.vertices[state.mesh.faces], normals, uv, Path('missing_mikk.dll'))


def test_full_source_load_requires_real_material_channels(tmp_path: Path) -> None:
    with pytest.raises((ValueError, OSError)):
        load_material_reference(tmp_path / "missing.glb")


def test_atlas_input_validation_and_deadline() -> None:
    with pytest.raises(ValueError):
        MaterialThresholds(important_multiplier=1.1)
    with pytest.raises(ValueError):
        atlas_from_arrays(np.zeros((2, 2, 3)), np.zeros((2, 2, 3)), np.zeros((2, 2, 3)))
    with pytest.raises(TimeoutError):
        bake_material(_state(), _reference(), 16, deadline=0)
    normal, tangent, signs = frame_arrays(_state())
    matrix, valid = interpolated_frames(normal, tangent, signs,
                                       np.asarray([0], dtype=np.int64), np.asarray([[0.2, 0.3, 0.5]]))
    assert valid.all()
    assert np.allclose(matrix @ np.swapaxes(matrix, 1, 2), np.eye(3))


def test_export_preserves_exact_frames_and_all_material_channels(tmp_path: Path) -> None:
    state, reference = _state(True), _reference()
    atlas = bake_material(state, reference, 16, padding_pixels=4)
    path = tmp_path / 'mesh.glb'
    record = export_material_glb(state, atlas, path)
    assert record['normal_tangent_exact']
    raw = path.read_bytes()
    size = struct.unpack_from('<I', raw, 12)[0]
    document = json.loads(raw[20:20 + size])
    binary = raw[20 + size + 8:]
    attributes = document['meshes'][0]['primitives'][0]['attributes']
    normal, tangent, signs = frame_arrays(state)
    expected = {'NORMAL': normal.reshape(-1, 3),
                'TANGENT': np.concatenate((tangent, signs[..., None]), axis=-1).reshape(-1, 4)}
    for key, values in expected.items():
        accessor = document['accessors'][attributes[key]]
        view = document['bufferViews'][accessor['bufferView']]
        array = np.frombuffer(binary, dtype='<f4', count=values.size,
                              offset=view['byteOffset']).reshape(values.shape)
        assert np.allclose(array, values, atol=1e-7)
    material = document['materials'][0]
    assert material['normalTexture']['index'] == 1
    assert material['pbrMetallicRoughness']['metallicRoughnessTexture']['index'] == 2
    assert document['samplers'][0]['minFilter'] == 9729
    with pytest.raises(FileExistsError):
        export_material_glb(state, atlas, path)


def test_old_raster_evidence_is_rejected_after_uv_or_corner_identity_changes() -> None:
    state, reference = _state(), _reference()
    atlas = bake_material(state, reference, 32, padding_pixels=4)
    moved = state.copy()
    moved.uv += [0.006, 0.003]
    result = evaluate_material(moved, reference, atlas, mip_levels=(0,))
    assert not result["support_state_matches"]
    assert not result["bake_reference_valid"]
    assert not result["valid"]
    disconnected = state.copy()
    disconnected.seams = frozenset({0})
    result = evaluate_material(disconnected, reference, atlas, mip_levels=(0,))
    assert not result["support_state_matches"]


def test_support_refresh_preserves_optimized_mip_values() -> None:
    state, reference = _state(), _reference()
    atlas = bake_material(state, reference, 32, padding_pixels=4)
    moved = state.copy()
    moved.uv += [0.006, 0.003]
    custom_base = tuple(image.copy() for image in atlas.base_color)
    custom_base[0][10, 10] = [25, 50, 75, 255]
    optimized = type(atlas)(custom_base, atlas.normal, atlas.orm, atlas.coverage, atlas.metadata)
    refreshed = refresh_material_support(moved, reference, optimized, padding_pixels=4)
    assert refreshed.base_color is optimized.base_color
    assert refreshed.normal is optimized.normal
    assert refreshed.orm is optimized.orm
    assert refreshed.base_color[0][10, 10].tolist() == [25, 50, 75, 255]
    result = evaluate_material(moved, reference, refreshed, mip_levels=(0,))
    assert result["support_state_matches"]
    assert result["bake_reference_valid"]


def test_new_uv_can_hit_source_alpha_hole_unseen_by_fixed_probes() -> None:
    state, reference = _state(), _reference()
    reference.geometry.image = np.full((128, 128, 4), [128, 64, 32, 255], dtype=np.uint8)
    moved = state.copy()
    moved.uv += [0.006, 0.003]
    cache = prepare_material_samples(moved, reference, samples_per_face=8, phase=0.73)

    def source_footprints(uv: np.ndarray) -> set[tuple[int, int]]:
        x = np.floor(uv[:, 0] * 128 - 0.5).astype(int)
        y = np.floor((1 - uv[:, 1]) * 128 - 0.5).astype(int)
        return {((int(j) + dy) % 128, (int(i) + dx) % 128)
                for i, j in zip(x, y, strict=True) for dx in (0, 1) for dy in (0, 1)}

    _, _, old_bary = _face_pixels(state.uv[state.uv_faces[0]], 16, 16)
    _, _, new_bary = _face_pixels(moved.uv[moved.uv_faces[0]], 16, 16)
    source_uv = reference.geometry.corner_uv[0]
    occupied = source_footprints(old_bary @ source_uv) | source_footprints(cache.barycentric @ source_uv)
    new_only = source_footprints(new_bary @ source_uv) - occupied
    assert new_only
    hole_y, hole_x = min(new_only)
    reference.geometry.image[hole_y, hole_x, 3] = 0
    atlas = bake_material(state, reference, 16, padding_pixels=4)
    assert atlas.metadata["invalid_projection_member_texels"] == 0
    # Fixed world-space held-out samples miss the deliberately localized hole.
    fresh_cache = prepare_material_samples(moved, reference, samples_per_face=8, phase=0.73)
    assert fresh_cache.reference.valid.all()
    stale_result = evaluate_material(moved, reference, atlas, mip_levels=(0,), prepared=fresh_cache)
    assert not stale_result["support_state_matches"]
    refreshed = refresh_material_support(moved, reference, atlas, padding_pixels=4)
    assert _metric(refreshed.metadata, "invalid_projection_member_texels") > 0
    result = evaluate_material(moved, reference, refreshed, mip_levels=(0,), prepared=fresh_cache)
    assert result["support_state_matches"]
    assert result["source_reference_valid"]
    assert not result["bake_reference_valid"]
    assert not result["valid"]
    assert result["raster_reference_invalid_faces"] == [0]
    assert result["failed_faces"] == [0]


def test_final_raster_gate_catches_corrupt_texel_invisible_to_independent_probes() -> None:
    state, reference = _state(), _reference()
    atlas = bake_material(state, reference, 256, padding_pixels=4)
    prepared = prepare_material_samples(state, reference, samples_per_face=24, phase=0.73)
    uv = prepared.barycentric @ state.uv[state.uv_faces[0]]
    supported: set[tuple[int, int]] = set()
    for mip in (0, 1):
        scale = 2**mip
        size = 256 // scale
        x = np.floor(uv[:, 0] * size - 0.5).astype(int)
        y = np.floor((1 - uv[:, 1]) * size - 0.5).astype(int)
        for i, j in zip(x, y, strict=True):
            for dx in (0, 1):
                for dy in (0, 1):
                    for sx in range(scale):
                        for sy in range(scale):
                            supported.add(((int(j) + dy) * scale + sy, (int(i) + dx) * scale + sx))
    xx, yy, bary = _face_pixels(state.uv[state.uv_faces[0]], 256, 256)
    candidates = {(int(y), int(x)) for x, y, b in zip(xx, yy, bary, strict=True)
                  if min(b) > 0.05} - supported
    assert candidates
    y, x = min(candidates)
    bad_base = atlas.base_color[0].copy()
    bad_base[y, x] = [255, 255, 255, 255]
    corrupted = type(atlas)(build_rgba8_mips(bad_base), atlas.normal, atlas.orm, atlas.coverage, atlas.metadata)
    probe_only = evaluate_material(state, reference, corrupted, prepared=prepared, mip_levels=(0, 1))
    assert probe_only["valid"]
    assert not probe_only["raster_checked"]
    assert probe_only["raster_valid"] is None
    final = evaluate_material(state, reference, corrupted, prepared=prepared,
                              mip_levels=(0, 1), raster_checks=True)
    assert not final["valid"]
    assert final["raster_checked"] and not final["raster_valid"]
    assert _metric(final, "raster_count") > 10_000
    assert _metric(final, "raster_channels", "color", "max") > 1
    assert final["failed_faces"] == [0]
    assert _metric(final, "quality_violation") > 0


def test_constant_material_passes_both_fixed_probes_and_actual_raster_centers() -> None:
    state, reference = _state(True), _reference()
    atlas = bake_material(state, reference, 32, padding_pixels=4)
    result = evaluate_material(state, reference, atlas, raster_checks=True)
    assert result["valid"]
    assert result["raster_valid"]
    assert _metric(result, "raster_count") > 0
    assert result["status"] == "CPU_MATERIAL_PROBES_AND_RASTER_VALIDATED"
