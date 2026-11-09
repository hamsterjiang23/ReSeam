"""Full topology and continuous triangle-intersection validation (not raster coverage)."""

from typing import Any

import numpy as np
from shapely import LineString, Point, Polygon, STRtree

from .mesh import DisjointSet, chart_labels, corner_map
from .types import FloatArray, MeshState


def jacobians(state: MeshState) -> FloatArray:
    triangles = state.mesh.vertices[state.mesh.faces] / state.mesh.scale
    e1, e2 = triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0]
    lengths = np.linalg.norm(e1, axis=1)
    x = np.zeros((len(triangles), 2, 2))
    x[:, 0, 0] = lengths
    x[:, 0, 1] = np.sum(e1 * e2, axis=1) / lengths
    x[:, 1, 1] = np.linalg.norm(np.cross(e1, e2), axis=1) / lengths
    corners = state.uv[state.uv_faces]
    y = np.stack((corners[:, 1] - corners[:, 0], corners[:, 2] - corners[:, 0]), axis=2)
    return y @ np.linalg.inv(x)


def distortion(state: MeshState) -> dict[str, float]:
    jac = jacobians(state)
    singular = np.linalg.svd(jac, compute_uv=False)
    if np.any(singular < 1e-14):
        return {
            "sd_mean": float("inf"),
            "sd_p95": float("inf"),
            "sd_max": float("inf"),
            "min_det": float(np.linalg.det(jac).min()),
        }
    values = 0.25 * np.sum(singular**2 + singular**-2, axis=1) - 1
    weights = state.mesh.areas / state.mesh.areas.sum()
    order = np.argsort(values)
    index = min(int(np.searchsorted(np.cumsum(weights[order]), 0.95)), len(values) - 1)
    return {
        "sd_mean": float(np.dot(values, weights)),
        "sd_p95": float(values[order[index]]),
        "sd_max": float(values.max()),
        "min_det": float(np.linalg.det(jac).min()),
    }


def topology_errors(state: MeshState) -> list[str]:
    errors: list[str] = []
    if state.uv_faces.shape != state.mesh.faces.shape or state.uv.ndim != 2 or state.uv.shape[1] != 2:
        return ["INVALID_UV_SHAPE"]
    if not np.isfinite(state.uv).all() or state.uv_faces.min() < 0 or state.uv_faces.max() >= len(state.uv):
        return ["INVALID_UV_INDEX_OR_COORDINATE"]
    if any(e < 0 or e >= len(state.mesh.edges) or len(state.mesh.edge_faces[e]) != 2 for e in state.seams):
        return ["INVALID_SEAM_ID"]
    expected = corner_map(state.mesh, state.seams).reshape(-1)
    actual = state.uv_faces.reshape(-1)
    forward: dict[int, int] = {}
    inverse: dict[int, int] = {}
    for left, right in zip(expected.tolist(), actual.tolist(), strict=True):
        if forward.setdefault(left, right) != right or inverse.setdefault(right, left) != left:
            return ["UV_COPY_CONNECTIVITY_MISMATCH"]
    for ei in state.seams:
        (a, _), (b, _) = state.mesh.edge_faces[ei]
        same = []
        for vertex in state.mesh.edges[ei]:
            ca = int(np.flatnonzero(state.mesh.faces[a] == vertex)[0])
            cb = int(np.flatnonzero(state.mesh.faces[b] == vertex)[0])
            same.append(state.uv_faces[a, ca] == state.uv_faces[b, cb])
        if all(same):
            errors.append(f"UNREALIZED_SEAM:{ei}")
    labels = chart_labels(state)
    for label in np.unique(labels):
        faces = state.uv_faces[labels == label]
        edges: dict[tuple[int, int], int] = {}
        for face in faces:
            for c in range(3):
                a, b = int(face[c]), int(face[(c + 1) % 3])
                edge = (min(a, b), max(a, b))
                edges[edge] = edges.get(edge, 0) + 1
        boundary = [edge for edge, count in edges.items() if count == 1]
        degree: dict[int, int] = {}
        for a, b in boundary:
            degree[a] = degree.get(a, 0) + 1
            degree[b] = degree.get(b, 0) + 1
        boundary_components = DisjointSet(len(state.uv))
        for a, b in boundary:
            boundary_components.union(a, b)
        loops = len({boundary_components.find(vertex) for vertex in degree})
        chi = len(np.unique(faces)) - len(edges) + len(faces)
        if (
            not boundary
            or chi != 2 - loops
            or any(n != 2 for n in degree.values())
            or any(n > 2 for n in edges.values())
        ):
            errors.append(f"NON_PLANAR_OR_NONMANIFOLD_CHART:{int(label)}")
    return errors


def intersection_errors(state: MeshState, tolerance: float = 1e-10) -> list[str]:
    labels = chart_labels(state)
    corners = state.uv[state.uv_faces]
    errors: list[str] = []
    for label in np.unique(labels):
        face_ids = np.flatnonzero(labels == label)
        polygons = [Polygon(corners[fi]) for fi in face_ids]
        pairs = STRtree(polygons).query(polygons, predicate="intersects")
        for a, b in zip(pairs[0], pairs[1], strict=True):
            if a >= b:
                continue
            fa, fb = int(face_ids[a]), int(face_ids[b])
            common = np.intersect1d(state.uv_faces[fa], state.uv_faces[fb])
            overlap = polygons[a].intersection(polygons[b])
            if len(common) == 1:
                overlap = overlap.difference(Point(state.uv[common[0]]).buffer(tolerance))
            elif len(common) == 2:
                overlap = overlap.difference(LineString(state.uv[common]).buffer(tolerance))
            if not overlap.is_empty:
                errors.append(f"CHART_INTERSECTION:{fa}:{fb}")
                if len(errors) >= 10:
                    return errors
    return errors


def seam_metrics(
    state: MeshState, weights: dict[int, float] | None = None, island_weight: float = 0.0
) -> dict[str, float | int]:
    weighted, length = 0.0, 0.0
    for ei in state.seams:
        a, b = state.mesh.edges[ei]
        value = float(np.linalg.norm(state.mesh.vertices[a] - state.mesh.vertices[b])) / state.mesh.scale
        length += value
        weighted += value * (weights or {}).get(ei, 1.0)
    labels = chart_labels(state)
    areas = np.bincount(labels, weights=state.mesh.areas) / state.mesh.areas.sum()
    return {
        "charts": len(areas),
        "seam_length_normalized": length,
        "weighted_seam_length": weighted,
        "objective": weighted + island_weight * len(areas),
        "small_chart_area_fraction": float(areas[areas < 0.005].sum()),
    }


def validate(state: MeshState, tau: float | None = 0.025, intersections: bool = True) -> dict[str, Any]:
    errors = topology_errors(state)
    if errors:
        return {"valid": False, "errors": errors, "quality_status": "STRUCTURE_INVALID"}
    metrics = distortion(state)
    if metrics["min_det"] <= 1e-12:
        errors.append("FLIPPED_OR_DEGENERATE")
    if intersections and not errors:
        errors.extend(intersection_errors(state))
    if tau is not None and metrics["sd_mean"] > tau + 1e-9:
        errors.append("DISTORTION_LIMIT")
    return {
        "valid": not errors,
        "errors": errors,
        **metrics,
        **seam_metrics(state),
        "quality_status": "VALID" if not errors else "QUALITY_INVALID",
    }
