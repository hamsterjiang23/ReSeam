"""A report cannot turn a live or incomplete experiment into a full comparison."""

from pathlib import Path

import pytest

from reseam.agent_tools.state import write_json
from scripts.build_optcuts_agent_report import check_completion


def matrix(root: Path) -> None:
    for asset in ("tripo", "dragon"):
        write_json(root / asset / "evaluations.json", {"rows": [
            {"method": method, "seed": seed} for method in ("optcuts", "agent_merge") for seed in (17, 29, 43)]})


def test_partial_and_live_runs_are_not_reported_as_complete(tmp_path: Path) -> None:
    matrix(tmp_path)
    check_completion(tmp_path)
    pending = tmp_path / "dragon/evaluate/extra/command.json"
    write_json(pending, {})
    write_json(pending.parent / "provenance.json", {})
    write_json(pending.parent / "request.json", {})
    with pytest.raises(ValueError, match="EXPERIMENT_NOT_COMPLETE:dragon"):
        check_completion(tmp_path)
    write_json(pending.parent / "result.json", {"status": "TIMEOUT"})
    check_completion(tmp_path)
    # Native backend commands are children of a finished public call and have
    # backend-specific outputs, not the public tool result.json contract.
    write_json(pending.parent / "native/command.json", {})
    check_completion(tmp_path)
    write_json(tmp_path / "dragon/evaluations.json", {"rows": [{"method": "optcuts", "seed": 17}]})
    with pytest.raises(ValueError, match="EXPERIMENT_NOT_COMPLETE:dragon"):
        check_completion(tmp_path)
