"""Semantic selections retain original face identities and immutable provenance."""

from pathlib import Path

import numpy as np

from ..mesh import chart_labels
from .state import (
    Snapshot,
    check_deadline,
    object_hash,
    require_snapshot,
    save_snapshot,
    snapshot_result,
    write_json,
)


def face_selection(value: object, face_count: int, *, allow_empty: bool = False) -> list[int]:
    if (not isinstance(value, list) or any(type(face) is not int for face in value)
            or len(set(value)) != len(value) or (not value and not allow_empty)):
        raise ValueError("UNIQUE_FACE_IDS_REQUIRED")
    if value and (min(value) < 0 or max(value) >= face_count):
        raise ValueError("FACE_ID_OUT_OF_RANGE")
    return sorted(value)


def selected_faces(request: dict[str, object], snapshot: Snapshot) -> list[int]:
    """Resolve a saved semantic region or explicit faces, never spatial proximity."""
    count = len(snapshot.state.surface.mesh.faces)
    if "selected_faces" in request and "region_id" in request:
        raise ValueError("SELECT_FACES_OR_REGION_NOT_BOTH")
    if "selected_faces" in request:
        return face_selection(request["selected_faces"], count)
    if "region_id" in request:
        regions = snapshot.metadata.get("regions", [])
        if isinstance(regions, list):
            for region in regions:
                if isinstance(region, dict) and region.get("region_id") == request["region_id"]:
                    return face_selection(region.get("faces"), count)
        raise ValueError("UNKNOWN_REGION_ID")
    return list(range(count))


def define_region(request: dict[str, object], output: Path, deadline: float) -> dict[str, object]:
    check_deadline(deadline)
    snapshot = require_snapshot(request)
    name, rationale, evidence = request.get("name"), request.get("rationale", ""), request.get("evidence", "")
    if not isinstance(name, str) or not name.strip():
        raise ValueError("REGION_NAME_REQUIRED")
    if not isinstance(rationale, str) or not isinstance(evidence, str):
        raise TypeError("REGION_RATIONALE_AND_EVIDENCE_MUST_BE_TEXT")
    role = request.get("role", "reuse_candidate")
    if role not in ("reuse_candidate", "protected", "exclusive", "diagnostic"):
        raise ValueError("UNKNOWN_REGION_ROLE")
    if ("faces" in request) == ("chart_ids" in request):
        raise ValueError("DEFINE_WITH_FACES_OR_CHART_IDS")
    surface = snapshot.state.surface
    if "faces" in request:
        faces = face_selection(request["faces"], len(surface.mesh.faces))
    else:
        labels = chart_labels(surface)
        charts = face_selection(request["chart_ids"], int(labels.max()) + 1)
        if not set(charts) <= set(labels.tolist()):
            raise ValueError("UNKNOWN_CHART_ID")
        faces = np.flatnonzero(np.isin(labels, charts)).tolist()
    protected = face_selection(request.get("protected_faces", []), len(surface.mesh.faces), allow_empty=True)
    if not set(protected) <= set(faces):
        raise ValueError("PROTECTED_FACES_OUTSIDE_REGION")
    if role == "protected":
        protected = faces.copy()
    regions = snapshot.metadata.get("regions", [])
    if not isinstance(regions, list) or any(not isinstance(region, dict) for region in regions):
        raise ValueError("INVALID_EXISTING_REGIONS")
    if any(region.get("name") == name.strip() for region in regions):
        raise ValueError("REGION_NAME_ALREADY_DEFINED")
    record: dict[str, object] = {
        "name": name.strip(), "faces": faces, "protected_faces": protected, "role": role,
        "rationale": rationale, "evidence": evidence, "asset_id": snapshot.asset_id,
        "defined_at_state_hash": snapshot.state_hash,
        "face_identity_hash": object_hash({"faces": faces, "vertices": surface.mesh.faces[faces].tolist()}),
    }
    record["region_id"] = "region:" + object_hash(record)[:20]
    metadata = dict(snapshot.metadata)
    metadata["regions"] = [*regions, record]
    metadata["last_operation"] = "region.define"
    check_deadline(deadline)
    child = save_snapshot(snapshot.state, output / "snapshot", metadata, snapshot)
    write_json(output / "region.json", record)
    return {**snapshot_result(child), "region_id": record["region_id"], "face_count": len(faces),
            "protected_face_count": len(protected), "report": str((output / "region.json").resolve()),
            "geometry_modified": False, "uv_modified": False, "quality_evidence": False}
