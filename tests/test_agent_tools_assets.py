"""Actual FBX conversion and conservative identity, without an interactive Blender."""

import json
import os
import subprocess
import time
from pathlib import Path

import pytest

from reseam.agent_tools.assets import import_asset
from reseam.agent_tools.state import load_snapshot
from reseam.bake_variant_bake import DEFAULT_BLENDER


@pytest.mark.skipif(not DEFAULT_BLENDER.is_file(), reason="real Blender not available")
@pytest.mark.parametrize("animated", [False, True])
def test_fbx_static_identity_and_animated_parent_refusal(tmp_path: Path, animated: bool) -> None:
    source = tmp_path / "source.fbx"
    script = tmp_path / "make_fixture.py"
    script.write_text(
        "import bpy\n"
        "bpy.ops.wm.read_factory_settings(use_empty=True)\n"
        "mesh=bpy.data.meshes.new('Mesh')\n"
        "mesh.from_pydata([(0,0,0),(1,0,0),(1,1,0),(0,1,0)],[],[(0,1,2),(0,2,3)])\n"
        "obj=bpy.data.objects.new('Panel',mesh);bpy.context.collection.objects.link(obj)\n"
        "uv=mesh.uv_layers.new(name='Artist')\n"
        "values=[(.1,.1),(.4,.1),(.4,.4),(.6,.1),(.9,.4),(.6,.4)]\n"
        "for i,value in enumerate(values): uv.data[i].uv=value\n"
        "parent=bpy.data.objects.new('Parent',None);bpy.context.collection.objects.link(parent);obj.parent=parent\n"
        + ("parent.keyframe_insert(data_path='location',frame=1)\nparent.location.x=1\nparent.keyframe_insert(data_path='location',frame=2)\n" if animated else "")
        + f"bpy.ops.export_scene.fbx(filepath={json.dumps(str(source))},bake_anim={animated})\n",
        encoding="utf-8",
    )
    subprocess.run([str(DEFAULT_BLENDER), "--background", "--factory-startup", "--threads", "1",
                    "--python-exit-code", "1", "--python", str(script)], check=True, capture_output=True,
                   timeout=30, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    output = tmp_path / "import"
    output.mkdir()
    if animated:
        with pytest.raises(ValueError, match="FBX_IMPORT_REJECTED"):
            import_asset({"path": str(source)}, output, time.monotonic() + 30)
        assert not (output / "snapshot").exists()
    else:
        result = import_asset({"path": str(source)}, output, time.monotonic() + 30)
        snapshot = load_snapshot(Path(str(result["snapshot"])))
        assert len(snapshot.state.surface.mesh.faces) == 2
        assert len(snapshot.state.surface.uv) == 6
        assert snapshot.metadata["active_uv_channel"] == "per-object active layer in metadata"
