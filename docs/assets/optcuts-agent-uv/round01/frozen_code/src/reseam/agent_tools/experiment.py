"""Append-only experiment evidence around the same public tools an Agent uses."""

import sys
import time
from pathlib import Path

import numpy as np

from ..mesh import chart_labels
from .runner import _versions, run_tool
from .state import file_hash, load_snapshot, object_hash, read_object, write_json


def code_manifest(root: Path) -> dict[str, str]:
    paths = sorted((root / "src/reseam").rglob("*.py"))
    paths += sorted((root / "scripts").glob("*.py"))
    paths += [path for path in (root / "uv.lock", root / "pyproject.toml", root / "configs/runtime.local.json")
              if path.is_file()]
    manifest = {str(path.relative_to(root)): file_hash(path) for path in paths}
    runtime_path = root / "configs/runtime.local.json"
    if runtime_path.is_file():
        runtime = read_object(runtime_path)
        for name in ("optcuts_binary", "blender", "continuous_binary"):
            path = Path(str(runtime.get(name, "")))
            manifest[f"backend:{name}"] = file_hash(path) if path.is_file() else "NOT_AVAILABLE"
    manifest["environment:python_and_packages"] = object_hash({"python": sys.version, "packages": _versions()})
    return manifest


def snapshot_metrics(path: str | None) -> dict[str, object]:
    if path is None:
        return {}
    snapshot = load_snapshot(Path(path))
    surface = snapshot.state.surface
    labels = chart_labels(surface)
    shared = {face for group in snapshot.state.groups for face in group.first_faces + group.second_faces}
    return {"state_hash": snapshot.state_hash, "asset_id": snapshot.asset_id,
            "faces": len(surface.mesh.faces), "vertices": len(surface.mesh.vertices),
            "chart_count": len(np.unique(labels)), "shared_group_count": len(snapshot.state.groups),
            "shared_surface_fraction": float(surface.mesh.areas[list(shared)].sum() / surface.mesh.areas.sum()),
            "geometry_hash": object_hash({"vertices": surface.mesh.vertices.tolist(),
                                          "faces": surface.mesh.faces.tolist()})}


def changed_fields(before: dict[str, object], after: dict[str, object], prefix: str = "") -> set[str]:
    """A profile change must declare the particular parameter, not the whole profile."""
    changed: set[str] = set()
    for key in before.keys() | after.keys():
        first, second = before.get(key), after.get(key)
        name = prefix + key
        if isinstance(first, dict) and isinstance(second, dict):
            changed.update(changed_fields(first, second, name + "."))
        elif first != second:
            changed.add(name)
    return changed


class Experiment:
    """Frozen protocol, immutable per-step receipts, recoverable aggregate indexes."""

    def __init__(self, root: Path, output: Path, protocol: dict[str, object], *, freeze: bool = True) -> None:
        self.root, self.output = root.resolve(), output.resolve()
        self.output.mkdir(parents=True, exist_ok=False)
        self.manifest = code_manifest(self.root)
        self.freeze = freeze
        self.steps: list[dict[str, object]] = []
        write_json(self.output / "protocol.json", {**protocol, "source_sha256": self.manifest,
                   "code_frozen": freeze, "protocol_hash": object_hash(protocol)})
        write_json(self.output / "steps.json", self.steps)

    def call(
        self, tool: str, request: dict[str, object], label: str, *, reason: str,
        comparison: str | None = None, decision: dict[str, object] | None = None,
        research_fields: tuple[str, ...] = (),
    ) -> dict[str, object]:
        if not label or any(char not in "abcdefghijklmnopqrstuvwxyz0123456789_-" for char in label):
            raise ValueError("SAFE_UNIQUE_STEP_LABEL_REQUIRED")
        if any(step["label"] == label for step in self.steps):
            raise ValueError("STEP_LABEL_ALREADY_EXISTS")
        if comparison is not None and comparison not in {step["label"] for step in self.steps}:
            raise ValueError("COMPARISON_STEP_NOT_FOUND")
        current_manifest = code_manifest(self.root)
        if self.freeze and current_manifest != self.manifest:
            raise ValueError("FROZEN_CODE_CHANGED_START_NEW_ROUND")
        before = str(request["snapshot"]) if "snapshot" in request else None
        before_metrics = snapshot_metrics(before)
        if comparison is not None:
            control = next(step for step in self.steps if step["label"] == comparison)
            if control["before"] != before_metrics:
                raise ValueError("PAIRED_ABLATION_REQUIRES_IDENTICAL_PARENT")
            previous = read_object(Path(str(control["request"])))
            changed = changed_fields(previous, request)
            if control["tool"] != tool or not changed <= set(research_fields):
                raise ValueError("PAIRED_ABLATION_CHANGED_UNDECLARED_VARIABLE")
        request_path = self.output / "requests" / f"{label}.json"
        write_json(request_path, request)
        started = time.monotonic()
        result = run_tool(tool, request_path, self.output / label)
        elapsed = time.monotonic() - started
        after = str(result["snapshot"]) if "snapshot" in result else before
        after_metrics = snapshot_metrics(after)
        deltas = {key: float(str(value)) - float(str(before_metrics[key]))
                  for key, value in after_metrics.items()
                  if type(value) in (int, float) and type(before_metrics.get(key)) in (int, float)}
        same_geometry = before_metrics.get("geometry_hash") == after_metrics.get("geometry_hash")
        code_unchanged = code_manifest(self.root) == current_manifest
        receipt: dict[str, object] = {
            "label": label, "tool": tool, "reason": reason, "agent_decision": decision,
            "comparison_step": comparison, "before_snapshot": before, "after_snapshot": after,
            "research_fields": list(research_fields),
            "before": before_metrics, "after": after_metrics, "delta": deltas,
            "original_geometry_unchanged": same_geometry, "code_unchanged_during_step": code_unchanged,
            "request": str(request_path), "request_hash": file_hash(request_path),
            "wall_seconds_including_runner": elapsed, "result": result,
            "status_is_not_quality_acceptance": True,
        }
        write_json(self.output / "receipts" / f"{label}.json", receipt)
        self.steps.append(receipt)
        write_json(self.output / "steps.json", self.steps)
        if not same_geometry and before is not None:
            raise ValueError("ORIGINAL_GEOMETRY_CHANGED")
        if self.freeze and not code_unchanged:
            raise ValueError("CODE_CHANGED_DURING_OPERATION_START_NEW_ROUND")
        return result

    def summary(self) -> dict[str, object]:
        return {"steps": len(self.steps), "wall_seconds": sum(float(str(step["wall_seconds_including_runner"]))
                   for step in self.steps), "failed_steps": [step["label"] for step in self.steps
                   if isinstance(step["result"], dict) and step["result"].get("status") != "OK"],
                "code_unchanged": code_manifest(self.root) == self.manifest}


def write_step_report(output: Path) -> Path:
    """A receipt digest includes rejected steps instead of selecting only the best."""
    index = read_object(output / "protocol.json")
    import json
    steps = json.loads((output / "steps.json").read_text(encoding="utf-8"))
    if not isinstance(steps, list):
        raise TypeError("STEP_LIST_REQUIRED")
    lines = ["# 关键步骤修正前后记录", "", f"代码冻结：{index.get('code_frozen')}。`[verified: protocol.json]`", "",
             "|步骤|工具|修改依据|岛数前→后|共享组前→后|状态|秒|证据|", "|---|---|---|---|---|---|---|---|"]
    for step in steps:
        before, after, result = step["before"], step["after"], step["result"]
        label = step["label"]
        reason = str(step["reason"]).replace("|", "\\|").replace("\n", " ")
        lines.append(f"|{label}|{step['tool']}|{reason}|{before.get('chart_count', '—')}→{after.get('chart_count', '—')}"
                     f"|{before.get('shared_group_count', '—')}→{after.get('shared_group_count', '—')}"
                     f"|{result.get('status')}|{step['wall_seconds_including_runner']:.3f}"
                     f"|[记录](receipts/{label}.json) `[verified: receipts/{label}.json]`|")
    lines += ["", "完整质量结论以各次 validation.json 为准；操作成功不等于质量通过。`[opinion]`", ""]
    path = output / "steps.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path
