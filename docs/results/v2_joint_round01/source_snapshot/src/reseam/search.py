"""Transactional regional search with an anytime feasible archive."""

import json
import time
from pathlib import Path
from typing import Any

from .candidates import build_candidate, proposals
from .constraints import Constraints
from .mesh import save_state
from .solver import relax
from .types import MeshState
from .validation import seam_metrics, validate


def accept(
    base: MeshState,
    proposed: MeshState,
    base_version: int,
    constraints: Constraints,
    tau: float,
    island_weight: float,
) -> tuple[MeshState, str]:
    if base.version != base_version:
        return base, "STALE_STATE"
    if constraints.errors(proposed):
        return base, "HARD_CONSTRAINT_VIOLATION"
    if not validate(proposed, tau)["valid"]:
        return base, "QUALITY_INVALID"
    old = float(seam_metrics(base, constraints.weights, island_weight)["objective"])
    new = float(seam_metrics(proposed, constraints.weights, island_weight)["objective"])
    if new >= old - 1e-8:
        return base, "NO_STRICT_IMPROVEMENT"
    result = proposed.copy()
    result.version = base.version + 1
    return result, "ACCEPTED"


def search(
    initial: MeshState,
    constraints: Constraints,
    runtime: dict[str, Any],
    folder: Path,
    seconds: float,
    seed: int,
    mode: str = "adaptive",
    tau: float = 0.025,
    island_weight: float = 0.0,
    max_candidates: int = 120,
) -> tuple[MeshState, dict[str, Any]]:
    start = time.monotonic()
    best = initial.copy()
    if not validate(best, tau)["valid"]:
        raise ValueError("INITIALIZATION_NOT_FEASIBLE")
    if constraints.errors(best):
        # A constraint-aware initialization is required; never silently relax requested constraints.
        raise ValueError("INITIALIZATION_HARD_CONSTRAINT_VIOLATION:" + str(constraints.errors(best)))
    folder.mkdir(parents=True, exist_ok=True)
    save_state(folder / "initial.json", initial)
    count, accepted, iteration = 0, 0, 0
    history: list[dict[str, Any]] = []
    with (folder / "candidates.jsonl").open("x", encoding="utf-8") as log:
        while count < max_candidates and time.monotonic() - start < seconds:
            changed = False
            for proposal in proposals(best, constraints, seed + iteration, deadline=start + seconds):
                kind, region, seams = proposal.kind, proposal.region_faces, proposal.seams
                if count >= max_candidates or (
                    proposal.error is None and seconds - (time.monotonic() - start) < 2
                ):
                    break
                count += 1
                record: dict[str, Any] = {
                    "candidate_id": f"candidate_{count:04d}",
                    "base_version": best.version,
                    "proposal_type": kind,
                    "region_faces": list(region),
                    "removed": sorted(best.seams - seams),
                    "added": sorted(seams - best.seams),
                    "accepted": False,
                }
                candidate_start = time.monotonic()
                score_trial = best.copy()
                score_trial.seams = seams
                if proposal.error is not None:
                    record["status"] = "BUDGET_EXHAUSTED" if "BUDGET" in proposal.error else "INIT_FAILED"
                    record["failure_stage"] = "BOUNDARY_BRIDGE"
                    record["reason"] = proposal.error
                elif (
                    float(seam_metrics(score_trial, constraints.weights, island_weight)["objective"])
                    >= float(seam_metrics(best, constraints.weights, island_weight)["objective"]) - 1e-8
                ):
                    record["status"] = "NO_POTENTIAL_IMPROVEMENT"
                else:
                    failure_stage = "INITIALIZATION"
                    try:
                        candidate = build_candidate(best, kind, region, seams, record["candidate_id"])
                        failure_stage = "CONTINUOUS_SOLVE"
                        budget = min(20.0, seconds - (time.monotonic() - start))
                        result, stages = relax(
                            candidate, mode, runtime, folder / record["candidate_id"], budget, tau
                        )
                        record["supports"] = stages
                        record["status"] = "QUALITY_OR_BUDGET_REJECTED"
                        if result is not None and time.monotonic() - start < seconds - 1:
                            best, record["status"] = accept(
                                best, result, candidate.base_version, constraints, tau, island_weight
                            )
                            if record["status"] == "ACCEPTED":
                                accepted += 1
                                changed = True
                                record["accepted"] = True
                                save_state(folder / "best.json", best)
                    except (ValueError, RuntimeError, TimeoutError) as error:
                        record["failure_stage"] = failure_stage
                        record["status"] = (
                            "BUDGET_EXHAUSTED"
                            if isinstance(error, TimeoutError)
                            else "INIT_FAILED"
                            if failure_stage == "INITIALIZATION"
                            else "NUMERICAL_FAILURE"
                        )
                        record["reason"] = str(error)
                record["seconds"] = time.monotonic() - candidate_start
                record["cumulative_seconds"] = time.monotonic() - start
                record["best_objective"] = seam_metrics(best, constraints.weights, island_weight)["objective"]
                log.write(json.dumps(record, allow_nan=False) + "\n")
                log.flush()
                history.append(record)
                if changed:
                    break
            iteration += 1
            if not changed:
                break
    save_state(folder / "best.json", best)
    return best, {
        "candidates": count,
        "accepted": accepted,
        "search_seconds": time.monotonic() - start,
        "status": "BUDGET_EXHAUSTED" if time.monotonic() - start >= seconds else "SEARCH_FINISHED",
        "history": history,
    }
