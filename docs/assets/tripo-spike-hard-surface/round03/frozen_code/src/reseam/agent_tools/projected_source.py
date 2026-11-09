"""Explicit original-low to high-surface BaseColor projection and its witnesses.

Lineage is independent of UV coordinates. A mirrored face first addresses the
retained original low triangle, then queries the original high reference. No
candidate atlas or intermediate low-mesh bake participates in this signal.
"""

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import cast

import numpy as np

from ..budget_texture import BoolArray, ReferenceSamples, ReferenceSurface
from ..types import FloatArray, IntArray
from .state import Snapshot, file_hash, object_hash, write_json

_RULE = "original_low_barycentric_then_exact_nearest_original_high_fixed_original_face_aabb_roi_v2"
_DEFAULT_FRACTION = .02
_WITNESS_LIMIT = 16384


def _fraction(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not np.isfinite(value) or value <= 0:
        raise ValueError("PROJECTED_SOURCE_POSITIVE_FINITE_MAX_FRACTION_REQUIRED")
    return float(value)


def _lineage(snapshot: Snapshot) -> tuple[FloatArray, IntArray, FloatArray, dict[str, object]]:
    metadata = snapshot.metadata.get("projected_source")
    nfaces = len(snapshot.state.surface.mesh.faces)
    if metadata is None:
        mesh = snapshot.state.surface.mesh
        return (mesh.vertices[mesh.faces].copy(), np.arange(nfaces, dtype=np.int64),
                np.broadcast_to(np.eye(3), (nfaces, 3, 3)).copy(), {})
    if not isinstance(metadata, dict) or metadata.get("schema_version") != 1:
        raise ValueError("INVALID_PROJECTED_SOURCE_METADATA")
    path = Path(str(metadata.get("lineage_npz", "")))
    if not path.is_file() or file_hash(path) != metadata.get("lineage_sha256"):
        raise ValueError("PROJECTED_SOURCE_LINEAGE_CHANGED")
    with np.load(path, allow_pickle=False) as data:
        triangles = np.asarray(data["original_low_triangles"], dtype=float)
        faces = np.asarray(data["source_low_faces"], dtype=np.int64)
        bary = np.asarray(data["source_low_corner_barycentric"], dtype=float)
    return triangles, faces, bary, cast(dict[str, object], metadata)


def _validate_lineage(triangles: FloatArray, faces: IntArray, bary: FloatArray,
                      nfaces: int) -> float:
    if (triangles.ndim != 3 or triangles.shape[1:] != (3, 3) or not len(triangles)
            or not np.isfinite(triangles).all() or faces.shape != (nfaces,)
            or np.any(faces < 0) or np.any(faces >= len(triangles))
            or bary.shape != (nfaces, 3, 3) or not np.isfinite(bary).all()
            or np.any(bary < -1e-8) or np.any(bary > 1 + 1e-8)
            or not np.allclose(bary.sum(axis=2), 1., rtol=0, atol=1e-8)):
        raise ValueError("INVALID_PROJECTED_SOURCE_LINEAGE_ARRAYS")
    diagonal = float(np.linalg.norm(np.ptp(triangles.reshape(-1, 3), axis=0)))
    if not np.isfinite(diagonal) or diagonal <= 0:
        raise ValueError("INVALID_PROJECTED_SOURCE_ORIGINAL_LOW_SCALE")
    return diagonal


def projected_source_metadata(snapshot: Snapshot, output_npz: Path,
                              max_fraction: float = _DEFAULT_FRACTION) -> dict[str, object]:
    """Write initial (or copied) lineage without mutating a snapshot.

    A mirror composes its ``source_face_ids`` and ``source_barycentric`` with
    these arrays, then writes the same three NPZ keys for its new face set.
    """
    triangles, faces, bary, previous = _lineage(snapshot)
    _validate_lineage(triangles, faces, bary, len(snapshot.state.surface.mesh.faces))
    reference = previous.get("reference_glb", snapshot.metadata.get("reference_glb"))
    if not isinstance(reference, str):
        raise TypeError("REFERENCE_GLB_REQUIRED")
    reference_path = Path(reference).resolve()
    digest = file_hash(reference_path)
    if previous and previous.get("reference_sha256") != digest:
        raise ValueError("PROJECTED_SOURCE_REFERENCE_CHANGED")
    output_npz.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(output_npz, original_low_triangles=triangles, source_low_faces=faces,
                        source_low_corner_barycentric=bary)
    return {"schema_version": 1, "reference_glb": str(reference_path), "reference_sha256": digest,
            "projection_max_fraction": _fraction(max_fraction),
            "lineage_npz": str(output_npz.resolve()), "lineage_sha256": file_hash(output_npz)}


@dataclass(frozen=True)
class ProjectedSamples:
    rgba_linear: FloatArray
    valid: BoolArray
    source_low_faces: IntArray
    source_low_barycentric: FloatArray
    source_points: FloatArray
    target_faces: IntArray
    barycentric: FloatArray
    distances: FloatArray


class ProjectedSource:
    """Hash-bound source sampler, with exact nearest-high witnesses per query."""

    def __init__(self, reference: ReferenceSurface, triangles: FloatArray, faces: IntArray,
                 bary: FloatArray, max_fraction: float = _DEFAULT_FRACTION,
                 *, reference_sha256: str = "", deadline: float | None = None) -> None:
        self.reference = reference
        self.original_low_diagonal = _validate_lineage(triangles, faces, bary, len(faces))
        self.original_low_triangles = triangles.copy()
        self.source_low_faces = faces.copy()
        self.source_low_corner_barycentric = bary.copy()
        for array in (self.original_low_triangles, self.source_low_faces, self.source_low_corner_barycentric):
            array.setflags(write=False)
        self.max_fraction = _fraction(max_fraction)
        self.max_distance = self.original_low_diagonal * self.max_fraction
        self.reference_sha256 = reference_sha256
        self.deadline = deadline
        self.roi_bounds = np.stack((self.original_low_triangles.min(axis=1),
                                    self.original_low_triangles.max(axis=1)), axis=1)
        self.roi_bounds.setflags(write=False)
        self.roi_cache: dict[int, ReferenceSurface | None] = {}
        digest = hashlib.sha256()
        for array in (self.original_low_triangles, self.source_low_faces, self.source_low_corner_barycentric):
            digest.update(str(array.shape).encode("ascii")); digest.update(array.tobytes())
        self.rule: dict[str, object] = {"schema_version": 1, "rule": _RULE,
            "normalization": "original_low_aabb_diagonal", "original_low_diagonal": self.original_low_diagonal,
            "projection_max_fraction": self.max_fraction, "max_distance": self.max_distance,
            "reference_sha256": reference_sha256, "lineage_arrays_hash": digest.hexdigest(),
            "acceleration": "ReferenceSurface.restrict_bounds per original-low face AABB expanded by max_distance",
            "roi_fallback": "full original high for points outside fixed AABB, empty ROI, or ROI distance above bound"}
        self.rule_hash = object_hash(self.rule)
        self.audit_output: Path | None = None
        self.audit_stage = "unattached"
        self.reset_audit()

    @classmethod
    def from_snapshot(cls, snapshot: Snapshot, reference: ReferenceSurface,
                      projection_max_fraction: float | None = None,
                      *, deadline: float | None = None) -> "ProjectedSource":
        triangles, faces, bary, metadata = _lineage(snapshot)
        _validate_lineage(triangles, faces, bary, len(snapshot.state.surface.mesh.faces))
        source = metadata.get("reference_glb", snapshot.metadata.get("reference_glb"))
        if not isinstance(source, str):
            raise TypeError("REFERENCE_GLB_REQUIRED")
        digest = file_hash(Path(source))
        if metadata and metadata.get("reference_sha256") != digest:
            raise ValueError("PROJECTED_SOURCE_REFERENCE_CHANGED")
        fraction = (projection_max_fraction if projection_max_fraction is not None else
                    _fraction(metadata.get("projection_max_fraction", _DEFAULT_FRACTION)))
        return cls(reference, triangles, faces, bary, fraction, reference_sha256=digest, deadline=deadline)

    @classmethod
    def from_frozen(cls, reference: ReferenceSurface, record: dict[str, object],
                    arrays_path: Path, *, deadline: float | None = None) -> "ProjectedSource":
        if file_hash(arrays_path) != record.get("lineage_sha256"):
            raise ValueError("TARGET_PROJECTED_SOURCE_ARRAYS_CHANGED")
        with np.load(arrays_path, allow_pickle=False) as data:
            result = cls(reference, np.asarray(data["original_low_triangles"], dtype=float),
                         np.asarray(data["source_low_faces"], dtype=np.int64),
                         np.asarray(data["source_low_corner_barycentric"], dtype=float),
                         _fraction(record.get("projection_max_fraction")),
                         reference_sha256=str(record.get("reference_sha256", "")), deadline=deadline)
        if result.rule_hash != record.get("source_rule_hash"):
            raise ValueError("TARGET_PROJECTED_SOURCE_RULE_CHANGED")
        return result

    def freeze(self, arrays_path: Path) -> dict[str, object]:
        np.savez_compressed(arrays_path, original_low_triangles=self.original_low_triangles,
                            source_low_faces=self.source_low_faces,
                            source_low_corner_barycentric=self.source_low_corner_barycentric)
        return {**self.rule, "source_rule_hash": self.rule_hash, "lineage_file": arrays_path.name,
                "lineage_sha256": file_hash(arrays_path)}

    def reset_audit(self) -> None:
        self.query_count = 0
        self.invalid_count = 0
        self.distance_sum = 0.
        self.distance_max = 0.
        self.face_counts = np.zeros(len(self.source_low_faces), dtype=np.int64)
        self.face_distance_max = np.zeros(len(self.source_low_faces))
        self.high_face_counts = np.zeros(len(self.reference.faces), dtype=np.int64)
        self.witness_count = 0
        self.witnesses: list[dict[str, FloatArray | IntArray | BoolArray]] = []
        self.roi_queries = 0
        self.full_reference_queries = 0
        self.roi_outside_bounds_queries = 0
        self.roi_distance_fallback_queries = 0
        self.roi_empty_fallback_queries = 0

    def attach_audit(self, output: Path, stage: str) -> None:
        self.audit_output, self.audit_stage = output, stage
        self.reset_audit()

    def sample(self, faces: int | IntArray, bary: FloatArray,
               *, require_valid: bool = True) -> ProjectedSamples:
        bary = np.asarray(bary, dtype=float)
        ids = np.full(len(bary), faces, dtype=np.int64) if isinstance(faces, (int, np.integer)) else np.asarray(faces, dtype=np.int64)
        if (bary.ndim != 2 or bary.shape[1] != 3 or not np.isfinite(bary).all()
                or ids.shape != (len(bary),) or np.any(ids < 0) or np.any(ids >= len(self.source_low_faces))
                or np.any(bary < -1e-8) or np.any(bary > 1 + 1e-8)
                or not np.allclose(bary.sum(axis=1), 1., rtol=0, atol=1e-8)):
            raise ValueError("INVALID_PROJECTED_SOURCE_QUERY")
        low_faces = self.source_low_faces[ids]
        low_bary = np.einsum("nc,ncd->nd", bary, self.source_low_corner_barycentric[ids])
        points = np.einsum("nc,ncd->nd", low_bary, self.original_low_triangles[low_faces])
        if not len(bary):
            return ProjectedSamples(np.empty((0, 4)), np.empty(0, dtype=bool), low_faces, low_bary,
                                    points, np.empty(0, dtype=np.int64), np.empty((0, 3)), np.empty(0))
        sampled = self._sample_high(low_faces, points)
        colors = sampled.rgba_linear.copy()
        # The supported material is OPAQUE: source alpha is not coverage.
        colors[:, 3] = 1.
        valid = (sampled.distances <= self.max_distance) & np.isfinite(colors).all(axis=1)
        result = ProjectedSamples(colors, valid, low_faces, low_bary, points,
                                  sampled.target_faces, sampled.barycentric, sampled.distances)
        self._observe(ids, bary, result)
        if require_valid and not np.all(valid):
            if self.audit_output is not None:
                self.save_audit()
            raise ValueError(f"PROJECTED_SOURCE_OUT_OF_RANGE:{int((~valid).sum())} samples; "
                             f"max_distance={float(sampled.distances.max()):.9g}; limit={self.max_distance:.9g}")
        return result

    def sample_colors(self, faces: int | IntArray, bary: FloatArray) -> FloatArray:
        return self.sample(faces, bary).rgba_linear

    def _roi(self, low_face: int) -> ReferenceSurface | None:
        """Cache only the fixed original triangle bounds, never query bounds."""
        if low_face not in self.roi_cache:
            try:
                self.roi_cache[low_face] = self.reference.restrict_bounds(
                    self.roi_bounds[low_face], self.max_distance)
            except ValueError as error:
                if str(error) != "REFERENCE_INVALID: no reference faces in expanded ROI":
                    raise
                self.roi_cache[low_face] = None
        return self.roi_cache[low_face]

    def _sample_high(self, low_faces: IntArray, points: FloatArray) -> ReferenceSamples:
        """Exact valid ROI queries; invalid queries retain global nearest witnesses."""
        colors = np.empty((len(points), 4))
        valid = np.empty(len(points), dtype=bool)
        distances = np.empty(len(points))
        high_faces = np.empty(len(points), dtype=np.int64)
        high_bary = np.empty((len(points), 3))

        def assign(indices: IntArray, values: ReferenceSamples) -> None:
            colors[indices], valid[indices], distances[indices] = (
                values.rgba_linear, values.valid, values.distances)
            high_faces[indices], high_bary[indices] = values.target_faces, values.barycentric

        for low_face in np.unique(low_faces):
            indices = np.flatnonzero(low_faces == low_face)
            bounds = self.roi_bounds[low_face]
            inside = np.all(points[indices] >= bounds[0], axis=1) & np.all(points[indices] <= bounds[1], axis=1)
            self.roi_outside_bounds_queries += int((~inside).sum())
            fallback = ~inside
            roi = self._roi(int(low_face)) if inside.any() else None
            if roi is not None:
                roi_indices = indices[inside]
                sampled = roi.sample(points[roi_indices], self.max_distance, deadline=self.deadline)
                self.roi_queries += len(roi_indices)
                bounded = sampled.distances <= self.max_distance
                # An out-of-bound ROI point may have a closer excluded face.
                # Re-query globally to preserve the true failure witness.
                self.roi_distance_fallback_queries += int((~bounded).sum())
                fallback[inside] = ~bounded
                assign(roi_indices[bounded], ReferenceSamples(sampled.rgba_linear[bounded], sampled.valid[bounded],
                    sampled.distances[bounded], sampled.target_faces[bounded], sampled.barycentric[bounded]))
            else:
                self.roi_empty_fallback_queries += int(inside.sum())
                fallback[:] = True
            full_indices = indices[fallback]
            if len(full_indices):
                self.full_reference_queries += len(full_indices)
                assign(full_indices, self.reference.sample(points[full_indices], self.max_distance,
                                                            deadline=self.deadline))
        return ReferenceSamples(colors, valid, distances, high_faces, high_bary)

    def _observe(self, ids: IntArray, bary: FloatArray, values: ProjectedSamples) -> None:
        self.query_count += len(ids)
        self.invalid_count += int((~values.valid).sum())
        self.distance_sum += float(values.distances.sum())
        self.distance_max = max(self.distance_max, float(values.distances.max(initial=0.)))
        np.add.at(self.face_counts, ids, 1)
        np.maximum.at(self.face_distance_max, ids, values.distances)
        np.add.at(self.high_face_counts, values.target_faces, 1)
        count = min(len(ids), _WITNESS_LIMIT - self.witness_count)
        if count:
            self.witnesses.append({"current_faces": ids[:count].copy(), "current_barycentric": bary[:count].copy(),
                "source_low_faces": values.source_low_faces[:count].copy(),
                "source_low_barycentric": values.source_low_barycentric[:count].copy(),
                "source_points": values.source_points[:count].copy(), "high_faces": values.target_faces[:count].copy(),
                "high_barycentric": values.barycentric[:count].copy(), "distances": values.distances[:count].copy(),
                "valid": values.valid[:count].copy()})
            self.witness_count += count

    def save_audit(self) -> dict[str, object]:
        if self.audit_output is None:
            raise ValueError("PROJECTED_SOURCE_AUDIT_OUTPUT_REQUIRED")
        self.audit_output.mkdir(parents=True, exist_ok=True)
        path = self.audit_output / "source_map.npz"
        arrays: dict[str, FloatArray | IntArray | BoolArray] = {
            "current_face_sample_counts": self.face_counts, "current_face_distance_max": self.face_distance_max,
            "high_face_sample_counts": self.high_face_counts}
        if self.witnesses:
            for key in self.witnesses[0]:
                arrays[key] = cast(FloatArray | IntArray | BoolArray,
                                   np.concatenate([part[key] for part in self.witnesses]))
        # Spell out array keys: current NumPy also exposes an ``allow_pickle``
        # keyword, so a generic **mapping weakens type safety.
        if self.witnesses:
            np.savez_compressed(path,
                current_face_sample_counts=self.face_counts, current_face_distance_max=self.face_distance_max,
                high_face_sample_counts=self.high_face_counts,
                current_faces=arrays["current_faces"], current_barycentric=arrays["current_barycentric"],
                source_low_faces=arrays["source_low_faces"], source_low_barycentric=arrays["source_low_barycentric"],
                source_points=arrays["source_points"], high_faces=arrays["high_faces"],
                high_barycentric=arrays["high_barycentric"], distances=arrays["distances"], valid=arrays["valid"])
        else:
            np.savez_compressed(path, current_face_sample_counts=self.face_counts,
                                current_face_distance_max=self.face_distance_max,
                                high_face_sample_counts=self.high_face_counts)
        record: dict[str, object] = {"schema_version": 1, "stage": self.audit_stage,
            "source_rule_hash": self.rule_hash, "reference_sha256": self.reference_sha256,
            "queries": self.query_count, "invalid_queries": self.invalid_count,
            "distance_mean": self.distance_sum / self.query_count if self.query_count else 0.,
            "distance_max": self.distance_max, "max_distance": self.max_distance,
            "queried_current_faces": int(np.count_nonzero(self.face_counts)),
            "roi_queries": self.roi_queries, "full_reference_queries": self.full_reference_queries,
            "roi_outside_bounds_queries": self.roi_outside_bounds_queries,
            "roi_distance_fallback_queries": self.roi_distance_fallback_queries,
            "roi_empty_fallback_queries": self.roi_empty_fallback_queries,
            "roi_cache_scope": "fixed original-low-face AABB; independent of current UV and query history",
            "witness_count": self.witness_count, "witness_limit": _WITNESS_LIMIT,
            "witness_selection": "first actual queries in deterministic execution order; counts/maxima include all queries",
            "source_map": path.name, "source_map_sha256": file_hash(path),
            "scope": "actual original-low to original-high query witnesses; finite probes, not continuous projection bounds"}
        write_json(self.audit_output / "source_map.json", record)
        return record
