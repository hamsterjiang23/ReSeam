"""Integration boundaries of the new ROI development entry point."""

import json
import time
from pathlib import Path

import numpy as np
import pytest

from reseam.budget_experiment import (
    BudgetExperimentConfig,
    load_budget_config,
    pack_pair,
    run_budget_experiment,
    select_wing_pair,
)
from reseam.joint_regions import RegionPair
from reseam.mesh import make_mesh
from reseam.types import Mesh, MeshState


def panels() -> MeshState:
    mesh = make_mesh(
        np.asarray([[0, 0, 0], [1, 0, 0], [0, 1, 0], [2, 0, 0], [3, 0, 0], [2, 1, 0]], dtype=float),
        np.asarray([[0, 1, 2], [3, 4, 5]], dtype=np.int64),
    )
    return MeshState(
        mesh,
        frozenset(),
        np.asarray([[0, 0], [1, 0], [0, 1], [0.02, 0], [1.02, 0], [0.02, 1]], dtype=float),
        np.asarray([[0, 1, 2], [3, 4, 5]], dtype=np.int64),
    )


def test_common_scale_packing_and_input_unchanged() -> None:
    state = panels()
    original = state.uv.copy()
    members = np.array([0, 1], dtype=np.int64)
    for shared in (False, True):
        result = pack_pair(state, members, shared, 64, 2, 2)
        assert np.all(result.uv >= 2 / 64 - 1e-12)
        assert np.all(result.uv <= 1 - 2 / 64 + 1e-12)
        length_a = np.linalg.norm(result.uv[1] - result.uv[0])
        length_b = np.linalg.norm(result.uv[4] - result.uv[3])
        assert length_a == pytest.approx(length_b)
        np.testing.assert_array_equal(state.uv, original)
        np.testing.assert_array_equal(result.mesh.faces, state.mesh.faces)
    independent = pack_pair(state, members, False, 64, 2, 2)
    assert independent.uv[3:, 0].min() - independent.uv[:3, 0].max() == pytest.approx(2 / 64)


@pytest.mark.parametrize("resolutions", [(8,), (32, 32), (True,), ()])
def test_invalid_budget_dimensions(resolutions: tuple[int, ...]) -> None:
    with pytest.raises(ValueError):
        BudgetExperimentConfig("invalid", resolutions=resolutions)


def test_round_identifier_and_missing_mips() -> None:
    with pytest.raises(ValueError):
        BudgetExperimentConfig("../overwrite")
    with pytest.raises(ValueError):
        BudgetExperimentConfig("invalid", resolutions=(32,), mip_levels=(6,))


def test_json_config_and_refuse_overwrite(tmp_path: Path) -> None:
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"round_id": "test", "resolutions": [32]}), encoding="utf-8")
    assert load_budget_config(config).resolutions == (32,)
    output = tmp_path / "exists"
    output.mkdir()
    marker = output / "do_not_touch.txt"
    marker.write_text("keep", encoding="utf-8")
    with pytest.raises(FileExistsError):
        run_budget_experiment(Path.cwd(), config, output)
    assert marker.read_text(encoding="utf-8") == "keep"


def test_failed_development_keeps_evidence_and_cannot_claim_validation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"round_id": "forced_failure"}), encoding="utf-8")

    def fail_selection(
        mesh: Mesh,
        settings: BudgetExperimentConfig,
        deadline: float,
        diagnostics_path: Path | None = None,
    ) -> tuple[object, dict[str, object]]:
        raise ValueError("FORCED_ROI_FAILURE")

    monkeypatch.setattr("reseam.budget_experiment.select_wing_pair", fail_selection)
    output = tmp_path / "failure"
    result = run_budget_experiment(Path(__file__).resolve().parents[1], config, output)
    assert result["status"] == "DEVELOPMENT_FAILED"
    assert result["full_v1_validated"] is False
    assert result["input_unchanged"] is True
    assert result["source_unchanged"] is True
    assert (output / "protocol.json").is_file()
    account = json.loads((output / "budget_account.json").read_text(encoding="utf-8"))
    assert account["historical_accounts_modified"] is False


def test_missing_config_during_run_still_finalizes_budget(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"round_id": "deleted_config"}), encoding="utf-8")

    def delete_config(
        mesh: Mesh,
        settings: BudgetExperimentConfig,
        deadline: float,
        diagnostics_path: Path | None = None,
    ) -> tuple[object, dict[str, object]]:
        config.unlink()
        raise ValueError("INJECTED_CONFIG_REMOVAL")

    monkeypatch.setattr("reseam.budget_experiment.select_wing_pair", delete_config)
    output = tmp_path / "deleted"
    result = run_budget_experiment(Path(__file__).resolve().parents[1], config, output)
    assert result["status"] == "SOURCE_OR_INPUT_CHANGED"
    assert result["source_unchanged"] is False
    assert result["input_unchanged"] is True
    assert result["integrity_read_errors"]
    assert (output / "budget_account.json").is_file()
    saved = json.loads((output / "result.json").read_text(encoding="utf-8"))
    assert saved["status"] != "RUNNING"


def test_initial_hash_read_failure_still_finalizes_budget(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from reseam import budget_experiment

    config = tmp_path / "config.json"
    config.write_text(json.dumps({"round_id": "initial_hash_failure"}), encoding="utf-8")
    original_sha = budget_experiment.sha256

    def fail_high(path: Path) -> str:
        if path.name == "high.glb":
            raise FileNotFoundError("INJECTED_INPUT_READ_FAILURE")
        return original_sha(path)

    monkeypatch.setattr(budget_experiment, "sha256", fail_high)
    output = tmp_path / "initial_failure"
    result = run_budget_experiment(Path(__file__).resolve().parents[1], config, output)
    assert result["status"] != "RUNNING"
    assert result["input_unchanged"] is False
    assert (output / "budget_account.json").is_file()


def test_refined_initialization_failure_retains_actual_face_ids(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pair = RegionPair((0,), (1,), (1.0, 0.0, 0.0), 1.5)
    monkeypatch.setattr("reseam.budget_experiment.region_pairs", lambda *args: ([pair], []))
    monkeypatch.setattr("reseam.budget_experiment.planar_regions", lambda *args: [pair])
    monkeypatch.setattr("reseam.budget_experiment.refine_pair", lambda *args: (pair, [{"iteration": 0}]))

    def reject_initialization(
        mesh: Mesh,
        region: RegionPair,
        deadline: float,
        planar: bool,
    ) -> None:
        raise ValueError("INJECTED_INITIALIZATION_FAILURE")

    monkeypatch.setattr("reseam.budget_experiment.paired_problem", reject_initialization)
    diagnostic = tmp_path / "roi.json"
    with pytest.raises(ValueError, match="NO_GEOMETRIC_ROI_INITIALIZATION"):
        select_wing_pair(
            panels().mesh, BudgetExperimentConfig("failure_ids"), time.monotonic() + 10, diagnostic
        )
    raw = json.loads(diagnostic.read_text(encoding="utf-8"))
    attempts = raw["initialization_attempts"]
    assert len(attempts) == 2
    refined = attempts[1]
    assert refined["mode"] == "refined"
    assert refined["status"] == "INIT_FAILED"
    assert refined["original_first_faces"] == refined["first_faces"] == [0]
    assert refined["original_second_faces"] == refined["second_faces"] == [1]
