from __future__ import annotations

import importlib.util
import json
import shutil
import sys
from pathlib import Path
from types import ModuleType

import pytest
from PIL import Image

from reseam.budget_compare import (
    SolverBindings,
    build_terminal_texture_gallery,
    run_budget_comparison,
    write_comparison_report,
)


def load_gallery() -> ModuleType:
    path = Path(__file__).resolve().parents[1] / "scripts/build_budget_texture_gallery.py"
    spec = importlib.util.spec_from_file_location("budget_texture_gallery", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


GALLERY = load_gallery()


def write_row(round_path: Path, method: str, seed: int, status: str) -> dict[str, object]:
    record = round_path / f"blade/seed{seed}/{method}/8/record.json"
    record.parent.mkdir(parents=True, exist_ok=True)
    record.write_text("{}\n", encoding="utf-8")
    return {"model": "blade", "method": method, "seed": seed, "resolution": 8,
            "status": status, "valid": status == "VALIDATED", "record_path": record.relative_to(round_path).as_posix()}


def write_textures(folder: Path, *, corrupt: bool = False, resolution: int = 8) -> None:
    folder.mkdir(parents=True)
    for channel, color in (("BaseColor", (10, 30, 60)), ("Normal", (128, 128, 255)), ("ORM", (255, 70, 0))):
        for mip in range(resolution.bit_length()):
            path = folder / f"{channel}_mip{mip}.png"
            if corrupt and channel == "Normal" and mip == 0:
                path.write_bytes(b"broken_png")
            else:
                Image.new("RGB", (max(1, resolution >> mip), max(1, resolution >> mip)), color).save(path)


def write_matrix(round_path: Path, rows: list[dict[str, object]]) -> None:
    (round_path / "matrix.json").write_text(json.dumps(rows), encoding="utf-8")


def test_all_planned_rows_real_pngs_fixed_seed_and_readonly(tmp_path: Path) -> None:
    round_path = tmp_path / "round"
    rows = [write_row(round_path, "joint", seed, "QUALITY_REJECTED") for seed in (17, 29)]
    rows.append(write_row(round_path, "optcuts", 17, "TIME_BUDGET_EXHAUSTED"))
    for row in rows[:2]:
        write_textures((round_path / str(row["record_path"])).parent / "textures")
    write_matrix(round_path, rows)
    original = {path: path.read_bytes() for path in round_path.rglob("*") if path.is_file()}
    result = GALLERY.build_gallery(round_path, tmp_path / "gallery")
    assert result["planned_rows"] == 3
    assert result["availability_counts"] == {"PRODUCED": 2, "NOT_PRODUCED": 1}
    assert result["source_integrity"]["unchanged"] is True
    assert result["source_integrity"]["png_files_checked"] == 24
    cases = result["rows"]
    assert [case["preview_selected"] for case in cases] == [True, False, True]
    assert all(case["valid"] is False for case in cases)
    texture = cases[0]["sources"][0]["files"][0]
    assert texture["width"] == 8 and texture["height"] == 8
    assert texture["bytes"] > 0 and len(texture["sha256"]) == 64
    assert texture["source_relative_path"].endswith("textures/BaseColor_mip0.png")
    assert len(cases[0]["sources"][0]["files"]) == 12
    assert len(cases[0]["thumbnails"]) == 3 and cases[0]["contact_sheet"]
    assert cases[2]["thumbnails"] == {} and cases[2]["contact_sheet"] is None
    for path, data in original.items():
        assert path.read_bytes() == data
    markdown = (tmp_path / "gallery/gallery.md").read_text(encoding="utf-8")
    overview, _ = markdown.split("## 全部预定项")
    assert "seed29" not in overview
    assert "NOT_PRODUCED" in markdown and "TIME_BUDGET_EXHAUSTED" in markdown
    assert "[mip3]" in markdown and "[原图]" in markdown


def test_candidate_fallback_keeps_corrupt_primary_and_all_candidate_sources(tmp_path: Path) -> None:
    round_path = tmp_path / "round"
    row = write_row(round_path, "joint", 17, "QUALITY_REJECTED")
    case = (round_path / str(row["record_path"])).parent
    write_textures(case / "textures", corrupt=True)
    for candidate in ("candidate_002", "candidate_001"):
        write_textures(case / "final_validation" / candidate / "textures")
    write_matrix(round_path, [row])
    result = GALLERY.build_gallery(round_path, tmp_path / "gallery")
    case_result = result["rows"][0]
    assert case_result["selected_source_index"] == 1
    assert case_result["sources"][1]["candidate"] == "candidate_001"
    assert len(case_result["sources"]) == 3
    assert any("DECODE_FAILED" in error for error in case_result["sources"][0]["errors"])
    assert any("CANDIDATE_DIAGNOSTIC_ONLY" in error for error in case_result["errors"])
    assert case_result["valid"] is False


def test_incomplete_chain_and_missing_record_are_visible(tmp_path: Path) -> None:
    round_path = tmp_path / "round"
    row = write_row(round_path, "joint", 17, "TARGET_FAILED")
    case = (round_path / str(row["record_path"])).parent
    write_textures(case / "textures")
    (case / "textures/Normal_mip3.png").unlink()
    (case / "record.json").unlink()
    write_matrix(round_path, [row])
    result = GALLERY.build_gallery(round_path, tmp_path / "gallery")
    item = result["rows"][0]
    assert item["texture_availability"] == "PARTIAL_OR_INVALID"
    assert item["contact_sheet"] is not None  # A complete mip0 preview is still an actual image.
    assert item["record_exists"] is False and item["record_sha256"] is None
    assert any("Normal_mip3" in error for error in item["sources"][0]["errors"])
    assert any("RECORD_READ_FAILED" in error for error in item["errors"])


def test_rejects_overwrite_active_round_and_path_escape(tmp_path: Path) -> None:
    round_path = tmp_path / "round"
    row = write_row(round_path, "joint", 17, "RUNNING")
    write_matrix(round_path, [row])
    output = tmp_path / "gallery"
    with pytest.raises(ValueError, match="active"):
        GALLERY.build_gallery(round_path, output)
    assert not output.exists()
    row["status"] = "TARGET_FAILED"
    row["record_path"] = "../../outside/record.json"
    write_matrix(round_path, [row])
    with pytest.raises(ValueError, match="escapes"):
        GALLERY.build_gallery(round_path, output)
    assert not output.exists()
    output.mkdir()
    marker = output / "keep"
    marker.write_text("keep", encoding="utf-8")
    with pytest.raises(FileExistsError):
        GALLERY.build_gallery(round_path, output)
    assert marker.read_text(encoding="utf-8") == "keep"


def test_source_mutation_during_preview_is_reported(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    round_path = tmp_path / "round"
    row = write_row(round_path, "joint", 17, "QUALITY_REJECTED")
    textures = (round_path / str(row["record_path"])).parent / "textures"
    write_textures(textures)
    write_matrix(round_path, [row])
    real_preview = GALLERY.preview_case

    def mutate_after_preview(case: object, output: Path) -> None:
        real_preview(case, output)
        (textures / "ORM_mip3.png").write_bytes(b"external change")

    monkeypatch.setattr(GALLERY, "preview_case", mutate_after_preview)
    with pytest.raises(RuntimeError, match="Source changed"):
        GALLERY.build_gallery(round_path, tmp_path / "gallery")
    manifest = json.loads((tmp_path / "gallery/texture_manifest.json").read_text(encoding="utf-8"))
    assert manifest["source_integrity"]["unchanged"] is False
    assert manifest["source_integrity"]["changed_files"] == [str((textures / "ORM_mip3.png").resolve())]


def test_terminal_overlay_retains_original_and_selected_matrix_hashes(tmp_path: Path) -> None:
    round_path = tmp_path / "round"
    row = write_row(round_path, "joint", 17, "RUNNING")
    write_textures((round_path / str(row["record_path"])).parent / "textures")
    write_matrix(round_path, [row])
    original = round_path / "matrix.json"
    original_data = original.read_bytes()
    row["status"] = "ABORTED_FOR_COMPLETION_AUDIT"
    overlay = round_path / "matrix.after_audit.json"
    overlay.write_text(json.dumps([row]), encoding="utf-8")
    overlay_data = overlay.read_bytes()
    result = GALLERY.build_gallery(round_path, tmp_path / "gallery", overlay)
    assert result["matrix_source_kind"] == "DIAGNOSTIC_TERMINAL_OVERLAY"
    assert result["qualification"] == "ABORTED_DIAGNOSTIC_ONLY_NOT_COMPLETE_FORMAL_COMPARISON"
    assert result["selected_matrix"] == str(overlay.resolve())
    assert result["original_matrix"] == str(original.resolve())
    assert result["selected_matrix_sha256"] == GALLERY.sha256(overlay)
    assert result["original_matrix_sha256"] == GALLERY.sha256(original)
    assert result["source_integrity"]["selected_matrix_unchanged"] is True
    assert result["source_integrity"]["original_matrix_unchanged"] is True
    assert result["rows"][0]["status"] == "ABORTED_FOR_COMPLETION_AUDIT"
    assert original.read_bytes() == original_data and overlay.read_bytes() == overlay_data
    assert "DIAGNOSTIC_TERMINAL_OVERLAY" in (tmp_path / "gallery/gallery.md").read_text(encoding="utf-8")


def test_overlay_path_security_and_active_rows_are_rejected(tmp_path: Path) -> None:
    round_path = tmp_path / "round"
    row = write_row(round_path, "joint", 17, "NOT_RUN")
    write_matrix(round_path, [row])
    output = tmp_path / "gallery"
    outside = tmp_path / "matrix.outside.json"
    outside.write_text(json.dumps([row]), encoding="utf-8")
    with pytest.raises(ValueError, match="escapes"):
        GALLERY.build_gallery(round_path, output, outside)
    with pytest.raises(FileNotFoundError, match="existing file"):
        GALLERY.build_gallery(round_path, output, round_path / "missing.json")
    with pytest.raises(FileNotFoundError, match="existing file"):
        GALLERY.build_gallery(round_path, output, round_path)
    overlay = round_path / "matrix.after_audit.json"
    overlay.write_text(json.dumps([row]), encoding="utf-8")
    with pytest.raises(ValueError, match="active"):
        GALLERY.build_gallery(round_path, output, overlay)
    row["status"] = "NOT_RUN_AUDIT_RESTART"
    row["method"] = "changed_method"
    overlay.write_text(json.dumps([row]), encoding="utf-8")
    with pytest.raises(ValueError, match="identities"):
        GALLERY.build_gallery(round_path, output, overlay)
    assert not output.exists()


def test_baseline_sources_are_diagnostic_fallback_after_final_and_case(tmp_path: Path) -> None:
    round_path = tmp_path / "round"
    rows = [write_row(round_path, method, 17, "QUALITY_REJECTED")
            for method in ("baseline_only", "final_available", "case_available")]
    for row in rows:
        case = (round_path / str(row["record_path"])).parent
        for candidate in ("candidate_002", "candidate_001"):
            write_textures(case / "baseline_validation" / candidate / "textures")
        if row["method"] != "baseline_only":
            write_textures(case / "final_validation/candidate_003/textures")
        if row["method"] == "case_available":
            write_textures(case / "textures")
    write_matrix(round_path, rows)
    before = {path: path.read_bytes() for path in round_path.rglob("*") if path.is_file()}
    result = GALLERY.build_gallery(round_path, tmp_path / "gallery")
    selected = [case["sources"][case["selected_source_index"]] for case in result["rows"]]
    assert [source["kind"] for source in selected] == [
        "BASELINE_VALIDATION_CANDIDATE", "FINAL_VALIDATION_CANDIDATE", "CASE_TEXTURES",
    ]
    assert selected[0]["candidate"] == "candidate_001"
    assert any("BASELINE_DIAGNOSTIC_ONLY" in error for error in result["rows"][0]["errors"])
    assert len(result["rows"][2]["sources"]) == 4
    assert set(result["channel_contacts"]) == {"BaseColor", "Normal", "ORM"}
    for path, data in before.items():
        assert path.read_bytes() == data
    markdown = (tmp_path / "gallery/gallery.md").read_text(encoding="utf-8")
    assert "baseline_validation/candidate_002/textures/ORM_mip3.png" in markdown
    assert "NOT_SELECTED" in markdown and "BASELINE_DIAGNOSTIC_ONLY" in markdown


def test_contact_images_are_only_created_for_real_seed17_channels(tmp_path: Path) -> None:
    round_path = tmp_path / "round"
    rows = [write_row(round_path, "joint", seed, "QUALITY_REJECTED") for seed in (17, 29)]
    other = (round_path / str(rows[1]["record_path"])).parent
    write_textures(other / "textures")
    write_matrix(round_path, rows)
    first = GALLERY.build_gallery(round_path, tmp_path / "no_seed17_images")
    assert first["channel_contacts"] == {}
    assert not list((tmp_path / "no_seed17_images").glob("*_seed17_contact.png"))
    seed17 = (round_path / str(rows[0]["record_path"])).parent / "textures"
    seed17.mkdir()
    Image.new("RGB", (8, 8), (120, 130, 140)).save(seed17 / "BaseColor_mip0.png")
    second = GALLERY.build_gallery(round_path, tmp_path / "one_channel")
    assert set(second["channel_contacts"]) == {"BaseColor"}
    assert second["rows"][0]["valid"] is False


def comparison_project(tmp_path: Path, gallery_source: str) -> tuple[Path, Path]:
    root = tmp_path / "project"
    (root / "src/reseam").mkdir(parents=True)
    (root / "src/reseam/frozen.py").write_text("# fixed source\n", encoding="utf-8")
    (root / "derivation").mkdir()
    (root / "derivation/v1.md").write_text("# fixed derivation\n", encoding="utf-8")
    asset = root / "assets/v1/energy_blade/high.glb"
    asset.parent.mkdir(parents=True)
    asset.write_bytes(b"mock fixed asset")
    (root / "assets/v1/manifest.json").write_text(json.dumps({"models": [
        {"id": "energy_blade", "files": [{"path": "energy_blade/high.glb", "sha256": GALLERY.sha256(asset)}]},
    ]}), encoding="utf-8")
    config = root / "config.json"
    config.write_text(json.dumps({
        "round_id": "gallery_mock", "models": ["energy_blade"], "methods": ["joint"],
        "seeds": [17], "resolutions": [16], "seconds_per_target": 10,
        "setup_seconds_per_model": 10, "formal": False,
    }), encoding="utf-8")
    if gallery_source != "missing":
        (root / "scripts").mkdir()
        target = root / "scripts/build_budget_texture_gallery.py"
        if gallery_source == "real":
            shutil.copyfile(Path(__file__).resolve().parents[1] / "scripts/build_budget_texture_gallery.py", target)
        else:
            target.write_text("raise RuntimeError('injected gallery failure')\n", encoding="utf-8")
    return root, config


def install_comparison_solver(monkeypatch: pytest.MonkeyPatch) -> None:
    def options(raw: dict[str, object]) -> object:
        return raw

    def prepare(root: Path, model: str, options: object, deadline: float) -> object:
        del root, model, options, deadline
        return object()

    def solve(
        inputs: object, method: str, seed: int, resolution: int, output: Path,
        *, seconds: float, options: object,
    ) -> dict[str, object]:
        del inputs, method, seed, seconds, options
        write_textures(output / "baseline_validation/candidate_001/textures", resolution=resolution)
        return {"status": "QUALITY_REJECTED", "valid": False, "payload_bytes": 123,
                "geometry": {"valid": True}, "material": {"valid": False}, "render": {"valid": False}}

    def bindings() -> SolverBindings:
        return SolverBindings(options, prepare, solve)

    monkeypatch.setattr("reseam.budget_compare._solver_bindings", bindings)


def test_terminal_comparison_builds_gallery_embeds_three_channels_and_locks_script(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, config = comparison_project(tmp_path, "real")
    install_comparison_solver(monkeypatch)
    script = root / "scripts/build_budget_texture_gallery.py"
    original = script.read_bytes()
    output = root / "run"
    result = run_budget_comparison(root, config, output)
    gallery = result["texture_gallery"]
    assert isinstance(gallery, dict) and gallery["status"] == "BUILT"
    assert result["status"] == "COMPARISON_COMPLETE" and result["validated_rows"] == 0
    report = (output / "report.md").read_text(encoding="utf-8")
    for channel in ("BaseColor", "Normal", "ORM"):
        assert f"![{channel} seed17 实际贴图](texture_gallery/{channel}_seed17_contact.png)" in report
    assert "texture_gallery/gallery.md" in report and "texture_gallery/texture_manifest.json" in report
    manifest = json.loads((output / "texture_gallery/texture_manifest.json").read_text(encoding="utf-8"))
    assert manifest["matrix_sha256"] == GALLERY.sha256(output / "matrix.json")
    case = manifest["rows"][0]
    assert case["record_sha256"] == GALLERY.sha256(output / case["record_path"])
    assert case["sources"][case["selected_source_index"]]["kind"] == "BASELINE_VALIDATION_CANDIDATE"
    source = json.loads((output / "source.json").read_text(encoding="utf-8"))
    assert source["source_sha256"][str(script)] == GALLERY.sha256(script)
    assert (output / "source_snapshot/scripts/build_budget_texture_gallery.py").read_bytes() == original
    assert script.read_bytes() == original
    account = json.loads((output / "budget_account.json").read_text(encoding="utf-8"))
    assert account["postprocessing_gallery_seconds"] == gallery["wall_seconds"]
    assert account["charged_total_seconds"] == account["observed_total_wall_seconds"]
    assert sum(row["charged_seconds_with_setup"] for row in account["row_charges"]) + gallery["wall_seconds"] \
        == pytest.approx(account["charged_total_seconds"])
    # The report never embeds an attachment image that is no longer present.
    (output / "texture_gallery/Normal_seed17_contact.png").unlink()
    matrix = json.loads((output / "matrix.json").read_text(encoding="utf-8"))
    write_comparison_report(output, result, matrix)
    updated = (output / "report.md").read_text(encoding="utf-8")
    assert "![Normal seed17 实际贴图]" not in updated
    assert "![BaseColor seed17 实际贴图]" in updated


@pytest.mark.parametrize("source,status", [("missing", "NOT_AVAILABLE"), ("broken", "FAILED")])
def test_terminal_gallery_unavailable_or_failed_preserves_numeric_results(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, source: str, status: str,
) -> None:
    root, config = comparison_project(tmp_path, source)
    install_comparison_solver(monkeypatch)
    output = root / "run"
    result = run_budget_comparison(root, config, output)
    assert result["status"] == "COMPARISON_COMPLETE" and result["status_counts"] == {"QUALITY_REJECTED": 1}
    assert result["texture_gallery"]["status"] == status  # type: ignore[index]
    assert (output / "matrix.json").is_file() and (output / "budget_account.json").is_file()
    report = (output / "report.md").read_text(encoding="utf-8")
    assert f"`{status}`" in report and "seed17 实际贴图](" not in report


def test_terminal_hook_refuses_to_replace_existing_gallery(tmp_path: Path) -> None:
    root, _ = comparison_project(tmp_path, "real")
    output = root / "run"
    output.mkdir()
    row = write_row(output, "joint", 17, "QUALITY_REJECTED")
    write_textures((output / str(row["record_path"])).parent / "textures")
    write_matrix(output, [row])
    first = build_terminal_texture_gallery(root, output)
    assert first["status"] == "BUILT"
    original = (output / "texture_gallery/BaseColor_seed17_contact.png").read_bytes()
    second = build_terminal_texture_gallery(root, output)
    assert second["status"] == "FAILED" and "FileExistsError" in str(second["reason"])
    assert (output / "texture_gallery/BaseColor_seed17_contact.png").read_bytes() == original


def test_cooperative_stop_finalizes_all_rows_and_gallery_without_starting_next_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, config = comparison_project(tmp_path, "real")
    raw = json.loads(config.read_text(encoding="utf-8"))
    raw["models"], raw["seeds"] = ["energy_blade", "dragon"], [17, 29]
    config.write_text(json.dumps(raw), encoding="utf-8")
    dragon = root / "assets/v1/dragon/high.glb"
    dragon.parent.mkdir()
    dragon.write_bytes(b"second fixed asset")
    manifest_path = root / "assets/v1/manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["models"].append({"id": "dragon", "files": [
        {"path": "dragon/high.glb", "sha256": GALLERY.sha256(dragon)},
    ]})
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    output = root / "run"
    prepared: list[str] = []
    solved: list[int] = []

    def options(raw: dict[str, object]) -> object:
        return raw

    def prepare(root: Path, model: str, options: object, deadline: float) -> object:
        del root, options, deadline
        prepared.append(model)
        return model

    def solve(
        inputs: object, method: str, seed: int, resolution: int, folder: Path,
        *, seconds: float, options: object,
    ) -> dict[str, object]:
        del inputs, method, seconds, options
        solved.append(seed)
        write_textures(folder / "textures", resolution=resolution)
        (output / "STOP_AFTER_CURRENT_TARGET").write_text("test stop requested\n", encoding="utf-8")
        return {"status": "QUALITY_REJECTED", "valid": False, "payload_bytes": 123}

    def bindings() -> SolverBindings:
        return SolverBindings(options, prepare, solve)

    monkeypatch.setattr("reseam.budget_compare._solver_bindings", bindings)
    result = run_budget_comparison(root, config, output)
    assert result["status"] == "COMPARISON_STOPPED_BY_REQUEST"
    assert result["stop_requested"] is True
    assert result["stop_marker_path"] == str(output / "STOP_AFTER_CURRENT_TARGET")
    assert prepared == ["energy_blade"] and solved == [17]
    assert result["status_counts"] == {"QUALITY_REJECTED": 1, "NOT_RUN_STOP_REQUESTED": 3}
    matrix = json.loads((output / "matrix.json").read_text(encoding="utf-8"))
    assert all((output / row["record_path"]).is_file() for row in matrix)
    assert not any(row["status"] in {"RUNNING", "NOT_RUN"} for row in matrix)
    assert all(row["valid"] is False for row in matrix)
    protocol = json.loads((output / "protocol.json").read_text(encoding="utf-8"))
    assert protocol["cooperative_stop"]["remaining_row_status"] == "NOT_RUN_STOP_REQUESTED"
    gallery = json.loads((output / "texture_gallery/texture_manifest.json").read_text(encoding="utf-8"))
    assert gallery["matrix_sha256"] == GALLERY.sha256(output / "matrix.json")
    assert [row["texture_availability"] for row in gallery["rows"]] == [
        "PRODUCED", "NOT_PRODUCED", "NOT_PRODUCED", "NOT_PRODUCED",
    ]
