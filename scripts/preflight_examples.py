"""Reproduce finite-probe and normal-bake examples in a fresh output directory."""

import argparse
import os
import subprocess
import time
from pathlib import Path
from typing import cast

import numpy as np
import trimesh

from reseam.agent_tools.runner import run_tool
from reseam.agent_tools.state import Snapshot, save_snapshot, write_json
from reseam.bake_variant_bake import DEFAULT_BLENDER, BakeVariantRequest, bake_variant
from reseam.budget_state import BudgetState
from reseam.mesh import make_mesh, state_from_corner_uv

BOXES = [([0., 0., 0.], [2., 1., 1.]), ([-.45, .74, 0.], [.16, .48, .16]),
         ([.45, .74, 0.], [.16, .48, .16]), ([0., 1.06, 0.], [1.06, .16, .16])]
BAKE_CONFIG = {"resolution": 128, "gap_pixels": 2., "border_pixels": 2., "padding_pixels": 1.,
               "mip_levels": [0, 1, 2]}


def toolbox(root: Path) -> Snapshot:
    vertices, faces, corners, ids, normals, polygons = [], [], [], [], [], []
    objects: list[dict[str, object]] = []
    for box_id, (center, size) in enumerate(BOXES):
        box = trimesh.creation.box(extents=size)
        xyz, tri = np.asarray(box.vertices) + center, np.asarray(box.faces)
        face_normals = np.asarray(box.face_normals)
        axes = np.argmax(np.abs(face_normals), axis=1)
        groups = axes * 2 + (face_normals[np.arange(12), axes] > 0).astype(int)
        uv = np.empty((12, 3, 2))
        for face, group in enumerate(groups):
            dimensions = [i for i in range(3) if i != axes[face]]
            local = (np.asarray(box.vertices)[tri[face]][:, dimensions] / np.asarray(size)[dimensions] + .5)
            tile = box_id * 6 + int(group)
            uv[face] = local * [.13, .20] + [.025 + (tile % 6) * .16, .025 + (tile // 6) / 4]
        vertices.append(xyz)
        faces.append(tri + box_id * 8)
        corners.append(uv)
        ids.append(tri + (groups[:, None] + box_id * 6) * 8)
        normals.append(np.repeat(face_normals[:, None], 3, axis=1))
        polygons.extend((groups + box_id * 6).tolist())
        objects.append({"node": f"toolbox_{box_id}", "face_start": box_id * 12, "face_count": 12})
    mesh = make_mesh(np.concatenate(vertices), np.concatenate(faces))
    surface = state_from_corner_uv(mesh, np.concatenate(corners), corner_ids=np.concatenate(ids))
    return save_snapshot(BudgetState(surface), root, {"objects": objects,
        "polygon_topology": {"triangle_to_polygon": polygons}, "hard_surface_source": {
            "corner_normals": np.concatenate(normals).tolist(), "sharp_vertex_edges": [],
            "objects": [{**obj, "has_custom_normals": True} for obj in objects]},
        "example": "closed hard box with three-box handle; six charts per cuboid"})


def cylinder(root: Path) -> Snapshot:
    count = 16
    vertices = [[.4*np.cos(2*np.pi*j/count), y, .4*np.sin(2*np.pi*j/count)]
                for y in (-.5, .5) for j in range(count)] + [[0., -.5, 0.], [0., .5, 0.]]
    faces, uv, identities, normals, polygons = [], [], [], [], []
    for j in range(count):
        k = (j+1) % count
        for local, grid in (([j, j+count, k+count], [(j, 0), (j, 1), (j+1, 1)]),
                            ([j, k+count, k], [(j, 0), (j+1, 1), (j+1, 0)])):
            faces.append(local)
            uv.append([[.1+.8*a/count, .1+.5*b] for a, b in grid])
            identities.append([a+b*(count+1) for a, b in grid])
            normals.append([[np.cos(2*np.pi*(v % count)/count), 0., np.sin(2*np.pi*(v % count)/count)]
                            for v in local])
            polygons.append(j)
        for top, local in ((False, [2*count, j, k]), (True, [2*count+1, k+count, j+count])):
            faces.append(local)
            uv.append([[.25+.5*top+vertices[v][0]*.25, .8+vertices[v][2]*.25] for v in local])
            identities.append([100+v+100*top for v in local])
            normals.append([[0., 1. if top else -1., 0.]]*3)
            polygons.append(count+int(top))
    surface = state_from_corner_uv(make_mesh(np.asarray(vertices), np.asarray(faces)), np.asarray(uv),
                                   corner_ids=np.asarray(identities))
    return save_snapshot(BudgetState(surface), root, {"polygon_topology": {"triangle_to_polygon": polygons},
        "hard_surface_source": {"corner_normals": normals, "sharp_vertex_edges": [],
            "objects": [{"face_start": 0, "face_count": len(faces), "has_custom_normals": True}]},
        "example": "smooth evaluated side normals with one longitudinal UV seam; hard cap seams"})


def invoke(name: str, request: dict[str, object], output: Path) -> dict[str, object]:
    path = output.parent / (output.name + "_request.json")
    write_json(path, request)
    result = run_tool(name, path, output)
    if not result.get("execution_success"):
        raise RuntimeError(f"{name}: {result}")
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--blender", type=Path, default=DEFAULT_BLENDER)
    parser.add_argument("--bake", action="store_true")
    args = parser.parse_args()
    root = args.output.resolve()
    root.mkdir(parents=True, exist_ok=False)
    box, round_shape = toolbox(root / "toolbox"), cylinder(root / "cylinder")
    fixtures = {"boxes": BOXES, "output": str(root)}
    write_json(root / "fixtures.json", fixtures)
    worker = Path(__file__).with_name("preflight_example_worker.py")
    with (root / "fixtures.log").open("w", encoding="utf-8") as log:
        subprocess.run([str(args.blender), "--background", "--factory-startup", "--threads", "1",
            "--python-exit-code", "1", "--python", str(worker.resolve()), "--", str(root / "fixtures.json")],
            stdout=log, stderr=subprocess.STDOUT, check=True, timeout=120,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    summary: dict[str, object] = {}
    for name in ("toolbox_high", "toolbox_shifted_high"):
        imported = invoke("asset.import", {"path": str(root / f"{name}.glb")}, root / name)
        result = invoke("material.preflight", {"snapshot": str(box.root), "state_hash": box.state_hash,
            "high_snapshot": imported["snapshot"], "high_state_hash": imported["state_hash"],
            "part_pairings": [{"low_faces": list(range(i*12, (i+1)*12)),
                               "high_objects": [f"toolbox_{i}_high"]} for i in range(4)],
            "blender": str(args.blender), "bake_config": BAKE_CONFIG}, root / (name + "_check"))
        summary[name] = result
    summary["cylinder"] = invoke("material.preflight", {"snapshot": str(round_shape.root),
        "state_hash": round_shape.state_hash, "high_snapshot": str(round_shape.root),
        "blender": str(args.blender), "bake_config": BAKE_CONFIG}, root / "cylinder_check")
    if args.bake:
        for name in ("toolbox_high", "toolbox_shifted_high"):
            surface = box.state.surface
            report = bake_variant(BakeVariantRequest(surface.mesh.vertices, surface.mesh.faces,
                surface.uv[surface.uv_faces], root / f"{name}.glb", resolution=128,
                cage_extrusion=.06, margin_pixels=1), root / (name + "_bake"),
                deadline=time.monotonic()+120, blender=args.blender)
            summary[name+"_bake"] = {"status": report["status"], "record": str(root / (name+"_bake/record.json"))}
        write_json(root / "render_config.json", {"output": str(root), "mode": "render"})
        with (root / "render.log").open("w", encoding="utf-8") as log:
            subprocess.run([str(args.blender), "--background", "--factory-startup", "--threads", "1",
                "--python-exit-code", "1", "--python", str(worker.resolve()), "--", str(root / "render_config.json")],
                stdout=log, stderr=subprocess.STDOUT, check=True, timeout=120,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    write_json(root / "summary.json", summary)
    # Store compact check conclusions separately from all heavy evidence.
    print({key: cast(dict, value).get("preflight_status", cast(dict, value).get("status"))
           for key, value in summary.items()})


if __name__ == "__main__":
    main()
