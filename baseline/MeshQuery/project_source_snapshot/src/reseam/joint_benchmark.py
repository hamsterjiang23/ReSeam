"""Frozen serial matrix; every scheduled row survives failure and budget exhaustion."""

import ast
import importlib.metadata
import json
import platform
import shutil
import subprocess
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

import numpy as np

from .joint_budget import JointBudget
from .joint_downstream import evaluate_joint, reference_views
from .joint_search import TRACKS, run_joint
from .joint_state import load_joint
from .joint_types import JointProfile
from .mesh import sha256
from .pipeline import write_json
from .runtime import read_runtime


def snapshot(root: Path, folder: Path) -> dict[str, Any]:
    hashes = {}
    for directory in ("src", "scripts", "native"):
        for path in (root / directory).rglob("*"):
            if path.is_file() and path.suffix in (".py", ".cpp", ".hpp"):
                relative = path.relative_to(root)
                target = folder / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, target)
                hashes[str(relative)] = sha256(path)
    runtime = read_runtime(root)
    manifest_path = root / "assets/v1/manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    assets = {}
    for model in manifest["models"]:
        for entry in model["files"]:
            actual = sha256(root / "assets/v1" / entry["path"])
            if actual != entry["sha256"]:
                raise ValueError("FIXED_ASSET_HASH_MISMATCH:" + entry["path"])
            assets[entry["path"]] = actual
    return {
        "source": hashes,
        "assets": assets,
        "manifest_sha256": sha256(manifest_path),
        "python": platform.python_version(),
        "packages": {
            name: importlib.metadata.version(name)
            for name in ("numpy", "scipy", "trimesh", "shapely", "pillow")
        },
        "backend_paths": {
            k: str(Path(runtime[k]).resolve()) for k in ("blender", "optcuts_binary", "continuous_binary")
        },
        "backend_sha256": {
            k: sha256(Path(runtime[k])) for k in ("blender", "optcuts_binary", "continuous_binary")
        },
        "git_head": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip(),
        "dirty": bool(subprocess.check_output(["git", "status", "--porcelain"], cwd=root, text=True).strip()),
    }


def verify_frozen(root: Path, snapshot_info: dict[str, Any]) -> None:
    for relative, expected in snapshot_info["source"].items():
        if sha256(root / relative) != expected:
            raise ValueError("SOURCE_CHANGED_AFTER_FREEZE:" + relative)
    for relative, expected in snapshot_info["assets"].items():
        if sha256(root / "assets/v1" / relative) != expected:
            raise ValueError("ASSET_CHANGED_AFTER_FREEZE:" + relative)
    runtime = read_runtime(root)
    for key, expected in snapshot_info["backend_sha256"].items():
        path = Path(runtime[key]).resolve()
        if str(path) != snapshot_info["backend_paths"][key] or sha256(path) != expected:
            raise ValueError("BACKEND_CHANGED_AFTER_FREEZE:" + key)


def run_joint_benchmark(root: Path, config_path: Path) -> None:
    config = json.loads(config_path.read_text(encoding="utf-8-sig"))
    if config["models"] != ["energy_blade", "dragon"] or config["seeds"] != [17, 29, 43]:
        raise ValueError("Formal matrix requires both fixed models and seeds 17/29/43")
    if config["tracks"] != list(TRACKS):
        raise ValueError("Formal matrix requires all four tracks")
    folder = root / "artifacts/v2_joint" / config["round_id"]
    folder.mkdir(parents=True, exist_ok=False)
    frozen = snapshot(root, folder / "source_snapshot")
    parsed = ast.parse((root / "src/reseam/joint_review.py").read_text(encoding="utf-8"))
    rubric = next(
        node.value.value
        for node in ast.walk(parsed)
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "rubric" for target in node.targets)
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str)
    )
    (folder / "review_rubric.txt").write_text(rubric, encoding="utf-8")
    frozen["review_prompt_sha256"] = sha256(folder / "review_rubric.txt")
    write_json(folder / "source.json", frozen)
    write_json(folder / "freeze_status.json", {"status": "RUNNING"})
    write_json(
        folder / "anonymous_schedule.json",
        {
            f"{model}_seed_{seed}": {
                f"sample_{chr(65 + i)}": TRACKS[int(t)]
                for i, t in enumerate(np.random.default_rng(seed).permutation(4))
            }
            for model in config["models"]
            for seed in config["seeds"]
        },
    )
    write_json(folder / "protocol.json", {**config, "profile": asdict(JointProfile()), "evidence": frozen})
    (folder / "hypotheses.md").write_text(
        "# 冻结前假设\n\n近似复用可能提高有效纹素密度；接缝反馈可能改善组级排布。两者均待验证。\n"
        "四轨道共享初值、门禁和预算。失败不剔除。两模型是开发回归集；正式反馈不用于本轮调参。\n"
        "接受规则：全部技术门禁通过且G严格增加至少0.0001。代理评价和人工验收分开。\n",
        encoding="utf-8",
    )
    rows = [
        {
            "model": model,
            "seed": seed,
            "track": track,
            "status": "PENDING",
            "downstream_status": "PENDING",
            "path": f"{model}/seed_{seed}/{track}",
        }
        for model in config["models"]
        for seed in config["seeds"]
        for track in TRACKS
    ]
    ledger = JointBudget(root / "artifacts/v2_joint/budget.json")
    write_json(folder / "matrix.json", rows)
    for row in rows:
        print(f"RUN {row['model']} {row['seed']} {row['track']}", flush=True)
        try:
            verify_frozen(root, frozen)
            with ledger.run(
                config["round_id"] + "/" + row["path"], "geometry", config["run_seconds"], require_full=True
            ) as deadline:
                result = run_joint(
                    root,
                    row["model"],
                    row["track"],
                    row["seed"],
                    folder / row["path"],
                    deadline - time.monotonic(),
                    config["candidate_seconds"],
                )
                row.update({k: result[k] for k in ("status", "wall_seconds", "budget_overrun")})
                row["initial_hash"] = result.get("initial_hash")
                row["quality"] = result.get("quality")
                row["accepted"] = result.get("accepted", 0)
                row["reuse_groups"] = result.get("reuse_groups", 0)
                row["reason"] = result.get("reason")
                verify_frozen(root, frozen)
        except (TimeoutError, ValueError, RuntimeError, OSError) as error:
            row.update(
                status="NOT_RUN_BUDGET" if isinstance(error, TimeoutError) else "FAILED", reason=str(error)
            )
        write_json(folder / "matrix.json", rows)
        print({k: row.get(k) for k in ("status", "accepted", "reuse_groups", "reason")}, flush=True)
    for model in config["models"]:
        for seed in config["seeds"]:
            matching = [r for r in rows if r["model"] == model and r["seed"] == seed]
            hashes = {r.get("initial_hash") for r in matching if r.get("initial_hash")}
            for row in matching:
                row["paired_initialization"] = (
                    "INIT_MISMATCH"
                    if len(hashes) > 1
                    else "MATCH"
                    if len(hashes) == 1 and all(r.get("initial_hash") for r in matching)
                    else "INCOMPLETE"
                )
    write_json(folder / "matrix.json", rows)
    references = {}
    for model in config["models"]:
        source = next((r for r in rows if r["model"] == model and r["status"] == "SUCCESS"), None)
        if source is None:
            continue
        try:
            verify_frozen(root, frozen)
            with ledger.run(config["round_id"] + "/reference/" + model, "downstream", 240) as deadline:
                reference = folder / "reference" / model
                reference_views(
                    root,
                    model,
                    load_joint(folder / source["path"] / "joint_state.json"),
                    reference,
                    deadline - time.monotonic(),
                )
                references[model] = reference
        except (TimeoutError, ValueError, RuntimeError, OSError) as error:
            write_json(folder / f"reference_failure_{model}.json", {"reason": str(error)})
    for row in rows:
        if row["status"] != "SUCCESS" or row["model"] not in references:
            row["downstream_status"] = "NOT_RUN_NO_VALID_INPUT_OR_REFERENCE"
        else:
            try:
                verify_frozen(root, frozen)
                with ledger.run(
                    config["round_id"] + "/downstream/" + row["path"],
                    "downstream",
                    config["downstream_seconds"],
                    require_full=True,
                ) as deadline:
                    data = evaluate_joint(
                        root,
                        row["model"],
                        load_joint(folder / row["path"] / "joint_state.json"),
                        folder / row["path"] / "downstream",
                        references[row["model"]],
                        deadline - time.monotonic(),
                        JointProfile(),
                    )
                    row["downstream_status"] = data["status"]
                    verify_frozen(root, frozen)
            except (TimeoutError, ValueError, RuntimeError, OSError) as error:
                row.update(downstream_status="FAILED", downstream_reason=str(error))
        write_json(folder / "matrix.json", rows)
        print({"path": row["path"], "downstream": row["downstream_status"]}, flush=True)
    try:
        verify_frozen(root, frozen)
        write_json(folder / "freeze_status.json", {"status": "PASS"})
    except (ValueError, OSError) as error:
        write_json(folder / "freeze_status.json", {"status": "FAILED", "reason": str(error)})
        raise
