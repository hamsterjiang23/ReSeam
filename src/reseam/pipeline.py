"""A complete, independently budgeted single-model run."""

import json
import platform
import time
from pathlib import Path
from typing import Any

import numpy as np
import trimesh
from PIL import Image, ImageDraw
from trimesh.visual.material import PBRMaterial
from trimesh.visual.texture import TextureVisuals

from .backends import baseline
from .constraints import load_constraints, suggestions
from .initialization import initialize
from .mesh import load_mesh, save_state, sha256, write_obj
from .runtime import read_runtime
from .search import search
from .solver import solve
from .types import MeshState
from .validation import seam_metrics, validate


def write_json(path: Path, value: Any) -> None:
    def sanitize(item: Any) -> Any:
        if isinstance(item, dict):
            return {key: sanitize(val) for key, val in item.items()}
        if isinstance(item, (list, tuple)):
            return [sanitize(x) for x in item]
        if isinstance(item, float) and not np.isfinite(item):
            return None
        return item

    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(sanitize(value), ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8"
    )
    temporary.replace(path)


def draw_uv(state: MeshState, path: Path, size: int = 1024) -> None:
    image = Image.new("RGB", (size, size), "#101820")
    draw = ImageDraw.Draw(image)
    uv = state.uv.copy()
    minimum = uv.min(axis=0)
    extent = max(float(np.ptp(uv, axis=0).max()), 1e-12)
    uv = (uv - minimum) / extent * (size - 32) + 16
    uv[:, 1] = size - uv[:, 1]
    for face in state.uv_faces:
        points = [tuple(map(float, x)) for x in uv[face]]
        draw.line(points + points[:1], fill="#93ddcd", width=1)
    image.save(path)


def export_glb(state: MeshState, path: Path, texture: Path | None = None) -> None:
    # Per-corner export preserves UV identities. Geometry IDs remain in state.json.
    vertices = state.mesh.vertices[state.mesh.faces.reshape(-1)]
    faces = np.arange(len(vertices)).reshape(-1, 3)
    mesh = trimesh.Trimesh(vertices=vertices, faces=faces, process=False)
    material = PBRMaterial(
        baseColorTexture=Image.open(texture).convert("RGBA") if texture else None,
        baseColorFactor=[255, 255, 255, 255],
        metallicFactor=0.0,
        roughnessFactor=1.0,
    )
    mesh.visual = TextureVisuals(uv=state.uv[state.uv_faces.reshape(-1)], material=material)
    mesh.export(path)
    reopened = trimesh.load(path, force="mesh", process=False)
    if not isinstance(reopened, trimesh.Trimesh):
        raise TypeError("GLB did not roundtrip as triangle mesh")
    if not isinstance(reopened.visual, TextureVisuals):
        raise TypeError("GLB has no texture coordinates")
    expected = state.uv[state.uv_faces]
    actual = np.asarray(reopened.visual.uv)[reopened.faces]
    if actual.shape != expected.shape or not np.allclose(actual, expected, atol=1e-6, rtol=0):
        raise ValueError("GLB roundtrip changed per-corner UV")


def run_unwrap(
    root: Path,
    model_id: str,
    method: str,
    output: Path,
    seed: int = 17,
    seconds: float = 180,
    annotations: Path | None = None,
    tau: float = 0.025,
    island_weight: float = 0,
    max_candidates: int = 120,
) -> dict[str, Any]:
    if output.exists():
        raise FileExistsError(f"Refusing to overwrite {output}")
    if seconds < 10:
        raise ValueError("A run budget must be at least 10 seconds, including cleanup/export reserve")
    output.mkdir(parents=True)
    started = time.monotonic()
    work_seconds = seconds - 8
    runtime = read_runtime(root)
    manifest = json.loads((root / "assets/v1/manifest.json").read_text(encoding="utf-8-sig"))
    model = next((item for item in manifest["models"] if item["id"] == model_id), None)
    if model is None:
        raise ValueError(f"Unknown fixed model: {model_id}")
    input_path = root / "assets/v1" / model_id / "low.obj"
    for file in model["files"]:
        path = root / "assets/v1" / file["path"]
        if sha256(path) != file["sha256"]:
            raise ValueError(f"Asset digest mismatch: {path}")
    config: dict[str, Any] = {
        "model": model_id,
        "method": method,
        "seed": seed,
        "seconds": seconds,
        "tau": tau,
        "island_weight": island_weight,
        "input_sha256": sha256(input_path),
        "initialization": "independent",
        "platform": platform.platform(),
        "constraints_source": str(annotations) if annotations else None,
        "constraints_sha256": sha256(annotations) if annotations else None,
        "max_candidates": max_candidates,
        "cleanup_export_reserve_seconds": 8,
        "runtime": runtime,
    }
    write_json(output / "config.json", config)
    result: dict[str, Any] = {**config, "status": "RUNNING", "quality": None, "artist_assessed": False}
    write_json(output / "result.json", result)
    try:
        mesh = load_mesh(input_path)
        suggestion_path = output / "suggestions.json"
        write_json(suggestion_path, suggestions(mesh))
        constraints = load_constraints(annotations, mesh)
        if method == "reseam_artist" and annotations is None:
            constraints = load_constraints(suggestion_path, mesh)
            result["constraints_source"] = "automatic_unconfirmed_soft_preferences"
        if method == "optcuts_restart":
            best = None
            trials = []
            for restart in range(3):
                remaining = work_seconds - (time.monotonic() - started)
                if remaining <= 1:
                    break
                try:
                    trial, metadata = baseline(
                        root,
                        input_path,
                        "optcuts",
                        runtime,
                        output / f"restart_{restart}",
                        remaining / (3 - restart),
                        seed + restart,
                        randomized_start=True,
                    )
                except (ValueError, RuntimeError, TimeoutError) as error:
                    trials.append({"restart": restart, "status": "FAILED", "reason": str(error)})
                    continue
                quality = validate(trial, tau)
                trials.append({**metadata, "quality": quality})
                if quality["valid"] and (
                    best is None or seam_metrics(trial)["objective"] < seam_metrics(best)["objective"]
                ):
                    best = trial
            if best is None:
                raise ValueError("NO_FEASIBLE_RESTART")
            state = best
            result["restarts"] = trials
        else:
            backend = "optcuts" if method.startswith("reseam_") else method
            state, result["backend"] = baseline(
                root,
                input_path,
                backend,
                runtime,
                output / "initialization",
                max(0.1, work_seconds - (time.monotonic() - started)),
                seed,
            )
        save_state(output / "initial_state.json", state)
        result["initial_quality"] = validate(state, tau)
        if method.startswith("reseam_"):
            if constraints.errors(state):
                initial_seams = (state.seams | constraints.must_cut) - constraints.must_join
                repaired = initialize(mesh, initial_seams)
                restored = solve(
                    repaired,
                    frozenset(range(len(repaired.uv))),
                    runtime,
                    output / "constraint_init",
                    max(0.1, work_seconds - (time.monotonic() - started)),
                )
                if restored.state is None or not validate(restored.state, tau)["valid"]:
                    raise ValueError("CONSTRAINT_INITIALIZATION_FAILED")
                state = restored.state
            if method == "reseam_fixed":
                from .partition import frozen_partition

                state, result["fixed_partition"] = frozen_partition(
                    root,
                    state,
                    runtime,
                    output / "partition",
                    max(0, work_seconds - (time.monotonic() - started)),
                    seed,
                    tau,
                )
            else:
                mode = (
                    "small"
                    if method == "reseam_small"
                    else "large"
                    if method == "reseam_large"
                    else "adaptive"
                )
                state, search_result = search(
                    state,
                    constraints,
                    runtime,
                    output / "search",
                    max(0, work_seconds - (time.monotonic() - started)),
                    seed,
                    mode,
                    tau,
                    island_weight,
                    max_candidates,
                )
                result["search"] = {key: value for key, value in search_result.items() if key != "history"}
        quality = validate(state, tau)
        quality["constraint_errors"] = constraints.errors(state)
        quality["valid"] = quality["valid"] and not quality["constraint_errors"]
        result["quality"] = {**quality, **seam_metrics(state, constraints.weights, island_weight)}
        result["status"] = "SUCCESS" if quality["valid"] else "UV_INVALID"
        save_state(output / "state.json", state)
        write_obj(output / "native.obj", state)
        export_glb(state, output / "native.glb")
        draw_uv(state, output / "uv_layout.png")
    except TimeoutError as error:
        result.update(status="TIMEOUT", reason=str(error))
    except (ValueError, RuntimeError, FileNotFoundError) as error:
        result.update(status="FAILED", reason=str(error))
    finalize_budget(result, time.monotonic() - started, seconds)
    write_json(output / "result.json", result)
    return result


def finalize_budget(result: dict[str, Any], elapsed: float, budget: float) -> None:
    result["wall_seconds"] = elapsed
    result["budget_exceeded"] = elapsed > budget
    if result["budget_exceeded"]:
        result["status_before_budget_check"] = result["status"]
        result["status"] = "BUDGET_EXCEEDED"
