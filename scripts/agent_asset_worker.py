"""Isolated FBX ingestion with original polygon/loop ancestry and glTF-axis output."""

import json
import sys
from pathlib import Path
from typing import Any, cast

import bpy  # pyright: ignore[reportMissingImports] -- supplied by the isolated Blender runtime
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from agent_fbx_uv import read_fbx_uv, restore_loop_uv_ids


def main() -> None:
    request = json.loads(Path(sys.argv[sys.argv.index("--") + 1]).read_text(encoding="utf-8"))
    folder = Path(request["output"])
    explicit_binding = bool(request.get("basecolor_texture"))
    bpy.ops.wm.read_factory_settings(use_empty=True)
    if Path(request["source"]).suffix.lower() == ".obj":
        bpy.ops.wm.obj_import(filepath=request["source"])
    else:
        bpy.ops.import_scene.fbx(filepath=request["source"], use_anim=not explicit_binding)
    raw_fbx: dict[str, dict[str, Any]] = {}
    raw_error: str | None = None
    if Path(request["source"]).suffix.lower() == ".fbx":
        try:
            raw_fbx = read_fbx_uv(Path(request["source"]))
        except (ValueError, KeyError, AttributeError, TypeError, OSError) as exc:
            raw_error = f"{type(exc).__name__}:{exc}"
    image_sources: list[dict[str, object]] = []
    for image in bpy.data.images:
        if explicit_binding:
            continue
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
    extra_uv: dict[str, list[list[list[float]]]] = {}
    extra_names: list[str] | None = None
    normal_corners: list[list[list[float]]] = []
    shading_objects: list[dict[str, object]] = []
    sharp_edges: list[list[int]] = []
    for obj in meshes:
        if obj.modifiers or obj.data.shape_keys or obj.animation_data:
            raise ValueError("STATIC_MESH_REQUIRED")
        mesh = obj.data
        mesh.calc_loop_triangles()
        offset = len(vertices)
        polygon_offset = len(polygons)
        start = len(faces)
        if request.get("collect_normal_evidence", False):
            shading_objects.append({"node": obj.name, "has_custom_normals": bool(mesh.has_custom_normals),
                "face_start": start, "face_count": len(mesh.loop_triangles),
                "raw_fbx_layers": raw_fbx.get(obj.name, {}).get("shading_layers", []),
                "raw_fbx_error": raw_error,
                "source": "Blender imported corner normals; custom and computed normals distinguished"})
            sharp_edges.extend([[offset + int(v) for v in edge.vertices]
                                for edge in mesh.edges if edge.use_edge_sharp])
        for vertex in mesh.vertices:
            world = obj.matrix_world @ vertex.co
            vertices.append([float(world.x), float(world.z), -float(world.y)])
        for polygon in mesh.polygons:
            indices = [offset + int(v) for v in polygon.vertices]
            if obj.matrix_world.determinant() < 0:
                indices = [indices[0], *reversed(indices[1:])]
            polygons.append(indices)
        selection = request.get("uv_channel")
        if isinstance(selection, str):
            layer = mesh.uv_layers.get(selection)
        elif type(selection) is int:
            layer = mesh.uv_layers[selection] if 0 <= selection < len(mesh.uv_layers) else None
        else:
            layer = mesh.uv_layers.active
        if selection is not None and layer is None:
            raise ValueError("UV_CHANNEL_NOT_FOUND:" + str(selection))
        if layer:
            mesh.uv_layers.active_index = next(i for i, candidate in enumerate(mesh.uv_layers)
                                                if candidate.name == layer.name)
            layer.active_render = True
        remaining = [candidate.name for candidate in mesh.uv_layers if not layer or candidate.name != layer.name]
        if extra_names is not None and remaining != extra_names:
            raise ValueError("UNSUPPORTED_MIXED_EXTRA_UV_LAYOUT")
        extra_names = remaining
        for name in remaining:
            extra_uv.setdefault(name, [])
        loop_ids = list(range(len(mesh.loops)))
        identity: dict[str, object] = {"exact": False, "policy": "conservative imported polygon-loop identity",
                                      "reason": "NO_FBX_UV_LAYER"}
        if layer:
            loop_ids, identity = restore_loop_uv_ids(raw_fbx.get(obj.name), layer.name,
                [[int(v) for v in polygon.vertices] for polygon in mesh.polygons],
                [int(loop.vertex_index) for loop in mesh.loops],
                [[float(v) for v in item.uv] for item in layer.data])
        if raw_error:
            identity["raw_parser_error"] = raw_error
        has_uv &= layer is not None
        uv_presence.append(layer is not None)
        for triangle in mesh.loop_triangles:
            loops = list(triangle.loops)
            if obj.matrix_world.determinant() < 0:
                loops = [loops[0], loops[2], loops[1]]
            face = [offset + mesh.loops[i].vertex_index for i in loops]
            values = [[float(x) for x in layer.data[i].uv] if layer else [0., 0.] for i in loops]
            ids = [loop_offset + loop_ids[i] for i in loops]
            faces.append(face)
            triangle_to_polygon.append(polygon_offset + triangle.polygon_index)
            uv.append(values)
            identities.append(ids)
            if request.get("collect_normal_evidence", False):
                normal_matrix = obj.matrix_world.to_3x3().inverted().transposed()
                values_n = []
                for loop in loops:
                    normal = (normal_matrix @ mesh.corner_normals[loop].vector).normalized()
                    values_n.append([float(normal.x), float(normal.z), -float(normal.y)])
                normal_corners.append(values_n)
            for name in remaining:
                extra_uv[name].append([[float(x) for x in mesh.uv_layers[name].data[i].uv] for i in loops])
            ancestry.append({"object": obj.name, "polygon": triangle.polygon_index,
                             "loops": loops, "material": mesh.polygons[triangle.polygon_index].material_index})
        objects.append({"node": obj.name, "face_start": start, "face_count": len(faces) - start,
                        "uv_layers": [layer.name for layer in mesh.uv_layers],
                        "active_uv_index": mesh.uv_layers.active_index if layer else None,
                        "active_uv_name": layer.name if layer else None, "uv_identity": identity})
        loop_offset += len(mesh.loops)
    if any(uv_presence) and not all(uv_presence):
        raise ValueError("UNSUPPORTED_MIXED_UV_PRESENCE")
    if not faces:
        raise ValueError("NO_MESH_IN_FBX")
    np.savez_compressed(folder / "imported.npz", vertices=np.asarray(vertices), faces=np.asarray(faces),
                        corner_uv=np.asarray(uv), corner_ids=np.asarray(identities),
                        **cast(Any, {f"extra_uv_{index}": np.asarray(extra_uv[name])
                                     for index, name in enumerate(extra_names or [])}))
    normal_record: dict[str, object] = {}
    if request.get("collect_normal_evidence", False):
        normal_record["hard_surface_source"] = {"corner_normals": normal_corners,
            "objects": shading_objects, "sharp_vertex_edges": sharp_edges,
            "normal_space": "inverse-transpose world transform then glTF Y-up",
            "scope": "read-only diagnostic; exported BaseColor shading is unchanged"}
    (folder / "identity.json").write_text(json.dumps({"objects": objects, "ancestry": ancestry,
        **normal_record,
        "polygon_topology": {"polygons": polygons, "triangle_to_polygon": triangle_to_polygon,
                             "source": "Blender original polygons and loop_triangles; snapshot vertex indices"},
        "source_uv_present": has_uv, "images": image_sources, "extra_uv_names": extra_names or [],
        "animation_policy": "FBX animation not imported: explicit undeformed static source" if explicit_binding
                            else "dynamic scene rejected",
        "coordinates": "glTF Y-up", "triangulation": "Blender loop_triangles"},
        ensure_ascii=False, indent=2), encoding="utf-8")
    if not explicit_binding:
        bpy.ops.export_scene.gltf(filepath=str(folder / "reference.glb"), export_format="GLB",
                                  export_yup=True, export_animations=False)


if __name__ == "__main__":
    main()
