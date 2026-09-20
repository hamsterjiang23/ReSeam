"""Post-freeze camera checks on the same 24 outputs; not a new search or a change to blind packets."""

import argparse
import json
import time
from pathlib import Path
from typing import Any

from reseam.downstream import blender, image_metrics, scene_request
from reseam.joint_budget import JointBudget
from reseam.joint_state import load_joint
from reseam.mesh import sha256
from reseam.pipeline import write_json
from reseam.runtime import read_runtime


def run(root: Path, folder: Path) -> None:
    output = folder / "additional_views"
    output.mkdir(parents=True, exist_ok=False)
    rows = json.loads((folder / "matrix.json").read_text(encoding="utf-8"))
    views = [[15, 35], [105, 35], [195, 35], [285, 35]]
    protocol = {
        "kind": "postfreeze_additional_camera_check_same_outputs_not_new_search",
        "views": views,
        "channels": ["base", "normal", "mip_128"],
        "source_script_sha256": sha256(Path(__file__)),
        "state_sha256": {r["path"]: sha256(folder / r["path"] / "joint_state.json") for r in rows},
        "purpose": "Additional fixed cameras excluded from this round's smoke views and tuning; original blind packets remain unchanged.",
    }
    write_json(output / "protocol.json", protocol)
    runtime = read_runtime(root)
    results: list[dict[str, Any]] = []
    with JointBudget(root / "artifacts/v2_joint/budget.json").run(
        "v2_joint_round01/additional_views", "downstream", 900, require_full=True
    ) as deadline:
        for model in ("energy_blade", "dragon"):
            selected = next(r for r in rows if r["model"] == model)
            state = load_joint(folder / selected["path"] / "joint_state.json")
            request = {
                **scene_request(state.surface, root / "assets/v1" / model / "high.glb"),
                "views": views,
            }
            for kind in ("base", "normal"):
                dest = output / "reference" / model / kind
                blender(
                    root,
                    runtime,
                    {
                        **request,
                        "mode": "render_high",
                        "normal_preview": kind == "normal",
                        "folder": str(dest),
                        "output": str(dest / "views.json"),
                    },
                    dest,
                    min(45, deadline - time.monotonic()),
                )
        for row in rows:
            result: dict[str, Any] = {"path": row["path"], "status": "PENDING"}
            case = folder / row["path"]
            limit = min(deadline, time.monotonic() + 35)
            try:
                state = load_joint(case / "joint_state.json")
                material = case / "downstream/bake/composed"
                if not material.exists():
                    material = case / "downstream/material"
                request = {
                    **scene_request(state.surface, root / "assets/v1" / row["model"] / "high.glb"),
                    "views": views,
                }
                for kind in ("base", "normal", "mip_128"):
                    dest = output / row["path"] / kind
                    texture = (
                        case / "downstream/mip_128.png" if kind == "mip_128" else material / "BaseColor.png"
                    )
                    blender(
                        root,
                        runtime,
                        {
                            **request,
                            "mode": "render_low",
                            "texture": str(texture),
                            "normal": str(material / "Normal.png") if kind == "normal" else None,
                            "normal_preview": kind == "normal",
                            "folder": str(dest),
                            "output": str(dest / "views.json"),
                        },
                        dest,
                        max(0.1, limit - time.monotonic()),
                    )
                result["metrics"] = {
                    kind: [
                        image_metrics(
                            output
                            / "reference"
                            / row["model"]
                            / ("normal" if kind == "normal" else "base")
                            / f"view_{i:02d}.png",
                            output / row["path"] / kind / f"view_{i:02d}.png",
                        )
                        for i in range(4)
                    ]
                    for kind in ("base", "normal", "mip_128")
                }
                if sha256(case / "joint_state.json") != protocol["state_sha256"][row["path"]]:
                    raise ValueError("STATE_CHANGED")
                result["status"] = "SUCCESS" if time.monotonic() <= limit else "BUDGET_EXCEEDED"
            except (ValueError, RuntimeError, OSError, TimeoutError) as error:
                result.update(status="FAILED", reason=str(error))
            results.append(result)
            write_json(output / "matrix.json", results)
            print({"path": row["path"], "status": result["status"]}, flush=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--round", type=Path, required=True)
    args = parser.parse_args()
    run(Path.cwd(), args.round.resolve())


if __name__ == "__main__":
    main()
