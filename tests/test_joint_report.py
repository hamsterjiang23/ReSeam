import json
from pathlib import Path

from reseam.joint_report import export_round_packets, generate_joint_report
from reseam.joint_search import TRACKS


def test_failed_matrix_is_not_dropped_or_scored(tmp_path: Path) -> None:
    rows = [
        {
            "model": model,
            "seed": seed,
            "track": track,
            "status": "FAILED",
            "downstream_status": "NOT_RUN",
            "path": f"{model}/{seed}/{track}",
        }
        for model in ("energy_blade", "dragon")
        for seed in (17, 29, 43)
        for track in TRACKS
    ]
    (tmp_path / "matrix.json").write_text(json.dumps(rows))
    packets = export_round_packets(tmp_path)
    assert len(packets) == 6
    generate_joint_report(tmp_path)
    summary = json.loads((tmp_path / "summary.json").read_text())
    assert summary["scheduled"] == 24 and summary["completed_searches"] == 0
    assert len(summary["tracks"]) == 8
    assert all(item["gain_median"] is None for item in summary["tracks"])
    review = json.loads((tmp_path / "review_summary.json").read_text())
    assert all(
        sample["status"] == "FAILED_QUALITY" for packet in review.values() for sample in packet["samples"]
    )
    assert (tmp_path / "index.html").read_text(encoding="utf-8").count("<article>") == 24
