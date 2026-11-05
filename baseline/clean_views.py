"""Presentation-only clean images: identical camera, no seam overlay, no UV edits."""

import argparse
from pathlib import Path

from reseam.agent_tools.runner import run_tool
from reseam.agent_tools.state import read_object, write_json


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    args = parser.parse_args()
    for case in sorted((args.input / "evaluation").glob("*_2048")):
        packed = case / "pack/result.json"
        texture = case / "bake/texture/BaseColor_mip0.png"
        output = case / "clean_color_views"
        if not texture.exists() or not packed.exists() or (output / "result.json").exists():
            continue
        pack = read_object(packed)
        request = {"snapshot": pack["snapshot"], "mode": "basecolor", "texture": str(texture.resolve()),
                   "seams": False, "annotate": False, "size": 1024, "timeout_seconds": 300,
                   "cameras": [{"name": "oblique", "azimuth": 35, "elevation": 15}]}
        path = case / "clean_color_request.json"
        write_json(path, request)
        result = run_tool("view.render", path, output)
        write_json(output / "receipt.json", {"tool": "view.render", "seconds": result["wall_seconds"],
                                             "status": result["status"], "presentation_only": True})
        print(case.name, result["status"], flush=True)


if __name__ == "__main__":
    main()
