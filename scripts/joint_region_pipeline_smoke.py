"""Development-only cached-input probe of paired region -> full mesh -> packing -> bake gates."""

import argparse
import json
import shutil
import time
from pathlib import Path

from reseam.downstream import scene_request
from reseam.fixed_uv import face_areas
from reseam.joint_budget import JointBudget
from reseam.joint_candidates import embed_pair
from reseam.joint_material import compose_material, material_slots
from reseam.joint_packing import pack_joint
from reseam.joint_regions import paired_problem, planar_regions, refine_pair, region_pairs
from reseam.joint_state import save_joint
from reseam.joint_types import JointProfile, V2State
from reseam.joint_validation import validate_joint_quality
from reseam.mesh import load_state, sha256
from reseam.paired_solver import solve_pair
from reseam.pipeline import export_glb, write_json
from reseam.runtime import read_runtime, run_process


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", choices=["dragon", "energy_blade"], required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--seconds", type=float, default=180)
    args = parser.parse_args()
    root = Path.cwd()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    code = {}
    for p in [*Path("src/reseam").glob("*.py"), *Path("scripts").glob("*worker.py"), Path(__file__)]:
        relative = p.resolve().relative_to(root)
        dest = output / "source_snapshot" / relative
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(p, dest)
        code[str(relative)] = sha256(p)
    source = (
        root / "artifacts/v1/v1_round03" / args.model / "original/optcuts/seed_17/downstream/pack/state.json"
    )
    state = load_state(source)
    runtime = read_runtime(root)
    profile = JointProfile()
    with JointBudget(root / "artifacts/v2_joint/budget.json").run(
        output.name, "smoke", args.seconds
    ) as deadline:
        start = time.monotonic()
        pairs, planes = region_pairs(state.mesh, deadline - 10)
        pairs = planar_regions(state.mesh, pairs)
        result = {
            "kind": "development_cached_input_not_formal_AB",
            "input_sha256": sha256(source),
            "code_sha256": code,
            "model": args.model,
            "region_count": len(pairs),
            "planes": planes,
            "attempts": [],
        }
        write_json(output / "result.json", result)
        for index, pair in enumerate(pairs):
            if time.monotonic() > deadline - 15 or index >= 5:
                break
            folder = output / f"candidate_{index:02d}"
            folder.mkdir()
            record = {"index": index}
            try:
                pair, refinement = refine_pair(state.mesh, pair, deadline - 10)
                record["refinement"] = refinement
                problem, ids = paired_problem(state.mesh, pair, deadline - 10, planar=True)
                target = abs(face_areas(state)[list(ids)])
                problem.target_areas = target / target.sum() * problem.initial_area
                solved = solve_pair(problem, min(12, deadline - time.monotonic() - 10))
                candidate = embed_pair(V2State(state), solved.state, ids, pair, problem, f"g{index}")
                save_joint(folder / "unpacked.json", candidate)
                packed, packing = pack_joint(
                    root, candidate, folder / "pack", min(40, deadline - time.monotonic() - 10), 17, profile
                )
                record["packing"] = packing
                if packed is None:
                    raise ValueError("PACKING_FAILED")
                save_joint(folder / "packed.json", packed)
                quality = validate_joint_quality(state, packed, profile)
                record["quality"] = quality
                record["status"] = "GEOMETRY_VALID" if quality["valid"] else "QUALITY_REJECTED"
                if quality["valid"]:
                    bake = folder / "bake"
                    bake.mkdir()
                    request = {
                        **scene_request(packed.surface, root / "assets/v1" / args.model / "high.glb"),
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
                            "--python-exit-code",
                            "1",
                            "--python",
                            str(root / "scripts/joint_blender_worker.py"),
                            "--",
                            str(bake / "request.json"),
                        ],
                        bake,
                        deadline - time.monotonic() - 8,
                    )
                    material = compose_material(packed, bake, folder / "material", profile)
                    record["material"] = material
                    record["status"] = "MATERIAL_VALID" if material["valid"] else "MATERIAL_REJECTED"
                    export_glb(
                        packed.surface, folder / "basecolor_preview.glb", folder / "material/BaseColor.png"
                    )
            except (ValueError, RuntimeError, TimeoutError, OSError) as error:
                record["status"] = "FAILED"
                record["reason"] = str(error)
            result["attempts"].append(record)
            write_json(output / "result.json", result)
            print(
                json.dumps(
                    {
                        "index": index,
                        "status": record["status"],
                        "reason": record.get("reason"),
                        "gain": record.get("quality", {}).get("gain"),
                        "errors": record.get("quality", {}).get("errors", [])[:6],
                    }
                ),
                flush=True,
            )
        result["wall_seconds"] = time.monotonic() - start
        result["input_unchanged"] = sha256(source) == result["input_sha256"]
        write_json(output / "result.json", result)


if __name__ == "__main__":
    main()
