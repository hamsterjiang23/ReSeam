"""Isolated FBX ingestion with original polygon/loop ancestry and glTF-axis output."""

import json
import sys
from pathlib import Path
from typing import Any

import bpy  # pyright: ignore[reportMissingImports] -- supplied by the isolated Blender runtime
import numpy as np


def main() -> None:
    request = json.loads(Path(sys.argv[sys.argv.index("--") + 1]).read_text(encoding="utf-8"))
    folder = Path(request["output"])
    bpy.ops.wm.read_factory_settings(use_empty=True)
    if Path(request["source"]).suffix.lower() == ".obj":
        bpy.ops.wm.obj_import(filepath=request["source"])
    else:
        bpy.ops.import_scene.fbx(filepath=request["source"])
    image_sources: list[dict[str, object]] = []
    for image in bpy.data.images:
        if image.source != "FILE":
            continue
        filename = Path(bpy.path.abspath(image.filepath)).name
        matches = [p for p in folder.rglob(filename) if p.is_file()]
        if len(matches) == 1:
            image.filepath = str(matches[0])
            image.reload()
        dimensions = tuple(image.size)  # force lazy file-backed pixels to load before checking has_data
        if not image.has_data or dimensions[0] <= 0:
            raise ValueError("FBX_SOURCE_IMAGE_MISSING:" + image.name + ":" + image.filepath)
        image_sources.append({"name": image.name, "file": bpy.path.abspath(image.filepath),
                              "width": image.size[0], "height": image.size[1]})
    if bpy.context.scene.animation_data or any(obj.animation_data or obj.constraints for obj in bpy.context.scene.objects):
        raise ValueError("UNSUPPORTED_DYNAMIC_ASSET: animated parents and constraints are not static input")
    meshes = sorted((o for o in bpy.context.scene.objects if o.type == "MESH"), key=lambda o: o.name)
    vertices: list[list[float]] = []
    faces: list[list[int]] = []
    uv: list[list[list[float]]] = []
    identities: list[list[int]] = []
    ancestry: list[dict[str, Any]] = []
    objects: list[dict[str, object]] = []
    polygons: list[list[int]] = []
    triangle_to_polygon: list[int] = []
    has_uv = True
    loop_offset = 0
    uv_presence: list[bool] = []
    for obj in meshes:
        if obj.modifiers or obj.data.shape_keys or obj.animation_data:
            raise ValueError("STATIC_MESH_REQUIRED")
        mesh = obj.data
        mesh.calc_loop_triangles()
        offset = len(vertices)
        polygon_offset = len(polygons)
        start = len(faces)
        for vertex in mesh.vertices:
            world = obj.matrix_world @ vertex.co
            vertices.append([float(world.x), float(world.z), -float(world.y)])
        for polygon in mesh.polygons:
            indices = [offset + int(v) for v in polygon.vertices]
            if obj.matrix_world.determinant() < 0:
                indices = [indices[0], *reversed(indices[1:])]
            polygons.append(indices)
        layer = mesh.uv_layers.active
        has_uv &= layer is not None
        uv_presence.append(layer is not None)
        for triangle in mesh.loop_triangles:
            loops = list(triangle.loops)
            if obj.matrix_world.determinant() < 0:
                loops = [loops[0], loops[2], loops[1]]
            face = [offset + mesh.loops[i].vertex_index for i in loops]
            values = [[float(x) for x in layer.data[i].uv] if layer else [0., 0.] for i in loops]
            # FBX's raw LayerElementUV indices are not exposed by Blender.
            # Preserve imported loop identity conservatively, never weld by UV values.
            ids = [loop_offset + i for i in loops]
            faces.append(face)
            triangle_to_polygon.append(polygon_offset + triangle.polygon_index)
            uv.append(values)
            identities.append(ids)
            ancestry.append({"object": obj.name, "polygon": triangle.polygon_index,
                             "loops": loops, "material": mesh.polygons[triangle.polygon_index].material_index})
        objects.append({"node": obj.name, "face_start": start, "face_count": len(faces) - start,
                        "uv_layers": [layer.name for layer in mesh.uv_layers],
                        "active_uv_index": mesh.uv_layers.active_index if layer else None,
                        "active_uv_name": layer.name if layer else None})
        loop_offset += len(mesh.loops)
    if any(uv_presence) and not all(uv_presence):
        raise ValueError("UNSUPPORTED_MIXED_UV_PRESENCE")
    if not faces:
        raise ValueError("NO_MESH_IN_FBX")
    np.savez_compressed(folder / "imported.npz", vertices=np.asarray(vertices), faces=np.asarray(faces),
                        corner_uv=np.asarray(uv), corner_ids=np.asarray(identities))
    (folder / "identity.json").write_text(json.dumps({"objects": objects, "ancestry": ancestry,
        "polygon_topology": {"polygons": polygons, "triangle_to_polygon": triangle_to_polygon,
                             "source": "Blender original polygons and loop_triangles; snapshot vertex indices"},
        "source_uv_present": has_uv, "images": image_sources,
        "coordinates": "glTF Y-up", "triangulation": "Blender loop_triangles"},
        ensure_ascii=False, indent=2), encoding="utf-8")
    bpy.ops.export_scene.gltf(filepath=str(folder / "reference.glb"), export_format="GLB",
                              export_yup=True, export_animations=False)


if __name__ == "__main__":
    main()
