"""Paired support-domain audit on an immutable candidate pool."""

import time
from pathlib import Path
from typing import Any

from .candidates import build_candidate, proposals
from .constraints import Constraints
from .mesh import save_state, sha256
from .pipeline import write_json
from .solver import relax
from .types import MeshState
from .validation import distortion, seam_metrics


def support_audit(
    initial: MeshState, runtime: dict[str, Any], output: Path, seconds: float = 240, pool_size: int = 8
) -> dict[str, Any]:
    output.mkdir(parents=True, exist_ok=False)
    start = time.monotonic()
    records: list[dict[str, Any]] = []
    rejected: list[dict[str, str]] = []
    stopped_for_budget = False
    for index, (kind, region, seams) in enumerate(proposals(initial, Constraints(), seed=17)):
        if len(records) >= pool_size:
            break
        if time.monotonic() - start > seconds - 30:
            stopped_for_budget = True
            break
        objective_trial = initial.copy()
        objective_trial.seams = seams
        if (
            float(seam_metrics(objective_trial)["objective"])
            >= float(seam_metrics(initial)["objective"]) - 1e-8
        ):
            continue
        try:
            candidate = build_candidate(initial, kind, region, seams, f"pool_{index:04d}")
        except ValueError as error:
            rejected.append({"kind": kind, "reason": str(error)})
            continue
        remaining = seconds - (time.monotonic() - start)
        if remaining < 6:
            stopped_for_budget = True
            break
        per_mode_seconds = min(10.0, (remaining - 3) / 3)
        folder = output / candidate.candidate_id
        folder.mkdir()
        save_state(folder / "common_start.json", candidate.state)
        record: dict[str, Any] = {
            "candidate_id": candidate.candidate_id,
            "kind": kind,
            "common_start_sha256": sha256(folder / "common_start.json"),
            "modes": {},
            "objective": seam_metrics(candidate.state)["objective"],
            "initial_objective": seam_metrics(initial)["objective"],
        }
        for mode in ("small", "large", "adaptive"):
            state, stages = relax(
                candidate, mode, runtime, folder / mode, seconds=per_mode_seconds, tau=0.025
            )
            record["modes"][mode] = {
                "feasible": state is not None,
                "stages": stages,
                "sd_mean": distortion(state)["sd_mean"] if state is not None else None,
                "common_start_sha256": record["common_start_sha256"],
            }
            record["modes"][mode]["budget_seconds"] = per_mode_seconds
        records.append(record)
        write_json(output / "pool.json", {"records": records, "initialization_failures": rejected})
    failed_small = [record for record in records if not record["modes"]["small"]["feasible"]]
    rescued = [
        record
        for record in failed_small
        if record["modes"]["large"]["feasible"] and record["objective"] < record["initial_objective"] - 1e-8
    ]
    result = {
        "records": records,
        "initialization_failures": rejected,
        "audited_count": len(records),
        "small_failure_count": len(failed_small),
        "rescued_and_improving_count": len(rescued),
        "rescue_rate": len(rescued) / len(failed_small) if failed_small else None,
        "seconds": time.monotonic() - start,
        "pool_size_requested": pool_size,
        "missing_candidates": max(0, pool_size - len(records)),
        "stopped_for_budget": stopped_for_budget,
        "scope": "Frozen initialized candidate pool; separate from diverging full-search trajectories",
    }
    write_json(output / "result.json", result)
    return result
