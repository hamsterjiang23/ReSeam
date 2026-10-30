"""Replay explicit Agent plans through public tools with frozen per-path receipts.

This is an executor, not a surrogate LLM. Decisions, revisions, protected regions,
and target reviews must be supplied in the input config. Every method gets its
own immutable Experiment and a cumulative 40-minute default tool budget.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import cast

import numpy as np

from reseam.agent_tools.experiment import Experiment, code_manifest, write_step_report
from reseam.agent_tools.registry import TOOLS
from reseam.agent_tools.state import load_snapshot, read_object, write_json

ROOT = Path(__file__).resolve().parents[1]


def obj(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        raise TypeError("JSON_OBJECT_REQUIRED")
    return cast(dict[str, object], value)


def items(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list):
        raise TypeError("JSON_OBJECT_LIST_REQUIRED")
    return [obj(item) for item in value]


def resolve(value: object, context: dict[str, object]) -> object:
    """Only explicit {$ref: 'name.key'} objects are interpolated; strings stay literal."""
    if isinstance(value, dict):
        if set(value) == {"$ref"}:
            route = value["$ref"]
            if not isinstance(route, str):
                raise TypeError("REFERENCE_PATH_MUST_BE_STRING")
            current: object = context
            for key in route.split("."):
                if not isinstance(current, dict) or key not in current:
                    raise ValueError("UNRESOLVED_RESULT_REFERENCE:" + route)
                current = current[key]
            return current
        return {str(k): resolve(v, context) for k, v in value.items()}
    if isinstance(value, list):
        return [resolve(item, context) for item in value]
    return value


def _label(value: object) -> str:
    if not isinstance(value, str) or not value or any(c not in "abcdefghijklmnopqrstuvwxyz0123456789_-" for c in value):
        raise ValueError("SAFE_LABEL_REQUIRED")
    return value


def validate_config(config: dict[str, object]) -> None:
    cases = items(config.get("cases"))
    names: set[str] = set()
    for case in cases:
        name = _label(case.get("name"))
        if name in names:
            raise ValueError("DUPLICATE_CASE_NAME")
        names.add(name)
        load_snapshot(Path(str(case["parent_snapshot"])))
        seconds = float(str(case.get("budget_seconds", 2400)))
        if not math.isfinite(seconds) or seconds <= 0:
            raise ValueError("POSITIVE_FINITE_BUDGET_REQUIRED")
        prior = float(str(case.get("prior_tool_seconds", 0)))
        if not math.isfinite(prior) or prior < 0:
            raise ValueError("PRIOR_TOOL_SECONDS_FINITE_NONNEGATIVE_REQUIRED")
        if {"parent", "current", "profile"} & obj(case.get("bindings", {})).keys():
            raise ValueError("BINDINGS_CANNOT_OVERRIDE_PARENT_CURRENT_PROFILE")
        steps: set[str] = set()
        for action in items(case.get("actions", [])):
            label = _label(action.get("label"))
            if label in steps:
                raise ValueError("DUPLICATE_ACTION_LABEL")
            steps.add(label)
            if str(action.get("tool")) not in TOOLS:
                raise ValueError("UNKNOWN_TOOL:" + str(action.get("tool")))
            obj(action.get("request", {}))
            if action.get("on_failure", "stop") not in ("stop", "continue"):
                raise ValueError("ON_FAILURE_MUST_BE_STOP_OR_CONTINUE")
            if not isinstance(action.get("reason"), str) or not action["reason"]:
                raise ValueError("ACTION_REASON_REQUIRED")


def run_config(config_path: Path, output: Path) -> None:
    config = read_object(config_path)
    validate_config(config)
    output.mkdir(parents=True, exist_ok=False)
    write_json(output / "input.json", config)
    matrix: list[dict[str, object]] = []
    for case in items(config["cases"]):
        name = str(case["name"])
        parent = str(case["parent_snapshot"])
        snapshot = load_snapshot(Path(parent))
        budget = float(str(case.get("budget_seconds", 2400)))
        spent = float(str(case.get("prior_tool_seconds", 0)))
        prior_manifest = case.get("prior_code_manifest")
        if prior_manifest is not None and prior_manifest != code_manifest(ROOT):
            raise ValueError("RESUMED_CODE_DIFFERS_START_NEW_ROUND")
        experiment = Experiment(ROOT, output / name, {
            "purpose": "Explicit MeshQuery/ReSeam plan replay; no generated choices attributed to LLM",
            "source_config": str(config_path.resolve()), "case": case, "budget_seconds": budget,
            "scope": "UV+BaseColor", "human_accepted": False, "gpu_verified": False,
            "llm_token_cost": None, "llm_fee": None,
        }, freeze=True)
        context: dict[str, object] = {
            "parent": {"snapshot": parent, "state_hash": snapshot.state_hash},
            "current": {"snapshot": parent, "state_hash": snapshot.state_hash},
            "profile": case.get("profile", {}),
            **obj(case.get("bindings", {})),
        }
        results: list[dict[str, object]] = []
        row: dict[str, object] = {"name": name, "asset": case.get("asset"), "method": case.get("method"),
            "parent_snapshot": parent, "status": "RUNNING", "results": results,
            "snapshot": parent, "prior_tool_seconds": spent, "budget_seconds": budget,
            "quality_accepted": None, "quality_status": "NOT_EVALUATED", "appearance_accepted": None}
        matrix.append(row)
        write_json(output / name / "context.json", context)
        stopped = False
        rejected = False
        for action in items(case.get("actions", [])):
            label = str(action["label"])
            remaining = budget - spent
            if stopped or remaining <= 0:
                results.append({"label": label, "status": "NOT_RUN",
                                "reason": "DEPENDENT_STEP_FAILED" if stopped else "PATH_BUDGET_EXHAUSTED"})
                if remaining <= 0:
                    row["status"] = "BUDGET_EXHAUSTED"
                continue
            try:
                request = obj(resolve(action.get("request", {}), context))
            except ValueError as error:
                if not str(error).startswith("UNRESOLVED_RESULT_REFERENCE:"):
                    raise
                skipped = {"status": "NOT_RUN", "reason": str(error), "execution_success": False}
                context[label] = skipped
                results.append({"label": label, "tool": action["tool"], "result": skipped})
                rejected = True
                if action.get("on_failure", "stop") == "stop":
                    stopped = True
                    row["status"] = "FAILED"
                    row["failure_step"] = label
                write_json(output / name / "context.json", context)
                write_json(output / "matrix.json", matrix)
                continue
            if str(action["tool"]) != "asset.import":
                request.setdefault("snapshot", obj(context["current"])["snapshot"])
            request["timeout_seconds"] = min(float(str(request.get("timeout_seconds", 300))), remaining)
            decision = action.get("decision")
            try:
                result = experiment.call(str(action["tool"]), request, label, reason=str(action["reason"]),
                    decision=obj(decision) if decision is not None else None,
                    comparison=str(action["comparison"]) if action.get("comparison") is not None else None,
                    research_fields=tuple(str(field) for field in cast(list[object], action.get("research_fields", []))))
            except ValueError as error:
                # Code freeze and invalid experimental pairing are protocol violations, not candidate failures.
                row["status"] = "PROTOCOL_ERROR"
                row["error"] = str(error)
                write_json(output / "matrix.json", matrix)
                raise
            spent = float(str(case.get("prior_tool_seconds", 0))) + float(str(experiment.summary()["wall_seconds"]))
            context[label] = result
            results.append({"label": label, "tool": action["tool"], "result": result})
            if isinstance(result.get("snapshot"), str) and result.get("status") == "OK":
                child = load_snapshot(Path(str(result["snapshot"])))
                if child.state_hash != obj(context["current"])["state_hash"]:
                    row["quality_accepted"] = None
                    row["quality_status"] = "NOT_EVALUATED"
                context["current"] = {"snapshot": result["snapshot"], "state_hash": child.state_hash}
                row["snapshot"] = result["snapshot"]
            if action["tool"] == "quality.validate":
                accepted = result.get("quality_accepted")
                row["quality_accepted"] = accepted if isinstance(accepted, bool) else None
                row["quality_status"] = ("ACCEPTED" if accepted else "REJECTED") if isinstance(accepted, bool) else "NOT_EVALUATED"
            if result.get("status") != "OK":
                rejected = True
                if action.get("on_failure", "stop") == "stop":
                    stopped = True
                    row["status"] = "FAILED"
                    row["failure_step"] = label
            row["tool_seconds"] = spent
            write_json(output / name / "context.json", context)
            write_json(output / name / "summary.json", experiment.summary())
            write_json(output / "matrix.json", matrix)
            write_step_report(output / name)
            print(json.dumps({"case": name, "step": label, "status": result.get("status")}), flush=True)
        if row["status"] == "RUNNING":
            row["status"] = "EXECUTED_WITH_REJECTIONS" if rejected else "EXECUTED"
        row["tool_seconds"] = spent
        row["code_manifest"] = experiment.manifest
        row["context"] = str(output / name / "context.json")
        write_json(output / name / "summary.json", experiment.summary())
        write_json(output / "matrix.json", matrix)
        write_step_report(output / name)
    lines = ["# MeshQuery 工具实验", "", "执行成功、数值通过与外观认可分别记录。`[opinion]`", "",
             "|资产／路径|执行状态|质量通过|累计工具秒|步骤证据|", "|---|---|---|---|---|"]
    for row in matrix:
        name = str(row["name"])
        lines.append(f"|{name}|{row['status']}|{row['quality_status']}|{float(str(row['tool_seconds'])):.3f}"
                     f"|[步骤]({name}/steps.md) `[verified: matrix.json {name}]`|")
    (output / "report.md").write_text("\n".join(lines)+"\n", encoding="utf-8")


def make_fixtures(output: Path) -> None:
    """Create textured source assets; all experiments still import through asset.import."""
    import trimesh
    from PIL import Image
    from trimesh.visual.material import PBRMaterial
    from trimesh.visual.texture import TextureVisuals

    from reseam.mesh import make_mesh, state_from_corner_uv, write_obj

    output.mkdir(parents=True, exist_ok=False)
    color = np.zeros((64, 64, 4), dtype=np.uint8)
    color[..., 0] = np.arange(64)[None, :] * 3 + 32
    color[..., 1] = np.arange(64)[:, None] * 3 + 32
    color[..., 2] = 96
    color[..., 3] = 255
    records: list[dict[str, object]] = []
    n = 24
    for name in ("cylinder", "cone"):
        angle = np.arange(n) * 2*np.pi / n
        ring = np.column_stack((np.cos(angle), np.sin(angle), np.zeros(n)))
        vertices = np.concatenate((ring, ring+[0, 0, 2])) if name == "cylinder" else np.vstack((ring, [0, 0, 2]))
        vertices = vertices.astype(np.float32).astype(np.float64)
        faces: list[list[int]] = []
        corners: list[list[list[float]]] = []
        for i in range(n):
            j = (i+1) % n
            a, b = i/n, (i+1)/n
            if name == "cylinder":
                faces.extend([[i, j, n+j], [i, n+j, n+i]])
                corners.extend([[[a, 0], [b, 0], [b, 1]], [[a, 0], [b, 1], [a, 1]]])
            else:
                faces.append([i, j, n])
                corners.append([[a, 0], [b, 0], [(a+b)/2, 1]])
        face_array = np.asarray(faces, dtype=np.int64)
        uv = np.asarray(corners, dtype=float)
        state = state_from_corner_uv(make_mesh(vertices, face_array), uv,
                                     corner_ids=np.arange(len(faces)*3).reshape(-1, 3))
        folder = output / name
        folder.mkdir()
        write_obj(folder / "source.obj", state)
        material = PBRMaterial(baseColorTexture=Image.fromarray(color), roughnessFactor=.8)
        reference = trimesh.Trimesh(vertices=vertices[face_array].reshape(-1, 3),
                                   faces=np.arange(len(faces)*3).reshape(-1, 3), process=False,
                                   visual=TextureVisuals(uv=uv.reshape(-1, 2), material=material))
        reference.export(folder / "reference.glb")
        Image.fromarray(color).save(folder / "source.png")
        records.append({"name": name, "path": str((folder / "source.obj").resolve()),
                        "reference": str((folder / "reference.glb").resolve()),
                        "longitudinal_seam_edges": [[0, n]], "resolution": 512,
                        "source_kind": "constructed textured geometry; not a real asset result"})
    write_json(output / "fixtures.json", {"assets": records})


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase", choices=("validate", "run", "fixtures"), default="run")
    parser.add_argument("--config", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.phase == "fixtures":
        if args.output is None:
            parser.error("--output is required")
        make_fixtures(args.output.resolve())
    else:
        if args.config is None:
            parser.error("--config is required")
        if args.phase == "validate":
            validate_config(read_object(args.config))
            print(json.dumps({"status": "VALID_CONFIG", "tools_executed": False}))
        else:
            if args.output is None:
                parser.error("--output is required")
            run_config(args.config.resolve(), args.output.resolve())


if __name__ == "__main__":
    main()
