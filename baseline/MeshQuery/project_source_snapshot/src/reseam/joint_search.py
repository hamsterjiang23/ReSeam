"""Independent four-track search with fresh initialization and transactional acceptance."""

import json
import time
from pathlib import Path
from typing import Any

from .backends import baseline
from .fixed_uv import face_areas
from .joint_candidates import embed_pair
from .joint_downstream import bake_joint
from .joint_material import state_fingerprint
from .joint_packing import pack_joint
from .joint_regions import RegionPair, components, paired_problem, planar_regions, refine_pair, region_pairs
from .joint_seams import seam_candidate, seam_proposals
from .joint_state import accept_joint, save_joint
from .joint_types import JointProfile, V2State
from .joint_validation import validate_joint_quality
from .mesh import save_state
from .paired_solver import solve_pair
from .pipeline import export_glb, write_json
from .runtime import read_runtime

TRACKS = ("packing_only", "seam_pack", "reuse_pack", "joint")


def shrink_pair(state: V2State, pair: RegionPair, failed: set[int]) -> RegionPair | None:
    """Remove failed faces, retain largest connected member on each side."""
    members = []
    for faces in (pair.first_faces, pair.second_faces):
        parts = components(state.surface.mesh, set(faces) - failed)
        if not parts or len(parts[0]) < 4:
            return None
        members.append(parts[0])
    result = RegionPair(members[0], members[1], pair.normal, pair.offset)
    return result if result != pair else None


def run_joint(
    root: Path,
    model: str,
    track: str,
    seed: int,
    folder: Path,
    seconds: float = 300,
    candidate_seconds: float = 30,
    maximum: int = 120,
) -> dict[str, Any]:
    if track not in TRACKS or seconds <= 20 or candidate_seconds <= 0:
        raise ValueError("Invalid joint run configuration")
    folder = folder.resolve()
    folder.mkdir(parents=True, exist_ok=False)
    started = time.monotonic()
    deadline = started + seconds
    runtime = read_runtime(root)
    profile = JointProfile()
    result: dict[str, Any] = {
        "model": model,
        "track": track,
        "seed": seed,
        "status": "RUNNING",
        "budget_seconds": seconds,
        "candidates": [],
        "human_acceptance": "NOT_REVIEWED",
    }
    best: V2State | None = None
    try:
        initial, info = baseline(
            root,
            root / "assets/v1" / model / "low.obj",
            "optcuts",
            runtime,
            folder / "initialize",
            min(180, seconds - 30),
            seed,
        )
        result["initialization"] = info
        result["unpacked_initial_hash"] = state_fingerprint(V2State(initial))
        packed, info = pack_joint(
            root,
            V2State(initial),
            folder / "initial_pack",
            min(45, deadline - time.monotonic() - 15),
            seed,
            profile,
        )
        if packed is None:
            raise ValueError("INITIAL_PACK_FAILED")
        reference = packed.surface.copy()
        quality = validate_joint_quality(reference, packed, profile)
        result["initial_quality"] = quality
        if not quality["valid"]:
            raise ValueError("INITIAL_QUALITY_FAILED")
        best = packed
        result["initial_hash"] = state_fingerprint(best)
        save_state(folder / "initial.json", reference)
        save_joint(folder / "joint_state.json", best)
        pairs: list[RegionPair] = []
        if track in ("reuse_pack", "joint") and time.monotonic() < deadline - 20:
            detected, planes = region_pairs(reference.mesh, min(deadline - 15, time.monotonic() + 15))
            pairs = planar_regions(reference.mesh, detected)
            result["planes"] = planes
            result["region_count"] = len(pairs)
        seams = iter(seam_proposals(best, seed, deadline - 15))
        kinds = {
            "packing_only": ["packing"],
            "seam_pack": ["seam", "packing"],
            "reuse_pack": ["reuse", "packing"],
            "joint": ["reuse", "seam", "packing"],
        }[track]
        for index in range(maximum):
            if time.monotonic() >= deadline - 18:
                break
            candidate_start = time.monotonic()
            until = min(deadline - 15, candidate_start + candidate_seconds)
            trial_folder = folder / f"candidate_{index:03d}"
            trial_folder.mkdir()
            kind = kinds[index % len(kinds)]
            record: dict[str, Any] = {"index": index, "kind": kind, "base_version": best.version}
            pair = None
            try:
                if kind == "reuse" and pairs:
                    pair = pairs.pop(0)
                    pair, record["refinement"] = refine_pair(reference.mesh, pair, until - 8)
                    problem, ids = paired_problem(reference.mesh, pair, until - 8, planar=True)
                    target = abs(face_areas(reference)[list(ids)])
                    problem.target_areas = target / target.sum() * problem.initial_area
                    solved = solve_pair(problem, min(8, max(0.1, until - time.monotonic() - 8)))
                    record["solve"] = {"status": solved.status, "history": solved.history}
                    candidate = embed_pair(best, solved.state, ids, pair, problem, f"reuse_{index}")
                elif kind == "seam":
                    proposal = next(seams, None)
                    if proposal is None:
                        candidate = best.copy()
                        record["kind"] = "packing_exhausted_seams"
                    else:
                        candidate, record["seam"] = seam_candidate(
                            best,
                            proposal,
                            runtime,
                            trial_folder / "solve",
                            max(0.1, until - time.monotonic() - 8),
                        )
                else:
                    candidate = best.copy()
                    if kind == "reuse":
                        record["kind"] = "packing_exhausted_regions"
                save_joint(trial_folder / "unpacked.json", candidate)
                remaining = until - time.monotonic()
                if remaining < 3:
                    raise TimeoutError("CANDIDATE_BUDGET_EXHAUSTED")
                packed, record["packing"] = pack_joint(
                    root, candidate, trial_folder / "pack", remaining, seed, profile, index + 1
                )
                if packed is None:
                    raise ValueError("PACKING_REJECTED")
                save_joint(trial_folder / "packed.json", packed)
                quality = validate_joint_quality(reference, packed, profile)
                record["quality"] = quality
                if not quality["valid"]:
                    record["status"] = "QUALITY_REJECTED"
                    if pair is not None:
                        failed = set(quality.get("anisotropy_failed_faces", []))
                        for coupling in quality.get("group_coupling", []):
                            failed.update(coupling["over_limit_source_faces"])
                        smaller = shrink_pair(best, pair, failed)
                        if smaller is not None and smaller not in pairs:
                            pairs.append(smaller)
                            record["requeued_faces"] = [len(smaller.first_faces), len(smaller.second_faces)]
                else:
                    if packed.groups:
                        remaining = until - time.monotonic()
                        if remaining < 5:
                            raise TimeoutError("MATERIAL_BUDGET_EXHAUSTED")
                        record["material"] = bake_joint(
                            root, model, packed, trial_folder / "bake", remaining, profile
                        )
                    if time.monotonic() >= until:
                        raise TimeoutError("CANDIDATE_BUDGET_EXHAUSTED")
                    updated, decision = accept_joint(reference, best, packed, best.version, profile)
                    record.update(decision)
                    # A slow final check may not commit after the reserved deadline.
                    if time.monotonic() >= until:
                        record["status"] = "CANDIDATE_BUDGET_EXHAUSTED"
                    else:
                        best = updated
                        if record["status"] == "ACCEPTED":
                            save_joint(folder / "joint_state.json", best)
                            seams = iter(seam_proposals(best, seed + best.version, deadline - 15))
            except (ValueError, RuntimeError, TimeoutError, OSError) as error:
                record.update(status="REJECTED", reason=str(error))
            record["seconds"] = time.monotonic() - candidate_start
            record["budget_overrun"] = time.monotonic() > until
            result["candidates"].append(record)
            write_json(trial_folder / "result.json", record)
            with (folder / "candidates.jsonl").open("a", encoding="utf-8") as stream:
                # Reuse the sanitized disk representation: NaN never enters the log.
                stream.write(
                    json.dumps(json.loads((trial_folder / "result.json").read_text()), ensure_ascii=False)
                    + "\n"
                )
            write_json(folder / "result.json", result)
        result["quality"] = validate_joint_quality(reference, best, profile)
        save_joint(folder / "joint_state.json", best)
        save_state(folder / "state.json", best.surface)
        from .joint_report import draw_atlas

        draw_atlas(best, folder / "uv.png")
        export_glb(best.surface, folder / "geometry.glb")
        result["status"] = "SUCCESS" if result["quality"]["valid"] else "FAILED_QUALITY"
        result["accepted"] = best.version
        result["reuse_groups"] = len(best.groups)
    except (ValueError, RuntimeError, TimeoutError, OSError) as error:
        result.update(status="FAILED", reason=str(error))
    result["wall_seconds"] = time.monotonic() - started
    result["budget_overrun"] = result["wall_seconds"] > seconds
    if result["budget_overrun"]:
        result["status"] = "BUDGET_EXCEEDED"
    write_json(folder / "result.json", result)
    return result
