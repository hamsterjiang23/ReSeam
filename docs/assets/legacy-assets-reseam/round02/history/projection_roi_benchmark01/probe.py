"""Reproduce exact projection ROI timing; use a fresh --output JSON path per run.

Example from the repository root:
uv run python artifacts/legacy_assets_reseam/projection_roi_benchmark01/probe.py \
  --output artifacts/legacy_assets_reseam/projection_roi_benchmark02/benchmark.json
"""

import argparse
import json
import platform
import time
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import trimesh

from reseam.agent_tools.projected_source import ProjectedSource
from reseam.agent_tools.state import file_hash
from reseam.budget_texture import _stratified_barycentric, load_reference


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[3])
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    repository = Path(arguments.repo).resolve()
    output = Path(arguments.output).resolve()
    witness_path = output.with_name(output.stem + "_queries.npz")
    if output.exists() or witness_path.exists():
        raise FileExistsError("Use a fresh output path; existing benchmark evidence is immutable")
    output.parent.mkdir(parents=True, exist_ok=True)
    low_path = repository / "assets/v1/energy_blade/low.obj"
    high_path = repository / "assets/v1/energy_blade/high.glb"
    low = trimesh.load_mesh(low_path, process=False)
    if not isinstance(low, trimesh.Trimesh):
        raise TypeError("Expected one energy_blade low mesh")
    reference = load_reference(high_path)
    triangles = np.asarray(low.vertices)[np.asarray(low.faces)]
    ids = np.repeat(np.arange(len(triangles), dtype=np.int64), 20)
    bary = np.tile(_stratified_barycentric(20), (len(triangles), 1))
    sampler = ProjectedSource(reference, triangles, np.arange(len(triangles), dtype=np.int64),
        np.broadcast_to(np.eye(3), (len(triangles), 3, 3)), reference_sha256=file_hash(high_path))
    points = np.einsum("nc,ncd->nd", bary, triangles[ids])

    start = time.perf_counter()
    full = reference.sample(points, sampler.max_distance)
    full_seconds = time.perf_counter() - start
    start = time.perf_counter()
    cold = sampler.sample(ids, bary, require_valid=False)
    cold_seconds = time.perf_counter() - start
    sampler.reset_audit()
    start = time.perf_counter()
    warm = sampler.sample(ids, bary, require_valid=False)
    warm_seconds = time.perf_counter() - start
    start = time.perf_counter()
    for face in range(len(triangles)):
        reference.sample(points[20 * face:20 * (face + 1)], sampler.max_distance)
    grouped_full_seconds = time.perf_counter() - start

    same_faces = full.target_faces == warm.target_faces
    full_colors = full.rgba_linear.copy()
    full_colors[:, 3] = 1.
    roi_sizes = [len(roi.faces) for roi in sampler.roi_cache.values() if roi is not None]
    np.savez_compressed(witness_path, low_faces=ids, low_barycentric=bary, source_points=points,
        full_high_faces=full.target_faces, full_high_barycentric=full.barycentric,
        full_distances=full.distances, full_rgba_linear_opaque=full_colors,
        roi_high_faces=warm.target_faces, roi_high_barycentric=warm.barycentric,
        roi_distances=warm.distances, roi_rgba_linear=warm.rgba_linear, roi_valid=warm.valid)
    result: dict[str, object] = {
        "schema_version": 1, "measured_at_utc": datetime.now(UTC).isoformat(),
        "asset": "energy_blade", "low_faces": len(triangles), "high_faces": len(reference.faces),
        "queries": len(ids),
        "protocol": {
            "deterministic_queries": "20 _stratified_barycentric probes per original low face, original face order",
            "projection_max_fraction": sampler.max_fraction,
            "timing": "perf_counter wall seconds; one measurement each; excludes asset loading and sampler constructor",
            "cold_roi": "includes all original-face ROI cache construction and source-map observation bookkeeping",
            "warm_roi": "same queries with fixed original-face ROI cache retained; audit counters reset",
            "full_face_grouped": "one full-reference sample call per original low face, 20 points each",
            "comparison": "same original high ReferenceSurface, exact nearest points; OPAQUE alpha normalized to one",
            "scope": "CPU query microbenchmark; not end-to-end bake, quality validation, GPU or artist acceptance",
        },
        "full_one_batch_seconds": full_seconds,
        "full_face_grouped_seconds": grouped_full_seconds,
        "roi_cold_seconds": cold_seconds, "roi_warm_seconds": warm_seconds,
        "warm_speedup_vs_single_batch": full_seconds / warm_seconds,
        "warm_speedup_vs_face_grouped": grouped_full_seconds / warm_seconds,
        "max_color_abs_difference": float(np.abs(full_colors - warm.rgba_linear).max()),
        "max_projection_distance_difference": float(np.abs(full.distances - warm.distances).max()),
        "source_face_mismatches": int((~same_faces).sum()),
        "max_matching_face_barycentric_difference": (
            float(np.abs(full.barycentric[same_faces] - warm.barycentric[same_faces]).max())
            if np.any(same_faces) else None),
        "cold_warm_max_color_difference": float(np.abs(cold.rgba_linear - warm.rgba_linear).max()),
        "invalid_queries": int((~warm.valid).sum()),
        "roi_queries": sampler.roi_queries, "full_reference_queries": sampler.full_reference_queries,
        "outside_bounds": sampler.roi_outside_bounds_queries,
        "distance_fallback": sampler.roi_distance_fallback_queries,
        "empty_roi_fallback": sampler.roi_empty_fallback_queries,
        "roi_faces_min": min(roi_sizes), "roi_faces_mean": float(np.mean(roi_sizes)),
        "roi_faces_max": max(roi_sizes), "source_rule_hash": sampler.rule_hash, "rule": sampler.rule,
        "environment": {"python": platform.python_version(), "platform": platform.platform(),
                        "numpy": np.__version__, "trimesh": trimesh.__version__},
        "files": {
            "low_input": {"path": str(low_path), "sha256": file_hash(low_path)},
            "high_input": {"path": str(high_path), "sha256": file_hash(high_path)},
            "projected_source_code": {"path": "src/reseam/agent_tools/projected_source.py",
                                      "sha256": file_hash(repository / "src/reseam/agent_tools/projected_source.py")},
            "reference_code": {"path": "src/reseam/budget_texture.py",
                               "sha256": file_hash(repository / "src/reseam/budget_texture.py")},
            "probe_script": {"path": Path(__file__).name, "sha256": file_hash(Path(__file__))},
            "query_witnesses": {"path": witness_path.name, "sha256": file_hash(witness_path)},
        },
    }
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(output), "result": result}, ensure_ascii=False, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
