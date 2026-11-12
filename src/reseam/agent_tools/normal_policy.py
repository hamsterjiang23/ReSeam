"""Explicit local repair of imported normal fans rejected by native MikkTSpace."""

from pathlib import Path

import numpy as np

from ..budget_material import MikkFrameError, frame_arrays
from ..types import FloatArray, IntArray
from .state import check_deadline, require_snapshot, save_snapshot, snapshot_result, write_json


def _fan(faces: IntArray, normals: FloatArray, start: tuple[int, int]) -> list[tuple[int, int]]:
    face, corner = start
    vertex = faces[face, corner]
    candidates = {int(f): int(c) for f, c in np.argwhere(faces == vertex)
                  if np.dot(normals[f, c], normals[face, corner]) > 1 - 1e-6}
    visited, pending = {face}, [face]
    while pending:
        current = pending.pop()
        for candidate in candidates:
            if candidate not in visited and len(set(faces[current]) & set(faces[candidate])) >= 2:
                visited.add(candidate)
                pending.append(candidate)
    return [(f, candidates[f]) for f in sorted(visited)]


def repair_normals(request: dict[str, object], output: Path, deadline: float) -> dict[str, object]:
    """Repair only failed smooth fans; geometry, face order and UV stay unchanged."""
    parent = require_snapshot(request)
    state = parent.state.copy()
    surface = state.surface
    if surface.corner_normals is None:
        raise ValueError("IMPORTED_NORMAL_POLICY_REQUIRED")
    original = surface.corner_normals.copy()
    xyz = surface.mesh.vertices[surface.mesh.faces]
    geometric = np.cross(xyz[:, 1] - xyz[:, 0], xyz[:, 2] - xyz[:, 0])
    operations: list[dict[str, object]] = []
    for attempt in range(3):
        check_deadline(deadline)
        try:
            frame_arrays(surface)
            break
        except MikkFrameError as error:
            before = surface.corner_normals.copy()
            handled: set[tuple[int, int]] = set()
            for row in error.invalid_corners:
                seed = (int(row[0]), int(row[1]))
                if seed in handled:
                    continue
                fan = _fan(surface.mesh.faces, before, seed)
                value = geometric[[face for face, _ in fan]].sum(axis=0)
                if np.linalg.norm(value) < 1e-12:
                    fan = [seed]
                    value = geometric[seed[0]].copy()
                value /= np.linalg.norm(value)
                for face, corner in fan:
                    surface.corner_normals[face, corner] = value
                handled.update(fan)
                operations.append({"attempt": attempt, "failed_corner": list(seed),
                    "changed_fan": [list(corner) for corner in fan],
                    "normal": value.tolist(), "reason": "native MikkTSpace returned invalid tangent"})
    # All failures remain failures; never emit a repaired snapshot with invalid frames.
    frame_arrays(surface)
    changed = np.argwhere(np.linalg.norm(surface.corner_normals - original, axis=-1) > 1e-10)
    angles = np.degrees(np.arccos(np.clip(np.sum(surface.corner_normals * original, axis=-1), -1., 1.)))
    report: dict[str, object] = {"parent_state_hash": parent.state_hash,
        "method": "area-weighted geometric normals on only native-Mikk-failing imported smooth fans",
        "geometry_changed": False, "uv_changed": False, "operations": operations,
        "changed_corner_count": len(changed), "changed_corners": changed.tolist(),
        "changed_faces": np.unique(changed[:, 0]).tolist(),
        "angular_change_degrees": [float(angles[f, c]) for f, c in changed],
        "maximum_angular_change_degrees": float(angles.max()),
        "frame_validation": "pass", "appearance_acceptance": "NOT_RUN"}
    metadata = {**parent.metadata, "normal_repair": report,
        "normal_policy": "imported_corner_normals_with_explicit_local_fan_repair_mikktspace"}
    state.invalidate()
    child = save_snapshot(state, output / "snapshot", metadata, parent)
    write_json(output / "normal_repair.json", report)
    return {"status": "OK", **snapshot_result(child), "report": str(output / "normal_repair.json"),
        "changed_corner_count": len(changed), "appearance_acceptance": "NOT_RUN"}
