"""Fixed-topology continuous solver, with compatible nested supports and real checkpoints."""

import json
import time
from pathlib import Path
from typing import Any

import numpy as np

from .runtime import linux_path, run_process, wsl_command
from .types import Candidate, MeshState, SolveResult
from .validation import distortion, validate


def solve(
    state: MeshState,
    free: frozenset[int],
    runtime: dict[str, Any],
    folder: Path,
    seconds: float,
    iterations: int = 160,
) -> SolveResult:
    start = time.monotonic()
    if folder.exists():
        raise FileExistsError(folder)
    folder.mkdir(parents=True)
    rest = np.zeros((len(state.uv), 3))
    rest[state.uv_faces.reshape(-1)] = state.mesh.vertices[state.mesh.faces.reshape(-1)] / state.mesh.scale
    fixed = sorted(set(range(len(state.uv))) - free)
    request = {
        "rest_vertices": rest.tolist(),
        "faces": state.uv_faces.tolist(),
        "uv": state.uv.tolist(),
        "fixed": fixed,
        "seconds": max(0.1, seconds - 1),
        "max_iterations": iterations,
    }
    request_path, result_path = folder / "request.json", folder / "result.json"
    request_path.write_text(json.dumps(request, allow_nan=False), encoding="utf-8")
    argv = wsl_command(
        runtime,
        [linux_path(Path(runtime["continuous_binary"])), linux_path(request_path), linux_path(result_path)],
        seconds,
    )
    status = "SOLVED"
    failure_reason = None
    try:
        run_process(argv, folder, seconds)
    except TimeoutError:
        status = "TIMEOUT_WITH_CHECKPOINT"
    except RuntimeError as error:
        status = "NUMERICAL_FAILURE_WITH_CHECKPOINT"
        failure_reason = str(error)
    if not result_path.exists():
        return SolveResult(
            "NUMERICAL_FAILURE" if failure_reason else "BUDGET_EXHAUSTED",
            None,
            time.monotonic() - start,
            reason=failure_reason,
        )
    checkpoints = list(folder.glob("result.json.checkpoint_*.json")) or [result_path]
    ranked = []
    for path in checkpoints:
        try:
            data = json.loads(path.read_text())
            ranked.append((float(data["energy_native"]), path))
        except (ValueError, KeyError):
            continue  # A killed process may leave one incomplete checkpoint.
    for _, path in sorted(ranked):
        data = json.loads(path.read_text())
        result = state.copy()
        result.uv = np.asarray(data["uv"], dtype=float)
        if result.uv.shape != state.uv.shape or data["topology_updates"] != 0:
            continue
        if fixed and not np.allclose(result.uv[fixed], state.uv[fixed], atol=1e-12, rtol=0):
            continue
        quality = validate(result, tau=None)
        if quality["valid"]:
            return SolveResult(
                status,
                result,
                time.monotonic() - start,
                int(data["iterations"]),
                len(free),
                reason=failure_reason,
                metadata={
                    "energy_native": data["energy_native"],
                    "quality": quality,
                    "selected_checkpoint": path.name,
                },
            )
    return SolveResult("NO_LEGAL_CHECKPOINT", None, time.monotonic() - start, reason=failure_reason)


def supports(candidate: Candidate) -> list[frozenset[int]]:
    state = candidate.state
    affected = set(map(int, state.uv_faces[list(candidate.region_faces)].reshape(-1)))
    free = {
        int(state.uv_faces[fi, ci])
        for fi in candidate.region_faces
        for ci in range(3)
        if int(state.mesh.faces[fi, ci]) in candidate.changed_vertices
    }
    if not free:
        free = affected.copy()
    neighbors: dict[int, set[int]] = {v: set() for v in affected}
    for face in state.uv_faces[list(candidate.region_faces)]:
        for vertex in face:
            neighbors[int(vertex)].update(map(int, face))
    rings: list[frozenset[int]] = []
    for ring in range(1, 4):
        free |= set().union(*(neighbors.get(v, set()) for v in free))
        if ring in (1, 3):
            rings.append(frozenset(free & affected))
    rings.append(frozenset(affected))
    return rings


def relax(
    candidate: Candidate, mode: str, runtime: dict[str, Any], folder: Path, seconds: float, tau: float
) -> tuple[MeshState | None, list[dict[str, Any]]]:
    domain = supports(candidate)
    schedule = (
        [(0, 160)] if mode == "small" else [(2, 160)] if mode == "large" else [(0, 20), (1, 40), (2, 100)]
    )
    start = time.monotonic()
    best = candidate.state.copy()
    best_distortion = distortion(best)["sd_mean"]
    history: list[dict[str, Any]] = []
    for level, iterations in schedule:
        remaining = seconds - (time.monotonic() - start)
        if remaining <= 1:
            break
        # Fixed coordinates always come from the same candidate initialization.
        trial = best.copy()
        frozen = sorted(set(range(len(trial.uv))) - domain[level])
        trial.uv[frozen] = candidate.state.uv[frozen]
        result = solve(
            trial, domain[level], runtime, folder / f"support_{level}", remaining, iterations=iterations
        )
        item: dict[str, Any] = {
            "level": level,
            "free_vertices": len(domain[level]),
            "status": result.status,
            "seconds": result.seconds,
            "iterations": result.iterations,
            "reason": result.reason,
        }
        if result.state is not None:
            value = distortion(result.state)["sd_mean"]
            item["sd_mean"] = value
            if value < best_distortion:
                best, best_distortion = result.state, value
        history.append(item)
        if best_distortion <= tau:
            break
    return best if validate(best, tau)["valid"] else None, history
