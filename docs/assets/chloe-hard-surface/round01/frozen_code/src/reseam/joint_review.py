"""Anonymous evidence packets and strict import of external subagent judgements."""

import hashlib
import json
from dataclasses import dataclass
from itertools import combinations
from pathlib import Path
from statistics import median
from typing import Any

import numpy as np
from PIL import Image

from .mesh import sha256
from .pipeline import write_json

DIMENSIONS = ("appearance", "seams", "normal_mip", "uv_editability")
REQUIRED = {
    "appearance": {"base"},
    "seams": {"seam_crop"},
    "normal_mip": {"normal", "mip"},
    "uv_editability": {"uv"},
}


@dataclass(frozen=True)
class ReviewSample:
    identity: str
    technically_valid: bool
    evidence: tuple[tuple[str, Path], ...]


def export_packet(
    folder: Path,
    private_mapping: Path,
    samples: list[ReviewSample],
    reference: list[tuple[str, Path]],
    seed: int,
) -> Path:
    if len(samples) != 4 or len({s.identity for s in samples}) != 4:
        raise ValueError("Four unique scheduled samples required, including failed samples")
    if private_mapping.resolve().is_relative_to(folder.resolve()):
        raise ValueError("Identity mapping cannot be stored inside public packet")
    if private_mapping.exists():
        raise FileExistsError(private_mapping)
    folder.mkdir(parents=True, exist_ok=False)
    aliases = ["sample_A", "sample_B", "sample_C", "sample_D"]
    order = np.random.default_rng(seed).permutation(4)
    public = []
    private = []
    evidence = []

    def copy_evidence(kind: str, path: Path, sample_id: str) -> None:
        eid = f"evidence_{len(evidence):03d}"
        output = folder / (eid + ".png")
        with Image.open(path) as source:
            # Preserve pixel values, remove method-bearing ancillary metadata.
            pixels = Image.fromarray(np.asarray(source))
            pixels.save(output)
        evidence.append(
            {"id": eid, "kind": kind, "sample_id": sample_id, "path": output.name, "sha256": sha256(output)}
        )
        private.append(
            {"evidence_id": eid, "source_path": str(path.resolve()), "source_sha256": sha256(path)}
        )

    for index in range(4):
        sample = samples[int(order[index])]
        alias = aliases[index]
        public.append(
            {"id": alias, "technical_status": "PASS" if sample.technically_valid else "FAILED_QUALITY"}
        )
        private.append({"sample_id": alias, "identity": sample.identity})
        for kind, path in sample.evidence:
            copy_evidence(kind, path, alias)
    for kind, path in reference:
        copy_evidence(kind, path, "reference")
    rubric = """你是匿名图像评审。只能查看本包提供的图片与manifest，不搜索源码、其他实验或私有映射，不推测方法身份。
分别评分appearance、seams、normal_mip、uv_editability：整数1明显不可用、2明显缺陷、3需修整、4接近可用、5给定证据未见明显问题。
评分不是人工验收；不得声称进行过模型编辑或虚构编辑分钟数。缺图或无法判断用null，不猜测。
每维需要reason和evidence图像ID，normal_mip必须实际查看normal及mip图；UV编辑性只评可见结构。
FAILED_QUALITY样例不评分。给出六个样例对的first/second及preference=first|second|tie|unable，附reason和evidence。
输出仅JSON：packet_sha256,reviewer_id,samples:[{id,scores:{维度:{score,evidence:[],reason}},major_defect:bool}],preferences:[{first,second,preference,evidence:[],reason}]。
不得看其他评审的结果。正式评审的意见仅用于冻结运行的报告，不用于同轮调参。"""
    (folder / "rubric.txt").write_text(rubric, encoding="utf-8")
    manifest = {
        "version": 1,
        "samples": public,
        "evidence": evidence,
        "dimensions": list(DIMENSIONS),
        "rubric_sha256": sha256(folder / "rubric.txt"),
    }
    write_json(folder / "manifest.json", manifest)
    private_mapping.parent.mkdir(parents=True, exist_ok=True)
    write_json(private_mapping, {"manifest_sha256": sha256(folder / "manifest.json"), "mapping": private})
    return folder / "manifest.json"


def validate_review(packet: Path, review: dict[str, Any]) -> None:
    manifest = json.loads(packet.read_text(encoding="utf-8"))
    if (
        review.get("packet_sha256") != sha256(packet)
        or not isinstance(review.get("reviewer_id"), str)
        or not review["reviewer_id"].strip()
    ):
        raise ValueError("Review packet or reviewer identity missing/mismatched")
    if sha256(packet.parent / "rubric.txt") != manifest["rubric_sha256"]:
        raise ValueError("Rubric changed")
    evidence = {e["id"]: e for e in manifest["evidence"]}
    for item in evidence.values():
        if sha256(packet.parent / item["path"]) != item["sha256"]:
            raise ValueError("Evidence changed")
    samples = {s["id"]: s for s in manifest["samples"]}
    given = review.get("samples", [])
    if len(given) != len(samples) or {s.get("id") for s in given} != set(samples):
        raise ValueError("Review omitted or duplicated a scheduled sample")
    for sample in given:
        if set(sample.get("scores", {})) != set(DIMENSIONS) or type(sample.get("major_defect")) is not bool:
            raise ValueError("Invalid rubric dimensions or major-defect flag")
        for dimension, record in sample["scores"].items():
            score = record.get("score")
            refs = record.get("evidence", [])
            if (
                not isinstance(record.get("reason"), str)
                or not record["reason"].strip()
                or not isinstance(refs, list)
            ):
                raise ValueError("Missing evidence explanation")
            if any(
                ref not in evidence or evidence[ref]["sample_id"] not in (sample["id"], "reference")
                for ref in refs
            ):
                raise ValueError("Unknown or wrong-sample evidence")
            if score is not None:
                if (
                    type(score) is not int
                    or score not in range(1, 6)
                    or samples[sample["id"]]["technical_status"] != "PASS"
                ):
                    raise ValueError("Invalid score or attempted scoring of rejected sample")
                kinds = {evidence[ref]["kind"] for ref in refs if evidence[ref]["sample_id"] == sample["id"]}
                if not REQUIRED[dimension] <= kinds:
                    raise ValueError("Score lacks required image evidence")
    seen = set()
    for pref in review.get("preferences", []):
        a, b = pref.get("first"), pref.get("second")
        if a not in samples or b not in samples or a == b:
            raise ValueError("Invalid preference pair")
        pair = frozenset((a, b))
        if pair in seen:
            raise ValueError("Duplicate preference pair")
        seen.add(pair)
        if pref.get("preference") not in ("first", "second", "tie", "unable") or not pref.get("reason"):
            raise ValueError("Invalid preference judgement")
        if any(
            x not in evidence or evidence[x]["sample_id"] not in (a, b, "reference")
            for x in pref.get("evidence", [])
        ):
            raise ValueError("Invalid preference evidence")
        if pref["preference"] != "unable" and (
            not pref.get("evidence") or any(samples[x]["technical_status"] != "PASS" for x in (a, b))
        ):
            raise ValueError("Unsupported preference or technical failure")
        if pref["preference"] != "unable":
            kinds_a = {evidence[x]["kind"] for x in pref["evidence"] if evidence[x]["sample_id"] == a}
            kinds_b = {evidence[x]["kind"] for x in pref["evidence"] if evidence[x]["sample_id"] == b}
            if not kinds_a & kinds_b:
                raise ValueError("Preference requires comparable evidence from both samples")
    if seen != {frozenset(x) for x in combinations(samples, 2)}:
        raise ValueError("Incomplete pairwise comparisons")


def import_review(packet: Path, review_path: Path, output: Path) -> Path:
    review = json.loads(review_path.read_text(encoding="utf-8"))
    validate_review(packet, review)
    output.mkdir(parents=True, exist_ok=True)
    name = hashlib.sha256(review["reviewer_id"].encode()).hexdigest()[:16] + ".json"
    destination = output / name
    if destination.exists():
        raise FileExistsError("Reviewer already imported")
    write_json(destination, review)
    return destination


def aggregate_reviews(packet: Path, reviews: list[dict[str, Any]]) -> dict[str, Any]:
    ids = set()
    for review in reviews:
        validate_review(packet, review)
        if review["reviewer_id"] in ids:
            raise ValueError("Duplicate reviewer cannot create quorum")
        ids.add(review["reviewer_id"])
    manifest = json.loads(packet.read_text(encoding="utf-8"))
    output = []
    for sample in manifest["samples"]:
        matching = [next(s for s in r["samples"] if s["id"] == sample["id"]) for r in reviews]
        scores = {}
        for dimension in DIMENSIONS:
            values = [
                s["scores"][dimension]["score"]
                for s in matching
                if s["scores"][dimension]["score"] is not None
            ]
            scores[dimension] = {
                "median": median(values) if len(values) >= 2 else None,
                "values": values,
                "disagreement": bool(values and max(values) - min(values) >= 2),
            }
        status = "AGENT_EVALUATED" if all(v["median"] is not None for v in scores.values()) else "UNSCORED"
        if sample["technical_status"] != "PASS":
            status = "FAILED_QUALITY"
        recommended = (
            status == "AGENT_EVALUATED"
            and all(v["median"] >= 4 for v in scores.values())
            and sum(not s["major_defect"] for s in matching) >= 2
        )
        output.append(
            {
                "id": sample["id"],
                "status": status,
                "scores": scores,
                "recommendation": "AGENT_RECOMMENDED"
                if recommended
                else "NEEDS_WORK"
                if status == "AGENT_EVALUATED"
                else status,
            }
        )
    preferences = []
    for a, b in combinations([s["id"] for s in manifest["samples"]], 2):
        votes = []
        for review in reviews:
            pref = next(p for p in review["preferences"] if {p["first"], p["second"]} == {a, b})
            vote = pref["preference"]
            if vote in ("first", "second"):
                vote = pref[vote]
            votes.append(vote)
        winners = [v for v in (a, b, "tie") if votes.count(v) >= 2]
        preferences.append(
            {"first": a, "second": b, "votes": votes, "majority": winners[0] if winners else "unable"}
        )
    return {
        "packet_sha256": sha256(packet),
        "reviewers": sorted(ids),
        "samples": output,
        "preferences": preferences,
        "human_acceptance": "NOT_REVIEWED",
        "statistical_note": "Same-model fresh contexts are not independent human experts.",
    }
