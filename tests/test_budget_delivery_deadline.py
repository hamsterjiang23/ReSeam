"""A late cleanup may retain only a complete, immutable timely delivery."""

import json
from pathlib import Path

import pytest

from reseam.budget_compare import _timely_delivery_proof
from reseam.mesh import sha256


def proof_fixture(folder: Path) -> dict[str, object]:
    snapshot = folder / "baseline_validation/candidate_-01"
    files = ("state.json", "export.json", "material_lod0_preview.glb",
             "textures/BaseColor_mip0.png", "textures/Normal_mip0.png", "textures/ORM_mip0.png")
    manifest: list[dict[str, object]] = []
    for relative in files:
        path = snapshot / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"exact already-validated immutable payload")
        manifest.append({"path": relative, "bytes": path.stat().st_size, "sha256": sha256(path)})
    raw: dict[str, object] = {"accepted_before_target_deadline": True, "accepted_completed_seconds": 7.5,
                             "accepted_candidate_id": -1, "accepted_candidate_fingerprint": "frozen-state",
                             "payload_bytes": 1234}
    archive = {"completed_seconds_from_start": 7.5, "candidate_id": -1,
               "state_fingerprint": "frozen-state", "payload_bytes": 1234,
               "provenance": {"output_directory": str(snapshot), "files": manifest}}
    (folder / "accepted_archive.json").write_text(json.dumps(archive), encoding="utf-8")
    return raw


def test_timely_snapshot_survives_late_cleanup(tmp_path: Path) -> None:
    raw = proof_fixture(tmp_path)
    assert _timely_delivery_proof(raw, tmp_path, 10)


@pytest.mark.parametrize("value", [True, -1, 11, float("inf"), float("nan"), None])
def test_late_or_invalid_completion_is_not_accepted(tmp_path: Path, value: object) -> None:
    raw = proof_fixture(tmp_path)
    raw["accepted_completed_seconds"] = value
    assert not _timely_delivery_proof(raw, tmp_path, 10)


def test_changed_snapshot_cannot_reuse_timely_certificate(tmp_path: Path) -> None:
    raw = proof_fixture(tmp_path)
    (tmp_path / "baseline_validation/candidate_-01/textures/Normal_mip0.png").write_bytes(b"changed")
    assert not _timely_delivery_proof(raw, tmp_path, 10)


def test_missing_export_cannot_reuse_timely_certificate(tmp_path: Path) -> None:
    raw = proof_fixture(tmp_path)
    (tmp_path / "baseline_validation/candidate_-01/material_lod0_preview.glb").unlink()
    assert not _timely_delivery_proof(raw, tmp_path, 10)


def test_claimed_timely_fields_without_archive_are_rejected(tmp_path: Path) -> None:
    assert not _timely_delivery_proof({"accepted_before_target_deadline": True,
                                      "accepted_completed_seconds": 1.0}, tmp_path, 10)


def test_outside_snapshot_does_not_count_as_this_target(tmp_path: Path) -> None:
    raw = proof_fixture(tmp_path)
    path = tmp_path / "accepted_archive.json"
    archive = json.loads(path.read_text(encoding="utf-8"))
    archive["provenance"]["output_directory"] = str(tmp_path.parent)
    path.write_text(json.dumps(archive), encoding="utf-8")
    assert not _timely_delivery_proof(raw, tmp_path, 10)
