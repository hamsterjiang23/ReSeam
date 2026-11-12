"""Pack independently verified actual GLBs into origin-aligned toggle collections."""

import json
import sys
from pathlib import Path
from typing import Any

import bpy  # pyright: ignore[reportMissingImports]
import numpy as np
from mathutils import Vector  # pyright: ignore[reportMissingImports]

sys.path.insert(0, str(Path(__file__).resolve().parent))
from triangle_identity_match import triangle_bijection


def check_model(objects: list[Any], binding: str) -> np.ndarray:
    positions, uvs = [], []
    for obj in objects:
        if obj.type != "MESH":
            continue
        mesh = obj.data
        mesh.calc_loop_triangles()
        uv = mesh.uv_layers.active
        if uv is None:
            raise ValueError("COMPARISON_SCENE_UV_MISSING")
        for tri in mesh.loop_triangles:
            positions.append([tuple(obj.matrix_world @ mesh.vertices[v].co) for v in tri.vertices])
            uvs.append([tuple(uv.data[loop].uv) for loop in tri.loops])
    with np.load(binding, allow_pickle=False) as source:
        xyz = source["vertices"][source["faces"]][..., [0, 2, 1]] * [1, -1, 1]
        expected = np.concatenate((xyz, source["corner_uv"]), axis=2)
    actual = np.concatenate((np.asarray(positions), np.asarray(uvs)), axis=2)
    if not triangle_bijection(actual, expected, 1e-4):
        raise ValueError("COMPARISON_SCENE_GEOMETRY_UV_MISMATCH")
    return np.asarray(positions).reshape(-1, 3)


def main() -> None:
    config = json.loads(Path(sys.argv[sys.argv.index("--") + 1]).read_text(encoding="utf-8"))
    output = Path(config["output"])
    bpy.ops.wm.read_factory_settings(use_empty=True)
    records: list[dict[str, object]] = []
    framing = None
    for label, record in config["models"].items():
        old = set(bpy.data.objects)
        bpy.ops.import_scene.gltf(filepath=record["model"])
        objects = sorted(set(bpy.data.objects) - old, key=lambda obj: obj.name)
        collection = bpy.data.collections.new(label)
        bpy.context.scene.collection.children.link(collection)
        for obj in objects:
            for previous in list(obj.users_collection):
                previous.objects.unlink(obj)
            collection.objects.link(obj)
        points = check_model(objects, record["expected_geometry"])
        if label == config["default_visible"]:
            framing = points
        collection.hide_render = label != config["default_visible"]
        collection.hide_viewport = label != config["default_visible"]
        records.append({"label": label, "model": record["model"],
            "objects": [{"name": obj.name, "matrix_world": [list(row) for row in obj.matrix_world]}
                        for obj in objects], "geometry_uv_checked": True})
    if framing is None:
        raise ValueError("COMPARISON_DEFAULT_MODEL_MISSING")
    scene = bpy.context.scene
    center = Vector(tuple((framing.min(axis=0) + framing.max(axis=0)) * .5))
    radius = float(np.linalg.norm(framing.max(axis=0) - framing.min(axis=0)) * .5)
    camera = bpy.data.objects.new("Comparison camera", bpy.data.cameras.new("Comparison camera"))
    scene.collection.objects.link(camera)
    scene.camera = camera
    camera.data.type = "ORTHO"
    camera.data.ortho_scale = radius * 2.2
    camera.location = center + Vector((0, -1, 0)) * radius * 4
    camera.rotation_euler = (center - camera.location).to_track_quat("-Z", "Y").to_euler()
    light = bpy.data.objects.new("Soft comparison light", bpy.data.lights.new("Soft comparison light", "AREA"))
    scene.collection.objects.link(light)
    light.location = center + Vector((0, -2, 5)) * radius
    light.rotation_euler = (center - light.location).to_track_quat("-Z", "Y").to_euler()
    light.data.energy = radius * radius * 150
    light.data.shape = "DISK"
    light.data.size = radius * 4
    scene.world = bpy.data.worlds.new("Comparison world")
    scene.world.use_nodes = True
    scene.world.node_tree.nodes["Background"].inputs[0].default_value = (.65, .7, .8, 1)
    scene.world.node_tree.nodes["Background"].inputs[1].default_value = .3
    scene.render.engine = "CYCLES"
    scene.cycles.device = "CPU"
    scene.cycles.samples = 16
    scene.cycles.use_denoising = True
    scene.render.resolution_x = scene.render.resolution_y = config.get("size", 512)
    scene.render.resolution_percentage = 100
    scene.view_settings.view_transform = "Standard"
    text = bpy.data.texts.new("README")
    text.write("All models retain their original world transforms. Toggle COLLECTION viewport and render visibility; show exactly one model collection. Shared is initially visible. This file does not imply artist acceptance.\n")
    bpy.ops.file.pack_all()
    path = output / "comparison.blend"
    bpy.ops.wm.save_as_mainfile(filepath=str(path))
    bpy.ops.wm.open_mainfile(filepath=str(path))
    for record in records:
        collection = bpy.data.collections[str(record["label"])]
        for item in record["objects"]:
            if not np.array_equal(np.asarray(collection.objects[item["name"]].matrix_world), item["matrix_world"]):
                raise ValueError("COMPARISON_SCENE_REOPEN_TRANSFORM_CHANGED")
        record["reopened_geometry_uv_checked"] = bool(check_model(list(collection.objects),
            config["models"][str(record["label"])] ["expected_geometry"]).size)
    images = [image for image in bpy.data.images if image.source == "FILE"]
    packed = all(image.packed_file is not None for image in images)
    if not packed:
        raise ValueError("COMPARISON_IMAGES_NOT_PACKED")
    scene = bpy.context.scene
    scene.render.filepath = str(output / "comparison-default.png")
    bpy.ops.render.render(write_still=True)
    (output / "scene_audit.json").write_text(json.dumps({"scene": str(path),
        "models": records, "image_count": len(images), "all_images_packed": packed,
        "default_visible": config["default_visible"], "reopened": True,
        "default_render": str(output / "comparison-default.png"),
        "transforms_modified": False, "visual_acceptance": "NOT_RUN"}, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
