"""Bounded real-backend probes before freezing the formal experiment source."""

import json
from pathlib import Path

from reseam.audit import support_audit
from reseam.mesh import load_state
from reseam.pipeline import run_unwrap
from reseam.runtime import read_runtime


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    cases = [
        ("energy_blade", "xatlas", 40),
        ("energy_blade", "optcuts_restart", 100),
        ("energy_blade", "reseam_fixed", 100),
        ("dragon", "reseam_adaptive", 140),
    ]
    for model, method, seconds in cases:
        output = root / "artifacts/smoke" / f"final_{model}_{method}"
        result = run_unwrap(root, model, method, output, seconds=seconds, max_candidates=16)
        print(
            json.dumps(
                {
                    key: result.get(key)
                    for key in ("model", "method", "status", "wall_seconds", "reason", "quality")
                }
            ),
            flush=True,
        )
    result = support_audit(
        load_state(root / "artifacts/smoke/energy_optcuts/state.json"),
        read_runtime(root),
        root / "artifacts/smoke/final_support_audit",
        seconds=100,
        pool_size=3,
    )
    print(
        json.dumps({key: result[key] for key in ("audited_count", "small_failure_count", "rescue_rate")}),
        flush=True,
    )


if __name__ == "__main__":
    main()
