"""Frozen full-PBR review/export bindings; constructed previews are test fixtures only."""

import time
from pathlib import Path
from typing import cast

import numpy as np
import pytest
from PIL import Image
from test_pbr_reuse import asset, folder

from reseam.agent_tools.artist_review import compare, export_visual, review
from reseam.agent_tools.color_reuse import freeze_target
from reseam.agent_tools.material import bake_material_tool, export_asset
from reseam.agent_tools.state import Snapshot, file_hash, read_object, snapshot_result, write_json
from reseam.agent_tools.worker import execute


def fixture_preview(root: Path, name: str, snapshot: Snapshot, model: Path, channels: list[str]) -> Path:
    output = folder(root, name)
    surface = snapshot.state.surface
    geometry = output / "expected_geometry.npz"
    np.savez_compressed(geometry, vertices=surface.mesh.vertices, faces=surface.mesh.faces,
                        corner_uv=surface.uv[surface.uv_faces])
    config = {"model": str(model), "expected_geometry": str(geometry), "size": 32,
              "cameras": [["front", 0.0, 0.0]],
              "channels": channels, "lights": ["soft"], "distances": [1], "focus_points": []}
    write_json(output / "render-request.json", config)
    images = []
    for channel in channels:
        path = output / (channel + ".png")
        Image.new("RGB", (32, 32), (90, 120, 150)).save(path)
        images.append({"path": str(path), "channel": channel, "camera": "front", "light": "soft", "distance": 1})
    path = output / "preview.json"
    write_json(path, {"model": str(model), "model_sha256": file_hash(model), "state_hash": snapshot.state_hash,
                      "geometry_and_uv_reimport_match": True, "images": images, "renderer": "constructed unit-test fixture"})
    return path


def create_pipeline(tmp_path: Path) -> tuple[Snapshot, dict[str, object], Path]:
    snapshot = asset(tmp_path)
    deadline = time.monotonic() + 60
    request: dict[str, object] = {**snapshot_result(snapshot), "mode": "pbr_reuse", "resolution": 32}
    target = freeze_target(request, folder(tmp_path, "target"), deadline)
    request["target"] = target["target"]
    baked = bake_material_tool(request, folder(tmp_path, "bake"), deadline)
    request["bake"] = baked["bake"]
    export = export_asset({**request, "mode": "diagnostic"}, folder(tmp_path, "export"), deadline)
    model = Path(str(export["asset"]))
    before = fixture_preview(tmp_path, "before", snapshot, model, ["material", "normal_lit"])
    after = fixture_preview(tmp_path, "after", snapshot, model, ["material", "normal_lit"])
    historical = tmp_path / "historical.json"
    write_json(historical, {"mode": "pbr", "before": {"preview": str(before)}, "after": {"preview": str(after)}})
    request.update(mode="pbr", model=str(model), export_manifest=str(tmp_path / "export/export.json"))
    return snapshot, request, historical


@pytest.fixture
def pipeline(tmp_path: Path) -> tuple[Snapshot, dict[str, object], Path]:
    return create_pipeline(tmp_path)


def freeze(root: Path, request: dict[str, object], historical: Path) -> dict[str, object]:
    return compare({**request, "before": request, "existing_comparison": str(historical)},
                   folder(root, "frozen"), time.monotonic() + 60)


def review_request(request: dict[str, object], comparison: dict[str, object], decision: str = "keep") -> dict[str, object]:
    return {**request, "comparison": comparison["comparison"], "decision": decision,
            "reviewer": "agent", "observer": "test-fixture-observer", "notes": "Constructed fixture review only",
            "reviewed_views": ["material|front|soft|1", "normal_lit|front|soft|1"]}


def test_keep_copies_exact_reviewed_glb_mips_without_promoting_numeric(
    tmp_path: Path, pipeline: tuple[Snapshot, dict[str, object], Path],
) -> None:
    _, request, historical = pipeline
    old_hash = file_hash(historical)
    comparison = freeze(tmp_path, request, historical)
    assert file_hash(historical) == old_hash
    receipt = execute("artist.review", review_request(request, comparison), folder(tmp_path, "review"), time.monotonic() + 60)
    assert receipt["status"] == "VISUAL_KEEP" and receipt["execution_success"] is True
    result = execute("artist.export", {**request, "review": receipt["review"]}, folder(tmp_path, "visual"), time.monotonic() + 60)
    assert result["status"] == "EXPORTED_VISUAL" and result["execution_success"] is True
    assert result["visual_accepted"] is True
    assert result["numerical_quality_accepted"] is False
    assert result["numeric_export_mode"] == "diagnostic"
    assert result["human_acceptance"] == "not_run" and result["game_shader_acceptance"] == "not_run"
    assert file_hash(Path(str(result["model"]))) == file_hash(Path(str(request["model"])))
    assert len(list((tmp_path / "visual/texture").glob("*_mip*.png"))) == 18
    assert read_object(tmp_path / "visual/export.json")["quality_accepted"] is False


def test_revert_records_parent_and_refuses_export(
    tmp_path: Path, pipeline: tuple[Snapshot, dict[str, object], Path],
) -> None:
    snapshot, request, historical = pipeline
    comparison = freeze(tmp_path, request, historical)
    receipt = execute("artist.review", review_request(request, comparison, "revert"), folder(tmp_path, "review"), time.monotonic() + 60)
    assert receipt["status"] == "VISUAL_REVERT" and receipt["execution_success"] is True
    assert receipt["visual_accepted"] is False
    assert cast(dict[str, object], receipt["restore"])["state_hash"] == snapshot.state_hash
    result = execute("artist.export", {**request, "review": receipt["review"]}, folder(tmp_path, "visual"), time.monotonic() + 60)
    assert result["status"] == "REJECTED" and result["reason"] == "PBR_VISUAL_REVERT"
    assert result["execution_success"] is False
    assert not (tmp_path / "visual/asset.glb").exists()


@pytest.mark.parametrize("mutation", ["image", "model", "mip", "preview", "geometry"])
def test_review_rejects_changed_frozen_evidence(
    tmp_path: Path, pipeline: tuple[Snapshot, dict[str, object], Path], mutation: str,
) -> None:
    _, request, historical = pipeline
    comparison = freeze(tmp_path, request, historical)
    paths = {"image": "after/material.png", "model": "export/asset.glb",
             "mip": "export/texture/Normal_mip1.png", "preview": "after/preview.json",
             "geometry": "after/expected_geometry.npz"}
    path = tmp_path / paths[mutation]
    if mutation == "image":
        Image.new("RGB", (32, 32), (10, 20, 30)).save(path)
    elif mutation == "preview":
        record = read_object(path)
        record["model_sha256"] = "changed"
        write_json(path, record)
    elif mutation == "geometry":
        with np.load(path, allow_pickle=False) as arrays:
            values = {key: arrays[key] for key in arrays.files}
        values["vertices"] = values["vertices"] + .1
        np.savez_compressed(path, **values)
    else:
        path.write_bytes(path.read_bytes() + b"changed")
    with pytest.raises(ValueError, match="PBR_"):
        review(review_request(request, comparison), folder(tmp_path, "review"), time.monotonic() + 60)


def test_review_requires_both_material_and_normal_actual_views(
    tmp_path: Path, pipeline: tuple[Snapshot, dict[str, object], Path],
) -> None:
    _, request, historical = pipeline
    comparison = freeze(tmp_path, request, historical)
    bad = review_request(request, comparison)
    bad["reviewed_views"] = ["material|front|soft|1"]
    with pytest.raises(ValueError, match="PBR_REVIEW_REQUIRES_MATERIAL_AND_NORMAL_LIT"):
        review(bad, folder(tmp_path, "review"), time.monotonic() + 60)


def test_historical_unfrozen_receipt_cannot_authorize_review(
    tmp_path: Path, pipeline: tuple[Snapshot, dict[str, object], Path],
) -> None:
    _, request, historical = pipeline
    with pytest.raises(ValueError, match="PBR_FROZEN_COMPARISON_HASH_REQUIRED"):
        review(review_request(request, {"comparison": str(historical)}), folder(tmp_path, "review"), time.monotonic() + 60)


def test_freeze_refuses_basecolor_only(
    tmp_path: Path, pipeline: tuple[Snapshot, dict[str, object], Path],
) -> None:
    snapshot, request, historical = pipeline
    only_color = fixture_preview(tmp_path, "only_color", snapshot, Path(str(request["model"])), ["material"])
    old = read_object(historical)
    old["after"] = {"preview": str(only_color)}
    write_json(historical, old)
    with pytest.raises(ValueError, match="PBR_MATERIAL_AND_NORMAL_LIT_VIEWS_REQUIRED"):
        freeze(tmp_path, request, historical)


def test_freeze_requires_complete_exported_mip_hashes(
    tmp_path: Path, pipeline: tuple[Snapshot, dict[str, object], Path],
) -> None:
    _, request, historical = pipeline
    path = Path(str(request["export_manifest"]))
    manifest = read_object(path)
    del cast(dict[str, str], manifest["artifacts"])["texture/Normal_mip1.png"]
    write_json(path, manifest)
    with pytest.raises(ValueError, match="PBR_EXPORT_COMPLETE_MIPS_REQUIRED"):
        freeze(tmp_path, request, historical)


def test_partial_keep_cannot_export_whole_model(
    tmp_path: Path, pipeline: tuple[Snapshot, dict[str, object], Path],
) -> None:
    _, request, historical = pipeline
    for role in ["before", "after"]:
        directory = tmp_path / role
        path = directory / "render-request.json"
        config = read_object(path)
        cast(list[list[object]], config["cameras"]).append(["back", 180.0, 0.0])
        write_json(path, config)
        path = directory / "preview.json"
        record = read_object(path)
        images = cast(list[dict[str, object]], record["images"])
        for item in images.copy():
            new_path = directory / (str(item["channel"]) + "_back.png")
            Image.new("RGB", (32, 32), (90, 120, 150)).save(new_path)
            images.append({**item, "camera": "back", "path": str(new_path)})
        write_json(path, record)
    comparison = freeze(tmp_path, request, historical)
    receipt = review(review_request(request, comparison), folder(tmp_path, "review"), time.monotonic() + 60)
    assert receipt["visual_accepted"] is True and receipt["all_prepared_views_reviewed"] is False
    with pytest.raises(ValueError, match="PBR_KEEP_ALL_WHOLE_SURFACE_VIEWS_BEFORE_EXPORT"):
        export_visual({**request, "review": receipt["review"]}, folder(tmp_path, "visual"), time.monotonic() + 60)


def test_review_cannot_authorize_model_changed_after_observation(
    tmp_path: Path, pipeline: tuple[Snapshot, dict[str, object], Path],
) -> None:
    _, request, historical = pipeline
    comparison = freeze(tmp_path, request, historical)
    receipt = review(review_request(request, comparison), folder(tmp_path, "review"), time.monotonic() + 60)
    model = Path(str(request["model"]))
    model.write_bytes(model.read_bytes() + b"material changed after review")
    with pytest.raises(ValueError, match="PBR_EXPORT_ARTIFACT_CHANGED"):
        export_visual({**request, "review": receipt["review"]}, folder(tmp_path, "visual"), time.monotonic() + 60)
    assert not (tmp_path / "visual/asset.glb").exists()


@pytest.mark.parametrize("absolute", [False, True])
def test_manifest_alias_cannot_escape_visual_export_destination(
    tmp_path: Path, pipeline: tuple[Snapshot, dict[str, object], Path], absolute: bool,
) -> None:
    _, request, historical = pipeline
    path = Path(str(request["export_manifest"]))
    manifest = read_object(path)
    artifacts = cast(dict[str, str], manifest["artifacts"])
    alias = str(path.parent / "asset.glb") if absolute else "../export/asset.glb"
    artifacts[alias] = artifacts["asset.glb"]
    write_json(path, manifest)
    with pytest.raises(ValueError, match="PBR_EXPORT_UNSAFE_ARTIFACT_PATH"):
        freeze(tmp_path, request, historical)
