"""Blender's native Smart UV operator with MeshQuery Appendix A.4 settings."""

import json
import math
import sys
from pathlib import Path

import bpy


def main() -> None:
    request = json.loads(Path(sys.argv[sys.argv.index("--") + 1]).read_text(encoding="utf-8"))
    mesh = bpy.data.meshes.new("FixedInput")
    mesh.from_pydata(request["vertices"], [], request["faces"])
    mesh.update()
    obj = bpy.data.objects.new("FixedInput", mesh)
    bpy.context.collection.objects.link(obj)
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    mesh.uv_layers.new(name="UVMap")
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.uv.smart_project(angle_limit=math.radians(request["angle_degrees"]),
                             island_margin=request["island_margin"])
    bpy.ops.uv.select_all(action="SELECT")
    bpy.ops.uv.seams_from_islands(mark_seams=True, mark_sharp=False)
    bpy.ops.object.mode_set(mode="OBJECT")
    result = {"corner_uv": [[list(mesh.uv_layers.active.data[i].uv) for i in p.loop_indices]
                             for p in mesh.polygons],
              "seams": [sorted(e.vertices) for e in mesh.edges if e.use_seam],
              "version": bpy.app.version_string, "threads": 1}
    Path(request["output"]).write_text(json.dumps(result), encoding="utf-8")


if __name__ == "__main__":
    main()
