"""Normalize selected source materials into a face-ordered BaseColor reference."""

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import numpy as np
import trimesh
from PIL import Image
from trimesh.visual.material import PBRMaterial

from ..budget_texture import decode_rgba8, encode_rgba8
from ..types import FloatArray
from .material import _source_document
from .state import Snapshot, check_deadline


@dataclass
class ColorTile:
    pixels: np.ndarray[tuple[int, ...], np.dtype[np.uint8]]
    corner_uv: FloatArray
    repeat: bool
    record: dict[str, object]


def source_tiles(source: Snapshot, deadline: float) -> tuple[list[ColorTile], np.ndarray]:
    """Follow the importer's sorted instance/face order, verifying actual XYZ."""
    path = Path(str(source.metadata.get("reference_glb", "")))
    if not path.is_file():
        raise ValueError("PARTS_BASECOLOR_REFERENCE_REQUIRED")
    raw_document, _ = _source_document(path)
    # glTF JSON is an external dynamic boundary; checks below restrict its features.
    document = cast(dict[str, Any], raw_document)
    for mesh in document.get("meshes", []):
        for primitive in mesh.get("primitives", []):
            if "COLOR_0" in primitive.get("attributes", {}):
                raise ValueError("PARTS_VERTEX_COLOR_UNSUPPORTED")
    materials = document.get("materials", [])
    wraps: set[bool] = set()
    for material in materials:
        pbr = material.get("pbrMetallicRoughness", {})
        definition = pbr.get("baseColorTexture", {})
        if (material.get("alphaMode", "OPAQUE") != "OPAQUE" or definition.get("texCoord", 0) != 0
                or definition.get("extensions")
                or set(material.get("extensions", {})) - {"KHR_materials_specular", "KHR_materials_ior"}):
            raise ValueError("PARTS_STANDARD_OPAQUE_BASECOLOR_REQUIRED")
        if "index" not in definition:
            raise ValueError("PARTS_BASECOLOR_TEXTURE_REQUIRED")
        texture = document["textures"][definition["index"]]
        sampler = document.get("samplers", [])[texture["sampler"]] if "sampler" in texture else {}
        s, t = sampler.get("wrapS", 10497), sampler.get("wrapT", 10497)
        if s != t or s not in (10497, 33071):
            raise ValueError("PARTS_SAMPLER_REPEAT_OR_CLAMP_REQUIRED")
        wraps.add(s == 10497)
    # trimesh does not retain per-material wrap modes; never guess mixed samplers.
    if len(wraps) != 1:
        raise ValueError("PARTS_MIXED_SAMPLERS_REQUIRE_SEPARATE_SOURCES")
    repeat = next(iter(wraps))
    scene = trimesh.load_scene(path, process=False)
    tiles: list[ColorTile] = []
    triangle_rows: list[FloatArray] = []
    tile_ids: list[int] = []
    for node in sorted(scene.graph.nodes_geometry):
        check_deadline(deadline)
        transform, name = scene.graph[node]
        geometry = scene.geometry[name]
        if not isinstance(geometry, trimesh.Trimesh):
            raise TypeError("PARTS_TRIANGLE_SOURCE_REQUIRED")
        material = getattr(geometry.visual, "material", None)
        uv = getattr(geometry.visual, "uv", None)
        if not isinstance(material, PBRMaterial) or material.baseColorTexture is None or uv is None:
            raise ValueError("PARTS_BASECOLOR_TEXTURE_AND_UV_REQUIRED")
        faces = np.asarray(geometry.faces, dtype=np.int64).copy()
        if np.linalg.det(transform[:3, :3]) < 0:
            faces = faces[:, [0, 2, 1]]
        xyz = trimesh.transform_points(geometry.vertices, transform)
        triangle_rows.append(xyz[faces])
        corners = np.asarray(uv, dtype=float)[faces]
        if not np.isfinite(corners).all() or np.any(corners < 0) or np.any(corners > 1):
            raise ValueError("PARTS_SOURCE_UV_OUTSIDE_UNIT_TILE_UNSUPPORTED")
        pixels = np.asarray(material.baseColorTexture.convert("RGBA"), dtype=np.uint8)
        raw = material.baseColorFactor
        factor = np.ones(4) if raw is None else np.asarray(raw, dtype=float) / (
            255. if np.issubdtype(np.asarray(raw).dtype, np.integer) else 1.)
        if factor.shape != (4,) or not np.isfinite(factor).all():
            raise ValueError("PARTS_INVALID_BASECOLOR_FACTOR")
        # One uniform source atlas factor cannot represent different materials.
        # Record the explicit RGBA8 normalization instead of silently discarding it.
        if not np.array_equal(factor, np.ones(4)):
            pixels = encode_rgba8(decode_rgba8(pixels) * factor)
        if np.any(pixels[..., 3] != 255):
            raise ValueError("PARTS_OPAQUE_BASECOLOR_REQUIRED")
        tiles.append(ColorTile(pixels, corners, repeat,
                               {"node": node, "source": str(path), "factor": factor.tolist(),
                                "factor_baked_to_rgba8": not np.array_equal(factor, np.ones(4))}))
        tile_ids.extend([len(tiles) - 1] * len(faces))
    expected = source.state.surface.mesh.vertices[source.state.surface.mesh.faces]
    actual = np.concatenate(triangle_rows)
    if actual.shape != expected.shape or not np.allclose(actual, expected, rtol=0, atol=1e-7):
        raise ValueError("PARTS_REFERENCE_FACE_CORNER_IDENTITY_MISMATCH")
    return tiles, np.asarray(tile_ids, dtype=np.int64)


def atlas_tiles(tiles: list[ColorTile], deadline: float) -> tuple[Image.Image, list[FloatArray], list[dict[str, object]]]:
    """Shelf-pack full resolution tiles with wrap-aware one-texel gutters."""
    unique: dict[tuple[object, ...], int] = {}
    owners: list[int] = []
    representatives: list[ColorTile] = []
    for tile in tiles:
        check_deadline(deadline)
        key = (*tile.pixels.shape, tile.repeat, hashlib.sha256(tile.pixels.tobytes()).hexdigest())
        if key not in unique:
            unique[key] = len(representatives)
            representatives.append(tile)
        owners.append(unique[key])
    widths = [tile.pixels.shape[1] + 2 for tile in representatives]
    heights = [tile.pixels.shape[0] + 2 for tile in representatives]
    target = max(max(widths), int(np.ceil(np.sqrt(sum(w*h for w, h in zip(widths, heights, strict=True))))))
    placements: list[tuple[int, int]] = []
    x = y = row_height = 0
    width = 0
    for w, h in zip(widths, heights, strict=True):
        if x + w > target:
            y += row_height
            x = row_height = 0
        placements.append((x, y))
        x += w
        row_height = max(row_height, h)
        width = max(width, x)
    height = y + row_height
    if width * height > 128 * 1024**2:
        raise ValueError("PARTS_SOURCE_ATLAS_EXCEEDS_128M_PIXELS")
    atlas = Image.new("RGBA", (width, height), (0, 0, 0, 255))
    mapped: list[FloatArray] = []
    records: list[dict[str, object]] = []
    for tile, (x, y) in zip(representatives, placements, strict=True):
        check_deadline(deadline)
        pixels = tile.pixels
        h, w = pixels.shape[:2]
        padded = np.pad(pixels, ((1, 1), (1, 1), (0, 0)), mode="wrap" if tile.repeat else "edge")
        atlas.paste(Image.fromarray(padded), (x, y))
    for tile, owner in zip(tiles, owners, strict=True):
        x, y = placements[owner]
        h, w = tile.pixels.shape[:2]
        uv = tile.corner_uv.copy()
        uv[..., 0] = (x + 1 + uv[..., 0] * w) / width
        uv[..., 1] = 1 - (y + 1 + (1 - uv[..., 1]) * h) / height
        mapped.append(uv)
        records.append({**tile.record, "size": [w, h], "atlas_xy": [x+1, y+1],
                        "repeat": tile.repeat, "gutter_pixels": 1})
    return atlas, cast(list[FloatArray], mapped), records
