"""Hold one serial ledger reservation while three external fresh reviewers run concurrently."""
import argparse
import time
from datetime import UTC, datetime
from pathlib import Path

from reseam.joint_budget import JointBudget
from reseam.mesh import sha256
from reseam.pipeline import write_json


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--control", type=Path, required=True)
    parser.add_argument("--seconds", type=float, default=180)
    args = parser.parse_args()
    folder = args.control.resolve()
    folder.mkdir(parents=True, exist_ok=False)
    with JointBudget(Path.cwd() / "artifacts/v2_joint/budget.json").run(
        "review/" + folder.name, "review", args.seconds, require_full=True
    ) as deadline:
        info = {
            "status": "RUNNING", "started_utc": datetime.now(UTC).isoformat(),
            "deadline_unix": time.time() + deadline - time.monotonic(),
            "guard_sha256": sha256(Path(__file__)),
        }
        write_json(folder / "status.json", info)
        print("REVIEW_BUDGET_READY " + str(folder), flush=True)
        while time.monotonic() < deadline and not (folder / "stop").exists():
            time.sleep(min(0.25, max(0, deadline - time.monotonic())))
        info["status"] = "COMPLETE" if (folder / "stop").exists() else "TIMED_OUT"
        info["finished_utc"] = datetime.now(UTC).isoformat()
        write_json(folder / "status.json", info)
        print(info["status"], flush=True)


if __name__ == "__main__":
    main()
