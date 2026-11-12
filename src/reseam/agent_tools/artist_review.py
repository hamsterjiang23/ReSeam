"""Actual texture comparisons, visual decisions and exported-GLB rendering."""

import base64
import html
import json
import time
from pathlib import Path
from typing import cast

import numpy as np
from PIL import Image

from ..runtime import read_runtime, run_process
from .artist import basic_check, member_faces
from .color_reuse import _load, export_color_reuse
from .state import (
    Snapshot,
    check_deadline,
    file_hash,
    object_hash,
    read_object,
    require_snapshot,
    snapshot_result,
    write_json,
)
from .views import render_views


def _uri(path: Path) -> str:
    return "data:image/png;base64," + base64.b64encode(path.read_bytes()).decode("ascii")


def _bound_bake(snapshot: Snapshot, value: object) -> tuple[Path, dict[str, object]]:
    if not isinstance(value, str):
        raise TypeError("ACTUAL_BAKE_PATH_REQUIRED")
    path = Path(value).resolve()
    record, _, _ = _load(snapshot, path)
    return path, record


def compare(request: dict[str, object], output: Path, deadline: float) -> dict[str, object]:
    if request.get("mode") == "pbr":
        from .pbr_review import compare_pbr
        return compare_pbr(request, output, deadline)
    after = require_snapshot(request)
    before_raw = request.get("before")
    if not isinstance(before_raw, dict):
        raise TypeError("BEFORE_SNAPSHOT_AND_BAKE_REQUIRED")
    before = require_snapshot(cast(dict[str, object], before_raw))
    a, b = before.state.surface, after.state.surface
    if not np.array_equal(a.mesh.vertices, b.mesh.vertices) or not np.array_equal(a.mesh.faces, b.mesh.faces):
        raise ValueError("COMPARISON_REQUIRES_SAME_GEOMETRY_AND_FACE_IDS")
    before_path, before_bake = _bound_bake(before, before_raw.get("bake"))
    after_path, after_bake = _bound_bake(after, request.get("bake"))
    size = request.get("size", 640)
    common = {key: request[key] for key in ("cameras", "selected_faces", "region_id") if key in request}
    common.update(mode="basecolor", size=size, annotate=False, seams=False, uv_labels="owners")
    rendered: list[dict[str, object]] = []
    for name, snapshot, bake in (("before", before, before_path), ("after", after, after_path)):
        folder = output / name
        folder.mkdir(parents=True)
        subrequest = {**common, **snapshot_result(snapshot), "texture": str(bake.parent / "texture/BaseColor_mip0.png")}
        write_json(folder / "request.json", subrequest)
        rendered.append(render_views(subrequest, folder, deadline))
    views = read_object(output / "after/views.json")
    cameras = cast(list[dict[str, object]], views["cameras"])
    frames: list[dict[str, object]] = []
    visible_faces: dict[str, list[int]] = {}
    image_files: list[Path] = []
    for camera in cameras:
        check_deadline(deadline)
        name = str(camera["name"])
        pa, pb = output / f"before/{name}.png", output / f"after/{name}.png"
        pixels_a, pixels_b = np.asarray(Image.open(pa).convert("RGB")), np.asarray(Image.open(pb).convert("RGB"))
        delta = np.abs(pixels_a.astype(float) - pixels_b.astype(float)) / 255
        heat = np.zeros_like(pixels_a)
        heat[..., 0] = np.rint(delta.max(axis=2) * 255).astype(np.uint8)
        diff = output / f"{name}-difference.png"
        Image.fromarray(heat).save(diff)
        with np.load(output / f"after/{name}_faces.npz", allow_pickle=False) as raster:
            face_ids = raster["face_ids"]
        visible_faces[name] = np.unique(face_ids[face_ids >= 0]).tolist()
        frames.append({"name": name, "before": _uri(pa), "after": _uri(pb), "difference": _uri(diff),
                       "face_ids": face_ids.ravel().tolist(), "mean_rgb_difference": float(delta.mean()),
                       "max_rgb_difference": float(delta.max())})
        image_files.extend((pa, pb, diff))
    domains = [{"id": g.group_id, "members": [list(m) for m in member_faces(g)]} for g in after.state.groups]
    report: dict[str, object] = {
        "before": {**snapshot_result(before), "bake": str(before_path), "bake_sha256": file_hash(before_path)},
        "after": {**snapshot_result(after), "bake": str(after_path), "bake_sha256": file_hash(after_path)},
        "cameras": cameras, "size": size, "selected_faces": views["selected_faces"],
        "visible_faces": visible_faces,
        "before_counts": basic_check(before), "after_counts": basic_check(after),
        "before_texture_bytes": before_bake["payload_bytes"], "after_texture_bytes": after_bake["payload_bytes"],
        "images": {str(p.resolve()): file_hash(p) for p in image_files},
        "numeric_quality_is_advisory": True, "visual_decision": "pending",
        "scope": "actual baked BaseColor, unlit, matching cameras; separate GLB preview for lighting",
    }
    report["comparison_hash"] = object_hash(report)
    write_json(output / "comparison.json", report)
    # Inline images and mesh/raster arrays make the desk work offline without file:// fetch permissions.
    from ..mesh import chart_labels
    data = {"frames": frames, "size": size, "uv": b.uv[b.uv_faces].tolist(),
            "chart_labels": chart_labels(b).tolist(), "uv_vertices": b.uv.tolist(), "uv_faces": b.uv_faces.tolist(),
            "groups": domains, "report": report, "snapshot": snapshot_result(after),
            "comparison_path": str((output / "comparison.json").resolve()),
            "uv_image": _uri(output / "after/uv.png")}
    data["locks"] = after.metadata.get("artist_locks", [])
    from .artist_web import PAGE
    payload = json.dumps(data, ensure_ascii=False).replace("<", "\\u003c")
    (output / "review.html").write_text(PAGE.replace("__DATA__", payload), encoding="utf-8")
    return {"comparison": str((output / "comparison.json").resolve()),
            "viewer": str((output / "review.html").resolve()), "comparison_hash": report["comparison_hash"],
            "visual_decision": "pending"}


def review(request: dict[str, object], output: Path, deadline: float) -> dict[str, object]:
    if request.get("mode") == "pbr":
        from .pbr_review import review_pbr
        return review_pbr(request, output, deadline)
    check_deadline(deadline)
    snapshot = require_snapshot(request)
    comparison_path = Path(str(request["comparison"])).resolve()
    record = read_object(comparison_path)
    digest = record.pop("comparison_hash", None)
    if digest != object_hash(record):
        raise ValueError("COMPARISON_HASH_MISMATCH")
    after = cast(dict[str, object], record["after"])
    if after["state_hash"] != snapshot.state_hash:
        raise ValueError("STALE_VISUAL_REVIEW")
    bake_path, _ = _bound_bake(snapshot, after["bake"])
    if file_hash(bake_path) != after["bake_sha256"]:
        raise ValueError("REVIEW_BAKE_CHANGED")
    for path, sha in cast(dict[str, str], record["images"]).items():
        if file_hash(Path(path)) != sha:
            raise ValueError("REVIEW_IMAGE_CHANGED")
    decision, reviewer = request.get("decision"), request.get("reviewer")
    if decision not in ("keep", "revert") or reviewer not in ("agent", "human"):
        raise ValueError("VISUAL_REVIEW_REQUIRES_KEEP_OR_REVERT_AND_AGENT_OR_HUMAN")
    notes = request.get("notes")
    seen = request.get("reviewed_cameras")
    cameras = {str(c["name"]) for c in cast(list[dict[str, object]], record["cameras"])}
    if not isinstance(notes, str) or not notes.strip() or not isinstance(seen, list) or not seen or not set(seen) <= cameras:
        raise ValueError("REVIEW_REQUIRES_NOTES_AND_VIEWED_CAMERAS")
    selected = cast(list[int], record["selected_faces"])
    all_surface = len(selected) == len(snapshot.state.surface.mesh.faces)
    visible = cast(dict[str, list[int]], record.get("visible_faces", {}))
    reviewed_faces = sorted({face for camera in seen for face in visible.get(str(camera), [])})
    receipt = {"state_hash": snapshot.state_hash, "comparison": str(comparison_path),
               "comparison_sha256": file_hash(comparison_path), "bake": str(bake_path),
               "bake_sha256": file_hash(bake_path), "decision": decision, "reviewer": reviewer,
               "notes": notes, "reviewed_cameras": seen, "reviewed_faces": reviewed_faces,
               "selected_faces": selected, "all_prepared_views_reviewed": set(seen) == cameras,
               "whole_surface_views": all_surface, "visual_accepted": decision == "keep",
               "numerical_acceptance": "not_used", "scope": record["scope"]}
    receipt["review_hash"] = object_hash(receipt)
    write_json(output / "review.json", receipt)
    result: dict[str, object] = {"review": str((output / "review.json").resolve()), **receipt}
    if decision == "revert":
        result["restore"] = record["before"]
    return result


def export_visual(request: dict[str, object], output: Path, deadline: float) -> dict[str, object]:
    if request.get("mode") == "pbr":
        from .pbr_review import export_pbr_visual
        return export_pbr_visual(request, output, deadline)
    snapshot = require_snapshot(request)
    path = Path(str(request["review"]))
    receipt = read_object(path)
    digest = receipt.pop("review_hash", None)
    if digest != object_hash(receipt) or receipt.get("state_hash") != snapshot.state_hash:
        raise ValueError("STALE_OR_CHANGED_VISUAL_REVIEW")
    if (receipt.get("visual_accepted") is not True or not receipt.get("whole_surface_views")
            or receipt.get("all_prepared_views_reviewed") is not True):
        raise ValueError("KEEP_WHOLE_SURFACE_COMPARISON_BEFORE_EXPORT")
    comparison_path = Path(str(receipt["comparison"]))
    if file_hash(comparison_path) != receipt["comparison_sha256"]:
        raise ValueError("REVIEW_COMPARISON_CHANGED")
    comparison = read_object(comparison_path)
    for image_path, sha in cast(dict[str, str], comparison["images"]).items():
        if file_hash(Path(image_path)) != sha:
            raise ValueError("REVIEW_IMAGE_CHANGED")
    bake_path, _ = _bound_bake(snapshot, receipt["bake"])
    if file_hash(bake_path) != receipt["bake_sha256"]:
        raise ValueError("REVIEW_BAKE_CHANGED")
    result = export_color_reuse({**snapshot_result(snapshot), "bake": str(bake_path), "mode": "diagnostic"},
                                output, deadline)
    # Keep the historical numeric receipt intact, add a distinct visual delivery receipt.
    visual = {"acceptance": "visual", "visual_accepted": True, "reviewer": receipt["reviewer"],
              "scope": receipt["scope"], "model": str((output / "asset.glb").resolve()),
              "model_sha256": file_hash(output / "asset.glb"), "review": str(path.resolve()),
              "review_sha256": file_hash(path), "numeric_result": result}
    write_json(output / "visual-export.json", visual)
    return visual


def preview(request: dict[str, object], output: Path, deadline: float) -> dict[str, object]:
    check_deadline(deadline)
    snapshot = require_snapshot(request)
    path = Path(str(request["model"])).resolve()
    if path.suffix.lower() != ".glb" or not path.is_file():
        raise ValueError("EXPORTED_GLB_REQUIRED")
    size = request.get("size", 512)
    if type(size) is not int or not 64 <= size <= 2048:
        raise ValueError("PREVIEW_SIZE_64_TO_2048")
    lights, distances = request.get("lights", ["studio", "side"]), request.get("distances", [1., 2.])
    if not isinstance(lights, list) or not lights or any(v not in ("studio", "side", "soft") for v in lights):
        raise ValueError("LIGHTS_STUDIO_SIDE_SOFT")
    if (not isinstance(distances, list) or not 1 <= len(distances) <= 3 or any(
            type(v) not in (float, int) or not np.isfinite(v) or not 1 <= v <= 5 for v in distances)):
        raise ValueError("DISTANCES_1_TO_5_UP_TO_THREE")
    from .views import _cameras
    cameras = _cameras(request)
    channels = request.get("channels", ["material"])
    if not isinstance(channels, list) or not channels or any(
            c not in ("material", "basecolor", "roughness", "metallic", "normal", "normal_lit") for c in channels):
        raise ValueError("UNKNOWN_PREVIEW_CHANNEL")
    if len(cameras)*len(lights)*len(distances)*len(channels) > 48:
        raise ValueError("PREVIEW_MAXIMUM_48_IMAGES")
    root = Path(__file__).resolve().parents[3]
    blender = Path(str(request.get("blender", read_runtime(root)["blender"])))
    worker = root / "scripts/artist_render_worker.py"
    binding = output / "expected_geometry.npz"
    output.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(binding, vertices=snapshot.state.surface.mesh.vertices,
                        faces=snapshot.state.surface.mesh.faces,
                        corner_uv=snapshot.state.surface.uv[snapshot.state.surface.uv_faces])
    focus_faces = request.get("focus_faces", [])
    if not isinstance(focus_faces, list) or any(type(v) is not int or not 0 <= v < len(snapshot.state.surface.mesh.faces) for v in focus_faces):
        raise ValueError("FOCUS_FACES_REQUIRE_VALID_FACE_IDS")
    focus_points = snapshot.state.surface.mesh.vertices[snapshot.state.surface.mesh.faces[focus_faces]].reshape(-1, 3)
    config = {"model": str(path), "output": str(output.resolve()), "size": size,
              "lights": lights, "distances": distances, "cameras": cameras, "expected_geometry": str(binding),
              "channels": channels, "save_blend": bool(request.get("save_blend", False)),
              "focus_points": focus_points.tolist()}
    write_json(output / "render-request.json", config)
    run_process([str(blender), "--background", "--factory-startup", "--threads", "4", "--python-exit-code", "1", "--python", str(worker), "--",
                 str((output / "render-request.json").resolve())], output / "blender", deadline - time.monotonic())
    report = read_object(output / "rendered.json")
    report.update(state_hash=snapshot.state_hash, model_sha256=file_hash(path), model=str(path),
                  renderer="Blender Cycles CPU", worker_sha256=file_hash(worker),
                  triangle_matcher_sha256=file_hash(root / "scripts/triangle_identity_match.py"),
                  numerical_acceptance="not_used", visual_decision="pending")
    write_json(output / "preview.json", report)
    items = cast(list[dict[str, object]], report["images"])
    cards = "".join(f'<figure><img src="{_uri(Path(str(item["path"])))}"><figcaption>'
                    f'{html.escape(str(item["channel"]))} · {html.escape(str(item["camera"]))} · {html.escape(str(item["light"]))} · '
                    f'{item["distance"]}×距离</figcaption></figure>' for item in items)
    page = '<!doctype html><meta charset="utf-8"><title>实际导出模型预览</title><style>' \
           'body{font:16px sans-serif;background:#edf1f5;margin:30px}main{display:flex;flex-wrap:wrap}' \
           'figure{margin:8px;background:white;padding:12px}img{width:320px}</style>' \
           '<h1>实际导出模型 · 不同光照与距离</h1><p>使用 GLB 中实际存在的材质。缺失贴图不补造。</p><main>'
    (output / "preview.html").write_text(page + cards + '</main>', encoding="utf-8")
    return {"preview": str((output / "preview.json").resolve()),
            "viewer": str((output / "preview.html").resolve()), **report}
