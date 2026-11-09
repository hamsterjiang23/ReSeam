"""Reproducible stage runner for the completed dragon, through public Agent tools."""

from __future__ import annotations

import argparse
import shutil
import time
from pathlib import Path

import numpy as np

from reseam.agent_tools.runner import run_tool
from reseam.agent_tools.state import file_hash, load_snapshot, read_object, save_snapshot, write_json
from reseam.budget_state import BudgetState
from reseam.mesh import make_mesh, state_from_corner_uv

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "artifacts/dragon_paper_baselines/round01"


def call(base: Path, label: str, tool: str, request: dict[str, object]) -> dict[str, object]:
    destination = base / label
    if (destination / "result.json").exists():
        if read_object(destination / "request.json") != request:
            raise ValueError("CACHED_REQUEST_CHANGED_USE_NEW_LABEL:" + label)
        return read_object(destination / "result.json")
    request_path = base / "requests" / (label.replace("/", "_") + ".json")
    write_json(request_path, request)
    start = time.monotonic()
    result = run_tool(tool, request_path, destination)
    write_json(destination / "receipt.json", {"tool": tool, "request": str(request_path),
               "seconds": time.monotonic() - start, "status": result.get("status"),
               "reason": result.get("reason")})
    print(label, result.get("status"), result.get("reason", ""), flush=True)
    return result


def initialize(base: Path) -> None:
    if (base / "parent/snapshot.json").exists():
        return
    latest = ROOT / "artifacts/meshquery_v1/midplane_cleanup01/seed29/pack/snapshot/snapshot.json"
    parent = load_snapshot(latest)
    source = ROOT / "docs/assets/dragon-midplane-cleanup/dragon.glb"
    inputs = base / "inputs"
    inputs.mkdir(parents=True, exist_ok=False)
    frozen = inputs / "completed_dragon.glb"
    shutil.copy2(source, frozen)
    metadata: dict[str, object] = {"reference_glb": str(frozen), "source_uv_present": True,
        "source_manifest": [{"path": str(frozen), "sha256": file_hash(frozen)}],
        "baseline_parent": {"path": str(latest), "state_hash": parent.state_hash},
        "source_material_scope": {"scope": "finished BaseColor appearance; no additional PBR claim"},
        "polygon_topology": {"polygons": parent.state.surface.mesh.faces.tolist(),
            "triangle_to_polygon": list(range(len(parent.state.surface.mesh.faces))),
            "source": "explicit original triangles; no inferred quads"}}
    state = parent.state.copy()
    state.texture = None
    state.evidence.clear()
    fixed = save_snapshot(state, base / "parent", metadata)
    unshared = BudgetState(state.surface.copy(), version=state.version)
    save_snapshot(unshared, base / "unshared_parent", metadata, fixed)
    mesh = make_mesh(np.array([[-1., -1., 0.], [1., -1., 0.], [1., 1., 0.], [-1., 1., 0.]]),
                     np.array([[0, 1, 2], [0, 2, 3]]))
    surface = state_from_corner_uv(mesh, mesh.vertices[mesh.faces, :2] * .25 + .5, corner_ids=mesh.faces)
    save_snapshot(BudgetState(surface), base / "smoke_parent", {})
    write_json(base / "protocol.json", {"source": str(source), "source_sha256": file_hash(source),
        "parent": str(fixed.root / "snapshot.json"), "state_hash": fixed.state_hash,
        "faces": len(state.surface.mesh.faces), "reference": "completed 2048 BaseColor frozen before algorithms",
        "resolution": 2048, "padding_pixels": 8, "gap_pixels": 8, "border_pixels": 8,
        "algorithm_budget_seconds": 2400, "tool_budget_seconds": 300, "scope": "UV+BaseColor",
        "methods": ["flatten_anything", "optcuts", "xatlas", "blender_smart", "partuv",
                    "meshquery_project_implementation", "reseam_shared", "reseam_unshared"],
        "paper": "https://arxiv.org/html/2610.02507v1", "paper_packing_margin": .001,
        "packing_difference": "production comparison uses fixed 8px gaps/borders, not paper .001 margin",
        "seed_policy": "OptCuts0; PartUV42; deterministic xatlas/Smart repeat once; FAM17",
        "historical_reseam_cost": "precomputed layout, prior creation costs not zero; no equal-budget claim",
        "artist_reviewed": False, "gpu_material_verified": False})


def adopt_native(base: Path, previous: Path) -> None:
    """Import immutable native outputs only after checking the exact common geometry."""
    initialize(base)
    fixed = load_snapshot(base / "parent")
    root = base / "methods"
    root.mkdir(exist_ok=True)
    for method in ("xatlas", "blender_smart", "optcuts", "partuv", "flatten_anything"):
        prior = previous / "methods" / method
        if not (prior / "result.json").exists() or (root / method).exists():
            continue
        result = read_object(prior / "result.json")
        if "snapshot" not in result:
            continue
        source = load_snapshot(Path(str(result["snapshot"])))
        np.testing.assert_array_equal(source.state.surface.mesh.vertices, fixed.state.surface.mesh.vertices)
        np.testing.assert_array_equal(source.state.surface.mesh.faces, fixed.state.surface.mesh.faces)
        if source.state.groups:
            raise ValueError("NATIVE_BASELINE_UNEXPECTED_SHARING")
        output = root / method
        output.mkdir()
        child = save_snapshot(source.state.copy(), output / "snapshot", {
            **fixed.metadata, "baseline_method": method, "requires_rebake": True,
            "native_cached_from": str(prior), "native_snapshot_hash": source.state_hash,
            "baseline_settings": source.metadata.get("baseline_settings", {})}, fixed)
        report = {**result, "snapshot": str(child.root / "snapshot.json"), "state_hash": child.state_hash,
                  "asset_id": child.asset_id, "native_cached_from": str(prior),
                  "native_cost_seconds": result.get("wall_seconds"), "cache_geometry_verified": True}
        write_json(output / "result.json", report)
        write_json(output / "native_cache.json", {"source": str(prior), "source_snapshot_hash": source.state_hash,
            "source_result_sha256": file_hash(prior / "result.json"), "same_vertices_and_faces": True,
            "cost_included_not_zero": result.get("wall_seconds"), "reason": "evaluation pipeline fixed, native algorithms unchanged"})
        print("adopt", method, flush=True)


def algorithm(base: Path, method: str, smoke: bool) -> None:
    label = ("smoke/" if smoke else "methods/") + method
    request: dict[str, object] = {"snapshot": str(base / ("smoke_parent" if smoke else "parent")),
        "method": method, "timeout_seconds": 120 if smoke else 2400}
    if smoke and method == "flatten_anything":
        request.update(points=256, iterations=10)
    call(base, label, "uv.baseline", request)


def finish(base: Path, method: str, resolution: int) -> None:
    if method == "reseam_shared":
        snapshot = str(base / "parent/snapshot.json")
    elif method == "reseam_unshared":
        snapshot = str(base / "unshared_parent/snapshot.json")
    elif method == "meshquery_project":
        record = read_object(base / "meshquery/selected.json")
        snapshot = str(record["snapshot"])
    else:
        result = read_object(base / "methods" / method / "result.json")
        if result.get("execution_success") is not True:
            print(method, "ALGORITHM_FAILED", flush=True)
            return
        snapshot = str(result["snapshot"])
    native = load_snapshot(Path(snapshot))
    profile: dict[str, object] = {"resolution": resolution, "gap_pixels": 8, "border_pixels": 8,
        "quality_constraints": False, "balance_density": False,
        "correspondence_fraction": .1, "normal_angle_degrees": 180,
        "preserve_source_uv": {"snapshot": snapshot, "state_hash": native.state_hash}}
    label = f"evaluation/{method}_{resolution}"
    call(base, label + "/native_views", "view.render", {"snapshot": snapshot, "mode": "checker",
        "size": 768, "uv_labels": "owners", "timeout_seconds": 300})
    packed = call(base, label + "/pack", "uv.pack", {"snapshot": snapshot,
        "backend": "blender_concave", "profile": profile, "safety_pixels": 2,
        "seed": 0, "timeout_seconds": 300})
    if "snapshot" not in packed:
        return
    current = str(packed["snapshot"])
    call(base, label + "/inspect", "uv.inspect", {"snapshot": current, "profile": profile,
                                                  "timeout_seconds": 300})
    target = call(base, label + "/target", "material.target", {"snapshot": current,
        "policy": "source_original", "geometry_profile": profile, "timeout_seconds": 300})
    if target.get("execution_success") is not True:
        return
    baked = call(base, label + "/bake", "material.bake", {"snapshot": current,
        "mode": "basecolor_reuse", "target": target["target"], "resolution": resolution,
        "padding_pixels": 8, "geometry_profile": profile, "timeout_seconds": 300})
    call(base, label + "/uv_views", "view.render", {"snapshot": current, "mode": "checker",
        "size": 1024, "uv_labels": "owners", "timeout_seconds": 300})
    if baked.get("execution_success") is not True:
        return
    validated = call(base, label + "/validate", "quality.validate", {"snapshot": current,
        "bake": baked["bake"], "geometry_profile": profile, "mip_levels": [0, 1, 2],
        "render_size": 96, "timeout_seconds": 300})
    if "validation" in validated:
        call(base, label + "/export", "asset.export", {"snapshot": current,
            "bake": baked["bake"], "validation": validated["validation"],
            "mode": "diagnostic", "timeout_seconds": 300})
    texture = base / label / "bake/texture/BaseColor_mip0.png"
    call(base, label + "/color_views", "view.render", {"snapshot": current, "mode": "basecolor",
        "texture": str(texture), "size": 1024, "timeout_seconds": 300,
        "cameras": [{"name": "front", "azimuth": 0, "elevation": 0},
                    {"name": "oblique", "azimuth": 35, "elevation": 15},
                    {"name": "back", "azimuth": 180, "elevation": 0},
                    {"name": "bottom", "azimuth": 0, "elevation": -60}]})


def meshquery_repair(base: Path, parent: Path, initial: dict[str, object]) -> dict[str, object]:
    """Agent's geometry-only revision: connect the two rims of each rejected annulus."""
    snapshot = load_snapshot(parent)
    mesh = snapshot.state.surface.mesh
    record = read_object(Path(str(initial["plan"])))
    topology = record["topology"]
    if not isinstance(topology, dict) or not isinstance(topology.get("charts"), list):
        raise TypeError("PLAN_TOPOLOGY_REQUIRED")
    edges: set[int] = set()
    decisions: list[dict[str, object]] = []
    for number, chart in enumerate(topology["charts"]):
        if not isinstance(chart, dict) or chart.get("disk") is True:
            continue
        if chart.get("boundary_loops") != 2 or chart.get("euler") != 0:
            raise ValueError("AGENT_REVISION_ONLY_HANDLES_OBSERVED_TWO_RIM_ANNULI")
        faces = list(chart["faces"])
        selected = set(faces)
        boundary = [i for i, incident in enumerate(mesh.edge_faces)
                    if len(incident) == 1 and incident[0][0] in selected]
        vertex_edges: dict[int, set[int]] = {}
        for edge_id in boundary:
            for vertex in mesh.edges[edge_id]:
                vertex_edges.setdefault(vertex, set()).add(edge_id)
        remaining = set(boundary)
        loops: list[list[int]] = []
        while remaining:
            pending = [min(remaining)]
            loop: set[int] = set()
            while pending:
                edge_id = pending.pop()
                if edge_id in loop:
                    continue
                loop.add(edge_id)
                remaining.discard(edge_id)
                for vertex in mesh.edges[edge_id]:
                    pending.extend(vertex_edges[vertex] - loop)
            loops.append(sorted(loop))
        if len(loops) != 2:
            raise ValueError("BOUNDARY_LOOP_EVIDENCE_CHANGED")
        first = mesh.vertices[np.asarray([mesh.edges[i] for i in loops[0]])].mean(axis=1)
        second = mesh.vertices[np.asarray([mesh.edges[i] for i in loops[1]])].mean(axis=1)
        distance = np.linalg.norm(first[:, None] - second[None], axis=2)
        # Prefer the back of the model (+Z is the front camera); this is an
        # explicit Agent policy, not an asserted paper formula or learned score.
        posterior = (first[:, None, 2] + second[None, :, 2]) * .5
        score = distance + .25 * (posterior - posterior.min())
        a, b = np.unravel_index(np.argmin(score), score.shape)
        anchors = [loops[0][int(a)], loops[1][int(b)]]
        result = call(base, f"meshquery/rim_bridge_{number}", "seam.select", {
            "snapshot": str(parent), "operator": "geodesic",
            "arguments": {"waypoint_edges": anchors, "face_ids": faces}, "timeout_seconds": 300})
        if "selection" not in result:
            raise ValueError("RIM_BRIDGE_SELECTION_FAILED:" + str(result))
        selection = read_object(Path(str(result["selection"])))
        edge_ids = selection.get("edge_ids")
        if not isinstance(edge_ids, list) or any(type(value) is not int for value in edge_ids):
            raise TypeError("SELECTION_EDGE_IDS_REQUIRED")
        edges.update(edge_ids)
        decisions.append({"region_faces": faces, "observed_euler": 0, "observed_boundary_loops": 2,
                          "anchors": anchors, "reason": "open annulus along short posterior rim-to-rim path"})
    write_json(base / "meshquery/revision_decision.json", {"observations": decisions,
        "hypothesis": "one bridge per annulus converts it to a disk without splitting off tiny islands",
        "model": "current Codex Agent", "exact_serving_model_id": None, "token_cost": None})
    return call(base, "meshquery/repaired_plan", "seam.submit_plan", {"snapshot": str(parent),
        "edge_ids": sorted(edges), "faces": list(range(len(mesh.faces))), "refine": False,
        "parent_plan": initial["plan"], "note": "Agent feedback revision: four annular regions need rim bridges",
        "timeout_seconds": 300})


def meshquery(base: Path) -> None:
    parent = base / "unshared_parent/snapshot.json"
    snapshot = load_snapshot(parent)
    common: dict[str, object] = {"snapshot": str(parent), "timeout_seconds": 300}
    call(base, "meshquery/observe", "view.render", {**common, "mode": "basecolor", "size": 768,
        "cameras": [{"name": "oblique", "azimuth": 35, "elevation": 15},
                    {"name": "bottom", "azimuth": 0, "elevation": -60}]})
    index = call(base, "meshquery/index", "mesh.index", {**common, "ao_rays": 8})
    call(base, "meshquery/components", "mesh.query", {**common, "index": index["index"],
        "sql": "SELECT * FROM component ORDER BY id"})
    call(base, "meshquery/propose", "region.propose", {**common, "index": index["index"],
        "backend": "geometry", "crease_degrees": [45, 70]})
    # Agent's first hypothesis: natural component boundaries already provide
    # large cuts. Discard all old UV seams and inspect the actual cut topology.
    plan = call(base, "meshquery/natural_plan", "seam.submit_plan", {**common,
        "edge_ids": [], "faces": list(range(len(snapshot.state.surface.mesh.faces))), "refine": False,
        "note": "Agent hypothesis: natural disconnected surfaces first; do not inherit completed UV seams"})
    if "plan" not in plan:
        return
    if plan.get("status") == "REJECTED":
        plan = meshquery_repair(base, parent, plan)
    if plan.get("status") == "REJECTED":
        return
    revision = "repaired_" if "repaired_plan" in str(plan["plan"]) else "natural_"
    call(base, "meshquery/" + revision + "preview", "seam.preview", {
        **common, "plan": plan["plan"], "size": 768})
    profile: dict[str, object] = {"resolution": 2048, "gap_pixels": 8, "border_pixels": 8,
        "quality_constraints": False, "balance_density": False, "correspondence_fraction": .1,
        "normal_angle_degrees": 180}
    for backend in ("blender_minimum_stretch", "blender_abf", "blender_lscm"):
        result = call(base, "meshquery/" + revision + backend, "seam.apply", {**common,
            "plan": plan["plan"], "backend": backend, "profile": profile})
        if "snapshot" in result:
            write_json(base / "meshquery/selected.json", {"snapshot": result["snapshot"],
                "backend": backend, "plan": plan["plan"], "implementation": "project, not author code",
                "agent_decision": "first natural-boundary plan whose actual UV passes basic legality",
                "scope": "no reuse; same downstream comparison as other baselines"})
            return


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("stage", choices=["init", "smoke", "algorithm", "finish", "meshquery", "adopt"])
    parser.add_argument("--method", choices=["xatlas", "blender_smart", "optcuts", "partuv", "flatten_anything",
                                            "meshquery_project", "reseam_shared", "reseam_unshared"])
    parser.add_argument("--resolution", type=int, default=2048)
    parser.add_argument("--previous", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    base = args.output.resolve()
    if args.stage == "init":
        initialize(base)
    elif args.stage == "adopt":
        adopt_native(base, args.previous.resolve())
    elif args.stage == "meshquery":
        meshquery(base)
    elif args.stage == "finish" and args.method:
        finish(base, args.method, args.resolution)
    elif args.method:
        algorithm(base, args.method, args.stage == "smoke")
    else:
        parser.error("--method is required")


if __name__ == "__main__":
    main()
