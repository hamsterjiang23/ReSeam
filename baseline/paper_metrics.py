"""Read-only MeshQuery A.6 metrics with explicit topology and AO sampling choices."""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field

import numpy as np
from shapely import MultiPoint

from reseam.agent_tools.mesh_query import _BVH, _build_bvh, _ray_hit
from reseam.mesh import chart_labels
from reseam.types import FloatArray, IntArray, Mesh, MeshState
from reseam.validation import jacobians


def _loops(boundary: list[tuple[int, int]]) -> tuple[list[list[int]], int]:
    """Extract every degree-two boundary component without coordinate welding."""
    adjacency: dict[int, list[int]] = {}
    for a, b in boundary:
        adjacency.setdefault(a, []).append(b)
        adjacency.setdefault(b, []).append(a)
    remaining = set(adjacency)
    loops: list[list[int]] = []
    invalid = 0
    while remaining:
        seed = min(remaining)
        component, pending = {seed}, [seed]
        while pending:
            for other in adjacency[pending.pop()]:
                if other not in component:
                    component.add(other)
                    pending.append(other)
        remaining.difference_update(component)
        if any(len(adjacency[v]) != 2 for v in component):
            invalid += 1
            continue
        order, previous, current = [seed], seed, min(adjacency[seed])
        while current != seed and len(order) <= len(component):
            order.append(current)
            choices = adjacency[current]
            previous, current = current, choices[0] if choices[0] != previous else choices[1]
        if current != seed or len(order) != len(component):
            invalid += 1
        else:
            loops.append(order)
    return loops, invalid


def resampled_loop(points: FloatArray, spacing: float = .005) -> FloatArray:
    lengths = np.linalg.norm(np.roll(points, -1, axis=0) - points, axis=1)
    keep = lengths > 0
    lengths = lengths[keep]
    starts = points[keep]
    ends = np.roll(points, -1, axis=0)[keep]
    total = float(lengths.sum())
    if total == 0:
        return np.empty((0, 2), dtype=np.float64)
    count = int(np.clip(math.ceil(total / spacing), 8, 4096))
    arc = np.arange(count, dtype=float) * total / count
    cumulative = np.cumsum(lengths)
    segments = np.searchsorted(cumulative, arc, side="right")
    preceding = np.r_[0., cumulative[:-1]]
    alpha = (arc - preceding[segments]) / lengths[segments]
    return starts[segments] + alpha[:, None] * (ends - starts)[segments]


def chart_metrics(surface: MeshState, spacing: float = .005) -> dict[str, object]:
    labels = chart_labels(surface)
    triangles = surface.uv[surface.uv_faces]
    delta = triangles[:, 1:] - triangles[:, :1]
    area = abs(delta[:, 0, 0] * delta[:, 1, 1] - delta[:, 0, 1] * delta[:, 1, 0]) / 2
    charts: list[dict[str, object]] = []
    compactness: list[float] = []
    convexity: list[float] = []
    jagged_sum, jagged_samples, non_disk, invalid_components = 0., 0, 0, 0
    for label in np.unique(labels):
        ids = np.flatnonzero(labels == label)
        faces = surface.uv_faces[ids]
        vertices = np.unique(faces)
        edges: dict[tuple[int, int], int] = {}
        for face in faces:
            for i in range(3):
                a, b = int(face[i]), int(face[(i + 1) % 3])
                edge = (min(a, b), max(a, b))
                edges[edge] = edges.get(edge, 0) + 1
        boundary = [edge for edge, count in edges.items() if count == 1]
        chi = len(vertices) - len(edges) + len(faces)
        non_disk += chi != 1
        loops, invalid = _loops(boundary)
        invalid_components += invalid
        chart_area = float(area[ids].sum())
        perimeter = sum(float(np.linalg.norm(surface.uv[a] - surface.uv[b])) for a, b in boundary)
        compact, convex = None, None
        if chart_area > 0 and perimeter > 0:
            hull = float(MultiPoint(surface.uv[vertices]).convex_hull.area)
            compact = 4 * math.pi * chart_area / perimeter**2
            compactness.append(compact)
            if hull > 0:
                convex = min(1., chart_area / hull)
                convexity.append(convex)
        loop_scores: list[float] = []
        for loop in loops:
            sampled = resampled_loop(surface.uv[loop], spacing)
            if not len(sampled):
                continue
            curvature = np.linalg.norm(np.roll(sampled, 1, axis=0) - 2 * sampled
                                       + np.roll(sampled, -1, axis=0), axis=1)
            jagged_sum += float(curvature.sum())
            jagged_samples += len(sampled)
            loop_scores.append(float(curvature.mean()))
        charts.append({"chart": int(label), "faces": len(ids), "vertices": len(vertices),
                       "edges": len(edges), "euler_characteristic": chi, "non_disk": chi != 1,
                       "uv_area_triangle_sum": chart_area, "perimeter": perimeter,
                       "compactness": compact, "convexity": convex,
                       "boundary_loops": len(loops), "invalid_boundary_components": invalid,
                       "jaggedness_loop_means": loop_scores,
                       "nonmanifold_uv_edges": sum(count > 2 for count in edges.values())})
    j = jacobians(surface)
    norms = np.linalg.norm(j[:, 0], axis=1) * np.linalg.norm(j[:, 1], axis=1)
    det = delta[:, 0, 0] * delta[:, 1, 1] - delta[:, 0, 1] * delta[:, 1, 0]
    kept = (abs(det) > 0) & (norms > 0)
    cosine = np.einsum("ij,ij->i", j[kept, 0], j[kept, 1]) / norms[kept]
    return {"non_disk_fraction": non_disk / len(charts),
            "compactness": float(np.mean(compactness)) if compactness else None,
            "convexity": float(np.mean(convexity)) if convexity else None,
            "jaggedness": jagged_sum / jagged_samples if jagged_samples else None,
            "angular_quality": float(1 - np.abs(np.clip(cosine, -1, 1)).mean()) if np.any(kept) else None,
            "angular_quality_faces": int(kept.sum()), "chart_count": len(charts),
            "shape_valid_charts": len(compactness), "convexity_valid_charts": len(convexity),
            "jaggedness_samples": jagged_samples, "invalid_boundary_components": invalid_components,
            "jaggedness_scope": "raw UV units; pooled resampled boundary points; no undocumented display multiplier",
            "charts": charts}


def ao_score(seams: IntArray, reference: IntArray, rays: int = 8) -> float:
    """Probability of higher occlusion, with half credit for exact hit-count ties."""
    if not len(seams) or not len(reference):
        raise ValueError("AO_REQUIRES_BOTH_SAMPLE_SETS")
    a = np.bincount(seams, minlength=rays + 1)
    b = np.bincount(reference, minlength=rays + 1)
    less = np.cumsum(b) - b
    return float(np.dot(a, less + .5 * b) / (len(seams) * len(reference)))


@dataclass
class AOScene:
    mesh: Mesh
    seed: int = 17
    samples: int = 1000
    rays: int = 8
    triangles: FloatArray = field(init=False)
    root: _BVH = field(init=False)
    lengths: FloatArray = field(init=False)
    normals: FloatArray = field(init=False)
    diagonal: float = field(init=False)
    cache: dict[tuple[int, ...], IntArray] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.triangles = self.mesh.vertices[self.mesh.faces]
        self.root = _build_bvh(self.triangles, np.arange(len(self.triangles), dtype=np.int64))
        endpoints = self.mesh.vertices[np.asarray(self.mesh.edges)]
        self.lengths = np.linalg.norm(endpoints[:, 1] - endpoints[:, 0], axis=1)
        cross = np.cross(self.triangles[:, 1] - self.triangles[:, 0],
                         self.triangles[:, 2] - self.triangles[:, 0])
        face_normals = cross / np.linalg.norm(cross, axis=1)[:, None]
        self.normals = np.asarray([face_normals[[f for f, _ in members]].mean(axis=0)
                                  for members in self.mesh.edge_faces])
        norm = np.linalg.norm(self.normals, axis=1)
        for edge in np.flatnonzero(norm < 1e-12):
            self.normals[edge] = face_normals[self.mesh.edge_faces[edge][0][0]]
        self.normals /= np.linalg.norm(self.normals, axis=1)[:, None]
        self.diagonal = float(np.linalg.norm(np.ptp(self.mesh.vertices, axis=0)))

    def hits(self, edges: tuple[int, ...], deadline: float | None = None) -> IntArray:
        if edges in self.cache:
            return self.cache[edges]
        if not edges:
            return np.empty(0, dtype=np.int64)
        selected = np.asarray(edges, dtype=np.int64)
        lengths = self.lengths[selected]
        cumulative = np.cumsum(lengths)
        total = float(cumulative[-1])
        arc = (np.arange(self.samples, dtype=float) + .5) * total / self.samples
        local = np.searchsorted(cumulative, arc, side="right")
        alpha = (arc - np.r_[0., cumulative[:-1]][local]) / lengths[local]
        ids = selected[local]
        vertices = self.mesh.vertices[np.asarray(self.mesh.edges)[ids]]
        points = vertices[:, 0] + alpha[:, None] * (vertices[:, 1] - vertices[:, 0])
        normals = self.normals[ids]
        helper = np.zeros_like(normals)
        helper[np.arange(len(normals)), np.argmin(abs(normals), axis=1)] = 1
        tangent = np.cross(normals, helper)
        tangent /= np.linalg.norm(tangent, axis=1)[:, None]
        bitangent = np.cross(normals, tangent)
        random = np.random.default_rng(self.seed).random((self.samples, self.rays, 2))
        radius = np.sqrt(random[:, :, 0])
        phi = 2 * math.pi * random[:, :, 1]
        local_x, local_y, local_z = radius * np.cos(phi), radius * np.sin(phi), np.sqrt(1 - radius**2)
        result = np.zeros(self.samples, dtype=np.int64)
        for point in range(self.samples):
            if deadline is not None and time.monotonic() > deadline:
                raise TimeoutError("AO_EVALUATION_DEADLINE")
            origin = points[point] + normals[point] * self.diagonal * 1e-6
            directions = local_x[point, :, None] * tangent[point] + local_y[point, :, None] * bitangent[point]
            directions += local_z[point, :, None] * normals[point]
            for direction in directions:
                result[point] += _ray_hit(self.root, self.triangles, origin, direction,
                                         self.diagonal * 2, self.diagonal * 1e-9) is not None
        self.cache[edges] = result
        return result

    def evaluate(self, surface: MeshState, deadline: float | None = None) -> dict[str, object]:
        reference = self.hits(tuple(range(len(self.mesh.edges))), deadline)
        cuts = tuple(sorted(surface.seams))
        boundaries = tuple(sorted(set(cuts) | {i for i, members in enumerate(self.mesh.edge_faces) if len(members) == 1}))
        cut_hits = self.hits(cuts, deadline)
        boundary_hits = self.hits(boundaries, deadline)
        return {"seam_ao": ao_score(cut_hits, reference, self.rays) if len(cut_hits) else None,
                "boundary_ao": ao_score(boundary_hits, reference, self.rays) if len(boundary_hits) else None,
                "seam_ao_status": "OK" if cuts else "NOT_APPLICABLE_NO_EXPLICIT_CUTS",
                "boundary_ao_status": "OK" if boundaries else "NOT_APPLICABLE_NO_BOUNDARIES",
                "explicit_cut_edges": len(cuts), "all_boundary_physical_edges": len(boundaries),
                "reference_hit_histogram": np.bincount(reference, minlength=self.rays + 1).tolist(),
                "seam_hit_histogram": np.bincount(cut_hits, minlength=self.rays + 1).tolist(),
                "boundary_hit_histogram": np.bincount(boundary_hits, minlength=self.rays + 1).tolist(),
                "reference_mean_occlusion": float(reference.mean() / self.rays),
                "seam_mean_occlusion": float(cut_hits.mean() / self.rays) if len(cut_hits) else None}
