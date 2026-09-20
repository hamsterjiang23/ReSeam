"""Measurements for external artist references, allowing intentional UV reuse to be reported."""

from typing import Any

import numpy as np
from shapely import Polygon, box, union_all

from .types import FloatArray, IntArray


def uv_reference_metrics(vertices: FloatArray, faces: IntArray, corner_uv: FloatArray) -> dict[str, Any]:
    triangles = vertices[faces]
    edges = triangles[:, 1:] - triangles[:, :1]
    source_areas = np.linalg.norm(np.cross(edges[:, 0], edges[:, 1]), axis=1) / 2
    uv_edges = corner_uv[:, 1:] - corner_uv[:, :1]
    signed_areas = (uv_edges[:, 0, 0] * uv_edges[:, 1, 1] - uv_edges[:, 0, 1] * uv_edges[:, 1, 0]) / 2
    uv_areas = np.abs(signed_areas)
    usable_uv = uv_areas > 1e-16
    polygons = [Polygon(corner_uv[i]) for i in np.flatnonzero(usable_uv)]
    footprint = union_all(polygons)
    union_area = float(footprint.area)
    summed_area = float(uv_areas.sum())
    source_epsilon = max(float(source_areas.sum()) * 1e-14, 1e-20)
    usable = (source_areas > source_epsilon) & usable_uv
    result: dict[str, Any] = {
        "triangle_count": len(faces),
        "degenerate_3d_triangles": int((source_areas <= source_epsilon).sum()),
        "degenerate_uv_triangles": int((~usable_uv).sum()),
        "positive_uv_triangles": int((signed_areas > 1e-16).sum()),
        "negative_uv_triangles": int((signed_areas < -1e-16).sum()),
        "unique_atlas_coverage": float(footprint.intersection(box(0, 0, 1, 1)).area),
        "summed_uv_triangle_area": summed_area,
        "redundant_uv_area": max(0.0, summed_area - union_area),
        "uv_area_multiplicity": summed_area / union_area if union_area else None,
        "uv_area_outside_unit_square": float(footprint.difference(box(0, 0, 1, 1)).area),
        "measured_source_area_fraction": float(source_areas[usable].sum() / source_areas.sum())
        if source_areas.sum()
        else 0,
        "orientation_note": "Negative UV orientation may be intentional mirrored reuse; not classified as a flipped chart without connectivity analysis",
        "scope": "Per object and UV layer; not a same-topology ReSeam benchmark or a proof that reuse is intentional",
    }
    if not usable.any():
        result["distortion_status"] = "NO_NONDEGENERATE_TRIANGLES"
        return result
    e1, e2 = edges[usable, 0], edges[usable, 1]
    length = np.linalg.norm(e1, axis=1)
    basis = np.zeros((len(length), 2, 2))
    basis[:, 0, 0] = length
    basis[:, 0, 1] = np.sum(e1 * e2, axis=1) / length
    basis[:, 1, 1] = np.linalg.norm(np.cross(e1, e2), axis=1) / length
    jacobian = np.transpose(uv_edges[usable], (0, 2, 1)) @ np.linalg.inv(basis)
    singular = np.linalg.svd(jacobian, compute_uv=False)
    weights = source_areas[usable] / source_areas[usable].sum()
    quadratic = np.sum(singular**2, axis=1)
    inverse = np.sum(singular**-2, axis=1)
    scale = float((np.dot(weights, inverse) / np.dot(weights, quadratic)) ** 0.25)
    sd = 0.25 * (scale**2 * quadratic + scale**-2 * inverse) - 1
    order = np.argsort(sd)
    p95 = min(int(np.searchsorted(np.cumsum(weights[order]), 0.95)), len(sd) - 1)
    density = np.sqrt(uv_areas[usable] / source_areas[usable])
    density_mean = float(np.dot(weights, density))
    result.update(
        distortion_status="FULL_SURFACE" if usable.all() else "NONDEGENERATE_SUBSET_ONLY",
        sd_mean_nondegenerate=float(np.dot(weights, sd)),
        sd_p95_nondegenerate=float(sd[order[p95]]),
        sd_max_nondegenerate=float(sd.max()),
        face_density_area_weighted_cv=float(
            np.sqrt(np.dot(weights, (density - density_mean) ** 2)) / density_mean
        ),
        distortion_note="SD uses a common optimal UV scale on the nondegenerate subset; density CV is resolution and uniform-geometry-scale invariant",
    )
    return result
