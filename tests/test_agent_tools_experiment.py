"""Evidence logging must not silently compare different code or overwrite a step."""

from pathlib import Path

import pytest

from reseam.agent_tools import experiment as evidence
from reseam.agent_tools.experiment import Experiment, changed_fields


def test_frozen_round_rejects_source_edit(tmp_path: Path) -> None:
    source = tmp_path / "src/reseam"
    source.mkdir(parents=True)
    module = source / "kernel.py"
    module.write_text("value = 1\n", encoding="utf-8")
    experiment = Experiment(tmp_path, tmp_path / "round", {"quality": "fixed"})
    module.write_text("value = 2\n", encoding="utf-8")
    with pytest.raises(ValueError, match="FROZEN_CODE_CHANGED"):
        experiment.call("uv.inspect", {}, "inspect", reason="same code required")
    assert not (tmp_path / "round/requests").exists()


def test_round_never_overwrites_and_requires_existing_control(tmp_path: Path) -> None:
    experiment = Experiment(tmp_path, tmp_path / "round", {})
    with pytest.raises(FileExistsError):
        Experiment(tmp_path, tmp_path / "round", {})
    with pytest.raises(ValueError, match="COMPARISON_STEP_NOT_FOUND"):
        experiment.call("uv.inspect", {}, "inspect", reason="paired ablation", comparison="missing")
    assert experiment.summary()["steps"] == 0


def test_paired_change_cannot_hide_threshold_in_profile(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def no_operation(name: str, request: Path, output: Path) -> dict[str, object]:
        return {"status": "OK"}

    monkeypatch.setattr(evidence, "run_tool", no_operation)
    experiment = Experiment(tmp_path, tmp_path / "round", {})
    original = {"profile": {"balance_density": False, "normal_angle_degrees": 45}}
    experiment.call("uv.inspect", original, "before", reason="locked baseline")
    changed = {"profile": {"balance_density": True, "normal_angle_degrees": 90}}
    with pytest.raises(ValueError, match="UNDECLARED_VARIABLE"):
        experiment.call("uv.inspect", changed, "after", reason="density only", comparison="before",
                        research_fields=("profile.balance_density",))
    assert not (experiment.output / "requests/after.json").exists()
    assert changed_fields(original, changed) == {"profile.balance_density", "profile.normal_angle_degrees"}
