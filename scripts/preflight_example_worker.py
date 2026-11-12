"""Blender fixture authoring and identical-camera normal-bake previews."""

import json
import sys
from pathlib import Path
from typing import Any

import bpy
from mathutils import Vector


def fixtures(config: dict[str, Any]) -> None:
    bpy.ops.wm.read_factory_settings(use_empty=True)
    material = bpy.data.materials.new("Metal")
    material.use_nodes = True
    shader = material.node_tree.nodes.get("Principled BSDF")
    shader.inputs["Base Color"].default_value = (.21, .32, .40, 1.)
    shader.inputs["Metallic"].default_value = .7
    shader.inputs["Roughness"].default_value = .3
    for number, (center, dimensions) in enumerate(config["boxes"]):
        bpy.ops.mesh.primitive_cube_add(location=(center[0], -center[2], center[1]))
        obj = bpy.context.object
        obj.name = f"toolbox_{number}_high"
        obj.dimensions = (dimensions[0], dimensions[2], dimensions[1])
        bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
        bevel = obj.modifiers.new("Beveled_source", "BEVEL")
        bevel.width, bevel.segments = .035 if number == 0 else .015, 3
        bpy.ops.object.modifier_apply(modifier=bevel.name)
        obj.data.materials.append(material)
    root = Path(config["output"])
    bpy.ops.export_scene.gltf(filepath=str(root / "toolbox_high.glb"), export_format="GLB", export_yup=True)
    for obj in bpy.context.scene.objects:
        obj.location.x += .45
    bpy.ops.export_scene.gltf(filepath=str(root / "toolbox_shifted_high.glb"), export_format="GLB", export_yup=True)


def render(config: dict[str, Any]) -> None:
    root = Path(config["output"])
    for name in ("toolbox_high", "toolbox_shifted_high"):
        bpy.ops.wm.open_mainfile(filepath=str(root / (name + "_bake/preview.blend")))
        scene = bpy.context.scene
        scene.render.engine = "CYCLES"
        scene.cycles.device, scene.cycles.samples = "CPU", 16
        scene.render.threads_mode, scene.render.threads = "FIXED", 1
        scene.render.resolution_x = scene.render.resolution_y = 384
        scene.render.resolution_percentage = 100
        if scene.world is None:
            scene.world = bpy.data.worlds.new("ComparisonWorld")
        scene.world.use_nodes = True
        scene.world.node_tree.nodes.get("Background").inputs["Color"].default_value = (.16, .16, .16, 1.)
        scene.view_settings.view_transform = "Standard"
        camera_data = bpy.data.cameras.new("Comparison")
        camera = bpy.data.objects.new("Comparison", camera_data)
        scene.collection.objects.link(camera)
        camera.location = (3.5, -5., 3.)
        camera.rotation_euler = (Vector((0., 0., .3))-camera.location).to_track_quat("-Z", "Y").to_euler()
        camera_data.type, camera_data.ortho_scale = "ORTHO", 3.3
        scene.camera = camera
        for pos, energy, size in (((-3., -4., 5.), 700., 4.), ((4., 1., 3.), 500., 3.)):
            light = bpy.data.lights.new("Area", "AREA")
            light.energy, light.size = energy, size
            obj = bpy.data.objects.new("Area", light)
            scene.collection.objects.link(obj)
            obj.location = pos
            obj.rotation_euler = (Vector((0., 0., .3))-obj.location).to_track_quat("-Z", "Y").to_euler()
        # Neutral material isolates normal-map differences from missing BaseColor/ORM rays.
        low = bpy.data.objects.get("low") or next(obj for obj in scene.objects
                                                 if obj.type == "MESH" and not obj.hide_render)
        material = low.data.materials[0]
        shader = next(node for node in material.node_tree.nodes if node.type == "BSDF_PRINCIPLED")
        for label, value in (("Base Color", (.21, .32, .40, 1.)), ("Metallic", .7), ("Roughness", .3)):
            socket = shader.inputs[label]
            for link in list(socket.links):
                material.node_tree.links.remove(link)
            socket.default_value = value
        scene.render.image_settings.file_format = "PNG"
        scene.render.filepath = str(root / f"{name}_normal_preview.png")
        bpy.ops.render.render(write_still=True)
    (root / "preview_contract.json").write_text(json.dumps({"same_camera": True, "same_lighting": True,
        "same_neutral_material": True, "compared_channel": "baked Normal only", "resolution": 384,
        "Blender": bpy.app.version_string, "artist_accepted": False}), encoding="utf-8")


if __name__ == "__main__":
    request = json.loads(Path(sys.argv[sys.argv.index("--")+1]).read_text(encoding="utf-8"))
    if request.get("mode") == "render":
        render(request)
    else:
        fixtures(request)
