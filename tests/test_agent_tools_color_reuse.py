"""Source-bound color targets remain independent of candidate UV and atlas files."""

import time
from dataclasses import replace
from pathlib import Path
from typing import cast

import numpy as np
import pytest
from PIL import Image

from reseam.agent_tools.color_reuse import (
    AtlasSignal,
    ColorTarget,
    _bary,
    _filtered,
    _mips,
    _project_uv_barycentric,
    _render,
    bake_color_reuse,
    export_color_reuse,
    freeze_target,
    validate_color_reuse,
)
from reseam.agent_tools.geometry import apply_reuse, check_pair
from reseam.agent_tools.state import Snapshot, load_snapshot, read_object, save_snapshot
from reseam.budget_material import atlas_from_arrays, export_material_glb
from reseam.budget_state import BudgetState
from reseam.budget_texture import sample_rgba8
from reseam.mesh import make_mesh, state_from_corner_uv


def _deadline() -> float:
    return time.monotonic() + 90


def _folder(root: Path, name: str) -> Path:
    result = root / name
    result.mkdir()
    return result


def _source(root: Path, checker: bool = False) -> Snapshot:
    vertices = np.array([[-1., 0, 0], [-1, 1, 0], [-1, 1, 1], [-1, 0, 1],
                         [1., 0, 0], [1, 1, 0], [1, 1, 1], [1, 0, 1]])
    faces = np.array([[0, 1, 2], [0, 2, 3], [4, 6, 5], [4, 7, 6]], dtype=np.int64)
    corners = .1 + .25 * vertices[faces, 1:]
    corners[2:, :, 0] += .5
    corners[2:, :, 1] = .45 - corners[2:, :, 1]
    surface = state_from_corner_uv(make_mesh(vertices, faces), corners, seam_ids=frozenset())
    color = np.full((32, 32, 4), [.7, .1, .1, 1.]); color[:, 16:] = [.1, .1, .7, 1.]
    if checker:
        grid = np.indices((32, 32))
        color[..., :3] = ((grid[0] + grid[1]) % 2)[..., None]
    atlas = atlas_from_arrays(color, np.full((32, 32, 3), [0., 0, 1]),
                              np.full((32, 32, 3), [1., 1., 0]))
    path = root / "source.glb"
    export_material_glb(surface, atlas, path)
    return save_snapshot(BudgetState(surface), root / "source", {"reference_glb": str(path)})


def _shared(root: Path, original: Snapshot) -> Snapshot:
    checked = check_pair({"snapshot": str(original.root), "first_faces": [0, 1], "second_faces": [2, 3],
                          "profile": {"resolution": 128, "gap_pixels": 8, "border_pixels": 8}},
                         _folder(root, "check"), _deadline())
    assert checked["status"] == "OK", checked
    applied = apply_reuse({"snapshot": str(original.root), "proposal": checked["proposal"]},
                          _folder(root, "apply"), _deadline())
    return load_snapshot(Path(str(applied["snapshot"])))


def test_explicit_geometry_policy_is_bound_across_target_bake_and_validation(tmp_path: Path) -> None:
    original = _source(tmp_path)
    shared = _shared(tmp_path, original)
    state = shared.state.copy()
    state.groups = (replace(state.groups[0], plane_offset=.05),)
    shifted = save_snapshot(state, tmp_path / "shifted", shared.metadata, shared)
    with pytest.raises(ValueError, match="LEGAL_SHARED_STATE"):
        freeze_target({"snapshot": str(shifted.root)}, _folder(tmp_path, "strict"), _deadline())
    policy: dict[str, object] = {"correspondence_fraction": .1, "normal_angle_degrees": 180,
                                 "quality_constraints": False}
    frozen = freeze_target({"snapshot": str(shifted.root), "geometry_profile": policy},
                           _folder(tmp_path, "relaxed"), _deadline())
    assert frozen["strict_geometry_profile_applied"] is False
    assert frozen["artist_reviewed"] is False
    common: dict[str, object] = {"snapshot": str(shifted.root), "target": frozen["target"],
                                "resolution": 128, "padding_pixels": 8}
    with pytest.raises(ValueError, match="GEOMETRY_PROFILE_MISMATCH"):
        bake_color_reuse({**common, "geometry_profile": {"correspondence_fraction": .2}},
                          _folder(tmp_path, "mismatched_bake"), _deadline())
    baked = bake_color_reuse(common, _folder(tmp_path, "relaxed_bake"), _deadline())
    assert baked["status"] == "OK"
    validated = validate_color_reuse({"snapshot": str(shifted.root), "bake": baked["bake"],
                                      "render_size": 16}, _folder(tmp_path, "relaxed_validation"), _deadline())
    assert validated["quality_accepted"] is True
    assert validated["geometry_policy"] == "explicit_custom"
    evidence = read_object(Path(str(validated["validation"])))
    assert cast(dict[str, object], evidence["geometry"])["quality_constraints_checked"] is False
    assert evidence["artist_reviewed"] is False
    with pytest.raises(ValueError, match="GEOMETRY_PROFILE_MISMATCH"):
        validate_color_reuse({"snapshot": str(shifted.root), "bake": baked["bake"],
                              "geometry_profile": {"quality_constraints": True}},
                             _folder(tmp_path, "mismatched_validation"), _deadline())
    # An independent no-sharing control may use the same source-derived target.
    baseline = bake_color_reuse({**common, "snapshot": str(original.root)},
                                _folder(tmp_path, "control"), _deadline())
    assert baseline["geometry_profile_hash"] == baked["geometry_profile_hash"]


def test_explicit_normal_limit_reaches_target_safety_check(tmp_path: Path) -> None:
    original = _source(tmp_path)
    shared = _shared(tmp_path, original)
    state = shared.state.copy()
    state.groups = (replace(state.groups[0], plane_normal=(2**-.5, 2**-.5, 0.)),)
    snapshot = save_snapshot(state, tmp_path / "turned_plane", shared.metadata, shared)
    broad_distance: dict[str, object] = {"correspondence_fraction": 1., "quality_constraints": False}
    with pytest.raises(ValueError, match="LEGAL_SHARED_STATE"):
        freeze_target({"snapshot": str(snapshot.root), "geometry_profile": broad_distance},
                       _folder(tmp_path, "normal_strict"), _deadline())
    frozen = freeze_target({"snapshot": str(snapshot.root),
                            "geometry_profile": {**broad_distance, "normal_angle_degrees": 180}},
                           _folder(tmp_path, "normal_relaxed"), _deadline())
    assert frozen["status"] == "OK"
    assert frozen["strict_geometry_profile_applied"] is False


def test_custom_geometry_policy_still_rejects_undeclared_overlap(tmp_path: Path) -> None:
    original = _source(tmp_path)
    state = original.state.copy()
    state.surface.uv[state.surface.uv_faces[2:]] = state.surface.uv[state.surface.uv_faces[:2]]
    invalid = save_snapshot(state, tmp_path / "invalid_overlap", original.metadata, original)
    baked = bake_color_reuse({"snapshot": str(invalid.root), "resolution": 32,
                              "geometry_profile": {"normal_angle_degrees": 180,
                                                   "correspondence_fraction": 1., "quality_constraints": False}},
                             _folder(tmp_path, "invalid_bake"), _deadline())
    assert baked["status"] == "REJECTED"
    assert baked["reason"] == "GEOMETRY_UNSAFE"
    assert not (tmp_path / "invalid_bake/texture").exists()


def test_target_symmetry_is_source_bound_and_reusable_on_baseline(tmp_path: Path) -> None:
    original = _source(tmp_path)
    shared = _shared(tmp_path, original)
    result = freeze_target({"snapshot": str(shared.root)}, _folder(tmp_path, "target"), _deadline())
    target = ColorTarget(original, Path(str(result["target"])))
    bary = np.array([[.2, .3, .5]])
    assert target.sample(2, bary)[0, 0] > .69
    assert target.original(2, bary)[0, 2] > .69
    change = read_object(Path(str(result["appearance_change"])))
    assert change["modified_faces"] == [2, 3]
    assert float(str(change["area_weighted_mean"])) > .4
    # Repacking or releasing sharing changes no frozen appearance target.
    assert np.array_equal(ColorTarget(shared, target.path).sample(2, bary), target.sample(2, bary))


def test_opaque_source_alpha_does_not_become_missing_atlas_coverage(tmp_path: Path) -> None:
    original = _source(tmp_path)
    frozen = freeze_target({"snapshot": str(original.root)}, _folder(tmp_path, "target"), _deadline())
    target = ColorTarget(original, Path(str(frozen["target"])))
    target.reference.image = target.reference.image.copy()
    target.reference.image[:, :, 3] = 0
    target.reference.base_factor[3] = 0
    assert target.sample(0, np.array([[.2, .3, .5]]))[0, 3] == 1


def test_uv_triangle_projection_is_scale_invariant() -> None:
    triangle = np.array([[[0., 0.], [1., 0.], [0., 1.]]] * 3)
    points = np.array([[.25, .25], [.7, .7], [-.2, .5]])
    expected = np.array([[.5, .25, .25], [0., .5, .5], [.5, 0., .5]])
    for scale in (1., 1e-3, 1e-5):
        actual = _project_uv_barycentric(.79 + scale * triangle, .79 + scale * points)
        assert np.allclose(actual, expected, rtol=0, atol=1e-10)


def test_tiny_chart_reference_is_independent_of_query_face_and_cache(tmp_path: Path) -> None:
    original = _source(tmp_path, checker=True)
    frozen = freeze_target({"snapshot": str(original.root)}, _folder(tmp_path, "target"), _deadline())
    target = ColorTarget(original, Path(str(frozen["target"])))
    surface = original.state.surface.copy()
    surface.uv = .79 + .001 * surface.uv
    triangles = surface.uv[surface.uv_faces]
    signal = AtlasSignal(target, surface, cache_filter=True)
    reference = AtlasSignal(target, surface)
    points = np.array([[.3, .3, .4], [.6, .3, .1]]) @ triangles[1]
    # Recover barycentrics in the other triangle, including negative coordinates.
    query = _bary(triangles[0], points)
    assert np.allclose(signal.sample(0, query), reference.sample(1, np.array([[.3, .3, .4], [.6, .3, .1]])), atol=1e-9)
    first = _filtered(signal, 0, triangles[0], points, 8192, 0)
    second = _filtered(signal, 1, triangles[1], points, 8192, 0)
    direct = _filtered(reference, 1, triangles[1], points, 8192, 0)
    assert np.allclose(first, second, atol=1e-9)
    assert np.allclose(second, direct, atol=1e-9)


def test_long_thin_triangle_apex_has_source_sampled_support(tmp_path: Path) -> None:
    original = _source(tmp_path)
    surface = original.state.surface
    corners = surface.uv[surface.uv_faces].copy()
    corners[0] = [[.1, .1], [.9, .1], [.9, .105]]
    corners[1, :, 1] += .3
    corners[2:, :, 1] += .5
    thin = state_from_corner_uv(surface.mesh, corners, seam_ids=frozenset(range(len(surface.mesh.edges))))
    snapshot = save_snapshot(BudgetState(thin), tmp_path / "thin", original.metadata, original)
    baked = bake_color_reuse({"snapshot": str(snapshot.root), "resolution": 256, "padding_pixels": 8},
                             _folder(tmp_path, "bake"), _deadline())
    assert baked["status"] == "OK"
    image = np.asarray(Image.open(tmp_path / "bake/texture/BaseColor_mip0.png"))
    points = np.array([[.98, .01, .01], [.95, .025, .025]]) @ corners[0]
    sampled = sample_rgba8(image, points)
    assert np.all(sampled[:, 3] == 1)
    assert np.allclose(sampled[:, :3], [.7, .1, .1], atol=.005)
    manifest = read_object(tmp_path / "bake/bake.json")
    assert 0 not in cast(list[int], manifest["subtexel_faces"])  # The tail has centers; only the apex is unsupported by center rasterization.
    geometric = np.load(tmp_path / "bake/geometric_coverage.npy")
    assert not geometric[int((1-points[0, 1])*256), int(points[0, 0]*256)]


def test_protected_metadata_cannot_be_omitted_to_allow_symmetrization(tmp_path: Path) -> None:
    original = _source(tmp_path)
    shared = _shared(tmp_path, original)
    protected = save_snapshot(shared.state, tmp_path / "protected",
        {**shared.metadata, "regions": [{"role": "protected", "faces": [2]}]}, shared)
    with pytest.raises(ValueError, match="PROTECTED_FACE_IN_SHARED_TARGET"):
        freeze_target({"snapshot": str(protected.root)}, _folder(tmp_path, "target"), _deadline())


def test_scoped_color_shared_bake_validation_export(tmp_path: Path) -> None:
    original = _source(tmp_path)
    shared = _shared(tmp_path, original)
    target = freeze_target({"snapshot": str(shared.root)}, _folder(tmp_path, "target"), _deadline())
    baked = bake_color_reuse({"snapshot": str(shared.root), "target": target["target"],
                             "resolution": 128, "padding_pixels": 8}, _folder(tmp_path, "bake"), _deadline())
    validation = validate_color_reuse({"snapshot": str(shared.root), "bake": baked["bake"],
        "render_size": 16, "geometry_profile": {"gap_pixels": 8, "border_pixels": 8}},
        _folder(tmp_path, "validation"), _deadline())
    assert validation["quality_accepted"], validation
    assert validation["full_pbr_passed"] is False
    exported = export_color_reuse({"snapshot": str(shared.root), "bake": baked["bake"],
                                   "validation": validation["validation"]},
                                  _folder(tmp_path, "export"), _deadline())
    assert exported["quality_accepted"] and exported["full_pbr_passed"] is False
    assert (tmp_path / "export/asset.glb").exists()
    assert (tmp_path / "export/texture/BaseColor_mip7.png").exists()
    assert not (tmp_path / "export/texture/Normal_mip0.png").exists()


def test_original_texture_conflict_does_not_pass_shared_validation(tmp_path: Path) -> None:
    original = _source(tmp_path)
    shared = _shared(tmp_path, original)
    target = freeze_target({"snapshot": str(original.root), "policy": "source_original"},
                           _folder(tmp_path, "target"), _deadline())
    baked = bake_color_reuse({"snapshot": str(shared.root), "target": target["target"], "resolution": 64},
                             _folder(tmp_path, "bake"), _deadline())
    validation = validate_color_reuse({"snapshot": str(shared.root), "bake": baked["bake"], "render_size": 16},
                                      _folder(tmp_path, "validation"), _deadline())
    assert validation["material_valid"] is False
    assert validation["quality_accepted"] is False


def test_target_tampering_and_timeout_are_rejected(tmp_path: Path) -> None:
    original = _source(tmp_path)
    result = freeze_target({"snapshot": str(original.root)}, _folder(tmp_path, "target"), _deadline())
    with pytest.raises(TimeoutError):
        bake_color_reuse({"snapshot": str(original.root), "target": result["target"], "resolution": 32},
                         _folder(tmp_path, "timeout"), time.monotonic() - 1)
    assert not (tmp_path / "timeout/bake.json").exists()
    path = tmp_path / "target/target.npz"
    path.write_bytes(path.read_bytes() + b"changed")
    with pytest.raises(ValueError, match="TARGET_ARRAY_HASH_MISMATCH"):
        ColorTarget(original, Path(str(result["target"])))


def test_source_filter_matches_linear_recursive_mip_on_identity(tmp_path: Path) -> None:
    original = _source(tmp_path)
    result = freeze_target({"snapshot": str(original.root)}, _folder(tmp_path, "target"), _deadline())
    target = ColorTarget(original, Path(str(result["target"])))
    # Interior of a constant source patch is preserved at every validated LOD.
    base = np.full((64, 64, 4), [218, 89, 89, 255], dtype=np.uint8)
    paths = _mips(base, _folder(tmp_path, "mips"), _deadline())
    triangle = original.state.surface.uv[original.state.surface.uv_faces[0]]
    uv = np.array([[.18, .17], [.23, .21]])
    for level in (0, 1, 2):
        expected = _filtered(target, 0, triangle, uv, 64, level)
        actual = sample_rgba8(np.asarray(Image.open(paths[level]).convert("RGBA")), uv)
        assert np.max(np.abs(expected - actual)) < .01


def test_fixed_screen_reference_rejects_loss_of_checker_detail(tmp_path: Path) -> None:
    original = _source(tmp_path, checker=True)
    result = freeze_target({"snapshot": str(original.root)}, _folder(tmp_path, "target"), _deadline())
    target = ColorTarget(original, Path(str(result["target"])))
    limits = {"render_mean": .025, "render_p95": .08, "render_max": .2}
    # A spatially constant atlas has the right mean. It must not pass by using
    # its own loss of detail to blur the source-side reference.
    levels = [np.full((16, 16, 4), [188, 188, 188, 255], dtype=np.uint8)]
    report = _render(target, original.state.surface, levels, [0], 64, limits,
                     tmp_path / "render_small", _deadline())
    assert report["valid"] is False
    large = _render(target, original.state.surface,
                    [np.full((64, 64, 4), [188, 188, 188, 255], dtype=np.uint8)], [0], 64, limits,
                    tmp_path / "render_large", _deadline())
    assert large["valid"] is False
    assert (tmp_path / "render_small/azimuth17_mip0_target.png").read_bytes() == (
        tmp_path / "render_large/azimuth17_mip0_target.png").read_bytes()


def test_chart_boundary_footprint_does_not_extrapolate_into_unrelated_source_island(tmp_path: Path) -> None:
    original = _source(tmp_path)
    result = freeze_target({"snapshot": str(original.root)}, _folder(tmp_path, "target"), _deadline())
    target = ColorTarget(original, Path(str(result["target"])))
    outside = np.array([[-2., 3., 0.]])
    assert target.sample(0, outside)[0, 2] > .69
    signal = AtlasSignal(target, original.state.surface)
    clamped = signal.sample(0, outside)
    assert clamped[0, 0] > .69 and clamped[0, 2] < .11


def test_subtexel_chart_gets_source_support_without_changing_geometry(tmp_path: Path) -> None:
    original = _source(tmp_path)
    state = original.state.copy()
    ids = np.unique(state.surface.uv_faces[:2])
    state.surface.uv[ids] = .202 + .001 * (state.surface.uv[ids] - .1)
    tiny = save_snapshot(state, tmp_path / "tiny", original.metadata, original)
    baked = bake_color_reuse({"snapshot": str(tiny.root), "resolution": 64},
                             _folder(tmp_path, "bake"), _deadline())
    record = read_object(Path(str(baked["bake"])))
    assert record["subtexel_faces"] == [0, 1]
    image = np.asarray(Image.open(tmp_path / "bake/texture/BaseColor_mip0.png").convert("RGBA"))
    sampled = sample_rgba8(image, np.array([[.2021, .2021]]))
    assert sampled[0, 3] == pytest.approx(1.)
    assert sampled[0, 0] > .69
    assert np.array_equal(tiny.state.surface.mesh.faces, original.state.surface.mesh.faces)


def test_source_filter_cache_preserves_exact_quadrature(tmp_path: Path) -> None:
    original = _source(tmp_path, checker=True)
    frozen = freeze_target({"snapshot": str(original.root)}, _folder(tmp_path, "target"), _deadline())
    target = ColorTarget(original, Path(str(frozen["target"])))
    cached = AtlasSignal(target, original.state.surface, cache_filter=True)
    plain = AtlasSignal(target, original.state.surface)
    uv = np.array([[.105, .105], [.2, .2], [.32, .34], [.105, .105]])
    triangle = original.state.surface.uv[original.state.surface.uv_faces[0]]
    for level in (0, 1, 2):
        a = _filtered(plain, 0, triangle, uv, 64, level)
        b = _filtered(cached, 0, triangle, uv, 64, level)
        assert np.allclose(a, b, atol=1e-14, rtol=0)
        assert np.allclose(b, _filtered(cached, 0, triangle, uv, 64, level), atol=1e-14, rtol=0)
