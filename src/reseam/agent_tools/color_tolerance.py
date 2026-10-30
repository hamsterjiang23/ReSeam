"""Sampled source-to-target color balls, independent of atlas reconstruction."""

import math
from pathlib import Path
from typing import cast

import numpy as np

from ..budget_texture import _stratified_barycentric
from ..types import FloatArray
from .color_reuse import ColorTarget
from .regions import selected_faces
from .state import check_deadline, file_hash, require_snapshot, write_json

PRESET_RADII = {"low": 5., "medium": 10., "high": 20.}
BOUNDARY_ROUNDOFF = 1e-10
CONVERSION_SOURCE = "https://www.w3.org/TR/css-color-4/#color-conversion-code"

# W3C CSS Color 4 sample code: linear sRGB -> XYZ(D65) -> Oklab.
_RGB_XYZ = np.array([
    [506752 / 1228815, 87881 / 245763, 12673 / 70218],
    [87098 / 409605, 175762 / 245763, 12673 / 175545],
    [7918 / 409605, 87881 / 737289, 1001167 / 1053270],
])
_XYZ_LMS = np.array([
    [.8190224379967030, .3619062600528904, -.1288737815209879],
    [.0329836539323885, .9292868615863434, .0361446663506424],
    [.0481771893596242, .2642395317527308, .6335478284694309],
])
_LMS_OKLAB = np.array([
    [.2104542683093140, .7936177747023054, -.0040720430116193],
    [1.9779985324311684, -2.4285922420485799, .4505937096174110],
    [.0259040424655478, .7827717124575296, -.8086757549230774],
])


def linear_rgb_to_oklab(colors: FloatArray) -> FloatArray:
    """Convert un-companded RGB to D65 Oklab; no gamma decoding or clipping."""
    values = np.asarray(colors, dtype=float)
    if values.ndim < 1 or values.shape[-1] != 3 or not np.isfinite(values).all():
        raise ValueError("FINITE_LINEAR_RGB_REQUIRED")
    xyz = values @ _RGB_XYZ.T
    return np.asarray(np.cbrt(xyz @ _XYZ_LMS.T) @ _LMS_OKLAB.T)


def color_distances(original: FloatArray, target: FloatArray) -> FloatArray:
    """Sphere radius units are 100 times Euclidean Oklab distance."""
    if np.shape(original) != np.shape(target):
        raise ValueError("COLOR_ARRAY_SHAPES_MUST_MATCH")
    return np.asarray(100 * np.linalg.norm(
        linear_rgb_to_oklab(target) - linear_rgb_to_oklab(original), axis=-1))


def tolerance(request: dict[str, object]) -> tuple[str, float]:
    level = request.get("level", "medium")
    if not isinstance(level, str) or level not in PRESET_RADII:
        raise ValueError("COLOR_TOLERANCE_LEVEL_MUST_BE_LOW_MEDIUM_OR_HIGH")
    radius = request.get("radius", PRESET_RADII[level])
    if (isinstance(radius, bool) or not isinstance(radius, (int, float))
            or not math.isfinite(radius) or radius <= 0):
        raise ValueError("POSITIVE_FINITE_COLOR_RADIUS_REQUIRED")
    return level, float(radius)


def sphere_summary(distances: FloatArray, areas: FloatArray, radius: float) -> dict[str, object]:
    """Finite quadrature of the fraction outside the inclusive color ball."""
    values, weights = np.asarray(distances, dtype=float), np.asarray(areas, dtype=float)
    if (values.ndim != 2 or weights.shape != (len(values),) or values.shape[1] < 1
            or not np.isfinite(values).all() or np.any(values < 0)
            or not np.isfinite(weights).all() or np.any(weights < 0)
            or not math.isfinite(radius) or radius <= 0):
        raise ValueError("INVALID_COLOR_SPHERE_SUMMARY_INPUT")
    outside = values > radius + BOUNDARY_ROUNDOFF
    samples = values.size
    total_area = float(weights.sum())
    area_fraction = (float(outside.mean(axis=1) @ weights / total_area) if total_area > 0 else 0.)
    return {
        "radius": radius, "color_accepted": not bool(outside.any()),
        "samples": samples, "outside_samples": int(outside.sum()),
        "outside_sample_fraction": float(outside.mean()) if samples else 0.,
        "outside_area_fraction": area_fraction, "inside_area_fraction": 1 - area_fraction,
        "outside_faces": int(np.count_nonzero(outside.any(axis=1))),
        "mean": float(values.mean()) if samples else 0.,
        "area_weighted_mean": float(values.mean(axis=1) @ weights / total_area) if total_area > 0 else 0.,
        "p95": float(np.quantile(values, .95)) if samples else 0.,
        "maximum": float(values.max()) if samples else 0.,
    }


def check_color(request: dict[str, object], output: Path, deadline: float) -> dict[str, object]:
    """Compare original source colors with a frozen target without changing either."""
    check_deadline(deadline)
    level, radius = tolerance(request)
    count = request.get("samples_per_face", 16)
    if type(count) is not int or not 16 <= count <= 256:
        raise ValueError("SAMPLES_PER_FACE_MUST_BE_INTEGER_16_TO_256")
    snapshot = require_snapshot(request)
    raw = request.get("target")
    if not isinstance(raw, str):
        raise TypeError("FROZEN_TARGET_PATH_REQUIRED")
    target = ColorTarget(snapshot, Path(raw))
    inputs = {target.path: file_hash(target.path), target.path.parent / "target.npz":
              str(target.record["arrays_sha256"]), Path(str(target.record["source"])):
              str(target.record["source_sha256"])}
    selected = (selected_faces(request, snapshot) if "selected_faces" in request or "region_id" in request
                else sorted(target.slaves))
    bary = _stratified_barycentric(count)
    values = np.empty((len(selected), count))
    for row, face in enumerate(selected):
        check_deadline(deadline)
        values[row] = color_distances(target.original(face, bary)[:, :3], target.sample(face, bary)[:, :3])
    areas = snapshot.state.surface.mesh.areas[selected]
    summary = sphere_summary(values, areas, radius)
    presets = {name: sphere_summary(values, areas, value) for name, value in PRESET_RADII.items()}
    exceedance = values > radius + BOUNDARY_ROUNDOFF
    exceeded = [face for row, face in enumerate(selected) if exceedance[row].any()]
    rows = [{"face_id": face, "mean": float(values[row].mean()),
             "p95": float(np.quantile(values[row], .95)), "maximum": float(values[row].max()),
             "outside_samples": int(exceedance[row].sum())} for row, face in enumerate(selected)]
    groups: list[dict[str, object]] = []
    selected_array = np.asarray(selected, dtype=np.int64)
    for group in cast(list[dict[str, object]], target.record.get("groups", [])):
        check_deadline(deadline)
        faces = cast(list[int], group["master_faces"]) + cast(list[int], group["slave_faces"])
        keep = np.isin(selected_array, faces)
        if keep.any():
            groups.append({"group_id": group["group_id"], "selected_faces": selected_array[keep].tolist(),
                           "summary": sphere_summary(values[keep], areas[keep], radius),
                           "presets": {name: sphere_summary(values[keep], areas[keep], value)
                                       for name, value in PRESET_RADII.items()}})
    protected = set(cast(list[int], target.record.get("protected_faces", [])))
    protected_rows = np.isin(selected_array, list(protected))
    check_deadline(deadline)
    if require_snapshot(request).state_hash != snapshot.state_hash or any(
        file_hash(path) != digest for path, digest in inputs.items()
    ):
        raise ValueError("COLOR_CHECK_INPUT_CHANGED")
    record: dict[str, object] = {
        "schema_version": 1, "scope": "source_to_frozen_target_color_tolerance",
        "asset_id": snapshot.asset_id, "state_hash": snapshot.state_hash,
        "target": str(target.path), "target_hash": target.record["target_hash"],
        "input_sha256": {str(path): digest for path, digest in inputs.items()},
        "level": level, "radius": radius, "custom_radius": "radius" in request,
        "metric": "100 * Euclidean Oklab(D65)", "sphere": "distance(original_color,target_color) <= radius",
        "input_color_space": "linear-light sRGB; RGB sampled after baseColorFactor; alpha ignored",
        "conversion_source": CONVERSION_SOURCE,
        "preset_policy": "project defaults, not universal perceptual or artistic thresholds",
        "boundary_roundoff": BOUNDARY_ROUNDOFF, "samples_per_face": count,
        "selected_faces": selected, "selection_default": "frozen target modified member faces",
        "summary": summary, "presets": presets, "faces": rows, "groups": groups,
        "exceedance_faces": exceeded, "protected_face_count": len(protected),
        "protected_faces_evaluated": int(protected_rows.sum()),
        "protected_faces_changed_in_evaluated_samples": int(np.count_nonzero(
            (values[protected_rows] > BOUNDARY_ROUNDOFF).any(axis=1))),
        "geometry_modified": False, "uv_modified": False, "target_modified": False,
        "acceptance_scope": "finite sampled colors only; not all continuous points, topology, mip or bake quality",
        "reconstruction_quality_checked": False, "artist_reviewed": False,
    }
    path = output / "color_check.json"
    write_json(path, record)
    return {"status": "OK", "execution_success": True, "color_accepted": summary["color_accepted"],
            "color_check": str(path.resolve()), "report": str(path.resolve()),
            "level": level, "radius": radius, "summary": summary, "presets": presets,
            "exceedance_faces": exceeded, "asset_id": snapshot.asset_id, "state_hash": snapshot.state_hash,
            "target_hash": target.record["target_hash"], "target_modified": False,
            "final_quality_passed": None}
