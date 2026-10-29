"""Visibility-correct, traceable diagnostic views with original face picking."""

import colorsys
from pathlib import Path

import numpy as np
from numpy.typing import NDArray
from PIL import Image, ImageDraw

from ..budget_render import _rasterize
from ..budget_texture import encode_rgba8, load_reference, sample_rgba8
from ..mesh import chart_labels, make_mesh
from ..types import FloatArray, IntArray
from .regions import face_selection, selected_faces
from .state import Snapshot, check_deadline, file_hash, require_snapshot, write_json


def _palette(ids: IntArray) -> NDArray[np.uint8]:
    return (np.asarray([colorsys.hsv_to_rgb((int(value) * .61803398875) % 1, .55, .88)
                        for value in ids], dtype=float) * 255).astype(np.uint8)


def _texture(
    request: dict[str, object], snapshot: Snapshot,
) -> tuple[NDArray[np.uint8], FloatArray, FloatArray, bool, dict[str, object]]:
    surface = snapshot.state.surface
    if "texture" in request:
        if not isinstance(request["texture"], str):
            raise TypeError("TEXTURE_PATH_REQUIRED")
        path = Path(request["texture"])
        with Image.open(path) as image:
            pixels = np.asarray(image.convert("RGBA"), dtype=np.uint8)
        return pixels, surface.uv[surface.uv_faces], np.ones(4), False, {
            "path": str(path.resolve()), "sha256": file_hash(path), "uv": "current_snapshot",
            "filter": "bilinear", "wrap": "clamp", "scope": "explicit color texture only",
        }
    reference_path = snapshot.metadata.get("reference_glb")
    if not isinstance(reference_path, str):
        raise TypeError("SOURCE_BASECOLOR_REFERENCE_REQUIRED")
    path = Path(reference_path)
    reference = load_reference(path)
    triangles = surface.mesh.vertices[surface.mesh.faces]
    if reference.triangles.shape != triangles.shape or not np.allclose(
        reference.triangles, triangles, rtol=0, atol=max(1e-12, reference.diagonal * 1e-7)
    ):
        raise ValueError("SOURCE_FACE_CORNER_IDENTITY_REQUIRED: supply a baked texture for different geometry")
    # Read raw factors to avoid lossy float-to-byte conversion in trimesh.
    from .material import _source_document, _wraps
    document, _ = _source_document(path)
    materials = document.get("materials", [])
    if not isinstance(materials, list) or len(materials) != 1 or not isinstance(materials[0], dict):
        raise ValueError("SINGLE_SOURCE_COLOR_MATERIAL_REQUIRED")
    material = materials[0]
    pbr = material.get("pbrMetallicRoughness", {})
    if not isinstance(pbr, dict):
        raise TypeError("INVALID_BASECOLOR_DEFINITION")
    definition = pbr.get("baseColorTexture")
    if (not isinstance(definition, dict) or definition.get("texCoord", 0) != 0
            or definition.get("extensions") or material.get("alphaMode", "OPAQUE") != "OPAQUE"):
        raise ValueError("UNSUPPORTED_DIAGNOSTIC_BASECOLOR_DEFINITION")
    factor = np.asarray(pbr.get("baseColorFactor", [1, 1, 1, 1]), dtype=float)
    if factor.shape != (4,) or not np.isfinite(factor).all() or np.any(factor < 0) or np.any(factor > 1):
        raise ValueError("INVALID_BASECOLOR_FACTOR")
    repeat = _wraps(document, (definition, None, None, None))[0]
    return reference.image, reference.corner_uv, factor, repeat, {
        "path": str(path.resolve()), "sha256": file_hash(path), "uv": "source_face_corner_uv",
        "filter": "bilinear", "wrap": "repeat" if repeat else "clamp",
        "ignored_material_extensions": material.get("extensions", {}),
        "scope": "BaseColor only; unlit diagnostic; no PBR, normal, or GPU verification",
    }


def _seam_mask(snapshot: Snapshot, face_ids: IntArray, bary: FloatArray,
               render_vertices: FloatArray, render_faces: IntArray, original_ids: IntArray,
               azimuth: float, size: int) -> NDArray[np.bool_]:
    """Visible fragments within 0.65 pixels of an actual original seam edge."""
    angle = np.radians(azimuth)
    right = np.asarray([np.cos(angle), 0, -np.sin(angle)])
    projection = np.column_stack((render_vertices @ right, render_vertices[:, 1]))
    scale = (size - 4) / max(float(np.ptp(projection, axis=0).max()), 1e-12)
    projected = projection[render_faces] * scale
    cross = ((projected[:, 1, 0] - projected[:, 0, 0]) * (projected[:, 2, 1] - projected[:, 0, 1])
             - (projected[:, 1, 1] - projected[:, 0, 1]) * (projected[:, 2, 0] - projected[:, 0, 0]))
    opposite = np.stack((projected[:, 2] - projected[:, 1], projected[:, 0] - projected[:, 2],
                         projected[:, 1] - projected[:, 0]), axis=1)
    altitude = np.abs(cross[:, None]) / np.maximum(np.linalg.norm(opposite, axis=2), 1e-30)
    seams = {snapshot.state.surface.mesh.edges[index] for index in snapshot.state.surface.seams}
    source_faces = snapshot.state.surface.mesh.faces[original_ids]
    flags = np.asarray([[tuple(sorted((int(face[(corner + 1) % 3]), int(face[(corner + 2) % 3]))))
                         in seams for corner in range(3)] for face in source_faces], dtype=bool)
    visible = face_ids >= 0
    result = np.zeros(face_ids.shape, dtype=bool)
    local = face_ids[visible]
    result[visible] = np.any(flags[local] & (bary[visible] * altitude[local] <= .65), axis=1)
    return result


def render_views(request: dict[str, object], output: Path, deadline: float) -> dict[str, object]:
    snapshot = require_snapshot(request)
    surface = snapshot.state.surface
    size = request.get("size", 640)
    if type(size) is not int or not 64 <= size <= 2048:
        raise ValueError("VIEW_SIZE_OUT_OF_RANGE")
    mode = request.get("mode", "groups")
    if mode not in ("groups", "charts", "regions", "basecolor", "checker", "errors"):
        raise ValueError("UNKNOWN_VIEW_MODE")
    seams, annotate = request.get("seams", True), request.get("annotate", mode in ("groups", "charts", "regions"))
    if not isinstance(seams, bool) or not isinstance(annotate, bool):
        raise TypeError("VIEW_FLAGS_MUST_BE_BOOLEAN")
    selected = np.asarray(selected_faces(request, snapshot), dtype=np.int64)
    vertices, inverse = np.unique(surface.mesh.faces[selected], return_inverse=True)
    render_mesh = make_mesh(surface.mesh.vertices[vertices], inverse.reshape(-1, 3))
    labels = chart_labels(surface)
    colors = labels.copy()
    if mode == "groups":
        for index, group in enumerate(snapshot.state.groups):
            colors[list(group.first_faces + group.second_faces)] = len(np.unique(labels)) + index
    if mode == "regions":
        colors[:] = 0
        regions = snapshot.metadata.get("regions", [])
        if isinstance(regions, list):
            for index, region in enumerate(regions):
                if isinstance(region, dict):
                    ids = face_selection(region.get("faces"), len(colors))
                    colors[ids] = index + 1
    face_colors = _palette(colors)
    if mode == "errors":
        errors = face_selection(request.get("error_faces", []), len(colors), allow_empty=True)
        face_colors[:] = (160, 178, 190)
        face_colors[errors] = (235, 40, 45)
    texture_data = _texture(request, snapshot) if mode == "basecolor" else None
    images: dict[str, str] = {}
    picks: dict[str, str] = {}
    pixel_queries = request.get("pick_pixels", {})
    if not isinstance(pixel_queries, dict) or not set(pixel_queries) <= {"front", "back", "left", "right"}:
        raise ValueError("PICK_PIXELS_REQUIRES_NAMED_VIEWS")
    for coordinates in pixel_queries.values():
        if (not isinstance(coordinates, list) or any(not isinstance(xy, list) or len(xy) != 2
                or any(type(value) is not int or not 0 <= value < size for value in xy) for xy in coordinates)):
            raise ValueError("PICK_PIXELS_REQUIRES_IN_BOUNDS_XY")
    picked_faces: dict[str, list[dict[str, object]]] = {}
    visible_counts: dict[str, int] = {}
    for name, azimuth in (("front", 0.), ("back", 180.), ("left", 90.), ("right", -90.)):
        check_deadline(deadline)
        local_faces, bary, depth, _ = _rasterize(render_mesh, size, azimuth, 0, deadline)
        visible = local_faces >= 0
        ids = selected[local_faces[visible]]
        original_faces = np.full(local_faces.shape, -1, dtype=np.int32)
        original_faces[visible] = ids
        pixels = np.empty((size, size, 3), dtype=np.uint8)
        pixels[:] = (245, 247, 250)
        if texture_data is not None:
            image, corner_uv, factor, repeat, _ = texture_data
            uv = np.einsum("nk,nkj->nj", bary[visible], corner_uv[ids])
            rgba = sample_rgba8(image, uv, repeat=repeat) * factor
            pixels[visible] = encode_rgba8(rgba)[:, :3]
        elif mode == "checker":
            uv = np.einsum("nk,nkj->nj", bary[visible], surface.uv[surface.uv_faces[ids]])
            checks = (np.floor(uv * 24).astype(np.int64).sum(axis=1) % 2).astype(bool)
            pixels[visible] = np.where(checks[:, None], [40, 55, 75], [235, 235, 235]).astype(np.uint8)
        else:
            pixels[visible] = face_colors[ids]
        if seams:
            edge = _seam_mask(snapshot, local_faces, bary, render_mesh.vertices, render_mesh.faces,
                              selected, azimuth, size)
            pixels[edge] = (90, 30, 45)
        picture = Image.fromarray(pixels)
        if annotate:
            draw = ImageDraw.Draw(picture)
            visible_labels = np.full(local_faces.shape, -1, dtype=np.int64)
            visible_labels[visible] = colors[ids]
            for label in np.unique(colors[ids]):
                ys, xs = np.nonzero(visible_labels == label)
                if len(xs) < 4:
                    continue
                middle = len(xs) // 2
                draw.text((int(xs[middle]), int(ys[middle])), str(int(label)), fill=(20, 20, 20),
                          stroke_width=1, stroke_fill="white")
        path = output / f"{name}.png"
        picture.save(path)
        images[name] = str(path.resolve())
        pick = output / f"{name}_faces.npz"
        np.savez_compressed(pick, face_ids=original_faces, barycentric=bary.astype(np.float32),
                            depth=np.where(visible, depth, 0).astype(np.float32))
        picks[name] = str(pick.resolve())
        visible_counts[name] = int(visible.sum())
        coordinates = pixel_queries.get(name, [])
        assert isinstance(coordinates, list)
        picked_faces[name] = [{"pixel": [x, y], "face_id": int(original_faces[y, x]),
                               "barycentric": bary[y, x].tolist() if original_faces[y, x] >= 0 else None,
                               "chart_id": int(labels[original_faces[y, x]]) if original_faces[y, x] >= 0 else None}
                              for x, y in coordinates]
    check_deadline(deadline)
    uv_picture = Image.new("RGB", (size, size), "white")
    draw = ImageDraw.Draw(uv_picture)
    uv_pixels = surface.uv * np.array([size - 24, -(size - 24)]) + np.array([12, size - 12])
    for face_id in selected:
        draw.polygon([tuple(point) for point in uv_pixels[surface.uv_faces[face_id]]],
                     fill=tuple(int(value) for value in face_colors[face_id]), outline=(80, 80, 80))
    for label in np.unique(labels[selected]):
        face = surface.uv_faces[selected[np.flatnonzero(labels[selected] == label)[0]]]
        draw.text(tuple(uv_pixels[face].mean(axis=0)), str(int(label)), fill="black",
                  stroke_width=1, stroke_fill="white")
    uv_picture.save(output / "uv.png")
    images["uv"] = str((output / "uv.png").resolve())
    record: dict[str, object] = {
        "state_hash": snapshot.state_hash, "asset_id": snapshot.asset_id, "images": images,
        "face_rasters": picks, "visible_pixels": visible_counts, "mode": mode,
        "picked_faces": picked_faces,
        "selected_faces": selected.tolist(), "selected_faces_only": len(selected) != len(surface.mesh.faces),
        "source": texture_data[4] if texture_data is not None else None,
        "regions": snapshot.metadata.get("regions", []), "seams": seams, "annotate": annotate,
        "coordinate_convention": "Y-up; front +Z, back -Z, left +X, right -X; orthographic auto-fit",
        "picking": "npz face_ids[y,x] original face ID; -1 background; barycentric[y,x] original corner order",
        "visibility": "per-pixel z-buffer; two-sided surfaces; equal-depth ties retain lower original face ID",
        "quality_evidence": False, "note": "Diagnostic view only; unlit BaseColor and no mip/PBR/engine acceptance.",
    }
    write_json(output / "views.json", record)
    return {"images": images, "face_rasters": picks, "state_hash": snapshot.state_hash,
            "picked_faces": picked_faces,
            "report": str((output / "views.json").resolve()), "quality_evidence": False,
            "coordinate_convention": record["coordinate_convention"]}
