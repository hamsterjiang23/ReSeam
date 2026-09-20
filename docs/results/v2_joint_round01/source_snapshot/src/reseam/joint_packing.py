"""Packing proposals transform complete reuse groups; independent quality remains authoritative."""

import json
import time
from pathlib import Path
from typing import Any

import numpy as np
from shapely import Polygon, union_all

from .fixed_uv import FixedAtlas, PackingProfile, face_areas, search_scale
from .joint_types import JointProfile, V2State
from .joint_validation import validate_joint_geometry
from .mesh import chart_labels
from .pipeline import write_json
from .runtime import read_runtime, run_process


def group_atlas(state: V2State) -> FixedAtlas:
    surface = state.surface
    labels = chart_labels(surface)
    next_label = int(labels.max()) + 1
    for group in state.groups:
        labels[list(group.first_faces + group.second_faces)] = next_label
        next_label += 1
    groups = [np.unique(surface.uv_faces[labels == label]) for label in np.unique(labels)]
    if sum(len(ids) for ids in groups) != len(surface.uv):
        raise ValueError("UV identities cross packing groups")
    centers = np.asarray([surface.uv[ids].mean(axis=0) for ids in groups])
    canonical = surface.uv.copy()
    for ids, center in zip(groups, centers, strict=True):
        canonical[ids] -= center
    shapes = [
        union_all([Polygon(canonical[face]) for face in surface.uv_faces[labels == label]])
        for label in np.unique(labels)
    ]
    # Normalize by unique group area, not duplicated surface area.
    scale = float(np.sqrt(sum(shape.area for shape in shapes)))
    canonical /= scale
    shapes = [
        union_all([Polygon(canonical[face]) for face in surface.uv_faces[labels == label]])
        for label in np.unique(labels)
    ]
    return FixedAtlas(surface.copy(), canonical, groups, shapes, scale, centers)


def pack_joint(
    root: Path, state: V2State, folder: Path, seconds: float, seed: int, profile: JointProfile, trial: int = 0
) -> tuple[V2State | None, dict[str, Any]]:
    started = time.monotonic()
    deadline = started + seconds
    folder.mkdir(parents=True, exist_ok=False)
    atlas = group_atlas(state)
    rng = np.random.default_rng(np.random.SeedSequence([seed, trial]))
    angles = np.zeros(len(atlas.groups)) if trial == 0 else rng.uniform(-np.pi, np.pi, len(atlas.groups))
    # Give different declared groups disjoint input positions; merge_overlap only joins members.
    centers = np.asarray([[i * 2.0, 0.0] for i in range(len(atlas.groups))])
    scattered = atlas.reconstruct(angles, centers, atlas.scale)
    request = {
        "mode": "pack_groups",
        "vertices": state.surface.mesh.vertices.tolist(),
        "faces": state.surface.mesh.faces.tolist(),
        "corner_uv": scattered.uv[scattered.uv_faces].tolist(),
        "margin": (profile.gap_pixels / 2 + 1) / profile.resolution,
        "output": str(folder / "response.json"),
    }
    write_json(folder / "request.json", request)
    runtime = read_runtime(root)
    run_process(
        [
            runtime["blender"],
            "-b",
            "--factory-startup",
            "--threads",
            "4",
            "--python-exit-code",
            "1",
            "--python",
            str(root / "scripts/joint_blender_worker.py"),
            "--",
            str(folder / "request.json"),
        ],
        folder,
        max(0.1, seconds - 3),
    )
    response = json.loads((folder / "response.json").read_text())
    corners = np.asarray(response["corner_uv"], dtype=float)
    if corners.shape != state.surface.uv_faces.shape + (2,):
        raise ValueError("BACKEND_CORNER_SHAPE")
    raw = state.surface.uv.copy()
    raw[state.surface.uv_faces.reshape(-1)] = corners.reshape(-1, 2)
    if not np.allclose(raw[state.surface.uv_faces], corners, atol=1e-6, rtol=0):
        raise ValueError("BACKEND_CHANGED_UV_CONNECTIVITY")
    angles, centers, residual = atlas.fit_poses(raw)
    pack_profile = PackingProfile(profile.resolution, profile.gap_pixels, profile.border_pixels)
    scale = search_scale(atlas, angles, centers, pack_profile, 1e-4, deadline - 1)
    if scale is None:
        return None, {"status": "NO_FEASIBLE_SCALE_FOUND", "seconds": time.monotonic() - started}
    candidate = state.copy()
    candidate.surface = atlas.reconstruct(angles, centers, scale)
    quality = validate_joint_geometry(candidate, profile)
    write_json(
        folder / "packing.json",
        {
            "quality": quality,
            "raw_fit_max_error": max(residual),
            "scale": scale,
            "surface_area": float(np.abs(face_areas(candidate.surface)).sum()),
        },
    )
    return (candidate if quality["valid"] and time.monotonic() <= deadline else None), {
        "status": "PACKED" if quality["valid"] else "PACKING_REJECTED",
        "quality": quality,
        "seconds": time.monotonic() - started,
    }
