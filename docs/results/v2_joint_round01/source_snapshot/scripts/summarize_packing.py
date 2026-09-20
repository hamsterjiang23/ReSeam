"""Independent final audit and compact delivery for a completed fixed-island round."""

import argparse
import json
import time
from collections import Counter
from pathlib import Path
from statistics import median
from typing import Any

import numpy as np

from reseam.fixed_uv import FixedAtlas, PackingProfile, certify
from reseam.mesh import load_state, sha256
from reseam.pipeline import write_json


def summarize(root: Path, round_path: Path, delivery: Path) -> dict[str, Any]:
    start = time.monotonic()
    data = json.loads((round_path / "summary.json").read_text(encoding="utf-8"))
    if "wall_seconds" not in data:
        raise ValueError("Round is not finalized")
    delivery.mkdir(parents=True, exist_ok=False)
    profile = PackingProfile(**data["config"]["profile"])
    atlases: dict[str, FixedAtlas] = {}
    audits = []
    for row in data["rows"]:
        if not row.get("quality", {}).get("valid"):
            audits.append({"folder": row["folder"], "status": row["status"], "audited": False})
            continue
        source = row["source"]
        if source not in atlases:
            atlases[source] = FixedAtlas.from_state(load_state(Path(source)))
        state = load_state(round_path / row["folder"] / "state.json")
        checked = certify(atlases[source], state, profile)
        assert checked["valid"], (row["folder"], checked)
        assert abs(checked["occupancy"] - row["quality"]["occupancy"]) < 1e-12
        assert sha256(Path(source)) == row["input_sha256"]
        audits.append({"folder": row["folder"], "audited": True, "quality": checked})
    groups = []
    for case in data["config"]["sources"]:
        for backend in data["config"]["backends"]:
            rows = [
                r
                for r in data["rows"]
                if r["model"] == case["model"]
                and r["uv_source"] == case["method"]
                and r["backend"] == backend
            ]
            accepted = [r for r in rows if r.get("quality", {}).get("valid") and not r.get("budget_overrun")]
            groups.append(
                {
                    "model": case["model"],
                    "uv_source": case["method"],
                    "backend": backend,
                    "scheduled": len(rows),
                    "valid_within_budget": len(accepted),
                    "initial": accepted[0]["initial"]["occupancy"] if accepted else None,
                    "scale_only_median": median(r["scale_only"]["occupancy"] for r in accepted)
                    if accepted
                    else None,
                    "occupancy_median": median(r["quality"]["occupancy"] for r in accepted)
                    if accepted
                    else None,
                    "occupancy_range": [
                        min(r["quality"]["occupancy"] for r in accepted),
                        max(r["quality"]["occupancy"] for r in accepted),
                    ]
                    if accepted
                    else None,
                    "density_gain_median": median(r["quality"]["density_gain"] for r in accepted)
                    if accepted
                    else None,
                    "seconds_median": median(r["wall_seconds"] for r in rows if "wall_seconds" in r)
                    if accepted
                    else None,
                }
            )
    # Equality is checked using arrays, not labels or different serialized version fields.
    duplicate_inputs = []
    items = list(atlases.items())
    for i, (left, first) in enumerate(items):
        for right, second in items[i + 1 :]:
            if (
                np.array_equal(first.source.mesh.faces, second.source.mesh.faces)
                and np.array_equal(first.source.mesh.vertices, second.source.mesh.vertices)
                and np.array_equal(first.source.uv_faces, second.source.uv_faces)
                and np.array_equal(first.source.uv, second.source.uv)
                and first.source.seams == second.source.seams
            ):
                duplicate_inputs.append([left, right])
    result = {
        "round": str(round_path),
        "status_counts": dict(Counter(r["status"] for r in data["rows"])),
        "groups": groups,
        "audits": audits,
        "duplicate_inputs": duplicate_inputs,
        "trials": dict(Counter(t["status"] for r in data["rows"] for t in r.get("trials", []))),
        "overruns": [r["folder"] for r in data["rows"] if r.get("budget_overrun")],
        "formal_seconds": data["wall_seconds"],
        "source_code_unchanged": data["code_unchanged"],
        "inputs_unchanged": data["inputs_unchanged"],
        "backends_unchanged": data["backends_unchanged"],
        "matrix_complete": data["matrix_complete"],
        "artist_acceptance": "PENDING_HUMAN",
        "bake_and_render_acceptance": "NOT_RUN",
    }
    result["audit_seconds"] = time.monotonic() - start
    write_json(delivery / "summary.json", result)
    print(json.dumps({k: v for k, v in result.items() if k != "audits"}, ensure_ascii=False, indent=2))
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--round", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    summarize(args.root.resolve(), args.round.resolve(), args.output.resolve())


if __name__ == "__main__":
    main()
