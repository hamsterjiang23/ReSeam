import copy
import json
from itertools import combinations
from pathlib import Path

import pytest
from PIL import Image, PngImagePlugin

from reseam.joint_review import DIMENSIONS, ReviewSample, aggregate_reviews, export_packet, validate_review
from reseam.mesh import sha256


def packet(tmp_path: Path) -> Path:
    samples = []
    for method in ("packing_only", "seam_pack", "reuse_pack", "joint"):
        evidence = []
        for kind in ("base", "seam_crop", "normal", "mip", "uv"):
            path = tmp_path / f"{method}_{kind}.png"
            info = PngImagePlugin.PngInfo()
            info.add_text("method", method)
            Image.new("RGB", (8, 8), (40, 80, 100)).save(path, pnginfo=info)
            evidence.append((kind, path))
        samples.append(ReviewSample(method, True, tuple(evidence)))
    return export_packet(tmp_path / "public", tmp_path / "private/map.json", samples, [], 17)


def review_for(manifest_path: Path, reviewer: str) -> dict:
    data = json.loads(manifest_path.read_text())
    samples = []
    for item in data["samples"]:
        evidence = [e["id"] for e in data["evidence"] if e["sample_id"] == item["id"]]
        samples.append(
            {
                "id": item["id"],
                "major_defect": False,
                "scores": {
                    d: {"score": 4, "reason": "Synthetic fixture only", "evidence": evidence}
                    for d in DIMENSIONS
                },
            }
        )
    preferences = [
        {
            "first": a["id"],
            "second": b["id"],
            "preference": "tie",
            "reason": "Synthetic comparison",
            "evidence": [e["id"] for e in data["evidence"] if e["sample_id"] in (a["id"], b["id"])],
        }
        for a, b in combinations(data["samples"], 2)
    ]
    return {
        "packet_sha256": sha256(manifest_path),
        "reviewer_id": reviewer,
        "samples": samples,
        "preferences": preferences,
    }


def test_blinding_quorum_and_no_human_claim(tmp_path: Path) -> None:
    path = packet(tmp_path)
    text = path.read_text()
    assert "packing_only" not in text and "seam_pack" not in text
    for p in path.parent.glob("*.png"):
        assert "method" not in Image.open(p).info
    a = review_for(path, "one")
    b = review_for(path, "two")
    one = aggregate_reviews(path, [a])
    assert all(s["status"] == "UNSCORED" for s in one["samples"])
    two = aggregate_reviews(path, [a, b])
    assert all(s["status"] == "AGENT_EVALUATED" for s in two["samples"])
    assert two["human_acceptance"] == "NOT_REVIEWED"
    with pytest.raises(ValueError, match="Duplicate reviewer"):
        aggregate_reviews(path, [a, a])


def test_fake_evidence_and_missing_dimension_not_accepted(tmp_path: Path) -> None:
    path = packet(tmp_path)
    review = review_for(path, "one")
    bad = copy.deepcopy(review)
    bad["samples"][0]["scores"]["normal_mip"]["evidence"] = []
    with pytest.raises(ValueError, match="required image"):
        validate_review(path, bad)
    bad = copy.deepcopy(review)
    bad["samples"][0]["scores"]["appearance"]["score"] = True
    with pytest.raises(ValueError, match="Invalid score"):
        validate_review(path, bad)
    bad = copy.deepcopy(review)
    bad["samples"][0]["scores"]["appearance"]["evidence"] = ["invented"]
    with pytest.raises(ValueError, match="Unknown"):
        validate_review(path, bad)


def test_stale_packet_and_modified_image_rejected(tmp_path: Path) -> None:
    path = packet(tmp_path)
    review = review_for(path, "one")
    bad = copy.deepcopy(review)
    bad["packet_sha256"] = "wrong"
    with pytest.raises(ValueError, match="identity"):
        validate_review(path, bad)
    next(path.parent.glob("*.png")).write_bytes(b"changed")
    with pytest.raises(ValueError, match="Evidence changed"):
        validate_review(path, review)


def test_pairwise_preference_needs_both_samples(tmp_path: Path) -> None:
    path = packet(tmp_path)
    review = review_for(path, "one")
    manifest = json.loads(path.read_text())
    pref = review["preferences"][0]
    pref["evidence"] = [e["id"] for e in manifest["evidence"] if e["sample_id"] == pref["first"]]
    with pytest.raises(ValueError, match="both samples"):
        validate_review(path, review)
    pref["preference"] = "unable"
    validate_review(path, review)
