"""Actual Blender export verifies explicit channels instead of legacy Phong fields."""

import json
import os
import struct
import subprocess
import time
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from reseam.agent_tools.assets import import_asset
from reseam.agent_tools.state import load_snapshot
from reseam.bake_variant_bake import DEFAULT_BLENDER


@pytest.mark.skipif(not DEFAULT_BLENDER.is_file(), reason="real Blender not available")
@pytest.mark.parametrize("partial", [False, True])
def test_explicit_pbr_import_retains_normals_and_core_material(tmp_path: Path, partial: bool) -> None:
    source = tmp_path / "input.fbx"
    script = tmp_path / "fixture.py"
    script.write_text(
        "import bpy\n"
        "bpy.ops.wm.read_factory_settings(use_empty=True)\n"
        "bpy.ops.mesh.primitive_plane_add()\n"
        f"bpy.ops.export_scene.fbx(filepath={str(source)!r},bake_anim=False)\n",
        encoding="utf-8")
    subprocess.run([str(DEFAULT_BLENDER), "--background", "--factory-startup", "--threads", "1",
                    "--python-exit-code", "1", "--python", str(script)], check=True,
                   capture_output=True, timeout=30,
                   creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    textures: dict[str, str] = {}
    for channel, color in {"basecolor": (64, 128, 192), "normal": (128, 192, 238),
                           "roughness": (80, 80, 80), "metallic": (210, 210, 210)}.items():
        path = tmp_path / (channel + ".png")
        Image.new("RGB", (8, 8), color).save(path)
        textures[channel] = str(path)
    if partial:
        textures = {k: v for k, v in textures.items() if k in {"basecolor", "normal"}}
    output = tmp_path / "import"
    output.mkdir()
    result = import_asset({"path": str(source), "uv_channel": 0, "pbr_textures": textures,
        "normal_convention": "directx", "collect_normal_evidence": True,
        "target_normal_policy": "imported", "repair_nonmanifold": "split_fans",
        "pbr_factors": {"roughness": .6, "metallic": 0}},
        output, time.monotonic() + 30)
    snapshot = load_snapshot(Path(str(result["snapshot"])))
    assert snapshot.state.surface.corner_normals is not None
    np.testing.assert_allclose(np.linalg.norm(snapshot.state.surface.corner_normals, axis=2), 1.)
    data = Path(str(snapshot.metadata["reference_glb"])).read_bytes()
    document = json.loads(data[20:20 + struct.unpack_from("<I", data, 12)[0]])
    material = document["materials"][0]
    assert "normalTexture" in material
    assert "baseColorTexture" in material["pbrMetallicRoughness"]
    if partial:
        assert material["pbrMetallicRoughness"]["roughnessFactor"] == pytest.approx(.6)
        assert material["pbrMetallicRoughness"]["metallicFactor"] == 0
    else:
        assert "metallicRoughnessTexture" in material["pbrMetallicRoughness"]
    assert not material.get("extensions")
    assert "TANGENT" in document["meshes"][0]["primitives"][0]["attributes"]
    with Image.open(output / "inputs/normal_opengl.png") as image:
        assert abs(int(np.asarray(image)[0, 0, 1]) - 63) <= 1
    assert Path(textures["normal"]).read_bytes() == (output / "inputs/pbr/normal/normal.png").read_bytes()
