"""Explain reported strict-injectivity failures without changing acceptance thresholds."""

import argparse
import json
from pathlib import Path

from shapely import Polygon

from reseam.mesh import load_state


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    ledger = json.loads((args.source / "ledger.json").read_text(encoding="utf-8-sig"))
    rows = []
    for record in ledger["runs"]:
        errors = (record.get("quality") or {}).get("errors", [])
        pairs = [
            list(map(int, error.split(":")[1:]))
            for error in errors
            if error.startswith("CHART_INTERSECTION:")
        ]
        if not pairs:
            continue
        state = load_state(args.source / record["path"] / "state.json")
        details = []
        for a, b in pairs:
            first, second = [Polygon(state.uv[state.uv_faces[i]]) for i in (a, b)]
            overlap = first.intersection(second)
            details.append(
                {
                    "face_ids": [a, b],
                    "intersection_type": overlap.geom_type,
                    "intersection_area": overlap.area,
                    "fraction_of_smaller_face": overlap.area / min(first.area, second.area),
                    "classification": "positive_area_overlap"
                    if overlap.area > 0
                    else "zero_area_boundary_contact",
                }
            )
        rows.append({"path": record["path"], "status": record["status"], "reported_intersections": details})
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(
            {
                "scope": "Reported pairs only (validator caps at ten), not exhaustive; no threshold changes or retrospective acceptance",
                "note": "Zero-area boundary contacts can violate strict injectivity without positive-area triangle overlap",
                "runs": rows,
            },
            stream,
            ensure_ascii=False,
            indent=2,
            allow_nan=False,
        )
    print(f"Wrote diagnostics for {len(rows)} runs to {args.output}")


if __name__ == "__main__":
    main()
