"""Approximate bilateral vertex-to-surface correspondences, independent of triangulation."""

from dataclasses import dataclass

import numpy as np
from scipy.optimize import minimize_scalar
from scipy.spatial import KDTree
from trimesh.triangles import closest_point

from .types import FloatArray, IntArray, Mesh


@dataclass
class SurfaceMatches:
    source_ids: IntArray
    target_faces: IntArray
    target_points: FloatArray
    barycentric: FloatArray
    nearest_vertex_ids: IntArray
    surface_error: FloatArray
    vertex_error: FloatArray
    cycle_error: FloatArray
    normal_angle_degrees: FloatArray
    side: IntArray


def reflect(points: FloatArray, normal: FloatArray, offset: float) -> FloatArray:
    return points - 2 * (points @ normal - offset)[..., None] * normal


def vertex_normals(mesh: Mesh) -> FloatArray:
    triangles = mesh.vertices[mesh.faces]
    cross = np.cross(triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0])
    result = np.zeros_like(mesh.vertices)
    for corner in range(3):
        np.add.at(result, mesh.faces[:, corner], cross)
    lengths = np.linalg.norm(result, axis=1)
    return result / np.maximum(lengths[:, None], 1e-15)


def project_surface(points: FloatArray, triangles: FloatArray) -> tuple[FloatArray, IntArray, FloatArray]:
    """Exact nearest point over the supplied triangles, batched for bounded memory."""
    if not len(triangles):
        raise ValueError("Empty target surface")
    result = np.empty_like(points)
    faces = np.empty(len(points), dtype=np.int64)
    distances = np.empty(len(points))
    for start in range(0, len(points), 16):
        block = points[start : start + 16]
        repeated = np.tile(triangles, (len(block), 1, 1))
        queries = np.repeat(block, len(triangles), axis=0)
        projected = closest_point(repeated, queries).reshape(len(block), len(triangles), 3)
        squared = np.sum((projected - block[:, None]) ** 2, axis=2)
        index = np.argmin(squared, axis=1)
        result[start : start + len(block)] = projected[np.arange(len(block)), index]
        faces[start : start + len(block)] = index
        distances[start : start + len(block)] = np.sqrt(squared[np.arange(len(block)), index])
    return result, faces, distances


def barycentric_coordinates(triangles: FloatArray, points: FloatArray) -> FloatArray:
    u, v, w = triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0], points - triangles[:, 0]
    uu, uv, vv = np.sum(u * u, axis=1), np.sum(u * v, axis=1), np.sum(v * v, axis=1)
    wu, wv = np.sum(w * u, axis=1), np.sum(w * v, axis=1)
    denom = uu * vv - uv * uv
    if np.any(denom <= 0):
        raise ValueError("Degenerate target triangle")
    b, c = (vv * wu - uv * wv) / denom, (uu * wv - uv * wu) / denom
    return np.column_stack((1 - b - c, b, c))


def fit_offset(mesh: Mesh, normal: FloatArray, initial_offset: float = 0.0) -> tuple[float, float, float]:
    """Fit plane offset only; orientation is explicitly chosen for the aligned dragon asset."""
    normal = normal / np.linalg.norm(normal)
    diagonal = float(np.linalg.norm(np.ptp(mesh.vertices, axis=0)))
    triangles = mesh.vertices[mesh.faces]
    centers = triangles.mean(axis=1)
    signs = centers @ normal - initial_offset
    target = triangles[np.max(triangles @ normal, axis=1) < initial_offset - 0.005 * diagonal]
    candidates = np.flatnonzero(signs > 0.02 * diagonal)
    # Deterministic area-stratified samples avoid weighting dense retopology too strongly.
    cumulative = np.cumsum(mesh.areas[candidates])
    indices = np.searchsorted(cumulative, (np.arange(96) + 0.5) / 96 * cumulative[-1])
    samples = centers[candidates[indices]]

    def objective(offset: float) -> float:
        _, _, distance = project_surface(reflect(samples, normal, offset), target)
        return float(np.mean(np.sort(distance**2)[: int(0.8 * len(distance))]) / diagonal**2)

    before = objective(initial_offset)
    result = minimize_scalar(
        objective,
        bounds=(initial_offset - 0.02 * diagonal, initial_offset + 0.02 * diagonal),
        method="bounded",
        options={"maxiter": 18, "xatol": diagonal * 1e-6},
    )
    offset = float(result.x) if result.fun < before else initial_offset
    return offset, before, min(before, float(result.fun))


def match_surfaces(
    mesh: Mesh, normal: FloatArray, offset: float, band_fraction: float = 0.02
) -> SurfaceMatches:
    if normal.shape != (3,) or not np.isfinite(normal).all() or np.linalg.norm(normal) < 1e-12:
        raise ValueError("Invalid plane normal")
    normal = normal / np.linalg.norm(normal)
    diagonal = float(np.linalg.norm(np.ptp(mesh.vertices, axis=0)))
    signs = mesh.vertices @ normal - offset
    source_ids = np.flatnonzero(np.abs(signs) > band_fraction * diagonal)
    if not len(source_ids):
        raise ValueError("No vertices outside symmetry plane band")
    triangles = mesh.vertices[mesh.faces]
    normals = vertex_normals(mesh)
    side = np.where(signs[source_ids] > 0, 1, -1).astype(np.int64)
    target_points = np.empty((len(source_ids), 3))
    target_faces = np.empty(len(source_ids), dtype=np.int64)
    surface_error = np.empty(len(source_ids))
    vertex_error = np.empty(len(source_ids))
    vertex_ids = np.empty(len(source_ids), dtype=np.int64)
    cycle_error = np.empty(len(source_ids))
    bary = np.empty((len(source_ids), 3))
    angle = np.empty(len(source_ids))
    for direction in (1, -1):
        rows = np.flatnonzero(side == direction)
        if not len(rows):
            continue
        targets = np.flatnonzero(np.max(direction * signs[mesh.faces], axis=1) < 0)
        originals = np.flatnonzero(np.min(direction * signs[mesh.faces], axis=1) > 0)
        queries = reflect(mesh.vertices[source_ids[rows]], normal, offset)
        point, face, error = project_surface(queries, triangles[targets])
        ids = targets[face]
        coordinates = barycentric_coordinates(triangles[ids], point)
        interpolated = np.einsum("fc,fcd->fd", coordinates, normals[mesh.faces[ids]])
        interpolated /= np.maximum(np.linalg.norm(interpolated, axis=1)[:, None], 1e-15)
        reflected_normals = reflect(normals[source_ids[rows]], normal, 0)
        returned, _, _ = project_surface(reflect(point, normal, offset), triangles[originals])
        target_vertex_ids = np.flatnonzero(direction * signs < 0)
        vd, vi = KDTree(mesh.vertices[target_vertex_ids]).query(queries)
        target_points[rows], target_faces[rows], bary[rows] = point, ids, coordinates
        surface_error[rows], vertex_error[rows] = error / diagonal, vd / diagonal
        vertex_ids[rows] = target_vertex_ids[vi]
        cycle_error[rows] = np.linalg.norm(returned - mesh.vertices[source_ids[rows]], axis=1) / diagonal
        angle[rows] = np.degrees(np.arccos(np.clip(np.sum(interpolated * reflected_normals, axis=1), -1, 1)))
    return SurfaceMatches(
        source_ids,
        target_faces,
        target_points,
        bary,
        vertex_ids,
        surface_error,
        vertex_error,
        cycle_error,
        angle,
        side,
    )
