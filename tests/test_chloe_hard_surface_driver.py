"""Experiment bookkeeping retains timeout/budget failures without inventing candidates."""

from pathlib import Path

import pytest

from reseam.agent_tools.state import read_object
from scripts import run_chloe_hard_surface as driver


def test_budget_includes_direct_work_and_preserves_failure(tmp_path: Path) -> None:
    trial = driver.Trial(tmp_path, "original")
    trial.previous_direct = 2401.
    result = trial.call("test/exhausted", "mesh.index", {"snapshot": "unused"})
    assert result["status"] == "BUDGET_EXHAUSTED"
    assert read_object(trial.folder / "test/exhausted/result.json")["status"] == "BUDGET_EXHAUSTED"
    trial.candidates()
    assert trial.state["candidates"] == {}


def test_candidate_discovery_timeout_does_not_invent_shared_result(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def failed_tool(_name: str, _request: Path, output: Path) -> dict[str, object]:
        return {"status": "TIMEOUT", "output": str(output)}

    monkeypatch.setattr(driver, "run_tool", failed_tool)
    trial = driver.Trial(tmp_path, "original")
    assert trial.share("unread_source", "test") is None
    ledger = read_object(trial.folder / "calls.json")
    assert len(ledger["calls"]) == 1  # type: ignore[arg-type]


def test_geometry_lock_prevents_concurrent_ledger_writers_and_releases(tmp_path: Path) -> None:
    with driver.exclusive_geometry_tool(tmp_path / "original"):
        with pytest.raises(RuntimeError, match="ALREADY_RUNNING"), driver.exclusive_geometry_tool(tmp_path / "original"):
            pytest.fail("same geometry lock admitted two writers")
        with driver.exclusive_geometry_tool(tmp_path / "mirror"):
            pass  # Different geometries have independent ledgers and locks.
    with driver.exclusive_geometry_tool(tmp_path / "original"):
        pass
