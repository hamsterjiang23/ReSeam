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
    report = commands.add_parser("report")
    report.add_argument("--round", type=Path, required=True)
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
    else:
        from .report import generate_report

        generate_report(args.round.resolve())


if __name__ == "__main__":
    main()
