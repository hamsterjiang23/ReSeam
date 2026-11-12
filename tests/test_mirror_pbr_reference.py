"""Actual PBR declarations and physical normal directions survive explicit mirroring."""

import time
from pathlib import Path
from typing import cast

import numpy as np
import pytest

from reseam.agent_tools.color_reuse import freeze_target
from reseam.agent_tools.material import (
    _accessor,
    _blender_atlas,
    _load_bake,
    _reference,
    _source_document,
    bake_material_tool,
)
from reseam.agent_tools.material_identity import face_corner_identity
from reseam.agent_tools.mirror_asset import mirror_asset
from reseam.agent_tools.mirror_pbr_reference import _append_attribute, _write_glb
from reseam.agent_tools.pbr_reuse import PBRTarget
from reseam.agent_tools.state import Snapshot, load_snapshot, read_object, save_snapshot
from reseam.budget_material import atlas_from_arrays, export_material_glb, sample_material
from reseam.budget_state import BudgetState
from reseam.mesh import make_mesh
from reseam.types import MeshState


def source_asset(root: Path) -> Snapshot:
    vertices = np.asarray([[-1., -1, 0], [1., -1, 0], [1., 1, 0], [-1., 1, 0]])
    faces = np.asarray([[0, 1, 2], [0, 2, 3]], dtype=np.int64)
    uv = np.asarray([[.1, .1], [.9, .1], [.9, .9], [.1, .9]])
    normal = np.asarray([0, .2, 1.])
    normal /= np.linalg.norm(normal)
    surface = MeshState(make_mesh(vertices, faces), frozenset(), uv, faces.copy(),
                        corner_normals=np.broadcast_to(normal, (2, 3, 3)).copy())
    atlas = atlas_from_arrays(np.full((16, 16, 4), [.4, .2, .8, 1]),
        np.full((16, 16, 3), [.2, .6, .77]), np.full((16, 16, 3), [.7, .5, .3]))
    source = root / "source.glb"
    export_material_glb(surface, atlas, source)
    document, binary = _source_document(source)
    material = cast(list[dict[str, object]], document["materials"])[0]
    pbr = cast(dict[str, object], material["pbrMetallicRoughness"])
    pbr.update(baseColorFactor=[.7, .8, .9, 1], metallicFactor=.6, roughnessFactor=.75)
    cast(dict[str, object], material["normalTexture"])["scale"] = .65
    cast(dict[str, object], material["occlusionTexture"])["strength"] = .5
    _write_glb(source, document, bytearray(binary))
    # UV cleanup can already have changed the parent UV, while the actual source
    # signal remains in its own UV and declared normal/tangent frame.
    surface.uv = surface.uv * .8 + .1
    return save_snapshot(BudgetState(surface), root / "parent", {"reference_glb": str(source)})


def mirror(parent: Snapshot, output: Path) -> dict[str, object]:
    output.mkdir()
    return mirror_asset({"snapshot": str(parent.root), "allow_geometry_change": True,
        "material_scope": "preserve_pbr", "profile": {"resolution": 128, "gap_pixels": 4,
        "border_pixels": 4, "quality_constraints": False}}, output, time.monotonic() + 60)


def test_pbr_mirror_preserves_images_declarations_frames_and_128_bake(tmp_path: Path) -> None:
    parent = source_asset(tmp_path)
    original, source_path = _reference(parent, {})
    result = mirror(parent, tmp_path / "mirror")
    shared = load_snapshot(Path(str(result["shared_snapshot"])))
    unshared = load_snapshot(Path(str(result["unshared_snapshot"])))
    assert shared.metadata["basecolor_only"] is False
    assert shared.state.surface.corner_normals is not None
    np.testing.assert_array_equal(shared.state.surface.corner_normals, unshared.state.surface.corner_normals)
    actual, path = _reference(shared, {})
    before, old_binary = _source_document(source_path)
    after, new_binary = _source_document(path)
    for key in ("materials", "textures", "samplers", "images"):
        assert before.get(key) == after.get(key)
    # Original embedded image ranges are retained byte-for-byte, not re-encoded.
    assert new_binary[:len(old_binary)] == old_binary
    with np.load(tmp_path / "mirror/source_correspondence.npz") as data:
        faces, bary, reflected = data["source_face_ids"], data["source_barycentric"], data["reflected_faces"]
        sample_bary = np.tile([[.2, .3, .5]], (len(faces), 1))
        source_bary = np.einsum("nk,nkj->nj", sample_bary, bary)
        expected = original.sample_at(faces, source_bary, np.zeros(len(faces)), np.ones(len(faces), dtype=bool))
        sampled = actual.sample_at(np.arange(len(faces)), sample_bary,
                                  np.zeros(len(faces)), np.ones(len(faces), dtype=bool))
        expected.normal_world[reflected, 0] *= -1
        assert sampled.valid.all()
        np.testing.assert_allclose(sampled.base_color, expected.base_color, atol=1e-7)
        np.testing.assert_allclose(sampled.orm, expected.orm, atol=1e-7)
        np.testing.assert_allclose(sampled.normal_world, expected.normal_world, atol=1e-7)
        retained, reflected_ids = data["face_pairs"].T
        np.testing.assert_array_equal(actual.signs[reflected_ids][:, [0, 2, 1]], -actual.signs[retained])
    deadline = time.monotonic() + 60
    target_dir, bake_dir = tmp_path / "target", tmp_path / "bake"
    target_dir.mkdir()
    bake_dir.mkdir()
    request: dict[str, object] = {"snapshot": str(shared.root), "mode": "pbr_reuse",
        "projection_method": "same_surface_face_corner", "resolution": 128,
        "geometry_profile": {"resolution": 128, "gap_pixels": 4, "border_pixels": 4},
        "acceptance_policy": "visual"}
    frozen = freeze_target(request, target_dir, deadline)
    request["target"] = frozen["target"]
    baked = bake_material_tool(request, bake_dir, deadline)
    assert baked["status"] == "OK", baked
    audit = read_object(bake_dir / "member_audit.json")
    assert audit["invalid_samples"] == 0
    assert baked["member_conflicts"] == []
    assert all(float(group["max_normal_degrees"]) < .001
               for group in cast(dict[str, dict[str, float]], audit["groups"]).values())
    atlas, _ = _load_bake(shared, Path(str(baked["bake"])))
    target = PBRTarget(shared, Path(str(frozen["target"])), deadline)
    face_ids = np.arange(len(shared.state.surface.mesh.faces), dtype=np.int64)
    barycentric = np.tile([[1 / 3] * 3], (len(face_ids), 1))
    expected_samples = target.original(face_ids, barycentric)
    baked_samples = sample_material(shared.state.surface, atlas, face_ids, barycentric)
    assert baked_samples.valid.all()
    cosines = np.sum(expected_samples.normal_world * baked_samples.normal_world, axis=1)
    assert np.max(np.degrees(np.arccos(np.clip(cosines, -1, 1)))) < 1
    assert baked["quality_accepted"] is False  # construction is not artist acceptance


def test_parent_repaired_normals_are_not_confused_with_source_normals(tmp_path: Path) -> None:
    parent = source_asset(tmp_path)
    state = parent.state.copy()
    assert state.surface.corner_normals is not None
    state.surface.corner_normals[:] = [0, 0, 1]
    repaired = save_snapshot(state, tmp_path / "repaired", parent.metadata, parent)
    result = mirror(repaired, tmp_path / "mirror")
    record = read_object(Path(str(result["report"])))
    scope = cast(dict[str, object], record["source_material_scope"])
    assert float(cast(float, scope["source_vs_parent_corner_normal_max_l2"])) > .1
    variant = load_snapshot(Path(str(result["variant_root"])))
    assert variant.state.surface.corner_normals is not None
    np.testing.assert_allclose(variant.state.surface.corner_normals, np.tile([[[0, 0, 1]]], (6, 3, 1)))
    source, _ = _reference(variant, {})
    assert source.normals[..., 1].min() > .1


@pytest.mark.parametrize("missing", ["normalTexture", "metallicRoughnessTexture", "TANGENT"])
def test_missing_declared_pbr_is_not_invented(tmp_path: Path, missing: str) -> None:
    parent = source_asset(tmp_path)
    source = Path(str(parent.metadata["reference_glb"]))
    document, binary = _source_document(source)
    if missing == "TANGENT":
        mesh = cast(list[dict[str, object]], document["meshes"])[0]
        primitive = cast(list[dict[str, object]], mesh["primitives"])[0]
        cast(dict[str, object], primitive["attributes"]).pop(missing)
    else:
        material = cast(list[dict[str, object]], document["materials"])[0]
        owner = cast(dict[str, object], material["pbrMetallicRoughness"]) if missing.startswith("metallic") else material
        owner.pop(missing)
    _write_glb(source, document, bytearray(binary))
    with pytest.raises(ValueError, match="(PBR_MIRROR_.*REQUIRED|DECLARED_TANGENT_REQUIRED)"):
        mirror(parent, tmp_path / "mirror")


def test_pbr_mirror_does_not_silently_project_to_high(tmp_path: Path) -> None:
    parent = source_asset(tmp_path)
    with pytest.raises(ValueError, match="PBR_MIRROR_SAME_SURFACE_REQUIRED"):
        mirror_asset({"snapshot": str(parent.root), "allow_geometry_change": True,
                      "source_mode": "projected_high", "material_scope": "preserve_pbr"},
                     tmp_path, time.monotonic() + 30)


def test_same_surface_requires_positions_and_order_not_just_face_count(tmp_path: Path) -> None:
    parent = source_asset(tmp_path)
    source, _ = _reference(parent, {})
    xyz = source.geometry.vertices[source.geometry.faces]
    face_corner_identity(parent.state.surface.mesh.vertices[parent.state.surface.mesh.faces], xyz)
    for changed in (xyz[::-1], xyz[:, [1, 2, 0]], xyz + .01):
        with pytest.raises(ValueError, match="FACE_CORNER_IDENTITY_MISMATCH"):
            face_corner_identity(xyz, changed)
    within_roundoff = face_corner_identity(xyz, xyz + 1e-8)
    assert within_roundoff["source_float32_sha256"] != within_roundoff["target_float32_sha256"]
    assert float(cast(float, within_roundoff["position_max_abs_difference"])) > 0


@pytest.mark.parametrize("bad_attribute", ["zero_tangent", "invalid_sign"])
def test_bad_frame_outside_retained_side_still_rejects_if_clipping_uses_it(tmp_path: Path,
                                                                       bad_attribute: str) -> None:
    parent = source_asset(tmp_path)
    source = Path(str(parent.metadata["reference_glb"]))
    document, binary = _source_document(source)
    mesh = cast(list[dict[str, object]], document["meshes"])[0]
    primitive = cast(list[dict[str, object]], mesh["primitives"])[0]
    attributes = cast(dict[str, int], primitive["attributes"])
    tangents = _accessor(document, binary, attributes["TANGENT"])
    # Source face0/corner1 is on the positive side. Clipping to negative still
    # uses it with nonzero barycentric weight at the new plane intersection.
    if bad_attribute == "zero_tangent":
        tangents[1, :3] = 0
    else:
        tangents[1, 3] = 2
    changed = bytearray(binary)
    attributes["TANGENT"] = _append_attribute(document, changed, tangents)
    _write_glb(source, document, changed)
    output = tmp_path / "negative"
    output.mkdir()
    with pytest.raises(ValueError, match="INVALID_PARTICIPATING_SOURCE_CORNERS"):
        mirror_asset({"snapshot": str(parent.root), "allow_geometry_change": True,
            "material_scope": "preserve_pbr", "keep_positive": False}, output, time.monotonic() + 60)


def test_declared_mirror_frames_do_not_depend_on_unrelated_regeneration(tmp_path: Path,
                                                                      monkeypatch: pytest.MonkeyPatch) -> None:
    from reseam.agent_tools import material

    parent = source_asset(tmp_path)

    def unrelated_regeneration(*args: object, **kwargs: object) -> None:
        raise AssertionError("declared frame path must not regenerate source tangents")

    monkeypatch.setattr(material, "mikktspace_frames", unrelated_regeneration)
    result = mirror(parent, tmp_path / "mirror")
    shared = load_snapshot(Path(str(result["shared_snapshot"])))
    source, _ = _reference(shared, {})
    assert cast(dict[str, object], source.metadata["source_mikk"])["status"] == "NOT_RUN"


def test_custom_authored_mirror_frame_cannot_enter_unverified_blender_bake(tmp_path: Path) -> None:
    parent = source_asset(tmp_path)
    source = Path(str(parent.metadata["reference_glb"]))
    document, binary = _source_document(source)
    mesh = cast(list[dict[str, object]], document["meshes"])[0]
    primitive = cast(list[dict[str, object]], mesh["primitives"])[0]
    attributes = cast(dict[str, int], primitive["attributes"])
    normals = _accessor(document, binary, attributes["NORMAL"])
    tangents = _accessor(document, binary, attributes["TANGENT"])
    tangents[:, :3] = np.cross(normals, tangents[:, :3])
    changed = bytearray(binary)
    attributes["TANGENT"] = _append_attribute(document, changed, tangents)
    _write_glb(source, document, changed)
    result = mirror(parent, tmp_path / "mirror")
    shared = load_snapshot(Path(str(result["shared_snapshot"])))
    reference, reference_path = _reference(shared, {})
    assert reference.metadata["custom_source_tangents"] is None
    with pytest.raises(ValueError, match="UNSUPPORTED_BLENDER_REFERENCE:UNVERIFIED_SOURCE_TANGENTS"):
        _blender_atlas(shared, {}, reference, reference_path, tmp_path / "not_run", time.monotonic() + 60)
    assert not (tmp_path / "not_run").exists()


def test_same_surface_target_refuses_wrong_source_and_never_calls_nearest(tmp_path: Path,
                                                                       monkeypatch: pytest.MonkeyPatch) -> None:
    from reseam.agent_tools.material import _SamplerReference

    parent = source_asset(tmp_path)
    output = tmp_path / "target"
    output.mkdir()
    deadline = time.monotonic() + 60
    request: dict[str, object] = {"snapshot": str(parent.root), "mode": "pbr_reuse",
                                 "projection_method": "same_surface_face_corner", "acceptance_policy": "visual"}
    frozen = freeze_target(request, output, deadline)
    target = PBRTarget(parent, Path(str(frozen["target"])), deadline)

    def refuse_nearest(*args: object, **kwargs: object) -> None:
        raise AssertionError("same-surface must not query nearest geometry")

    monkeypatch.setattr(_SamplerReference, "sample", refuse_nearest)
    face_ids, bary = np.asarray([0, 1]), np.full((2, 3), 1 / 3)
    assert target.original(face_ids, bary).valid.all()
    changed = parent.state.copy()
    changed.surface.mesh = make_mesh(changed.surface.mesh.vertices + .01, changed.surface.mesh.faces)
    other = save_snapshot(changed, tmp_path / "wrong_geometry", parent.metadata)
    output2 = tmp_path / "wrong_target"
    output2.mkdir()
    with pytest.raises(ValueError, match="FACE_CORNER_IDENTITY_MISMATCH"):
        freeze_target({**request, "snapshot": str(other.root)}, output2, deadline)
