"""Semantic selections retain original face identities and immutable provenance."""

from pathlib import Path

import numpy as np

from ..mesh import chart_labels, make_mesh, state_from_corner_uv, write_obj
from ..types import MeshState
from .regional_cut import edge_selection
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


def bounded_faces(
    surface: MeshState, seed_faces: object, boundary_edges: object, deadline: float,
) -> tuple[list[int], tuple[tuple[int, int], ...]]:
    """Flood original face adjacency without crossing the explicit edge barrier."""
    seeds = face_selection(seed_faces, len(surface.mesh.faces))
    blocked = edge_selection(boundary_edges, surface.mesh)
    if not blocked:
        raise ValueError("NONEMPTY_REGION_BOUNDARY_REQUIRED")
    barrier = set(blocked)
    neighbors: list[list[int]] = [[] for _ in surface.mesh.faces]
    for index, (edge, entries) in enumerate(zip(surface.mesh.edges, surface.mesh.edge_faces, strict=True)):
        if index % 512 == 0:
            check_deadline(deadline)
        if edge not in barrier and len(entries) == 2:
            a, b = entries[0][0], entries[1][0]
            neighbors[a].append(b)
            neighbors[b].append(a)
    visited = set(seeds)
    pending = seeds.copy()
    while pending:
        if len(visited) % 512 == 0:
            check_deadline(deadline)
        face = pending.pop()
        for neighbor in neighbors[face]:
            if neighbor not in visited:
                visited.add(neighbor)
                pending.append(neighbor)
    # An open/incomplete barrier can be walked around. Do not silently return
    # both sides of a supposed boundary or an unrelated seed component.
    edge_entries = dict(zip(surface.mesh.edges, surface.mesh.edge_faces, strict=True))
    if any(sum(face in visited for face, _ in edge_entries[edge]) != 1 for edge in blocked):
        raise ValueError("REGION_BOUNDARY_DOES_NOT_SEPARATE_SEEDS")
    return sorted(visited), blocked


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
    if sum(key in request for key in ("faces", "chart_ids", "seed_faces")) != 1:
        raise ValueError("DEFINE_WITH_FACES_OR_CHART_IDS_OR_SEEDS")
    if "boundary_edges" in request and "seed_faces" not in request:
        raise ValueError("BOUNDARY_EDGES_REQUIRE_SEED_FACES")
    surface = snapshot.state.surface
    selection: dict[str, object] = {"method": "faces"}
    if "faces" in request:
        faces = face_selection(request["faces"], len(surface.mesh.faces))
    elif "chart_ids" in request:
        labels = chart_labels(surface)
        charts = face_selection(request["chart_ids"], int(labels.max()) + 1)
        if not set(charts) <= set(labels.tolist()):
            raise ValueError("UNKNOWN_CHART_ID")
        faces = np.flatnonzero(np.isin(labels, charts)).tolist()
        selection = {"method": "chart_ids", "chart_ids": charts}
    else:
        faces, boundary = bounded_faces(surface, request["seed_faces"], request.get("boundary_edges"), deadline)
        selection = {"method": "bounded_original_adjacency", "seed_faces": request["seed_faces"],
                     "boundary_edges": [list(edge) for edge in boundary]}
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
        "selection": selection,
    }
    record["region_id"] = "region:" + object_hash(record)[:20]
    metadata = dict(snapshot.metadata)
    metadata["regions"] = [*regions, record]
    metadata["last_operation"] = "region.define"
    check_deadline(deadline)
    child = save_snapshot(snapshot.state, output / "snapshot", metadata, snapshot)
    selection_mesh = make_mesh(surface.mesh.vertices, surface.mesh.faces[faces])
    selection_state = state_from_corner_uv(selection_mesh, surface.uv[surface.uv_faces[faces]],
                                           corner_ids=surface.uv_faces[faces])
    selection_path = output / "selection.obj"
    write_obj(selection_path, selection_state)
    write_json(output / "region.json", record)
    return {**snapshot_result(child), "region_id": record["region_id"], "face_count": len(faces),
            "protected_face_count": len(protected), "report": str((output / "region.json").resolve()),
            "selection_mesh": str(selection_path.resolve()), "selection_mesh_face_ids": faces,
            "geometry_modified": False, "uv_modified": False, "quality_evidence": False}
