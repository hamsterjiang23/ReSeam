"""Blender contour packing with a common-scale float64 similarity replay."""

import json
import os
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from reseam.agent_tools.state import check_deadline, file_hash, write_json
from reseam.budget_state import BudgetState, owners_and_members
from reseam.types import FloatArray, IntArray


@dataclass(frozen=True)
class SimilarityFit:
    rotation: FloatArray
    scale: float
    source_center: FloatArray
    target_center: FloatArray
    weight: float
    maximum_error: float


def fit_similarity(source: FloatArray, target: FloatArray, *, tolerance: float = 2e-6) -> SimilarityFit:
    """Proper 2D Procrustes via complex correlation; no ill-conditioned inverse."""
    if source.shape != target.shape or source.ndim != 2 or source.shape[1] != 2:
        raise ValueError("PACK_SIMILARITY_SHAPE_MISMATCH")
    if len(source) < 3 or not np.isfinite(source).all() or not np.isfinite(target).all():
        raise ValueError("PACK_SIMILARITY_INVALID_POINTS")
    a, b = source.mean(axis=0), target.mean(axis=0)
    x, y = source - a, target - b
    weight = float(np.sum(x * x))
    dot = float(np.sum(x * y))
    cross = float(np.sum(x[:, 0] * y[:, 1] - x[:, 1] * y[:, 0]))
    amplitude = float(np.hypot(dot, cross))
    if weight <= 0.0 or not np.any(y):
        raise ValueError("PACK_SIMILARITY_COLLAPSED_OWNER")
    if amplitude <= 0.0:
        raise ValueError("PACK_NON_SIMILAR_OR_REFLECTED_OWNER")
    cosine, sine = dot / amplitude, cross / amplitude
    rotation = np.asarray([[cosine, sine], [-sine, cosine]], dtype=np.float64)
    scale = amplitude / weight
    error = float(np.max(np.linalg.norm(x @ rotation * scale + b - target, axis=1)))
    if error > tolerance:
        raise ValueError(f"PACK_NON_SIMILAR_OR_REFLECTED_OWNER:{error:.9g}")
    return SimilarityFit(rotation, scale, a, b, weight, error)


def _run_blender(argv: list[str], folder: Path, deadline: float) -> float:
    check_deadline(deadline)
    write_json(folder / "command.json", argv)
    environment = {**os.environ, "OMP_NUM_THREADS": "1", "OPENBLAS_NUM_THREADS": "1",
                   "MKL_NUM_THREADS": "1", "PYTHONUNBUFFERED": "1"}
    start = time.monotonic()
    with (folder / "process.log").open("w", encoding="utf-8") as log:
        process = subprocess.Popen(argv, stdout=log, stderr=subprocess.STDOUT, env=environment,
                                   creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        try:
            process.wait(timeout=max(.01, deadline - time.monotonic()))
        except subprocess.TimeoutExpired as error:
            if os.name == "nt":
                subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                               stdout=log, stderr=subprocess.STDOUT, check=False)
            else:
                process.kill()
            process.wait()
            raise TimeoutError("BLENDER_PACK_TIMEOUT") from error
    if process.returncode:
        raise ValueError("BLENDER_PACK_FAILED:" + str(folder / "process.log"))
    check_deadline(deadline)
    return time.monotonic() - start


def compact_pack(
    state: BudgetState, resolution: int, *, gap_pixels: float = 4.0, border_pixels: float = 4.0,
    blender: Path, worker: Path, output: Path, deadline: float,
    fit_tolerance_uv: float = 2e-6,
) -> tuple[BudgetState, dict[str, object]]:
    """Pack one master per owner and apply its same similarity to every member.

    Input density is preserved: no per-owner area normalization is performed.
    A 2-pixel safety allowance is reserved in both gap and border. Independent
    geometry validation remains mandatory, especially for approximate members
    whose footprint can extend outside their selected master's outline.
    """
    check_deadline(deadline)
    safety_pixels = 2.0
    values = np.asarray([gap_pixels, border_pixels, fit_tolerance_uv], dtype=float)
    if (not np.isfinite(values).all() or min(gap_pixels, border_pixels) < 0
            or not 0 < fit_tolerance_uv <= 1e-5 or resolution <= 2 * (border_pixels + safety_pixels)):
        raise ValueError("INVALID_COMPACT_PACKING_PROFILE")
    if not blender.is_file():
        raise FileNotFoundError("BLENDER_MISSING:" + str(blender))
    if not worker.is_file():
        raise FileNotFoundError("BLENDER_PACK_WORKER_MISSING:" + str(worker))
    owners, members = owners_and_members(state)
    unique_owners = np.unique(owners)
    owner_uv: dict[int, IntArray] = {}
    master_faces: list[int] = []
    master_owners: list[int] = []
    master_records: list[dict[str, object]] = []
    assigned: set[int] = set()
    for owner_value in unique_owners:
        check_deadline(deadline)
        owner = int(owner_value)
        face_ids = np.flatnonzero(owners == owner)
        uv_ids = np.unique(state.surface.uv_faces[face_ids])
        if assigned.intersection(map(int, uv_ids)):
            raise ValueError("UV_IDENTITIES_CROSS_PACKING_GROUPS")
        assigned.update(map(int, uv_ids))
        owner_uv[owner] = uv_ids
        master = int(members[face_ids[0]])
        for group in state.groups:
            if int(owners[group.first_faces[0]]) == owner:
                labels = np.unique(members[list(group.first_faces)])
                if len(labels) != 1:
                    raise ValueError("COMPACT_PACK_REQUIRES_SINGLE_MASTER_CHART:" + str(owner))
                master = int(labels[0])
                break
        chosen = face_ids[members[face_ids] == master]
        if not np.array_equal(chosen, np.flatnonzero(members == master)):
            raise ValueError("COMPACT_PACK_MASTER_IS_PARTIAL_CHART:" + str(owner))
        master_faces.extend(map(int, chosen))
        master_owners.extend([owner] * len(chosen))
        master_records.append({"owner": owner, "master_chart": master, "master_faces": chosen.tolist(),
                               "member_charts": np.unique(members[face_ids]).tolist()})
    if len(assigned) != len(state.surface.uv):
        raise ValueError("UNREFERENCED_UV_COPY")
    original_uv = state.surface.uv
    origin = original_uv.min(axis=0)
    extent = float(np.max(np.ptp(original_uv, axis=0)))
    if not np.isfinite(extent) or extent <= 0.0:
        raise ValueError("COMPACT_PACK_DEGENERATE_UV")
    normalized = (original_uv - origin) / extent
    selected_uv_faces = state.surface.uv_faces[master_faces]
    selected_ids, local_faces = np.unique(selected_uv_faces, return_inverse=True)
    local_faces = local_faces.reshape(-1, 3)
    interior = 1.0 - 2.0 * (border_pixels + safety_pixels) / resolution
    # Blender's contour expansion applies this margin on both facing islands.
    # Convert the requested pairwise gap to a per-island dilation; the caller
    # still measures the resulting gap independently after float64 replay.
    margin = .5 * (gap_pixels + safety_pixels) / resolution / interior
    if margin >= 1.0:
        raise ValueError("COMPACT_PACK_MARGIN_TOO_LARGE")
    folder = output / "blender_pack"
    folder.mkdir(parents=True, exist_ok=False)
    input_path = folder / "input.npz"
    np.savez_compressed(input_path, uv=normalized[selected_ids], faces=local_faces,
                        source_faces=np.asarray(master_faces), owners=np.asarray(master_owners))
    request = folder / "request.json"
    write_json(request, {"input": str(input_path.resolve()), "output": str(folder.resolve()),
                         "margin_fraction": margin})
    seconds = _run_blender([str(blender), "--background", "--factory-startup", "--threads", "1",
                            "--python-exit-code", "1", "--python", str(worker.resolve()), "--",
                            str(request.resolve())], folder, deadline)
    with np.load(folder / "packed.npz", allow_pickle=False) as arrays:
        if (not np.array_equal(arrays["faces"], local_faces)
                or not np.array_equal(arrays["source_faces"], master_faces)
                or not np.array_equal(arrays["owners"], master_owners)):
            raise ValueError("BLENDER_PACK_IDENTITY_CHANGED")
        packed = np.asarray(arrays["corner_uv"], dtype=np.float64)
    if packed.shape != (len(master_faces), 3, 2) or not np.isfinite(packed).all():
        raise ValueError("BLENDER_PACK_INVALID_CORNER_UV")
    fits: dict[int, SimilarityFit] = {}
    fit_samples: dict[int, tuple[FloatArray, FloatArray]] = {}
    for owner in map(int, unique_owners):
        check_deadline(deadline)
        selected = np.flatnonzero(np.asarray(master_owners) == owner)
        before = normalized[selected_uv_faces[selected]].reshape(-1, 2)
        after = packed[selected].reshape(-1, 2)
        fits[owner] = fit_similarity(before, after, tolerance=fit_tolerance_uv)
        fit_samples[owner] = before, after
    common_scale = sum(f.scale * f.weight for f in fits.values()) / sum(f.weight for f in fits.values())
    result = state.copy()
    records: list[dict[str, object]] = []
    for owner, fit in fits.items():
        check_deadline(deadline)
        before, after = fit_samples[owner]
        maximum_error = float(np.max(np.linalg.norm(
            (before - fit.source_center) @ fit.rotation * common_scale + fit.target_center - after, axis=1)))
        if maximum_error > fit_tolerance_uv:
            raise ValueError(f"BLENDER_PACK_NONUNIFORM_SCALE:{owner}:{maximum_error:.9g}")
        ids = owner_uv[owner]
        result.surface.uv[ids] = ((normalized[ids] - fit.source_center) @ fit.rotation * common_scale
                                  + fit.target_center)
        records.append({"owner": owner, "rotation": fit.rotation.tolist(), "raw_scale": fit.scale,
                        "common_scale": common_scale, "raw_fit_error_uv": fit.maximum_error,
                        "common_scale_fit_error_uv": maximum_error,
                        "source_center_normalized": fit.source_center.tolist(),
                        "packed_center": fit.target_center.tolist()})
    minimum, maximum = result.surface.uv.min(axis=0), result.surface.uv.max(axis=0)
    actual_extent = float(np.max(maximum - minimum))
    if actual_extent <= 0.0:
        raise ValueError("BLENDER_PACK_COLLAPSED_LAYOUT")
    final_scale = interior / actual_extent
    result.surface.uv[:] = (result.surface.uv - (minimum + maximum) * .5) * final_scale + .5
    result.invalidate()
    check_deadline(deadline)
    report: dict[str, object] = {
        "backend": "blender_concave", "backend_report": json.loads((folder / "backend.json").read_text()),
        "worker_sha256": file_hash(worker), "input_sha256": file_hash(input_path),
        "output_sha256": file_hash(folder / "packed.npz"), "blender": str(blender),
        "backend_seconds": seconds, "owner_count": len(unique_owners), "masters": master_records,
        "transforms": records, "input_origin": origin.tolist(), "input_extent": extent,
        "common_scale_normalized": common_scale, "final_border_scale": final_scale,
        "common_scale_original_uv": common_scale * final_scale / extent,
        "fit_tolerance_uv": fit_tolerance_uv, "resolution": resolution,
        "gap_pixels": gap_pixels, "border_pixels": border_pixels, "safety_pixels": safety_pixels,
        "margin_fraction": margin, "final_uv_min": result.surface.uv.min(axis=0).tolist(),
        "margin_semantics": "half requested pairwise gap plus safety, as per-island contour dilation",
        "final_uv_max": result.surface.uv.max(axis=0).tolist(),
        "density_normalize": False, "unwrap_called": False, "original_uv_identities_preserved": True,
        "independent_geometry_validation_required": True,
        "requested_margin_after_border_pixels": margin * final_scale * resolution,
        "spacing_verified": False, "uniform_scale_replay": True, "reflection_allowed": False,
        "slave_envelope_may_exceed_master": True,
    }
    write_json(output / "compact_packing.json", report)
    return result, report
