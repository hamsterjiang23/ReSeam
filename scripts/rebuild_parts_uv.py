"""Rebuild every selected part through public UV tools, preserving failed trials."""

import argparse
from pathlib import Path
from typing import cast

import numpy as np

from reseam.agent_tools.runner import run_tool
from reseam.agent_tools.state import load_snapshot, read_object, write_json
from reseam.mesh import DisjointSet, make_mesh


def openings(snapshot: Path, faces: list[int], output: Path) -> list[list[int]]:
    """Plan back-side longitudinal openings on existing original edges only."""
    state = load_snapshot(snapshot).state.surface
    mesh = state.mesh
    local = make_mesh(mesh.vertices, mesh.faces[faces])
    dsu = DisjointSet(len(faces))
    for adjacent in local.edge_faces:
        if len(adjacent) == 2:
            dsu.union(adjacent[0][0], adjacent[1][0])
    components: dict[int, list[int]] = {}
    for index, face in enumerate(faces):
        components.setdefault(dsu.find(index), []).append(face)
    edge_ids = {edge: index for index, edge in enumerate(mesh.edges)}
    cuts: set[tuple[int, int]] = set()
    for index, selected in enumerate(components.values()):
        if len(selected) < 4:
            continue
        patch = make_mesh(mesh.vertices, mesh.faces[selected])
        boundary = [edge for edge, incident in zip(patch.edges, patch.edge_faces, strict=True) if len(incident) == 1]
        choices = boundary or list(patch.edges)
        xyz = mesh.vertices[np.unique(patch.faces)]
        low, high = xyz.min(0), xyz.max(0)
        mids = np.array([mesh.vertices[list(edge)].mean(0) for edge in choices])
        first = choices[int(np.argmin(abs(mids[:, 2]-low[2])+.25*abs(mids[:, 0]-(low[0]+high[0])/2)))]
        axis = int(np.argmax(high-low))
        start = mesh.vertices[list(first)].mean(0)[axis]
        end = high[axis] if abs(start-low[axis]) < abs(start-high[axis]) else low[axis]
        second = min(patch.edges, key=lambda edge: abs(float(mesh.vertices[list(edge)].mean(0)[axis])-end))
        if first == second:
            continue
        path = output / f"cut_{index}.request.json"
        write_json(path, {"snapshot": str(snapshot), "operator": "geodesic", "timeout_seconds": 60,
                          "arguments": {"face_ids": selected, "waypoint_edges": [edge_ids[first], edge_ids[second]]}})
        result = run_tool("seam.select", path, output / f"cut_{index}")
        if result["status"] == "OK":
            cuts.update((edge[0], edge[1]) for edge in cast(list[list[int]], result["edges"]))
    return [list(edge) for edge in sorted(cuts)]


def rebuild(snapshot: Path, output: Path, skip_parts: set[str], backend: str) -> None:
    output.mkdir(parents=True, exist_ok=False)
    original = load_snapshot(snapshot)
    current = str(snapshot)
    decisions: list[dict[str, object]] = []
    for index, part in enumerate(cast(list[dict[str, object]], original.metadata["parts"])):
        if part["id"] in skip_parts:
            continue
        request = {"snapshot": current, "faces": part["faces"], "seam_edges": [], "backend": backend,
                   "pack_after": False,
                   "initial_uv": "geometry", "acceptance_policy": "visual", "timeout_seconds": 180,
                   "profile": {"resolution": 2048, "gap_pixels": 8, "border_pixels": 8,
                               "quality_constraints": False}}
        request_file = output / f"part_{index}.request.json"
        write_json(request_file, request)
        result = run_tool("uv.reparameterize", request_file, output / f"part_{index}")
        trials = [result]
        if result["status"] != "OK" and backend != "optcuts":
            request["seam_edges"] = openings(Path(current), cast(list[int], part["faces"]), output / f"opening_{index}")
            request_file = output / f"part_{index}_opened.request.json"
            write_json(request_file, request)
            result = run_tool("uv.reparameterize", request_file, output / f"part_{index}_opened")
            trials.append(result)
        record: dict[str, object] = {"part": part["id"], "parent": current, "result": result,
                                    "decision": "fallback_parent", "trials": trials}
        if result["status"] == "OK":
            report = read_object(Path(str(result["report"])))
            before, after = int(str(report["old_region_chart_count"])), int(str(report["result_chart_count"]))
            record.update(before=before, after=after)
            # Provisional geometry choice only; the actual baked model is reviewed later.
            current = str(result["snapshot"])
            record["decision"] = "provisional_rebuild_requires_bake_review"
        decisions.append(record)
        write_json(output / "progress.json", {"snapshot": current, "decisions": decisions,
                                             "visual_accepted": False, "requires_rebake": True})
        print(f"{part['id']}: {result['status']} {record.get('before')} -> {record.get('after')}", flush=True)
    write_json(output / "rebuild.json", {"snapshot": current, "decisions": decisions,
                                        "visual_accepted": False, "requires_rebake": True})


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--skip-part", action="append", default=[])
    parser.add_argument("--backend", default="optcuts", choices=("optcuts", "blender_abf", "blender_lscm"))
    args = parser.parse_args()
    rebuild(args.snapshot.resolve(), args.output.resolve(), set(args.skip_part), args.backend)


if __name__ == "__main__":
    main()
