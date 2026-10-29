"""Independent orthographic z-buffer views for the frozen CPU material contract.

Reference radiance is projected high-surface material on the same visible low
triangles.  This isolates UV/material reconstruction from different high/low
silhouettes; it is not an engine, shadow, or artist acceptance certificate.
"""

import math
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image

from .budget_material import (
    MaterialAtlas,
    MaterialReference,
    MaterialSamples,
    sample_material,
    shade_material,
)
from .budget_texture import BoolArray, encode_rgba8
from .types import FloatArray, IntArray, Mesh, MeshState

_LIGHTS = (np.asarray([0.6, -0.2, 1.0]), np.asarray([-0.5, 0.7, 0.6]))


@dataclass(frozen=True)
class RenderView:
    azimuth_degrees: float
    elevation_degrees: float
    face_ids: IntArray
    barycentric: FloatArray
    mask: BoolArray
    image_face_ids: IntArray
    zbuffer: FloatArray
    pixel_indices: IntArray
    view_direction: FloatArray
    reference: MaterialSamples
    reference_radiance: tuple[FloatArray, ...]


@dataclass(frozen=True)
class RenderReference:
    mesh: Mesh
    size: int
    views: tuple[RenderView, ...]
    lights: tuple[FloatArray, ...]
    metadata: dict[str, object]


def _check_deadline(deadline: float | None) -> None:
    if deadline is not None:
        if not math.isfinite(deadline):
            raise ValueError("Finite absolute render deadline required")
        if time.monotonic() >= deadline:
            raise TimeoutError("RENDER_BUDGET_EXHAUSTED")


def _readonly(array: FloatArray | IntArray | BoolArray) -> None:
    array.setflags(write=False)


def _rasterize(
    mesh: Mesh, size: int, azimuth: float, elevation: float, deadline: float | None,
) -> tuple[IntArray, FloatArray, FloatArray, FloatArray]:
    azimuth_r, elevation_r = np.radians([azimuth, elevation])
    direction = np.asarray([np.sin(azimuth_r) * np.cos(elevation_r), np.sin(elevation_r),
                            np.cos(azimuth_r) * np.cos(elevation_r)])
    right = np.asarray([np.cos(azimuth_r), 0, -np.sin(azimuth_r)])
    up = np.cross(direction, right)
    projection = np.column_stack((mesh.vertices @ right, mesh.vertices @ up))
    center = (projection.min(axis=0) + projection.max(axis=0)) / 2
    extent = max(float(np.ptp(projection, axis=0).max()), 1e-12)
    screen = (projection - center) / extent * (size - 4)
    screen[:, 1] *= -1
    screen += size / 2
    depth = mesh.vertices @ direction
    face_image = np.full((size, size), -1, dtype=np.int64)
    bary_image = np.zeros((size, size, 3), dtype=np.float64)
    zbuffer = np.full((size, size), -np.inf, dtype=np.float64)
    depth_tolerance = max(float(np.ptp(depth)), 1.0) * 1e-12
    for face_id, face in enumerate(mesh.faces):
        _check_deadline(deadline)
        triangle = screen[face]
        matrix = np.column_stack((triangle[1] - triangle[0], triangle[2] - triangle[0]))
        if abs(float(np.linalg.det(matrix))) < 1e-14:
            continue
        low = np.maximum(0, np.floor(triangle.min(axis=0) - 0.5).astype(np.int64))
        high = np.minimum(size - 1, np.ceil(triangle.max(axis=0) - 0.5).astype(np.int64))
        if np.any(low > high):
            continue
        xx, yy = np.meshgrid(np.arange(low[0], high[0] + 1), np.arange(low[1], high[1] + 1))
        x, y = xx.reshape(-1), yy.reshape(-1)
        points = np.column_stack((x + 0.5, y + 0.5))
        tail = np.linalg.solve(matrix, (points - triangle[0]).T).T
        bary = np.column_stack((1 - tail.sum(axis=1), tail))
        inside = np.all(bary >= -1e-10, axis=1)
        interpolated = bary @ depth[face]
        visible = inside & (interpolated > zbuffer[y, x] + depth_tolerance)
        x, y = x[visible], y[visible]
        face_image[y, x] = face_id
        bary_image[y, x] = bary[visible]
        zbuffer[y, x] = interpolated[visible]
    return face_image, bary_image, zbuffer, direction


def prepare_render_reference(
    mesh: Mesh,
    reference: MaterialReference,
    *,
    size: int = 48,
    azimuths: tuple[float, ...] = (17, 107, 197, 287),
    elevation_degrees: float = 23,
    max_projection_fraction: float = 0.02,
    deadline: float | None = None,
) -> RenderReference:
    """Cache actual low-mesh visibility and high-material samples per fixed view."""
    if type(size) is not int or not 8 <= size <= 256:
        raise ValueError("Render size must be an integer in [8, 256]")
    if (not azimuths or not all(math.isfinite(a) for a in azimuths)
            or not math.isfinite(elevation_degrees) or not -89 < elevation_degrees < 89
            or not math.isfinite(max_projection_fraction) or max_projection_fraction <= 0):
        raise ValueError("Invalid fixed render protocol")
    _check_deadline(deadline)
    views: list[RenderView] = []
    lights = tuple(light.copy() for light in _LIGHTS)
    for azimuth in azimuths:
        image_faces, image_bary, zbuffer, direction = _rasterize(
            mesh, size, float(azimuth), elevation_degrees, deadline,
        )
        mask = image_faces >= 0
        pixels = np.asarray(np.flatnonzero(mask), dtype=np.int64)
        face_ids = image_faces[mask].copy()
        bary = image_bary[mask].copy()
        points = np.einsum("nk,nkj->nj", bary, mesh.vertices[mesh.faces[face_ids]])
        if len(points):
            samples = reference.sample(points, max_projection_fraction * reference.diagonal, deadline=deadline)
        else:
            samples = MaterialSamples(np.empty((0, 4)), np.empty((0, 3)), np.empty((0, 3)),
                                      np.empty(0, dtype=bool), np.empty(0), np.empty(0, dtype=np.int64),
                                      np.empty((0, 3)))
        radiance = tuple(shade_material(samples, light, direction) for light in lights)
        for array in (image_faces, zbuffer, mask, pixels, face_ids, bary, direction,
                      samples.base_color, samples.normal_world, samples.orm, samples.valid,
                      samples.distances, samples.target_faces, samples.barycentric, *radiance):
            _readonly(array)
        views.append(RenderView(float(azimuth), elevation_degrees, face_ids, bary, mask,
                                image_faces, zbuffer, pixels, direction, samples, radiance))
    _check_deadline(deadline)
    for light in lights:
        _readonly(light)
    metadata: dict[str, object] = {
        "contract": "CPU_orthographic_low_triangle_zbuffer_Lambert_GGX",
        "visibility": "two_sided_nearest_depth_original_low_triangles",
        "reference": "visible_low_face_points_projected_to_high_material_mip0",
        "geometry_scope": "same_fixed_low_geometry; high_low_silhouette_error_not_scored",
        "shader": "budget_material.shade_material; Lambert+GGX; no IBL/shadow/tonemap",
        "sampling": "pixel_center_single_sample; forced_integer_mip; CPU_bilinear",
        "reference_filter": "high_surface_point_reference_not_matched_pixel_or_mip_footprint",
        "size": size, "azimuth_degrees": list(azimuths), "elevation_degrees": elevation_degrees,
        "lights_world": [light.tolist() for light in lights],
        "max_projection_fraction": max_projection_fraction,
        "gpu_validated": False, "artist_reviewed": False,
    }
    return RenderReference(mesh, size, tuple(views), lights, metadata)


def _save_radiance(
    path: Path, values: FloatArray, view: RenderView, size: int,
) -> None:
    image = np.zeros((size, size, 4), dtype=np.float64)
    image[view.mask, :3] = values
    image[view.mask, 3] = 1
    Image.fromarray(encode_rgba8(image)).save(path)


def evaluate_render(
    state: MeshState,
    atlas: MaterialAtlas,
    prepared: RenderReference,
    output: Path | None = None,
    *,
    mean_limit: float = 0.04,
    p95_limit: float = 0.1,
    max_limit: float = 0.3,
    mip_levels: tuple[int, ...] = (0, 1),
    deadline: float | None = None,
) -> dict[str, object]:
    """Compare decoded atlases in cached independent views; preserve all failures."""
    _check_deadline(deadline)
    if not np.array_equal(state.mesh.vertices, prepared.mesh.vertices) or not np.array_equal(
        state.mesh.faces, prepared.mesh.faces
    ):
        raise ValueError("RENDER_GEOMETRY_CACHE_MISMATCH")
    if (not all(math.isfinite(v) and v > 0 for v in (mean_limit, p95_limit, max_limit))
            or not mip_levels or any(type(m) is not int or m < 0 or m >= len(atlas.base_color)
                                     for m in mip_levels)):
        raise ValueError("Invalid render thresholds or requested integer mip")
    if output is not None:
        output.mkdir(parents=True, exist_ok=False)
    records: list[dict[str, object]] = []
    all_errors: list[FloatArray] = []
    valid = True
    reference_count, reference_valid = 0, 0
    projection_distances: list[FloatArray] = []
    for view_id, view in enumerate(prepared.views):
        _check_deadline(deadline)
        reference_count += len(view.face_ids)
        reference_valid += int(view.reference.valid.sum())
        projection_distances.append(view.reference.distances)
        if len(view.face_ids) == 0:
            valid = False
            records.append({"view": view_id, "azimuth_degrees": view.azimuth_degrees,
                            "status": "NO_VISIBLE_PIXELS", "passed": False, "pixels": 0})
            continue
        for mip in mip_levels:
            _check_deadline(deadline)
            actual = sample_material(state, atlas, view.face_ids, view.barycentric, mip=mip)
            sample_valid = actual.valid & view.reference.valid
            for light_id, light in enumerate(prepared.lights):
                _check_deadline(deadline)
                rendered = shade_material(actual, light, view.view_direction)
                target = view.reference_radiance[light_id]
                error = np.linalg.norm(rendered - target, axis=1)
                finite = bool(np.isfinite(error).all())
                mean = float(error.mean()) if finite else None
                p95 = float(np.quantile(error, 0.95)) if finite else None
                maximum = float(error.max()) if finite else None
                passed = bool(finite and sample_valid.all() and mean is not None and mean <= mean_limit
                              and p95 is not None and p95 <= p95_limit
                              and maximum is not None and maximum <= max_limit)
                valid &= passed
                records.append({
                    "view": view_id, "azimuth_degrees": view.azimuth_degrees,
                    "elevation_degrees": view.elevation_degrees, "light": light_id, "mip": mip,
                    "pixels": len(error), "mean": mean, "p95": p95, "max": maximum,
                    "reference_valid_fraction": float(view.reference.valid.mean()),
                    "actual_valid_fraction": float(actual.valid.mean()), "passed": passed,
                })
                all_errors.append(error)
                if output is not None:
                    prefix = f"view{view_id}_light{light_id}_mip{mip}"
                    _save_radiance(output / f"{prefix}_actual.png", rendered, view, prepared.size)
                    _save_radiance(output / f"{prefix}_reference.png", target, view, prepared.size)
                    # Display clips at max_limit; metrics above use unclipped linear radiance.
                    normalized = np.clip(error / max_limit, 0, 1)
                    heat = np.column_stack((normalized, normalized * 0.15, np.zeros(len(error))))
                    _save_radiance(output / f"{prefix}_error.png", heat, view, prepared.size)
    _check_deadline(deadline)
    errors = np.concatenate(all_errors) if all_errors else np.asarray([], dtype=float)
    distances = np.concatenate(projection_distances) if projection_distances else np.asarray([], dtype=float)
    finite = bool(len(errors) and np.isfinite(errors).all())
    coverage = reference_valid / reference_count if reference_count else 0.0
    valid &= finite and coverage == 1.0
    return {
        "valid": valid, "status": "CPU_RENDER_VALIDATED" if valid else "CPU_RENDER_REJECTED",
        "reference_coverage": coverage, "reference_pixels": reference_count,
        "views": len(prepared.views), "lights": len(prepared.lights), "mip_levels": list(mip_levels),
        "mean": float(errors.mean()) if finite else None,
        "p95": float(np.quantile(errors, 0.95)) if finite else None,
        "max": float(errors.max()) if finite else None,
        "projection_distance_mean": float(distances.mean()) if len(distances) else None,
        "projection_distance_max": float(distances.max()) if len(distances) else None,
        "thresholds": {"mean": mean_limit, "p95": p95_limit, "max": max_limit},
        "per_view_light_mip": records, "protocol": prepared.metadata,
        "geometry_scope": prepared.metadata["geometry_scope"],
        "gpu_validated": False, "artist_reviewed": False,
    }
