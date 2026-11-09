"""Validate human UV edits against immutable source identity, without claiming artistic acceptance."""

from typing import Any

import numpy as np

from .constraints import Constraints, mesh_identity
from .initialization import orient_and_scale
from .mesh import state_from_corner_uv
from .types import Mesh, MeshState
from .validation import validate


def validate_edit(
    mesh: Mesh, data: dict[str, Any], constraints: Constraints
) -> tuple[MeshState, dict[str, Any]]:
    if data.get("kind") != "reseam_edited_uv_v1" or data.get("mesh_identity") != mesh_identity(mesh):
        raise ValueError("Edited UV does not identify the fixed source mesh")
    vertices = np.asarray(data["vertices"], dtype=float)
    faces = np.asarray(data["faces"], dtype=np.int64)
    if vertices.shape != mesh.vertices.shape or not np.allclose(
        vertices, mesh.vertices, atol=mesh.scale * 1e-6, rtol=0
    ):
        raise ValueError("Artist edit changed source geometry")
    if not np.array_equal(faces, mesh.faces):
        raise ValueError("Artist edit changed face topology or identity")
    lookup = {edge: i for i, edge in enumerate(mesh.edges)}
    seams = set()
    for edge in data["seam_edges"]:
        if len(edge) != 2:
            raise ValueError("An edge needs two endpoints")
        a, b = map(int, edge)
        key = (min(a, b), max(a, b))
        if key not in lookup:
            raise ValueError("Unknown edited seam edge")
        index = lookup[key]
        if len(mesh.edge_faces[index]) == 2:
            seams.add(index)
    corners = np.asarray(data["corner_uv"], dtype=float)
    if corners.shape != (len(mesh.faces), 3, 2):
        raise ValueError("Edited UV has wrong corner coverage")
    state = state_from_corner_uv(mesh, corners, seam_ids=frozenset(seams))
    if not np.allclose(state.uv[state.uv_faces], corners, atol=1e-10, rtol=0):
        raise ValueError("Edited corner UV conflicts with explicit seam topology")
    state = orient_and_scale(state)
    quality = validate(state, 0.025)
    errors = constraints.errors(state)
    return state, {
        "technical_checks_passed": bool(quality["valid"] and not errors),
        "quality": quality,
        "constraint_errors": errors,
        "human_acceptance": "PENDING_HUMAN",
        "reviewer": data.get("reviewer"),
        "normalization": "common scale and whole-chart orientation convention; subsequent packing is standardized",
    }
