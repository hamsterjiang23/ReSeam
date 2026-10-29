"""Comparison bookkeeping using frozen tiny files and deterministic solver mocks."""

import json
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest

from reseam.budget_compare import (
    ComparisonConfig,
    SolverBindings,
    TargetSolver,
    load_comparison_config,
    run_budget_comparison,
)
from reseam.mesh import sha256


@pytest.fixture
def comparison_project(tmp_path: Path) -> tuple[Path, Path]:
    root = tmp_path / "project"
    (root / "src/reseam").mkdir(parents=True)
    (root / "src/reseam/frozen.py").write_text("# frozen source\n", encoding="utf-8")
    (root / "derivation").mkdir()
    (root / "derivation/v1.md").write_text("# Frozen derivation\n", encoding="utf-8")
    models: list[dict[str, object]] = []
    for model in ("energy_blade", "dragon"):
        directory = root / "assets/v1" / model
        directory.mkdir(parents=True)
        files: list[dict[str, str]] = []
        for filename in ("high.glb", "low.obj", "low.glb"):
            path = directory / filename
            path.write_bytes(f"{model} {filename}".encode())
            files.append({"path": f"{model}/{filename}", "sha256": sha256(path)})
        models.append({"id": model, "files": files})
    (root / "assets/v1/manifest.json").write_text(json.dumps({"models": models}), encoding="utf-8")
    config = root / "config.json"
    config.write_text(json.dumps({
        "round_id": "mock_round01",
        "models": ["energy_blade", "dragon"],
        "methods": ["independent", "joint"],
        "seeds": [17],
        "resolutions": [64, 128],
        "seconds_per_target": 35,
        "setup_seconds_per_model": 120,
        "options": {},
        "formal": False,
    }), encoding="utf-8")
    return root, config


def install_solver(
    monkeypatch: pytest.MonkeyPatch,
    solve: TargetSolver,
    prepare: Callable[[Path, str, object, float], object] | None = None,
) -> None:
    def default_prepare(root: Path, model: str, options: object, deadline: float) -> object:
        del root, options, deadline
        return SimpleNamespace(
            model=model,
            initialization={"fixed_initialization": True},
            region_diagnostics={"region_status": "FIXED_GEOMETRY"},
            reference=SimpleNamespace(metadata={"channels": ["BaseColor", "Normal", "ORM"]}),
        )

    def make_options(raw: dict[str, object]) -> object:
        return dict(raw)

    def bindings() -> SolverBindings:
        return SolverBindings(make_options, default_prepare if prepare is None else prepare, solve)

    monkeypatch.setattr("reseam.budget_compare._solver_bindings", bindings)


def solver_result(valid: bool, payload: int) -> dict[str, object]:
    return {
        "status": "VALIDATED" if valid else "QUALITY_REJECTED",
        "valid": valid,
        "payload_bytes": payload,
        "geometry": {"valid": True},
        "material": {
            "valid": valid,
            "raster_checked": True,
            "raster_valid": valid,
            "channels": {
                "color": {"area_weighted_mean": 0.012345},
                "normal": {"max": 2.75},
                "orm": {"max": 0.03},
            },
            "faces": [{"face": 0, "valid": valid}],
        },
        "render": {"valid": valid},
        "final_groups": 1,
        "mesh_attribute_bytes": 1560,
        "mesh_attribute_policy": "mock_explicit_corners",
        "wall_seconds": 0.123,
    }


def test_complete_matrix_and_minimum_actual_validated_payload(
    comparison_project: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, config = comparison_project
    output = root / "run"
    calls: list[tuple[str, str, int, int, float]] = []

    def solve(
        inputs: object, method: str, seed: int, resolution: int, folder: Path,
        *, seconds: float, options: object,
    ) -> dict[str, object]:
        del options
        assert not folder.exists()
        model = cast(SimpleNamespace, inputs).model
        calls.append((model, method, seed, resolution, seconds))
        matrix = json.loads((output / "matrix.json").read_text(encoding="utf-8"))
        assert len(matrix) == 8
        folder.mkdir(parents=True)
        (folder / "textures").mkdir()
        (folder / "textures/BaseColor_mip0.png").write_bytes(b"png")
        (folder / "preview.glb").write_bytes(b"glb_preview")
        return solver_result(method == "joint" or resolution == 128, 200 if resolution == 64 else 800)

    install_solver(monkeypatch, solve)
    result = run_budget_comparison(root, config, output)
    assert result["status"] == "COMPARISON_COMPLETE"
    assert result["source_unchanged"] is True and result["input_unchanged"] is True
    assert result["planned_rows"] == 8 and result["validated_rows"] == 6
    assert len(calls) == 8
    assert all(call[-1] == 35 for call in calls)
    minima = result["minimum_results"]
    assert isinstance(minima, list)
    independent = [item for item in minima if item["method"] == "independent"]
    shared = [item for item in minima if item["method"] == "joint"]
    assert all(item["minimum_payload_bytes"] == 800 for item in independent)
    assert all(item["minimum_payload_bytes"] == 200 for item in shared)
    comparisons = result["comparisons_vs_independent"]
    assert isinstance(comparisons, list)
    assert all(item["saving_fraction"] == 0.75 for item in comparisons)
    matrix = json.loads((output / "matrix.json").read_text(encoding="utf-8"))
    assert all(row["mesh_attribute_bytes"] == 1560 for row in matrix)
    assert all(row["package_texture_png_bytes"] == 3 for row in matrix)
    assert all(row["export_glb_package_bytes"] == 11 for row in matrix)
    assert all("faces" not in row["material"] and row["material"]["faces_count"] == 1 for row in matrix)
    assert all((output / row["record_path"]).is_file() for row in matrix)
    setups = json.loads((output / "setup.json").read_text(encoding="utf-8"))
    assert all(item["metadata"]["initialization"]["fixed_initialization"] for item in setups)
    report = (output / "report.md").read_text(encoding="utf-8")
    assert "0.012345" in report and "2.75" in report and "75.00%" in report
    assert (output / "source_snapshot/src/reseam/frozen.py").read_text() == "# frozen source\n"


def test_failure_remains_in_matrix_and_negative_report_has_no_saving_claim(
    comparison_project: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, config = comparison_project

    def solve(
        inputs: object, method: str, seed: int, resolution: int, folder: Path,
        *, seconds: float, options: object,
    ) -> dict[str, object]:
        del inputs, seed, resolution, folder, seconds, options
        if method == "joint":
            raise ValueError("FIXED_TEST_INITIALIZATION_FAILURE")
        return solver_result(False, 200)

    install_solver(monkeypatch, solve)
    result = run_budget_comparison(root, config, root / "failed")
    assert result["status"] == "COMPARISON_COMPLETE"
    assert result["planned_rows"] == 8 and result["validated_rows"] == 0
    assert result["status_counts"] == {"QUALITY_REJECTED": 4, "TARGET_FAILED": 4}
    report = (root / "failed/report.md").read_text(encoding="utf-8")
    assert "本轮未取得满足全部合同的候选" in report
    assert "NOT_COMPARABLE" in report
    assert "75.00%" not in report


def test_setup_failure_preserves_unrun_targets_and_does_not_reuse_other_model(
    comparison_project: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, config = comparison_project

    def prepare(root: Path, model: str, options: object, deadline: float) -> object:
        del root, options, deadline
        if model == "dragon":
            raise ValueError("FIXED_MODEL_SETUP_FAILED")
        return model

    def solve(
        inputs: object, method: str, seed: int, resolution: int, folder: Path,
        *, seconds: float, options: object,
    ) -> dict[str, object]:
        del method, seed, resolution, folder, seconds, options
        assert inputs == "energy_blade"
        return solver_result(True, 100)

    install_solver(monkeypatch, solve, prepare)
    result = run_budget_comparison(root, config, root / "setup_failure")
    assert result["planned_rows"] == 8
    assert result["validated_rows"] == 4
    matrix = json.loads((root / "setup_failure/matrix.json").read_text(encoding="utf-8"))
    assert all(row["status"] == "NOT_RUN_INIT_FAILED" for row in matrix if row["model"] == "dragon")


def test_source_mutation_invalidates_previous_results_and_keeps_all_unrun_rows(
    comparison_project: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, config = comparison_project

    def solve(
        inputs: object, method: str, seed: int, resolution: int, folder: Path,
        *, seconds: float, options: object,
    ) -> dict[str, object]:
        del inputs, method, seed, resolution, folder, seconds, options
        (root / "src/reseam/new_source.py").write_text("# changed\n", encoding="utf-8")
        return solver_result(True, 100)

    install_solver(monkeypatch, solve)
    result = run_budget_comparison(root, config, root / "source_change")
    assert result["status"] == "SOURCE_OR_INPUT_CHANGED"
    assert result["source_unchanged"] is False and result["input_unchanged"] is True
    assert result["validated_rows"] == 0
    matrix = json.loads((root / "source_change/matrix.json").read_text(encoding="utf-8"))
    assert len(matrix) == 8
    assert sum(row["status"] == "NOT_RUN_SOURCE_CHANGED" for row in matrix) == 7
    assert not any(row["status"] == "RUNNING" for row in matrix)


def test_asset_mutation_invalidates_even_successful_solver_claims(
    comparison_project: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, config = comparison_project

    def solve(
        inputs: object, method: str, seed: int, resolution: int, folder: Path,
        *, seconds: float, options: object,
    ) -> dict[str, object]:
        del inputs, method, seed, resolution, folder, seconds, options
        (root / "assets/v1/dragon/high.glb").write_bytes(b"modified")
        return solver_result(True, 100)

    install_solver(monkeypatch, solve)
    result = run_budget_comparison(root, config, root / "asset_change")
    assert result["status"] == "SOURCE_OR_INPUT_CHANGED"
    assert result["source_unchanged"] is True and result["input_unchanged"] is False
    assert result["validated_rows"] == 0
    assert (root / "asset_change/budget_account.json").is_file()


def test_missing_config_after_start_still_finalizes_integrity_and_budget(
    comparison_project: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, config = comparison_project

    def solve(
        inputs: object, method: str, seed: int, resolution: int, folder: Path,
        *, seconds: float, options: object,
    ) -> dict[str, object]:
        del inputs, method, seed, resolution, folder, seconds, options
        config.unlink()
        return solver_result(True, 100)

    install_solver(monkeypatch, solve)
    result = run_budget_comparison(root, config, root / "deleted_config")
    assert result["status"] == "SOURCE_OR_INPUT_CHANGED"
    assert result["validated_rows"] == 0
    assert (root / "deleted_config/budget_account.json").is_file()
    integrity = json.loads((root / "deleted_config/integrity.json").read_text(encoding="utf-8"))
    assert str(config) in integrity["read_errors"]


def test_hash_mismatch_before_preparation_has_complete_failure_artifacts(
    comparison_project: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, config = comparison_project
    (root / "assets/v1/dragon/high.glb").write_bytes(b"already modified")

    def bindings_forbidden() -> SolverBindings:
        raise AssertionError("Should reject asset hashes before solver loading")

    monkeypatch.setattr("reseam.budget_compare._solver_bindings", bindings_forbidden)
    result = run_budget_comparison(root, config, root / "hash_mismatch")
    assert result["status"] == "COMPARISON_FAILED"
    assert "ASSET_HASH_MISMATCH" in str(result["reason"])
    assert result["validated_rows"] == 0
    assert (root / "hash_mismatch/report.md").is_file()


def test_exact_setup_amortization_and_target_overrun_are_reported(
    comparison_project: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, config = comparison_project
    clock = [0.0]
    monkeypatch.setattr("reseam.budget_compare.time.monotonic", lambda: clock[0])

    def prepare(root: Path, model: str, options: object, deadline: float) -> object:
        del root, options, deadline
        clock[0] += 4
        return model

    def solve(
        inputs: object, method: str, seed: int, resolution: int, folder: Path,
        *, seconds: float, options: object,
    ) -> dict[str, object]:
        del inputs, seed, resolution, folder, options
        clock[0] += seconds + 1 if method == "joint" else 3
        return solver_result(True, 100)

    install_solver(monkeypatch, solve, prepare)
    result = run_budget_comparison(root, config, root / "costs")
    assert result["validated_rows"] == 4
    matrix = json.loads((root / "costs/matrix.json").read_text(encoding="utf-8"))
    assert all(row["setup_amortized_seconds"] == 1 for row in matrix)
    assert all(row["status"] == "TIME_BUDGET_EXHAUSTED" for row in matrix if row["method"] == "joint")
    account = json.loads((root / "costs/budget_account.json").read_text(encoding="utf-8"))
    assert account["setup_seconds"] == 8
    assert account["target_seconds"] == 156
    assert account["observed_total_wall_seconds"] == 164
    assert sum(row["charged_seconds_with_setup"] for row in account["row_charges"]) == 164
    assert account["historical_accounts_modified"] is False


def test_incomplete_material_gate_cannot_enter_minimum_set(
    comparison_project: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, config = comparison_project

    def solve(
        inputs: object, method: str, seed: int, resolution: int, folder: Path,
        *, seconds: float, options: object,
    ) -> dict[str, object]:
        del inputs, method, seed, resolution, folder, seconds, options
        result = solver_result(True, 100)
        del result["render"]
        return result

    install_solver(monkeypatch, solve)
    result = run_budget_comparison(root, config, root / "missing_gate")
    assert result["validated_rows"] == 0
    minima = result["minimum_results"]
    assert isinstance(minima, list)
    assert all(row["minimum_payload_bytes"] is None for row in minima)
    assert result["status_counts"] == {"INVALID_ACCEPTANCE_CLAIM": 8}


@pytest.mark.parametrize("raster", [
    {}, {"raster_checked": True, "raster_valid": False},
    {"raster_checked": False, "raster_valid": True},
])
def test_actual_raster_checks_are_required_for_acceptance(
    comparison_project: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch,
    raster: dict[str, bool],
) -> None:
    root, config = comparison_project

    def solve(
        inputs: object, method: str, seed: int, resolution: int, folder: Path,
        *, seconds: float, options: object,
    ) -> dict[str, object]:
        del inputs, method, seed, resolution, folder, seconds, options
        result = solver_result(True, 100)
        material = cast(dict[str, object], result["material"])
        material.pop("raster_checked")
        material.pop("raster_valid")
        material.update(raster)
        return result

    install_solver(monkeypatch, solve)
    result = run_budget_comparison(root, config, root / "missing_raster")
    assert result["validated_rows"] == 0
    assert result["status_counts"] == {"INVALID_ACCEPTANCE_CLAIM": 8}


def test_unexpected_solver_exception_is_preserved_without_a_running_summary(
    comparison_project: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, config = comparison_project

    def solve(
        inputs: object, method: str, seed: int, resolution: int, folder: Path,
        *, seconds: float, options: object,
    ) -> dict[str, object]:
        del inputs, method, seed, resolution, folder, seconds, options
        raise KeyError("injected unexpected solver error")

    install_solver(monkeypatch, solve)
    result = run_budget_comparison(root, config, root / "unexpected")
    assert result["status"] == "COMPARISON_COMPLETE"
    assert result["status_counts"] == {"UNEXPECTED_SOLVER_FAILURE": 8}
    record = json.loads((root / "unexpected/energy_blade/seed17/independent/64/record.json").read_text())
    assert "KeyError" in record["traceback"]
    assert (root / "unexpected/budget_account.json").is_file()


def test_native_mikktspace_source_and_backend_are_frozen_and_snapshotted(
    comparison_project: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, config = comparison_project
    native = root / "artifacts/runtime/mikktspace"
    native.mkdir(parents=True)
    for filename in ("mikktspace.c", "mikktspace.h", "mikktspace.dll", "build_manifest.json"):
        (native / filename).write_bytes(filename.encode())

    def solve(
        inputs: object, method: str, seed: int, resolution: int, folder: Path,
        *, seconds: float, options: object,
    ) -> dict[str, object]:
        del inputs, method, seed, resolution, folder, seconds, options
        (native / "mikktspace.dll").write_bytes(b"changed backend")
        return solver_result(True, 100)

    install_solver(monkeypatch, solve)
    output = root / "native_changed"
    result = run_budget_comparison(root, config, output)
    assert result["status"] == "SOURCE_OR_INPUT_CHANGED"
    assert result["validated_rows"] == 0
    snapshot = output / "source_snapshot/artifacts/runtime/mikktspace"
    assert (snapshot / "mikktspace.dll").read_bytes() == b"mikktspace.dll"
    assert all((snapshot / filename).is_file() for filename in ("mikktspace.c", "mikktspace.h", "build_manifest.json"))


def test_native_build_script_is_in_source_lock_and_snapshot(
    comparison_project: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, config = comparison_project
    (root / "scripts").mkdir()
    build = root / "scripts/build_budget_mikktspace.py"
    build.write_text("# frozen build script\n", encoding="utf-8")

    def solve(
        inputs: object, method: str, seed: int, resolution: int, folder: Path,
        *, seconds: float, options: object,
    ) -> dict[str, object]:
        del inputs, method, seed, resolution, folder, seconds, options
        return solver_result(True, 100)

    install_solver(monkeypatch, solve)
    output = root / "build_script"
    result = run_budget_comparison(root, config, output)
    assert result["source_unchanged"] is True
    source = json.loads((output / "source.json").read_text(encoding="utf-8"))
    assert source["source_sha256"][str(build)] == sha256(build)
    assert (output / "source_snapshot/scripts/build_budget_mikktspace.py").read_text() == "# frozen build script\n"


def test_refuses_overwrite_and_validates_formal_matrix(comparison_project: tuple[Path, Path]) -> None:
    root, config = comparison_project
    output = root / "existing"
    output.mkdir()
    (output / "marker").write_bytes(b"keep")
    with pytest.raises(FileExistsError):
        run_budget_comparison(root, config, output)
    assert (output / "marker").read_bytes() == b"keep"
    assert load_comparison_config(config).resolutions == (64, 128)
    with pytest.raises(ValueError, match="Formal comparison"):
        ComparisonConfig("invalid_formal", methods=("independent", "joint"), formal=True)
    assert ComparisonConfig("valid_formal", formal=True).formal
    assert ComparisonConfig(
        "formal_with_optcuts", methods=("independent", "old_pair", "geometry_only", "joint", "optcuts"),
        formal=True,
    ).formal


def test_manifest_path_escape_is_rejected_before_solver(
    comparison_project: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, config = comparison_project
    manifest_path = root / "assets/v1/manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["models"][0]["files"][0]["path"] = "../../outside.glb"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    def bindings_forbidden() -> SolverBindings:
        raise AssertionError("Manifest path escape must fail before solver loading")

    monkeypatch.setattr("reseam.budget_compare._solver_bindings", bindings_forbidden)
    result = run_budget_comparison(root, config, root / "escaped")
    assert result["status"] == "COMPARISON_FAILED"
    assert "escapes" in str(result["reason"])
    assert result["validated_rows"] == 0
