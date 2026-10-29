"""Prepare four immutable UV branches; semantic candidates are supplied by the Agent."""

import argparse
import math
import time
from pathlib import Path

import numpy as np

from reseam.agent_tools.experiment import Experiment, snapshot_metrics, write_step_report
from reseam.agent_tools.regions import face_selection
from reseam.agent_tools.state import file_hash, load_snapshot, read_object, write_json
from reseam.budget_state import owners_and_members

DEFAULT_PROFILE: dict[str, object] = {
    "resolution": 1024, "gap_pixels": 16, "border_pixels": 8, "balance_density": True,
}
METHODS = ("no_share", "optcuts_no_share", "geometry_auto", "agent")


def _object(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        raise TypeError("OBJECT_REQUIRED")
    return value


def _objects(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list):
        raise TypeError("OBJECT_LIST_REQUIRED")
    return [_object(item) for item in value]


def _ids(value: object) -> set[int]:
    if not isinstance(value, list) or any(type(face) is not int for face in value):
        raise TypeError("INTEGER_FACE_LIST_REQUIRED")
    return set(value)


def _path(base: Path, value: object) -> Path:
    if not isinstance(value, str) or not value:
        raise ValueError("NONEMPTY_PATH_REQUIRED")
    path = Path(value)
    return (path if path.is_absolute() else base / path).resolve()


def packing_owners(snapshot: str) -> int:
    owners, _ = owners_and_members(load_snapshot(Path(snapshot)).state)
    return len(np.unique(owners))


def retry_faces(report: dict[str, object], first: set[int], second: set[int]) -> set[int]:
    """Deterministic evidence-only exclusions; never use hand-selected initial exceptions."""
    choices: list[set[int]] = []
    failed = report.get("failed_samples", [])
    if isinstance(failed, list):
        source = {int(sample["source_face"]) for sample in failed
                  if isinstance(sample, dict) and type(sample.get("source_face")) is int}
        if source:
            choices.append(source)
    diagnostics = _object(report.get("diagnostics", {}))
    for attempt in _objects(diagnostics.get("attempts", [])):
        bad = set()
        suggested = attempt.get("suggested_exclusive_faces", [])
        if isinstance(suggested, list):
            bad.update(face for face in suggested if type(face) is int)
        geometry = _object(attempt.get("geometry", {}))
        errors = geometry.get("hard_errors", geometry.get("errors", []))
        if isinstance(errors, list):
            for error in errors:
                if isinstance(error, str) and error.startswith("OVERLAP_CORRESPONDENCE:"):
                    fields = error.split(":")[1:]
                    if fields and all(field.isdecimal() for field in fields):
                        bad.update(set(map(int, fields)) & second)
        failed_faces = geometry.get("failed_faces", [])
        if isinstance(failed_faces, list):
            bad.update(face for face in failed_faces if type(face) is int)
        bad &= first | second
        if bad:
            choices.append(bad)
    choices = [choice & (first | second) for choice in choices if choice & (first | second)]
    return min(choices, key=lambda choice: (len(choice), sorted(choice))) if choices else set()


def bind_request(request: dict[str, object], bindings: dict[str, dict[str, object]]) -> dict[str, object]:
    """Only explicit recipe tokens may bind new snapshots or proposals."""
    result = dict(request)
    for key in ("snapshot", "proposal"):
        value = result.get(key)
        if not isinstance(value, str):
            continue
        if not value.startswith("$"):
            raise ValueError(f"REPLAY_REQUIRES_BOUND_{key.upper()}")
        token = value[1:]
        label, field = token.rsplit(".", 1) if "." in token else (token, "snapshot")
        if label not in bindings or field not in bindings[label]:
            raise ValueError("REPLAY_TOKEN_UNAVAILABLE:" + token)
        result[key] = bindings[label][field]
    result.pop("state_hash", None)
    return result


class Preparation:
    def __init__(self, plan_path: Path, output: Path, *, freeze: bool = True) -> None:
        self.started = time.monotonic()
        self.failure: dict[str, str] | None = None
        self.completed = False
        self.plan_path = plan_path.resolve()
        self.plan = read_object(self.plan_path)
        base = self.plan_path.parent
        self.source = load_snapshot(_path(base, self.plan["snapshot"]))
        decision_path = _path(base, self.plan["decisions"])
        replay_path = _path(base, self.plan["agent_replay"])
        self.decisions, self.replay = read_object(decision_path), read_object(replay_path)
        if self.decisions.get("asset_id", self.source.asset_id) != self.source.asset_id:
            raise ValueError("CANDIDATES_BELONG_TO_DIFFERENT_ASSET")
        count = len(self.source.state.surface.mesh.faces)
        self.protected = set(face_selection(self.decisions["protected_faces"], count, allow_empty=True))
        self.pairs = _objects(self.decisions["pairs"])
        names = [str(pair["name"]) for pair in self.pairs]
        if len(set(names)) != len(names) or any(not name or any(
            char not in "abcdefghijklmnopqrstuvwxyz0123456789_-" for char in name) for name in names):
            raise ValueError("SAFE_UNIQUE_PAIR_NAMES_REQUIRED")
        for pair in self.pairs:
            pair["first_faces"] = face_selection(pair["first_faces"], count)
            pair["second_faces"] = face_selection(pair["second_faces"], count)
            if _ids(pair["first_faces"]) & _ids(pair["second_faces"]):
                raise ValueError("PAIR_MEMBERS_MUST_BE_DISJOINT")
        self.profile = {**DEFAULT_PROFILE, **_object(self.plan.get("geometry_profile", {}))}
        self.timeout = float(str(self.plan.get("tool_timeout_seconds", 300)))
        self.budget = float(str(self.plan.get("automatic_budget_seconds", 600)))
        if not all(math.isfinite(value) and value > 0 for value in (self.timeout, self.budget)) or self.budget > 600:
            raise ValueError("POSITIVE_TIMEOUT_AND_AUTOMATIC_BUDGET_AT_MOST_600_REQUIRED")
        development = self.plan.get("development_receipts", [])
        if not isinstance(development, list):
            raise TypeError("DEVELOPMENT_RECEIPTS_LIST_REQUIRED")
        self.development_receipts = [_path(base, value) for value in development]
        files = {self.plan_path, decision_path, replay_path, self.source.root / "snapshot.json",
                 self.source.root / "arrays.npz", *self.development_receipts}
        if "reference_glb" in self.source.metadata:
            files.add(Path(str(self.source.metadata["reference_glb"])).resolve())
        for record in _objects(self.source.metadata.get("source_manifest", [])):
            files.add(Path(str(record["path"])).resolve())
        self.pinned = {str(path): file_hash(path) for path in sorted(files)}
        self.experiment = Experiment(Path(__file__).resolve().parents[1], output, {
            "input_plan": str(self.plan_path), "input_sha256": self.pinned, "geometry_profile": self.profile,
            "scope": "geometry preparation only; OptCuts regional wing baseline, not whole-model OptCuts",
            "semantic_candidate_source": "same frozen Agent-provided semantic pool for geometry_auto and agent",
            "automatic_budget_seconds": self.budget, "maximum_attempts_per_candidate": 3,
            "quality_acceptance_separate": True, "development_receipts": list(map(str, self.development_receipts)),
        }, freeze=freeze)
        self.common_receipts: list[str] = []
        self.paths: dict[str, dict[str, object]] = {
            method: {"snapshot": None, "setup_status": "NOT_RUN", "setup_receipts": []} for method in METHODS}
        self.automatic: list[dict[str, object]] = []
        self.common: str | None = None

    def call(self, tool: str, request: dict[str, object], label: str, *, reason: str,
             receipts: list[str], decision: dict[str, object] | None = None) -> dict[str, object]:
        self.verify_inputs()
        request.setdefault("timeout_seconds", self.timeout)
        result = self.experiment.call(tool, request, label, reason=reason, decision=decision)
        receipts.append(str(self.experiment.output / "receipts" / f"{label}.json"))
        self.verify_inputs()
        return result

    def verify_inputs(self) -> None:
        if any(not Path(path).is_file() or file_hash(Path(path)) != digest for path, digest in self.pinned.items()):
            raise ValueError("FROZEN_PREPARATION_INPUT_CHANGED")

    def save(self) -> dict[str, object]:
        record: dict[str, object] = {
            "paths": self.paths, "common_snapshot": self.common, "common_setup_receipts": self.common_receipts,
            "geometry_profile": self.profile, "padding_pixels": 8, "input_sha256": self.pinned,
            "automatic_candidates": self.automatic, "summary": self.experiment.summary(),
            "wall_seconds_total": time.monotonic() - self.started,
            "run_status": "FAILED" if self.failure else "COMPLETED" if self.completed else "RUNNING",
            "failure": self.failure,
            "development_receipts": list(map(str, self.development_receipts)),
            "development_cost_is_separate_from_formal_preparation": True,
            "targets_required_from_frozen_final_agent_state": True,
            "comparison_scope": "fixed Agent semantic candidate pool; not autonomous semantic detection",
        }
        write_json(self.experiment.output / "branches.json", record)
        write_step_report(self.experiment.output)
        return record

    def finish_path(self, method: str, snapshot: str, receipts: list[str], *, ready: bool,
                    reason: str = "") -> None:
        self.paths[method] = {
            "snapshot": snapshot if ready else None, "last_valid_snapshot": snapshot,
            "setup_status": "READY" if ready else "SETUP_REJECTED", "setup_reason": reason,
            "setup_receipts": [*self.common_receipts, *receipts], "branch_setup_receipts": receipts,
            "fork_parent": self.common, "metrics": snapshot_metrics(snapshot),
            "packing_groups": packing_owners(snapshot), "final_quality_passed": None,
        }
        self.save()

    def prepare_common(self) -> bool:
        current = str(self.source.root / "snapshot.json")
        regions = ([{"name": "appearance_protection", "faces": sorted(self.protected), "role": "protected"}]
                   if self.protected else [])
        regions += [{"name": str(pair["name"]), "faces": sorted(_ids(pair["first_faces"]) |
                    _ids(pair["second_faces"])), "role": "reuse_candidate"} for pair in self.pairs]
        for index, region in enumerate(regions):
            result = self.call("region.define", {"snapshot": current, **region,
                "rationale": "Frozen common semantic pool and appearance protection from the calling Agent.",
                "evidence": str(self.plan_path)}, f"common_region_{index:02d}",
                reason="共同预处理：保存原始面身份及同一语义候选池", receipts=self.common_receipts)
            if result.get("status") != "OK" or "snapshot" not in result:
                self.finish_path("no_share", current, [], ready=False, reason="COMMON_REGION_DEFINITION_FAILED")
                return False
            current = str(result["snapshot"])
        packed = self.call("uv.pack", {"snapshot": current, "profile": self.profile}, "common_pack",
                           reason="共同预处理：相同密度规则和mip安全间距", receipts=self.common_receipts)
        if packed.get("status") != "OK" or packed.get("geometry_passed") is not True:
            self.finish_path("no_share", current, [], ready=False, reason="COMMON_GEOMETRY_REJECTED")
            return False
        self.common = str(packed["snapshot"])
        self.finish_path("no_share", self.common, [], ready=True)
        return True

    def prepare_optcuts(self) -> None:
        assert self.common is not None
        current, receipts = self.common, []
        name = str(self.plan.get("optcuts_pair", "wing_membrane"))
        pair = next((pair for pair in self.pairs if pair["name"] == name), None)
        if pair is None:
            self.finish_path("optcuts_no_share", current, receipts, ready=False, reason="OPTCUTS_REGION_NOT_FOUND")
            return
        for member in ("first", "second"):
            faces = pair[member + "_faces"]
            if self.protected.intersection(_ids(faces)):
                self.finish_path("optcuts_no_share", current, receipts, ready=False, reason="OPTCUTS_REGION_PROTECTED")
                return
            result = self.call("uv.reparameterize", {"snapshot": current, "faces": faces,
                "backend": "optcuts", "profile": self.profile}, "optcuts_" + member,
                reason="区域OptCuts无共享对照：仅重建指定翼膜区域，拒绝不回退冒充成功", receipts=receipts)
            if result.get("status") != "OK" or result.get("geometry_passed") is not True:
                self.finish_path("optcuts_no_share", current, receipts, ready=False, reason="REGIONAL_OPTCUTS_REJECTED")
                return
            current = str(result["snapshot"])
        self.finish_path("optcuts_no_share", current, receipts, ready=True)

    def prepare_automatic(self) -> None:
        assert self.common is not None
        current, receipts = self.common, []
        areas = self.source.state.surface.mesh.areas
        pairs = sorted(self.pairs, key=lambda pair: (-float(areas[list(_ids(pair["first_faces"]) |
                       _ids(pair["second_faces"]))].sum()), str(pair["name"])))
        self.automatic = [{"name": pair["name"], "status": "NOT_RUN", "attempts": []} for pair in pairs]
        spent = 0.
        for pair, outcome in zip(pairs, self.automatic, strict=True):
            first, second = _ids(pair["first_faces"]), _ids(pair["second_faces"])
            excluded = (first | second) & self.protected
            original_area = float(areas[list(first | second)].sum())
            outcome["original_area"] = original_area
            attempts: list[dict[str, object]] = []
            outcome["attempts"] = attempts
            for revision in range(3):
                remaining = self.budget - spent
                if remaining < 1:
                    outcome["reason"] = "AUTOMATIC_TOOL_BUDGET_EXHAUSTED"
                    break
                label = f"geometry_{pair['name']}_{revision}"
                request = {"snapshot": current, "first_faces": sorted(first), "second_faces": sorted(second),
                    "exclusive_faces": sorted(excluded), "plane_normal": pair.get("plane_normal", [1, 0, 0]),
                    "plane_offset": pair.get("plane_offset", 0), "mode": "auto", "profile": self.profile,
                    "timeout_seconds": min(self.timeout, remaining)}
                result = self.call("reuse.check_pair", request, label, receipts=receipts,
                    reason="固定语义候选池按3D面积排序；最多三次证据驱动的确定性尝试",
                    decision={"selector": "deterministic_geometry", "excluded_faces": sorted(excluded),
                              "exception_area_fraction": float(areas[list(excluded)].sum()) / original_area})
                spent += float(str(self.experiment.steps[-1]["wall_seconds_including_runner"]))
                attempts.append({"label": label, "result": result, "excluded_faces": sorted(excluded)})
                outcome.update(status="REJECTED", reason=result.get("reason", "GEOMETRY_QUALITY_REJECTED"))
                if result.get("status") == "OK" and result.get("geometry_passed") is True:
                    proposal = read_object(Path(str(result["proposal"])))
                    before, after = packing_owners(current), packing_owners(str(proposal["candidate_snapshot"]))
                    attempts[-1].update(before_packing_groups=before, candidate_packing_groups=after)
                    if after >= before:
                        outcome["reason"] = "INDEPENDENT_PACKING_GROUPS_NOT_DECREASED"
                        break
                    if self.budget - spent < 1:
                        outcome["reason"] = "BUDGET_EXHAUSTED_BEFORE_APPLY"
                        break
                    applied = self.call("reuse.apply", {"snapshot": current, "proposal": result["proposal"],
                        "timeout_seconds": min(self.timeout, self.budget - spent)}, label + "_apply",
                        reason="几何通过且独立排布对象严格减少时才提交新快照", receipts=receipts)
                    spent += float(str(self.experiment.steps[-1]["wall_seconds_including_runner"]))
                    attempts[-1]["apply_result"] = applied
                    if applied.get("status") == "OK" and applied.get("geometry_passed") is True:
                        current = str(applied["snapshot"])
                        outcome.update(status="ACCEPTED", reason="GEOMETRY_AND_OWNER_REDUCTION",
                                       proposed_area=float(areas[list((first | second) - excluded)].sum()))
                    else:
                        outcome["reason"] = "APPLY_FAILED_PARENT_RETAINED"
                    break
                report = read_object(Path(str(result["report"]))) if "report" in result else {}
                extra = retry_faces(report, first, second) - excluded
                if not extra or not first - excluded - extra or not second - excluded - extra:
                    break
                excluded |= extra
            self.save()
        self.finish_path("geometry_auto", current, receipts, ready=True)
        self.paths["geometry_auto"].update(tool_seconds=spent, tool_budget_seconds=self.budget,
            selection_scope="geometry decisions conditioned on the same frozen Agent semantic regions")

    def prepare_agent(self) -> None:
        assert self.common is not None
        current, receipts = self.common, []
        bindings: dict[str, dict[str, object]] = {"initial_snapshot": {"snapshot": self.common}}
        allowed = {"reuse.check_pair", "reuse.apply", "uv.pack"}
        for index, operation in enumerate(_objects(self.replay["selected_recipe"])):
            tool, original_label = str(operation["tool"]), str(operation["label"])
            if tool not in allowed or original_label in bindings:
                raise ValueError("UNSUPPORTED_OR_DUPLICATE_AGENT_REPLAY_OPERATION")
            request = bind_request(_object(operation["request"]), bindings)
            request["timeout_seconds"] = self.timeout
            if request.get("snapshot") != current:
                raise ValueError("AGENT_REPLAY_MUST_DERIVE_CURRENT_BRANCH")
            if tool in ("reuse.check_pair", "uv.pack"):
                request["profile"] = self.profile
            if tool == "reuse.check_pair":
                first, second = _ids(request["first_faces"]), _ids(request["second_faces"])
                if not any(first == _ids(pair["first_faces"]) and second == _ids(pair["second_faces"])
                           for pair in self.pairs):
                    raise ValueError("AGENT_REPLAY_LEAVES_COMMON_CANDIDATE_POOL")
                selected = first | second
                excluded = _ids(request.get("exclusive_faces", []))
                if self.protected & (selected - excluded):
                    raise ValueError("AGENT_REPLAY_SHARES_PROTECTED_FACES")
            result = self.call(tool, request, f"agent_{index:02d}_{original_label}", receipts=receipts,
                reason=str(operation.get("reason", "重放Agent已记录决策，重绑定父状态并统一本轮排布协议")),
                decision={"selector": "recorded_calling_agent", "original_label": original_label,
                          "profile_rebound_to_common": True})
            bindings[original_label] = result
            if result.get("status") != "OK" or result.get("geometry_passed") is not True:
                self.finish_path("agent", current, receipts, ready=False, reason="AGENT_REPLAY_REJECTED")
                return
            if "snapshot" in result:
                current = str(result["snapshot"])
        self.finish_path("agent", current, receipts, ready=True)

    def run(self) -> dict[str, object]:
        self.save()
        try:
            if self.prepare_common():
                self.prepare_optcuts()
                self.prepare_automatic()
                self.prepare_agent()
            self.completed = True
            return self.save()
        except Exception as error:
            self.failure = {"type": type(error).__name__, "reason": str(error)}
            raise
        finally:
            self.save()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = Preparation(args.plan, args.output).run()
    print({name: _object(entry)["setup_status"] for name, entry in _object(result["paths"]).items()})


if __name__ == "__main__":
    main()
