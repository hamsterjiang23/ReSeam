"""Opt-in packing reproduction; never overwrites a primary run."""

import argparse
import json
from pathlib import Path

from reseam.downstream import pack
from reseam.mesh import load_state
from reseam.runtime import read_runtime


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("state", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--profile", choices=["primary", "historical_fraction_0_02"], required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    _, result = pack(
        root, load_state(args.state), read_runtime(root), args.output.resolve(), 60, args.profile
    )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
