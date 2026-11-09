"""Isolated Blender UV parameterization; input triangles and corner order are fixed."""

import json
import sys
from pathlib import Path

import bpy  # pyright: ignore[reportMissingImports] -- isolated Blender runtime
import numpy as np


def main() -> None:
    request = json.loads(Path(sys.argv[sys.argv.index("--") + 1]).read_text(encoding="utf-8"))
    output = Path(request["output"])
    method = request["method"]
    capabilities = [item.identifier for item in bpy.ops.uv.unwrap.get_rna_type().properties["method"].enum_items]
    report = {"blender_version": bpy.app.version_string, "available_methods": capabilities,
              "requested_method": method, "threads": 1, "device": "CPU"}
    (output / "capabilities.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    if method not in capabilities:
        raise ValueError("BLENDER_UNWRAP_METHOD_UNAVAILABLE:" + method)
    with np.load(request["input"], allow_pickle=False) as arrays:
        vertices, faces = arrays["vertices"], arrays["faces"]
    bpy.ops.wm.read_factory_settings(use_empty=True)
    mesh = bpy.data.meshes.new("ReSeamInput")
    mesh.from_pydata(vertices.tolist(), [], faces.tolist())
    mesh.update()
    obj = bpy.data.objects.new("ReSeamInput", mesh)
    bpy.context.collection.objects.link(obj)
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    mesh.uv_layers.new(name="ReSeamUV")
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    result = bpy.ops.uv.unwrap(method=method, fill_holes=False, correct_aspect=False,
                               use_subsurf_data=False, margin=0.0)
    bpy.ops.object.mode_set(mode="OBJECT")
    layer = mesh.uv_layers.active
    if "FINISHED" not in result:
        raise ValueError("BLENDER_UNWRAP_NOT_FINISHED")
    actual_faces = np.asarray([list(p.vertices) for p in mesh.polygons], dtype=np.int64)
    if not np.array_equal(actual_faces, faces):
        raise ValueError("BLENDER_FACE_CORNER_ORDER_CHANGED")
    corners = np.asarray([[list(layer.data[index].uv) for index in p.loop_indices] for p in mesh.polygons])
    if not np.isfinite(corners).all():
        raise ValueError("BLENDER_NONFINITE_UV")
    # Temporary cut vertices already encode all requested seams. Refuse an
    # unreported extra UV split instead of inferring connectivity by proximity.
    by_vertex: dict[int, np.ndarray] = {}
    for face, values in zip(faces, corners, strict=True):
        for vertex, value in zip(face, values, strict=True):
            vertex_id = int(vertex)
            if vertex_id in by_vertex and not np.array_equal(by_vertex[vertex_id], value):
                raise ValueError("BLENDER_UNREPORTED_UV_DISCONTINUITY:" + str(vertex_id))
            by_vertex[vertex_id] = value
    np.savez_compressed(output / "unwrapped.npz", faces=actual_faces, corner_uv=corners,
                        corner_ids=faces, vertices=np.asarray([list(v.co) for v in mesh.vertices]))
    report.update({"status": "OK", "face_count": len(faces), "geometry_modified": False})
    (output / "backend.json").write_text(json.dumps(report, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
