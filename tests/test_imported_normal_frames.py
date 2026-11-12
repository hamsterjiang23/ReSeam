"""Imported shading normals remain bound through UV edits and material export."""

import json
import os
import struct
import subprocess
import time
from pathlib import Path
from typing import cast

import numpy as np
import pytest
from PIL import Image

from reseam.agent_tools.material import _reference
from reseam.agent_tools.normal_policy import repair_normals
from reseam.agent_tools.preflight import normal_uv_audit
from reseam.agent_tools.state import load_snapshot, save_snapshot
from reseam.bake_variant_bake import DEFAULT_BLENDER, BakeVariantRequest, bake_variant
from reseam.budget_material import (
    MaterialReference,
    atlas_from_arrays,
    bake_material,
    export_material_glb,
    frame_arrays,
    interpolated_frames,
    load_material_reference,
    sample_material,
)
from reseam.budget_state import BudgetState
from reseam.budget_texture import ReferenceSurface
from reseam.mesh import make_mesh, state_from_corner_uv
from scripts.preflight_examples import cylinder, toolbox


def test_smooth_cylinder_survives_snapshot_uv_edit_and_export(tmp_path: Path) -> None:
    original = cylinder(tmp_path / "original")
    state = original.state.copy()
    record = cast(dict[str, object], original.metadata["hard_surface_source"])
    state.surface.corner_normals = np.asarray(record["corner_normals"], dtype=np.float64)
    source_normals = state.surface.corner_normals.copy()
    imported = save_snapshot(state, tmp_path / "imported", original.metadata)
    assert imported.state_hash != original.state_hash
    assert load_snapshot(original.root).state.surface.corner_normals is None
    assert normal_uv_audit(imported, None, .1)["status"] == "pass"
    # A UV tool reconstructs only UV topology. Snapshot inheritance retains shading.
    surface = state_from_corner_uv(state.surface.mesh,
        state.surface.uv[state.surface.uv_faces] * .9, corner_ids=state.surface.uv_faces)
    child = save_snapshot(BudgetState(surface), tmp_path / "child", imported.metadata, imported)
    loaded = load_snapshot(child.root)
    assert loaded.state.surface.corner_normals is not None
    assert np.array_equal(loaded.state.surface.corner_normals, source_normals)
    copied = loaded.state.surface.copy()
    assert copied.corner_normals is not None
    copied.corner_normals[0] *= -1
    assert np.array_equal(loaded.state.surface.corner_normals, source_normals)
    color = np.full((128, 128, 4), [120, 80, 40, 255], dtype=np.uint8)
    normal = np.full_like(color, [128, 128, 255, 255])
    orm = np.full_like(color, [255, 180, 0, 255])
    atlas = atlas_from_arrays(color.astype(float) / 255.,
        normal[..., :3].astype(float) / 127.5 - 1., orm[..., :3].astype(float) / 255.)
    path = tmp_path / "smooth.glb"
    export_material_glb(loaded.state.surface, atlas, path)
    ref = load_material_reference(path)
    expected_n, expected_t, expected_s = frame_arrays(loaded.state.surface)
    assert np.allclose(ref.normals, expected_n, atol=1e-6)
    assert np.allclose(ref.tangents, expected_t, atol=1e-6)
    assert np.array_equal(ref.signs, expected_s)
    assert np.max(np.abs(expected_n - frame_arrays(original.state.surface)[0])) > .1


def test_mirrored_uv_rebake_preserves_world_normal_with_imported_smoothing(tmp_path: Path) -> None:
    snap = cylinder(tmp_path / "cylinder")
    surface = snap.state.surface
    record = cast(dict[str, object], snap.metadata["hard_surface_source"])
    surface.corner_normals = np.asarray(record["corner_normals"], dtype=np.float64)
    original_frames = frame_arrays(surface)
    color = np.full((4, 4, 4), [128, 64, 32, 255], dtype=np.uint8)
    normal = np.full_like(color, [165, 110, 247, 255])
    orm = np.full_like(color, [255, 160, 25, 255])
    reference = MaterialReference(ReferenceSurface(surface.mesh.vertices, surface.mesh.faces,
        surface.uv[surface.uv_faces], color, source_has_normal=True, source_has_orm=True),
        normal, orm, *original_frames)
    mirrored = surface.copy()
    mirrored.uv[:, 0] = 1 - mirrored.uv[:, 0]
    new_frames = frame_arrays(mirrored)
    assert np.array_equal(new_frames[0], original_frames[0])
    assert np.array_equal(new_frames[2], -original_frames[2])
    ids = np.arange(len(surface.mesh.faces), dtype=np.int64)
    bary = np.tile([.3, .3, .4], (len(ids), 1))
    atlas = bake_material(mirrored, reference, 128, padding_pixels=4)
    actual = sample_material(mirrored, atlas, ids, bary)
    xyz = np.einsum("fc,fcj->fj", bary, surface.mesh.vertices[surface.mesh.faces])
    expected = reference.sample(xyz, .01 * surface.mesh.scale)
    assert actual.valid.all() and expected.valid.all()
    angle = np.degrees(np.arccos(np.clip(np.sum(actual.normal_world * expected.normal_world, axis=1), -1., 1.)))
    assert angle.max() < 2.
    matrix, valid = interpolated_frames(*new_frames, ids, bary)
    assert valid.all()
    assert np.allclose(matrix @ np.swapaxes(matrix, 1, 2), np.eye(3), atol=1e-7)


def test_hard_box_keeps_face_normals_and_invalid_values_reject(tmp_path: Path) -> None:
    snap = toolbox(tmp_path / "box")
    record = cast(dict[str, object], snap.metadata["hard_surface_source"])
    surface = snap.state.surface
    legacy = frame_arrays(surface)
    surface.corner_normals = np.asarray(record["corner_normals"], dtype=np.float64)
    imported = frame_arrays(surface)
    assert np.allclose(imported[0], legacy[0])
    assert normal_uv_audit(snap, None, .1)["status"] == "pass"
    surface.corner_normals[0, 0] = 0
    with pytest.raises(ValueError, match="invalid imported corner normals"):
        frame_arrays(surface)
    with pytest.raises(ValueError, match="SNAPSHOT_CORNER_NORMALS_INVALID"):
        save_snapshot(snap.state, tmp_path / "invalid", snap.metadata)


def test_explicit_bad_normal_fan_repair_preserves_geometry_uv_and_parent(tmp_path: Path) -> None:
    mesh = make_mesh(np.asarray([[0., 0., 0.], [1., 0., 0.], [0., 1., 0.]]), np.asarray([[0, 1, 2]]))
    surface = state_from_corner_uv(mesh, mesh.vertices[mesh.faces, :2], corner_ids=mesh.faces)
    surface.corner_normals = np.tile([1., 0., 0.], (1, 3, 1))
    source = save_snapshot(BudgetState(surface), tmp_path / "source", {})
    with pytest.raises(ValueError, match="MIKKTSPACE_INVALID_FRAMES"):
        frame_arrays(source.state.surface)
    result = repair_normals({"snapshot": str(source.root)}, tmp_path / "repair", time.monotonic()+30)
    repaired = load_snapshot(Path(str(result["snapshot"])))
    assert result["changed_corner_count"] == 3
    assert np.array_equal(repaired.state.surface.mesh.faces, mesh.faces)
    assert np.array_equal(repaired.state.surface.mesh.vertices, mesh.vertices)
    assert np.array_equal(repaired.state.surface.uv, surface.uv)
    assert np.allclose(frame_arrays(repaired.state.surface)[0], [0., 0., 1.])
    saved_normals = load_snapshot(source.root).state.surface.corner_normals
    assert saved_normals is not None
    assert np.allclose(saved_normals, [1., 0., 0.])


@pytest.mark.skipif(not DEFAULT_BLENDER.is_file(), reason="Blender unavailable")
@pytest.mark.parametrize("mirror", [False, True])
def test_blender_bake_imported_corner_frame_and_independent_readback(tmp_path: Path, mirror: bool) -> None:
    mesh = make_mesh(np.asarray([[0., 0., 0.], [1., 0., 0.], [0., 1., 0.]]), np.asarray([[0, 1, 2]]))
    surface = state_from_corner_uv(mesh, np.asarray([[[.1, .1], [.9, .1], [.1, .9]]]), corner_ids=mesh.faces)
    atlas = atlas_from_arrays(np.full((8, 8, 4), [.2, .3, .4, 1.]),
        np.full((8, 8, 3), [.2, -.3, .93]), np.full((8, 8, 3), [1., .6, .2]))
    source = tmp_path / "source.glb"
    export_material_glb(surface, atlas, source)
    surface.corner_normals = np.asarray([[[0., 0., 1.], [.3, 0., .95], [0., .3, .95]]])
    surface.corner_normals /= np.linalg.norm(surface.corner_normals, axis=-1, keepdims=True)
    if mirror:
        surface.uv[:, 0] = 1 - surface.uv[:, 0]
    result = bake_variant(BakeVariantRequest(mesh.vertices, mesh.faces, surface.uv[surface.uv_faces],
        source, 128, .1, 4, surface.corner_normals), tmp_path / "bake", deadline=time.monotonic()+120)
    assert result["status"] == "BAKED_THREE_CHANNELS"
    ref = load_material_reference(tmp_path / "bake/asset.glb")
    assert surface.corner_normals is not None
    assert np.allclose(ref.normals, surface.corner_normals, atol=1e-6)
    with Image.open(tmp_path / "bake/Normal.png") as image:
        pixels = np.asarray(image, dtype=float) / 255. * 2. - 1.
    raw_glb = (tmp_path / "bake/asset.glb").read_bytes()
    json_size = struct.unpack_from("<I", raw_glb, 12)[0]
    assert json.loads(raw_glb[20:20+json_size])["materials"][0]["doubleSided"] is True
    x, y = (79 if mirror else 48), 79
    uv = np.asarray([(x+.5)/128., 1.-(y+.5)/128.])
    if mirror:
        uv[0] = 1-uv[0]
    u, v = (uv-.1)/.8
    frames, valid = interpolated_frames(*frame_arrays(surface), np.asarray([0]), np.asarray([[1-u-v, u, v]]))
    assert valid.all()
    actual = frames[0] @ pixels[y, x, :3]
    actual /= np.linalg.norm(actual)
    expected = np.asarray([.2, .3, .93])
    expected /= np.linalg.norm(expected)
    assert actual == pytest.approx(expected, abs=.025)


@pytest.mark.parametrize("contract", ["native", "untagged_native", "legacy", "legacy_old"])
def test_missing_source_tangents_respect_native_or_legacy_frame_contract(tmp_path: Path, contract: str) -> None:
    mesh = make_mesh(np.asarray([[0., 0., 0.], [1., 0., 0.], [0., 1., 0.]]), np.asarray([[0, 1, 2]]))
    surface = state_from_corner_uv(mesh, np.asarray([[[.1, .1], [.9, .1], [.1, .9]]]), corner_ids=mesh.faces)
    if contract in ("native", "untagged_native"):
        surface.corner_normals = np.tile([0., 0., 1.], (1, 3, 1))
    atlas = atlas_from_arrays(np.full((8, 8, 4), [.2, .3, .4, 1.]),
        np.full((8, 8, 3), [.2, .6, .77]), np.full((8, 8, 3), [1., .5, 0.]))
    source = tmp_path / "source.glb"
    export_material_glb(surface, atlas, source)
    explicit = load_material_reference(source)
    snapshot = save_snapshot(BudgetState(surface), tmp_path / "source_snapshot", {"reference_glb": str(source)})
    public_explicit, _ = _reference(snapshot, {})
    assert public_explicit.metadata["custom_source_tangents"] is False
    raw = source.read_bytes(); size = struct.unpack_from("<I", raw, 12)[0]
    document = json.loads(raw[20:20+size])
    del document["meshes"][0]["primitives"][0]["attributes"]["TANGENT"]
    if contract == "untagged_native":
        del document["extras"]
    elif contract == "legacy_old":
        del document["extras"]["reseam"]["tangent_basis"]
    encoded = json.dumps(document, separators=(",", ":")).encode()
    encoded += b" " * (-len(encoded) % 4)
    binary = raw[20+size:]
    source.write_bytes(struct.pack("<III", 0x46546C67, 2, 20+len(encoded)+len(binary))
        + struct.pack("<II", len(encoded), 0x4E4F534A) + encoded + binary)
    fallback = load_material_reference(source)
    public_fallback, _ = _reference(snapshot, {})
    np.testing.assert_allclose(fallback.tangents, explicit.tangents, atol=1e-7)
    np.testing.assert_array_equal(fallback.signs, explicit.signs)
    points = np.asarray([[.2, .3, 0.]])
    np.testing.assert_allclose(fallback.sample(points, .01).normal_world,
                               explicit.sample(points, .01).normal_world, atol=1e-7)
    np.testing.assert_allclose(public_fallback.tangents, public_explicit.tangents, atol=1e-7)
    np.testing.assert_array_equal(public_fallback.signs, public_explicit.signs)
    np.testing.assert_allclose(public_fallback.sample(points, .01).normal_world,
                               public_explicit.sample(points, .01).normal_world, atol=1e-7)


@pytest.mark.skipif(not DEFAULT_BLENDER.is_file(), reason="Blender unavailable")
@pytest.mark.parametrize("backface", [False, True])
def test_native_blender_reimport_renders_asymmetric_normal_direction(tmp_path: Path, backface: bool) -> None:
    """Render the actual imported GLB shader, without any compensating green flip."""
    mesh = make_mesh(np.asarray([[0., 0., 0.], [1., 0., 0.], [0., 1., 0.]]), np.asarray([[0, 1, 2]]))
    surface = state_from_corner_uv(mesh, np.asarray([[[.1, .1], [.9, .1], [.1, .9]]]), corner_ids=mesh.faces)
    surface.corner_normals = np.tile([0., 0., 1.], (1, 3, 1))
    mapped = np.asarray([.2, .6, .77]); mapped /= np.linalg.norm(mapped)
    atlas = atlas_from_arrays(np.full((8, 8, 4), [.4, .2, .1, 1.]),
        np.tile(mapped, (8, 8, 1)), np.full((8, 8, 3), [1., .5, 0.]))
    export_material_glb(surface, atlas, tmp_path / "native.glb")
    worker = tmp_path / "render.py"
    worker.write_text('''import sys
from pathlib import Path
import bpy
from mathutils import Vector
root=Path(sys.argv[sys.argv.index("--")+1])
backface=sys.argv[-1]=="back"
bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.import_scene.gltf(filepath=str(root/"native.glb"))
obj=next(o for o in bpy.context.scene.objects if o.type=="MESH")
tree=obj.data.materials[0].node_tree
normal=next(n for n in tree.nodes if n.type=="NORMAL_MAP")
vector=tree.nodes.new("ShaderNodeVectorMath");vector.operation="MULTIPLY_ADD"
vector.inputs[1].default_value=(.5,.5,.5);vector.inputs[2].default_value=(.5,.5,.5)
tree.links.new(normal.outputs[0],vector.inputs[0])
emission=tree.nodes.new("ShaderNodeEmission");tree.links.new(vector.outputs[0],emission.inputs[0])
output=next(n for n in tree.nodes if n.type=="OUTPUT_MATERIAL")
tree.links.new(emission.outputs[0],output.inputs["Surface"])
scene=bpy.context.scene;scene.render.engine="CYCLES";scene.cycles.samples=1;scene.cycles.device="CPU"
scene.render.threads_mode="FIXED";scene.render.threads=1
camera=bpy.data.objects.new("camera",bpy.data.cameras.new("camera"));scene.collection.objects.link(camera)
camera.location=(.3,2 if backface else -2,.3);camera.rotation_euler=(Vector((.3,0,.3))-camera.location).to_track_quat("-Z","Y").to_euler()
camera.data.type="ORTHO";camera.data.ortho_scale=.3;scene.camera=camera
scene.render.resolution_x=scene.render.resolution_y=32;scene.render.resolution_percentage=100
scene.view_settings.view_transform="Raw";scene.view_settings.look="None";scene.view_settings.exposure=0;scene.view_settings.gamma=1
scene.render.image_settings.file_format="PNG";scene.render.filepath=str(root/"normal_world.png")
bpy.ops.render.render(write_still=True)
''', encoding="utf-8")
    with (tmp_path / "render.log").open("w", encoding="utf-8") as log:
        subprocess.run([str(DEFAULT_BLENDER), "--background", "--factory-startup", "--threads", "1",
            "--python-exit-code", "1", "--python", str(worker), "--", str(tmp_path), "back" if backface else "front"],
            stdout=log, stderr=subprocess.STDOUT, timeout=90, check=True,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    with Image.open(tmp_path / "normal_world.png") as image:
        actual = np.asarray(image)[16, 16, :3].astype(float) / 255. * 2 - 1
    expected_blender = mapped[[0, 2, 1]] * [1, -1, 1]
    if backface:
        expected_blender *= -1
    assert actual == pytest.approx(expected_blender, abs=.02)


@pytest.mark.skipif(not DEFAULT_BLENDER.is_file(), reason="Blender unavailable")
def test_native_blender_bsdf_backface_matches_transported_low_frontface(tmp_path: Path) -> None:
    """True source backface and opposite low winding must shade identically."""
    vertices = np.asarray([[0., 0., 0.], [1., 0., 0.], [0., 1., 0.]])
    mesh = make_mesh(vertices, np.asarray([[0, 1, 2]]))
    source = state_from_corner_uv(mesh, vertices[mesh.faces, :2] * .8 + .1, corner_ids=mesh.faces)
    source.corner_normals = np.tile([0., 0., 1.], (1, 3, 1))
    mapped = np.asarray([.2, .6, .77]); mapped /= np.linalg.norm(mapped)
    color = np.full((8, 8, 4), [.5, .5, .5, 1.])
    orm = np.full((8, 8, 3), [1., .8, 0.])
    export_material_glb(source, atlas_from_arrays(color, np.tile(mapped, (8, 8, 1)), orm), tmp_path / "source.glb")
    low_mesh = make_mesh(vertices, np.asarray([[0, 2, 1]]))
    low = state_from_corner_uv(low_mesh, vertices[low_mesh.faces, :2] * .8 + .1, corner_ids=low_mesh.faces)
    low.corner_normals = np.tile([0., 0., -1.], (1, 3, 1))
    source_frames, _ = interpolated_frames(*frame_arrays(source), np.asarray([0]), np.asarray([[1/3]*3]))
    low_frames, _ = interpolated_frames(*frame_arrays(low), np.asarray([0]), np.asarray([[1/3]*3]))
    low_mapped = low_frames[0].T @ -(source_frames[0] @ mapped)
    export_material_glb(low, atlas_from_arrays(color, np.tile(low_mapped, (8, 8, 1)), orm), tmp_path / "low.glb")
    worker = tmp_path / "render_bsdf.py"
    worker.write_text('''import sys
from pathlib import Path
import bpy
from mathutils import Vector
root=Path(sys.argv[sys.argv.index("--")+1])
for name in ("source", "low"):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.import_scene.gltf(filepath=str(root/(name+".glb")))
    scene=bpy.context.scene;scene.render.engine="CYCLES";scene.cycles.samples=8;scene.cycles.device="CPU"
    scene.render.threads_mode="FIXED";scene.render.threads=1;scene.cycles.use_denoising=False
    camera=bpy.data.objects.new("camera",bpy.data.cameras.new("camera"));scene.collection.objects.link(camera)
    camera.location=(.3,2,.3);camera.rotation_euler=(Vector((.3,0,.3))-camera.location).to_track_quat("-Z","Y").to_euler()
    camera.data.type="ORTHO";camera.data.ortho_scale=.3;scene.camera=camera
    sun=bpy.data.objects.new("sun",bpy.data.lights.new("sun","SUN"));scene.collection.objects.link(sun)
    sun.data.energy=2;sun.data.angle=0;sun.rotation_euler=Vector((0,-1,0)).to_track_quat("-Z","Y").to_euler()
    scene.render.resolution_x=scene.render.resolution_y=32;scene.render.resolution_percentage=100
    scene.view_settings.view_transform="Raw";scene.view_settings.look="None";scene.view_settings.exposure=0;scene.view_settings.gamma=1
    scene.render.image_settings.file_format="PNG";scene.render.filepath=str(root/(name+".png"))
    bpy.ops.render.render(write_still=True)
''', encoding="utf-8")
    with (tmp_path / "render_bsdf.log").open("w", encoding="utf-8") as log:
        subprocess.run([str(DEFAULT_BLENDER), "--background", "--factory-startup", "--threads", "1",
            "--python-exit-code", "1", "--python", str(worker), "--", str(tmp_path)],
            stdout=log, stderr=subprocess.STDOUT, timeout=90, check=True,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    with Image.open(tmp_path / "source.png") as image:
        expected = np.asarray(image)[8:24, 8:24, :3].astype(float) / 255.
    with Image.open(tmp_path / "low.png") as image:
        actual = np.asarray(image)[8:24, 8:24, :3].astype(float) / 255.
    assert expected.mean() > .1
    assert np.max(np.abs(actual - expected)) < .025
