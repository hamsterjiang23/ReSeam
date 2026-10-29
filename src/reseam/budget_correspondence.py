"""UV-induced surface-correspondence proposals with explicit original identities.

The finite samples below are evidence for a proposed association, not a global
bijection certificate.  Exact overlap-cell and member-injectivity validation
remain independent acceptance steps in the complete solver.
"""

import math
import time
from dataclasses import dataclass

import numpy as np

from .budget_state import BudgetState
from .joint_types import Correspondence
from .surface_match import reflect
from .types import FloatArray

_PROBES = np.asarray(
    [[1, 0, 0], [0, 1, 0], [0, 0, 1], [1 / 3, 1 / 3, 1 / 3],
     [0.6, 0.3, 0.1], [0.2, 0.6, 0.2], [0.3, 0.2, 0.5]],
    dtype=np.float64,
)


@dataclass
class CorrespondenceUpdate:
    state: BudgetState | None
    status: str
    records: tuple[dict[str, object], ...]
    diagnostics: dict[str, object]


def _check_deadline(deadline: float | None) -> None:
    if deadline is not None and time.monotonic() >= deadline:
        raise TimeoutError("CORRESPONDENCE_UPDATE_BUDGET_EXHAUSTED")


def _uv_hits(
    point: FloatArray, faces: tuple[int, ...], triangles: FloatArray,
    tolerance: float = 1e-10,
) -> list[tuple[int, FloatArray]]:
    """Locate only containing UV triangles; no nearest-point fallback."""
    p = triangles[list(faces)]
    low, high = p.min(axis=1), p.max(axis=1)
    eligible = np.flatnonzero(np.all(point >= low - tolerance, axis=1)
                              & np.all(point <= high + tolerance, axis=1))
    result = []
    for index in eligible:
        triangle = p[index]
        matrix = np.column_stack((triangle[1] - triangle[0], triangle[2] - triangle[0]))
        if abs(float(np.linalg.det(matrix))) <= 1e-20:
            raise ValueError("DEGENERATE_UV_MEMBER")
        tail = np.linalg.solve(matrix, point - triangle[0])
        bary = np.asarray([1 - float(tail.sum()), float(tail[0]), float(tail[1])])
        if np.all(bary >= -tolerance) and np.all(bary <= 1 + tolerance):
            bary = np.clip(bary, 0.0, 1.0)
            bary /= bary.sum()
            result.append((faces[int(index)], bary))
    return result


def _ambiguous_surface_hits(
    hits: list[tuple[int, FloatArray]], geometry: FloatArray, tolerance: float,
) -> bool:
    if len(hits) < 2:
        return False
    positions = np.asarray([bary @ geometry[face] for face, bary in hits])
    # Multiple adjacent faces at one source vertex/edge are ordinary.  Distinct
    # 3D points under the same member UV are not resolved by a nearest choice.
    return bool(np.linalg.norm(positions - positions[0], axis=1).max() > tolerance)


def update_uv_correspondences(
    state: BudgetState,
    *,
    position_fraction: float = 0.005,
    cycle_fraction: float = 0.005,
    normal_angle_degrees: float = 45,
    require_face_coverage: bool = True,
    deadline: float | None = None,
) -> CorrespondenceUpdate:
    """Replace associations only in a complete candidate copy after all probes.

    Source and target are original face IDs with barycentrics.  A source corner
    additionally records its original vertex ID.  Both position and UV-induced
    round-trip distances use the complete original bounding-box diagonal, even
    when only a small group is sampled.  Points outside the opposite UV domain
    are explicitly NO_SHARED_DOMAIN, rather than invented nearest associations.
    """
    limits = (position_fraction, cycle_fraction, normal_angle_degrees)
    if not all(math.isfinite(x) and x > 0 for x in limits) or normal_angle_degrees > 180:
        raise ValueError("Positive finite correspondence limits required")
    if deadline is not None and not math.isfinite(deadline):
        raise ValueError("Finite absolute deadline required")
    _check_deadline(deadline)
    diagnostics: dict[str, object] = {
        "scope": "fixed_corner_and_interior_UV_induced_correspondence_proposal",
        "global_bijection_proven": False,
        "exact_geometry_validation": "CALLER_REQUIRED",
        "probes_per_face": len(_PROBES),
        "position_fraction_limit": position_fraction,
        "cycle_fraction_limit": cycle_fraction,
        "normal_angle_degrees_limit": normal_angle_degrees,
    }
    if not state.groups:
        return CorrespondenceUpdate(None, "NO_SHARED_GROUPS", (), diagnostics)
    mesh = state.surface.mesh
    diagonal = float(np.linalg.norm(np.ptp(mesh.vertices, axis=0)))
    if diagonal <= 0 or not math.isfinite(diagonal):
        raise ValueError("Invalid complete-original-mesh normalization scale")
    diagnostics["normalization"] = "complete_original_mesh_bbox_diagonal"
    diagnostics["normalization_scale"] = diagonal
    geometry = mesh.vertices[mesh.faces]
    uv_triangles = state.surface.uv[state.surface.uv_faces]
    raw_normal = np.cross(geometry[:, 1] - geometry[:, 0], geometry[:, 2] - geometry[:, 0])
    normals = raw_normal / np.maximum(np.linalg.norm(raw_normal, axis=1)[:, None], 1e-30)
    associations: list[Correspondence] = []
    records: list[dict[str, object]] = []
    failed: list[str] = []
    face_coverage: set[int] = set()
    required: set[int] = set()
    group_names: set[str] = set()
    maximum_error, maximum_cycle, maximum_angle = 0.0, 0.0, 0.0
    for group in state.groups:
        _check_deadline(deadline)
        all_faces = group.first_faces + group.second_faces
        normal = np.asarray(group.plane_normal, dtype=float)
        if (not group.first_faces or not group.second_faces or len(set(all_faces)) != len(all_faces)
                or min(all_faces) < 0 or max(all_faces) >= len(mesh.faces)
                or required.intersection(all_faces) or group.group_id in group_names
                or normal.shape != (3,) or not np.isfinite(normal).all()
                or abs(float(np.linalg.norm(normal)) - 1) > 1e-8
                or not math.isfinite(group.plane_offset)):
            failed.append("INVALID_SHARED_GROUP")
            break
        required.update(all_faces)
        group_names.add(group.group_id)
        for sources, targets in ((group.first_faces, group.second_faces),
                                 (group.second_faces, group.first_faces)):
            for source in sources:
                _check_deadline(deadline)
                for sample_index, source_bary in enumerate(_PROBES):
                    source_point = source_bary @ geometry[source]
                    q = source_bary @ uv_triangles[source]
                    record: dict[str, object] = {
                        "group_id": group.group_id,
                        "source_face": source,
                        "source_vertex_id": int(mesh.faces[source, sample_index]) if sample_index < 3 else None,
                        "source_barycentric": source_bary.tolist(),
                        "sample_index": sample_index,
                        "uv": q.tolist(),
                        "association": "final_piecewise_affine_UV_containment",
                    }
                    records.append(record)
                    hits = _uv_hits(q, targets, uv_triangles)
                    if not hits:
                        record.update(status="NO_SHARED_DOMAIN", valid=False, target_face=None,
                                      target_barycentric=None)
                        continue
                    if _ambiguous_surface_hits(hits, geometry, max(diagonal * 1e-9, 1e-12)):
                        record.update(status="AMBIGUOUS_TARGET_UV", valid=False,
                                      target_face_ids=[face for face, _ in hits])
                        failed.append("AMBIGUOUS_TARGET_UV")
                        continue
                    # Shared-edge ties have the same 3D point; deterministic
                    # original-face ordering avoids changing the association.
                    target, target_bary = min(hits, key=lambda item: item[0])
                    target_point = target_bary @ geometry[target]
                    q_back = target_bary @ uv_triangles[target]
                    back_hits = _uv_hits(q_back, sources, uv_triangles)
                    if not back_hits or _ambiguous_surface_hits(
                        back_hits, geometry, max(diagonal * 1e-9, 1e-12)
                    ):
                        record.update(status="AMBIGUOUS_OR_MISSING_UV_ROUND_TRIP", valid=False,
                                      target_face=target, target_barycentric=target_bary.tolist())
                        failed.append("AMBIGUOUS_OR_MISSING_UV_ROUND_TRIP")
                        continue
                    back_face, back_bary = min(back_hits, key=lambda item: item[0])
                    back_point = back_bary @ geometry[back_face]
                    position = float(np.linalg.norm(reflect(source_point, normal, group.plane_offset)
                                                    - target_point) / diagonal)
                    cycle = float(np.linalg.norm(back_point - source_point) / diagonal)
                    angle = float(np.degrees(np.arccos(np.clip(
                        reflect(normals[source], normal, 0) @ normals[target], -1, 1))))
                    maximum_error = max(maximum_error, position)
                    maximum_cycle = max(maximum_cycle, cycle)
                    maximum_angle = max(maximum_angle, angle)
                    valid = (position <= position_fraction + 1e-10
                             and cycle <= cycle_fraction + 1e-10
                             and angle <= normal_angle_degrees + 1e-8)
                    record.update(
                        status="COMPATIBLE_UV_ASSOCIATION" if valid else "GEOMETRY_REJECTED",
                        valid=valid, target_face=target, target_barycentric=target_bary.tolist(),
                        target_point=target_point.tolist(), position_error_fraction=position,
                        cycle_error_fraction=cycle, normal_angle_degrees=angle,
                        round_trip_source_face=back_face,
                        round_trip_source_barycentric=back_bary.tolist(),
                        uv_residual=float(np.linalg.norm(q_back - q)),
                        cycle_definition="source_UV_to_target_PL_surface_then_same_UV_to_source_PL_surface",
                    )
                    if valid:
                        associations.append(Correspondence(
                            source, (float(source_bary[0]), float(source_bary[1]), float(source_bary[2])),
                            target, (float(target_bary[0]), float(target_bary[1]), float(target_bary[2])),
                            float(mesh.areas[source]) / len(_PROBES),
                        ))
                        face_coverage.add(source)
                    else:
                        failed.append("SAMPLED_GEOMETRY_INCOMPATIBLE")
    _check_deadline(deadline)
    uncovered = sorted(required - face_coverage)
    if require_face_coverage and uncovered:
        failed.append("INCOMPLETE_SOURCE_FACE_COVERAGE")
    diagnostics.update(
        association_count=len(associations), bidirectional_query_count=len(records),
        unmatched_domain_samples=sum(r["status"] == "NO_SHARED_DOMAIN" for r in records),
        source_faces_covered=sorted(face_coverage), uncovered_source_faces=uncovered,
        maximum_position_error_fraction=maximum_error, maximum_cycle_error_fraction=maximum_cycle,
        maximum_normal_angle_degrees=maximum_angle, errors=sorted(set(failed)),
        independent_pair_count=None,
    )
    if failed:
        return CorrespondenceUpdate(None, "CORRESPONDENCE_REJECTED", tuple(records), diagnostics)
    candidate = state.copy()
    candidate.invalidate()
    candidate.correspondences = tuple(associations)
    candidate.correspondence_records = tuple(records)
    return CorrespondenceUpdate(candidate, "PROPOSED_NOT_VALIDATED", tuple(records), diagnostics)
