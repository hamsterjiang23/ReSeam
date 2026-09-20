"""Command-line entry points for explicit, reproducible local runs."""

import argparse
import json
from pathlib import Path

from .mesh import sha256
from .pipeline import run_unwrap
from .runtime import read_runtime


def doctor(root: Path) -> dict[str, object]:
    runtime = read_runtime(root)
    manifest = json.loads((root / "assets/v1/manifest.json").read_text(encoding="utf-8-sig"))
    asset_checks = {
        entry["path"]: sha256(root / "assets/v1" / entry["path"]) == entry["sha256"]
        for model in manifest["models"]
        for entry in model["files"]
    }
    dependency_checks = {
        key: Path(runtime[key]).exists()
        for key in (
            "blender",
            "optcuts_binary",
            "continuous_binary",
            "partfield_checkpoint",
            "differentiable_python",
        )
    }
    dependency_checks["partuv_environment"] = (
        Path(runtime["partuv_python"]).parent.parent / "pyvenv.cfg"
    ).exists()
    return {
        "assets": asset_checks,
        "dependencies": dependency_checks,
        "ready": all(asset_checks.values()) and all(dependency_checks.values()),
        "note": "Presence and hashes only; this is not execution proof.",
    }


def main() -> None:
    parser = argparse.ArgumentParser(prog="reseam")
    parser.add_argument("--root", type=Path, default=Path.cwd())
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("doctor")
    unwrap = commands.add_parser("unwrap")
    unwrap.add_argument("--model", choices=["energy_blade", "dragon"], required=True)
    unwrap.add_argument(
        "--method",
        default="reseam_adaptive",
        choices=[
            "optcuts",
            "xatlas",
            "blender_smart",
            "partuv",
            "optcuts_restart",
            "reseam_fixed",
            "reseam_small",
            "reseam_large",
            "reseam_adaptive",
            "reseam_low_fragments",
            "reseam_artist",
        ],
    )
    unwrap.add_argument("--output", type=Path, required=True)
    unwrap.add_argument("--seed", type=int, default=17)
    unwrap.add_argument("--seconds", type=float, default=180)
    unwrap.add_argument("--tau", type=float, default=0.025)
    unwrap.add_argument("--island-weight", type=float, default=0)
    unwrap.add_argument("--max-candidates", type=int, default=120)
    unwrap.add_argument("--annotations", type=Path)
    benchmark = commands.add_parser("benchmark")
    benchmark.add_argument("--config", type=Path, required=True)
    packing = commands.add_parser("pack-benchmark")
    packing.add_argument("--config", type=Path, required=True)
    reuse = commands.add_parser("reuse-preview")
    reuse.add_argument("--config", type=Path, required=True)
    report = commands.add_parser("report")
    report.add_argument("--round", type=Path, required=True)
    joint = commands.add_parser("joint-unwrap")
    joint.add_argument("--model", choices=["energy_blade", "dragon"], required=True)
    joint.add_argument("--track", choices=["packing_only", "seam_pack", "reuse_pack", "joint"], required=True)
    joint.add_argument("--seed", type=int, default=17)
    joint.add_argument("--seconds", type=float, default=300)
    joint.add_argument("--output", type=Path, required=True)
    joint_benchmark = commands.add_parser("joint-benchmark")
    joint_benchmark.add_argument("--config", type=Path, required=True)
    review_export = commands.add_parser("review-export")
    review_export.add_argument("--round", type=Path, required=True)
    review_import = commands.add_parser("review-import")
    review_import.add_argument("--packet", type=Path, required=True)
    review_import.add_argument("--review", type=Path, required=True)
    review_import.add_argument("--output", type=Path, required=True)
    joint_report = commands.add_parser("joint-report")
    joint_report.add_argument("--round", type=Path, required=True)
    args = parser.parse_args()
    root = args.root.resolve()
    if args.command == "doctor":
        print(json.dumps(doctor(root), ensure_ascii=False, indent=2))
    elif args.command == "unwrap":
        result = run_unwrap(
            root,
            args.model,
            args.method,
            args.output.resolve(),
            args.seed,
            args.seconds,
            args.annotations,
            args.tau,
            args.island_weight,
            args.max_candidates,
        )
        print(
            json.dumps(
                {key: result[key] for key in ("model", "method", "status", "wall_seconds", "quality")},
                ensure_ascii=False,
                indent=2,
            )
        )
        if result["status"] != "SUCCESS":
            raise SystemExit(1)
    elif args.command == "benchmark":
        from .benchmark import run_benchmark

        run_benchmark(root, args.config)
    elif args.command == "pack-benchmark":
        from .packing_benchmark import run_packing_benchmark

        run_packing_benchmark(root, args.config.resolve())
    elif args.command == "reuse-preview":
        from .reuse_preview import run_preview

        run_preview(root, args.config.resolve())
    elif args.command == "joint-unwrap":
        import time

        from .joint_budget import JointBudget
        from .joint_search import run_joint

        with JointBudget(root / "artifacts/v2_joint/budget.json").run(
            str(args.output), "smoke", args.seconds
        ) as deadline:
            result = run_joint(
                root, args.model, args.track, args.seed, args.output.resolve(), deadline - time.monotonic()
            )
        print(json.dumps({k: result.get(k) for k in ("status", "quality", "reason")}, ensure_ascii=False))
    elif args.command == "joint-benchmark":
        from .joint_benchmark import run_joint_benchmark

        run_joint_benchmark(root, args.config.resolve())
    elif args.command == "review-export":
        from .joint_report import export_round_packets

        for path in export_round_packets(args.round.resolve()):
            print(path)
    elif args.command == "review-import":
        from .joint_review import import_review

        print(import_review(args.packet.resolve(), args.review.resolve(), args.output.resolve()))
    elif args.command == "joint-report":
        from .joint_report import generate_joint_report

        generate_joint_report(args.round.resolve())
    else:
        from .report import generate_report

        generate_report(args.round.resolve())


if __name__ == "__main__":
    main()
