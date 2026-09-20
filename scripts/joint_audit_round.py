"""Read-only experiment audit; supplementary summaries never alter run evidence."""

import argparse
import json
from collections import Counter
from itertools import product
from pathlib import Path
from statistics import median
from typing import Any

from reseam.mesh import sha256
from reseam.pipeline import write_json


def audit(folder: Path) -> dict[str, Any]:
    rows = json.loads((folder / "matrix.json").read_text(encoding="utf-8"))
    tracks = ("packing_only", "seam_pack", "reuse_pack", "joint")
    models = ("energy_blade", "dragon")
    expected = set(product(models, (17, 29, 43), tracks))
    assert len(rows) == 24 and {(r["model"], r["seed"], r["track"]) for r in rows} == expected
    groups, comparisons, initial, candidates = [], [], [], []
    accepted_kinds: Counter[str] = Counter()
    rejected: Counter[str] = Counter()
    for model in models:
        for seed in (17, 29, 43):
            selected = {r["track"]: r for r in rows if r["model"] == model and r["seed"] == seed}
            hashes = {
                t: sha256(folder / r["path"] / "initial.json")
                for t, r in selected.items()
                if (folder / r["path"] / "initial.json").exists()
            }
            same = len(hashes) == 4 and len(set(hashes.values())) == 1
            initial.append({"model": model, "seed": seed, "full_initial_state_sha256": hashes, "match": same})
            joint = selected["joint"]
            for track in tracks[:-1]:
                other = selected[track]
                valid = same and all(r["status"] == "SUCCESS" for r in (joint, other))
                comparisons.append(
                    {
                        "model": model,
                        "seed": seed,
                        "comparison": "joint_vs_" + track,
                        "eligible": valid,
                        "density_ratio": joint["quality"]["gain"] / other["quality"]["gain"]
                        if valid
                        else None,
                        "coverage_percentage_points": 100
                        * (joint["quality"]["unique_coverage"] - other["quality"]["unique_coverage"])
                        if valid
                        else None,
                    }
                )
        for track in tracks:
            selected = [r for r in rows if r["model"] == model and r["track"] == track]
            valid = [r for r in selected if r["status"] == "SUCCESS"]
            metrics = {}
            for key in ("gain", "unique_coverage", "reuse_factor", "charts", "shape_distortion"):
                values = [r["quality"][key] for r in valid]
                metrics[key] = {
                    "values": values,
                    "median": median(values) if values else None,
                    "range": [min(values), max(values)] if values else None,
                }
            groups.append(
                {"model": model, "track": track, "scheduled": 3, "valid": len(valid), "metrics": metrics}
            )
    for row in rows:
        path = folder / row["path"] / "result.json"
        data = json.loads(path.read_text()) if path.exists() else {}
        records = data.get("candidates", [])
        reasons: Counter[str] = Counter()
        reuse_reasons: Counter[str] = Counter()
        for record in records:
            if record["status"] == "ACCEPTED":
                accepted_kinds[(record.get("seam") or {}).get("kind", record["kind"])] += 1
            else:
                codes = {
                    reason.split(":", 1)[0]
                    for reason in record.get("quality", {}).get("errors", []) + record.get("errors", [])
                }
                if record.get("reason"):
                    codes.add(record["reason"])
                reasons.update(codes)
                if record["kind"] == "reuse":
                    reuse_reasons.update(codes)
        rejected.update(reasons)
        candidates.append(
            {
                "path": row["path"],
                "count": len(records),
                "statuses": dict(Counter(r["status"] for r in records)),
                "candidate_overruns": sum(r.get("budget_overrun", False) for r in records),
                "accepted": sum(r["status"] == "ACCEPTED" for r in records),
                "reuse_proposals": sum(r["kind"] == "reuse" for r in records),
                "shrunk_regions_enqueued": sum("requeued_faces" in r for r in records),
                "material_checks": sum("material" in r for r in records),
                "geometry_valid_reuse": sum(
                    r["kind"] == "reuse" and r.get("quality", {}).get("valid", False) for r in records
                ),
                "rejection_codes": dict(reasons),
                "reuse_rejection_codes": dict(reuse_reasons),
            }
        )
    proxy = {}
    summary_path = folder / "review_summary.json"
    if summary_path.exists():
        for key, summary in json.loads(summary_path.read_text()).items():
            mapping = json.loads((folder / "private" / (key + ".json")).read_text())["mapping"]
            names = {m["sample_id"]: m["identity"] for m in mapping if "identity" in m}
            proxy[key] = {
                "reviewers": summary["reviewers"],
                "samples": [{**sample, "track": names[sample["id"]]} for sample in summary["samples"]],
                "preferences": [
                    {
                        **item,
                        "first_track": names[item["first"]],
                        "second_track": names[item["second"]],
                        "majority_track": names.get(item["majority"], item["majority"]),
                    }
                    for item in summary["preferences"]
                ],
            }
    downstream = []
    for row in rows:
        path = folder / row["path"] / "downstream/result.json"
        if path.exists():
            data = json.loads(path.read_text())
            downstream.append(
                {
                    "path": row["path"],
                    "status": data["status"],
                    "export_frames": data.get("export_frames"),
                    "projection_or_frame_invalid_faces": data.get("material", {}).get(
                        "projection_or_frame_invalid_faces", []
                    ),
                    "wall_seconds": data["wall_seconds"],
                }
            )
    return {
        "round_id": folder.name,
        "auditor_sha256": sha256(Path(__file__)),
        "matrix_sha256": sha256(folder / "matrix.json"),
        "scheduled": 24,
        "search_successes": sum(r["status"] == "SUCCESS" for r in rows),
        "downstream_successes": sum(r["downstream_status"] == "SUCCESS" for r in rows),
        "groups": groups,
        "paired_comparisons": comparisons,
        "initial_state_audit": initial,
        "candidates": candidates,
        "accepted_candidate_kinds": dict(accepted_kinds),
        "rejection_codes": dict(rejected),
        "downstream": downstream,
        "proxy": proxy,
        "additional_views": json.loads((folder / "additional_views/matrix.json").read_text(encoding="utf-8"))
        if (folder / "additional_views/matrix.json").exists() else [],
        "human_acceptance": "NOT_REVIEWED",
        "view_scope": "known regression cameras, not unseen-view generalization",
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--round", type=Path, required=True)
    args = parser.parse_args()
    output = args.round.resolve() / "audit.json"
    write_json(output, audit(args.round.resolve()))
    print(output)


if __name__ == "__main__":
    main()
