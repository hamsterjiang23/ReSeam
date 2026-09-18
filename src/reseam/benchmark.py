"""Serial phase-capped experiment ledger. Incomplete runs remain visible."""

import json
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .audit import support_audit
from .downstream import evaluate, prepare_annotation
from .mesh import load_state, sha256
from .pipeline import run_unwrap, write_json
from .runtime import read_runtime


def run_benchmark(root: Path, config_path: Path) -> None:
    config = json.loads(config_path.read_text(encoding="utf-8-sig"))
    output = root / "artifacts/v1" / config["round_id"]
    output.mkdir(parents=True, exist_ok=False)
    write_json(output / "config.json", config)
    source_hashes = {
        str(p.relative_to(root)): sha256(p)
        for directory in ("src", "scripts", "native")
        for p in (root / directory).rglob("*")
        if p.is_file() and p.suffix in (".py", ".cpp", ".hpp")
    }
    runtime = read_runtime(root)
    external_hashes = {
        key: sha256(Path(runtime[key]))
        for key in ("blender", "optcuts_binary", "continuous_binary", "differentiable_python")
    }
    external_hashes["renderer"] = sha256(
        Path(runtime["differentiable_renderer_root"]) / "unlit_mesh_render.py"
    )
    write_json(
        output / "source.json",
        {
            "sha256": source_hashes,
            "external_sha256": external_hashes,
            "git_head": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip(),
            "dirty": bool(
                subprocess.check_output(["git", "status", "--porcelain"], cwd=root, text=True).strip()
            ),
            "runtime": runtime,
        },
    )
    started = time.monotonic()
    total_deadline = started + config.get("total_seconds", 28800)
    ledger: dict[str, Any] = {
        "round_id": config["round_id"],
        "started_utc": datetime.now(UTC).isoformat(),
        "v1_complete": False,
        "artist_assessment": "PENDING_HUMAN",
        "execution_complete": False,
        "runs": [],
        "downstream": [],
        "audits": [],
        "annotations": [],
    }
    for seed in config["seeds"]:
        for model in config["models"]:
            for method in config["methods"]:
                ledger["runs"].append(
                    {
                        "model": model,
                        "method": method,
                        "seed": seed,
                        "status": "PENDING",
                        "path": f"{model}/original/{method}/seed_{seed}",
                        "phase": "artist" if method == "reseam_artist" else "geometry",
                    }
                )
    ledger["downstream"] = [
        {
            "model": record["model"],
            "method": record["method"],
            "seed": record["seed"],
            "path": record["path"] + "/downstream",
            "status": "PENDING",
        }
        for record in ledger["runs"]
        if record["seed"] in config.get("downstream_seeds", [17])
    ]
    write_json(output / "ledger.json", ledger)
    for model in config["models"]:
        if total_deadline - time.monotonic() < 60:
            ledger["annotations"].append({"model": model, "status": "NOT_RUN_BUDGET"})
            continue
        try:
            scene = prepare_annotation(root, model, output / "annotations" / model)
            ledger["annotations"].append(
                {"model": model, "path": str(scene.relative_to(output)), "status": "PREPARED"}
            )
        except (RuntimeError, ValueError, TimeoutError) as error:
            ledger["annotations"].append({"model": model, "status": "FAILED", "reason": str(error)})
    for phase, cap in (
        ("geometry", config.get("geometry_seconds", 14400)),
        ("artist", config.get("downstream_seconds", 7200)),
    ):
        phase_deadline = min(
            total_deadline - config.get("report_reserve_seconds", 600), time.monotonic() + cap
        )
        for record in ledger["runs"]:
            if record["phase"] != phase:
                continue
            remaining = phase_deadline - time.monotonic()
            if remaining < config.get("run_seconds", 180):
                record["status"] = "NOT_RUN_PHASE_BUDGET"
                write_json(output / "ledger.json", ledger)
                continue
            folder = output / record["path"]
            print(f"RUN {record['model']} {record['method']} seed={record['seed']}", flush=True)
            record["status"] = "RUNNING"
            write_json(output / "ledger.json", ledger)
            method = record["method"]
            island_weight = 0.01 if method == "reseam_low_fragments" else 0
            try:
                result = run_unwrap(
                    root,
                    record["model"],
                    method,
                    folder,
                    record["seed"],
                    config.get("run_seconds", 180),
                    tau=0.025,
                    island_weight=island_weight,
                    max_candidates=config.get("max_candidates", 120),
                )
                record["status"] = result["status"]
                record["quality"] = result["quality"]
                record["wall_seconds"] = result["wall_seconds"]
            except (ValueError, TypeError, RuntimeError, OSError) as error:
                # Preserve one failed matrix cell; do not discard the rest of the batch.
                record.update(status="FAILED", reason=f"{type(error).__name__}: {error}")
            write_json(output / "ledger.json", ledger)
            print(f"DONE {record['model']} {method} {record['status']}", flush=True)
        if phase == "geometry":
            for model in config["models"]:
                state_path = output / model / "original/optcuts" / f"seed_{config['seeds'][0]}" / "state.json"
                remaining = phase_deadline - time.monotonic()
                if remaining < 360 or not state_path.exists():
                    ledger["audits"].append({"model": model, "status": "NOT_RUN_BUDGET_OR_INITIALIZATION"})
                    continue
                try:
                    result = support_audit(
                        load_state(state_path), read_runtime(root), output / "support_audit" / model
                    )
                    ledger["audits"].append(
                        {
                            "model": model,
                            "status": "COMPLETED" if result["missing_candidates"] == 0 else "INCOMPLETE_POOL",
                            "audited_count": result["audited_count"],
                            "missing_candidates": result["missing_candidates"],
                        }
                    )
                except (ValueError, TypeError, RuntimeError, OSError) as error:
                    ledger["audits"].append({"model": model, "status": "FAILED", "reason": str(error)})
                write_json(output / "ledger.json", ledger)
        else:
            # Downstream tests use the full first-seed method matrix, predefined in config.
            for record in ledger["runs"]:
                if record["seed"] not in config.get("downstream_seeds", [17]):
                    continue
                target = next(
                    item for item in ledger["downstream"] if item["path"] == record["path"] + "/downstream"
                )
                errors = (record.get("quality") or {}).get("errors", [])
                diagnostic = record["status"] == "UV_INVALID" and errors == ["DISTORTION_LIMIT"]
                target["diagnostic_only"] = diagnostic
                if record["status"] != "SUCCESS" and not diagnostic:
                    target["upstream_status"] = record["status"]
                    target["status"] = (
                        "NOT_RUN_INVALID_UV"
                        if record["status"] == "UV_INVALID"
                        else "NOT_RUN_UPSTREAM_FAILURE"
                    )
                elif phase_deadline - time.monotonic() < 60:
                    target["status"] = "NOT_RUN_PHASE_BUDGET"
                else:
                    print(f"DOWNSTREAM {record['model']} {record['method']}", flush=True)
                    target["status"] = "RUNNING"
                    write_json(output / "ledger.json", ledger)
                    try:
                        result = evaluate(
                            root,
                            output / record["path"],
                            min(600, phase_deadline - time.monotonic()),
                            differentiable=config.get("differentiable", True),
                        )
                        target.update(
                            status=result["status"],
                            blender_mae=result.get("blender_mae"),
                            differentiable_mae=result.get("differentiable_mae"),
                            reason=result.get("reason"),
                        )
                    except (ValueError, TypeError, RuntimeError, OSError) as error:
                        target.update(status="FAILED", reason=f"{type(error).__name__}: {error}")
                write_json(output / "ledger.json", ledger)
    ledger["seconds"] = time.monotonic() - started
    terminal = {
        "SUCCESS",
        "FAILED",
        "UV_INVALID",
        "TIMEOUT",
        "BUDGET_EXCEEDED",
        "NOT_RUN_INVALID_UV",
        "NOT_RUN_UPSTREAM_FAILURE",
        "COMPLETED",
        "PREPARED",
    }
    ledger["execution_complete"] = all(
        record["status"] in terminal
        for group in ("runs", "downstream", "audits", "annotations")
        for record in ledger[group]
    )
    ledger["finished_utc"] = datetime.now(UTC).isoformat()
    write_json(output / "ledger.json", ledger)
    from .report import generate_report

    report_started = time.monotonic()
    generate_report(output)
    ledger["report_seconds"] = time.monotonic() - report_started
    ledger["seconds"] = time.monotonic() - started
    ledger["total_budget_exceeded"] = time.monotonic() > total_deadline
    if ledger["total_budget_exceeded"]:
        ledger["execution_complete"] = False
    ledger["finished_utc"] = datetime.now(UTC).isoformat()
    write_json(output / "ledger.json", ledger)
    summary_path = output / "summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    summary["execution_complete"] = ledger["execution_complete"]
    summary["total_budget_exceeded"] = ledger["total_budget_exceeded"]
    write_json(summary_path, summary)
    print(
        json.dumps(
            {
                "round": str(output),
                "execution_complete": ledger["execution_complete"],
                "v1_complete": False,
                "seconds": ledger["seconds"],
            }
        ),
        flush=True,
    )
