"""Validate an exported edit and optionally run a new bake; never marks human acceptance complete."""

import argparse
import json
from pathlib import Path

from reseam.artist_review import validate_edit
from reseam.constraints import load_constraints
from reseam.downstream import evaluate
from reseam.mesh import load_mesh, save_state, write_obj
from reseam.pipeline import draw_uv, write_json


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("input", type=Path)
    parser.add_argument("--model", choices=["energy_blade", "dragon"], required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--annotations", type=Path)
    parser.add_argument("--bake", action="store_true")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    mesh = load_mesh(root / "assets/v1" / args.model / "low.obj")
    data = json.loads(args.input.read_text(encoding="utf-8"))
    state, result = validate_edit(mesh, data, load_constraints(args.annotations, mesh))
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    save_state(output / "state.json", state)
    write_obj(output / "native.obj", state)
    draw_uv(state, output / "uv_layout.png")
    result.update(
        model=args.model,
        method="human_edit",
        seed=17,
        status="SUCCESS" if result["technical_checks_passed"] else "UV_INVALID",
    )
    write_json(output / "result.json", result)
    if args.bake and result["technical_checks_passed"]:
        evaluate(root, output, 600, differentiable=False)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if not result["technical_checks_passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
