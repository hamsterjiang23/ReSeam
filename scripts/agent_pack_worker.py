"""Isolated Blender contour packing; never unwrap or merge original UV charts."""

import json
import sys
from pathlib import Path

import bpy  # pyright: ignore[reportMissingImports] -- Blender-only worker
import numpy as np


def main() -> None:
    request = json.loads(Path(sys.argv[sys.argv.index("--") + 1]).read_text(encoding="utf-8"))
    folder = Path(request["output"])
    settings = {"shape_method": "CONCAVE", "rotate": True, "rotate_method": "ANY",
                "margin_method": "FRACTION", "margin": float(request["margin_fraction"]),
                "merge_overlap": False, "scale": True, "pin": False}
    capabilities = {
        name: [item.identifier for item in bpy.ops.uv.pack_islands.get_rna_type().properties[name].enum_items]
        for name in ("shape_method", "rotate_method", "margin_method")
    }
    for name, choices in capabilities.items():
        if settings[name] not in choices:
            raise ValueError("BLENDER_PACK_CAPABILITY_MISSING:" + name)
    with np.load(request["input"], allow_pickle=False) as arrays:
        uv, faces = arrays["uv"], arrays["faces"]
        source_faces, owners = arrays["source_faces"], arrays["owners"]
    bpy.ops.wm.read_factory_settings(use_empty=True)
    mesh = bpy.data.meshes.new("ReSeamPackingMasters")
    mesh.from_pydata(np.column_stack((uv, np.zeros(len(uv)))).tolist(), [], faces.tolist())
    mesh.update()
    obj = bpy.data.objects.new("ReSeamPackingMasters", mesh)
    bpy.context.collection.objects.link(obj)
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    layer = mesh.uv_layers.new(name="ReSeamUV")
    for polygon in mesh.polygons:
        for loop_id, vertex in zip(polygon.loop_indices, polygon.vertices, strict=True):
            layer.data[loop_id].uv = uv[vertex]
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.uv.select_all(action="SELECT")
    result = bpy.ops.uv.pack_islands(**settings)
    bpy.ops.object.mode_set(mode="OBJECT")
    if "FINISHED" not in result:
        raise ValueError("BLENDER_PACK_NOT_FINISHED")
    actual = np.asarray([list(p.vertices) for p in mesh.polygons], dtype=np.int64)
    if not np.array_equal(actual, faces):
        raise ValueError("BLENDER_PACK_FACE_IDENTITY_CHANGED")
    layer = mesh.uv_layers.active
    corners = np.asarray([[list(layer.data[i].uv) for i in p.loop_indices] for p in mesh.polygons],
                         dtype=np.float64)
    if not np.isfinite(corners).all():
        raise ValueError("BLENDER_PACK_NONFINITE_UV")
    np.savez_compressed(folder / "packed.npz", faces=actual, corner_uv=corners,
                        source_faces=source_faces, owners=owners)
    report = {"backend": "blender_concave", "blender_version": bpy.app.version_string,
              "settings": settings, "capabilities": capabilities, "threads": 1,
              "device": "CPU", "face_count": len(faces), "unwrap_called": False}
    (folder / "backend.json").write_text(json.dumps(report, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
