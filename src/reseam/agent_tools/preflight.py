"""Read-only bake diagnostics. Findings never authorize an edit or an export."""

import json
import math
import struct
import time
from dataclasses import asdict
from pathlib import Path
from typing import cast

import numpy as np
from PIL import Image, ImageDraw

from ..budget_material import frame_arrays
from ..types import FloatArray
from ..validation import topology_errors
from .geometry import geometry_report
from .hard_edges import source_fields
from .state import (
    Snapshot,
    load_snapshot,
    object_hash,
    require_snapshot,
    write_json,
)


def mapping(value: object, label: str) -> dict[str, object]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise ValueError(f"OBJECT_REQUIRED:{label}")
    return cast(dict[str, object], value)


def finite_number(data: dict[str, object], key: str, default: float, minimum: float = 0.) -> float:
    value = data.get(key, default)
    if type(value) not in (int, float) or not math.isfinite(cast(float, value)) or cast(float, value) < minimum:
        raise ValueError(f"FINITE_NUMBER_OUT_OF_RANGE:{key}")
    return float(cast(float, value))


def normal_uv_audit(snapshot: Snapshot, source: Snapshot | None, epsilon: float) -> dict[str, object]:
    """Compare endpoint identities, including internal polygon diagonals."""
    if not 0 < epsilon <= 180:
        raise ValueError("NORMAL_EPSILON_IN_0_180_REQUIRED")
    surface = snapshot.state.surface
    mesh = surface.mesh
    imported, polygons, sides, evidence = source_fields(snapshot, source)
    target: FloatArray | None = None
    target_error: str | None = None
    try:
        target = frame_arrays(surface)[0]
    except (ValueError, OSError) as error:
        target_error = str(error)
    rows: list[dict[str, object]] = []
    source_bad: list[int] = []
    target_bad: list[int] = []
    coincident: list[int] = []
    source_checked = 0
    xyz = mesh.vertices[mesh.faces]
    geometric = np.cross(xyz[:, 1]-xyz[:, 0], xyz[:, 2]-xyz[:, 0])
    geometric /= np.linalg.norm(geometric, axis=1)[:, None]
    sharp = {tuple(sorted(edge)) for edge in cast(list[list[int]], evidence["imported_sharp_vertex_edges"])}
    for edge_id, entries in enumerate(mesh.edge_faces):
        if len(entries) != 2:
            continue
        a, b = (entry[0] for entry in entries)
        ca = [int(np.flatnonzero(mesh.faces[a] == v)[0]) for v in mesh.edges[edge_id]]
        cb = [int(np.flatnonzero(mesh.faces[b] == v)[0]) for v in mesh.edges[edge_id]]
        ua, ub = surface.uv_faces[a, ca], surface.uv_faces[b, cb]
        split = bool(np.any(ua != ub))
        same_position = bool(np.array_equal(surface.uv[ua], surface.uv[ub]))
        angles: dict[str, float | None] = {}
        for label, normals in (("source", imported), ("target", target)):
            angle: float | None = None
            # Transferred mirror normals are only comparable within each known side.
            if normals is not None and (label == "target" or sides[a] == sides[b]):
                angle = float(np.degrees(np.arccos(np.clip(
                    np.sum(normals[a, ca] * normals[b, cb], axis=1), -1., 1.))).max())
                if label == "source":
                    source_checked += 1
                if angle >= epsilon and not split:
                    (source_bad if label == "source" else target_bad).append(edge_id)
            angles[label] = angle
        if split and same_position:
            coincident.append(edge_id)
        rows.append({"edge_id": edge_id, "vertices": list(mesh.edges[edge_id]), "faces": [a, b],
                     "uv_split": split, "split_with_coincident_coordinates": split and same_position,
                     "source_angle_degrees": angles["source"], "target_angle_degrees": angles["target"],
                     "geometric_dihedral_degrees": float(np.degrees(np.arccos(np.clip(
                         geometric[a] @ geometric[b], -1., 1.)))),
                     "geometric_angle_is_proxy": True, "imported_sharp_flag": mesh.edges[edge_id] in sharp,
                     "original_polygon_diagonal": bool(polygons is not None and polygons[a] == polygons[b]
                                                        and sides[a] == sides[b])})
    source_complete = imported is not None and source_checked == len(rows)
    return {"status": "fail" if source_bad or target_bad else "warn" if coincident else
            "pass" if source_complete and target is not None else "not_checked",
            "source": {"status": "fail" if source_bad else "pass" if source_complete else "not_checked",
                       "checked_edges": source_checked, "missing_uv_seam_edges": source_bad,
                       "evidence": evidence},
            "target": {"status": "fail" if target_bad else "pass" if target is not None else "not_checked",
                       "missing_uv_seam_edges": target_bad, "error": target_error,
                       "contract": ("budget_material.frame_arrays imported corner normals with MikkTSpace"
                                    if surface.corner_normals is not None else
                                    "budget_material.frame_arrays explicit flat target; not arbitrary engine normals")},
            "split_coincident_edges": coincident, "edges": rows,
            "normal_epsilon_degrees": epsilon, "diagonals_suppressed": False,
            "scope": "original indexed edges only; no position welding; imported evaluated corner normals",
            "smoothing_groups": "not inferred from IDs; imported evaluated normals include membership effects"}


def uv_sampling_audit(snapshot: Snapshot, raw: object) -> dict[str, object]:
    if raw is None:
        return {"status": "not_checked", "reason": "EXPLICIT_BAKE_CONFIG_REQUIRED"}
    config = mapping(raw, "bake_config")
    required = {"resolution", "gap_pixels", "border_pixels", "padding_pixels", "mip_levels"}
    if not required.issubset(config):
        raise ValueError("BAKE_CONFIG_MISSING:" + ",".join(sorted(required - config.keys())))
    resolution = config["resolution"]
    if type(resolution) is not int or not 4 <= resolution <= 8192:
        raise ValueError("RESOLUTION_INTEGER_4_8192_REQUIRED")
    gap = finite_number(config, "gap_pixels", 0.)
    border = finite_number(config, "border_pixels", 0.)
    padding = finite_number(config, "padding_pixels", 0.)
    levels = config["mip_levels"]
    if (not isinstance(levels, list) or not levels or any(type(v) is not int or v < 0
            or v > int(math.log2(resolution)) for v in levels) or len(set(levels)) != len(levels)):
        raise ValueError("MIP_LEVELS_UNIQUE_VALID_INTEGERS_REQUIRED")
    if np.any(snapshot.state.surface.uv < 0) or np.any(snapshot.state.surface.uv > 1):
        return {"status": "not_checked", "reason": "SINGLE_0_1_ATLAS_ONLY_UDIM_NOT_SUPPORTED", "config": config}
    reference = snapshot.metadata.get("reference_glb")
    if isinstance(reference, str):
        with Path(reference).open("rb") as stream:
            header = stream.read(20)
            length = struct.unpack_from("<I", header, 12)[0]
            document = json.loads(stream.read(length))
        materials = {p.get("material", -1) for m in document.get("meshes", []) for p in m.get("primitives", [])}
        if len(materials) > 1:
            return {"status": "not_checked", "reason": "MULTI_MATERIAL_ATLAS_ROUTING_NOT_SUPPORTED",
                    "config": config, "material_ids": sorted(materials)}
    geometry = geometry_report(snapshot.state, {"resolution": resolution, "gap_pixels": gap,
        "border_pixels": border, "quality_constraints": False})
    actual_gap = geometry.get("minimum_gap_pixels")
    actual_border = geometry.get("minimum_border_pixels")
    mip_rows = [{"level": level, "resolution": max(1, resolution // 2**level),
                 "effective_gap_pixels": None if actual_gap is None else float(str(actual_gap)) / 2**level,
                 "effective_border_pixels": None if actual_border is None else float(str(actual_border)) / 2**level,
                 "effective_padding_pixels": padding / 2**level} for level in levels]
    warnings = []
    if actual_gap is not None and float(str(actual_gap)) < 2 * padding:
        warnings.append("ISLAND_GAP_LESS_THAN_TWO_PADDING_WIDTHS")
    if any(row["effective_gap_pixels"] is not None and float(str(row["effective_gap_pixels"])) < 1
           for row in mip_rows):
        warnings.append("SUBPIXEL_GAP_AT_REQUESTED_MIP")
    return {"status": "fail" if not geometry["valid"] else "warn" if warnings else "pass",
            "config": config, "geometry": geometry, "mips": mip_rows, "warnings": warnings,
            "authorized_groups": [asdict(group) for group in snapshot.state.groups],
            "scope": "active UV atlas geometry; not shader/material routing or filter/compression acceptance",
            "filtering_validated": False}


def write_report(output: Path, report: dict[str, object]) -> None:
    write_json(output / "preflight.json", report)
    checks = mapping(report["checks"], "checks")
    lines = ["# UV 与法线烘焙预检", "", f"父状态：`{report['state_hash']}`。`[verified: preflight.json state_hash]`",
             "", (f"执行完整：`{report['complete']}`；诊断结论：`{report['preflight_status']}`。"
                  "`[verified: preflight.json complete/preflight_status]`"),
             "", "此报告不授权修改模型，不代表最终外观、完整 PBR、GPU 或人工验收。`[opinion]`", "",
             "| 检查 | 状态 | 证据 |", "|---|---|---|"]
    for name, value in checks.items():
        check = mapping(value, name)
        lines.append(f"| {name} | {check['status']} | [preflight.json](preflight.json) "
                     f"`[verified: checks.{name}]` |")
    lines.extend(["", "数值与定位详见 JSON；射线逐条记录见 projection/samples.jsonl（若执行）。",
                  "最近点距离与射线命中分列；有限采样不保证连续覆盖或 Cycles 全部采样一致。`[opinion]`"])
    (output / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def normal_vector_picture(snapshot: Snapshot, source: Snapshot | None, path: Path) -> None:
    surface = snapshot.state.surface
    vertices, faces = surface.mesh.vertices, surface.mesh.faces
    imported, _, _, evidence = source_fields(snapshot, source)
    source_label = "Imported evaluated normals"
    if evidence.get("lineage"):
        # Lineage retains source-space angles, not reflected world-space vectors.
        imported = None
        source_label = "Transferred normals: direction unavailable"
    try:
        target = frame_arrays(surface)[0]
    except (ValueError, OSError):
        target = None
    basis = np.array([[.819, 0., -.574], [.243, .906, .346]])
    points = vertices @ basis.T
    center = (points.min(axis=0)+points.max(axis=0))/2
    span = max(float(np.ptp(points, axis=0).max()), 1e-12)
    picture = Image.new("RGB", (768, 384), "#f7f7f7")
    draw = ImageDraw.Draw(picture)
    stride = max(1, len(faces)//96)
    for column, (label, normals, color) in enumerate(((source_label, imported, "#087cba"),
                                                     ("ReSeam selected target normals", target, "#d96808"))):
        def pixel(point: FloatArray, offset: int = column) -> tuple[float, float]:
            xy = (point @ basis.T-center) * (270/span)
            return float(192+384*offset+xy[0]), float(205-xy[1])
        draw.text((12+384*column, 12), label if normals is not None else label+": unavailable", fill="black")
        draw.text((12+384*column, 29), "X-ray vectors, not a shaded quality preview", fill="black")
        for face in faces:
            tri = [pixel(vertices[v]) for v in face]
            draw.line([*tri, tri[0]], fill="#c4cbd0")
        if normals is None:
            continue
        for face_id in range(0, len(faces), stride):
            point = vertices[faces[face_id]].mean(axis=0)
            normal = normals[face_id].mean(axis=0)
            normal /= max(float(np.linalg.norm(normal)), 1e-12)
            start, end = pixel(point), pixel(point+normal*span*.10)
            draw.line([start, end], fill=color, width=2)
            draw.ellipse((end[0]-2, end[1]-2, end[0]+2, end[1]+2), fill=color)
    picture.save(path)


def preflight(request: dict[str, object], output: Path, deadline: float) -> dict[str, object]:
    snapshot = require_snapshot(request)
    if type(request.get("views", True)) is not bool:
        raise TypeError("VIEWS_BOOLEAN_REQUIRED")
    names = request.get("checks", ["geometry", "normal_uv", "uv_sampling", "projection"])
    allowed = {"geometry", "normal_uv", "uv_sampling", "projection"}
    if (not isinstance(names, list) or not names or any(not isinstance(x, str) or x not in allowed for x in names)
            or len(set(names)) != len(names)):
        raise ValueError("INVALID_PREFLIGHT_CHECKS")
    checks: dict[str, object] = {name: {"status": "not_checked", "reason": "NOT_REQUESTED"}
                               for name in sorted(allowed)}
    report: dict[str, object] = {"schema_version": 1, "state_hash": snapshot.state_hash,
        "snapshot": str(snapshot.root), "config": request, "config_hash": object_hash(request),
        "complete": False, "preflight_status": "not_checked", "checks": checks,
        "snapshot_modified": False, "final_quality_passed": None, "appearance_acceptance": "NOT_RUN"}
    source: Snapshot | None = None
    write_report(output, report)
    if "geometry" in names:
        errors = topology_errors(snapshot.state.surface)
        checks["geometry"] = {"status": "fail" if errors else "pass", "errors": errors,
                              "scope": "snapshot structural topology and finite coordinates"}
        write_report(output, report)
    if "normal_uv" in names:
        source = load_snapshot(Path(str(request["source_snapshot"]))) if "source_snapshot" in request else None
        checks["normal_uv"] = normal_uv_audit(snapshot, source,
                                             finite_number(request, "normal_epsilon_degrees", .1))
        write_report(output, report)
    if "uv_sampling" in names:
        checks["uv_sampling"] = uv_sampling_audit(snapshot, request.get("bake_config"))
        write_report(output, report)
    if "projection" in names:
        from .preflight_projection import projection_audit
        checks["projection"] = projection_audit(snapshot, request, output / "projection", deadline)
        write_report(output, report)
    statuses = [str(mapping(checks[str(name)], str(name))["status"]) for name in names]
    report["preflight_status"] = next((s for s in ("fail", "not_checked", "warn", "pass") if s in statuses),
                                      "not_checked")
    report["complete"] = not any(mapping(checks[str(n)], str(n)).get("incomplete", False) for n in names)
    # Visuals are diagnostics: errors/edges use the existing visibility-correct renderer.
    if request.get("views", True) and time.monotonic() < deadline - 8:
        from .views import render_views
        normal = mapping(checks["normal_uv"], "normal_uv")
        edges = sorted(set(cast(list[int], mapping(normal.get("source", {}), "source").get(
            "missing_uv_seam_edges", [])) + cast(list[int], mapping(normal.get("target", {}), "target").get(
            "missing_uv_seam_edges", []))))
        projection = mapping(checks["projection"], "projection")
        view_dir = output / "views"
        view_dir.mkdir()
        report["views"] = render_views({"snapshot": str(snapshot.root), "state_hash": snapshot.state_hash,
            "mode": "errors", "error_faces": projection.get("problem_faces", []), "seam_edge_ids": edges,
            "size": 384, "cameras": [{"name": "oblique", "azimuth": 35., "elevation": 25.}]},
            view_dir, deadline)
        normal_vector_picture(snapshot, source, output / "normal_vectors.png")
        report["normal_vectors"] = str(output / "normal_vectors.png")
    if require_snapshot(request).state_hash != snapshot.state_hash:
        raise ValueError("PREFLIGHT_INPUT_CHANGED")
    if source is not None and load_snapshot(source.root).state_hash != source.state_hash:
        raise ValueError("PREFLIGHT_SOURCE_EVIDENCE_CHANGED")
    write_report(output, report)
    return {"status": "OK" if report["complete"] else "TIMEOUT", "execution_success": report["complete"],
            "report": str(output / "preflight.json"), "markdown": str(output / "report.md"),
            "preflight_status": report["preflight_status"], "snapshot_modified": False,
            "final_quality_passed": None}
