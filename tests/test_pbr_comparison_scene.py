"""Comparison scenes refuse stale or unverified actual-model receipts."""

import json
import time
from pathlib import Path

import pytest

from reseam.agent_tools.pbr_review import comparison_scene
from reseam.agent_tools.state import file_hash


@pytest.mark.parametrize(
    ("verified", "valid_hash", "error"),
    [
        (False, True, "COMPARISON_REFERENCE_PREVIEW_NOT_VERIFIED"),
        (True, False, "COMPARISON_REFERENCE_MODEL_CHANGED"),
        (True, True, "COMPARISON_REFERENCE_GEOMETRY_MISSING"),
    ],
)
def test_comparison_rejects_incomplete_reference_binding(
    tmp_path: Path, verified: bool, valid_hash: bool, error: str,
) -> None:
    model = tmp_path / "reference.glb"
    model.write_bytes(b"immutable test model")
    receipt = tmp_path / "preview.json"
    receipt.write_text(json.dumps({
        "geometry_and_uv_reimport_match": verified,
        "model": str(model),
        "model_sha256": file_hash(model) if valid_hash else "stale digest",
    }), encoding="utf-8")
    request: dict[str, object] = {"reference_previews": {"reference_high": str(receipt)}}
    with pytest.raises(ValueError, match=error):
        comparison_scene(request, tmp_path, {}, time.monotonic() + 1.)
