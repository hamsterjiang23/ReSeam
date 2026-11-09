"""Isolated Blender 4.5 CPU three-channel baker; invoked only by its adapter.

Input is one non-overlapping target sheet. The caller separates shared members.
The exported target uses flat, explicit glTF tangent frames. Imported source
materials and source shading normals are preserved for the normal bake.
"""

import json
import struct
import sys
from pathlib import Path
from typing import Any

import bpy  # pyright: ignore[reportMissingImports] -- executed by Blender's Python
import numpy as np

# Blender does not automatically add the script folder to sys.path.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from blender_worker import import_high, make_low, scene_settings


def _source_materials(high: list[Any], declarations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    materials = {material.name: material for obj in high for material in obj.data.materials if material}
    result: list[dict[str, Any]] = []
    for declaration in declarations:
        name = declaration["name"]
        material = materials.get(name)
        if material is None:
            # Blender's importer names unnamed glTF materials Material_<index>.
            raise ValueError("SOURCE_MATERIAL_NOT_FOUND:" + name)
        material.use_nodes = True
        nodes = material.node_tree.nodes
        shader = next((node for node in nodes if node.type == "BSDF_PRINCIPLED"), None)
        output = next(
            (node for node in nodes if node.type == "OUTPUT_MATERIAL" and node.is_active_output), None
        )
        if shader is None or output is None or not output.inputs["Surface"].is_linked:
            raise ValueError("UNSUPPORTED_IMPORTED_SHADER:" + name)
        # The reference contract uses the glTF top-V derivative basis. Blender
        # imports UVs into bottom-V and regenerates its normal-map frame, so
        # convert the encoded Y component before evaluating source normals.
        for normal_map in tuple(node for node in nodes if node.type == "NORMAL_MAP"):
            color = normal_map.inputs["Color"]
            separate = nodes.new("ShaderNodeSeparateXYZ")
            _link_or_value(material.node_tree, color, separate.inputs[0])
            combine = nodes.new("ShaderNodeCombineXYZ")
            flip = nodes.new("ShaderNodeMath")
            flip.operation = "SUBTRACT"
            flip.inputs[0].default_value = 1
            links = material.node_tree.links
            links.new(separate.outputs["X"], combine.inputs["X"])
            links.new(separate.outputs["Z"], combine.inputs["Z"])
            links.new(separate.outputs["Y"], flip.inputs[1])
            links.new(flip.outputs[0], combine.inputs["Y"])
            links.new(combine.outputs[0], color)
        occlusion = next(
            (
                node.inputs["Occlusion"]
                for node in nodes
                if node.type == "GROUP" and node.inputs.get("Occlusion") is not None
            ),
            None,
        )
        if declaration["occlusion_texture"] and (occlusion is None or not occlusion.is_linked):
            raise ValueError("SOURCE_AO_SOCKET_MISSING:" + name)
        result.append(
            {
                "material": material,
                "shader": shader,
                "output": output,
                "surface": output.inputs["Surface"].links[0].from_socket,
                "occlusion": occlusion,
                "declaration": declaration,
            }
        )
    if set(materials) != {record["declaration"]["name"] for record in result}:
        raise ValueError("SOURCE_MATERIAL_SET_MISMATCH")
    return result


def _link_or_value(tree: Any, source: Any, destination: Any) -> None:
    if source.is_linked:
        tree.links.new(source.links[0].from_socket, destination)
    else:
        destination.default_value = source.default_value


def _route_emission(records: list[dict[str, Any]], channel: str) -> None:
    for record in records:
        tree = record["material"].node_tree
        emission = tree.nodes.new("ShaderNodeEmission")
        if channel == "BaseColor":
            _link_or_value(tree, record["shader"].inputs["Base Color"], emission.inputs["Color"])
        else:
            combine = tree.nodes.new("ShaderNodeCombineXYZ")
            occlusion = record["occlusion"]
            if occlusion is None:
                # No source AO texture: glTF's defined occlusion value is one.
                combine.inputs["X"].default_value = 1
            else:
                _link_or_value(tree, occlusion, combine.inputs["X"])
            _link_or_value(tree, record["shader"].inputs["Roughness"], combine.inputs["Y"])
            _link_or_value(tree, record["shader"].inputs["Metallic"], combine.inputs["Z"])
            tree.links.new(combine.outputs[0], emission.inputs["Color"])
        tree.links.new(emission.outputs[0], record["output"].inputs["Surface"])


def _restore_source(records: list[dict[str, Any]]) -> None:
    for record in records:
        record["material"].node_tree.links.new(record["surface"], record["output"].inputs["Surface"])


def _target_frames(request: dict[str, Any]) -> tuple[Any, Any]:
    positions = np.asarray(request["vertices"], dtype=float)[np.asarray(request["faces"])]
    uv = np.asarray(request["corner_uv"], dtype=float).copy()
    uv[..., 1] = 1 - uv[..., 1]
    a, b = positions[:, 1] - positions[:, 0], positions[:, 2] - positions[:, 0]
    normal = np.cross(a, b)
    normal /= np.linalg.norm(normal, axis=1, keepdims=True)
    du, dv = uv[:, 1] - uv[:, 0], uv[:, 2] - uv[:, 0]
    determinant = du[:, 0] * dv[:, 1] - du[:, 1] * dv[:, 0]
    tangent = (a * dv[:, 1, None] - b * du[:, 1, None]) / determinant[:, None]
    bitangent = (-a * dv[:, 0, None] + b * du[:, 0, None]) / determinant[:, None]
    tangent -= normal * np.sum(normal * tangent, axis=1, keepdims=True)
    tangent /= np.linalg.norm(tangent, axis=1, keepdims=True)
    sign = np.sign(np.sum(np.cross(normal, tangent) * bitangent, axis=1))
    normals = np.repeat(normal[:, None, :], 3, axis=1)
    tangents = np.repeat(np.column_stack((tangent, sign))[:, None, :], 3, axis=1)
    return normals, tangents


def _write_glb(request: dict[str, Any], channels: dict[str, str], normals: Any, tangents: Any) -> None:
    """Write explicit corner attributes; no implicit exporter normal convention."""
    blob = bytearray()
    views: list[dict[str, Any]] = []
    accessors: list[dict[str, Any]] = []

    def buffer(data: bytes) -> int:
        while len(blob) % 4:
            blob.append(0)
        index = len(views)
        views.append({"buffer": 0, "byteOffset": len(blob), "byteLength": len(data)})
        blob.extend(data)
        return index

    def attribute(array: Any, kind: str) -> int:
        value = np.asarray(array, dtype="<f4").reshape(-1, int(kind[-1]))
        index = len(accessors)
        accessors.append(
            {
                "bufferView": buffer(value.tobytes()),
                "componentType": 5126,
                "count": len(value),
                "type": kind,
                "min": value.min(axis=0).tolist(),
                "max": value.max(axis=0).tolist(),
            }
        )
        return index

    faces = np.asarray(request["faces"], dtype=np.int64)
    positions = np.asarray(request["vertices"], dtype=float)[faces]
    uv = np.asarray(request["corner_uv"], dtype=float).copy()
    uv[..., 1] = 1 - uv[..., 1]
    attributes = {
        "POSITION": attribute(positions, "VEC3"),
        "NORMAL": attribute(normals, "VEC3"),
        "TANGENT": attribute(tangents, "VEC4"),
        "TEXCOORD_0": attribute(uv, "VEC2"),
    }
    images = [
        {"bufferView": buffer(Path(channels[name]).read_bytes()), "mimeType": "image/png", "name": name}
        for name in ("BaseColor", "Normal", "ORM")
    ]
    document = {
        "asset": {"version": "2.0", "generator": "ReSeam isolated Blender baker"},
        "scene": 0,
        "scenes": [{"nodes": [0]}],
        "nodes": [{"mesh": 0}],
        "meshes": [{"primitives": [{"attributes": attributes, "material": 0, "mode": 4}]}],
        "materials": [
            {
                "name": "ReSeam_baked",
                "alphaMode": "OPAQUE",
                "pbrMetallicRoughness": {
                    "baseColorTexture": {"index": 0},
                    "metallicRoughnessTexture": {"index": 2},
                    "metallicFactor": 1,
                    "roughnessFactor": 1,
                },
                "normalTexture": {"index": 1},
                "occlusionTexture": {"index": 2},
            }
        ],
        "textures": [{"source": index, "sampler": 0} for index in range(3)],
        "samplers": [{"wrapS": 33071, "wrapT": 33071, "magFilter": 9729, "minFilter": 9729}],
        "images": images,
        "accessors": accessors,
        "bufferViews": views,
        "buffers": [{"byteLength": len(blob)}],
    }
    encoded = json.dumps(document, separators=(",", ":")).encode("utf-8")
    encoded += b" " * (-len(encoded) % 4)
    blob.extend(b"\0" * (-len(blob) % 4))
    data = struct.pack("<4sII", b"glTF", 2, 12 + 8 + len(encoded) + 8 + len(blob))
    data += struct.pack("<I4s", len(encoded), b"JSON") + encoded
    data += struct.pack("<I4s", len(blob), b"BIN\0") + blob
    (Path(request["folder"]) / "asset.glb").write_bytes(data)


def _preview_material(low: Any, channels: dict[str, str]) -> None:
    material = low.data.materials[0]
    tree = material.node_tree
    shader = next(node for node in tree.nodes if node.type == "BSDF_PRINCIPLED")
    for name, path in channels.items():
        texture = tree.nodes.new("ShaderNodeTexImage")
        texture.image = bpy.data.images.load(path, check_existing=True)
        texture.image.colorspace_settings.name = "sRGB" if name == "BaseColor" else "Non-Color"
        if name == "BaseColor":
            tree.links.new(texture.outputs["Color"], shader.inputs["Base Color"])
        elif name == "ORM":
            separate = tree.nodes.new("ShaderNodeSeparateXYZ")
            tree.links.new(texture.outputs["Color"], separate.inputs[0])
            tree.links.new(separate.outputs["Y"], shader.inputs["Roughness"])
            tree.links.new(separate.outputs["Z"], shader.inputs["Metallic"])
        else:
            # Restore bottom-V Blender basis for the preview only. Saved PNG
            # and explicit GLB use top-V glTF tangent coordinates.
            separate = tree.nodes.new("ShaderNodeSeparateXYZ")
            combine = tree.nodes.new("ShaderNodeCombineXYZ")
            flip = tree.nodes.new("ShaderNodeMath")
            flip.operation = "SUBTRACT"
            flip.inputs[0].default_value = 1
            tree.links.new(texture.outputs["Color"], separate.inputs[0])
            tree.links.new(separate.outputs["X"], combine.inputs["X"])
            tree.links.new(separate.outputs["Z"], combine.inputs["Z"])
            tree.links.new(separate.outputs["Y"], flip.inputs[1])
            tree.links.new(flip.outputs[0], combine.inputs["Y"])
            normal = tree.nodes.new("ShaderNodeNormalMap")
            tree.links.new(combine.outputs[0], normal.inputs["Color"])
            tree.links.new(normal.outputs[0], shader.inputs["Normal"])


def main(request_path: Path) -> None:
    request = json.loads(request_path.read_text(encoding="utf-8"))
    bpy.ops.wm.read_factory_settings(use_empty=True)
    high = import_high(request["high"])
    records = _source_materials(high, request["source"]["materials"])
    low = make_low(request)
    for polygon in low.data.polygons:
        polygon.use_smooth = False
    low.data.update()
    material = bpy.data.materials.new("ReSeam_bake_target")
    material.use_nodes = True
    low.data.materials.append(material)
    resolution = int(request["resolution"])
    scene_settings(resolution)
    scene = bpy.context.scene
    scene.cycles.device = "CPU"
    scene.render.threads_mode = "FIXED"
    scene.render.threads = 1
    scene.render.bake.use_selected_to_active = True
    scene.render.bake.use_cage = True
    scene.render.bake.cage_extrusion = float(request["cage_extrusion"])
    scene.render.bake.margin = int(request["margin_pixels"])
    scene.render.bake.margin_type = "EXTEND"
    scene.render.bake.normal_space = "TANGENT"
    scene.render.bake.use_clear = True
    bpy.ops.object.select_all(action="DESELECT")
    for obj in high + [low]:
        obj.select_set(True)
    bpy.context.view_layer.objects.active = low
    channels: dict[str, str] = {}
    for label in ("BaseColor", "ORM", "Normal"):
        if label == "Normal":
            _restore_source(records)
        else:
            _route_emission(records, label)
        image = bpy.data.images.new(label, width=resolution, height=resolution, alpha=True)
        image.generated_color = (0, 0, 0, 0)
        image.colorspace_settings.name = "sRGB" if label == "BaseColor" else "Non-Color"
        target = material.node_tree.nodes.new("ShaderNodeTexImage")
        target.image = image
        material.node_tree.nodes.active = target
        bpy.ops.object.bake(type="NORMAL" if label == "Normal" else "EMIT")
        if label == "Normal":
            pixels = np.asarray(image.pixels[:], dtype=np.float32).reshape(-1, 4)
            pixels[:, 1] = 1 - pixels[:, 1]
            image.pixels.foreach_set(pixels.ravel())
            image.update()
        output = Path(request["folder"]) / (label + ".png")
        image.filepath_raw = str(output)
        image.file_format = "PNG"
        image.save()
        channels[label] = str(output)
    _restore_source(records)
    normals, tangents = _target_frames(request)
    frames = {key: request[key] for key in ("vertices", "faces", "corner_uv")}
    frames.update(
        corner_normals_gltf=normals.tolist(),
        corner_tangents_gltf=tangents.tolist(),
        target_shading="flat explicit glTF top-V frames; source shading unchanged",
    )
    (Path(request["folder"]) / "mesh_frames.json").write_text(json.dumps(frames), encoding="utf-8")
    _write_glb(request, channels, normals, tangents)
    _preview_material(low, channels)
    for obj in high:
        obj.hide_render = True
        obj.hide_set(True)
    bpy.ops.wm.save_as_mainfile(filepath=str(Path(request["folder"]) / "preview.blend"))
    result = {
        "status": "BAKED_THREE_CHANNELS",
        "channels": channels,
        "source_materials": [
            {
                **record["declaration"],
                "occlusion_socket_linked": bool(record["occlusion"] and record["occlusion"].is_linked),
            }
            for record in records
        ],
        "backend": {"Blender": bpy.app.version_string, "device": "CPU", "threads": 1},
        "normal_contract": "source Cycles shading; target flat; baked green inverted into explicit glTF top-V TBN",
        "projection": {
            "selected_to_active": True,
            "cage_extrusion": request["cage_extrusion"],
            "exact_coverage_verified": False,
        },
        "claims": {"material_channels_baked": True, "quality_validated": False, "gpu_verified": False},
    }
    Path(request["output"]).write_text(json.dumps(result, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main(Path(sys.argv[sys.argv.index("--") + 1]))
