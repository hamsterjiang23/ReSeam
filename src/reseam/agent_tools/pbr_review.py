"""Matched Blender PBR previews; never substitute a BaseColor acceptance receipt."""

import html
import shutil
import time
from pathlib import Path
from typing import cast

import numpy as np
from PIL import Image

from ..runtime import read_runtime, run_process
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


def comparison_scene(
    request: dict[str, object], output: Path, reports: dict[str, dict[str, object]], deadline: float,
) -> dict[str, object]:
    """Use verified preview receipts and recheck actual geometry inside Blender."""
    labels = {"before": "noshare", "after": "shared"}
    bindings: dict[str, dict[str, object]] = {}
    for role, report in reports.items():
        bindings[labels[role]] = {"model": report["model"], "model_sha256": report["model_sha256"],
            "expected_geometry": str(output / role / "expected_geometry.npz")}
    references = request.get("reference_previews", {})
    if not isinstance(references, dict):
        raise TypeError("REFERENCE_PREVIEWS_MAPPING_REQUIRED")
    for label, raw in references.items():
        if not isinstance(label, str) or not label or label in bindings or not isinstance(raw, str):
            raise ValueError("COMPARISON_REFERENCE_LABEL_OR_PATH_INVALID")
        receipt = Path(raw).resolve()
        record = read_object(receipt)
        if record.get("geometry_and_uv_reimport_match") is not True:
            raise ValueError("COMPARISON_REFERENCE_PREVIEW_NOT_VERIFIED")
        model = Path(str(record["model"]))
        if file_hash(model) != record.get("model_sha256"):
            raise ValueError("COMPARISON_REFERENCE_MODEL_CHANGED")
        binding = receipt.parent / "expected_geometry.npz"
        if not binding.is_file():
            raise ValueError("COMPARISON_REFERENCE_GEOMETRY_MISSING")
        bindings[label] = {"model": str(model), "model_sha256": record["model_sha256"],
            "expected_geometry": str(binding), "preview": str(receipt), "preview_sha256": file_hash(receipt)}
    root = Path(__file__).resolve().parents[3]
    blender = str(request.get("blender", read_runtime(root)["blender"]))
    worker = root / "scripts/pbr_comparison_scene_worker.py"
    config = output / "comparison-scene-request.json"
    write_json(config, {"models": bindings, "output": str(output), "default_visible": "shared",
                       "size": request.get("size", 512)})
    run_process([blender, "--background", "--factory-startup", "--threads", "4", "--python-exit-code", "1",
        "--python", str(worker), "--", str(config)], output / "scene_worker", deadline - time.monotonic())
    audit = read_object(output / "scene_audit.json")
    audit.update(worker_sha256=file_hash(worker), models_bound=bindings,
                 scene_sha256=file_hash(output / "comparison.blend"))
    write_json(output / "scene_audit.json", audit)
    return audit


def compare_pbr(request: dict[str, object], output: Path, deadline: float) -> dict[str, object]:
    from .artist_review import preview

    before_request = request.get("before")
    if not isinstance(before_request, dict):
        raise TypeError("PBR_BEFORE_SNAPSHOT_AND_MODEL_REQUIRED")
    before = require_snapshot(cast(dict[str, object], before_request))
    after = require_snapshot(request)
    a, b = before.state.surface.mesh, after.state.surface.mesh
    if not np.array_equal(a.vertices, b.vertices) or not np.array_equal(a.faces, b.faces):
        raise ValueError("COMPARISON_REQUIRES_SAME_GEOMETRY_AND_FACE_IDS")
    bindings = {"before": _export_binding(cast(dict[str, object], before_request), deadline),
                "after": _export_binding(request, deadline)}
    existing = request.get("existing_comparison")
    if existing is not None:
        if not isinstance(existing, str):
            raise TypeError("PBR_EXISTING_COMPARISON_PATH_REQUIRED")
        old_path = Path(existing).resolve()
        old = read_object(old_path)
        if old.get("mode") != "pbr":
            raise ValueError("PBR_EXISTING_COMPARISON_REQUIRED")
        previews = {role: Path(str(cast(dict[str, object], old[role])["preview"]))
                    for role in ("before", "after")}
        result = _freeze_comparison(bindings, previews, output, deadline)
        result["existing_comparison"] = str(old_path)
        result["existing_comparison_sha256"] = file_hash(old_path)
        result["freeze_note"] = "Explicit current-file evidence freeze; historical image immutability before this freeze is not claimed."
        return _save_comparison(result, output)
    common = {key: request[key] for key in ("size", "cameras", "lights", "distances", "channels", "blender", "focus_faces", "save_blend") if key in request}
    reports: dict[str, dict[str, object]] = {}
    for label, binding in (("before", before_request), ("after", request)):
        reports[label] = preview({**binding, **common}, output / label, deadline)
    result: dict[str, object] = {"mode": "pbr", "before": reports["before"], "after": reports["after"],
        "visual_decision": "pending", "human_acceptance": "not_run", "engine_acceptance": "not_run",
        "note": "Matched actual GLB materials; no numerical or visual acceptance inferred."}
    if request.get("save_blend"):
        result["comparison_scene"] = comparison_scene(request, output, reports, deadline)
    cards = []
    for label, report in reports.items():
        cards.append(f'<section><h2>{label}</h2>')
        for item in cast(list[dict[str, object]], report["images"]):
            path = Path(str(item["path"])).relative_to(output).as_posix()
            cards.append(f'<figure><img src="{html.escape(path)}"><figcaption>{html.escape(Path(path).name)}</figcaption></figure>')
        cards.append('</section>')
    (output / "comparison.html").write_text('<!doctype html><meta charset="utf-8"><title>PBR 对照</title><style>body{font:16px sans-serif}main{display:flex}section{width:50%}img{max-width:95%}</style><main>' + ''.join(cards) + '</main>', encoding="utf-8")
    result.update(_freeze_comparison(bindings, {role: output / role / "preview.json" for role in reports}, output, deadline))
    return {"viewer": str(output / "comparison.html"), **_save_comparison(result, output)}


def _file(value: object, label: str) -> Path:
    if not isinstance(value, str) or not value:
        raise ValueError("PBR_REVIEW_PATH_REQUIRED:" + label)
    path = Path(value).resolve()
    if not path.is_file():
        raise ValueError("PBR_REVIEW_FILE_MISSING:" + label)
    return path


def _export_binding(request: dict[str, object], deadline: float) -> dict[str, object]:
    from .material import _load_bake

    check_deadline(deadline)
    snapshot = require_snapshot(request)
    bake_path = _file(request.get("bake"), "bake")
    atlas, bake = _load_bake(snapshot, bake_path)
    if bake.get("scope") != "pbr_reuse":
        raise ValueError("PBR_REVIEW_REQUIRES_FULL_PBR_REUSE_BAKE")
    manifest_path = _file(request.get("export_manifest"), "export_manifest")
    manifest = read_object(manifest_path)
    model = _file(request.get("model"), "model")
    if (manifest.get("state_hash") != snapshot.state_hash
            or manifest.get("bake_sha256") != file_hash(bake_path)
            or model != manifest_path.parent / "asset.glb"):
        raise ValueError("PBR_EXPORT_STATE_BAKE_OR_MODEL_MISMATCH")
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, dict) or not artifacts:
        raise ValueError("PBR_EXPORT_HASH_MANIFEST_REQUIRED")
    verified: dict[str, str] = {}
    for relative, digest in artifacts.items():
        check_deadline(deadline)
        relative_path = Path(str(relative))
        path = (manifest_path.parent / relative_path).resolve()
        if (not isinstance(relative, str) or not isinstance(digest, str) or relative_path.is_absolute()
                or relative_path.drive or ".." in relative_path.parts
                or not path.is_relative_to(manifest_path.parent) or path == manifest_path.parent):
            raise ValueError("PBR_EXPORT_UNSAFE_ARTIFACT_PATH")
        if file_hash(path) != digest:
            raise ValueError("PBR_EXPORT_ARTIFACT_CHANGED:" + relative)
        verified[relative] = digest
    if verified.get("asset.glb") != file_hash(model) or verified.get("bake.json") != file_hash(bake_path):
        raise ValueError("PBR_EXPORT_MODEL_OR_BAKE_UNBOUND")
    for name, levels in (("BaseColor", atlas.base_color), ("Normal", atlas.normal), ("ORM", atlas.orm)):
        for mip, level in enumerate(levels):
            relative = f"texture/{name}_mip{mip}.png"
            if relative not in verified or cast(dict[str, object], bake["files"]).get(relative) != verified[relative]:
                raise ValueError("PBR_EXPORT_COMPLETE_MIPS_REQUIRED")
            with Image.open(manifest_path.parent / relative) as image:
                if image.size != (level.shape[1], level.shape[0]) or image.mode != "RGBA":
                    raise ValueError("PBR_EXPORT_MIP_DIMENSIONS_CHANGED")
    return {**snapshot_result(snapshot), "model": str(model), "model_sha256": file_hash(model),
        "bake": str(bake_path), "bake_sha256": file_hash(bake_path),
        "export_manifest": str(manifest_path), "export_manifest_sha256": file_hash(manifest_path),
        "artifacts": verified, "payload_bytes": bake["payload_bytes"],
        "numerical_quality_accepted": manifest.get("quality_accepted") is True,
        "export_mode": manifest.get("mode")}


def _preview_evidence(path: Path, binding: dict[str, object]) -> dict[str, object]:
    record = read_object(path)
    if (record.get("geometry_and_uv_reimport_match") is not True
            or record.get("state_hash") != binding["state_hash"]
            or record.get("model_sha256") != binding["model_sha256"]
            or Path(str(record.get("model"))).resolve() != Path(str(binding["model"]))):
        raise ValueError("PBR_PREVIEW_STATE_MODEL_OR_GEOMETRY_MISMATCH")
    config_path = path.parent / "render-request.json"
    config = read_object(config_path)
    snapshot = require_snapshot(binding)
    geometry_path = _file(config.get("expected_geometry"), "expected_geometry")
    surface = snapshot.state.surface
    with np.load(geometry_path, allow_pickle=False) as actual:
        if (not np.array_equal(actual["vertices"], surface.mesh.vertices)
                or not np.array_equal(actual["faces"], surface.mesh.faces)
                or not np.array_equal(actual["corner_uv"], surface.uv[surface.uv_faces])):
            raise ValueError("PBR_PREVIEW_EXPECTED_GEOMETRY_CHANGED")
    if Path(str(config.get("model"))).resolve() != Path(str(binding["model"])):
        raise ValueError("PBR_PREVIEW_RENDER_MODEL_CHANGED")
    channels = config.get("channels")
    if not isinstance(channels, list) or not {"material", "normal_lit"}.issubset(channels):
        raise ValueError("PBR_MATERIAL_AND_NORMAL_LIT_VIEWS_REQUIRED")
    images = record.get("images")
    if not isinstance(images, list) or not images:
        raise ValueError("PBR_PREVIEW_IMAGES_REQUIRED")
    cameras = config.get("cameras")
    if (not isinstance(cameras, list) or not cameras or any(
            not isinstance(camera, list) or len(camera) != 3 or not isinstance(camera[0], str)
            or any(type(value) not in (int, float) or not np.isfinite(value) for value in camera[1:])
            for camera in cameras)):
        raise ValueError("PBR_CANONICAL_RENDER_CAMERA_TRIPLES_REQUIRED")
    prepared = {f"{channel}|{camera[0]}|{light}|{float(distance):g}"
        for channel in channels for camera in cameras
        for light in cast(list[str], config["lights"]) for distance in cast(list[float], config["distances"])}
    evidence: dict[str, dict[str, object]] = {}
    for item in cast(list[dict[str, object]], images):
        key = f"{item['channel']}|{item['camera']}|{item['light']}|{float(cast(float, item['distance'])):g}"
        if key in evidence or key not in prepared:
            raise ValueError("PBR_DUPLICATE_OR_UNDECLARED_PREVIEW_VIEW")
        image_path = _file(item.get("path"), "image")
        if not image_path.is_relative_to(path.parent):
            raise ValueError("PBR_PREVIEW_IMAGE_OUTSIDE_RECEIPT")
        with Image.open(image_path) as image:
            if image.size != (config["size"], config["size"]):
                raise ValueError("PBR_PREVIEW_IMAGE_SIZE_MISMATCH")
        evidence[key] = {"path": str(image_path), "sha256": file_hash(image_path),
                         "channel": item["channel"], "camera": item["camera"], "light": item["light"]}
    if set(evidence) != prepared:
        raise ValueError("PBR_PREVIEW_INCOMPLETE_VIEWS")
    common = {key: config.get(key) for key in ("size", "cameras", "lights", "distances", "channels", "focus_points")}
    return {"preview": str(path), "preview_sha256": file_hash(path),
        "render_config": str(config_path), "render_config_sha256": file_hash(config_path),
        "expected_geometry": str(geometry_path), "expected_geometry_sha256": file_hash(geometry_path),
        "settings": common, "views": evidence, "whole_surface_views": not bool(config.get("focus_points"))}


def _freeze_comparison(bindings: dict[str, dict[str, object]], previews: dict[str, Path],
                       output: Path, deadline: float) -> dict[str, object]:
    del output
    check_deadline(deadline)
    for binding in bindings.values():
        if _export_binding(binding, deadline) != binding:
            raise ValueError("PBR_EXPORT_CHANGED_DURING_COMPARISON")
    evidence = {role: _preview_evidence(previews[role].resolve(), binding) for role, binding in bindings.items()}
    if evidence["before"]["settings"] != evidence["after"]["settings"]:
        raise ValueError("PBR_COMPARISON_VIEW_SETTINGS_DIFFER")
    return {"mode": "pbr", "scope": "frozen_actual_full_pbr_comparison_v1", "before": bindings["before"],
        "after": bindings["after"], "evidence": evidence, "visual_decision": "pending",
        "whole_surface_views": all(item["whole_surface_views"] for item in evidence.values()),
        "human_acceptance": "not_run", "engine_acceptance": "not_run", "numerical_acceptance": "separate_unchanged"}


def _save_comparison(record: dict[str, object], output: Path) -> dict[str, object]:
    record["comparison_hash"] = object_hash(record)
    write_json(output / "comparison.json", record)
    return {"comparison": str((output / "comparison.json").resolve()), **record}


def _verified_comparison(path: Path, snapshot: Snapshot, deadline: float) -> dict[str, object]:
    record = read_object(path)
    digest = record.pop("comparison_hash", None)
    if digest != object_hash(record) or record.get("scope") != "frozen_actual_full_pbr_comparison_v1":
        raise ValueError("PBR_FROZEN_COMPARISON_HASH_REQUIRED")
    if cast(dict[str, object], record["after"])["state_hash"] != snapshot.state_hash:
        raise ValueError("STALE_PBR_VISUAL_REVIEW")
    evidence = cast(dict[str, dict[str, object]], record["evidence"])
    for role in ("before", "after"):
        bound = cast(dict[str, object], record[role])
        if _export_binding(bound, deadline) != bound:
            raise ValueError("PBR_REVIEW_EXPORT_BINDING_CHANGED")
        current = _preview_evidence(Path(str(evidence[role]["preview"])), bound)
        if current != evidence[role]:
            raise ValueError("PBR_REVIEW_IMAGE_OR_PREVIEW_CHANGED")
    return record


def review_pbr(request: dict[str, object], output: Path, deadline: float) -> dict[str, object]:
    snapshot = require_snapshot(request)
    path = _file(request.get("comparison"), "comparison")
    comparison = _verified_comparison(path, snapshot, deadline)
    decision, reviewer, observer = request.get("decision"), request.get("reviewer"), request.get("observer")
    if decision not in ("keep", "revert") or reviewer not in ("agent", "human") or not isinstance(observer, str) or not observer.strip():
        raise ValueError("PBR_EXPLICIT_DECISION_REVIEWER_OBSERVER_REQUIRED")
    seen, notes = request.get("reviewed_views"), request.get("notes")
    evidence = cast(dict[str, dict[str, object]], comparison["evidence"])
    views = cast(dict[str, dict[str, object]], evidence["after"]["views"])
    if (not isinstance(seen, list) or not seen or any(not isinstance(v, str) for v in seen)
            or len(set(seen)) != len(seen) or not set(seen) <= set(views)
            or not isinstance(notes, str) or not notes.strip()):
        raise ValueError("PBR_ACTUALLY_REVIEWED_VIEW_IDS_AND_NOTES_REQUIRED")
    if not {"material", "normal_lit"}.issubset({views[v]["channel"] for v in seen}):
        raise ValueError("PBR_REVIEW_REQUIRES_MATERIAL_AND_NORMAL_LIT")
    receipt: dict[str, object] = {"scope": "full_pbr_visual_review_v1", "state_hash": snapshot.state_hash,
        "comparison": str(path), "comparison_sha256": file_hash(path), "decision": decision,
        "reviewer": reviewer, "observer": observer, "reviewed_views": seen, "notes": notes,
        "all_prepared_views_reviewed": set(seen) == set(views),
        "whole_surface_views": comparison["whole_surface_views"], "visual_accepted": decision == "keep",
        "numerical_acceptance": "separate_unchanged", "human_acceptance": decision if reviewer == "human" else "not_run",
        "game_shader_acceptance": "not_run", "status": "VISUAL_KEEP" if decision == "keep" else "VISUAL_REVERT"}
    if decision == "revert":
        receipt["restore"] = comparison["before"]
        receipt["restore_scope"] = "Verified parent selection receipt; immutable snapshots are not modified."
    receipt["review_hash"] = object_hash(receipt)
    write_json(output / "review.json", receipt)
    return {"review": str((output / "review.json").resolve()), **receipt, "execution_success": True}


def export_pbr_visual(request: dict[str, object], output: Path, deadline: float) -> dict[str, object]:
    snapshot = require_snapshot(request)
    path = _file(request.get("review"), "review")
    review_sha256 = file_hash(path)
    receipt = read_object(path)
    digest = receipt.pop("review_hash", None)
    if (digest != object_hash(receipt) or receipt.get("scope") != "full_pbr_visual_review_v1"
            or receipt.get("state_hash") != snapshot.state_hash):
        raise ValueError("PBR_STALE_OR_CHANGED_REVIEW")
    comparison_path = _file(receipt.get("comparison"), "comparison")
    if file_hash(comparison_path) != receipt["comparison_sha256"]:
        raise ValueError("PBR_REVIEW_COMPARISON_CHANGED")
    comparison = _verified_comparison(comparison_path, snapshot, deadline)
    if receipt.get("decision") == "revert":
        refusal = {"status": "REJECTED", "reason": "PBR_VISUAL_REVERT", "visual_accepted": False,
                   "restore": comparison["before"], "review": str(path)}
        write_json(output / "visual-export.json", refusal)
        return refusal
    if (receipt.get("visual_accepted") is not True or receipt.get("whole_surface_views") is not True
            or receipt.get("all_prepared_views_reviewed") is not True):
        raise ValueError("PBR_KEEP_ALL_WHOLE_SURFACE_VIEWS_BEFORE_EXPORT")
    binding = cast(dict[str, object], comparison["after"])
    source = Path(str(binding["export_manifest"])).parent
    artifacts = cast(dict[str, str], binding["artifacts"])
    copied: dict[str, str] = {}
    for relative, digest_value in artifacts.items():
        check_deadline(deadline)
        destination = (output / relative).resolve()
        if not destination.is_relative_to(output.resolve()) or destination == output.resolve():
            raise ValueError("PBR_VISUAL_EXPORT_UNSAFE_DESTINATION")
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            raise ValueError("PBR_VISUAL_EXPORT_DESTINATION_EXISTS")
        shutil.copy2(source / relative, destination)
        if file_hash(destination) != digest_value or file_hash(source / relative) != digest_value:
            raise ValueError("PBR_VISUAL_COPY_CHANGED")
        copied[relative] = digest_value
    # Preserve the reviewed manifest verbatim; its absolute paths are source provenance.
    shutil.copy2(Path(str(binding["export_manifest"])), output / "export.json")
    if file_hash(output / "export.json") != binding["export_manifest_sha256"]:
        raise ValueError("PBR_VISUAL_MANIFEST_CHANGED")
    _verified_comparison(comparison_path, snapshot, deadline)
    if file_hash(path) != review_sha256:
        raise ValueError("PBR_REVIEW_CHANGED_DURING_EXPORT")
    visual = {"status": "EXPORTED_VISUAL", "scope": "full_pbr_visual_export_v1", "visual_accepted": True,
        "reviewer": receipt["reviewer"], "observer": receipt["observer"], "review": str(path),
        "review_sha256": file_hash(path), "model": str((output / "asset.glb").resolve()),
        "model_sha256": file_hash(output / "asset.glb"), "artifacts": copied,
        "source_export_manifest": binding["export_manifest"], "source_export_manifest_sha256": binding["export_manifest_sha256"],
        "numerical_quality_accepted": binding["numerical_quality_accepted"], "numeric_export_mode": binding["export_mode"],
        "human_acceptance": receipt["human_acceptance"], "game_shader_acceptance": "not_run",
        "copy_policy": "Exact reviewed GLB and complete exported mip sidecars; no rebake or regeneration."}
    write_json(output / "visual-export.json", visual)
    return {**visual, "execution_success": True}
