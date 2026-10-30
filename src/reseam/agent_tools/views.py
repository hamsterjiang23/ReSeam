"""Visibility-correct, traceable diagnostic views with original face picking."""

import colorsys
import math
import re
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np
from numpy.typing import NDArray
from PIL import Image, ImageDraw
from scipy.ndimage import distance_transform_edt
from scipy.ndimage import label as connected_components

from ..budget_render import _rasterize
from ..budget_state import owners_and_members
from ..budget_texture import _stratified_barycentric, encode_rgba8, load_reference, sample_rgba8
from ..mesh import chart_labels, make_mesh
from ..types import FloatArray, IntArray
from .regions import face_selection, selected_faces
from .state import Snapshot, check_deadline, file_hash, require_snapshot, write_json

if TYPE_CHECKING:
    from .color_reuse import ColorTarget


def _cameras(request: dict[str, object]) -> list[tuple[str, float, float]]:
    """Named diagnostic views; reject collisions with generated comparison files."""
    defaults = [("front", 0., 0.), ("back", 180., 0.), ("left", 90., 0.), ("right", -90., 0.)]
    if "cameras" not in request:
        return defaults
    entries = request["cameras"]
    if not isinstance(entries, list) or not 1 <= len(entries) <= 16:
        raise ValueError("CAMERAS_REQUIRE_1_TO_16_NAMED_VIEWS")
    result: list[tuple[str, float, float]] = []
    filenames = {"uv", "views", "request", "result", "provenance", "command", "process"}
    reserved = {"con", "prn", "aux", "nul", *[f"com{i}" for i in range(1, 10)],
                *[f"lpt{i}" for i in range(1, 10)]}
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != {"name", "azimuth", "elevation"}:
            raise ValueError("CAMERA_REQUIRES_NAME_AZIMUTH_ELEVATION")
        name, azimuth, elevation = entry["name"], entry["azimuth"], entry["elevation"]
        if (not isinstance(name, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,39}", name)
                or name.lower() in reserved):
            raise ValueError("CAMERA_NAME_MUST_BE_SAFE_FILE_STEM")
        generated = {name.lower()+suffix for suffix in ("", "_original", "_target", "_difference", "_faces")}
        if generated & filenames:
            raise ValueError("CAMERA_NAMES_COLLIDE_WITH_OUTPUTS")
        filenames.update(generated)
        if (type(azimuth) not in (int, float) or type(elevation) not in (int, float)
                or not math.isfinite(azimuth) or not math.isfinite(elevation)
                or not -90 <= elevation <= 90):
            raise ValueError("CAMERA_ANGLES_MUST_BE_FINITE_ELEVATION_MINUS90_TO90")
        result.append((name, float(azimuth), float(elevation)))
    return result


def _palette(ids: IntArray) -> NDArray[np.uint8]:
    return (np.asarray([colorsys.hsv_to_rgb((int(value) * .61803398875) % 1, .55, .88)
                        for value in ids], dtype=float) * 255).astype(np.uint8)


def _label_mapping(snapshot: Snapshot, colors: IntArray, charts: IntArray,
                   selected: IntArray, mode: str) -> dict[str, dict[str, object]]:
    result: dict[str, dict[str, object]] = {}
    chart_count = len(np.unique(charts))
    regions = snapshot.metadata.get("regions", [])
    for value in np.unique(colors[selected]):
        label = int(value)
        faces = np.flatnonzero(colors == label)
        entry: dict[str, object] = {"label": label, "kind": "uv_chart",
            "chart_ids": np.unique(charts[faces]).tolist(), "faces": faces.tolist(),
            "selected_faces": np.intersect1d(faces, selected).tolist()}
        if mode == "groups" and label >= chart_count:
            group = snapshot.state.groups[label - chart_count]
            entry.update(kind="shared_group", shared_group_id=group.group_id,
                         member_faces={"first": list(group.first_faces), "second": list(group.second_faces)})
        elif mode == "regions":
            entry["kind"] = "unassigned" if label == 0 else "region"
            if label and isinstance(regions, list) and isinstance(regions[label - 1], dict):
                region = regions[label - 1]
                entry.update(region_id=region.get("region_id"), name=region.get("name"),
                             declared_region_faces=region.get("faces"))
        result[str(label)] = entry
    return result


def _annotation_instances(visible_labels: IntArray, face_ids: NDArray[np.int32], minimum: int,
                          deadline: float) -> list[dict[str, object]]:
    """One label inside each sufficiently large visible eight-connected component."""
    result: list[dict[str, object]] = []
    for value in np.unique(visible_labels[visible_labels >= 0]):
        check_deadline(deadline)
        ys, xs = np.nonzero(visible_labels == value)
        x0, y0, x1, y1 = int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1
        mask = visible_labels[y0:y1, x0:x1] == value
        labeled = connected_components(mask, structure=np.ones((3, 3), dtype=np.uint8))
        if not isinstance(labeled, tuple):
            raise TypeError("COMPONENT_LABELS_REQUIRED")
        components = np.asarray(labeled[0])
        for component in range(1, int(labeled[1]) + 1):
            keep = components == component
            pixels = int(keep.sum())
            if pixels < minimum:
                continue
            distance = np.asarray(distance_transform_edt(np.pad(keep, 1)))[1:-1, 1:-1]
            y, x = np.unravel_index(int(distance.argmax()), distance.shape)
            result.append({"label": int(value), "component": component, "visible_pixels": pixels,
                           "pixel": [x0 + int(x), y0 + int(y)],
                           "visible_faces": np.unique(face_ids[y0:y1, x0:x1][keep]).tolist()})
    return result


def _frozen_colors(target: "ColorTarget", faces: IntArray, bary: FloatArray,
                   deadline: float) -> tuple[FloatArray, FloatArray]:
    original = target._source_color(np.einsum("nc,ncd->nd", bary, target.source_uv[faces]))
    frozen = original.copy()
    for face in np.unique(faces):
        if int(face) in target.slaves:
            check_deadline(deadline)
            keep = faces == face
            frozen[keep] = target.sample(int(face), bary[keep])
    return original, frozen


def _selected_change(target: "ColorTarget", snapshot: Snapshot, selected: IntArray,
                     deadline: float) -> dict[str, object]:
    bary = _stratified_barycentric(16)
    rows: list[dict[str, object]] = []
    means = np.zeros(len(selected))
    maxima = np.zeros(len(selected))
    for index, face in enumerate(selected):
        check_deadline(deadline)
        if int(face) in target.slaves:
            error = np.linalg.norm(target.sample(int(face), bary)[:, :3]
                                   - target.original(int(face), bary)[:, :3], axis=1)
            means[index], maxima[index] = float(error.mean()), float(error.max())
        rows.append({"face": int(face), "mean": float(means[index]), "max": float(maxima[index])})
    raw_protected = target.record.get("protected_faces", [])
    if not isinstance(raw_protected, list) or any(type(face) is not int for face in raw_protected):
        raise ValueError("INVALID_TARGET_PROTECTED_FACES")
    protected = set(raw_protected)
    areas = snapshot.state.surface.mesh.areas[selected]
    return {"per_face": rows, "mean": float(means.mean()), "max": float(maxima.max()),
            "area_weighted_mean": float(means @ areas / areas.sum()), "samples_per_face": len(bary),
            "protected_faces_selected": sorted(protected.intersection(map(int, selected))),
            "protected_faces_changed": [int(face) for index, face in enumerate(selected)
                                        if int(face) in protected and maxima[index] > 0],
            "sampling": "deterministic original-face barycentric probes; unchanged non-slaves use exact identity",
            "scope": "intentional frozen-target appearance change, not candidate reconstruction or quality acceptance"}


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
               azimuth: float, size: int, edge_ids: list[int] | None = None,
               elevation: float = 0.) -> NDArray[np.bool_]:
    """Visible fragments within 0.65 pixels of an actual original seam edge."""
    angle = np.radians(azimuth)
    right = np.asarray([np.cos(angle), 0, -np.sin(angle)])
    vertical = render_vertices[:, 1]
    if elevation != 0:
        pitch = np.radians(elevation)
        direction = np.asarray([np.sin(angle)*np.cos(pitch), np.sin(pitch), np.cos(angle)*np.cos(pitch)])
        vertical = render_vertices @ np.cross(direction, right)
    projection = np.column_stack((render_vertices @ right, vertical))
    scale = (size - 4) / max(float(np.ptp(projection, axis=0).max()), 1e-12)
    projected = projection[render_faces] * scale
    cross = ((projected[:, 1, 0] - projected[:, 0, 0]) * (projected[:, 2, 1] - projected[:, 0, 1])
             - (projected[:, 1, 1] - projected[:, 0, 1]) * (projected[:, 2, 0] - projected[:, 0, 0]))
    opposite = np.stack((projected[:, 2] - projected[:, 1], projected[:, 0] - projected[:, 2],
                         projected[:, 1] - projected[:, 0]), axis=1)
    altitude = np.abs(cross[:, None]) / np.maximum(np.linalg.norm(opposite, axis=2), 1e-30)
    seams = {snapshot.state.surface.mesh.edges[index] for index in
             (snapshot.state.surface.seams if edge_ids is None else edge_ids)}
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
    if mode not in ("groups", "charts", "regions", "basecolor", "checker", "errors", "wireframe", "normals", "distortion"):
        raise ValueError("UNKNOWN_VIEW_MODE")
    cameras = _cameras(request)
    uv_label_mode = request.get("uv_labels", "charts")
    if uv_label_mode not in ("charts", "owners"):
        raise ValueError("UV_LABELS_REQUIRE_CHARTS_OR_OWNERS")
    seams, annotate = request.get("seams", True), request.get("annotate", mode in ("groups", "charts", "regions"))
    if not isinstance(seams, bool) or not isinstance(annotate, bool):
        raise TypeError("VIEW_FLAGS_MUST_BE_BOOLEAN")
    target_field = None
    target_inputs: dict[Path, str] = {}
    if "target" in request:
        if mode != "basecolor":
            raise ValueError("TARGET_VIEW_REQUIRES_BASECOLOR_MODE")
        if "texture" in request:
            raise ValueError("TARGET_AND_EXPLICIT_TEXTURE_ARE_AMBIGUOUS")
        if not isinstance(request["target"], str):
            raise TypeError("TARGET_PATH_REQUIRED")
        from .color_reuse import ColorTarget

        target_field = ColorTarget(snapshot, Path(request["target"]))
        target_inputs = {target_field.path: file_hash(target_field.path),
                         target_field.path.parent / "target.npz": str(target_field.record["arrays_sha256"]),
                         Path(str(snapshot.metadata["reference_glb"])): str(target_field.record["source_sha256"])}
        topology = target_field.record.get("original_topology")
        if isinstance(topology, dict):
            target_inputs[target_field.path.parent / "original_topology.npz"] = str(topology["sha256"])
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
    if mode in ("wireframe", "normals", "distortion"):
        triangles = surface.mesh.vertices[surface.mesh.faces]
        normals = np.cross(triangles[:, 1]-triangles[:, 0], triangles[:, 2]-triangles[:, 0])
        normals /= np.maximum(np.linalg.norm(normals, axis=1, keepdims=True), 1e-30)
        face_colors[:] = (190, 198, 210)
        if mode == "normals":
            face_colors = np.rint((normals+1)*127.5).astype(np.uint8)
        elif mode == "distortion":
            from ..validation import jacobians
            singular = np.linalg.svd(jacobians(surface), compute_uv=False)
            ratio = singular[:, 0] / np.maximum(singular[:, 1], 1e-30)
            heat = np.clip(np.log2(np.maximum(ratio, 1))/4, 0, 1)
            face_colors = np.column_stack((255*heat, 160*(1-heat), 255*(1-heat))).astype(np.uint8)
    if mode == "errors":
        errors = face_selection(request.get("error_faces", []), len(colors), allow_empty=True)
        face_colors[:] = (160, 178, 190)
        face_colors[errors] = (235, 40, 45)
    texture_data = _texture(request, snapshot) if mode == "basecolor" and target_field is None else None
    images: dict[str, str] = {}
    picks: dict[str, str] = {}
    pixel_queries = request.get("pick_pixels", {})
    if not isinstance(pixel_queries, dict) or not set(pixel_queries) <= {name for name, _, _ in cameras}:
        raise ValueError("PICK_PIXELS_REQUIRES_NAMED_VIEWS")
    for coordinates in pixel_queries.values():
        if (not isinstance(coordinates, list) or any(not isinstance(xy, list) or len(xy) != 2
                or any(type(value) is not int or not 0 <= value < size for value in xy) for xy in coordinates)):
            raise ValueError("PICK_PIXELS_REQUIRES_IN_BOUNDS_XY")
    picked_faces: dict[str, list[dict[str, object]]] = {}
    visible_counts: dict[str, int] = {}
    annotations: dict[str, list[dict[str, object]]] = {}
    appearance_views: dict[str, dict[str, object]] = {}
    component_minimum = max(16, (size // 128) ** 2)
    for name, azimuth, elevation in cameras:
        check_deadline(deadline)
        local_faces, bary, depth, _ = _rasterize(render_mesh, size, azimuth, elevation, deadline)
        visible = local_faces >= 0
        ids = selected[local_faces[visible]]
        original_faces = np.full(local_faces.shape, -1, dtype=np.int32)
        original_faces[visible] = ids
        pixels = np.empty((size, size, 3), dtype=np.uint8)
        pixels[:] = (245, 247, 250)
        if target_field is not None:
            original, frozen = _frozen_colors(target_field, ids, bary[visible], deadline)
            pixels[visible] = encode_rgba8(frozen)[:, :3]
            source_pixels = pixels.copy()
            source_pixels[visible] = encode_rgba8(original)[:, :3]
            error = np.linalg.norm(original[:, :3] - frozen[:, :3], axis=1)
            difference = np.full_like(pixels, (245, 247, 250))
            heat = np.zeros((len(error), 4))
            heat[:, 0], heat[:, 3] = error / np.sqrt(3), 1.
            difference[visible] = encode_rgba8(heat)[:, :3]
            for suffix, content in (("original", source_pixels), ("target", pixels), ("difference", difference)):
                comparison_path = output / f"{name}_{suffix}.png"
                Image.fromarray(content).save(comparison_path)
                images[f"{name}_{suffix}"] = str(comparison_path.resolve())
            appearance_views[name] = {"visible_samples": len(error),
                "mean": float(error.mean()) if len(error) else None,
                "max": float(error.max()) if len(error) else None}
        elif texture_data is not None:
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
        if mode == "wireframe":
            wire = _seam_mask(snapshot, local_faces, bary, render_mesh.vertices, render_mesh.faces,
                              selected, azimuth, size, list(range(len(surface.mesh.edges))), elevation)
            pixels[wire] = (75, 85, 100)
        if seams:
            requested_edges = request.get("seam_edge_ids")
            edge_ids = None if requested_edges is None else face_selection(
                requested_edges, len(surface.mesh.edges), allow_empty=True)
            edge = _seam_mask(snapshot, local_faces, bary, render_mesh.vertices, render_mesh.faces,
                              selected, azimuth, size, edge_ids, elevation)
            pixels[edge] = (90, 30, 45)
        picture = Image.fromarray(pixels)
        annotations[name] = []
        if annotate:
            draw = ImageDraw.Draw(picture)
            visible_labels = np.full(local_faces.shape, -1, dtype=np.int64)
            visible_labels[visible] = colors[ids]
            annotations[name] = _annotation_instances(visible_labels, original_faces, component_minimum, deadline)
            for annotation in annotations[name]:
                point = annotation["pixel"]
                assert isinstance(point, list)
                draw.text(tuple(point), str(annotation["label"]), fill=(20, 20, 20), anchor="mm",
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
        edge_lookup: dict[tuple[int, ...], int] = {edge: i for i, edge in enumerate(surface.mesh.edges)}
        for picked in picked_faces[name]:
            face_id = int(str(picked["face_id"]))
            nearby: list[int] = []
            if face_id >= 0:
                face = surface.mesh.faces[face_id]
                weights = np.asarray(picked["barycentric"], dtype=float)
                for corner in np.argsort(weights):
                    edge = tuple(sorted((int(face[(corner+1) % 3]), int(face[(corner+2) % 3]))))
                    nearby.append(edge_lookup[edge])
            picked["nearby_edge_ids"] = nearby
    check_deadline(deadline)
    uv_picture = Image.new("RGB", (size, size), "white")
    draw = ImageDraw.Draw(uv_picture)
    uv_pixels = surface.uv * np.array([size - 24, -(size - 24)]) + np.array([12, size - 12])
    uv_labels = labels if uv_label_mode == "charts" else owners_and_members(snapshot.state)[0]
    uv_colors = face_colors if uv_label_mode == "charts" else _palette(uv_labels)
    uv_mapping: dict[str, dict[str, object]] = {}
    for face_id in selected:
        draw.polygon([tuple(point) for point in uv_pixels[surface.uv_faces[face_id]]],
                     fill=tuple(int(value) for value in uv_colors[face_id]), outline=(80, 80, 80))
    for number, label in enumerate(np.unique(uv_labels[selected]), 1):
        face_ids = selected[uv_labels[selected] == label]
        displayed = int(label) if uv_label_mode == "charts" else number
        chosen = 0
        if uv_label_mode == "owners":
            triangles = surface.uv[surface.uv_faces[face_ids]]
            edges = triangles[:, 1:]-triangles[:, :1]
            chosen = int(np.argmax(np.abs(np.linalg.det(edges))))
        face = surface.uv_faces[face_ids[chosen]]
        uv_mapping[str(displayed)] = {"chart_id" if uv_label_mode == "charts" else "owner_id": int(label),
                                     "faces": face_ids.tolist()}
        draw.text(tuple(uv_pixels[face].mean(axis=0)), str(displayed), fill="black",
                  stroke_width=1, stroke_fill="white")
    uv_picture.save(output / "uv.png")
    images["uv"] = str((output / "uv.png").resolve())
    record: dict[str, object] = {
        "state_hash": snapshot.state_hash, "asset_id": snapshot.asset_id, "images": images,
        "face_rasters": picks, "visible_pixels": visible_counts, "mode": mode,
        "picked_faces": picked_faces,
        "cameras": [{"name": name, "azimuth": azimuth, "elevation": elevation}
                    for name, azimuth, elevation in cameras],
        "camera_angle_convention": "degrees; Y-up; azimuth 0 at +Z, 90 at +X; elevation +90 top, -90 bottom",
        "label_mapping": _label_mapping(snapshot, colors, labels, selected, str(mode)),
        "annotation_instances": annotations, "label_component_min_pixels": component_minimum,
        "label_placement": "each visible eight-connected component; maximum interior clearance; same label keeps same color",
        "uv_label_mode": uv_label_mode, "uv_label_mapping": uv_mapping,
        "selected_faces": selected.tolist(), "selected_faces_only": len(selected) != len(surface.mesh.faces),
        "source": texture_data[4] if texture_data is not None else None,
        "regions": snapshot.metadata.get("regions", []), "seams": seams, "annotate": annotate,
        "coordinate_convention": "Y-up; front +Z, back -Z, left +X, right -X; orthographic auto-fit",
        "picking": "npz face_ids[y,x] original face ID; -1 background; barycentric[y,x] original corner order",
        "visibility": "per-pixel z-buffer; two-sided surfaces; equal-depth ties retain lower original face ID",
        "quality_evidence": False, "note": "Diagnostic view only; unlit BaseColor and no mip/PBR/engine acceptance.",
    }
    if target_field is not None:
        record["appearance_change"] = {**_selected_change(target_field, snapshot, selected, deadline),
            "views": appearance_views, "metric": "linear RGB Euclidean distance",
            "difference_image_scale": "fixed 0..sqrt(3) mapped to linear red then sRGB; no acceptance threshold",
            "comparison_images": "identical z-buffer and selection; clean source/target/difference before display overlays"}
        record["frozen_target"] = {"path": str(target_field.path), "target_hash": target_field.record["target_hash"],
            "policy": target_field.record.get("policy"), "input_sha256": {str(p): h for p, h in target_inputs.items()},
            "current_atlas_uv_used_for_signal": False, "candidate_bake_read": False}
        record["source"] = {"path": str(snapshot.metadata["reference_glb"]),
                            "sha256": target_field.record["source_sha256"], "uv": "original_source_face_corner_uv"}
        if require_snapshot(request).state_hash != snapshot.state_hash or any(
            file_hash(path) != digest for path, digest in target_inputs.items()
        ):
            raise ValueError("FROZEN_TARGET_VIEW_INPUT_CHANGED")
    write_json(output / "views.json", record)
    return {"images": images, "face_rasters": picks, "state_hash": snapshot.state_hash,
            "picked_faces": picked_faces,
            "report": str((output / "views.json").resolve()), "quality_evidence": False,
            "coordinate_convention": record["coordinate_convention"]}
