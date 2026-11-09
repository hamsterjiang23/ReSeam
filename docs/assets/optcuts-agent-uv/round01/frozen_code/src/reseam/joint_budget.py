"""Separate v2_joint ledger; unfinished reservations remain charged after interruption."""

import json
import os
import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import ClassVar

from .pipeline import write_json


class JointBudget:
    CAPS: ClassVar[dict[str, float]] = {
        "smoke": 3600.0,
        "geometry": 10800.0,
        "downstream": 7200.0,
        "review": 3600.0,
        "report": 3600.0,
    }

    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            write_json(
                path, {"account": "v2_joint", "limit_seconds": 28800, "stage_caps": self.CAPS, "runs": []}
            )
        stored = json.loads(path.read_text(encoding="utf-8"))
        if (
            stored.get("account") != "v2_joint"
            or stored.get("limit_seconds") != 28800
            or stored.get("stage_caps") != self.CAPS
        ):
            raise ValueError("Budget account/config mismatch; never reuse an old experiment ledger")

    @contextmanager
    def run(
        self, label: str, stage: str, requested_seconds: float, require_full: bool = False
    ) -> Iterator[float]:
        with self.path.with_suffix(".lock").open("a+b") as lock:
            if lock.seek(0, 2) == 0:
                lock.write(b"0")
                lock.flush()
            lock.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            try:
                with self._run_locked(label, stage, requested_seconds, require_full) as deadline:
                    yield deadline
            finally:
                lock.seek(0)
                if os.name == "nt":
                    msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(lock, fcntl.LOCK_UN)

    @contextmanager
    def _run_locked(
        self, label: str, stage: str, requested_seconds: float, require_full: bool
    ) -> Iterator[float]:
        if stage not in self.CAPS or requested_seconds <= 0:
            raise ValueError("Invalid budget reservation")
        # Only the top-level serial experiment coordinator writes this ledger.
        data = json.loads(self.path.read_text(encoding="utf-8"))
        total = sum(r["charged_seconds"] for r in data["runs"])
        used = sum(r["charged_seconds"] for r in data["runs"] if r["stage"] == stage)
        available = min(requested_seconds, 28800 - total, self.CAPS[stage] - used)
        if available <= 0 or (require_full and available < requested_seconds):
            raise TimeoutError("STAGE_OR_TOTAL_BUDGET_EXHAUSTED")
        index = len(data["runs"])
        data["runs"].append(
            {
                "label": label,
                "stage": stage,
                "status": "RESERVED",
                "started_utc": datetime.now(UTC).isoformat(),
                "reserved_seconds": available,
                "charged_seconds": available,
            }
        )
        write_json(self.path, data)
        start = time.monotonic()
        status = "COMPLETE"
        try:
            yield start + available
        except BaseException:
            status = "FAILED_OR_INTERRUPTED"
            raise
        finally:
            elapsed = time.monotonic() - start
            data["runs"][index].update(status=status, charged_seconds=elapsed, overrun=elapsed > available)
            data["total_charged_seconds"] = sum(r["charged_seconds"] for r in data["runs"])
            data["remaining_seconds"] = max(0, 28800 - data["total_charged_seconds"])
            write_json(self.path, data)
