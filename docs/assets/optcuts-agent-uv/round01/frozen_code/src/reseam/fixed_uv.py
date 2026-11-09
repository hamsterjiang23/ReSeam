"""Rigid island packing: backend poses are proposals, geometry is reconstructed and checked."""

import time
from dataclasses import dataclass
from typing import Any

import numpy as np
from shapely import Polygon, union_all
from shapely.affinity import affine_transform
from shapely.geometry.base import BaseGeometry

from .mesh import chart_labels
from .types import FloatArray, IntArray, MeshState
from .validation import jacobians, validate


@dataclass(frozen=True)
class PackingProfile:
    resolution: int = 1024
    gap_pixels: float = 16.0
    border_pixels: float = 8.0
    dilation_pixels: float = 8.0

    def __post_init__(self) -> None:
        values = (self.gap_pixels, self.border_pixels, self.dilation_pixels)
        if self.resolution <= 0 or not np.isfinite(values).all() or min(values) < 0:
            raise ValueError("Invalid sampling profile")
        if self.border_pixels * 2 >= self.resolution:
            raise ValueError("No interior atlas area")


def face_areas(state: MeshState) -> FloatArray:
    t = state.uv[state.uv_faces]
    a, b = t[:, 1] - t[:, 0], t[:, 2] - t[:, 0]
    return (a[:, 0] * b[:, 1] - a[:, 1] * b[:, 0]) * 0.5


@dataclass
class FixedAtlas:
    source: MeshState
    canonical: FloatArray
    groups: list[IntArray]
    shapes: list[BaseGeometry]
    scale: float
    centers: FloatArray

    @classmethod
    def from_state(cls, state: MeshState) -> "FixedAtlas":
        if not validate(state, tau=None)["valid"]:
            raise ValueError("SOURCE_UV_INVALID")
        scale = float(np.sqrt(face_areas(state).sum()))
        labels = chart_labels(state)
        groups = [np.unique(state.uv_faces[labels == label]) for label in np.unique(labels)]
        if sum(len(ids) for ids in groups) != len(state.uv):
            raise ValueError("Charts share UV vertex identities or unused UV vertices exist")
        centers = np.asarray([state.uv[ids].mean(axis=0) for ids in groups])
        canonical = state.uv.copy()
        for ids, center in zip(groups, centers, strict=True):
            canonical[ids] = (state.uv[ids] - center) / scale
        shapes = [
            union_all([Polygon(canonical[face]) for face in state.uv_faces[labels == label]])
            for label in np.unique(labels)
        ]
        return cls(state.copy(), canonical, groups, shapes, scale, centers)

    def reconstruct(self, angles: FloatArray, centers: FloatArray, scale: float) -> MeshState:
        state = self.source.copy()
        for ids, angle, center in zip(self.groups, angles, centers, strict=True):
            c, s = np.cos(angle), np.sin(angle)
            rotation = np.asarray([[c, -s], [s, c]])
            state.uv[ids] = scale * (self.canonical[ids] @ rotation.T) + center
        return state

    def fit_poses(self, uv: FloatArray) -> tuple[FloatArray, FloatArray, list[float]]:
        if uv.shape != self.canonical.shape or not np.isfinite(uv).all():
            raise ValueError("Invalid backend coordinates")
        angles, centers, residuals = [], [], []
        for ids in self.groups:
            x = self.canonical[ids]
            center = uv[ids].mean(axis=0)
            y = uv[ids] - center
            dot = float(np.sum(x * y))
            cross = float(np.sum(x[:, 0] * y[:, 1] - x[:, 1] * y[:, 0]))
            angle = float(np.arctan2(cross, dot))
            local_scale = float(np.hypot(dot, cross) / np.sum(x * x))
            c, s = np.cos(angle), np.sin(angle)
            fitted = local_scale * (x @ np.asarray([[c, s], [-s, c]]))
            residuals.append(float(np.max(np.linalg.norm(fitted - y, axis=1))))
            angles.append(angle)
            centers.append(center)
        return np.asarray(angles), np.asarray(centers), residuals

    def posed_shapes(self, angles: FloatArray, centers: FloatArray, scale: float) -> list[BaseGeometry]:
        result = []
        for shape, angle, center in zip(self.shapes, angles, centers, strict=True):
            c, s = scale * np.cos(angle), scale * np.sin(angle)
            result.append(affine_transform(shape, [c, -s, s, c, center[0], center[1]]))
        return result


def spatial_metrics(shapes: list[BaseGeometry], profile: PackingProfile) -> dict[str, Any]:
    gap = float("inf")
    border = float("inf")
    overlap = 0.0
    for i, shape in enumerate(shapes):
        left, bottom, right, top = shape.bounds
        border = min(border, left, bottom, 1 - right, 1 - top)
        for other in shapes[i + 1 :]:
            gap = min(gap, float(shape.distance(other)))
            if shape.intersects(other):
                overlap += float(shape.intersection(other).area)
    tolerance = 1e-5  # pixels; never borrow the V1 display tolerance of 0.05 px.
    return {
        "min_gap_pixels": gap * profile.resolution if np.isfinite(gap) else None,
        "min_border_pixels": border * profile.resolution,
        "overlap_area": overlap,
        "spatial_valid": bool(
            overlap <= 1e-12
            and gap * profile.resolution >= profile.gap_pixels - tolerance
            and border * profile.resolution >= profile.border_pixels - tolerance
        ),
    }


def shape_distortion(state: MeshState) -> float:
    singular = np.linalg.svd(jacobians(state), compute_uv=False)
    weights = state.mesh.areas / state.mesh.areas.sum()
    p = float(weights @ np.sum(singular**2, axis=1))
    q = float(weights @ np.sum(singular**-2, axis=1))
    return float(0.5 * np.sqrt(p * q) - 1)


def certify(atlas: FixedAtlas, state: MeshState, profile: PackingProfile) -> dict[str, Any]:
    source = atlas.source
    topology_same = (
        state.seams == source.seams
        and np.array_equal(state.mesh.faces, source.mesh.faces)
        and np.array_equal(state.mesh.vertices, source.mesh.vertices)
        and np.array_equal(state.uv_faces, source.uv_faces)
        and state.uv.shape == source.uv.shape
    )
    if not topology_same:
        return {"valid": False, "errors": ["FIXED_TOPOLOGY_CHANGED"]}
    basic = validate(state, tau=None)
    if not basic["valid"]:
        return {"valid": False, "errors": basic["errors"]}
    areas = face_areas(state)
    scale = float(np.sqrt(areas.sum()))
    angles, centers, _ = atlas.fit_poses(state.uv)
    reconstructed = atlas.reconstruct(angles, centers, scale)
    rigid_error = float(np.max(np.abs(reconstructed.uv - state.uv)))
    labels = chart_labels(state)
    shapes = [
        union_all([Polygon(state.uv[face]) for face in state.uv_faces[labels == label]])
        for label in np.unique(labels)
    ]
    spatial = spatial_metrics(shapes, profile)
    union_area = float(union_all(shapes).area)
    rho = profile.resolution * np.sqrt(areas / state.mesh.areas)
    old_rho = profile.resolution * np.sqrt(face_areas(source) / source.mesh.areas)
    ratios = rho / old_rho
    density_error = float(np.max(np.abs(ratios - scale / atlas.scale)))
    errors = []
    if not spatial["spatial_valid"]:
        errors.append("GAP_BORDER_OR_OVERLAP")
    if rigid_error > 1e-8 or density_error > 1e-7:
        errors.append("ISLAND_SHAPE_OR_RELATIVE_DENSITY_CHANGED")
    if scale < atlas.scale - 1e-9:
        errors.append("SOURCE_DENSITY_DECREASED")
    if abs(union_area - scale**2) > 1e-9:
        errors.append("UNION_AREA_MISMATCH")
    return {
        "valid": not errors,
        "errors": errors,
        **spatial,
        "occupancy": union_area,
        "summed_triangle_area": float(areas.sum()),
        "scale": scale,
        "density_gain": scale / atlas.scale,
        "minimum_texels_per_unit": float(rho.min()),
        "relative_density_error": density_error,
        "rigid_error": rigid_error,
        "scale_invariant_sd": shape_distortion(state),
        "islands": len(shapes),
    }


def search_scale(
    atlas: FixedAtlas,
    angles: FloatArray,
    centers: FloatArray,
    profile: PackingProfile,
    floor: float,
    deadline: float,
) -> float | None:
    """Finite scale samples plus local refinement; no monotonicity/global-optimum claim."""
    border = profile.border_pixels / profile.resolution
    upper = 1 - 2 * border  # area bound, since canonical triangle area is one
    for ids, angle, center in zip(atlas.groups, angles, centers, strict=True):
        c, s = np.cos(angle), np.sin(angle)
        points = atlas.canonical[ids] @ np.asarray([[c, s], [-s, c]])
        if np.any(center < border) or np.any(center > 1 - border):
            return None
        for axis in (0, 1):
            lo, hi = points[:, axis].min(), points[:, axis].max()
            if lo < 0:
                upper = min(upper, float((center[axis] - border) / -lo))
            if hi > 0:
                upper = min(upper, float((1 - border - center[axis]) / hi))
    if upper < floor:
        return None
    failed = upper
    for scale in np.linspace(upper, floor, 32):
        if time.monotonic() >= deadline:
            return None
        if spatial_metrics(atlas.posed_shapes(angles, centers, float(scale)), profile)["spatial_valid"]:
            best = float(scale)
            for _ in range(12):
                if time.monotonic() >= deadline:
                    break
                mid = (best + failed) / 2
                if spatial_metrics(atlas.posed_shapes(angles, centers, mid), profile)["spatial_valid"]:
                    best = mid
                else:
                    failed = mid
            return best
        failed = float(scale)
    return None


def raster_mask(state: MeshState, resolution: int) -> np.ndarray:
    """Pixel-centre sampling of actual triangles, without dilation or color thresholds."""
    mask = np.zeros((resolution, resolution), dtype=bool)
    for tri in state.uv[state.uv_faces] * resolution:
        lo = np.maximum(0, np.ceil(tri.min(axis=0) - 0.5).astype(int))
        hi = np.minimum(resolution - 1, np.floor(tri.max(axis=0) - 0.5).astype(int))
        if np.any(lo > hi):
            continue
        x, y = np.meshgrid(np.arange(lo[0], hi[0] + 1) + 0.5, np.arange(lo[1], hi[1] + 1) + 0.5)
        inside = np.ones(x.shape, dtype=bool)
        for i in range(3):
            a, b = tri[i], tri[(i + 1) % 3]
            inside &= (b[0] - a[0]) * (y - a[1]) - (b[1] - a[1]) * (x - a[0]) >= -1e-10
        mask[lo[1] : hi[1] + 1, lo[0] : hi[0] + 1] |= inside
    return mask[::-1]
