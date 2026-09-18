"""Controlled Blender operations. Request paths and fresh outputs are chosen by the parent."""

import json
import math
import sys
from pathlib import Path
from typing import Any

import bpy
from mathutils import Vector
from mathutils.bvhtree import BVHTree


def make_low(request: dict[str, Any]) -> Any:
    mesh = bpy.data.meshes.new("ReSeam_fixed_mesh")
    # glTF is Y-up; Blender's importer applies this same proper rotation.
    mesh.from_pydata([(v[0], -v[2], v[1]) for v in request["vertices"]], [], request["faces"])
    mesh.update()
    obj = bpy.data.objects.new("ReSeam_low", mesh)
    bpy.context.collection.objects.link(obj)
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    uv_layer = mesh.uv_layers.new(name="UVMap")
    if "corner_uv" in request:
        for polygon, coords in zip(mesh.polygons, request["corner_uv"], strict=True):
            for loop, uv in zip(polygon.loop_indices, coords, strict=True):
                uv_layer.data[loop].uv = uv
    for polygon in mesh.polygons:
        polygon.use_smooth = True
    return obj


def import_high(path: str) -> list[Any]:
    before = set(bpy.context.scene.objects)
    bpy.ops.import_scene.gltf(filepath=path)
    return [obj for obj in bpy.context.scene.objects if obj not in before and obj.type == "MESH"]


def emission_materials(objects: list[Any]) -> None:
    for obj in objects:
        for material in obj.data.materials:
            if material is None:
                continue
            material.use_nodes = True
            nodes, links = material.node_tree.nodes, material.node_tree.links
            shader = next((node for node in nodes if node.type == "BSDF_PRINCIPLED"), None)
            output = next((node for node in nodes if node.type == "OUTPUT_MATERIAL"), None)
            if output is None:
                output = nodes.new("ShaderNodeOutputMaterial")
            emission = nodes.new("ShaderNodeEmission")
            if shader is not None:
                color = shader.inputs["Base Color"]
                if color.is_linked:
                    links.new(color.links[0].from_socket, emission.inputs["Color"])
                else:
                    emission.inputs["Color"].default_value = color.default_value
            links.new(emission.outputs[0], output.inputs["Surface"])


def low_material(obj: Any, base_path: str | None = None, normal_path: str | None = None) -> Any:
    material = bpy.data.materials.new("ReSeam_baked")
    material.use_nodes = True
    obj.data.materials.clear()
    obj.data.materials.append(material)
    nodes, links = material.node_tree.nodes, material.node_tree.links
    shader = nodes.get("Principled BSDF")
    shader.inputs["Metallic"].default_value = 0
    shader.inputs["Roughness"].default_value = 0.65
    if base_path:
        texture = nodes.new("ShaderNodeTexImage")
        texture.image = bpy.data.images.load(base_path)
        links.new(texture.outputs["Color"], shader.inputs["Base Color"])
    if normal_path:
        texture = nodes.new("ShaderNodeTexImage")
        texture.image = bpy.data.images.load(normal_path)
        texture.image.colorspace_settings.name = "Non-Color"
        normal = nodes.new("ShaderNodeNormalMap")
        links.new(texture.outputs["Color"], normal.inputs["Color"])
        links.new(normal.outputs["Normal"], shader.inputs["Normal"])
    return material


def scene_settings(resolution: int) -> None:
    scene = bpy.context.scene
    scene.render.engine = "CYCLES"
    scene.cycles.samples = 16
    scene.render.resolution_x = resolution
    scene.render.resolution_y = resolution
    scene.render.resolution_percentage = 100
    scene.render.film_transparent = True
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_mode = "RGBA"
    scene.view_settings.view_transform = "Standard"
    scene.view_settings.look = "None"
    scene.view_settings.exposure = 0
    scene.view_settings.gamma = 1


def bake(request: dict[str, Any], low: Any) -> None:
    high = import_high(request["high"])
    high_vertices, high_faces = [], []
    for obj in high:
        obj.data.calc_loop_triangles()
        offset = len(high_vertices)
        high_vertices.extend(obj.matrix_world @ vertex.co for vertex in obj.data.vertices)
        high_faces.extend(
            tuple(offset + index for index in triangle.vertices) for triangle in obj.data.loop_triangles
        )
    surface = BVHTree.FromPolygons(high_vertices, high_faces, all_triangles=True)
    samples = ((1 / 3, 1 / 3, 1 / 3), (0.6, 0.2, 0.2), (0.2, 0.6, 0.2), (0.2, 0.2, 0.6))
    probe_hits, probe_distances = [], []
    extrusion = float(request["cage_extrusion"])
    for face in low.data.polygons:
        hits, distances = 0, []
        vertices = [low.data.vertices[i] for i in face.vertices]
        for weights in samples:
            point = sum(
                (vertex.co * weight for vertex, weight in zip(vertices, weights, strict=True)), Vector()
            )
            normal = sum(
                (vertex.normal * weight for vertex, weight in zip(vertices, weights, strict=True)), Vector()
            ).normalized()
            hit = surface.ray_cast(point + normal * extrusion, -normal, extrusion * 4)
            hits += hit[0] is not None
            nearest = surface.find_nearest(point)
            if nearest[3] is not None:
                distances.append(float(nearest[3]))
        probe_hits.append(hits / len(samples))
        probe_distances.extend(distances)
    original_surfaces = []
    for obj in high:
        for material in obj.data.materials:
            if material is not None and material.use_nodes:
                output = next((n for n in material.node_tree.nodes if n.type == "OUTPUT_MATERIAL"), None)
                if output is not None and output.inputs["Surface"].is_linked:
                    original_surfaces.append(
                        (material, output, output.inputs["Surface"].links[0].from_socket)
                    )
    emission_materials(high)
    resolution = int(request.get("resolution", 1024))
    scene_settings(resolution)
    material = low_material(low)
    scene = bpy.context.scene
    scene.render.bake.use_selected_to_active = True
    scene.render.bake.use_cage = True
    scene.render.bake.cage_extrusion = float(request["cage_extrusion"])
    scene.render.bake.margin = 8
    scene.render.bake.margin_type = "EXTEND"
    scene.render.bake.normal_space = "TANGENT"
    scene.render.bake.use_clear = True
    bpy.ops.object.select_all(action="DESELECT")
    for obj in high + [low]:
        obj.select_set(True)
    bpy.context.view_layer.objects.active = low
    outputs: dict[str, str] = {}
    for label, kind in (("BaseColor", "EMIT"), ("Normal", "NORMAL")):
        if kind == "NORMAL":
            for source_material, output_node, source_socket in original_surfaces:
                source_material.node_tree.links.new(source_socket, output_node.inputs["Surface"])
        image = bpy.data.images.new(label, width=resolution, height=resolution, alpha=True)
        image.generated_color = (0, 0, 0, 0)
        image.colorspace_settings.name = "sRGB" if label == "BaseColor" else "Non-Color"
        target = material.node_tree.nodes.new("ShaderNodeTexImage")
        target.image = image
        material.node_tree.nodes.active = target
        bpy.ops.object.bake(type=kind)
        output = str(Path(request["folder"]) / (label + ".png"))
        image.filepath_raw = output
        image.file_format = "PNG"
        image.save()
        outputs[label] = output
    low_material(low, outputs["BaseColor"], outputs["Normal"])
    bpy.ops.object.select_all(action="DESELECT")
    low.select_set(True)
    bpy.context.view_layer.objects.active = low
    bpy.ops.export_scene.gltf(
        filepath=str(Path(request["folder"]) / "asset.glb"),
        export_format="GLB",
        use_selection=True,
        export_tangents=True,
    )
    Path(request["output"]).write_text(
        json.dumps(
            {
                "channels": outputs,
                "cycles_samples": 16,
                "selected_to_active": True,
                "cage_extrusion": scene.render.bake.cage_extrusion,
                "projection_probe": {
                    "samples_per_face": len(samples),
                    "face_hit_fraction": probe_hits,
                    "mean_hit_fraction": sum(probe_hits) / len(probe_hits),
                    "nearest_surface_distance_mean": sum(probe_distances) / max(1, len(probe_distances)),
                    "nearest_surface_distance_max": max(probe_distances, default=None),
                    "scope": "4 barycentric probes per face, NOT exact texel coverage or exact Cycles cage rays",
                },
            }
        ),
        encoding="utf-8",
    )


def render(request: dict[str, Any], low: Any) -> None:
    if request["mode"] == "render_high":
        low.hide_render = True
        objects = import_high(request["high"])
    else:
        objects = [low]
        low_material(low, request["texture"], request.get("normal"))
    if request.get("normal_preview"):
        for obj in objects:
            for material in obj.data.materials:
                material.use_nodes = True
                nodes, links = material.node_tree.nodes, material.node_tree.links
                shader = next((n for n in nodes if n.type == "BSDF_PRINCIPLED"), None)
                normal_socket = None
                if shader is not None and shader.inputs["Normal"].is_linked:
                    normal_socket = shader.inputs["Normal"].links[0].from_socket
                if normal_socket is None:
                    normal_socket = nodes.new("ShaderNodeNewGeometry").outputs["Normal"]
                scale = nodes.new("ShaderNodeVectorMath")
                scale.operation = "SCALE"
                scale.inputs[3].default_value = 0.5
                links.new(normal_socket, scale.inputs[0])
                shift = nodes.new("ShaderNodeVectorMath")
                shift.operation = "ADD"
                shift.inputs[1].default_value = (0.5, 0.5, 0.5)
                links.new(scale.outputs[0], shift.inputs[0])
                emission = nodes.new("ShaderNodeEmission")
                links.new(shift.outputs[0], emission.inputs[0])
                output = next(n for n in nodes if n.type == "OUTPUT_MATERIAL")
                links.new(emission.outputs[0], output.inputs["Surface"])
    else:
        emission_materials(objects)
    scene_settings(int(request.get("resolution", 512)))
    camera_data = bpy.data.cameras.new("EvaluationCamera")
    camera = bpy.data.objects.new("EvaluationCamera", camera_data)
    bpy.context.collection.objects.link(camera)
    bpy.context.scene.camera = camera
    center = Vector(request["camera_center_blender"])
    distance = float(request["camera_distance"])
    camera_data.type = "ORTHO"
    camera_data.ortho_scale = float(request["ortho_scale"])
    camera_data.clip_start = max(distance / 1000, 0.0001)
    camera_data.clip_end = distance * 10
    folder = Path(request["folder"])
    for index, (azimuth, elevation) in enumerate(request["views"]):
        az, el = math.radians(azimuth), math.radians(elevation)
        direction = Vector((math.sin(az) * math.cos(el), -math.cos(az) * math.cos(el), math.sin(el)))
        camera.location = center + direction * distance
        camera.rotation_euler = (center - camera.location).to_track_quat("-Z", "Y").to_euler()
        bpy.context.scene.render.filepath = str(folder / f"view_{index:02d}.png")
        bpy.ops.render.render(write_still=True)
    Path(request["output"]).write_text(
        json.dumps(
            {
                "views": request["views"],
                "renderer": "Blender Cycles",
                "projection": "ORTHO",
                "normal_preview": bool(request.get("normal_preview")),
            }
        ),
        encoding="utf-8",
    )


def main() -> None:
    request_path = Path(sys.argv[sys.argv.index("--") + 1])
    request = json.loads(request_path.read_text(encoding="utf-8-sig"))
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)
    obj = make_low(request)
    mode = request["mode"]
    if mode == "bake":
        bake(request, obj)
        return
    if mode in ("render_high", "render_low"):
        render(request, obj)
        return
    if mode == "annotate":
        obj["reseam_vertex_ids"] = list(range(len(obj.data.vertices)))
        edges = {tuple(sorted(edge)): i for i, edge in enumerate(request["edges"])}
        attribute = obj.data.attributes.new("reseam_edge_id", "INT", "EDGE")
        for edge in obj.data.edges:
            attribute.data[edge.index].value = edges[tuple(sorted(edge.vertices))]
        hints = {
            int(item["edge_id"])
            for item in request["annotation"].get("preferences", [])
            if item["kind"] == "prefer_cut"
        }
        actual_seams = {tuple(edge) for edge in request.get("seam_edges", [])}
        for edge in obj.data.edges:
            edge.use_seam = (
                tuple(sorted(edge.vertices)) in actual_seams
                if request.get("editing_template")
                else attribute.data[edge.index].value in hints
            )
        attribute = obj.data.attributes.new("reseam_face_id", "INT", "FACE")
        for face in obj.data.polygons:
            attribute.data[face.index].value = face.index
        obj["reseam_annotation"] = json.dumps(request["annotation"])
        neutral = bpy.data.materials.new("ReSeam neutral")
        neutral.diffuse_color = (0.45, 0.48, 0.5, 1)
        protected = bpy.data.materials.new("ReSeam protected region")
        protected.diffuse_color = (0.08, 0.5, 0.65, 1)
        obj.data.materials.append(neutral)
        obj.data.materials.append(protected)
        if request.get("base_texture"):
            low_material(obj, request["base_texture"], request.get("normal_texture"))
            obj.data.materials.append(protected)
        text = bpy.data.texts.load(request["addon"])
        text.use_module = False
        bpy.ops.file.pack_all()
        bpy.ops.wm.save_as_mainfile(filepath=request["output"])
        return
    if mode not in ("smart", "pack"):
        raise ValueError(f"Unsupported Blender mode: {mode}")
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    if mode == "smart":
        bpy.ops.uv.smart_project(angle_limit=math.radians(66), island_margin=0)
    else:
        bpy.ops.uv.select_all(action="SELECT")
        bpy.ops.uv.average_islands_scale()
        bpy.ops.uv.pack_islands(
            rotate=True, margin_method="FRACTION", margin=float(request.get("margin", 8 / 1024))
        )
    bpy.ops.uv.select_all(action="SELECT")
    bpy.ops.uv.seams_from_islands(mark_seams=True, mark_sharp=False)
    bpy.ops.object.mode_set(mode="OBJECT")
    corners = [
        [list(obj.data.uv_layers.active.data[loop].uv) for loop in polygon.loop_indices]
        for polygon in obj.data.polygons
    ]
    seam_edges = [sorted(edge.vertices) for edge in obj.data.edges if edge.use_seam]
    Path(request["output"]).write_text(
        json.dumps({"corner_uv": corners, "seam_edges": seam_edges}), encoding="utf-8"
    )


if __name__ == "__main__":
    main()
