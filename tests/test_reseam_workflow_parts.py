"""Workflow state binding, rollback, target-review pauses and real GLB collection."""

from __future__ import annotations

import time
from pathlib import Path
from typing import cast

import numpy as np
import pytest
import trimesh
from PIL import Image
from trimesh.visual.material import PBRMaterial
from trimesh.visual.texture import TextureVisuals

from reseam.agent_tools.artist_review import compare as compare_views
from reseam.agent_tools.artist_review import review as review_views
from reseam.agent_tools.color_reuse import bake_color_reuse, export_color_reuse, freeze_target
from reseam.agent_tools.geometry import pack_uv
from reseam.agent_tools.parts import prepare_parts
from reseam.agent_tools.state import (
    Snapshot,
    file_hash,
    load_snapshot,
    object_hash,
    read_object,
    save_snapshot,
    snapshot_result,
    write_json,
)
from reseam.workflows import parts


def prepared(folder: Path) -> Snapshot:
    scene = trimesh.Scene()
    for name, offset, color in (("panel", 0., (10, 110, 210, 255)), ("tab", 3., (230, 60, 40, 255))):
        mesh = trimesh.Trimesh(vertices=[[0., 0, 0], [1., 0, 0], [1., 1, 0], [0., 1, 0]],
                              faces=[[0, 1, 2], [0, 2, 3]], process=False)
        mesh.visual = TextureVisuals(uv=np.array([[0., 0], [1., 0], [1., 1], [0., 1]]),
                                    material=PBRMaterial(baseColorTexture=Image.new("RGBA", (8, 8), color)))
        transform = np.eye(4)
        transform[0, 3] = offset
        scene.add_geometry(mesh, node_name=name, geom_name=name, transform=transform)
    source = folder / "two-panels.glb"
    scene.export(source, file_type="glb")
    result = prepare_parts({"sources": [{"id": "fixture", "path": str(source)}], "placement": "source_world"},
                           folder / "prepared", time.monotonic()+30)
    return load_snapshot(Path(str(result["snapshot"])))


def test_rebuild_failed_part_keeps_parent_and_continues(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = prepared(tmp_path)
    requests: list[dict[str, object]] = []

    def fake(tool: str, request: Path, output: Path) -> dict[str, object]:
        assert tool == "uv.reparameterize"
        record = read_object(request)
        requests.append(record)
        assert record["pack_after"] is False
        output.mkdir()
        if len(requests) == 1:
            return {"status": "REJECTED", "reason": "constructed bad seam"}
        parent = load_snapshot(Path(str(record["snapshot"])))
        child = save_snapshot(parent.state, output / "snapshot", parent.metadata, parent)
        report = output / "reparameterization.json"
        write_json(report, {"old_region_chart_count": 2, "result_chart_count": 1})
        return {"status": "OK", **snapshot_result(child), "report": str(report)}

    monkeypatch.setattr(parts, "run_tool", fake)
    output = tmp_path / "rebuild"
    output.mkdir()
    result = parts.rebuild({**snapshot_result(source), "backend": "harmonic"}, output)
    assert len(cast(list[str], result["failed_parts"])) == 1
    assert requests[1]["state_hash"] == source.state_hash
    assert result["visual_accepted"] is False and result["requires_rebake"] is True
    decisions = cast(list[dict[str, object]], result["decisions"])
    assert decisions[0]["decision"] == "fallback_parent"
    assert decisions[1]["decision"] == "provisional_rebuild_requires_bake_review"


@pytest.mark.parametrize("change,reason", [
    ({"state_hash": "stale"}, "STALE_SNAPSHOT"),
    ({"parts": [{"id": "unknown"}]}, "UNKNOWN_PART"),
])
def test_rebuild_rejects_stale_identity(tmp_path: Path, change: dict[str, object], reason: str) -> None:
    snapshot = prepared(tmp_path)
    with pytest.raises(ValueError, match=reason):
        parts.rebuild({**snapshot_result(snapshot), **change}, tmp_path / "output")


def test_rebuild_rejects_old_faces_before_tool_execution(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    snapshot = prepared(tmp_path)
    first = cast(list[dict[str, object]], snapshot.metadata["parts"])[0]

    def forbidden(tool: str, request: Path, output: Path) -> dict[str, object]:
        pytest.fail("Stale face IDs reached the geometry tool")

    monkeypatch.setattr(parts, "run_tool", forbidden)
    with pytest.raises(ValueError, match="STALE_PART_FACES"):
        parts.rebuild({**snapshot_result(snapshot), "parts": [{"id": first["id"], "faces": [999]}]}, tmp_path / "out")


def prepare_mock_views(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[dict[str, object], list[str]]:
    snapshot = prepared(tmp_path)
    calls: list[str] = []

    def fake(tool: str, request: Path, output: Path) -> dict[str, object]:
        calls.append(tool)
        output.mkdir()
        record = read_object(request)
        if tool == "uv.pack":
            return {"status": "OK", **snapshot_result(snapshot)}
        if tool == "material.target":
            target = output / "target.json"
            write_json(target, {"target_hash": "frozen-target"})
            return {"status": "OK", "target": str(target), "target_hash": "frozen-target"}
        if tool == "view.render":
            image = output / "front.png"
            Image.new("RGBA", (4, 4), (50, 100, 150, 255)).save(image)
            report = output / "views.json"
            views = {"images": {"front": str(image)}, "cameras": record["cameras"]}
            write_json(report, views)
            return {"status": "OK", **views, "report": str(report)}
        return {"status": "OK"}

    class Target:
        def __init__(self, source: Snapshot, path: Path) -> None:
            self.record = read_object(path)

    monkeypatch.setattr(parts, "run_tool", fake)
    monkeypatch.setattr(parts, "ColorTarget", Target)
    output = tmp_path / "prepare"
    output.mkdir()
    result = parts.preview({**snapshot_result(snapshot), "packing_backend": "rectpack",
                            "cameras": [{"name": "front", "azimuth": 0, "elevation": 0}]}, output)
    return result, calls


def test_preview_prepare_never_bakes_or_accepts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    result, calls = prepare_mock_views(tmp_path, monkeypatch)
    assert calls == ["uv.pack", "uv.inspect", "view.render", "material.target", "view.render"]
    assert result["requires_target_review"] is True and result["visual_review"] == "pending"
    preparation = read_object(Path(str(result["preparation"])))
    digest = preparation.pop("preparation_hash")
    assert digest == object_hash(preparation)
    assert preparation["padding_pixels"] == 8


@pytest.mark.parametrize("patch", [
    {}, {"target_hash": "wrong"}, {"reviewed_cameras": []}, {"reviewer": "automatic"}, {"decision": "revert"},
])
def test_preview_bake_requires_actual_bound_target_review(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, patch: dict[str, object],
) -> None:
    prepared_result, calls = prepare_mock_views(tmp_path, monkeypatch)
    review: dict[str, object] = {"target_hash": "frozen-target", "decision": "keep", "reviewer": "agent",
                                 "reviewed_cameras": ["front"], "notes": "Viewed the actual frozen target"}
    request: dict[str, object] = {"stage": "bake", "preparation": prepared_result["preparation"]}
    if patch:
        request["target_review"] = {**review, **patch}
    with pytest.raises(ValueError, match="(target_review|ACTUAL_TARGET_REVIEW)"):
        parts.preview(request, tmp_path / "bake")
    assert "material.bake" not in calls


def test_preview_rejects_modified_prepared_image(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    result, calls = prepare_mock_views(tmp_path, monkeypatch)
    manifest = read_object(Path(str(result["preparation"])))
    image = Path(next(iter(cast(dict[str, str], manifest["images"]))))
    image.write_bytes(b"changed")
    with pytest.raises(ValueError, match="PREPARED_VIEW_CHANGED"):
        parts.preview({"stage": "bake", "preparation": result["preparation"]}, tmp_path / "bake")
    assert "material.bake" not in calls


def collect_fixture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    snapshot = prepared(tmp_path)
    state = snapshot.state.copy()
    state.surface.uv = state.surface.uv * [.731371231, .4705003245] + [.031271237, .264749675]
    snapshot = save_snapshot(state, tmp_path / "fractional-uv", snapshot.metadata, snapshot)
    texture = Image.new("RGBA", (8, 8), (30, 120, 210, 255))
    surface = snapshot.state.surface
    mesh = trimesh.Trimesh(vertices=surface.mesh.vertices[surface.mesh.faces].reshape(-1, 3),
                           faces=np.arange(len(surface.mesh.faces)*3).reshape(-1, 3), process=False)
    mesh.visual = TextureVisuals(uv=surface.uv[surface.uv_faces].reshape(-1, 2),
                                material=PBRMaterial(baseColorTexture=texture))
    model = tmp_path / "asset.glb"
    mesh.export(model, file_type="glb")
    texture_folder = tmp_path / "texture"
    texture_folder.mkdir()
    texture.save(texture_folder / "BaseColor_mip0.png")
    bake = tmp_path / "bake.json"
    write_json(bake, {"scope": "basecolor_reuse", "target_hash": "target"})
    comparison = tmp_path / "comparison.json"
    selected = list(range(len(surface.mesh.faces)))
    comparison_record: dict[str, object] = {
        "images": {}, "after": {**snapshot_result(snapshot), "bake": str(bake), "bake_sha256": file_hash(bake)},
        "selected_faces": selected, "cameras": [{"name": "front"}],
    }
    comparison_record["comparison_hash"] = object_hash(comparison_record)
    write_json(comparison, comparison_record)
    review: dict[str, object] = {"state_hash": snapshot.state_hash, "decision": "keep", "visual_accepted": True,
                                 "reviewer": "human", "whole_surface_views": True, "all_prepared_views_reviewed": True,
                                 "scope": "basecolor_reuse", "bake": str(bake), "bake_sha256": file_hash(bake),
                                 "comparison": str(comparison), "comparison_sha256": file_hash(comparison)}
    review.update(selected_faces=selected, reviewed_cameras=["front"])
    review["review_hash"] = object_hash(review)
    review_path = tmp_path / "review.json"
    write_json(review_path, review)
    export = tmp_path / "export.json"
    write_json(export, {"state_hash": snapshot.state_hash, "scope": "basecolor_reuse", "target_hash": "target",
                        "files": {"asset.glb": file_hash(model), "bake.json": file_hash(bake)}})
    parts_path = tmp_path / "parts.json"
    write_json(parts_path, {"parts": snapshot.metadata["parts"]})

    def load(source: Snapshot, path: Path) -> tuple[dict[str, object], None, list[np.ndarray]]:
        assert source.state_hash == snapshot.state_hash and path == bake
        return read_object(path), None, [np.asarray(texture)]

    monkeypatch.setattr(parts, "_load", load)
    return {**snapshot_result(snapshot), "model": str(model), "export_manifest": str(export),
            "parts": str(parts_path), "bake": str(bake), "review": str(review_path)}


def test_collect_real_named_glb_preserves_review_identity(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    request = collect_fixture(tmp_path, monkeypatch)
    output = tmp_path / "collected"
    output.mkdir()
    result = parts.collect(request, output)
    assert result["named_objects"] == 2
    assert result["reviewer"] == "human" and result["human_reviewed"] is True
    assert result["numeric_accepted"] is False and result["full_pbr_passed"] is False
    assert result["part_positions_uv_and_textures_reimported_exact"] is True
    assert result["corner_uv_gltf_roundtrip_exact"] is True
    assert (output / "model-parts.glb").is_file()
    assert (output / "texture/BaseColor_mip0.png").is_file()


def test_glb_fractional_uv_follows_exact_flip_quantization_contract(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = collect_fixture(tmp_path, monkeypatch)
    snapshot = load_snapshot(Path(str(request["snapshot"])))
    model = trimesh.load(Path(str(request["model"])), force="mesh", process=False)
    assert isinstance(model, trimesh.Trimesh) and isinstance(model.visual, TextureVisuals)
    actual = np.asarray(model.visual.uv)[model.faces]
    source = snapshot.state.surface.uv[snapshot.state.surface.uv_faces]
    assert not np.array_equal(actual, source.astype(np.float32).astype(float))
    np.testing.assert_array_equal(actual, parts._gltf_roundtrip_uv(source))
    texture = np.asarray(Image.new("RGBA", (8, 8), (30, 120, 210, 255)))
    assert parts._reimport(Path(str(request["model"])), snapshot, texture) == 4
    model.visual.uv[0, 1] += 1e-5
    changed = tmp_path / "wrong-uv.glb"
    model.export(changed, file_type="glb")
    with pytest.raises(AssertionError):
        parts._reimport(changed, snapshot, texture)


def test_collect_accepts_actual_artist_compare_and_review_chain(tmp_path: Path) -> None:
    snapshot = prepared(tmp_path)
    deadline = time.monotonic()+60
    profile: dict[str, object] = {"resolution": 64, "gap_pixels": 2, "border_pixels": 2,
                                   "quality_constraints": False}
    pack_folder = tmp_path / "packed"
    pack_folder.mkdir()
    packed = pack_uv({**snapshot_result(snapshot), "backend": "rectangles", "profile": profile,
                      "acceptance_policy": "visual"}, pack_folder, deadline)
    current = load_snapshot(Path(str(packed["snapshot"])))
    target_folder = tmp_path / "target"
    target_folder.mkdir()
    target = freeze_target({**snapshot_result(current), "geometry_profile": profile,
                            "acceptance_policy": "visual"}, target_folder, deadline)
    bake_folder = tmp_path / "baked"
    bake_folder.mkdir()
    baked = bake_color_reuse({**snapshot_result(current), "target": target["target"],
                              "resolution": 64, "padding_pixels": 2, "acceptance_policy": "visual"},
                             bake_folder, deadline)
    compare_folder = tmp_path / "comparison"
    compare_folder.mkdir()
    comparison = compare_views({**snapshot_result(current), "bake": baked["bake"],
                                "before": {**snapshot_result(current), "bake": baked["bake"]}, "size": 64,
                                "cameras": [{"name": "front", "azimuth": 0, "elevation": 0}]},
                               compare_folder, deadline)
    review_folder = tmp_path / "review"
    review_folder.mkdir()
    reviewed = review_views({**snapshot_result(current), "comparison": comparison["comparison"],
                             "decision": "keep", "reviewer": "agent", "reviewed_cameras": ["front"],
                             "notes": "Test fixture records its constructed comparison views."},
                            review_folder, deadline)
    assert reviewed["scope"] != "basecolor_reuse"
    exported_folder = tmp_path / "exported"
    exported_folder.mkdir()
    exported = export_color_reuse({**snapshot_result(current), "bake": baked["bake"], "mode": "diagnostic"},
                                  exported_folder, deadline)
    parts_path = tmp_path / "parts.json"
    write_json(parts_path, {"parts": current.metadata["parts"]})
    collected_folder = tmp_path / "collected"
    collected_folder.mkdir()
    result = parts.collect({**snapshot_result(current), "model": exported["asset"],
                            "export_manifest": exported["manifest"], "parts": str(parts_path),
                            "bake": baked["bake"], "review": reviewed["review"]}, collected_folder)
    assert result["visual_accepted"] is True and result["reviewer"] == "agent"
    assert result["scope"] == "basecolor_reuse" and result["review_scope"] == reviewed["scope"]
    assert result["numeric_accepted"] is False and result["full_pbr_passed"] is False


def test_collect_rejects_changed_model(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    request = collect_fixture(tmp_path, monkeypatch)
    model = Path(str(request["model"]))
    model.write_bytes(model.read_bytes()+b"changed")
    with pytest.raises(ValueError, match="MODEL_EXPORT_BINDING_CHANGED"):
        parts.collect(request, tmp_path / "output")


def test_collect_rejects_stale_parts_mapping(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    request = collect_fixture(tmp_path, monkeypatch)
    path = Path(str(request["parts"]))
    record = read_object(path)
    cast(list[dict[str, object]], record["parts"])[0]["faces"] = [999]
    write_json(path, record)
    with pytest.raises(ValueError, match="STALE_PARTS_MAPPING"):
        parts.collect(request, tmp_path / "output")
