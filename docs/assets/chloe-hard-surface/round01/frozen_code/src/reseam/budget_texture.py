"""Bounded reference sampling and an explicit RGBA8 Base Color diagnostic codec.

This module never labels Base Color-only evaluation as complete material validation.
The prototype uses exact closest points, a fixed projection-distance gate, linear
RGB composition, PNG quantization and an explicitly generated complete mip chain.
"""

import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import trimesh
from numpy.typing import NDArray
from PIL import Image
from scipy.ndimage import distance_transform_edt
from scipy.spatial import KDTree
from trimesh.triangles import closest_point
from trimesh.visual.material import PBRMaterial

from .mesh import chart_labels
from .surface_match import barycentric_coordinates
from .types import FloatArray, IntArray, MeshState

Uint8Array = NDArray[np.uint8]
BoolArray = NDArray[np.bool_]


def _check_deadline(deadline: float | None) -> None:
    if deadline is not None and time.monotonic() >= deadline:
        raise TimeoutError("TEXTURE_DIAGNOSTIC_DEADLINE")


def mip_dimensions(width: int, height: int) -> tuple[tuple[int, int], ...]:
    """Return a complete floor-halved chain, including the final 1 by 1 level."""
    if type(width) is not int or type(height) is not int or min(width, height) < 1:
        raise ValueError("Positive integer texture dimensions required")
    result = [(width, height)]
    while max(width, height) > 1:
        width, height = max(1, width // 2), max(1, height // 2)
        result.append((width, height))
    return tuple(result)


def texture_payload_bytes(
    width: int,
    height: int,
    block_width: int = 1,
    block_height: int = 1,
    bytes_per_block: int = 4,
    include_mips: bool = True,
) -> int:
    """Count fixed-block payload only, excluding containers and GPU alignment."""
    if any(type(x) is not int or x <= 0 for x in (block_width, block_height, bytes_per_block)):
        raise ValueError("Positive integer block dimensions and byte size required")
    sizes = mip_dimensions(width, height)
    if not include_mips:
        sizes = sizes[:1]
    return sum(
        ((w + block_width - 1) // block_width) * ((h + block_height - 1) // block_height) * bytes_per_block
        for w, h in sizes
    )


def srgb_to_linear(value: FloatArray) -> FloatArray:
    value = np.asarray(value, dtype=np.float64)
    return np.asarray(np.where(value <= 0.04045, value / 12.92, ((value + 0.055) / 1.055) ** 2.4))


def linear_to_srgb(value: FloatArray) -> FloatArray:
    value = np.asarray(value, dtype=np.float64)
    return np.asarray(
        np.where(value <= 0.0031308, value * 12.92, 1.055 * np.maximum(value, 0) ** (1 / 2.4) - 0.055)
    )


def encode_rgba8(linear: FloatArray) -> Uint8Array:
    """RGB is sRGB encoded; alpha remains linear."""
    value = np.asarray(linear, dtype=np.float64)
    if value.shape[-1] != 4 or not np.isfinite(value).all():
        raise ValueError("Finite RGBA values required")
    value = np.clip(value.copy(), 0, 1)
    value[..., :3] = linear_to_srgb(value[..., :3])
    return np.asarray(np.rint(value * 255), dtype=np.uint8)


def decode_rgba8(encoded: Uint8Array) -> FloatArray:
    value = np.asarray(encoded, dtype=np.float64) / 255
    if value.shape[-1] != 4:
        raise ValueError("RGBA8 image required")
    value[..., :3] = srgb_to_linear(value[..., :3])
    return value


def sample_rgba8(image: Uint8Array, uv: FloatArray, *, repeat: bool = False) -> FloatArray:
    """Bilinear, texel-center sampling; normalized v=0 is the image bottom."""
    if image.ndim != 3 or image.shape[2] != 4 or not np.isfinite(uv).all():
        raise ValueError("RGBA image and finite UV required")
    uv = np.asarray(uv, dtype=np.float64)
    if uv.ndim != 2 or uv.shape[1] != 2:
        raise ValueError("UV must have shape (n, 2)")
    height, width = image.shape[:2]
    px = uv[:, 0] * width - 0.5
    py = (1 - uv[:, 1]) * height - 0.5
    x, y = np.floor(px).astype(np.int64), np.floor(py).astype(np.int64)
    alpha, beta = (px - x)[:, None], (py - y)[:, None]
    if repeat:
        x0, x1, y0, y1 = x % width, (x + 1) % width, y % height, (y + 1) % height
    else:
        x0, x1 = np.clip(x, 0, width - 1), np.clip(x + 1, 0, width - 1)
        y0, y1 = np.clip(y, 0, height - 1), np.clip(y + 1, 0, height - 1)
    a, b = decode_rgba8(image[y0, x0]), decode_rgba8(image[y0, x1])
    c, d = decode_rgba8(image[y1, x0]), decode_rgba8(image[y1, x1])
    return (1 - alpha) * (1 - beta) * a + alpha * (1 - beta) * b + (1 - alpha) * beta * c + alpha * beta * d


def shared_color_error(values: FloatArray, weights: FloatArray) -> tuple[FloatArray, float]:
    """Fixed-correspondence ideal squared L2 sum; not an encoded quality gate."""
    values, weights = np.asarray(values, dtype=float), np.asarray(weights, dtype=float)
    if (
        values.ndim != 2
        or weights.shape != (len(values),)
        or not len(values)
        or not np.isfinite(values).all()
        or not np.isfinite(weights).all()
        or np.any(weights <= 0)
    ):
        raise ValueError("Finite values and positive sample weights required")
    mean = np.sum(weights[:, None] * values, axis=0) / weights.sum()
    return mean, float(np.sum(weights[:, None] * (values - mean) ** 2))


@dataclass(frozen=True)
class ReferenceSamples:
    rgba_linear: FloatArray
    valid: BoolArray
    distances: FloatArray
    target_faces: IntArray
    barycentric: FloatArray


@dataclass
class ReferenceSurface:
    vertices: FloatArray
    faces: IntArray
    corner_uv: FloatArray
    image: Uint8Array
    base_factor: FloatArray = field(default_factory=lambda: np.ones(4))
    source_has_normal: bool = False
    source_has_orm: bool = False
    original_face_ids: IntArray | None = None
    repeat: bool = True
    triangles: FloatArray = field(init=False, repr=False)
    centers: FloatArray = field(init=False, repr=False)
    tree: KDTree = field(init=False, repr=False)
    maximum_radius: float = field(init=False)
    diagonal: float = field(init=False)

    def __post_init__(self) -> None:
        self.vertices = np.asarray(self.vertices, dtype=np.float64)
        self.faces = np.asarray(self.faces, dtype=np.int64)
        self.corner_uv = np.asarray(self.corner_uv, dtype=np.float64)
        self.base_factor = np.asarray(self.base_factor, dtype=np.float64)
        if (
            self.vertices.ndim != 2
            or self.vertices.shape[1] != 3
            or self.faces.ndim != 2
            or self.faces.shape[1] != 3
            or not len(self.faces)
            or self.faces.min() < 0
            or self.faces.max() >= len(self.vertices)
            or self.corner_uv.shape != (len(self.faces), 3, 2)
            or self.image.ndim != 3
            or self.image.shape[2] != 4
            or min(self.image.shape[:2]) < 1
            or self.image.dtype != np.uint8
            or self.base_factor.shape != (4,)
            or not np.isfinite(self.vertices).all()
            or not np.isfinite(self.corner_uv).all()
            or not np.isfinite(self.base_factor).all()
            or np.any(self.base_factor < 0)
            or np.any(self.base_factor > 1)
        ):
            raise ValueError("Invalid reference geometry, UV, RGBA8 image or factor")
        self.triangles = self.vertices[self.faces]
        cross = np.cross(
            self.triangles[:, 1] - self.triangles[:, 0], self.triangles[:, 2] - self.triangles[:, 0]
        )
        if np.any(np.linalg.norm(cross, axis=1) <= 1e-20):
            raise ValueError("Degenerate reference faces unsupported")
        self.centers = self.triangles.mean(axis=1)
        self.maximum_radius = float(np.linalg.norm(self.triangles - self.centers[:, None], axis=2).max())
        self.tree = KDTree(self.centers)
        self.diagonal = float(np.linalg.norm(np.ptp(self.vertices, axis=0)))
        if self.original_face_ids is None:
            self.original_face_ids = np.arange(len(self.faces), dtype=np.int64)
        elif self.original_face_ids.shape != (len(self.faces),):
            raise ValueError("Reference original-face identity shape mismatch")

    def restrict_bounds(self, bounds: FloatArray, max_distance: float) -> "ReferenceSurface":
        """Safe AABB broad phase for queries inside bounds and distance <= r.

        Points whose true closest distance is above r need not retain their true
        closest triangle, but cannot become a valid <=r projection by this filter.
        """
        bounds = np.asarray(bounds, dtype=np.float64)
        if bounds.shape != (2, 3) or not np.isfinite(bounds).all() or np.any(bounds[1] < bounds[0]):
            raise ValueError("Expected finite ordered (2, 3) bounds")
        if not np.isfinite(max_distance) or max_distance <= 0:
            raise ValueError("Positive distance required")
        keep = np.all(self.triangles.max(axis=1) >= bounds[0] - max_distance, axis=1) & np.all(
            self.triangles.min(axis=1) <= bounds[1] + max_distance, axis=1
        )
        if not keep.any():
            raise ValueError("REFERENCE_INVALID: no reference faces in expanded ROI")
        assert self.original_face_ids is not None
        return ReferenceSurface(
            self.vertices,
            self.faces[keep],
            self.corner_uv[keep],
            self.image,
            self.base_factor,
            self.source_has_normal,
            self.source_has_orm,
            self.original_face_ids[keep],
            self.repeat,
        )

    def sample(
        self, points: FloatArray, max_distance: float, *, deadline: float | None = None
    ) -> ReferenceSamples:
        """Exact nearest points with a conservative centroid-radius broad phase."""
        points = np.asarray(points, dtype=np.float64)
        if points.ndim != 2 or points.shape[1] != 3 or not np.isfinite(points).all():
            raise ValueError("Finite query points of shape (n, 3) required")
        if not np.isfinite(max_distance) or max_distance <= 0:
            raise ValueError("Positive maximum distance required")
        selected = np.empty(len(points), dtype=np.int64)
        projected = np.empty_like(points)
        distances = np.empty(len(points))
        for start in range(0, len(points), 64):
            _check_deadline(deadline)
            block = points[start : start + 64]
            _, first = self.tree.query(block, k=1)
            first = np.asarray(first, dtype=np.int64).reshape(-1)
            first_point = closest_point(self.triangles[first], block)
            upper = np.linalg.norm(first_point - block, axis=1)
            # Every potentially closer triangle has center distance <= upper + radius.
            radius = upper + self.maximum_radius + max(1e-12, self.diagonal * 1e-12)
            candidates = self.tree.query_ball_point(block, radius)
            lengths = np.asarray([len(x) for x in candidates], dtype=np.int64)
            ids = np.concatenate([np.asarray(x, dtype=np.int64) for x in candidates])
            all_points = np.repeat(block, lengths, axis=0)
            closest = closest_point(self.triangles[ids], all_points)
            squared = np.sum((closest - all_points) ** 2, axis=1)
            offset = 0
            for i, length in enumerate(lengths):
                best = offset + int(np.argmin(squared[offset : offset + length]))
                selected[start + i] = ids[best]
                projected[start + i] = closest[best]
                distances[start + i] = np.sqrt(squared[best])
                offset += int(length)
        barycentric = barycentric_coordinates(self.triangles[selected], projected)
        uv = np.einsum("nc,ncd->nd", barycentric, self.corner_uv[selected])
        rgba = sample_rgba8(self.image, uv, repeat=self.repeat) * self.base_factor
        valid = (distances <= max_distance) & (rgba[:, 3] >= 0.99) & np.isfinite(rgba).all(axis=1)
        assert self.original_face_ids is not None
        return ReferenceSamples(rgba, valid, distances, self.original_face_ids[selected], barycentric)


def load_reference(path: Path) -> ReferenceSurface:
    """Load the supported single-PBR-primitive GLB without altering its geometry."""
    scene = trimesh.load_scene(path, process=False)
    nodes = list(scene.graph.nodes_geometry)
    if len(nodes) != 1:
        raise ValueError("UNSUPPORTED_REFERENCE: exactly one geometry instance required")
    transform, name = scene.graph[nodes[0]]
    mesh = scene.geometry[name]
    if not isinstance(mesh, trimesh.Trimesh):
        raise TypeError("UNSUPPORTED_REFERENCE: triangle geometry required")
    material = getattr(mesh.visual, "material", None)
    uv = getattr(mesh.visual, "uv", None)
    if not isinstance(material, PBRMaterial) or uv is None or material.baseColorTexture is None:
        raise ValueError("UNSUPPORTED_REFERENCE: PBR BaseColor texture and UV required")
    image = np.asarray(material.baseColorTexture.convert("RGBA"), dtype=np.uint8)
    factor = np.ones(4)
    if material.baseColorFactor is not None:
        raw_factor = np.asarray(material.baseColorFactor)
        factor = raw_factor.astype(float) / (255 if np.issubdtype(raw_factor.dtype, np.integer) else 1)
    vertices = trimesh.transform_points(np.asarray(mesh.vertices, dtype=float), transform)
    faces = np.asarray(mesh.faces, dtype=np.int64)
    return ReferenceSurface(
        vertices,
        faces,
        np.asarray(uv, dtype=float)[faces],
        image,
        factor,
        material.normalTexture is not None,
        material.metallicRoughnessTexture is not None,
    )


def _face_pixels(triangle: FloatArray, width: int, height: int) -> tuple[IntArray, IntArray, FloatArray]:
    low = np.floor(triangle.min(axis=0) * (width, height) - 0.5).astype(int)
    high = np.ceil(triangle.max(axis=0) * (width, height) - 0.5).astype(int)
    xs = np.arange(max(0, low[0]), min(width - 1, high[0]) + 1)
    vs = np.arange(max(0, low[1]), min(height - 1, high[1]) + 1)
    xx, vv = np.meshgrid(xs, vs)
    x, y = xx.reshape(-1), height - 1 - vv.reshape(-1)
    uv = np.column_stack(((x + 0.5) / width, 1 - (y + 0.5) / height))
    matrix = np.column_stack((triangle[1] - triangle[0], triangle[2] - triangle[0]))
    if abs(float(np.linalg.det(matrix))) <= 1e-18:
        raise ValueError("Degenerate atlas triangle")
    bc = np.linalg.solve(matrix, (uv - triangle[0]).T).T
    bary = np.column_stack((1 - bc.sum(axis=1), bc))
    keep = np.all(bary >= -1e-10, axis=1)
    return x[keep], y[keep], bary[keep]


def build_rgba8_mips(base: Uint8Array) -> tuple[Uint8Array, ...]:
    if base.ndim != 3 or base.shape[2] != 4 or base.dtype != np.uint8:
        raise ValueError("RGBA8 base image required")
    levels = [base.copy()]
    for width, height in mip_dimensions(base.shape[1], base.shape[0])[1:]:
        previous = decode_rgba8(levels[-1])
        channels = [
            np.asarray(
                Image.fromarray(previous[..., c].astype(np.float32)).resize(
                    (width, height),
                    resample=Image.Resampling.BOX,
                ),
                dtype=float,
            )
            for c in range(4)
        ]
        levels.append(encode_rgba8(np.stack(channels, axis=-1)))
    return tuple(levels)


@dataclass(frozen=True)
class AtlasTexture:
    levels: tuple[Uint8Array, ...]
    coverage: BoolArray
    metadata: dict[str, object]


def bake_atlas(
    state: MeshState,
    reference: ReferenceSurface,
    width: int,
    height: int,
    output: Path | None = None,
    *,
    member_ids: IntArray | None = None,
    max_projection_fraction: float = 0.02,
    padding_pixels: int = 4,
    deadline: float | None = None,
) -> AtlasTexture:
    """Compose linear colors; caller must separately authorize UV overlaps."""
    mip_dimensions(width, height)
    if output is not None and output.exists():
        raise FileExistsError(output)
    if width * height > 4_194_304:
        raise ValueError("Prototype atlas exceeds four-million-pixel resource limit")
    if not np.isfinite(max_projection_fraction) or max_projection_fraction <= 0 or padding_pixels < 0:
        raise ValueError("Invalid projection or padding policy")
    members = chart_labels(state) if member_ids is None else np.asarray(member_ids, dtype=np.int64)
    if members.shape != (len(state.mesh.faces),):
        raise ValueError("One member identity required per face")
    if not np.isfinite(state.uv).all() or np.any(state.uv < 0) or np.any(state.uv > 1):
        raise ValueError("Atlas UV outside [0, 1]")
    weights = np.zeros((height, width))
    sums = np.zeros((height, width, 4))
    seen: dict[int, set[int]] = {}
    source_invalid = 0
    projected_count = 0
    distance_limit = max_projection_fraction * reference.diagonal
    for fi, face in enumerate(state.uv_faces):
        _check_deadline(deadline)
        x, y, bary = _face_pixels(state.uv[face], width, height)
        if not len(x):
            continue
        visited = seen.setdefault(int(members[fi]), set())
        keys = y * width + x
        keep = np.asarray([int(k) not in visited for k in keys])
        x, y, bary, keys = x[keep], y[keep], bary[keep], keys[keep]
        visited.update(map(int, keys))
        if not len(x):
            continue
        points = bary @ state.mesh.vertices[state.mesh.faces[fi]]
        sample = reference.sample(points, distance_limit, deadline=deadline)
        projected_count += len(x)
        source_invalid += int(np.count_nonzero(~sample.valid))
        tri = state.uv[face]
        edge0, edge1 = tri[1] - tri[0], tri[2] - tri[0]
        uv_area = abs(float(edge0[0] * edge1[1] - edge0[1] * edge1[0])) * 0.5
        density_weight = state.mesh.areas[fi] / uv_area
        valid = sample.valid
        sums[y[valid], x[valid]] += sample.rgba_linear[valid] * density_weight
        weights[y[valid], x[valid]] += density_weight
    coverage = weights > 0
    linear = sums / np.maximum(weights[..., None], 1e-30)
    base = encode_rgba8(linear)
    if coverage.any() and padding_pixels:
        padding_result = distance_transform_edt(~coverage, return_indices=True)
        if not isinstance(padding_result, tuple):
            raise TypeError("Padding distance transform omitted indices")
        distances, nearest = padding_result
        pad = (~coverage) & (distances <= padding_pixels)
        base[pad] = base[nearest[0][pad], nearest[1][pad]]
    levels = build_rgba8_mips(base)
    if output is not None:
        output.mkdir(parents=True, exist_ok=False)
        for level, encoded in enumerate(levels):
            Image.fromarray(encoded).save(output / f"BaseColor_mip{level}.png")
    metadata: dict[str, object] = {
        "codec": "RGBA8_SRGB_RGB_LINEAR_ALPHA",
        "mip_policy": "floor-halved complete chain; linear BOX then per-level RGBA8 quantization",
        "payload_bytes": sum(level.nbytes for level in levels),
        "base_covered_pixels": int(coverage.sum()),
        "projected_member_texels": projected_count,
        "invalid_projection_member_texels": source_invalid,
        "max_projection_distance": distance_limit,
        "padding_pixels": padding_pixels,
        "normal_status": "NOT_MEASURED",
        "orm_status": "NOT_MEASURED",
        "source_normal_present": reference.source_has_normal,
        "source_metallic_roughness_present": reference.source_has_orm,
        "overlap_authorization": "CALLER_REQUIRED",
    }
    return AtlasTexture(levels, coverage, metadata)


def _stratified_barycentric(count: int) -> FloatArray:
    if count < 1:
        raise ValueError("Positive samples per face required")
    index = np.arange(count, dtype=float)
    a = np.sqrt((index + 0.5) / count)
    b = np.mod(index * ((np.sqrt(5) - 1) / 2), 1)
    return np.column_stack((1 - a, a * (1 - b), a * b))


def evaluate_atlas(
    state: MeshState,
    reference: ReferenceSurface,
    atlas: AtlasTexture,
    *,
    samples_per_face: int = 16,
    mip_levels: tuple[int, ...] = (0,),
    max_projection_fraction: float = 0.02,
    color_mean_limit: float = 0.025,
    color_p95_limit: float = 0.08,
    color_max_limit: float = 0.20,
    deadline: float | None = None,
) -> dict[str, object]:
    """Check every face at fixed samples against the same high-poly point signal.

    Mip quality here measures point-signal preservation, not integrated-footprint
    equivalence or view-dependent hardware LOD/render quality.
    """
    if (
        not mip_levels
        or min(mip_levels) < 0
        or max(mip_levels) >= len(atlas.levels)
        or not np.isfinite(
            [max_projection_fraction, color_mean_limit, color_p95_limit, color_max_limit]
        ).all()
        or min(max_projection_fraction, color_mean_limit, color_p95_limit, color_max_limit) <= 0
    ):
        raise ValueError("Invalid evaluation protocol")
    bary = _stratified_barycentric(samples_per_face)
    distance_limit = max_projection_fraction * reference.diagonal
    records: list[dict[str, object]] = []
    failures: set[int] = set()
    invalid_faces: set[int] = set()
    all_errors: list[FloatArray] = []
    all_weights: list[FloatArray] = []
    for fi, face in enumerate(state.uv_faces):
        _check_deadline(deadline)
        samples = reference.sample(
            bary @ state.mesh.vertices[state.mesh.faces[fi]], distance_limit, deadline=deadline
        )
        uv = bary @ state.uv[face]
        if not samples.valid.all():
            invalid_faces.add(fi)
        for level in mip_levels:
            actual = sample_rgba8(atlas.levels[level], uv)
            error = np.linalg.norm(actual[:, :3] - samples.rgba_linear[:, :3], axis=1)
            metrics: dict[str, object] = {
                "face": fi,
                "mip": level,
                "samples": len(bary),
                "valid_reference_samples": int(samples.valid.sum()),
                "color_mean": float(error.mean()),
                "color_p95": float(np.quantile(error, 0.95)),
                "color_max": float(error.max()),
            }
            passed = bool(
                samples.valid.all()
                and float(error.mean()) <= color_mean_limit
                and float(np.quantile(error, 0.95)) <= color_p95_limit
                and float(error.max()) <= color_max_limit
            )
            metrics["passed"] = passed
            records.append(metrics)
            if not passed:
                failures.add(fi)
            all_errors.append(error)
            all_weights.append(np.full(len(bary), state.mesh.areas[fi] / len(bary)))
    errors, weights = np.concatenate(all_errors), np.concatenate(all_weights)
    baked_invalid = atlas.metadata.get("invalid_projection_member_texels")
    bake_reference_valid = (
        isinstance(baked_invalid, int) and baked_invalid == 0 and bool(atlas.coverage.any())
    )
    return {
        "status": "BASE_COLOR_DIAGNOSTIC",
        "valid": False,
        "base_color_valid": not failures and bake_reference_valid,
        "bake_reference_valid": bake_reference_valid,
        "normal_status": "NOT_MEASURED",
        "orm_status": "NOT_MEASURED",
        "source_channels_present": {
            "base_color": True,
            "normal": reference.source_has_normal,
            "metallic_roughness": reference.source_has_orm,
        },
        "required_channels_not_measured": ["normal"]
        + (["metallic_roughness"] if reference.source_has_orm else []),
        "geometry_status": "CALLER_REQUIRED",
        "render_status": "NOT_MEASURED",
        "failed_faces": sorted(failures),
        "reference_invalid_faces": sorted(invalid_faces),
        "area_weighted_color_mean": float(np.sum(errors * weights) / weights.sum()),
        "color_aggregation": "surface-area-weighted mean, with equal weight per requested mip",
        "color_max": float(errors.max()),
        "required_reference_coverage": 1.0,
        "samples_per_face": samples_per_face,
        "mip_levels": list(mip_levels),
        "mip_reference": "fixed high-poly point signal; not footprint-integrated reference",
        "faces": records,
    }
