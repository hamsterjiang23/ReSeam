"""Finite PBR source rays honor material sidedness and retain exact hits."""

import time

import numpy as np
import trimesh
from trimesh.ray.ray_triangle import ray_triangle_id

from ..budget_material import frame_arrays, interpolated_frames
from ..budget_texture import BoolArray
from ..types import FloatArray, IntArray, MeshState


class FiniteRayProjection:
    """Source projection has no closest-point fallback or automatic distance growth."""

    def __init__(self, low_surface: MeshState, source_vertices: FloatArray, source_faces: IntArray,
                 front_fraction: float = .02, back_fraction: float = .02,
                 source_double_sided: bool = False) -> None:
        if (not np.isfinite([front_fraction, back_fraction]).all() or front_fraction <= 0 or back_fraction <= 0):
            raise ValueError("FINITE_RAY_POSITIVE_FRONT_BACK_REQUIRED")
        self.low = low_surface
        self.frames = frame_arrays(low_surface)
        self.mesh = trimesh.Trimesh(vertices=source_vertices, faces=source_faces, process=False)
        self.diagonal = float(np.linalg.norm(np.ptp(low_surface.mesh.vertices, axis=0)))
        self.front = self.diagonal * front_fraction
        self.back = self.diagonal * back_fraction
        self.source_double_sided = source_double_sided
        self.counts = {"hit": 0, "miss": 0, "backface_only": 0, "out_of_range": 0, "invalid_low_frame": 0}
        self.two_sided_hits = 0
        self.last_hit_sides = np.empty(0, dtype=np.int8)
        self.failed_faces: set[int] = set()
        self.witnesses: list[dict[str, object]] = []
        self.seconds = 0.

    def query(self, low_faces: IntArray, bary: FloatArray) -> tuple[IntArray, FloatArray, FloatArray, BoolArray]:
        started = time.monotonic()
        if low_faces.ndim != 1 or bary.shape != (len(low_faces), 3):
            raise ValueError("FINITE_RAY_QUERY_SHAPE")
        xyz = np.einsum("ni,nij->nj", bary, self.low.mesh.vertices[self.low.mesh.faces[low_faces]])
        frames, normal_valid = interpolated_frames(*self.frames, low_faces, bary)
        direction = -frames[:, :, 2]
        origins = xyz - direction * self.front
        faces_out = np.full(len(low_faces), -1, dtype=np.int64)
        bary_out = np.zeros((len(low_faces), 3))
        hit_sides = np.zeros(len(low_faces), dtype=np.int8)
        distances = np.full(len(low_faces), self.front + self.back)
        classified = np.full(len(low_faces), "miss", dtype=object)
        classified[~normal_valid] = "invalid_low_frame"
        for offset in range(0, len(low_faces), 1024):
            end = min(offset + 1024, len(low_faces))
            # Keep coincident hit candidates until after facing filtering; the
            # public location-deduplicating wrapper can discard the front face.
            high_ids, ray_ids, locations = ray_triangle_id(triangles=self.mesh.triangles,
                ray_origins=origins[offset:end], ray_directions=direction[offset:end],
                tree=self.mesh.triangles_tree, multiple_hits=True, triangles_normal=self.mesh.face_normals)
            high_ids = np.asarray(high_ids, dtype=np.int64)
            ray_ids = np.asarray(ray_ids, dtype=np.int64) + offset
            locations = np.asarray(locations, dtype=np.float64)
            if not len(high_ids):
                continue
            t = np.einsum("ni,ni->n", locations - origins[ray_ids], direction[ray_ids])
            inside = (t >= -1e-9*self.diagonal) & (t <= self.front+self.back+1e-9*self.diagonal)
            facing = np.einsum("ni,ni->n", self.mesh.face_normals[high_ids], direction[ray_ids]) < -1e-8
            classified[np.unique(ray_ids)] = "out_of_range"
            classified[np.unique(ray_ids[inside])] = "backface_only"
            eligible = np.flatnonzero(inside & (facing | self.source_double_sided) & normal_valid[ray_ids])
            if not len(eligible):
                continue
            order = eligible[np.lexsort((high_ids[eligible], t[eligible], ray_ids[eligible]))]
            chosen = order[np.r_[True, np.diff(ray_ids[order]) != 0]]
            selected_rays, selected_faces = ray_ids[chosen], high_ids[chosen]
            faces_out[selected_rays] = selected_faces
            hit_sides[selected_rays] = np.where(facing[chosen], 1, -1)
            coordinates = trimesh.triangles.points_to_barycentric(self.mesh.triangles[selected_faces], locations[chosen])
            coordinates = np.clip(coordinates, 0., 1.)
            coordinates /= coordinates.sum(axis=1, keepdims=True)
            bary_out[selected_rays] = coordinates
            distances[selected_rays] = np.linalg.norm(locations[chosen] - xyz[selected_rays], axis=1)
            classified[selected_rays] = "hit"
        classified[~normal_valid] = "invalid_low_frame"
        valid = (faces_out >= 0) & normal_valid
        self.last_hit_sides = hit_sides
        self.two_sided_hits += int(np.count_nonzero(valid & (hit_sides == -1)))
        for name in self.counts:
            self.counts[name] += int(np.count_nonzero(classified == name))
        self.failed_faces.update(map(int, low_faces[~valid]))
        for index in range(min(len(low_faces), max(0, 16384-len(self.witnesses)))):
            self.witnesses.append({"low_face": int(low_faces[index]), "barycentric": bary[index].tolist(),
                "classification": str(classified[index]), "high_face": int(faces_out[index]),
                "source_hit_side": int(hit_sides[index]),
                "high_barycentric": bary_out[index].tolist(), "distance_from_low": float(distances[index])})
        self.seconds += time.monotonic()-started
        return faces_out, bary_out, distances, valid

    def audit(self) -> dict[str, object]:
        return {"method": "finite_material_sided_ray", "counts": dict(self.counts),
            "source_double_sided": self.source_double_sided, "two_sided_hit_count": self.two_sided_hits,
            "front_distance": self.front, "back_distance": self.back, "low_bbox_diagonal": self.diagonal,
            "failed_low_faces": sorted(self.failed_faces), "seconds": self.seconds,
            "witness_limit": 16384, "witnesses": self.witnesses,
            "selection": "first finite source hit permitted by material sidedness, stable face ID tie break",
            "source_hit_side": "+1 front, -1 back, 0 miss; relative to actual ray direction",
            "nearest_point_fallback": False, "full_continuous_coverage_proven": False}
