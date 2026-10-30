"""Replay Agent-approved whole-component UV experiments through public tools."""

import argparse
import json
import time
from collections import defaultdict
from pathlib import Path

import numpy as np

from reseam.agent_tools.experiment import Experiment, write_step_report
from reseam.agent_tools.reparameterize import boundary_loop
from reseam.agent_tools.state import load_snapshot, read_object, write_json
from reseam.mesh import chart_labels

ROOT = Path(__file__).resolve().parents[1]
DEV = ROOT / "artifacts/dragon_reuse_development"
PARENT = DEV / "geometry_restored_regions01/region_2/snapshot/snapshot.json"
PROFILE: dict[str, object] = {
    "resolution": 1024, "gap_pixels": 16, "border_pixels": 8,
    "normal_angle_degrees": 180, "correspondence_fraction": 0.1,
    "quality_constraints": False, "balance_density": True,
}
EXTRA_PROTECTED = [7883, 7879, 7743, 7882, 7742, 7954]
ORDER = ["wing_membrane", "wing_support", "wing_rim", "wing_upper", "wing_strip",
         "wing_tip", "wing_finger_a", "wing_finger_b", "thigh", "arm", "foot_sole",
         "ankle_inner", "ankle_outer", "horn_outer", "horn_base", "horn_side", "horn_upper"]


def obj(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        raise TypeError("OBJECT_REQUIRED")
    return value


def ids(value: object) -> list[int]:
    if not isinstance(value, list) or any(type(item) is not int for item in value):
        raise TypeError("INTEGER_LIST_REQUIRED")
    return value


def topology(faces: list[int], mesh_faces: np.ndarray, labels: np.ndarray) -> dict[str, object]:
    selected = mesh_faces[faces]
    edges: dict[tuple[int, int], list[int]] = defaultdict(list)
    for index, face in enumerate(selected):
        for corner in range(3):
            edges[tuple(sorted((int(face[corner]), int(face[(corner + 1) % 3]))))].append(index)
    parent = list(range(len(selected)))

    def root(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    for members in edges.values():
        for member in members[1:]:
            parent[root(member)] = root(members[0])
    try:
        loop = boundary_loop(selected)
        disk: object = True
    except ValueError as error:
        loop, disk = [], str(error)
    return {"faces": len(faces), "old_uv_charts": len(np.unique(labels[faces])),
            "geometric_components": len({root(i) for i in range(len(faces))}),
            "euler_characteristic": len(np.unique(selected)) - len(edges) + len(selected),
            "nonmanifold_edges": sum(len(value) > 2 for value in edges.values()),
            "boundary_edges": sum(len(value) == 1 for value in edges.values()),
            "disk": disk, "boundary_vertex_ids": loop}


def corner_identity(vertex: int, faces: list[int], mesh_faces: np.ndarray) -> list[int]:
    for face in faces:
        matches = np.flatnonzero(mesh_faces[face] == vertex)
        if len(matches):
            return [face, int(matches[0])]
    raise ValueError("BOUNDARY_VERTEX_OUTSIDE_COMPONENT")


def anchors(first: list[int], second: list[int], vertices: np.ndarray,
            mesh_faces: np.ndarray, transform: np.ndarray | None = None) -> list[dict[str, object]]:
    """Three boundary extremes; heuristic initialization, not semantic ground truth."""
    a = np.asarray(boundary_loop(mesh_faces[first]), dtype=np.int64)
    b = np.asarray(boundary_loop(mesh_faces[second]), dtype=np.int64)
    points = vertices[a]
    start = int(np.argmin(points[:, 1]))
    far = int(np.argmax(np.linalg.norm(points - points[start], axis=1)))
    edge = points[far] - points[start]
    third = int(np.argmax(np.linalg.norm(np.cross(points - points[start], edge), axis=1)))
    chosen = sorted({start, far, third})
    if len(chosen) != 3:
        raise ValueError("THREE_BOUNDARY_EXTREMES_UNAVAILABLE")
    reflected = vertices[a[chosen]].copy()
    if transform is None:
        reflected[:, 0] *= -1
    else:
        reflected = reflected @ transform[:, :3].T + transform[:, 3]
    target = np.argmin(np.linalg.norm(reflected[:, None] - vertices[b][None], axis=2), axis=1)
    if len(set(map(int, target))) != 3:
        raise ValueError("MIRROR_EXTREME_ANCHORS_COLLIDE")
    return [{"first": corner_identity(int(a[index]), first, mesh_faces),
             "second": corner_identity(int(b[int(match)]), second, mesh_faces),
             "label": f"heuristic_boundary_extreme_{number}"}
            for number, (index, match) in enumerate(zip(chosen, target, strict=True))]


def manifest() -> dict[str, object]:
    snapshot = load_snapshot(PARENT)
    surface = snapshot.state.surface
    original = read_object(DEV / "agent_decisions.json")
    semantic = read_object(DEV / "semantic_regions_v2/semantic_proposals_v2.json")
    pairs = {str(obj(pair)["name"]): obj(pair) for pair in original["pairs"]}  # type: ignore[union-attr]
    components: list[dict[str, object]] = []
    labels = chart_labels(surface)
    for number, name in enumerate(ORDER, 1):
        pair = pairs[name]
        first, second = ids(pair["first_faces"]), ids(pair["second_faces"])
        components.append({"number": number, "name": name, "first_faces": first, "second_faces": second,
                           "first_seam_edges": [], "second_seam_edges": [],
                           "boundary_anchors": anchors(first, second, surface.mesh.vertices, surface.mesh.faces),
                           "anchor_source": "heuristic boundary extremes matched by reflected position; Agent review pending",
                           "first_topology": topology(first, surface.mesh.faces, labels),
                           "second_topology": topology(second, surface.mesh.faces, labels),
                           "decision": "attempt whole natural component; no per-face exclusions"})
    semantic_pairs = {str(obj(pair)["proposal_id"]): obj(pair) for pair in semantic["proposals"]}  # type: ignore[union-attr]
    proximal = semantic_pairs["repeated_dorsal_spikes_proximal_to_middle"]
    distal = semantic_pairs["repeated_dorsal_spikes_middle_to_distal"]
    small = semantic_pairs["repeated_dorsal_spikes_middle_to_small"]
    initial = np.asarray(obj(proximal["alignment_hint"])["first_to_second_initial_3x4"], dtype=float)
    middle_distal = np.eye(4)
    middle_distal[:3] = np.asarray(obj(distal["alignment_hint"])["first_to_second_initial_3x4"], dtype=float)
    middle_small = np.eye(4)
    middle_small[:3] = np.asarray(obj(small["alignment_hint"])["first_to_second_initial_3x4"], dtype=float)
    for name, first, second, transform in (
        ("dorsal_proximal_middle", ids(proximal["first_faces"]), ids(proximal["second_faces"]), initial),
        ("dorsal_distal_small", ids(distal["second_faces"]), ids(small["second_faces"]),
         (middle_small @ np.linalg.inv(middle_distal))[:3]),
    ):
        components.append({"number": len(components) + 1, "name": name, "first_faces": first,
            "second_faces": second, "first_seam_edges": [], "second_seam_edges": [],
            "first_to_second": transform.tolist(),
            "boundary_anchors": anchors(first, second, surface.mesh.vertices, surface.mesh.faces, transform),
            "anchor_source": "heuristic boundary extremes matched by prior advisory similarity; Agent review pending",
            "first_topology": topology(first, surface.mesh.faces, labels),
            "second_topology": topology(second, surface.mesh.faces, labels),
            "decision": "Lower-confidence whole repeated spike; preserve failure; no per-face exclusions"})
    protected = sorted(set(ids(original["protected_faces"]) + EXTRA_PROTECTED))
    return {"parent_snapshot": str(PARENT), "parent_state_hash": snapshot.state_hash,
            "asset_id": snapshot.asset_id, "geometry_hash": read_object(PARENT)["geometry_hash"],
            "profile": PROFILE, "components": components, "protected_faces": protected,
            "reference_decisions": str(DEV / "agent_decisions.json"),
            "reference_semantic_proposals": str(DEV / "semantic_regions_v2/semantic_proposals_v2.json"),
            "whole_region_topology": {str(obj(pair)["proposal_id"]): {
                side: topology(ids(obj(pair)[side + "_faces"]), surface.mesh.faces, labels)
                for side in ("first", "second")} for pair in semantic["proposals"]},  # type: ignore[union-attr]
            "agent_decision": {
                "selector": "calling root Codex Agent", "decision_source": "explicit parent message 2026-10-30",
                "wing": "Preserve eight geometrically disconnected natural components; do not coordinate-weld.",
                "foot": "Previous white-claw expansion motivates retaining entire foot_top and toe patches exclusive."
                        " Share whole sole and inner/outer ankle patches first.",
                "protected_features": "Original1529 plus6 recorded toe-root faces; no six-face excision in shared patch.",
                "priority": "membrane smoke, then fragmented wing_support, then all remaining approved components",
                "visual_review": "Root Agent viewed full source front, source left and numbered front; part outlines plausible."
                                 " Preserve blue/white feature boundaries, claw patches and horn tips. Not human acceptance.",
                "visual_evidence": [str(DEV / "components_selection01/source_basecolor/front.png"),
                                    str(DEV / "components_selection01/source_basecolor/left.png"),
                                    str(DEV / "components_selection01/source_regions/front.png")],
            },
            "exclusive_natural_components": ["foot_top", "toe_a", "toe_b", "toe_c"],
            "symmetry_score": None, "symmetry_score_reason": "No calibrated similarity percentage; geometry is initialization.",
            "artist_reviewed": False}


def prepare(output: Path) -> None:
    plan = manifest()
    experiment = Experiment(ROOT, output, {"purpose": "component selection and source views", "plan": plan}, freeze=False)
    write_json(output / "manifest.json", plan)
    parent = str(PARENT)
    for component in plan["components"]:  # type: ignore[union-attr]
        item = obj(component)
        result = experiment.call("region.define", {"snapshot": parent, "name": item["name"],
            "faces": ids(item["first_faces"]) + ids(item["second_faces"]), "rationale": plan["agent_decision"]["priority"],  # type: ignore[index]
            "evidence": str(plan["reference_decisions"]), "timeout_seconds": 300},
            f"region_{int(str(item['number'])):02d}", reason="Number complete natural part across old UV seams.")
        if isinstance(result.get("snapshot"), str):
            parent = str(result["snapshot"])
    for mode in ("basecolor", "regions", "checker"):
        experiment.call("view.render", {"snapshot": parent, "mode": mode, "size": 768,
                         "seams": mode != "basecolor", "timeout_seconds": 300}, "source_" + mode,
                        reason="Fixed model source appearance and numbered component selection before UV editing.")
    plan["numbered_snapshot"] = parent
    write_json(output / "manifest.json", plan)
    write_json(output / "summary.json", experiment.summary())
    write_step_report(output)


def run_geometry(output: Path, selection: Path, names: list[str], freeze: bool) -> None:
    plan = read_object(selection)
    profile = obj(plan.get("profile", PROFILE))
    components = [obj(item) for item in plan["components"] if not names or obj(item)["name"] in names]  # type: ignore[union-attr]
    if len(components) != len(names) and names:
        raise ValueError("UNKNOWN_OR_DUPLICATE_COMPONENT_NAME")
    experiment = Experiment(ROOT, output, {"purpose": "whole natural component reparameterization and reuse",
        "selection": str(selection.resolve()), "plan": plan, "components": [item["name"] for item in components],
        "methods": ["no_share", "optcuts_no_share", "geometry_auto", "agent_paired"],
        "formal_comparison": freeze, "artist_reviewed": False}, freeze=freeze)
    current = str(plan["parent_snapshot"])
    rows: list[dict[str, object]] = []
    layouts: dict[str, object] = {}
    for component in components:
        name = str(component["name"])
        for side in ("first", "second"):
            result = experiment.call("uv.reparameterize", {"snapshot": current, "faces": component[side + "_faces"],
                "seam_edges": component[side + "_seam_edges"], "backend": "optcuts", "profile": profile,
                "initial_uv": plan.get("optcuts_initial_uv", "existing"),
                "timeout_seconds": 300}, f"optcuts_{name}_{side}",
                reason="Rebuild complete natural part from geometry, resetting fragmented old UV; explicit OptCuts.")
            print(json.dumps({"step": f"optcuts_{name}_{side}", "status": result.get("status")}), flush=True)
            if isinstance(result.get("snapshot"), str):
                current = str(result["snapshot"])
                if isinstance(result.get("report"), str):
                    layouts[f"{name}_{side}"] = read_object(Path(str(result["report"])))
            else:
                rows.append({"name": name, "method": "optcuts_no_share", "side": side,
                             "status": result.get("status"), "result": result})
        write_json(output / "matrix.json", rows)
    optcuts = current
    states: dict[str, object] = {"no_share": plan["parent_snapshot"], "optcuts_no_share": optcuts,
                               "uniform_profile": profile}
    write_json(output / "layouts.json", layouts)
    for method in ("geometry_auto", "agent_paired"):
        current = optcuts
        for component in components:
            name = str(component["name"])
            request: dict[str, object] = {"snapshot": current, "first_faces": component["first_faces"],
                "second_faces": component["second_faces"], "plane_normal": [1, 0, 0], "plane_offset": 0,
                "profile": profile, "timeout_seconds": 300, "mode": "auto" if method == "geometry_auto" else "paired_domain"}
            if method == "agent_paired":
                first_layout = obj(layouts.get(f"{name}_first", {}))
                second_layout = obj(layouts.get(f"{name}_second", {}))
                request.update(first_seam_edges=first_layout.get("result_seam_edges", component["first_seam_edges"]),
                    second_seam_edges=second_layout.get("result_seam_edges", component["second_seam_edges"]),
                    boundary_anchors=component["boundary_anchors"], master_member="first", domain="master_uv",
                    allow_convex_fallback=True, protected_faces=plan["protected_faces"], refine_iterations=0)
            if "first_to_second" in component:
                request["first_to_second"] = component["first_to_second"]
                if method == "geometry_auto":
                    request["mode"] = "similarity"
            result = experiment.call("reuse.check_pair", request, f"{method}_{name}_check",
                reason=("Agent-approved natural component, ordered boundary anchors, coherent domain; no per-face exclusions."
                        if method == "agent_paired" else "Same OptCuts start and component pool, automatic geometry decision."),
                decision=obj(plan["agent_decision"]) if method == "agent_paired" else None)
            row: dict[str, object] = {"name": name, "method": method, "check": result, "accepted": False}
            if isinstance(result.get("proposal"), str):
                applied = experiment.call("reuse.apply", {"snapshot": current, "proposal": result["proposal"],
                    "profile": profile, "timeout_seconds": 300}, f"{method}_{name}_apply",
                    reason="Apply only checked complete part; immutable child snapshot.")
                row["apply"] = applied
                if isinstance(applied.get("snapshot"), str):
                    current = str(applied["snapshot"])
                    row.update(accepted=True, snapshot=current)
            rows.append(row)
            print(json.dumps({"method": method, "name": name, "accepted": row["accepted"],
                              "status": result.get("status")}), flush=True)
            write_json(output / "matrix.json", rows)
            states[method] = current
            write_json(output / "states.json", states)
        for mode in ("groups", "checker"):
            experiment.call("view.render", {"snapshot": current, "mode": mode, "size": 768,
                "timeout_seconds": 300}, f"{method}_{mode}", reason="Actual current UV and shared part evidence.")
        experiment.call("uv.inspect", {"snapshot": current, "profile": profile, "timeout_seconds": 300},
                        f"{method}_inspect", reason="Actual independent tiles and covered surface, not declared group area.")
    states["selection"] = str(selection.resolve())
    states["geometry_rows"] = str((output / "matrix.json").resolve())
    write_json(output / "states.json", states)
    write_json(output / "summary.json", experiment.summary())
    write_step_report(output)


def target_preview(output: Path, states_path: Path, freeze: bool) -> None:
    states = read_object(states_path)
    selection = read_object(Path(str(states["selection"])))
    candidate = str(states["agent_paired"])
    profile = obj(states.get("uniform_profile", PROFILE))
    snapshot = load_snapshot(Path(candidate))
    experiment = Experiment(ROOT, output, {"purpose": "Freeze source-derived target and review BEFORE baking",
        "states": str(states_path.resolve()), "artist_reviewed": False}, freeze=freeze)
    source_topology = obj(snapshot.metadata["topology_restoration"])["original_topology"]
    target = experiment.call("material.target", {"snapshot": candidate, "policy": "symmetrize",
        "protected_faces": selection["protected_faces"], "geometry_profile": profile,
        "original_topology": source_topology, "timeout_seconds": 300}, "target",
        reason="Freeze original-source appearance through checked domains; protect original head and entire claw-bearing patches.")
    if "target" not in target:
        write_json(output / "summary.json", experiment.summary())
        raise ValueError("TARGET_FREEZE_FAILED")
    for mode in ("basecolor", "groups", "checker"):
        request: dict[str, object] = {"snapshot": candidate, "mode": mode, "size": 768, "timeout_seconds": 300}
        if mode == "basecolor":
            request["target"] = target["target"]
        experiment.call("view.render", request, "full_" + mode,
            reason="Root Agent review source/target/difference before producing any baked candidate texture.")
    for region in ("wing", "foot", "horn"):
        faces = sorted({face for item in selection["components"] if region in str(obj(item)["name"])  # type: ignore[union-attr]
                        for side in ("first_faces", "second_faces") for face in ids(obj(item)[side])})
        if faces:
            experiment.call("view.render", {"snapshot": candidate, "mode": "basecolor", "size": 768,
                "target": target["target"], "selected_faces": faces, "timeout_seconds": 300}, region + "_target",
                reason="Local feature review; source and target images, not candidate reconstruction.")
    groups = [group.group_id for group in snapshot.state.groups]
    if groups:
        unshared = experiment.call("reuse.apply", {"snapshot": candidate, "action": "unshare", "group_ids": groups,
            "profile": profile, "timeout_seconds": 300}, "same_layout_unshared",
            reason="Matched layout control: preserve component UV shapes, separate sharing members and repack.")
        if isinstance(unshared.get("snapshot"), str):
            states["same_layout_unshared"] = unshared["snapshot"]
    states.update(target=target["target"], protected_faces=selection["protected_faces"], source_topology=source_topology,
                  target_preview=str(output.resolve()))
    write_json(output / "states.json", states)
    write_json(output / "summary.json", experiment.summary())
    write_step_report(output)


def color_comparison(output: Path, states_path: Path, sizes: list[int], review: str, freeze: bool) -> None:
    if not review:
        raise ValueError("ROOT_AGENT_TARGET_REVIEW_NOTE_REQUIRED")
    states = read_object(states_path)
    base_profile = obj(states.get("uniform_profile", PROFILE))
    experiment = Experiment(ROOT, output, {"purpose": "Real component BaseColor comparison",
        "states": str(states_path.resolve()), "root_agent_target_review": review,
        "resolutions": sizes, "scope": "basecolor_reuse", "artist_reviewed": False,
        "full_pbr_verified": False, "gpu_verified": False}, freeze=freeze)
    rows: list[dict[str, object]] = []
    for size in sizes:
        profile = {**base_profile, "resolution": size, "gap_pixels": size // 64, "border_pixels": size // 128}
        for method in ("no_share", "optcuts_no_share", "geometry_auto", "agent_paired", "same_layout_unshared"):
            if method not in states:
                continue
            label = f"{method}_{size}"
            row: dict[str, object] = {"method": method, "resolution": size, "target": states["target"]}
            packed = experiment.call("uv.pack", {"snapshot": states[method], "profile": profile, "timeout_seconds": 300},
                label + "_pack", reason="Identical normalized padding/density rules for all independent branches.")
            if isinstance(packed.get("snapshot"), str):
                current = str(packed["snapshot"])
                row["snapshot"] = current
                inspect = experiment.call("uv.inspect", {"snapshot": current, "profile": profile, "timeout_seconds": 300},
                    label + "_inspect", reason="Actual tile and area metrics under current packing.")
                row["inspect"] = inspect
                bake = experiment.call("material.bake", {"snapshot": current, "mode": "basecolor_reuse",
                    "target": states["target"], "resolution": size, "padding_pixels": size // 128,
                    "geometry_profile": profile, "timeout_seconds": 300}, label + "_bake",
                    reason="Actual source-derived BaseColor raster and complete mip chain; same frozen target.")
                row["bake"] = bake
                if isinstance(bake.get("bake"), str):
                    validation = experiment.call("quality.validate", {"snapshot": current, "scope": "basecolor_reuse",
                        "bake": bake["bake"], "mip_levels": [0, 1, 2], "geometry_profile": profile,
                        "render_size": 96, "timeout_seconds": 300}, label + "_validate",
                        reason="Preserve numerical quality rejection independently of appearance-first Agent review.")
                    row["validation"] = validation
                    if isinstance(validation.get("validation"), str):
                        exported = experiment.call("asset.export", {"snapshot": current, "scope": "basecolor_reuse",
                            "bake": bake["bake"], "validation": validation["validation"],
                            "mode": "accepted" if validation.get("quality_accepted") is True else "diagnostic",
                            "timeout_seconds": 300}, label + "_export", reason="Traceable actual texture and full fixed-geometry GLB.")
                        row["export"] = exported
                    texture = Path(str(bake["bake"])).parent / "texture/BaseColor_mip0.png"
                    experiment.call("view.render", {"snapshot": current, "mode": "basecolor", "texture": str(texture),
                        "size": 768, "timeout_seconds": 300}, label + "_views",
                        reason="Render ACTUAL newly produced texture, current UV, same full-model cameras.")
            else:
                row["pack_failure"] = packed
            rows.append(row)
            write_json(output / "matrix.json", rows)
            write_json(output / "summary.json", experiment.summary())
            write_step_report(output)
            print(json.dumps({"method": method, "resolution": size,
                              "baked": "bake" in row, "validation": row.get("validation")}), flush=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase", choices=("prepare", "geometry", "target", "color"), required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--selection", type=Path)
    parser.add_argument("--components", default="")
    parser.add_argument("--development", action="store_true")
    parser.add_argument("--states", type=Path)
    parser.add_argument("--sizes", default="512,2048")
    parser.add_argument("--target-review", default="")
    args = parser.parse_args()
    started = time.monotonic()
    if args.phase == "prepare":
        prepare(args.output.resolve())
    elif args.phase == "geometry":
        if args.selection is None:
            raise ValueError("SELECTION_MANIFEST_REQUIRED")
        run_geometry(args.output.resolve(), args.selection.resolve(),
                     [name for name in args.components.split(",") if name], not args.development)
    elif args.phase == "target":
        if args.states is None:
            raise ValueError("STATES_REQUIRED")
        target_preview(args.output.resolve(), args.states.resolve(), not args.development)
    else:
        if args.states is None:
            raise ValueError("STATES_REQUIRED")
        color_comparison(args.output.resolve(), args.states.resolve(), [int(value) for value in args.sizes.split(",")],
                         args.target_review, not args.development)
    print(json.dumps({"output": str(args.output.resolve()), "elapsed_seconds": time.monotonic() - started}), flush=True)


if __name__ == "__main__":
    main()
