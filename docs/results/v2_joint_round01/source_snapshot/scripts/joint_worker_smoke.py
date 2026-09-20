"""Constructed mirrored-panel integration test for native group pack/bake/material composition."""

import argparse
import json
import time
from pathlib import Path

import numpy as np
from PIL import Image

from reseam.downstream import scene_request
from reseam.joint_budget import JointBudget
from reseam.joint_downstream import joint_worker
from reseam.joint_export import verify_export_frames
from reseam.joint_material import compose_material, material_slots
from reseam.joint_packing import pack_joint
from reseam.joint_types import JointProfile, ReuseGroup, V2State
from reseam.mesh import make_mesh
from reseam.pipeline import export_glb, write_json
from reseam.runtime import read_runtime, run_process
from reseam.types import MeshState


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output = args.output.resolve()
    args.output.mkdir(parents=True, exist_ok=False)
    root = Path.cwd()
    runtime = read_runtime(root)
    with JointBudget(root / "artifacts/v2_joint/budget.json").run(args.output.name, "smoke", 110) as deadline:
        vertices = np.asarray(
            [[-1, 0, 0], [-1, 0, 1], [-1, 1, 0], [1, 0, 0], [1, 1, 0], [1, 0, 1]], dtype=float
        )
        faces = np.arange(6).reshape(2, 3)
        uv = vertices[:, 1:] * 0.25 + 0.2
        state = V2State(
            MeshState(make_mesh(vertices, faces), frozenset(), uv, faces.copy()),
            (ReuseGroup("g", (0,), (1,), (1.0, 0, 0), 0, -1, 1),),
        )
        texture = args.output / "source.png"
        Image.new("RGB", (32, 32), (160, 90, 40)).save(texture)
        high = args.output / "high.glb"
        export_glb(state.surface, high, texture)
        packed, quality = pack_joint(
            root, state, args.output / "pack", min(40, deadline - time.monotonic() - 10), 17, JointProfile()
        )
        if packed is None:
            raise ValueError(str(quality))
        bake = args.output / "bake"
        bake.mkdir()
        request = {
            **scene_request(packed.surface, high),
            "mode": "bake_members",
            "resolution": 1024,
            "face_slots": material_slots(packed).tolist(),
            "folder": str(bake),
            "output": str(bake / "result.json"),
        }
        write_json(bake / "request.json", request)
        run_process(
            [
                runtime["blender"],
                "-b",
                "--factory-startup",
                "--threads",
                "4",
                "--python",
                str(root / "scripts/joint_blender_worker.py"),
                "--",
                str(bake / "request.json"),
            ],
            bake,
            deadline - time.monotonic() - 5,
        )
        result = compose_material(packed, bake, args.output / "material", JointProfile())
        joint_worker(
            root,
            {
                **scene_request(packed.surface, high),
                "mode": "export_shared",
                "basecolor": str(args.output / "material/BaseColor.png"),
                "normal": str(args.output / "material/Normal.png"),
                "asset": str(args.output / "asset.glb"),
                "output": str(args.output / "export/result.json"),
            },
            args.output / "export",
            deadline - time.monotonic(),
        )
        exported = verify_export_frames(args.output / "asset.glb", packed, bake / "result.json")
        write_json(
            args.output / "result.json",
            {
                "kind": "constructed_smoke_not_asset_result",
                "packing": quality,
                "material": result,
                "export_frames": exported,
            },
        )
        print(
            json.dumps(
                {
                    "material_valid": result["valid"],
                    "failed_faces": result["failed_faces"],
                    "packing": quality["quality"],
                },
                ensure_ascii=False,
            )
        )


if __name__ == "__main__":
    main()
