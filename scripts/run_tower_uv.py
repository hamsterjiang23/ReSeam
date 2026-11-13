"""Reproducible tower UV experiments through the existing immutable Agent tools."""

from __future__ import annotations

import argparse
import time
from pathlib import Path

import numpy as np

from reseam.agent_tools.regional_cut import cut_region
from reseam.agent_tools.runner import run_tool
from reseam.agent_tools.state import file_hash, load_snapshot, read_object, write_json
from reseam.mesh import chart_labels

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "artifacts/tower_uv_comparison/round02"
BLENDER = "E:/Blender 4.5.11/blender.exe"
CAMERAS = [{"name": name, "azimuth": azimuth, "elevation": elevation}
           for name, azimuth, elevation in [
               ("front", 0, 0), ("back", 180, 0), ("left", -90, 0), ("right", 90, 0),
               ("front_right", 35, 15), ("front_left", -35, 15),
               ("back_right", 145, 15), ("back_left", -145, 15)]]


def call(label: str, tool: str, request: dict[str, object]) -> dict[str, object]:
    output = BASE / label
    path = BASE / "requests" / (label.replace("/", "_") + ".json")
    if (output / "result.json").exists():
        if path.exists() and read_object(path) != request:
            raise ValueError("REQUEST_CHANGED_NEW_LABEL_REQUIRED:" + label)
        return read_object(output / "result.json")
    write_json(path, request)
    result = run_tool(tool, path, output)
    print(label, result.get("status"), result.get("reason", ""), flush=True)
    return result


def parent() -> str:
    record = BASE / "parent.json"
    if record.exists():
        return str(read_object(record)["snapshot"])
    return str(BASE / "clean_import/snapshot/snapshot.json")


def profile(snapshot: str, resolution: int = 2048) -> dict[str, object]:
    snap = load_snapshot(Path(snapshot))
    return {"resolution": resolution, "gap_pixels": 16 if resolution == 2048 else 2,
            "border_pixels": 8 if resolution == 2048 else 1, "quality_constraints": False,
            "normal_angle_degrees": 180, "correspondence_fraction": .1,
            "preserve_source_uv": {"snapshot": snapshot, "state_hash": snap.state_hash}}


def prepare() -> None:
    repaired = call("normal_repair", "asset.repair_normals", {"snapshot": parent(), "timeout_seconds": 300})
    if "snapshot" in repaired:
        write_json(BASE / "parent.json", {"snapshot": repaired["snapshot"]})
    snap = load_snapshot(Path(parent()))
    write_json(BASE / "protocol.json", {
        "asset": "fantasy cross statue", "source_zip_sha256": file_hash(BASE / "inputs/source.zip"),
        "parent": parent(), "state_hash": snap.state_hash, "faces": len(snap.state.surface.mesh.faces),
        "scope": "UV + BaseColor + Normal; constant roughness=.6 metallic=0",
        "normal_convention": "OpenGL, explicitly assumed and checked through matched renders",
        "packing_seeds": [17, 29, 43], "resolution": 2048, "gap_pixels": 16,
        "border_pixels": 8, "padding_pixels": 8, "acceptance_policy": "visual",
        "user_direction": "maximize optimization; numerical rejection remains advisory",
        "cameras": CAMERAS, "human_acceptance": "NOT_RUN", "game_shader": "NOT_RUN"})
    call("source_views", "view.render", {"snapshot": parent(), "mode": "basecolor",
         "size": 768, "cameras": CAMERAS, "timeout_seconds": 300})
    write_json(BASE / "selected/source_uv.json", {"snapshot": parent()})


def native(method: str) -> None:
    result = call("native/" + method, "uv.baseline", {
        "snapshot": parent(), "method": method, "timeout_seconds": 2400})
    if "snapshot" in result:
        write_json(BASE / "selected" / (method + ".json"), {"snapshot": result["snapshot"]})


def pack(label: str, snapshot: str, resolution: int) -> str | None:
    options: list[tuple[float, str]] = []
    for seed in (17, 29, 43):
        result = call(f"{label}/pack{seed}", "uv.pack", {
            "snapshot": snapshot, "backend": "blender_concave", "packing_seed": seed,
            "density_normalize": True, "profile": profile(snapshot, resolution),
            "acceptance_policy": "visual", "timeout_seconds": 300})
        if "snapshot" in result:
            report = read_object(BASE / label / f"pack{seed}/geometry.json")
            coverage = float(str(report.get("unique_coverage", 0)))
            options.append((coverage, str(result["snapshot"])))
    if not options:
        return None
    selected = max(options)
    write_json(BASE / label / "packing_selection.json", {
        "candidates": options, "snapshot": selected[1], "coverage": selected[0],
        "rule": "highest measured coverage among tool-emitted layouts; numerical flags retained"})
    return selected[1]


def evaluate(method: str, resolution: int) -> None:
    choice = BASE / "selected" / (method + ".json")
    if not choice.exists():
        return
    choice_record = read_object(choice)
    original = str(choice_record["snapshot"])
    label = f"evaluation/{method}_{resolution}"
    snapshot = original if method == "source_uv" else pack(label, original, resolution)
    if snapshot is None:
        call(label + "/raw_views", "view.render", {"snapshot": original, "mode": "checker",
             "size": 768, "cameras": CAMERAS[:1], "timeout_seconds": 300})
        return
    common = {"snapshot": snapshot, "acceptance_policy": "visual", "timeout_seconds": 300,
              "geometry_profile": profile(snapshot, resolution), "mode": "pbr_reuse"}
    call(label + "/inspect", "uv.inspect", {"snapshot": snapshot,
         "profile": profile(snapshot, resolution), "timeout_seconds": 300})
    call(label + "/uv_views", "view.render", {"snapshot": snapshot, "mode": "checker",
         "size": 768, "uv_labels": "owners", "cameras": CAMERAS[:1], "timeout_seconds": 300})
    if "frozen_target" in choice_record:
        target = {"target": choice_record["frozen_target"]}
        write_json(BASE / label / "frozen_target_reference.json", target)
    else:
        target = call(label + "/target", "material.target", {**common,
            **({"master_boundary_tolerance_pixels": choice_record["master_boundary_tolerance_pixels"]}
               if "master_boundary_tolerance_pixels" in choice_record else {}),
            "projection_method": "same_surface_face_corner", "policy": "master" if "shared" in method
            and "unshared" not in method else "source_original"})
    if "target" not in target:
        return
    target_record = read_object(Path(str(target["target"])))
    common["geometry_profile"] = target_record["geometry_profile"]
    bake = call(label + "/bake", "material.bake", {**common, "target": target["target"],
         "resolution": resolution, "padding_pixels": 8 if resolution == 2048 else 1})
    if "bake" not in bake:
        return
    validation = call(label + "/validate", "quality.validate", {"snapshot": snapshot,
         "bake": bake["bake"], "geometry_profile": common["geometry_profile"],
         "mip_levels": [0, 1, 2], "render_size": 48, "timeout_seconds": 300})
    exported = call(label + "/export", "asset.export", {"snapshot": snapshot,
         "bake": bake["bake"], "mode": "diagnostic", "timeout_seconds": 300})
    if "asset" in exported:
        call(label + "/preview", "artist.preview", {"snapshot": snapshot, "model": exported["asset"],
             "size": 512, "channels": ["material", "basecolor", "normal_lit"],
             "cameras": CAMERAS, "lights": ["soft", "side"], "distances": [1],
             "save_blend": True, "timeout_seconds": 300})
    write_json(BASE / label / "summary.json", {"snapshot": snapshot, "bake": bake,
         "validation": validation, "export": exported, "material_scope": "color+normal+constant factors"})


def mirror(optimized: bool = False, aggressive: bool = False, exhaustive: bool = False) -> None:
    for side in ((False,) if optimized else (True, False)):
        name = "positive" if side else "negative"
        parent_method = "reseam_exhaustive" if exhaustive else "reseam_aggressive" if aggressive else "reseam_optimized" if optimized else "reseam_unshared"
        suffix = "_exhaustive_minimum" if exhaustive else "_aggressive_minimum" if aggressive else "_optimized_minimum" if optimized else ""
        mirror_parent = str(read_object(BASE / f"selected/{parent_method}.json")["snapshot"])
        result = call("mirror/" + name + suffix + "_clean", "asset.mirror_variant", {
            "snapshot": mirror_parent, "axis": "x", "offset": 0., "keep_positive": side,
            "allow_geometry_change": True, "material_scope": "preserve_pbr",
            "profile": profile(mirror_parent), "timeout_seconds": 300})
        if "snapshot" in result:
            write_json(BASE / "selected" / ("mirror_" + name + suffix + "_shared.json"), {
                "snapshot": result["snapshot"]})
            write_json(BASE / "selected" / ("mirror_" + name + suffix + "_unshared.json"), {
                "snapshot": result["unshared_snapshot"]})
            call("mirror/" + name + suffix + "_views", "view.render", {"snapshot": result["snapshot"],
                "mode": "basecolor", "size": 768, "cameras": CAMERAS, "timeout_seconds": 300})


def craft() -> None:
    snap = load_snapshot(Path(parent()))
    surface = snap.state.surface
    mesh = surface.mesh
    uv = surface.uv[surface.uv_faces]
    seams: list[list[int]] = []
    for edge, incident in zip(mesh.edges, mesh.edge_faces, strict=True):
        if len(incident) != 2:
            continue
        a, b = incident[0][0], incident[1][0]
        first = [uv[a, np.flatnonzero(mesh.faces[a] == v)[0]] for v in edge]
        second = [uv[b, np.flatnonzero(mesh.faces[b] == v)[0]] for v in edge]
        if not np.allclose(first, second, rtol=0, atol=1e-7):
            seams.append(list(edge))
    write_json(BASE / "craft/seam_decision.json", {"parent": parent(), "edges": seams,
        "reason": "Use original-edge UV discontinuities as openings, remove per-polygon attribute splits by explicit regional reparameterization; positions are unchanged",
        "uv_edge_endpoint_tolerance": 1e-7, "new_uv_required": True})
    indexed = call("craft/index", "mesh.index", {"snapshot": parent(), "ao_rays": 8, "timeout_seconds": 300})
    if "index" in indexed:
        call("craft/components", "mesh.query", {"snapshot": parent(), "index": indexed["index"],
             "sql": "SELECT * FROM component ORDER BY id", "timeout_seconds": 300})
    for backend in ("blender_lscm", "blender_minimum_stretch", "blender_abf"):
        result = call("craft_actual_cuts/" + backend, "uv.reparameterize", {
            "snapshot": parent(), "faces": list(range(len(mesh.faces))), "seam_edges": seams,
            "backend": backend, "profile": profile(parent()), "acceptance_policy": "visual",
            "timeout_seconds": 300})
        if "snapshot" in result:
            method = "meshquery_project" if backend == "blender_lscm" else "reseam_unshared" if backend == "blender_minimum_stretch" else "reseam_optimized"
            write_json(BASE / "selected" / (method + ".json"), {"snapshot": result["snapshot"],
                       "seam_decision": str(BASE / "craft/seam_decision.json"), "backend": backend})


def optimize(aggressive: bool = False, exhaustive: bool = False) -> None:
    path = str(read_object(BASE / "selected/reseam_unshared.json")["snapshot"])
    snap = load_snapshot(Path(path))
    surface, mesh = snap.state.surface, snap.state.surface.mesh
    labels = chart_labels(surface).copy()
    seams = set(surface.seams)
    xyz = mesh.vertices[mesh.faces]
    normals = np.cross(xyz[:, 1] - xyz[:, 0], xyz[:, 2] - xyz[:, 0])
    normals /= np.linalg.norm(normals, axis=1, keepdims=True)
    changes = []
    for _ in range(len(np.unique(labels)) if exhaustive else 70):
        neighbors: dict[tuple[int, int], list[int]] = {}
        for edge_id, incident in enumerate(mesh.edge_faces):
            if len(incident) == 2:
                a, b = incident[0][0], incident[1][0]
                pair = (min(int(labels[a]), int(labels[b])), max(int(labels[a]), int(labels[b])))
                if pair[0] != pair[1]:
                    neighbors.setdefault(pair, []).append(edge_id)
        accepted = False
        for pair, boundary in sorted(neighbors.items(), key=lambda item: -len(item[1])):
            angles = [float(np.degrees(np.arccos(np.clip(normals[mesh.edge_faces[e][0][0]] @
                       normals[mesh.edge_faces[e][1][0]], -1, 1)))) for e in boundary]
            if max(angles) > (150 if aggressive else 75) or np.mean(angles) > (90 if aggressive else 40):
                continue
            selected = tuple(map(int, np.flatnonzero(np.isin(labels, pair))))
            remaining = seams - set(boundary)
            selected_set = set(selected)
            cuts = [list(mesh.edges[e]) for e in remaining
                    if any(f in selected_set for f, _ in mesh.edge_faces[e])]
            cut = cut_region(surface, selected, cuts, time.monotonic() + 15)
            local = cut.surface.mesh
            if len(local.vertices) - len(local.edges) + len(local.faces) != 1:
                continue
            seams = remaining
            labels[labels == pair[1]] = pair[0]
            changes.append({"charts": pair, "removed_original_edges": boundary,
                            "faces": selected, "max_boundary_angle": max(angles),
                            "mean_boundary_angle": float(np.mean(angles)), "cut_euler": 1})
            accepted = True
            break
        if not accepted:
            break
    plan_name = "exhaustive_merge_plan" if exhaustive else "aggressive_merge_plan" if aggressive else "merge_plan"
    write_json(BASE / f"optimization/{plan_name}.json", {"parent": path, "changes": changes,
        "remaining_edges": [list(mesh.edges[e]) for e in sorted(seams)],
        "policy": "remove full common boundary of adjacent charts, retain disk topology and observed hard folds; render decides retention"})
    result = call("optimization/merged_exhaustive_minimum" if exhaustive else "optimization/merged_aggressive_minimum" if aggressive else "optimization/merged_abf", "uv.reparameterize", {
        "snapshot": path, "faces": list(range(len(mesh.faces))), "backend": "blender_minimum_stretch" if aggressive else "blender_abf",
        "seam_edges": [list(mesh.edges[e]) for e in sorted(seams)], "profile": profile(path),
        "acceptance_policy": "visual", "timeout_seconds": 300})
    if "snapshot" in result:
        method = "reseam_exhaustive" if exhaustive else "reseam_aggressive" if aggressive else "reseam_optimized"
        write_json(BASE / f"selected/{method}.json", {"snapshot": result["snapshot"],
                   "merge_plan": str(BASE / f"optimization/{plan_name}.json")})


def original_share() -> None:
    path = str(read_object(BASE / "selected/reseam_unshared.json")["snapshot"])
    snap = load_snapshot(Path(path))
    centers = snap.state.surface.mesh.vertices[snap.state.surface.mesh.faces].mean(1)
    left = np.flatnonzero((centers[:, 0] < -.055) & (centers[:, 1] > .72)).tolist()
    right = np.flatnonzero((centers[:, 0] > .055) & (centers[:, 1] > .72)).tolist()
    result = call("original_share/bow_pair", "reuse.check_pair", {
        "snapshot": path, "first_faces": left, "second_faces": right,
        "mode": "chart_aware", "preserve_rejected_candidate": True,
        "plane_normal": [1, 0, 0], "plane_offset": 0, "profile": profile(path), "timeout_seconds": 300})
    if "proposal" in result:
        applied = call("original_share/apply", "reuse.apply", {"snapshot": path,
            "proposal": result["proposal"], "action": "share", "profile": profile(path), "timeout_seconds": 300})
        if "snapshot" in applied:
            write_json(BASE / "selected/reseam_shared.json", {"snapshot": applied["snapshot"]})


def share_candidates() -> None:
    current = str(read_object(BASE / "selected/reseam_unshared.json")["snapshot"])
    record = read_object(BASE / "original_share/candidates/candidates.json")
    candidates = record["candidates"]
    if not isinstance(candidates, list):
        raise TypeError("CANDIDATES_REQUIRED")
    candidates.sort(key=lambda item: -len(item["check_request"]["first_faces"]))
    accepted = []
    for index, candidate in enumerate(candidates[:16]):
        snap = load_snapshot(Path(current))
        request = dict(candidate["check_request"])
        request.update(snapshot=current, state_hash=snap.state_hash, profile=profile(current),
                       timeout_seconds=300)
        result = call(f"original_share/individual{index}/check", "reuse.check_pair", request)
        if "proposal" not in result:
            continue
        result = call(f"original_share/individual{index}/apply", "reuse.apply", {
            "snapshot": current, "action": "share", "proposal": result["proposal"],
            "profile": profile(current), "timeout_seconds": 300})
        if "snapshot" in result:
            current = str(result["snapshot"])
            accepted.append(candidate["candidate_id"])
            write_json(BASE / "selected/reseam_shared.json", {"snapshot": current,
                       "accepted_candidate_ids": accepted})


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("stage", choices=["prepare", "native", "evaluate", "mirror", "craft", "optimize", "share", "pairs"])
    parser.add_argument("--method", default="source_uv")
    parser.add_argument("--resolution", type=int, default=2048)
    args = parser.parse_args()
    if args.stage == "prepare":
        prepare()
    elif args.stage == "native":
        native(args.method)
    elif args.stage == "mirror":
        mirror(args.method in ("optimized", "aggressive", "exhaustive"), args.method in ("aggressive", "exhaustive"), args.method == "exhaustive")
    elif args.stage == "craft":
        craft()
    elif args.stage == "optimize":
        optimize(args.method in ("aggressive", "exhaustive"), args.method == "exhaustive")
    elif args.stage == "share":
        original_share()
    elif args.stage == "pairs":
        share_candidates()
    else:
        evaluate(args.method, args.resolution)


if __name__ == "__main__":
    main()
