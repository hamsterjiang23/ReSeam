"""Snapshot-bound UV sharing measurements after complete hard-geometry validation."""

import numpy as np
from shapely import Polygon, STRtree, union_all
from shapely.geometry.base import BaseGeometry

from ..mesh import chart_labels
from .state import Snapshot, check_deadline, file_hash, object_hash


def _covered_area(polygon: Polygon, cells: list[BaseGeometry], original_area: float) -> float:
    """A face contributes its intersection UNION, never the sum of pairwise cells."""
    # A union of subsets cannot exceed the triangle. Clamp only arithmetic overshoot;
    # illegal member overlap has already failed the independent hard validator.
    fraction = min(1.0, float(union_all(cells).area) / float(polygon.area))
    return original_area * fraction


def measure_uv_metrics(
    snapshot: Snapshot, geometry: dict[str, object], selection: list[int], deadline: float,
) -> dict[str, object]:
    """Count all-atlas overlap components; selection changes only the area denominator.

    The caller must supply the freshly computed all-original-face geometry report
    for this snapshot. A failed hard gate produces explicit unverified/null metrics.
    Quality and material acceptance remain independent of these measurements.
    """
    check_deadline(deadline)
    state = snapshot.state
    labels = chart_labels(state.surface)
    areas = state.surface.mesh.areas
    total_area = float(areas.sum())
    selected_area = float(areas[selection].sum())
    protocol = geometry.get("geometry_protocol")
    preserved = geometry.get("preserved_source_conflicts", [])
    result: dict[str, object] = {
        "schema_version": 1, "state_hash": snapshot.state_hash, "asset_id": snapshot.asset_id,
        "source_snapshot": str((snapshot.root / "snapshot.json").resolve()),
        "source_snapshot_sha256": file_hash(snapshot.root / "snapshot.json"),
        "geometry_report_object_sha256": object_hash(geometry),
        "resolution": protocol.get("resolution") if isinstance(protocol, dict) else None,
        "metrics_verified": False, "verification_reason": "FULL_MODEL_HARD_VALIDATION_REQUIRED",
        "full_geometry_passed": geometry.get("valid") is True,
        "hard_geometry_passed": geometry.get("hard_valid") is True,
        "quality_constraints_checked": geometry.get("quality_constraints_checked") is True,
        "strict_hard_geometry_passed": geometry.get("strict_hard_valid", geometry.get("hard_valid")) is True,
        "geometry_acceptance_scope": geometry.get("geometry_acceptance_scope", "strict_hard_geometry"),
        "preserved_source_conflict_count": len(preserved) if isinstance(preserved, list) else 0,
        "uv_connected_charts": len(np.unique(labels)), "packing_owners": geometry.get("packing_groups"),
        "actual_independent_tiles": None, "authorized_positive_overlap_cells": None,
        "global": {"face_count": len(areas), "surface_area": total_area,
                   "actual_shared_surface_area": None, "actual_shared_surface_fraction": None},
        "selection": {"face_count": len(selection), "face_ids_sha256": object_hash(selection),
                      "surface_area": selected_area, "actual_shared_surface_area": None,
                      "actual_shared_surface_fraction": None},
        "measurement_method": "All UV charts form DSU nodes; join only authorized compatible strict-positive "
            "intersection cells after full hard validation. Per-face intersection UNION / UV face area "
            "times original3D face area; both members count. Selection never limits global geometry or tile count.",
        "positive_overlap_area_floor": 0.0, "material_validated": False,
    }
    if geometry.get("hard_valid") is not True or geometry.get("geometric_scope") != "all_original_faces":
        return result

    parents = {int(label): int(label) for label in np.unique(labels)}

    def find(label: int) -> int:
        while parents[label] != label:
            parents[label] = parents[parents[label]]
            label = parents[label]
        return label

    members = {face for group in state.groups for face in group.first_faces + group.second_faces}
    corners = state.surface.uv[state.surface.uv_faces]
    polygons = {face: Polygon(corners[face]) for face in members}
    pieces: dict[int, list[BaseGeometry]] = {}
    positive_cells = 0
    for group in state.groups:
        check_deadline(deadline)
        second = list(group.second_faces)
        tree = STRtree([polygons[face] for face in second])
        for index, first in enumerate(group.first_faces):
            if index % 64 == 0:
                check_deadline(deadline)
            for target_index in tree.query(polygons[first]):
                other = second[int(target_index)]
                # Match the independent validator's stable face order, including GEOS rounding.
                low, high = sorted((first, other))
                cell = polygons[low].intersection(polygons[high])
                if cell.area <= 0:
                    continue
                positive_cells += 1
                parents[find(int(labels[other]))] = find(int(labels[first]))
                pieces.setdefault(first, []).append(cell)
                pieces.setdefault(other, []).append(cell)
    if positive_cells != geometry.get("overlap_cells"):
        result["verification_reason"] = "INTERSECTION_CELL_COUNT_MISMATCH"
        return result
    tiles = len({find(label) for label in parents})
    owners = geometry.get("packing_groups")
    if type(owners) is not int or tiles < owners:
        result["verification_reason"] = "TILE_OWNER_CONSISTENCY_FAILED"
        return result
    coverage = np.zeros(len(areas), dtype=float)
    for index, (face, cells) in enumerate(pieces.items()):
        if index % 64 == 0:
            check_deadline(deadline)
        coverage[face] = _covered_area(polygons[face], cells, float(areas[face]))
    shared_area = float(coverage.sum())
    selected_shared_area = float(coverage[selection].sum())
    result.update({
        "metrics_verified": True, "verification_reason": (
            "SOURCE_UV_PRESERVED_NEW_CONFLICTS_REJECTED" if "source_uv_preservation" in geometry
            else "FULL_MODEL_HARD_GEOMETRY_PASSED"),
        "actual_independent_tiles": tiles, "authorized_positive_overlap_cells": positive_cells,
        "global_metrics_scope": "all_original_faces",
        "global": {"face_count": len(areas), "surface_area": total_area,
                   "actual_shared_surface_area": shared_area,
                   "actual_shared_surface_fraction": shared_area / total_area},
        "selection": {"face_count": len(selection), "face_ids_sha256": object_hash(selection),
                      "surface_area": selected_area, "actual_shared_surface_area": selected_shared_area,
                      "actual_shared_surface_fraction": selected_shared_area / selected_area},
    })
    check_deadline(deadline)
    return result
