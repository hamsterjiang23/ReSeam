"""Explicit static four-channel PBR binding inside the isolated Blender importer."""

from pathlib import Path
from typing import Any

import bpy  # pyright: ignore[reportMissingImports]
import numpy as np


def bind_pbr(meshes: list[Any], textures: dict[str, str], convention: str, folder: Path) -> dict[str, object]:
    material = bpy.data.materials.new("ReSeam_Explicit_PBR")
    material.use_nodes = True
    nodes, links = material.node_tree.nodes, material.node_tree.links
    nodes.clear()
    shader, output = nodes.new("ShaderNodeBsdfPrincipled"), nodes.new("ShaderNodeOutputMaterial")
    links.new(shader.outputs["BSDF"], output.inputs["Surface"])
    bound: dict[str, object] = {}
    for channel, socket in (("basecolor", "Base Color"), ("roughness", "Roughness"),
                            ("metallic", "Metallic"), ("normal", "Normal")):
        image = bpy.data.images.load(textures[channel], check_existing=False)
        image.colorspace_settings.name = "sRGB" if channel == "basecolor" else "Non-Color"
        if image.size[0] <= 0:
            raise ValueError("PBR_IMAGE_UNREADABLE:" + channel)
        if channel == "normal" and convention == "directx":
            pixels = np.empty(len(image.pixels), dtype=np.float32)
            image.pixels.foreach_get(pixels)
            pixels[1::4] = 1. - pixels[1::4]
            image.pixels.foreach_set(pixels)
            image.filepath_raw = str(folder / "normal_opengl.png")
            image.file_format = "PNG"
            image.save()
        texture = nodes.new("ShaderNodeTexImage")
        texture.image = image
        if channel == "normal":
            normal = nodes.new("ShaderNodeNormalMap")
            normal.space = "TANGENT"
            links.new(texture.outputs["Color"], normal.inputs["Color"])
            links.new(normal.outputs["Normal"], shader.inputs[socket])
        else:
            links.new(texture.outputs["Color"], shader.inputs[socket])
        bound[channel] = {"file": textures[channel], "width": image.size[0], "height": image.size[1],
                          "colorspace": image.colorspace_settings.name, "socket": socket}
    for obj in meshes:
        obj.data.materials.clear()
        obj.data.materials.append(material)
        for polygon in obj.data.polygons:
            polygon.material_index = 0
    return {"channels": bound, "normal_convention": convention,
            "normal_convention_verification": "declared; directional visual verification pending",
            "source_phong_reproduced": False, "shader": "explicit Blender Principled core PBR"}
