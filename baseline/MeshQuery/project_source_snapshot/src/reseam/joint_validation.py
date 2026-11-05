"""Exact UV overlap-cell authorization and independent V2 geometric/quality gates."""

import math
from typing import Any

import numpy as np
from shapely import Polygon, STRtree, union_all

from .fixed_uv import face_areas, shape_distortion
from .joint_contacts import sheet_intersections
from .joint_types import JointProfile, V2State
from .mesh import chart_labels
from .surface_match import reflect
from .types import FloatArray, MeshState
from .validation import jacobians, seam_metrics, topology_errors


def uv_barycentric(triangle: FloatArray, points: FloatArray) -> FloatArray:
    matrix = np.stack((triangle[1] - triangle[0], triangle[2] - triangle[0]), axis=1)
    coords = np.linalg.solve(matrix, (points - triangle[0]).T).T
    return np.column_stack((1 - coords.sum(axis=1), coords))


def density_objective(reference: MeshState, proposed: MeshState, resolution: int = 1024) -> dict[str, Any]:
    initial = np.abs(face_areas(reference))
    current = np.abs(face_areas(proposed))
    if np.any(initial <= 0) or np.any(current <= 0):
        raise ValueError("Degenerate UV for density comparison")
    ratio = np.sqrt(current / initial)
    weights = reference.mesh.areas / reference.mesh.areas.sum()
    gain = float(np.exp(weights @ np.log(ratio)))
    rho = resolution * np.sqrt(current / reference.mesh.areas)
    return {
        "gain": gain,
        "minimum_ratio": float(ratio.min()),
        "relative_allocation_min": float((ratio / gain).min()),
        "relative_allocation_max": float((ratio / gain).max()),
        "minimum_density": float(rho.min()),
        "mean_density": float(weights @ rho),
        "face_density": rho.tolist(),
    }


def validate_joint_geometry(state: V2State, profile: JointProfile, packed: bool = True) -> dict[str, Any]:
    surface = state.surface
    errors = topology_errors(surface)
    if errors:
        return {"valid": False, "errors": errors}
    count = len(surface.mesh.faces)
    labels = chart_labels(surface)
    owner = labels.copy()
    member = np.full(count, -1, dtype=int)
    orientation = np.ones(count, dtype=int)
    assigned: set[int] = set()
    group_by_owner = {}
    next_owner = int(labels.max()) + 1
    ids: set[str] = set()
    for group in state.groups:
        if group.first_to_second is not None:
            return {"valid": False, "errors": ["SIMILARITY_REQUIRES_BUDGET_VALIDATOR"]}
        all_faces = group.first_faces + group.second_faces
        normal = np.asarray(group.plane_normal, dtype=float)
        if (
            group.group_id in ids
            or not group.first_faces
            or not group.second_faces
            or len(set(all_faces)) != len(all_faces)
            or min(all_faces) < 0
            or max(all_faces) >= count
            or assigned.intersection(all_faces)
            or not np.isfinite(normal).all()
            or abs(float(np.linalg.norm(normal)) - 1) > 1e-8
            or not np.isfinite(group.plane_offset)
            or group.first_orientation not in (-1, 1)
            or group.second_orientation not in (-1, 1)
        ):
            return {"valid": False, "errors": ["INVALID_REUSE_GROUP"]}
        ids.add(group.group_id)
        assigned.update(all_faces)
        for side, (faces, sign) in enumerate(
            ((group.first_faces, group.first_orientation), (group.second_faces, group.second_orientation))
        ):
            chart_faces = set(map(int, np.flatnonzero(np.isin(labels, np.unique(labels[list(faces)])))))
            if chart_faces != set(faces):
                return {"valid": False, "errors": ["REUSE_MEMBER_NOT_ISOLATED"]}
            owner[list(faces)] = next_owner
            member[list(faces)] = side
            orientation[list(faces)] = sign
        group_by_owner[next_owner] = group
        next_owner += 1
    determinant = np.linalg.det(jacobians(surface)) * orientation
    if np.any(determinant <= 1e-12):
        return {"valid": False, "errors": ["FLIPPED_OR_DEGENERATE"]}
    errors.extend(sheet_intersections(surface, owner * 3 + member + 1, state.contact_classes))
    polygons = [Polygon(surface.uv[face]) for face in surface.uv_faces]
    pairs = STRtree(polygons).query(polygons, predicate="intersects")
    triangles = surface.mesh.vertices[surface.mesh.faces]
    normals = np.cross(triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0])
    normals /= np.linalg.norm(normals, axis=1)[:, None]
    diagonal = float(np.linalg.norm(np.ptp(surface.mesh.vertices, axis=0)))
    max_error = 0.0
    authorized_area = 0.0
    overlap_cells = 0
    for first, second in zip(*pairs, strict=True):
        if first >= second:
            continue
        cell = polygons[first].intersection(polygons[second])
        if cell.area <= 1e-12:
            continue
        if owner[first] != owner[second] or member[first] < 0 or member[first] == member[second]:
            errors.append(f"UNAUTHORIZED_OVERLAP:{first}:{second}")
            continue
        if not isinstance(cell, Polygon):
            errors.append("INVALID_INTERSECTION_CELL")
            continue
        group = group_by_owner[int(owner[first])]
        uv = np.asarray(cell.exterior.coords)[:-1]
        point_a = uv_barycentric(surface.uv[surface.uv_faces[first]], uv) @ triangles[first]
        point_b = uv_barycentric(surface.uv[surface.uv_faces[second]], uv) @ triangles[second]
        normal = np.asarray(group.plane_normal)
        error = (
            float(np.max(np.linalg.norm(reflect(point_a, normal, group.plane_offset) - point_b, axis=1)))
            / diagonal
        )
        angle = float(
            np.degrees(np.arccos(np.clip(reflect(normals[first], normal, 0) @ normals[second], -1, 1)))
        )
        max_error = max(max_error, error)
        if error > profile.correspondence_fraction + 1e-10 or angle > profile.normal_angle_degrees + 1e-8:
            errors.append(f"OVERLAP_CORRESPONDENCE:{first}:{second}")
        else:
            authorized_area += float(cell.area)
            overlap_cells += 1
    shapes = [
        union_all([polygons[int(i)] for i in np.flatnonzero(owner == label)]) for label in np.unique(owner)
    ]
    gap = float("inf")
    for i, shape in enumerate(shapes):
        for other in shapes[i + 1 :]:
            gap = min(gap, float(shape.distance(other)) * profile.resolution)
    border = float(min(surface.uv.min(), (1 - surface.uv).min()) * profile.resolution)
    if packed and (gap < profile.gap_pixels - 1e-5 or border < profile.border_pixels - 1e-5):
        errors.append("PACKING_GAP_OR_BORDER")
    unique = float(union_all(polygons).area)
    total = float(np.abs(face_areas(surface)).sum())
    return {
        "valid": not errors,
        "errors": errors,
        "unique_coverage": unique,
        "summed_uv_area": total,
        "reuse_factor": total / unique if unique else None,
        "authorized_overlap_area": authorized_area,
        "overlap_cells": overlap_cells,
        "max_overlap_geometry_error": max_error,
        "minimum_gap_pixels": gap if math.isfinite(gap) else None,
        "minimum_border_pixels": border,
        "packing_groups": len(shapes),
    }


def validate_joint_quality(reference: MeshState, proposed: V2State, profile: JointProfile) -> dict[str, Any]:
    current = proposed.surface
    if not np.array_equal(current.mesh.vertices, reference.mesh.vertices) or not np.array_equal(
        current.mesh.faces, reference.mesh.faces
    ):
        return {"valid": False, "errors": ["ORIGINAL_GEOMETRY_CHANGED"]}
    quality = validate_joint_geometry(proposed, profile)
    if not quality["valid"]:
        return quality
    coupling = validate_coupling(proposed, profile)
    metrics = density_objective(reference, current, profile.resolution)
    errors = quality["errors"]
    errors.extend(coupling["errors"])
    before = np.linalg.svd(jacobians(reference), compute_uv=False)
    after = np.linalg.svd(jacobians(current), compute_uv=False)
    anisotropy = float(np.max((after[:, 0] / after[:, 1]) / (before[:, 0] / before[:, 1])))
    anisotropy_faces = np.flatnonzero(
        (after[:, 0] / after[:, 1]) / (before[:, 0] / before[:, 1]) > profile.anisotropy_ratio_limit + 1e-8
    )
    if anisotropy > profile.anisotropy_ratio_limit + 1e-8:
        errors.append("ANISOTROPY_LIMIT")
    distortion = shape_distortion(current)
    if distortion > profile.distortion_limit + 1e-9:
        errors.append("DISTORTION_LIMIT")
    if metrics["minimum_ratio"] < profile.density_floor_ratio:
        errors.append("DENSITY_FLOOR")
    if (
        metrics["relative_allocation_min"] < 1 - profile.density_allocation_tolerance
        or metrics["relative_allocation_max"] > 1 + profile.density_allocation_tolerance
    ):
        errors.append("DENSITY_ALLOCATION")
    old, new = seam_metrics(reference), seam_metrics(current)
    if (
        float(new["seam_length_normalized"])
        > profile.seam_ratio_limit * float(old["seam_length_normalized"]) + 1e-9
    ):
        errors.append("SEAM_BUDGET")
    if int(new["charts"]) > max(int(old["charts"]) + 8, math.ceil(1.5 * int(old["charts"]))):
        errors.append("CHART_BUDGET")
    return {
        **quality,
        "group_coupling": coupling.get("group_coupling", []),
        **metrics,
        "valid": not errors,
        "errors": errors,
        "shape_distortion": distortion,
        "anisotropy_ratio_max": anisotropy,
        "anisotropy_failed_faces": anisotropy_faces.tolist(),
        **new,
    }


def validate_coupling(state: V2State, profile: JointProfile) -> dict[str, Any]:
    owners = {}
    for group in state.groups:
        for fi in group.first_faces + group.second_faces:
            owners[fi] = group.group_id
    records: dict[str, list[tuple[float, float, int]]] = {g.group_id: [] for g in state.groups}
    for item in state.correspondences:
        group_id = owners.get(item.source_face)
        if group_id is None or owners.get(item.target_face) != group_id:
            return {"valid": False, "errors": ["CORRESPONDENCE_OUTSIDE_GROUP"]}
        group = next(g for g in state.groups if g.group_id == group_id)
        if (item.source_face in group.first_faces) == (item.target_face in group.first_faces):
            return {"valid": False, "errors": ["CORRESPONDENCE_SAME_MEMBER"]}
        if not np.isfinite(item.weight) or item.weight <= 0:
            return {"valid": False, "errors": ["INVALID_CORRESPONDENCE_WEIGHT"]}
        for bary in (item.source_barycentric, item.target_barycentric):
            if (
                len(bary) != 3
                or not np.isfinite(bary).all()
                or min(bary) < -1e-10
                or abs(sum(bary) - 1) > 1e-8
            ):
                return {"valid": False, "errors": ["INVALID_CORRESPONDENCE_BARYCENTRIC"]}
        a = np.asarray(item.source_barycentric) @ state.surface.uv[state.surface.uv_faces[item.source_face]]
        b = np.asarray(item.target_barycentric) @ state.surface.uv[state.surface.uv_faces[item.target_face]]
        records[group_id].append(
            (float(np.linalg.norm(a - b)) * profile.resolution, item.weight, item.source_face)
        )
    metrics = []
    errors = []
    for group in state.groups:
        values = records[group.group_id]
        if {x[2] for x in values} != set(group.first_faces + group.second_faces):
            errors.append("INCOMPLETE_GROUP_CORRESPONDENCE:" + group.group_id)
            continue
        ordered = sorted(values)
        cumulative = np.cumsum([x[1] for x in ordered])
        cutoff = 0.95 * cumulative[-1]
        p95 = ordered[min(int(np.searchsorted(cumulative, cutoff)), len(ordered) - 1)][0]
        metrics.append(
            {
                "group_id": group.group_id,
                "p95_pixels": p95,
                "max_pixels": ordered[-1][0],
                "over_limit_source_faces": sorted({x[2] for x in values if x[0] > 1 + 1e-6}),
            }
        )
        if p95 > 1 + 1e-6:
            errors.append("COUPLING_PIXEL_LIMIT:" + group.group_id)
    return {"valid": not errors, "errors": errors, "group_coupling": metrics}
