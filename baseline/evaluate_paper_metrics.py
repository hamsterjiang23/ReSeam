"""Evaluate retained snapshots; never rerun or modify the UV generation pipeline."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import time
from pathlib import Path
from typing import cast

import numpy as np
from shapely import Polygon, box, union_all

from baseline.paper_metrics import AOScene, chart_metrics
from baseline.report import distortion_metrics
from reseam.agent_tools.state import load_snapshot
from reseam.mesh import chart_labels
from reseam.types import MeshState

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "docs/assets/dragon-paper-baselines"
OUT = DATA / "with-original-input"


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save_json(path: Path, data: object) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def base_metrics(surface: MeshState, resolution: int) -> dict[str, object]:
    labels = chart_labels(surface)
    triangles = surface.uv[surface.uv_faces]
    delta = triangles[:, 1:] - triangles[:, :1]
    det = delta[:, 0, 0] * delta[:, 1, 1] - delta[:, 0, 1] * delta[:, 1, 0]
    area = abs(det) / 2
    merged = union_all([Polygon(triangle) for triangle in triangles])
    inside = float(merged.intersection(box(0, 0, 1, 1)).area)
    total = float(area.sum())
    mixed = 0
    for label in np.unique(labels):
        ids = np.flatnonzero(labels == label)
        pos, neg = float(area[ids][det[ids] > 0].sum()), float(area[ids][det[ids] < 0].sum())
        mixed += int(np.count_nonzero(det[ids] < 0 if pos >= neg else det[ids] > 0))
    return {"faces": len(surface.mesh.faces), "vertices": len(surface.mesh.vertices),
            "charts": len(np.unique(labels)), "seam_edge_count": len(surface.seams),
            "summed_uv_area": total, "raw_uv_union_area": float(merged.area),
            "unit_square_coverage": inside, "uv_outside_unit_square_area": max(0., float(merged.area) - inside),
            "all_overlap_fraction_not_validated_reuse": max(0., 1 - float(merged.area) / total) if total else None,
            "zero_uv_area_faces": int(np.count_nonzero(abs(det) < 1e-18)),
            "mixed_orientation_faces": mixed,
            "aggregate_texel_density": math.sqrt(total / float(surface.mesh.areas.sum())) * resolution,
            "distortion": distortion_metrics(surface)}


def run(output: Path, samples: int, timeout: float) -> None:
    if output.exists():
        raise FileExistsError("Use a new metric round directory")
    output.mkdir(parents=True)
    source_path = DATA / "measurements.json"
    original_path = OUT / "comparison-evidence.json"
    source = json.loads(source_path.read_text(encoding="utf-8"))
    origin = json.loads(original_path.read_text(encoding="utf-8"))["original"]
    cases = [{"method": "original_tripo", "snapshot": origin["snapshot"], "resolution": 8192,
              "charts": origin["charts"], "quality_accepted": None}, *source["rows"]]
    protocol = {
        "paper": "https://arxiv.org/html/2610.02507v1#A1.SS6", "scope": "single-dragon read-only metrics",
        "seed": 17, "ao_samples_per_population": samples, "ao_cosine_rays_per_point": 8,
        "ao_arc_sampling": "equal-length strata centers on stable edge-ID order",
        "ao_ray_sequence": "NumPy default_rng seed17; shared local cosine directions for equal sample indices",
        "ao_reference": "all original physical mesh edges; same reference cached per exact geometry hash",
        "ao_primary_scope": "explicit state.seams, physical edges counted once; empty set is N/A",
        "ao_additional_scope": "explicit seams UNION original one-face boundary edges, counted once",
        "ao_origin_offset_bbox_diagonal": 1e-6, "ao_min_hit_distance_bbox_diagonal": 1e-9,
        "ao_max_hit_distance_bbox_diagonal": 2,
        "ao_distance_reason": "covers entire finite mesh from a surface-origin sample, not A.3 local .3d attribute",
        "ao_edge_normal": "mean unit incident face normals, normalized; first incident normal if cancellation",
        "ao_hit_test": "existing ReSeam NumPy BVH, two-sided triangles; no geometry repair",
        "non_disk": "per-chart Euler V-E+F != 1 on actual UV vertex identities",
        "chart_shape": "sum absolute triangle UV areas; topological boundary length; arithmetic mean over charts",
        "jaggedness_spacing": .005, "jaggedness_count": "ceil(perimeter/.005), clamped 8..4096",
        "jaggedness_aggregation": "all resampled boundary-point second differences pooled; raw UV units",
        "jaggedness_unpublished_scale": "no multiplier to force agreement with paper Table1 values",
        "invalid_boundaries": "skip non-cycle boundary components and report counts; no quality upgrade",
        "distortion": "reuse frozen baseline.report.distortion_metrics; angular orthogonality added per A.6",
        "original_reference": "different original geometry; no cross-geometry color or timing imputation",
        "timeout_per_case_seconds": timeout,
        "measurement_sha256": digest(source_path), "original_evidence_sha256": digest(original_path),
        "code_sha256": {name: digest(ROOT / name) for name in ["baseline/paper_metrics.py",
                          "baseline/evaluate_paper_metrics.py", "baseline/report.py",
                          "src/reseam/agent_tools/mesh_query.py"]},
        "project_choices": "formula-based project implementation; official evaluation code was not available",
    }
    save_json(output / "protocol.json", protocol)
    scenes: dict[str, AOScene] = {}
    rows: list[dict[str, object]] = []
    for case in cases:
        started = time.monotonic()
        name = str(case["method"])
        print(f"START {name}", flush=True)
        path = Path(case["snapshot"])
        if path.is_dir():
            path = path / "snapshot.json"
        before = {p.name: digest(p) for p in [path, path.parent / "arrays.npz"]}
        snapshot = load_snapshot(path)
        surface = snapshot.state.surface
        base = base_metrics(surface, int(case["resolution"]))
        assert base["charts"] == case["charts"]
        shape = chart_metrics(surface)
        geometry_hash = hashlib.sha256(surface.mesh.vertices.tobytes() + surface.mesh.faces.tobytes()).hexdigest()
        scene = scenes.get(geometry_hash)
        if scene is None:
            scene = AOScene(surface.mesh, samples=samples)
            scenes[geometry_hash] = scene
        visibility = scene.evaluate(surface, deadline=started + timeout)
        for filename, expected in before.items():
            assert digest(path.parent / filename) == expected
        for key in ["vertices", "summed_uv_area", "raw_uv_union_area", "unit_square_coverage",
                    "zero_uv_area_faces", "mixed_orientation_faces", "seam_edge_count"]:
            if key in case:
                assert math.isclose(float(str(base[key])), float(str(case[key])), rel_tol=1e-9, abs_tol=1e-10), key
        if "distortion" in case:
            old_distortion = cast(dict[str, object], case["distortion"])
            new_distortion = cast(dict[str, object], base["distortion"])
            for key, value in old_distortion.items():
                if isinstance(value, (int, float)):
                    assert math.isclose(float(value), float(str(new_distortion[key])), rel_tol=1e-9, abs_tol=1e-10), key
        row = {"method": name, "state_hash": snapshot.state_hash, "geometry_hash": geometry_hash,
               "snapshot": str(path.relative_to(ROOT)), "source_sha256": before, "base_metrics": base,
               "paper_metrics": {**shape, **visibility}, "seconds": time.monotonic() - started,
               "historical_quality_accepted": case["quality_accepted"], "quality_status_changed": False}
        save_json(output / f"{name}.json", row)
        rows.append({**row, "paper_metrics": {key: value for key, value in {**shape, **visibility}.items() if key != "charts"}})
        print(f"DONE {name} seconds={row['seconds']:.3f} AO={visibility['seam_ao']} "
              f"boundaryAO={visibility['boundary_ao']} non_disk={shape['non_disk_fraction']}", flush=True)
    assert digest(source_path) == protocol["measurement_sha256"]
    assert digest(original_path) == protocol["original_evidence_sha256"]
    save_json(output / "summary.json", {"protocol": protocol, "rows": rows,
              "unique_geometries": len(scenes), "supplement_tool_seconds": sum(float(str(r["seconds"])) for r in rows),
              "all_sources_unchanged": True, "all_historical_quality_states_preserved": True})
    print(f"PASS cases={len(rows)} unique_geometries={len(scenes)}", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--samples", type=int, default=1000)
    parser.add_argument("--timeout", type=float, default=300)
    args = parser.parse_args()
    if args.samples < 1 or args.timeout <= 0:
        parser.error("positive samples and timeout required")
    run(args.output, args.samples, args.timeout)


if __name__ == "__main__":
    main()
