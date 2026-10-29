"""Tiny real UV branch orchestration; no real dragon or native OptCuts launch."""

import importlib.util
from pathlib import Path

import numpy as np
import pytest

from reseam.agent_tools.experiment import Experiment
from reseam.agent_tools.state import load_snapshot, read_object, save_snapshot, write_json
from reseam.budget_state import BudgetState
from reseam.mesh import make_mesh, state_from_corner_uv

_spec = importlib.util.spec_from_file_location("prepare_dragon_reuse", Path(__file__).parents[1] /
                                              "scripts" / "prepare_dragon_reuse.py")
assert _spec is not None and _spec.loader is not None
_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_module)
Preparation, bind_request, retry_faces = _module.Preparation, _module.bind_request, _module.retry_faces


def _plan(tmp_path: Path) -> Path:
    vertices = np.asarray([[-1., 0, 0], [-1, 1, 0], [-1, 1, 1], [-1, 0, 1],
                           [1., 0, 0], [1, 1, 0], [1, 1, 1], [1, 0, 1],
                           [2., 2, 0], [2, 3, 0], [2, 2, 1]])
    faces = np.asarray([[0, 1, 2], [0, 2, 3], [4, 6, 5], [4, 7, 6], [8, 9, 10]], dtype=np.int64)
    corners = .1 + .25 * vertices[faces, 1:]
    corners[2:4, :, 0] += .5
    corners[2:4, :, 1] = .45 - corners[2:4, :, 1]
    corners[4] = [[.1, .6], [.35, .6], [.1, .85]]
    state = BudgetState(state_from_corner_uv(make_mesh(vertices, faces), corners, seam_ids=frozenset()))
    source = save_snapshot(state, tmp_path / "source", {})
    pair = {"name": "wing_membrane", "first_faces": [0, 1], "second_faces": [2, 3],
            "plane_normal": [1, 0, 0], "plane_offset": 0}
    write_json(tmp_path / "decisions.json", {"asset_id": source.asset_id, "protected_faces": [4], "pairs": [pair]})
    write_json(tmp_path / "replay.json", {"selected_recipe": [
        {"tool": "reuse.check_pair", "label": "wing_check", "request": {
            "snapshot": "$initial_snapshot", **pair, "mode": "auto", "exclusive_faces": []}},
        {"tool": "reuse.apply", "label": "wing_apply", "request": {
            "snapshot": "$initial_snapshot", "proposal": "$wing_check.proposal"}},
    ]})
    path = tmp_path / "prepare.json"
    write_json(path, {"snapshot": "source", "decisions": "decisions.json", "agent_replay": "replay.json",
        "geometry_profile": {"resolution": 64, "gap_pixels": 2, "border_pixels": 2},
        "tool_timeout_seconds": 30})
    return path


def _without_native(monkeypatch: pytest.MonkeyPatch) -> None:
    original = Experiment.call

    def call(self: Experiment, tool: str, request: dict[str, object], label: str, *, reason: str,
             comparison: str | None = None, decision: dict[str, object] | None = None,
             research_fields: tuple[str, ...] = ()) -> dict[str, object]:
        # Exercise the public tool's real unsupported-backend rejection instead
        # of depending on a workstation OptCuts installation in this unit test.
        if tool == "uv.reparameterize":
            request = {**request, "backend": "unavailable_test_backend"}
        return original(self, tool, request, label, reason=reason, comparison=comparison,
                        decision=decision, research_fields=research_fields)

    monkeypatch.setattr(Experiment, "call", call)


def test_real_tiny_branches_share_parent_and_do_not_fake_optcuts_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _without_native(monkeypatch)
    preparation = Preparation(_plan(tmp_path), tmp_path / "prepared", freeze=False)
    result = preparation.run()
    paths = result["paths"]
    assert isinstance(paths, dict)
    common = result["common_snapshot"]
    for name in ("no_share", "geometry_auto", "agent"):
        assert paths[name]["setup_status"] == "READY", result
        assert paths[name]["fork_parent"] == common
        assert paths[name]["setup_receipts"]
    assert paths["no_share"]["packing_groups"] == 3
    assert paths["geometry_auto"]["packing_groups"] == paths["agent"]["packing_groups"] == 2
    assert paths["optcuts_no_share"]["setup_status"] == "SETUP_REJECTED"
    assert paths["optcuts_no_share"]["snapshot"] is None
    assert paths["optcuts_no_share"]["last_valid_snapshot"] == common
    for name in ("geometry_auto", "agent"):
        snapshot = load_snapshot(Path(paths[name]["snapshot"]))
        assert snapshot.state.groups and all(4 not in group.first_faces + group.second_faces
                                             for group in snapshot.state.groups)
        regions = snapshot.metadata["regions"]
        assert isinstance(regions, list)
        assert {region["name"] for region in regions} == {
            "appearance_protection", "wing_membrane"}
    assert not load_snapshot(tmp_path / "source").state.groups
    assert (tmp_path / "prepared" / "steps.md").is_file()
    assert result["comparison_scope"] == "fixed Agent semantic candidate pool; not autonomous semantic detection"


def test_exhausted_automatic_budget_keeps_not_run_candidates(tmp_path: Path) -> None:
    path = _plan(tmp_path)
    config = read_object(path)
    config["automatic_budget_seconds"] = .01
    write_json(path, config)
    preparation = Preparation(path, tmp_path / "budget", freeze=False)
    preparation.common = str(tmp_path / "source")
    preparation.prepare_automatic()
    assert preparation.automatic[0]["status"] == "NOT_RUN"
    assert preparation.automatic[0]["reason"] == "AUTOMATIC_TOOL_BUDGET_EXHAUSTED"
    assert preparation.experiment.steps == []
    assert preparation.paths["geometry_auto"]["snapshot"] == str(tmp_path / "source")


def test_input_edits_are_rejected_before_any_tool(tmp_path: Path) -> None:
    preparation = Preparation(_plan(tmp_path), tmp_path / "pinned", freeze=False)
    path = tmp_path / "decisions.json"
    changed = read_object(path)
    changed["protected_faces"] = []
    write_json(path, changed)
    with pytest.raises(ValueError, match="FROZEN_PREPARATION_INPUT_CHANGED"):
        preparation.run()
    assert preparation.experiment.steps == []


def test_recipe_rebinds_and_rejects_old_proposal_paths() -> None:
    bound = bind_request({"snapshot": "$initial_snapshot", "proposal": "$check.proposal", "state_hash": "old"},
                         {"initial_snapshot": {"snapshot": "new-parent"}, "check": {"proposal": "new-proposal"}})
    assert bound == {"snapshot": "new-parent", "proposal": "new-proposal"}
    with pytest.raises(ValueError, match="REPLAY_REQUIRES_BOUND_PROPOSAL"):
        bind_request({"proposal": "old-proposal.json"}, {})
    with pytest.raises(ValueError, match="REPLAY_TOKEN_UNAVAILABLE"):
        bind_request({"snapshot": "$missing.snapshot"}, {})


def test_agent_cannot_leave_controlled_semantic_pool(tmp_path: Path) -> None:
    path = _plan(tmp_path)
    replay = read_object(tmp_path / "replay.json")
    operations = replay["selected_recipe"]
    assert isinstance(operations, list)
    operations[0]["request"]["first_faces"] = [0]
    write_json(tmp_path / "replay.json", replay)
    preparation = Preparation(path, tmp_path / "outside_pool", freeze=False)
    preparation.common = str(tmp_path / "source")
    with pytest.raises(ValueError, match="AGENT_REPLAY_LEAVES_COMMON_CANDIDATE_POOL"):
        preparation.prepare_agent()
    assert preparation.experiment.steps == []


def test_retry_policy_uses_smallest_deterministic_in_region_evidence() -> None:
    report = {"diagnostics": {"attempts": [
        {"suggested_exclusive_faces": [4, 3, 999]},
        {"geometry": {"errors": ["OVERLAP_CORRESPONDENCE:0:4"]}},
        {"suggested_exclusive_faces": [3]},
    ]}}
    assert retry_faces(report, {0, 1}, {3, 4}) == {3}
    assert retry_faces({"failed_samples": [{"source_face": 1}, {"source_face": 999}]}, {0, 1}, {3, 4}) == {1}
