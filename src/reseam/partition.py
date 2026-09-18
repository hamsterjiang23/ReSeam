"""Independent OptCuts inside frozen initial chart boundaries."""

import time
from pathlib import Path
from typing import Any

import numpy as np

from .backends import baseline
from .mesh import chart_labels, state_from_corner_uv
from .types import MeshState
from .validation import seam_metrics, validate


def frozen_partition(
    root: Path,
    initial: MeshState,
    runtime: dict[str, Any],
    output: Path,
    seconds: float,
    seed: int,
    tau: float,
) -> tuple[MeshState, dict[str, Any]]:
    output.mkdir(parents=True, exist_ok=False)
    start = time.monotonic()
    labels = chart_labels(initial)
    corners = initial.uv[initial.uv_faces].copy()
    identities = initial.uv_faces.copy()
    next_id = len(initial.uv)
    records = []
    charts = list(np.unique(labels))
    for index, chart in enumerate(charts):
        face_ids = np.flatnonzero(labels == chart)
        remaining = seconds - (time.monotonic() - start)
        if remaining < 1:
            records.append({"chart": int(chart), "status": "NOT_RUN_BUDGET"})
            continue
        selected = initial.mesh.faces[face_ids]
        vertices = np.unique(selected)
        remap = {int(v): i for i, v in enumerate(vertices)}
        folder = output / f"chart_{int(chart)}"
        folder.mkdir()
        source = folder / "input.obj"
        with source.open("w", encoding="utf-8") as stream:
            for vertex in initial.mesh.vertices[vertices]:
                stream.write("v " + " ".join(format(float(v), ".17g") for v in vertex) + "\n")
            for face in selected:
                stream.write("f " + " ".join(str(remap[int(v)] + 1) for v in face) + "\n")
        try:
            local, metadata = baseline(
                root, source, "optcuts", runtime, folder / "solve", remaining / (len(charts) - index), seed
            )
            if not validate(local, tau)["valid"]:
                raise ValueError("Local chart failed quality bound")
            local_uv = local.uv * local.mesh.scale / initial.mesh.scale
            local_uv += corners[face_ids].mean(axis=(0, 1)) - local_uv.mean(axis=0)
            corners[face_ids] = local_uv[local.uv_faces]
            identities[face_ids] = local.uv_faces + next_id
            next_id += len(local.uv)
            records.append({"chart": int(chart), "status": "SOLVED", **metadata})
        except (RuntimeError, ValueError, TimeoutError) as error:
            records.append({"chart": int(chart), "status": "KEPT_INITIAL", "reason": str(error)})
    result = state_from_corner_uv(initial.mesh, corners, corner_ids=identities)
    valid = validate(result, tau)["valid"]
    improved = float(seam_metrics(result)["objective"]) < float(seam_metrics(initial)["objective"]) - 1e-8
    # Frozen chart boundaries must remain seams, but non-separating internal cuts may change.
    boundaries = {
        ei
        for ei in initial.seams
        if len(initial.mesh.edge_faces[ei]) == 2
        and labels[initial.mesh.edge_faces[ei][0][0]] != labels[initial.mesh.edge_faces[ei][1][0]]
    }
    if not boundaries <= result.seams:
        raise ValueError("Frozen partition boundary changed")
    return (result if valid and improved else initial.copy()), {
        "mode": "independent_optcuts_within_frozen_initial_charts",
        "charts": records,
        "accepted": bool(valid and improved),
        "seconds": time.monotonic() - start,
    }
