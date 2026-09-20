"""Budgeted real initialization, controller and unchanged-layout downstream development probe."""

import argparse
import time
from pathlib import Path

from reseam.joint_budget import JointBudget
from reseam.joint_downstream import evaluate_joint, reference_views
from reseam.joint_search import run_joint
from reseam.joint_state import load_joint
from reseam.joint_types import JointProfile
from reseam.pipeline import write_json


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--model", default="energy_blade")
    args = parser.parse_args()
    root = Path.cwd()
    folder = args.output.resolve()
    folder.mkdir(parents=True, exist_ok=False)
    with JointBudget(root / "artifacts/v2_joint/budget.json").run(folder.name, "smoke", 600) as deadline:
        result = run_joint(root, args.model, "joint", 17, folder / "search", seconds=150, maximum=8)
        print(
            {"search": result["status"], "reason": result.get("reason"), "accepted": result.get("accepted")},
            flush=True,
        )
        if result["status"] != "SUCCESS":
            return
        state = load_joint(folder / "search/joint_state.json")
        reference_views(root, args.model, state, folder / "reference", min(90, deadline - time.monotonic()))
        downstream = evaluate_joint(
            root,
            args.model,
            state,
            folder / "downstream",
            folder / "reference",
            min(270, deadline - time.monotonic()),
            JointProfile(),
        )
        write_json(folder / "summary.json", {"search": result["status"], "downstream": downstream})
        print({"downstream": downstream["status"], "reason": downstream.get("reason")}, flush=True)


if __name__ == "__main__":
    main()
