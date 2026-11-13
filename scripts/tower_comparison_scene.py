"""Build a packed Blender comparison from the actual delivered tower GLBs."""

import json
import math
import sys
from pathlib import Path

import bpy  # pyright: ignore[reportMissingImports]
from mathutils import Vector  # pyright: ignore[reportMissingImports]


def main() -> None:
    request = json.loads(Path(sys.argv[sys.argv.index("--") + 1]).read_text(encoding="utf-8"))
    bpy.ops.wm.read_factory_settings(use_empty=True)
    receipts = []
    for index, record in enumerate(request["models"]):
        before = set(bpy.data.objects)
        bpy.ops.import_scene.gltf(filepath=record["path"])
        added = set(bpy.data.objects) - before
        meshes = [obj for obj in added if obj.type == "MESH"]
        for obj in meshes:
            obj.data.calc_loop_triangles()
        triangles = sum(len(obj.data.loop_triangles) for obj in meshes)
        if triangles != record["faces"]:
            raise ValueError("COMPARISON_TRIANGLE_COUNT_CHANGED:" + record["label"])
        collection = bpy.data.collections.new(record["label"])
        bpy.context.scene.collection.children.link(collection)
        offset = Vector(((index % 4) * 1.45, 0., -(index // 4) * 1.45))
        for obj in added:
            for parent in list(obj.users_collection):
                parent.objects.unlink(obj)
            collection.objects.link(obj)
            if obj.parent is None:
                obj.location += offset
        text = bpy.data.curves.new(record["label"], "FONT")
        text.body = record["label"]
        text.size = .055
        text.font = bpy.data.fonts.load("C:/Windows/Fonts/msyh.ttc", check_existing=True)
        label = bpy.data.objects.new(record["label"] + " label", text)
        collection.objects.link(label)
        label.location = offset + Vector((-.45, -.12, -.1))
        label.rotation_euler = (math.pi / 2, 0, 0)
        receipts.append({"label": record["label"], "path": record["path"], "triangles_reimported": triangles})
    scene = bpy.context.scene
    scene.world = bpy.data.worlds.new("Studio")
    scene.world.use_nodes = True
    scene.world.node_tree.nodes["Background"].inputs[0].default_value = (.45, .45, .45, 1)
    for name, position, energy, size in [("Soft", (2, -4, 5), 700, 6), ("Side", (-4, -2, 2), 350, 4)]:
        light = bpy.data.objects.new(name, bpy.data.lights.new(name, "AREA"))
        scene.collection.objects.link(light)
        light.location = position
        light.rotation_euler = (Vector((2, 0, -1)) - light.location).to_track_quat("-Z", "Y").to_euler()
        light.data.energy, light.data.size = energy, size
    camera = bpy.data.objects.new("Comparison camera", bpy.data.cameras.new("Comparison camera"))
    scene.collection.objects.link(camera)
    camera.location = (2.1, -10, -1.5)
    camera.rotation_euler = (Vector((2.1, 0, -1.5)) - camera.location).to_track_quat("-Z", "Y").to_euler()
    camera.data.type, camera.data.ortho_scale = "ORTHO", 7
    scene.camera = camera
    scene.render.engine = "CYCLES"
    scene.cycles.samples = 16
    scene.view_settings.view_transform = "Standard"
    bpy.ops.file.pack_all()
    bpy.ops.wm.save_as_mainfile(filepath=request["output"])
    bpy.ops.wm.open_mainfile(filepath=request["output"])
    Path(request["receipt"]).write_text(json.dumps({"models": receipts, "packed_and_reopened": True,
        "model_count": len(receipts)}, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
