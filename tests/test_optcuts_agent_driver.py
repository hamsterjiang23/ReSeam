"""Failure boundaries and multi-candidate decisions in the thin Agent driver."""

from pathlib import Path

import numpy as np
import pytest

from reseam.agent_tools.state import Snapshot, read_object, save_snapshot, write_json
from reseam.budget_state import BudgetState
from reseam.mesh import make_mesh, state_from_corner_uv
from scripts import run_optcuts_agent_uv as driver


def fixture_snapshot(path: Path, split: bool) -> Snapshot:
    vertices = np.asarray([[0., 0, 0], [1., 0, 0], [1., 1, 0], [0., 1, 0]])
    faces = np.asarray([[0, 1, 2], [0, 2, 3]])
    uv = vertices[faces, :2].copy()
    if split:
        uv[1] += [2., 0.]
    surface = state_from_corner_uv(make_mesh(vertices, faces), uv,
        corner_ids=np.arange(6).reshape(2, 3) if split else faces)
    return save_snapshot(BudgetState(surface), path, {})


def configured_trial(tmp_path: Path) -> tuple[driver.AgentTrial, str]:
    source = fixture_snapshot(tmp_path / "source", True)
    merged = fixture_snapshot(tmp_path / "merged", False)
    trial = driver.AgentTrial(tmp_path / "run", "tripo")
    mesh = source.state.surface.mesh
    diagonal = next(e for e, pair in enumerate(mesh.edges) if list(pair) == [0, 2])
    trial.state.update(optcuts={"snapshot": str(source.root / "snapshot.json")},
        baseline_metrics=driver.metrics(str(source.root / "snapshot.json")),
        proposals=[{"faces": [0, 1], "remove_edge_ids": [diagonal]}])
    return trial, str(merged.root / "snapshot.json")


def test_failed_optcuts_preserves_unavailable_target_instead_of_crashing(tmp_path: Path) -> None:
    trial = driver.AgentTrial(tmp_path, "tripo")
    trial.state.update(optcuts={"status": "TIMEOUT"}, target="stale-target")
    trial.targets()
    assert trial.state["target"] is None
    assert trial.state["target_unavailable"]["status"] == "TIMEOUT"  # type: ignore[index]
    assert read_object(trial.state_path)["target"] is None


def test_second_candidate_failure_still_tries_fallback_with_previous_success(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    trial, merged = configured_trial(tmp_path)
    trial.state["merged"] = "earlier-success"
    write_json(trial.folder / "initial/proposal0_views/views.json", {"reviewed": True})
    calls: list[str] = []

    def call(_self: driver.AgentTrial, label: str, _tool: str, _request: dict[str, object]) -> dict[str, object]:
        calls.append(label)
        return {"status": "REJECTED"} if label.endswith("/blender_lscm") else {"snapshot": merged} if label.endswith("/blender_minimum_stretch") else {}

    monkeypatch.setattr(driver.AgentTrial, "call", call)
    trial.merge(0)
    assert "merge0/blender_minimum_stretch" in calls
    assert trial.state["merged"] == merged


def test_missing_proposal_views_stops_before_editing_until_agent_can_review(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    trial, _ = configured_trial(tmp_path)
    calls: list[str] = []

    def call(_self: driver.AgentTrial, label: str, tool: str, _request: dict[str, object]) -> dict[str, object]:
        calls.append(tool)
        write_json(trial.folder / label / "views.json", {"images": {}})
        return {"report": str(trial.folder / label / "views.json"), "status": "OK"}

    monkeypatch.setattr(driver.AgentTrial, "call", call)
    trial.merge(0)
    assert calls == ["view.render"]
    assert trial.state["merge_review_required"]["proposal_index"] == 0  # type: ignore[index]
    assert "merged" not in trial.state
