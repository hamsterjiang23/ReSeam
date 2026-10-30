"""Failure/budget bookkeeping never upgrades missing or rejected artifacts."""

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from typing import cast

import pytest
from test_agent_tools_seam_selection import grid

from reseam.agent_tools import experiment as experiment_module
from reseam.agent_tools.state import read_object, write_json

spec = spec_from_file_location("meshquery_driver", Path(__file__).resolve().parents[1] / "scripts/run_meshquery_experiments.py")
assert spec is not None and spec.loader is not None
driver = module_from_spec(spec)
spec.loader.exec_module(driver)
resolve, run_config, validate_config = driver.resolve, driver.run_config, driver.validate_config


def test_unresolved_check_result_records_not_run_and_keeps_parent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    snapshot = grid(tmp_path)
    calls: list[str] = []

    def fake_run(tool: str, request: Path, output: Path) -> dict[str, object]:
        calls.append(tool)
        return {"status": "REJECTED", "reason": "CONSTRUCTED_CORRESPONDENCE_FAILURE"}

    monkeypatch.setattr(experiment_module, "run_tool", fake_run)
    monkeypatch.setattr(experiment_module, "code_manifest", lambda root: {})
    config = {"cases": [{"name": "failure", "parent_snapshot": str(snapshot.root), "actions": [
        {"label": "check", "tool": "reuse.check_pair", "request": {}, "reason": "test failure",
         "on_failure": "continue"},
        {"label": "apply", "tool": "reuse.apply", "request": {"proposal": {"$ref": "check.proposal"}},
         "reason": "never apply missing proposal", "on_failure": "continue"}]}]}
    write_json(tmp_path / "config.json", config)
    run_config(tmp_path / "config.json", tmp_path / "run")
    import json
    matrix = json.loads((tmp_path / "run/matrix.json").read_text())
    assert calls == ["reuse.check_pair"]
    assert matrix[0]["snapshot"] == str(snapshot.root)
    assert matrix[0]["status"] == "EXECUTED_WITH_REJECTIONS"
    assert matrix[0]["results"][1]["result"]["status"] == "NOT_RUN"
    assert matrix[0]["quality_accepted"] is None
    assert matrix[0]["quality_status"] == "NOT_EVALUATED"


def test_exhausted_resumed_budget_runs_no_tool(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    snapshot = grid(tmp_path)

    def fake_run(tool: str, request: Path, output: Path) -> dict[str, object]:
        raise AssertionError("budget exhausted; must not run")

    monkeypatch.setattr(experiment_module, "run_tool", fake_run)
    monkeypatch.setattr(experiment_module, "code_manifest", lambda root: {})
    config = {"cases": [{"name": "exhausted", "parent_snapshot": str(snapshot.root),
        "budget_seconds": 10, "prior_tool_seconds": 10, "actions": [
        {"label": "inspect", "tool": "uv.inspect", "request": {}, "reason": "inspect"}]}]}
    write_json(tmp_path / "config.json", config)
    run_config(tmp_path / "config.json", tmp_path / "run")
    import json
    matrix = json.loads((tmp_path / "run/matrix.json").read_text())
    assert matrix[0]["status"] == "BUDGET_EXHAUSTED"
    context = read_object(Path(matrix[0]["context"]))
    assert cast(dict[str, object], context["current"])["state_hash"] == snapshot.state_hash


@pytest.mark.parametrize("extra,reason", [
    ({"prior_tool_seconds": -1}, "PRIOR_TOOL_SECONDS"),
    ({"bindings": {"current": {}}}, "BINDINGS_CANNOT_OVERRIDE"),
])
def test_invalid_budget_or_binding_rejects(tmp_path: Path, extra: dict[str, object], reason: str) -> None:
    snapshot = grid(tmp_path)
    with pytest.raises(ValueError, match=reason):
        validate_config({"cases": [{"name": "invalid", "parent_snapshot": str(snapshot.root), **extra}]})


def test_only_explicit_reference_objects_are_substituted() -> None:
    context: dict[str, object] = {"check": {"proposal": "a.json"}}
    assert resolve("check.proposal", context) == "check.proposal"
    assert resolve({"nested": [{"$ref": "check.proposal"}]}, context) == {"nested": ["a.json"]}


@pytest.mark.parametrize("accepted,status", [(True, "ACCEPTED"), (False, "REJECTED"), (None, "NOT_EVALUATED")])
def test_quality_outcome_distinguishes_rejection_from_unexecuted_validation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, accepted: bool | None, status: str,
) -> None:
    snapshot = grid(tmp_path)

    def fake_run(tool: str, request: Path, output: Path) -> dict[str, object]:
        if accepted is None:
            return {"status": "ERROR", "reason": "BACKEND_UNAVAILABLE"}
        return {"status": "OK" if accepted else "REJECTED", "quality_accepted": accepted}

    monkeypatch.setattr(experiment_module, "run_tool", fake_run)
    monkeypatch.setattr(experiment_module, "code_manifest", lambda root: {})
    write_json(tmp_path / "config.json", {"cases": [{"name": "validation", "parent_snapshot": str(snapshot.root),
        "actions": [{"label": "validate", "tool": "quality.validate", "request": {},
                     "reason": "test outcome semantics", "on_failure": "continue"}]}]})
    run_config(tmp_path / "config.json", tmp_path / "run")
    import json
    matrix = json.loads((tmp_path / "run/matrix.json").read_text())
    assert matrix[0]["quality_accepted"] is accepted
    assert matrix[0]["quality_status"] == status
    assert status in (tmp_path / "run/report.md").read_text(encoding="utf-8")
