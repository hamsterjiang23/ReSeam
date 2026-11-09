import json
from pathlib import Path

from scripts.build_legacy_reseam_report import audit_call_ledger


def write_object(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def tool_directory(root: Path, label: str, *, finished: bool) -> None:
    folder = root / "chloe" / label
    for name in ("command", "request", "provenance"):
        write_object(folder / f"{name}.json", {})
    if finished:
        write_object(folder / "result.json", {"status": "OK", "wall_seconds": 2.0})


def test_report_rejects_missing_call_and_pending_tool(tmp_path: Path) -> None:
    tool_directory(tmp_path, "finished", finished=True)
    tool_directory(tmp_path, "running", finished=False)
    write_object(tmp_path / "calls.json", {"calls": []})
    audit = audit_call_ledger(tmp_path)
    assert audit["valid"] is False
    assert audit["missing_ledger_records"] == ["chloe/finished"]
    assert audit["pending_tool_directories"] == ["chloe/running"]


def test_report_requires_unique_costed_call_records(tmp_path: Path) -> None:
    tool_directory(tmp_path, "finished", finished=True)
    call = {"asset": "chloe", "label": "finished", "seconds": 2.1}
    write_object(tmp_path / "calls.json", {"calls": [call]})
    assert audit_call_ledger(tmp_path)["valid"] is True
    write_object(tmp_path / "calls.json", {"calls": [call, call]})
    assert audit_call_ledger(tmp_path)["duplicate_entries"] == 1
    write_object(tmp_path / "calls.json", {"calls": [{**call, "seconds": None}]})
    assert audit_call_ledger(tmp_path)["invalid_cost_records"] == ["chloe/finished"]
