"""Evaluate one completed UV run without modifying its native UV result."""

import argparse
import json
from pathlib import Path

from reseam.downstream import evaluate


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("run", type=Path)
    parser.add_argument("--seconds", type=float, default=600)
    parser.add_argument("--no-differentiable", action="store_true")
    args = parser.parse_args()
    result = evaluate(
        Path(__file__).resolve().parents[1], args.run.resolve(), args.seconds, not args.no_differentiable
    )
    print(
        json.dumps(
            {
                key: result.get(key)
                for key in ("status", "packing", "blender_mae", "differentiable_mae", "seconds", "reason")
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    if result["status"] != "SUCCESS":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
