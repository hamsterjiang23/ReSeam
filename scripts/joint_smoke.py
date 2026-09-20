"""Budgeted real-asset paired-solver diagnostic. Not a formal A/B runner."""

import argparse
import shutil
import time
from dataclasses import asdict
from pathlib import Path

import numpy as np

from reseam.fixed_uv import shape_distortion
from reseam.joint_budget import JointBudget
from reseam.joint_regions import paired_problem, planar_regions, refine_pair, region_pairs
from reseam.joint_types import JointProfile, ReuseGroup, V2State
from reseam.joint_validation import validate_joint_geometry
from reseam.mesh import load_mesh, load_state, save_state, sha256
from reseam.paired_solver import solve_pair
from reseam.pipeline import draw_uv, write_json


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", choices=["dragon", "energy_blade"], required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--seconds", type=float, default=120)
    parser.add_argument("--initial-uv", type=Path)
    parser.add_argument("--planar", action="store_true")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    code = {}
    for source in [*Path("src/reseam").glob("*.py"), Path(__file__)]:
        relative = source.resolve().relative_to(Path.cwd())
        dest = args.output / "source_snapshot" / relative
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, dest)
        code[str(relative)] = sha256(source)
    path = Path("assets/v1") / args.model / "low.obj"
    ledger = JointBudget(Path("artifacts/v2_joint/budget.json"))
    with ledger.run(args.output.name, "smoke", args.seconds) as deadline:
        start = time.monotonic()
        mesh = load_mesh(path)
        inherited = load_state(args.initial_uv) if args.initial_uv else None
        pairs, planes = region_pairs(mesh, deadline - 10)
        if args.planar:
            pairs = planar_regions(mesh, pairs)
        result = {
            "code_sha256": code,
            "model": args.model,
            "input_sha256": sha256(path),
            "planes": planes,
            "regions": [asdict(pair) for pair in pairs],
            "solves": [],
        }
        write_json(args.output / "result.json", result)
        for i, pair in enumerate(pairs[:3]):
            if time.monotonic() > deadline - 5:
                break
            record = {"index": i}
            try:
                pair, refinement = refine_pair(mesh, pair, deadline - 5)
                record["refinement"] = refinement
                problem, ids = paired_problem(mesh, pair, deadline - 5, inherited, planar=args.planar)
                initial_rms = float(
                    np.sqrt(
                        np.sum(
                            problem.coupling_weights[:, None] * (problem.coupling @ problem.initial.uv) ** 2
                        )
                    )
                )
                solution = solve_pair(problem, min(25, deadline - time.monotonic() - 5))
                split = len(pair.first_faces)
                group = ReuseGroup(
                    "pair", tuple(range(split)), tuple(range(split, len(ids))), pair.normal, pair.offset
                )
                quality = validate_joint_geometry(
                    V2State(
                        solution.state, (group,), problem.constraints, contact_classes=problem.contact_classes
                    ),
                    JointProfile(),
                    packed=False,
                )
                record.update(
                    status=solution.status,
                    initial_rms=initial_rms,
                    final_rms=solution.coupling_rms,
                    iterations=len(solution.history),
                    diagnostics=problem.diagnostics,
                    shape_distortion=shape_distortion(solution.state),
                    quality=quality,
                )
                save_state(args.output / f"pair_{i:02d}.json", solution.state)
                draw_uv(solution.state, args.output / f"pair_{i:02d}.png")
                write_json(
                    args.output / f"pair_{i:02d}_solve.json",
                    {"original_face_ids": ids, "history": solution.history},
                )
            except (ValueError, RuntimeError, TimeoutError, np.linalg.LinAlgError) as error:
                record.update(status="PAIR_FAILED", reason=str(error))
            result["solves"].append(record)
            write_json(args.output / "result.json", result)
        result["wall_seconds"] = time.monotonic() - start
        result["input_unchanged"] = sha256(path) == result["input_sha256"]
        write_json(args.output / "result.json", result)
        print(
            {
                "regions": len(pairs),
                "solves": [
                    {k: v for k, v in r.items() if k not in ("quality", "diagnostics", "refinement")}
                    for r in result["solves"]
                ],
                "seconds": result["wall_seconds"],
            }
        )


if __name__ == "__main__":
    main()
