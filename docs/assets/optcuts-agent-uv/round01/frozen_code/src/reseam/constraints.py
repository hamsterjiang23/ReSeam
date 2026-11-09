"""Unconfirmed suggestions are soft; only explicit human confirmation grants hard constraints."""

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from .types import Mesh, MeshState


def mesh_identity(mesh: Mesh) -> str:
    digest = hashlib.sha256()
    digest.update(mesh.vertices.astype("<f8").tobytes())
    digest.update(mesh.faces.astype("<i8").tobytes())
    return digest.hexdigest()


@dataclass
class Constraints:
    must_cut: frozenset[int] = frozenset()
    must_join: frozenset[int] = frozenset()
    weights: dict[int, float] = field(default_factory=dict)
    confirmed: bool = False

    def errors(self, state: MeshState) -> list[str]:
        return [f"MISSING_REQUIRED_CUT:{e}" for e in sorted(self.must_cut - state.seams)] + [
            f"FORBIDDEN_CUT:{e}" for e in sorted(self.must_join & state.seams)
        ]


def suggestions(mesh: Mesh) -> dict[str, Any]:
    triangles = mesh.vertices[mesh.faces]
    normals = np.cross(triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0])
    normals /= np.linalg.norm(normals, axis=1)[:, None]
    preferences = []
    for ei, entries in enumerate(mesh.edge_faces):
        if len(entries) != 2:
            continue
        angle = float(np.arccos(np.clip(np.dot(normals[entries[0][0]], normals[entries[1][0]]), -1, 1)))
        if angle > np.deg2rad(45):
            preferences.append(
                {
                    "edge_id": ei,
                    "kind": "prefer_cut",
                    "confidence": min(0.8, angle / np.pi),
                    "source": "geometric_dihedral_not_semantic_label",
                }
            )
    return {
        "schema_version": 1,
        "mesh_identity": mesh_identity(mesh),
        "confirmed": False,
        "confirmed_by": None,
        "must_cut": [],
        "must_join": [],
        "protected_regions": [],
        "preferences": preferences,
        "material_boundary_status": "not_available_without_verified_correspondence",
    }


def load_constraints(path: Path | None, mesh: Mesh) -> Constraints:
    if path is None:
        return Constraints()
    data = json.loads(path.read_text(encoding="utf-8"))
    confirmed = data.get("confirmed") is True and bool(data.get("confirmed_by"))
    if data.get("mesh_identity") is not None and data["mesh_identity"] != mesh_identity(mesh):
        raise ValueError("Annotation belongs to a different mesh")
    if confirmed and data.get("mesh_identity") is None:
        raise ValueError("Confirmed annotations require a mesh identity")
    interior = {i for i, items in enumerate(mesh.edge_faces) if len(items) == 2}
    requested_cut = set(map(int, data.get("must_cut", [])))
    requested_join = set(map(int, data.get("must_join", [])))
    for region in data.get("protected_regions", []):
        faces = set(map(int, region["face_ids"]))
        if not faces <= set(range(len(mesh.faces))):
            raise ValueError("Unknown protected-region face ID")
        requested_join.update(
            i for i, pair in enumerate(mesh.edge_faces) if len(pair) == 2 and all(f in faces for f, _ in pair)
        )
    if not (requested_cut | requested_join) <= interior:
        raise ValueError("Constraint edge is not an original interior edge")
    if requested_cut & requested_join:
        raise ValueError(f"Conflicting cut/join edges: {sorted(requested_cut & requested_join)}")
    weights: dict[int, float] = {}
    for preference in data.get("preferences", []):
        edge = int(preference["edge_id"])
        confidence = float(preference.get("confidence", 1))
        if edge not in interior or not 0 <= confidence <= 1:
            raise ValueError("Invalid preference edge or confidence")
        kind = preference["kind"]
        if kind not in ("protect", "prefer_cut"):
            raise ValueError(f"Unknown preference: {kind}")
        target = 3.0 if kind == "protect" else 0.5
        weights[edge] = 1 + confidence * (target - 1)
    if not confirmed:
        weights.update({e: 3.0 for e in requested_join})
        weights.update({e: 0.5 for e in requested_cut})
    return Constraints(
        frozenset(requested_cut if confirmed else ()),
        frozenset(requested_join if confirmed else ()),
        weights,
        confirmed,
    )
