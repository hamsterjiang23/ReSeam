"""Reusable part workflows; geometry tools and visual decisions keep their identities."""

from __future__ import annotations

import math
import shutil
from pathlib import Path
from typing import cast

import numpy as np
import trimesh
from PIL import Image
from trimesh.visual.material import PBRMaterial
from trimesh.visual.texture import TextureVisuals

from ..agent_tools.color_reuse import ColorTarget, _load
from ..agent_tools.runner import run_tool
from ..agent_tools.state import (
    Snapshot,
    file_hash,
    load_snapshot,
    object_hash,
    read_object,
    require_snapshot,
    snapshot_result,
    write_json,
)
from ..mesh import DisjointSet, make_mesh
from ..runtime import read_runtime
from ..types import FloatArray

_CAMERAS: list[dict[str, object]] = [
    {"name": name, "azimuth": azimuth, "elevation": elevation}
    for name, azimuth, elevation in (
        ("front", 0, 0), ("back", 180, 0), ("left", 90, 0), ("right", -90, 0),
        ("upper", 30, 45), ("lower", -30, -40),
    )
]
_BACKENDS = {"optcuts", "harmonic", "blender_abf", "blender_lscm", "blender_minimum_stretch"}


def _mapping(value: object, label: str) -> dict[str, object]:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise ValueError(f"WORKFLOW_OBJECT_REQUIRED:{label}")
    return cast(dict[str, object], value)


def _objects(value: object, label: str) -> list[dict[str, object]]:
    if not isinstance(value, list) or not value:
        raise ValueError(f"WORKFLOW_NONEMPTY_LIST_REQUIRED:{label}")
    return [_mapping(item, label) for item in value]


def _path(value: object, label: str) -> Path:
    if not isinstance(value, str) or not value.strip() or not Path(value).is_absolute():
        raise ValueError(f"WORKFLOW_ABSOLUTE_PATH_REQUIRED:{label}")
    path = Path(value).resolve()
    if not path.is_file():
        raise FileNotFoundError(f"WORKFLOW_FILE_MISSING:{label}:{path}")
    return path


def _bound_snapshot(request: dict[str, object]) -> Snapshot:
    if not isinstance(request.get("state_hash"), str):
        raise TypeError("WORKFLOW_STATE_HASH_REQUIRED")
    _path(request.get("snapshot"), "snapshot")
    return require_snapshot(request)


def _timeout(request: dict[str, object]) -> float:
    value = request.get("timeout_seconds", 300)
    if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value) or value <= 0:
        raise ValueError("WORKFLOW_POSITIVE_TIMEOUT_REQUIRED")
    return float(value)


def _integer(value: object, label: str, low: int, high: int) -> int:
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f"WORKFLOW_INTEGER_RANGE:{label}:{low}:{high}")
    return value


def _blender(request: dict[str, object], required: bool) -> dict[str, object]:
    value = request.get("blender")
    if value is None and required:
        runtime = read_runtime(Path(__file__).resolve().parents[3])
        value = runtime.get("blender")
    if value is None:
        return {}
    return {"blender": str(_path(value, "blender"))}


def _profile(request: dict[str, object], resolution: int) -> dict[str, object]:
    profile = {"resolution": resolution, "gap_pixels": 8, "border_pixels": 8,
               "quality_constraints": False, **_mapping(request.get("profile", {}), "profile")}
    profile["resolution"] = resolution
    return profile


class _Steps:
    def __init__(self, output: Path, timeout: float) -> None:
        self.output = output
        self.timeout = timeout
        self.records: list[dict[str, object]] = []

    def run(self, name: str, tool: str, request: dict[str, object], *, required: bool = True) -> dict[str, object]:
        path = self.output / f"{name}.request.json"
        write_json(path, {"timeout_seconds": self.timeout, **request})
        result = run_tool(tool, path, self.output / name)
        self.records.append({"step": name, "tool": tool, **result})
        write_json(self.output / "progress.json", {"steps": self.records})
        if required and result.get("status") != "OK":
            raise ValueError(f"WORKFLOW_STEP_FAILED:{name}:{result.get('reason', result.get('status'))}")
        return result


def _part_faces(snapshot: Snapshot) -> dict[str, list[int]]:
    records = _objects(snapshot.metadata.get("parts"), "snapshot.parts")
    parts: dict[str, list[int]] = {}
    for record in records:
        identity = record.get("id")
        if not isinstance(identity, str) or not identity or identity in parts:
            raise ValueError("WORKFLOW_PART_IDS_MUST_BE_UNIQUE")
        faces = record.get("faces")
        if (not isinstance(faces, list) or not faces or any(type(face) is not int for face in faces)
                or len(set(faces)) != len(faces)
                or min(faces) < 0 or max(faces) >= len(snapshot.state.surface.mesh.faces)):
            raise ValueError("WORKFLOW_PART_FACES_INVALID")
        parts[identity] = cast(list[int], faces)
    return parts


def _seams(snapshot: Snapshot, faces: list[int], value: object) -> list[list[int]]:
    if not isinstance(value, list):
        raise TypeError("WORKFLOW_SEAM_EDGES_LIST_REQUIRED")
    mesh = snapshot.state.surface.mesh
    edges = set(make_mesh(mesh.vertices, mesh.faces[faces]).edges)
    result: set[tuple[int, int]] = set()
    for item in value:
        if not isinstance(item, list) or len(item) != 2 or any(type(vertex) is not int for vertex in item):
            raise ValueError("WORKFLOW_SEAM_ORIGINAL_VERTEX_PAIR_REQUIRED")
        edge = tuple(sorted(cast(list[int], item)))
        if edge not in edges:
            raise ValueError("WORKFLOW_STALE_OR_OUTSIDE_PART_EDGE")
        result.add(cast(tuple[int, int], edge))
    return [list(edge) for edge in sorted(result)]


def _automatic_openings(snapshot: Snapshot, faces: list[int], options: dict[str, object],
                        steps: _Steps, prefix: str) -> list[list[int]]:
    axis, side = options.get("axis"), options.get("side")
    along = options.get("longitudinal_axis")
    if axis not in ("x", "y", "z") or side not in ("positive", "negative"):
        raise ValueError("WORKFLOW_OPENING_AXIS_AND_SIDE_REQUIRED")
    if along is not None and (along not in ("x", "y", "z") or along == axis):
        raise ValueError("WORKFLOW_OPENING_LONGITUDINAL_AXIS_INVALID")
    mesh = snapshot.state.surface.mesh
    local = make_mesh(mesh.vertices, mesh.faces[faces])
    dsu = DisjointSet(len(faces))
    for adjacent in local.edge_faces:
        if len(adjacent) == 2:
            dsu.union(adjacent[0][0], adjacent[1][0])
    components: dict[int, list[int]] = {}
    for index, face in enumerate(faces):
        components.setdefault(dsu.find(index), []).append(face)
    original_edges = {edge: index for index, edge in enumerate(mesh.edges)}
    cuts: set[tuple[int, int]] = set()
    for index, selected in enumerate(components.values()):
        if len(selected) < 4:
            continue
        patch = make_mesh(mesh.vertices, mesh.faces[selected])
        boundary = [edge for edge, incident in zip(patch.edges, patch.edge_faces, strict=True) if len(incident) == 1]
        choices = boundary or list(patch.edges)
        xyz = mesh.vertices[np.unique(patch.faces)]
        lower, upper = xyz.min(0), xyz.max(0)
        side_axis = ("x", "y", "z").index(str(axis))
        along_axis = (("x", "y", "z").index(str(along)) if along is not None else
                      max((item for item in range(3) if item != side_axis),
                          key=lambda item: float(upper[item]-lower[item])))
        sign = -1 if side == "negative" else 1
        first = min(choices, key=lambda edge: -sign * float(mesh.vertices[list(edge)].mean(0)[side_axis]))
        start = mesh.vertices[list(first)].mean(0)[along_axis]
        end = upper[along_axis] if abs(start-lower[along_axis]) < abs(start-upper[along_axis]) else lower[along_axis]
        second = min(patch.edges, key=lambda edge: abs(float(mesh.vertices[list(edge)].mean(0)[along_axis])-end))
        if first == second:
            continue
        result = steps.run(f"{prefix}_opening_{index}", "seam.select", {
            **snapshot_result(snapshot), "operator": "geodesic",
            "arguments": {"face_ids": selected, "waypoint_edges": [original_edges[first], original_edges[second]]},
        }, required=False)
        if result.get("status") == "OK":
            cuts.update(cast(tuple[int, int], tuple(edge)) for edge in _seams(snapshot, faces, result.get("edges")))
    return [list(edge) for edge in sorted(cuts)]


def rebuild(request: dict[str, object], output: Path) -> dict[str, object]:
    """Rebuild selected current parts; failed trials retain their full parent state."""
    original = _bound_snapshot(request)
    parts = _part_faces(original)
    selected = _objects(request.get("parts", [{"id": identity} for identity in parts]), "parts")
    identities = [part.get("id") for part in selected]
    if any(not isinstance(identity, str) or identity not in parts for identity in identities):
        raise ValueError("WORKFLOW_UNKNOWN_PART_ID")
    if len(set(identities)) != len(identities):
        raise ValueError("WORKFLOW_DUPLICATE_PART_SELECTION")
    plans: list[dict[str, object]] = []
    for part in selected:
        faces = parts[str(part["id"])]
        if "faces" in part and part["faces"] != faces:
            raise ValueError("WORKFLOW_STALE_PART_FACES")
        backend = part.get("backend", request.get("backend", "optcuts"))
        if backend not in _BACKENDS:
            raise ValueError("WORKFLOW_UNKNOWN_UV_BACKEND")
        plans.append({**part, "faces": faces, "backend": backend,
                      "seam_edges": _seams(original, faces, part.get("seam_edges", []))})
    steps = _Steps(output, _timeout(request))
    profile = _profile(request, _integer(request.get("resolution", 2048), "resolution", 4, 8192))
    current = original
    decisions: list[dict[str, object]] = []
    for index, part in enumerate(plans):
        faces = cast(list[int], part["faces"])
        seams = cast(list[list[int]], part["seam_edges"])
        if "auto_opening" in part:
            seams = _seams(current, faces, [*seams, *_automatic_openings(
                current, faces, _mapping(part["auto_opening"], "auto_opening"), steps, f"part_{index}")])
        tool_request = {**snapshot_result(current), "faces": faces, "seam_edges": seams,
                        "backend": part["backend"], "pack_after": False, "initial_uv": "geometry",
                        "acceptance_policy": "visual", "profile": profile,
                        **_blender(request, str(part["backend"]).startswith("blender_"))}
        result = steps.run(f"part_{index}", "uv.reparameterize", tool_request, required=False)
        decision: dict[str, object] = {"id": part["id"], "parent": snapshot_result(current),
                                      "request": tool_request, "result": result, "decision": "fallback_parent"}
        if result.get("status") == "OK":
            child = load_snapshot(_path(result.get("snapshot"), "result.snapshot"))
            if (not np.array_equal(current.state.surface.mesh.vertices, child.state.surface.mesh.vertices)
                    or not np.array_equal(current.state.surface.mesh.faces, child.state.surface.mesh.faces)):
                raise ValueError("WORKFLOW_UV_REBUILD_CHANGED_GEOMETRY")
            report = read_object(_path(result.get("report"), "result.report"))
            decision.update(before=report["old_region_chart_count"], after=report["result_chart_count"],
                            decision="provisional_rebuild_requires_bake_review")
            current = child
        decisions.append(decision)
        write_json(output / "rebuild.json", {**snapshot_result(current), "decisions": decisions,
                   "visual_accepted": False, "requires_pack": True, "requires_rebake": True})
    failed = [str(row["id"]) for row in decisions if row["decision"] == "fallback_parent"]
    result = {"status": "OK", "execution_success": True, **snapshot_result(current), "failed_parts": failed,
              "report": str((output / "rebuild.json").resolve()), "decisions": decisions,
              "requires_pack": True, "requires_rebake": True, "visual_accepted": False}
    write_json(output / "rebuild.json", result)
    return result


def _prepare(request: dict[str, object], output: Path) -> dict[str, object]:
    snapshot = _bound_snapshot(request)
    resolution = _integer(request.get("resolution", 512), "resolution", 4, 8192)
    padding = _integer(request.get("padding_pixels", 8), "padding_pixels", 0, resolution)
    size = _integer(request.get("size", 512), "size", 64, 2048)
    profile = _profile(request, resolution)
    backend = request.get("packing_backend", "blender_concave")
    blender = _blender(request, backend == "blender_concave")
    cameras = _objects(request.get("cameras", _CAMERAS), "cameras")
    steps = _Steps(output, _timeout(request))
    packed = steps.run("pack", "uv.pack", {**snapshot_result(snapshot), "profile": profile, "backend": backend,
                       "packing_seed": request.get("packing_seed", 17), "acceptance_policy": "visual", **blender})
    current = load_snapshot(_path(packed.get("snapshot"), "pack.snapshot"))
    steps.run("inspect", "uv.inspect", {**snapshot_result(current), "profile": profile})
    regions = steps.run("parts", "view.render", {**snapshot_result(current), "mode": "regions", "size": size,
                       "cameras": cameras})
    frozen = steps.run("target", "material.target", {**snapshot_result(current), "geometry_profile": profile,
                       "acceptance_policy": "visual"})
    target = _path(frozen.get("target"), "target")
    target_views = steps.run("target_views", "view.render", {**snapshot_result(current), "mode": "basecolor",
                            "target": str(target), "size": size, "cameras": cameras, "seams": False})
    views = read_object(_path(target_views.get("report"), "target_views.report"))
    actual_cameras = _objects(views.get("cameras"), "render.cameras")
    images = _mapping(views.get("images"), "render.images")
    record: dict[str, object] = {"schema_version": 1, **snapshot_result(current), "profile": profile,
                                "resolution": resolution, "padding_pixels": padding, "size": size,
                                "cameras": actual_cameras, "target": str(target), "target_hash": frozen["target_hash"],
                                "target_sha256": file_hash(target), "target_views": str(output / "target_views/views.json"),
                                "images": {str(path): file_hash(_path(path, "target.image")) for path in images.values()},
                                "parts": regions, "steps": steps.records, "visual_review": "pending",
                                "numeric_quality": "not_validated", "full_pbr_passed": False}
    record["preparation_hash"] = object_hash(record)
    write_json(output / "preparation.json", record)
    return {"status": "OK", "execution_success": True, **snapshot_result(current), "stage": "prepare",
            "preparation": str((output / "preparation.json").resolve()), "target": str(target),
            "target_hash": frozen["target_hash"], "preparation_hash": record["preparation_hash"],
            "visual_review": "pending", "requires_target_review": True, "numeric_quality": "not_validated"}


def _texture_model(model_path: Path) -> tuple[trimesh.Trimesh, TextureVisuals, PBRMaterial]:
    model = trimesh.load(model_path, force="mesh", process=False)
    if not isinstance(model, trimesh.Trimesh):
        raise TypeError("WORKFLOW_TRIANGLE_MODEL_REQUIRED")
    visual = model.visual
    if not isinstance(visual, TextureVisuals) or not isinstance(visual.material, PBRMaterial):
        raise TypeError("WORKFLOW_TEXTURED_MODEL_REQUIRED")
    if visual.uv is None or visual.material.baseColorTexture is None:
        raise ValueError("WORKFLOW_UV_AND_BASECOLOR_REQUIRED")
    return model, visual, visual.material


def _gltf_roundtrip_uv(uv: FloatArray) -> FloatArray:
    """Replay trimesh glTF's V flip, float32 storage, and float32 import flip exactly."""
    encoded = np.asarray(uv, dtype=np.float64).copy()
    encoded[..., 1] = 1.0 - encoded[..., 1]
    stored = encoded.astype(np.float32)
    stored[..., 1] = np.float32(1.0) - stored[..., 1]
    return stored.astype(np.float64)


def _reimport(model_path: Path, snapshot: Snapshot, texture: np.ndarray) -> int:
    model, visual, material = _texture_model(model_path)
    surface = snapshot.state.surface
    np.testing.assert_array_equal(model.vertices[model.faces],
                                  surface.mesh.vertices[surface.mesh.faces].astype(np.float32).astype(float))
    np.testing.assert_array_equal(np.asarray(visual.uv)[model.faces],
                                  _gltf_roundtrip_uv(surface.uv[surface.uv_faces]))
    image = cast(Image.Image, material.baseColorTexture)
    np.testing.assert_array_equal(np.asarray(image.convert("RGBA")), texture)
    return len(model.faces)


def _bake(request: dict[str, object], output: Path) -> dict[str, object]:
    if set(request) - {"stage", "preparation", "target_review", "timeout_seconds"}:
        raise ValueError("WORKFLOW_BAKE_CONFIGURATION_MUST_USE_PREPARATION")
    path = _path(request.get("preparation"), "preparation")
    preparation = read_object(path)
    digest = preparation.pop("preparation_hash", None)
    if digest != object_hash(preparation):
        raise ValueError("WORKFLOW_PREPARATION_CHANGED")
    snapshot = _bound_snapshot(preparation)
    target = _path(preparation.get("target"), "target")
    if file_hash(target) != preparation.get("target_sha256"):
        raise ValueError("WORKFLOW_PREPARED_TARGET_CHANGED")
    field = ColorTarget(snapshot, target)
    if field.record["target_hash"] != preparation.get("target_hash"):
        raise ValueError("WORKFLOW_PREPARED_TARGET_CHANGED")
    for image_path, expected in _mapping(preparation.get("images"), "preparation.images").items():
        if file_hash(_path(image_path, "prepared.image")) != expected:
            raise ValueError("WORKFLOW_PREPARED_VIEW_CHANGED")
    review = _mapping(request.get("target_review"), "target_review")
    camera_names = {str(item["name"]) for item in _objects(preparation.get("cameras"), "cameras")}
    seen, notes = review.get("reviewed_cameras"), review.get("notes")
    if (review.get("target_hash") != preparation["target_hash"] or review.get("decision") != "keep"
            or review.get("reviewer") not in ("agent", "human") or not isinstance(notes, str) or not notes.strip()
            or not isinstance(seen, list) or any(not isinstance(item, str) for item in seen)
            or set(seen) != camera_names):
        raise ValueError("WORKFLOW_ACTUAL_TARGET_REVIEW_REQUIRED")
    target_review = {**review, "preparation": str(path), "preparation_sha256": file_hash(path)}
    target_review["review_hash"] = object_hash(target_review)
    write_json(output / "target-review.json", target_review)
    steps = _Steps(output, _timeout(request))
    baked = steps.run("bake", "material.bake", {**snapshot_result(snapshot), "mode": "basecolor_reuse",
                     "target": str(target), "resolution": preparation["resolution"],
                     "padding_pixels": preparation["padding_pixels"], "acceptance_policy": "visual"})
    bake_path = _path(baked.get("bake"), "bake")
    actual = steps.run("actual_views", "view.render", {**snapshot_result(snapshot), "mode": "basecolor",
                      "texture": str(bake_path.parent / "texture/BaseColor_mip0.png"), "size": preparation["size"],
                      "cameras": preparation["cameras"], "seams": False})
    exported = steps.run("export", "asset.export", {**snapshot_result(snapshot), "bake": str(bake_path),
                         "mode": "diagnostic"})
    model = _path(exported.get("asset"), "export.asset")
    _, _, levels = _load(snapshot, bake_path)
    count = _reimport(model, snapshot, levels[0])
    record = {"status": "OK", "execution_success": True, **snapshot_result(snapshot), "stage": "bake",
              "preparation": str(path), "target_review": str((output / "target-review.json").resolve()),
              "bake": str(bake_path), "model": str(model), "export_manifest": exported["manifest"],
              "actual_views": actual, "reimport_faces": count, "face_corner_positions_float32_exact": True,
              "corner_uv_gltf_roundtrip_exact": True, "texture_reimport_exact": True,
              "uv_roundtrip_contract": "trimesh_gltf_v_flip_float32_export_and_import",
              "uv_roundtrip_max_delta_from_snapshot": float(np.max(np.abs(
                  _gltf_roundtrip_uv(snapshot.state.surface.uv[snapshot.state.surface.uv_faces])
                  - snapshot.state.surface.uv[snapshot.state.surface.uv_faces]))),
              "visual_review": "pending", "numeric_quality": "not_validated", "full_pbr_passed": False,
              "human_reviewed": False}
    write_json(output / "preview.json", record)
    return {**record, "report": str((output / "preview.json").resolve())}


def preview(request: dict[str, object], output: Path) -> dict[str, object]:
    """Prepare source/target views, or bake only after an explicit bound target review."""
    stage = request.get("stage", "prepare")
    if stage == "prepare":
        return _prepare(request, output)
    if stage == "bake":
        return _bake(request, output)
    raise ValueError("WORKFLOW_PREVIEW_STAGE_REQUIRED")


def _review(path: Path, snapshot: Snapshot, bake_path: Path) -> dict[str, object]:
    record = read_object(path)
    digest = record.pop("review_hash", None)
    if digest != object_hash(record):
        raise ValueError("WORKFLOW_VISUAL_REVIEW_CHANGED")
    if (record.get("state_hash") != snapshot.state_hash or record.get("decision") != "keep"
            or record.get("visual_accepted") is not True or record.get("reviewer") not in ("agent", "human")
            or record.get("whole_surface_views") is not True or record.get("all_prepared_views_reviewed") is not True
            or record.get("bake_sha256") != file_hash(bake_path)
            or _path(record.get("bake"), "review.bake") != bake_path):
        raise ValueError("WORKFLOW_BOUND_WHOLE_SURFACE_VISUAL_KEEP_REQUIRED")
    comparison = _path(record.get("comparison"), "review.comparison")
    if file_hash(comparison) != record.get("comparison_sha256"):
        raise ValueError("WORKFLOW_REVIEW_COMPARISON_CHANGED")
    report = read_object(comparison)
    comparison_hash = report.pop("comparison_hash", None)
    if comparison_hash != object_hash(report):
        raise ValueError("WORKFLOW_REVIEW_COMPARISON_CHANGED")
    after = _mapping(report.get("after"), "comparison.after")
    selected = report.get("selected_faces")
    camera_names = {str(camera["name"]) for camera in _objects(report.get("cameras"), "comparison.cameras")}
    seen = record.get("reviewed_cameras")
    if (after.get("state_hash") != snapshot.state_hash or after.get("bake_sha256") != file_hash(bake_path)
            or _path(after.get("bake"), "comparison.after.bake") != bake_path
            or not isinstance(selected, list) or any(type(face) is not int for face in selected)
            or sorted(selected) != list(range(len(snapshot.state.surface.mesh.faces)))
            or record.get("selected_faces") != selected or not isinstance(seen, list)
            or any(not isinstance(camera, str) for camera in seen) or set(seen) != camera_names):
        raise ValueError("WORKFLOW_COMPARISON_REVIEW_BINDING_CHANGED")
    for image_path, expected in _mapping(report.get("images"), "comparison.images").items():
        if file_hash(_path(image_path, "comparison.image")) != expected:
            raise ValueError("WORKFLOW_REVIEW_IMAGE_CHANGED")
    return {**record, "review_hash": digest}


def _export_binding(path: Path, model: Path, review: Path, snapshot: Snapshot,
                    bake_path: Path, bake: dict[str, object]) -> None:
    record = read_object(path)
    if record.get("visual_accepted") is True:
        if (record.get("model_sha256") != file_hash(model) or record.get("review_sha256") != file_hash(review)
                or _path(record.get("model"), "export.model") != model
                or _path(record.get("review"), "export.review") != review):
            raise ValueError("WORKFLOW_VISUAL_EXPORT_BINDING_CHANGED")
        numeric = _mapping(record.get("numeric_result"), "export.numeric_result")
        path = _path(numeric.get("manifest"), "numeric_export.manifest")
        record = read_object(path)
    files = _mapping(record.get("files"), "export.files")
    if (record.get("state_hash") != snapshot.state_hash or files.get("asset.glb") != file_hash(model)
            or record.get("scope") != "basecolor_reuse" or record.get("target_hash") != bake.get("target_hash")
            or files.get("bake.json") != file_hash(bake_path)):
        raise ValueError("WORKFLOW_MODEL_EXPORT_BINDING_CHANGED")
    for relative, expected in files.items():
        source = (path.parent / relative).resolve()
        if not source.is_relative_to(path.parent) or not source.is_file() or file_hash(source) != expected:
            raise ValueError("WORKFLOW_EXPORTED_ARTIFACT_CHANGED")


def collect(request: dict[str, object], output: Path) -> dict[str, object]:
    """Collect a reviewed BaseColor model and prove named-object export integrity."""
    snapshot = _bound_snapshot(request)
    model_path = _path(request.get("model"), "model")
    export_path = _path(request.get("export_manifest"), "export_manifest")
    parts_path = _path(request.get("parts"), "parts")
    bake_path = _path(request.get("bake"), "bake")
    review_path = _path(request.get("review"), "review")
    bake, _, levels = _load(snapshot, bake_path)
    review = _review(review_path, snapshot, bake_path)
    _export_binding(export_path, model_path, review_path, snapshot, bake_path, bake)
    _reimport(model_path, snapshot, levels[0])
    manifest = read_object(parts_path)
    if "state_hash" in manifest and manifest["state_hash"] != snapshot.state_hash:
        raise ValueError("WORKFLOW_STALE_PARTS_MANIFEST")
    expected_parts = _part_faces(snapshot)
    supplied = _objects(manifest.get("parts"), "parts")
    if {str(part.get("id")): part.get("faces") for part in supplied} != expected_parts or len(supplied) != len(expected_parts):
        raise ValueError("WORKFLOW_STALE_PARTS_MAPPING")
    all_faces = [face for faces in expected_parts.values() for face in faces]
    if sorted(all_faces) != list(range(len(snapshot.state.surface.mesh.faces))):
        raise ValueError("WORKFLOW_PART_FACE_COVERAGE_MISMATCH")
    numeric_accepted = False
    validation: dict[str, object] = {}
    if "validation" in request:
        validation_path = _path(request["validation"], "validation")
        validation = read_object(validation_path)
        if (validation.get("state_hash") != snapshot.state_hash or validation.get("bake_sha256") != file_hash(bake_path)
                or validation.get("target_hash") != bake["target_hash"]
                or validation.get("scope") != "basecolor_reuse"
                or validation.get("profile_hash") != object_hash(validation.get("profile"))):
            raise ValueError("WORKFLOW_STALE_NUMERIC_VALIDATION")
        numeric_accepted = validation.get("valid") is True and all(
            _mapping(validation.get(key), f"validation.{key}").get("valid") is True
            for key in ("geometry", "material", "render", "seams"))
        shutil.copy2(validation_path, output / "numeric-validation.json")
    model, visual, material = _texture_model(model_path)
    uv = np.asarray(visual.uv)
    scene = trimesh.Scene()
    expected: dict[str, FloatArray] = {}
    for identity, face_ids in expected_parts.items():
        points = model.vertices[model.faces[face_ids]]
        child = trimesh.Trimesh(vertices=points.reshape(-1, 3),
                                faces=np.arange(len(face_ids)*3).reshape(-1, 3), process=False)
        child.visual = TextureVisuals(uv=uv[model.faces[face_ids]].reshape(-1, 2), material=material)
        scene.add_geometry(child, node_name=identity, geom_name=identity)
        expected[identity] = points
    multi = output / "model-parts.glb"
    scene.export(multi, file_type="glb")
    reloaded = trimesh.load_scene(multi, process=False)
    if set(reloaded.graph.nodes_geometry) != set(expected):
        raise ValueError("WORKFLOW_REIMPORT_OBJECTS_MISMATCH")
    for node in reloaded.graph.nodes_geometry:
        transform, name = reloaded.graph[node]
        geometry = reloaded.geometry[name]
        if not isinstance(geometry, trimesh.Trimesh):
            raise TypeError("WORKFLOW_REIMPORT_TRIANGLES_REQUIRED")
        positions = trimesh.transform_points(geometry.vertices, transform)[geometry.faces]
        np.testing.assert_array_equal(positions, expected[str(node)])
        child_visual = geometry.visual
        if not isinstance(child_visual, TextureVisuals) or not isinstance(child_visual.material, PBRMaterial):
            raise TypeError("WORKFLOW_REIMPORT_MATERIAL_REQUIRED")
        np.testing.assert_array_equal(np.asarray(child_visual.uv)[geometry.faces],
                                      _gltf_roundtrip_uv(uv[model.faces[expected_parts[str(node)]]]))
        texture = cast(Image.Image, child_visual.material.baseColorTexture)
        np.testing.assert_array_equal(np.asarray(texture.convert("RGBA")), levels[0])
    shutil.copy2(model_path, output / "model.glb")
    shutil.copytree(bake_path.parent / "texture", output / "texture")
    shutil.copy2(parts_path, output / "parts.json")
    shutil.copy2(review_path, output / "visual-review.json")
    shutil.copy2(bake_path, output / "bake-evidence.json")
    for key, filename in (("uv_image", "uv.png"), ("preview_image", "preview.png")):
        if key in request:
            shutil.copy2(_path(request[key], key), output / filename)
    result: dict[str, object] = {
        "status": "OK", "execution_success": True, **snapshot_result(snapshot),
        "model": str((output / "model.glb").resolve()), "named_model": str(multi.resolve()),
        "named_objects": len(expected), "faces": len(model.faces), "face_coverage_exact": True,
        "part_positions_uv_and_textures_reimported_exact": True,
        "corner_uv_gltf_roundtrip_exact": True,
        "uv_roundtrip_contract": "trimesh_gltf_v_flip_float32_export_and_import",
        "visual_accepted": True,
        "reviewer": review["reviewer"], "review_scope": review.get("scope"),
        "scope": "basecolor_reuse", "numeric_accepted": numeric_accepted,
        "numeric_profile": validation.get("profile"), "human_reviewed": review["reviewer"] == "human",
        "full_pbr_passed": False, "gpu_verified": False,
        "sources": {str(path): file_hash(path) for path in (model_path, export_path, parts_path, bake_path, review_path)},
        "files": {str(path.relative_to(output)): file_hash(path) for path in sorted(output.rglob("*")) if path.is_file()},
    }
    write_json(output / "verification.json", result)
    return {**result, "verification": str((output / "verification.json").resolve())}
