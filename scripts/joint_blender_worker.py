"""V2 grouped packing and conflict-free per-member baking on the complete low mesh."""

import json
import sys
from pathlib import Path
from typing import Any

import bpy

sys.path.insert(0, str(Path(__file__).parent))
import blender_worker as common


def source_vector(vector: Any) -> list[float]:
    return [float(vector.x), float(vector.z), float(-vector.y)]


def bake_members(request: dict[str, Any], low: Any) -> dict[str, Any]:
    high = common.import_high(request["high"])
    surfaces = []
    for obj in high:
        for material in obj.data.materials:
            if material is not None and material.use_nodes:
                out = next((n for n in material.node_tree.nodes if n.type == "OUTPUT_MATERIAL"), None)
                if out is not None and out.inputs["Surface"].is_linked:
                    surfaces.append((material, out, out.inputs["Surface"].links[0].from_socket))
    common.emission_materials(high)
    slots = request["face_slots"]
    if len(slots) != len(low.data.polygons) or min(slots) < 0:
        raise ValueError("Invalid face slots")
    materials = []
    for index in range(max(slots) + 1):
        mat = bpy.data.materials.new(f"member_{index}")
        mat.use_nodes = True
        low.data.materials.append(mat)
        materials.append(mat)
    for polygon, index in zip(low.data.polygons, slots, strict=True):
        polygon.material_index = index
    common.scene_settings(request.get("resolution", 1024))
    scene = bpy.context.scene
    scene.render.bake.use_selected_to_active = True
    scene.render.bake.use_cage = True
    scene.render.bake.cage_extrusion = request["cage_extrusion"]
    scene.render.bake.margin = 8
    scene.render.bake.margin_type = "EXTEND"
    scene.render.bake.normal_space = "TANGENT"
    bpy.ops.object.select_all(action="DESELECT")
    for obj in high + [low]:
        obj.select_set(True)
    bpy.context.view_layer.objects.active = low
    outputs = []
    for label, kind in (("BaseColor", "EMIT"), ("Normal", "NORMAL")):
        if kind == "NORMAL":
            for mat, out, socket in surfaces:
                mat.node_tree.links.new(socket, out.inputs["Surface"])
        images = []
        for index, mat in enumerate(materials):
            img = bpy.data.images.new(
                f"{label}_{index}",
                width=request.get("resolution", 1024),
                height=request.get("resolution", 1024),
                alpha=True,
            )
            img.generated_color = (0, 0, 0, 0)
            img.colorspace_settings.name = "sRGB" if label == "BaseColor" else "Non-Color"
            node = mat.node_tree.nodes.new("ShaderNodeTexImage")
            node.image = img
            mat.node_tree.nodes.active = node
            images.append(img)
        bpy.ops.object.bake(type=kind)
        for index, img in enumerate(images):
            filename = f"{label}_{index:03d}.png"
            img.filepath_raw = str((Path(request["folder"]) / filename).resolve())
            img.file_format = "PNG"
            img.save()
            if not Path(img.filepath_raw).is_file():
                raise RuntimeError("Baked image was not written: " + img.filepath_raw)
            outputs.append({"channel": label, "slot": index, "file": filename})
    low.data.calc_tangents(uvmap="UVMap")
    frames = []
    for poly in low.data.polygons:
        frames.append(
            [
                {
                    "normal": source_vector(low.data.loops[i].normal),
                    "tangent": source_vector(low.data.loops[i].tangent),
                    "sign": float(low.data.loops[i].bitangent_sign),
                }
                for i in poly.loop_indices
            ]
        )
    return {
        "images": outputs,
        "corner_frames": frames,
        "version": bpy.app.version_string,
        "geometry_faces": len(low.data.polygons),
        "mode": "per_material_images_full_geometry",
    }


def main() -> None:
    request = json.loads(Path(sys.argv[sys.argv.index("--") + 1]).read_text(encoding="utf-8-sig"))
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)
    low = common.make_low(request)
    if request["mode"] == "pack_groups":
        bpy.ops.object.mode_set(mode="EDIT")
        bpy.ops.mesh.select_all(action="SELECT")
        bpy.ops.uv.select_all(action="SELECT")
        bpy.ops.uv.pack_islands(
            rotate=True, margin_method="FRACTION", margin=request["margin"], merge_overlap=True
        )
        bpy.ops.object.mode_set(mode="OBJECT")
        layer = low.data.uv_layers.active
        result = {
            "corner_uv": [[list(layer.data[i].uv) for i in p.loop_indices] for p in low.data.polygons],
            "version": bpy.app.version_string,
            "merge_overlap": True,
        }
    elif request["mode"] == "bake_members":
        result = bake_members(request, low)
    elif request["mode"] == "export_shared":
        common.low_material(low, request["basecolor"], request["normal"])
        for polygon in low.data.polygons:
            polygon.material_index = 0
        bpy.ops.object.select_all(action="DESELECT")
        low.select_set(True)
        bpy.context.view_layer.objects.active = low
        bpy.ops.export_scene.gltf(
            filepath=request["asset"], export_format="GLB", use_selection=True, export_tangents=True
        )
        result = {"asset": request["asset"], "version": bpy.app.version_string}
    else:
        raise ValueError("Unknown joint worker mode")
    Path(request["output"]).write_text(json.dumps(result, allow_nan=False), encoding="utf-8")


if __name__ == "__main__":
    main()
