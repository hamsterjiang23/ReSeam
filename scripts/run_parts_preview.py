"""Reproducible part-snapshot preview; no automatic visual acceptance."""

import argparse
from pathlib import Path

import numpy as np
import trimesh

from reseam.agent_tools.runner import run_tool
from reseam.agent_tools.state import load_snapshot, write_json


def run_preview(snapshot: Path, output: Path, resolution: int, blender: str) -> None:
    output.mkdir(parents=True, exist_ok=False)
    steps: list[dict[str, object]] = []

    def step(name: str, tool: str, request: dict[str, object]) -> dict[str, object]:
        path = output / f"{name}.request.json"
        write_json(path, {"timeout_seconds": 300, **request})
        result = run_tool(tool, path, output / name)
        steps.append({"step": name, **result})
        write_json(output / "progress.json", {"steps": steps})
        print(f"{name}: {result['status']} {result.get('reason', '')}", flush=True)
        if result["status"] != "OK":
            raise ValueError(f"PART_PREVIEW_STEP_FAILED:{name}:{result.get('reason')}")
        return result

    profile: dict[str, object] = {"resolution": resolution, "gap_pixels": 8, "border_pixels": 8,
                                  "quality_constraints": False, "correspondence_fraction": .03,
                                  "normal_angle_degrees": 180}
    packed = step("pack", "uv.pack", {"snapshot": str(snapshot), "profile": profile,
                  "backend": "blender_concave", "blender": blender, "packing_seed": 17,
                  "acceptance_policy": "visual"})
    current = str(packed["snapshot"])
    step("inspect", "uv.inspect", {"snapshot": current, "profile": profile})
    cameras = [{"name": name, "azimuth": azimuth, "elevation": elevation} for name, azimuth, elevation in
               (("front", 0, 0), ("back", 180, 0), ("left", 90, 0), ("right", -90, 0),
                ("upper", 30, 45), ("lower", -30, -40))]
    step("parts", "view.render", {"snapshot": current, "mode": "regions", "size": 512, "cameras": cameras})
    frozen = step("target", "material.target", {"snapshot": current, "geometry_profile": profile,
                                                "acceptance_policy": "visual"})
    step("target_views", "view.render", {"snapshot": current, "mode": "basecolor", "target": frozen["target"],
                                         "size": 512, "cameras": cameras, "seams": False})
    baked = step("bake", "material.bake", {"snapshot": current, "mode": "basecolor_reuse",
                 "target": frozen["target"], "resolution": resolution, "padding_pixels": 8,
                 "acceptance_policy": "visual"})
    step("actual_views", "view.render", {"snapshot": current, "mode": "basecolor",
         "texture": str((output / "bake/texture/BaseColor_mip0.png").resolve()), "size": 512,
         "cameras": cameras, "seams": False})
    step("export", "asset.export", {"snapshot": current, "bake": baked["bake"], "mode": "diagnostic"})
    model = trimesh.load(output / "export/asset.glb", force="mesh", process=False)
    if not isinstance(model, trimesh.Trimesh):
        raise TypeError("PART_PREVIEW_REIMPORT_MESH_REQUIRED")
    original = load_snapshot(Path(current)).state.surface
    if not np.array_equal(model.vertices[model.faces], original.mesh.vertices[original.mesh.faces]):
        raise ValueError("PART_PREVIEW_EXPORTED_FACE_CORNER_MISMATCH")
    write_json(output / "preview.json", {"snapshot": current, "resolution": resolution, "steps": steps,
               "model": str((output / "export/asset.glb").resolve()), "reimport_faces": len(model.faces),
               "face_corner_positions_exact": True, "visual_review": "pending", "numeric_quality": "not_validated",
               "full_pbr_passed": False, "human_reviewed": False})


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--resolution", type=int, default=512, choices=(512, 1024, 2048))
    parser.add_argument("--blender", default="E:/Blender 4.5.11/blender.exe")
    args = parser.parse_args()
    run_preview(args.snapshot.resolve(), args.output.resolve(), args.resolution, args.blender)


if __name__ == "__main__":
    main()
