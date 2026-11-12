"""PBR comparison must bind both real models and keep acceptance pending."""

import shutil
from pathlib import Path
from typing import cast

import numpy as np
import pytest
from PIL import Image
from test_artist_tools import deadline, source
from test_pbr_visual_review import create_pipeline

from reseam.agent_tools import artist_review
from reseam.agent_tools.pbr_review import compare_pbr
from reseam.agent_tools.state import file_hash, read_object, snapshot_result, write_json


def test_pbr_comparison_preserves_render_contract(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot, binding, _ = create_pipeline(tmp_path)
    before_export = tmp_path / "before_export"
    shutil.copytree(tmp_path / "export", before_export)
    before_binding = {**binding, "model": str(before_export / "asset.glb"),
                      "export_manifest": str(before_export / "export.json")}
    calls: list[dict[str, object]] = []

    def fake_preview(request: dict[str, object], output: Path, deadline: float) -> dict[str, object]:
        calls.append(request)
        output.mkdir(parents=True)
        geometry = output / "expected_geometry.npz"
        surface = snapshot.state.surface
        np.savez_compressed(geometry, vertices=surface.mesh.vertices, faces=surface.mesh.faces,
                            corner_uv=surface.uv[surface.uv_faces])
        config = {"model": request["model"], "expected_geometry": str(geometry), "size": request["size"],
                  "cameras": [["front", 0.0, 0.0]], "channels": request["channels"],
                  "lights": request["lights"], "distances": [1], "focus_points": []}
        write_json(output / "render-request.json", config)
        images = []
        for channel in cast(list[str], request["channels"]):
            for light in cast(list[str], request["lights"]):
                image_path = output / f"{channel}-{light}.png"
                Image.new("RGB", (cast(int, request["size"]), cast(int, request["size"])), (90, 120, 150)).save(image_path)
                images.append({"path": str(image_path), "channel": channel, "camera": "front",
                               "light": light, "distance": 1})
        record = {"images": images, "model": request["model"], "model_sha256": file_hash(Path(str(request["model"]))),
                  "state_hash": snapshot.state_hash, "geometry_and_uv_reimport_match": True,
                  "renderer": "constructed render-contract fixture", "visual_decision": "pending"}
        write_json(output / "preview.json", record)
        return {"preview": str(output / "preview.json"), **record}

    monkeypatch.setattr(artist_review, "preview", fake_preview)
    result = compare_pbr({**binding, "before": before_binding,
        "channels": ["normal_lit", "material"], "lights": ["soft", "side"], "size": 128}, tmp_path / "compare", deadline())
    assert [call["model"] for call in calls] == [before_binding["model"], binding["model"]]
    assert all(call["channels"] == ["normal_lit", "material"] for call in calls)
    assert all(call["lights"] == ["soft", "side"] for call in calls)
    assert result["visual_decision"] == "pending"
    comparison = read_object(tmp_path / "compare/comparison.json")
    assert comparison["engine_acceptance"] == "not_run"
    assert comparison["scope"] == "frozen_actual_full_pbr_comparison_v1"
    assert comparison["comparison_hash"]


def test_pbr_comparison_rejects_different_geometry(tmp_path: Path) -> None:
    first = tmp_path / "a"
    second = tmp_path / "b"
    first.mkdir()
    second.mkdir()
    a, b = source(first), source(second, shared=True)
    with pytest.raises(ValueError, match="SAME_GEOMETRY"):
        compare_pbr({**snapshot_result(b), "before": snapshot_result(a)}, tmp_path / "bad", deadline())
