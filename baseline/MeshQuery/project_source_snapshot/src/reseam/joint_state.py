"""V2 serialization and atomic candidate acceptance after geometry and material checks."""

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

import numpy as np

from .joint_material import state_fingerprint
from .joint_types import Correspondence, JointProfile, ReuseGroup, V2State
from .joint_validation import density_objective, validate_joint_quality
from .mesh import make_mesh, sha256, state_dict
from .pipeline import write_json
from .reuse_transform import similarity_transform
from .types import MeshState


def save_joint(path: Path, state: V2State) -> None:
    write_json(
        path,
        {
            "surface": state_dict(state.surface),
            "groups": [asdict(g) for g in state.groups],
            "correspondences": [asdict(c) for c in state.correspondences],
            "version": state.version,
            "material_evidence": state.material_evidence,
            "contact_classes": state.contact_classes,
        },
    )


def load_joint(path: Path) -> V2State:
    data = json.loads(path.read_text(encoding="utf-8"))
    raw = data["surface"]
    mesh = make_mesh(np.asarray(raw["vertices"], dtype=float), np.asarray(raw["faces"], dtype=np.int64))
    surface = MeshState(
        mesh,
        frozenset(raw["seams"]),
        np.asarray(raw["uv"], dtype=float),
        np.asarray(raw["uv_faces"], dtype=np.int64),
        raw.get("version", 0),
    )
    groups = tuple(
        ReuseGroup(
            g["group_id"],
            tuple(g["first_faces"]),
            tuple(g["second_faces"]),
            tuple(g["plane_normal"]),
            g["plane_offset"],
            g["first_orientation"],
            g["second_orientation"],
            None if g.get("first_to_second") is None else similarity_transform(g["first_to_second"]),
        )
        for g in data["groups"]
    )
    constraints = tuple(
        Correspondence(
            c["source_face"],
            tuple(c["source_barycentric"]),
            c["target_face"],
            tuple(c["target_barycentric"]),
            c["weight"],
        )
        for c in data["correspondences"]
    )
    classes = data.get("contact_classes")
    return V2State(
        surface,
        groups,
        constraints,
        data["version"],
        data.get("material_evidence", {}),
        tuple(classes) if classes is not None else None,
    )


def material_errors(state: V2State) -> list[str]:
    errors = []
    for group in state.groups:
        folder = state.material_evidence.get(group.group_id)
        if folder is None:
            errors.append("MATERIAL_NOT_VALIDATED:" + group.group_id)
            continue
        try:
            path = Path(folder)
            record = json.loads((path / "material.json").read_text())
            valid = (
                record.get("valid") is True
                and record.get("state_fingerprint") == state_fingerprint(state)
                and sha256(path / "BaseColor.png") == record["base_sha256"]
                and sha256(path / "Normal.png") == record["normal_sha256"]
            )
            if not valid:
                errors.append("STALE_OR_FAILED_MATERIAL:" + group.group_id)
        except (OSError, ValueError, KeyError):
            errors.append("MATERIAL_EVIDENCE_MISSING:" + group.group_id)
    return errors


def accept_joint(
    reference: MeshState, current: V2State, candidate: V2State, expected_version: int, profile: JointProfile
) -> tuple[V2State, dict[str, Any]]:
    if current.version != expected_version:
        return current, {"status": "STALE_STATE"}
    quality = validate_joint_quality(reference, candidate, profile)
    if not quality["valid"]:
        return current, {"status": "QUALITY_REJECTED", "quality": quality}
    errors = material_errors(candidate)
    if errors:
        return current, {"status": "MATERIAL_REJECTED", "errors": errors, "quality": quality}
    old = density_objective(reference, current.surface, profile.resolution)["gain"]
    if quality["gain"] < old + profile.gain_epsilon:
        return current, {"status": "NO_STRICT_IMPROVEMENT", "quality": quality}
    result = candidate.copy()
    result.version = current.version + 1
    result.surface.version = result.version
    return result, {"status": "ACCEPTED", "quality": quality}
