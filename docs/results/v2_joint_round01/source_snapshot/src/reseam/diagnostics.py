"""Offline artist proxies and validated optimized-asset packaging."""

import json
import math
import time
from pathlib import Path
from typing import Any

import numpy as np
import trimesh
from PIL import Image
from shapely import Polygon, union_all
from trimesh.visual.material import PBRMaterial
from trimesh.visual.texture import TextureVisuals

from .mesh import chart_labels, import_uv_arrays, load_state, sha256
from .pipeline import write_json
from .types import MeshState
from .validation import jacobians, validate


def packing_diagnostics(native: MeshState, packed: MeshState, resolution: int = 1024) -> dict[str, Any]:
    if not np.array_equal(native.mesh.faces, packed.mesh.faces) or not np.array_equal(
        native.mesh.vertices, packed.mesh.vertices
    ):
        raise ValueError("Packing diagnostics require identical source geometry")
    corners = packed.uv[packed.uv_faces]
    a, b = corners[:, 1] - corners[:, 0], corners[:, 2] - corners[:, 0]
    uv_areas = np.abs(a[:, 0] * b[:, 1] - a[:, 1] * b[:, 0]) / 2
    labels = chart_labels(packed)
    source_areas = np.bincount(labels, weights=packed.mesh.areas)
    chart_density = resolution * np.sqrt(np.bincount(labels, weights=uv_areas) / source_areas)
    weights = source_areas / source_areas.sum()
    mean = float(np.dot(weights, chart_density))
    face_density = resolution * np.sqrt(uv_areas / packed.mesh.areas)
    face_weights = packed.mesh.areas / packed.mesh.areas.sum()
    face_mean = float(np.dot(face_weights, face_density))
    native_singular = np.linalg.svd(jacobians(native), compute_uv=False)
    packed_singular = np.linalg.svd(jacobians(packed), compute_uv=False)
    before = native_singular[:, 0] / native_singular[:, 1]
    after = packed_singular[:, 0] / packed_singular[:, 1]
    return {
        "density_unit": "pixels per source mesh length unit; source unit is not certified as metres",
        "resolution": resolution,
        "chart_density_min": float(chart_density.min()),
        "chart_density_max": float(chart_density.max()),
        "chart_density_area_weighted_mean": mean,
        "chart_density_area_weighted_cv": float(np.sqrt(np.dot(weights, (chart_density - mean) ** 2)) / mean),
        "face_density_area_weighted_cv": float(
            np.sqrt(np.dot(face_weights, (face_density - face_mean) ** 2)) / face_mean
        ),
        "uv_triangle_area_fraction": float(uv_areas.sum()),
        "face_anisotropy_max_relative_change_after_packing": float(np.max(np.abs(after / before - 1))),
        "stretch_definition": "Per-face singular-value ratio; invariant to uniform scale and rotation, distinct from native SD",
        "scope": "Offline diagnostics only; does not replace packed-gap validation or human acceptance",
    }


def artist_metrics(state: MeshState, confirmed_regions: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    checked = validate(state, tau=None)
    labels = chart_labels(state)
    flagged: set[int] = set()
    for error in checked["errors"]:
        if error.startswith("CHART_INTERSECTION:"):
            flagged.update(map(int, error.split(":")[1:]))
        elif error == "FLIPPED_OR_DEGENERATE":
            flagged.update(map(int, np.flatnonzero(np.linalg.det(jacobians(state)) <= 1e-12)))
        elif error.startswith("NON_PLANAR_OR_NONMANIFOLD_CHART:"):
            flagged.update(map(int, np.flatnonzero(labels == int(error.rsplit(":", 1)[1]))))
    fraction = float(state.mesh.areas[sorted(flagged)].sum() / state.mesh.areas.sum())
    result: dict[str, Any] = {
        "definition_version": "artist-proxies-v1",
        "mapping_valid": checked["valid"],
        "flagged_face_area_fraction": fraction if flagged or checked["valid"] else None,
        "definitions": {
            "thin_chart": "minimum rotated UV rectangle short/long side < 0.05",
            "compactness": "UV perimeter^2 / (4*pi*UV area), including hole boundaries",
            "turn_ratio": "mean loop absolute turning / (2*pi)",
            "weighting": "original 3D chart area; proxies, not artist scores",
        },
        "semantic_fragmentation": None,
        "semantic_status": "NO_CONFIRMED_REGIONS",
    }
    if not checked["valid"]:
        result["status"] = "INVALID_MAPPING_DIAGNOSTICS_ONLY"
        return result
    charts = []
    for chart in np.unique(labels):
        face_ids = np.flatnonzero(labels == chart)
        shape = union_all([Polygon(state.uv[state.uv_faces[fi]]) for fi in face_ids])
        if not isinstance(shape, Polygon):
            raise TypeError("Chart did not form one polygon")
        rectangle_shape = shape.minimum_rotated_rectangle
        if not isinstance(rectangle_shape, Polygon):
            raise TypeError("Chart bounding rectangle is degenerate")
        rectangle = np.asarray(rectangle_shape.exterior.coords)
        lengths = np.linalg.norm(np.diff(rectangle, axis=0), axis=1)
        aspect = float(lengths.min() / lengths.max())
        ratios = []
        for ring in [shape.exterior, *shape.interiors]:
            points = np.asarray(ring.coords)[:-1]
            first = np.roll(points, -1, axis=0) - points
            second = np.roll(first, -1, axis=0)
            cross = first[:, 0] * second[:, 1] - first[:, 1] * second[:, 0]
            ratios.append(
                float(np.abs(np.arctan2(cross, np.sum(first * second, axis=1))).sum() / (2 * math.pi))
            )
        charts.append(
            {
                "chart_id": int(chart),
                "source_area_fraction": float(state.mesh.areas[face_ids].sum() / state.mesh.areas.sum()),
                "aspect_ratio": aspect,
                "thin": aspect < 0.05,
                "compactness": float(shape.length**2 / (4 * math.pi * shape.area)),
                "boundary_turn_ratio": float(np.mean(ratios)),
            }
        )
    result.update(
        status="COMPUTED",
        charts=charts,
        thin_chart_area_fraction=sum(c["source_area_fraction"] for c in charts if c["thin"]),
        compactness_area_weighted=sum(c["source_area_fraction"] * c["compactness"] for c in charts),
        turn_ratio_area_weighted=sum(c["source_area_fraction"] * c["boundary_turn_ratio"] for c in charts),
    )
    if confirmed_regions:
        result["semantic_fragmentation"] = [
            {
                "label": region.get("label", f"region_{i}"),
                "chart_count": len(np.unique(labels[region["face_ids"]])),
            }
            for i, region in enumerate(confirmed_regions)
        ]
        result["semantic_status"] = "CONFIRMED_REGION_COUNTS"
    return result


def package_optimized(run_folder: Path) -> dict[str, Any]:
    """Replace only Base Color, retaining the baked geometry, normals and UV."""
    start = time.monotonic()
    downstream = run_folder / "downstream"
    texture_path = downstream / "differentiable/BaseColor.png"
    if not texture_path.exists() or not (downstream / "blender/asset.glb").exists():
        return {"status": "NOT_AVAILABLE"}
    output = downstream / "differentiable/asset.glb"
    evidence_path = downstream / "differentiable/packaging.json"
    if output.exists() and evidence_path.exists():
        cached = json.loads(evidence_path.read_text(encoding="utf-8"))
        if (
            cached.get("output_sha256") == sha256(output)
            and cached.get("texture_sha256") == sha256(texture_path)
            and cached.get("source_asset_sha256") == sha256(downstream / "blender/asset.glb")
        ):
            return cached
    mesh = trimesh.load(downstream / "blender/asset.glb", force="mesh", process=False)
    if not isinstance(mesh, trimesh.Trimesh) or not isinstance(mesh.visual, TextureVisuals):
        raise TypeError("Baked asset is not a textured mesh")
    if not isinstance(mesh.visual.material, PBRMaterial):
        raise TypeError("Baked asset has no PBR material")
    original_normal = mesh.visual.material.normalTexture
    if original_normal is None:
        raise ValueError("Baked asset has no normal texture")
    original_normal_pixels = np.asarray(original_normal.convert("RGB"))
    mesh.visual.material.baseColorTexture = Image.open(texture_path).convert("RGBA")
    normals = mesh.vertex_normals.copy()
    if not output.exists():
        mesh.export(output)
    loaded = trimesh.load(output, force="mesh", process=False)
    if not isinstance(loaded, trimesh.Trimesh) or not isinstance(loaded.visual, TextureVisuals):
        raise TypeError("Optimized GLB lost texture data")
    state = load_state(downstream / "pack/state.json")
    restored = import_uv_arrays(
        state.mesh,
        np.asarray(loaded.vertices),
        np.asarray(loaded.faces),
        np.asarray(loaded.visual.uv),
        np.asarray(loaded.faces),
    )
    if restored.seams != state.seams or not np.allclose(
        restored.uv[restored.uv_faces], state.uv[state.uv_faces], atol=1e-6, rtol=0
    ):
        raise ValueError("Optimized export changed UV")
    if not np.allclose(loaded.vertex_normals, normals, atol=1e-5, rtol=0):
        raise ValueError("Optimized export changed normals")
    if not isinstance(loaded.visual.material, PBRMaterial) or loaded.visual.material.normalTexture is None:
        raise ValueError("Optimized export lost normal texture")
    normal_image = loaded.visual.material.normalTexture
    if not np.array_equal(np.asarray(normal_image.convert("RGB")), original_normal_pixels):
        raise ValueError("Optimized export changed normal texture pixels")
    base_image = loaded.visual.material.baseColorTexture
    if base_image is None or not np.array_equal(
        np.asarray(base_image.convert("RGB")), np.asarray(Image.open(texture_path).convert("RGB"))
    ):
        raise ValueError("Optimized asset does not contain the requested texture")
    result = {
        "status": "VERIFIED",
        "geometry_uv_normals_preserved": True,
        "normal_texture_preserved": True,
        "normal_texture_pixels_preserved": True,
        "seconds": time.monotonic() - start,
        "output_sha256": sha256(output),
        "texture_sha256": sha256(texture_path),
        "source_asset_sha256": sha256(downstream / "blender/asset.glb"),
    }
    write_json(evidence_path, result)
    return result
