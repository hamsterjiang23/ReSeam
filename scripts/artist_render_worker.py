"""Blender-only renderer of the actual delivered GLB, including material channels."""

import json
import math
import sys
from pathlib import Path
from typing import Any

import bpy  # type: ignore[import-not-found]
import numpy as np
from mathutils import Vector  # type: ignore[import-not-found]


def canonical(triangles: np.ndarray) -> np.ndarray:
    result = []
    for triangle in triangles:
        rounded = np.round(triangle, 4)
        result.append(rounded[np.lexsort(rounded.T[::-1])].ravel())
    rows = np.asarray(result)
    return rows[np.lexsort(rows.T[::-1])]


def channel_material(source: Any, channel: str) -> Any:
    if channel == "material":
        return source
    material = source.copy()
    nodes = material.node_tree.nodes
    principled = next((n for n in nodes if n.type == "BSDF_PRINCIPLED"), None)
    if principled is None:
        raise ValueError("CHANNEL_REQUIRES_PRINCIPLED_MATERIAL")
    socket = principled.inputs[{"basecolor": "Base Color", "roughness": "Roughness", "metallic": "Metallic",
                               "normal": "Normal"}[channel]]
    if channel == "normal":
        if not socket.is_linked or socket.links[0].from_node.type != "NORMAL_MAP":
            raise ValueError("NORMAL_TEXTURE_NOT_PRESENT")
        socket = socket.links[0].from_node.inputs["Color"]
        if not socket.is_linked:
            raise ValueError("NORMAL_TEXTURE_NOT_PRESENT")
    emission = nodes.new("ShaderNodeEmission")
    if socket.is_linked:
        material.node_tree.links.new(socket.links[0].from_socket, emission.inputs["Color"])
    else:
        value = socket.default_value
        emission.inputs["Color"].default_value = ((value, value, value, 1.) if isinstance(value, (float, int))
                                                    else value)
    output = next(n for n in nodes if n.type == "OUTPUT_MATERIAL" and n.is_active_output)
    material.node_tree.links.new(emission.outputs[0], output.inputs["Surface"])
    return material


def main() -> None:
    config = json.loads(Path(sys.argv[sys.argv.index("--") + 1]).read_text(encoding="utf-8"))
    output = Path(config["output"])
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)
    bpy.ops.import_scene.gltf(filepath=config["model"])
    objects = [o for o in bpy.context.scene.objects if o.type == "MESH"]
    positions, texcoords = [], []
    for obj in objects:
        obj.data.calc_loop_triangles()
        uv = obj.data.uv_layers.active
        if uv is None:
            raise ValueError("EXPORTED_GLB_HAS_NO_UV")
        for tri in obj.data.loop_triangles:
            positions.append([tuple(obj.matrix_world @ obj.data.vertices[i].co) for i in tri.vertices])
            texcoords.append([tuple(uv.data[i].uv) for i in tri.loops])
    with np.load(config["expected_geometry"], allow_pickle=False) as data:
        original = data["vertices"][data["faces"]]
        expected_positions = original[..., [0, 2, 1]] * [1, -1, 1]
        expected = np.concatenate((expected_positions, data["corner_uv"]), axis=2)
    actual = np.concatenate((np.asarray(positions), np.asarray(texcoords)), axis=2)
    if actual.shape != expected.shape or not np.allclose(canonical(actual), canonical(expected), atol=1e-4, rtol=0):
        raise ValueError("EXPORTED_MODEL_GEOMETRY_OR_UV_DIFFERS_FROM_SNAPSHOT")
    points = np.asarray(positions).reshape(-1, 3)
    center = Vector(tuple((points.min(axis=0) + points.max(axis=0))*.5))
    radius = float(np.linalg.norm(points.max(axis=0) - points.min(axis=0))*.5)
    scene = bpy.context.scene
    scene.render.engine = "CYCLES"
    scene.cycles.device = "CPU"
    scene.cycles.samples = 16
    scene.cycles.use_denoising = True
    scene.render.resolution_x = scene.render.resolution_y = config["size"]
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = "PNG"
    scene.view_settings.view_transform = "Standard"
    scene.render.film_transparent = False
    scene.world.use_nodes = True
    scene.world.node_tree.nodes["Background"].inputs[0].default_value = (.65, .7, .8, 1)
    scene.world.node_tree.nodes["Background"].inputs[1].default_value = .3
    camera_data = bpy.data.cameras.new("Artist camera")
    camera = bpy.data.objects.new("Artist camera", camera_data)
    scene.collection.objects.link(camera)
    scene.camera = camera
    camera_data.type = "ORTHO"
    camera_data.clip_end = max(1000., radius*100)
    light_data = bpy.data.lights.new("Artist key", "AREA")
    light = bpy.data.objects.new("Artist key", light_data)
    scene.collection.objects.link(light)
    original_materials = {o.name: list(o.data.materials) for o in objects}
    materials = [{"object": o.name, "materials": [{"name": m.name,
                  "image_textures": [n.image.name for n in m.node_tree.nodes if n.type == "TEX_IMAGE" and n.image]}
                  for m in o.data.materials if m is not None and m.use_nodes]} for o in objects]
    images: list[dict[str, object]] = []
    unavailable: list[dict[str, str]] = []
    for channel in config.get("channels", ["material"]):
        try:
            replacements = {name: [channel_material(m, channel) for m in mats]
                            for name, mats in original_materials.items()}
        except ValueError as error:
            unavailable.append({"channel": channel, "reason": str(error)})
            continue
        for obj in objects:
            obj.data.materials.clear()
            for material in replacements[obj.name]:
                obj.data.materials.append(material)
        for name, azimuth, elevation in config["cameras"]:
            a, e = math.radians(azimuth), math.radians(elevation)
            direction = Vector((math.sin(a)*math.cos(e), -math.cos(a)*math.cos(e), math.sin(e)))
            camera.location = center + direction * radius*4
            camera.rotation_euler = (center - camera.location).to_track_quat("-Z", "Y").to_euler()
            for lighting in config["lights"]:
                offset = {"studio": (2, -3, 4), "side": (-4, -1, 1), "soft": (0, -2, 5)}[lighting]
                light.location = center + Vector(offset)*radius
                light.rotation_euler = (center-light.location).to_track_quat("-Z", "Y").to_euler()
                light_data.energy = radius*radius*150
                light_data.shape = "DISK"
                light_data.size = radius*(4 if lighting == "soft" else 2)
                for distance in config["distances"]:
                    camera_data.ortho_scale = radius*2.2*distance
                    path = output / f"{channel}-{name}-{lighting}-{distance:g}.png"
                    scene.render.filepath = str(path)
                    bpy.ops.render.render(write_still=True)
                    images.append({"path": str(path), "channel": channel, "camera": name,
                                   "light": lighting, "distance": distance})
    (output / "rendered.json").write_text(json.dumps({"images": images, "unavailable": unavailable,
        "materials": materials, "blender_version": bpy.app.version_string,
        "geometry_and_uv_reimport_match": True, "comparison_tolerance": 1e-4,
        "distance_mode": "orthographic relative screen size; not explicit mip-level validation"}, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
