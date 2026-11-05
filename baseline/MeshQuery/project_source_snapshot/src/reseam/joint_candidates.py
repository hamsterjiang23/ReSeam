"""Embed a local paired solve without changing original geometry or face identity."""

import numpy as np

from .fixed_uv import face_areas
from .joint_regions import RegionPair
from .joint_types import Correspondence, ReuseGroup, V2State
from .mesh import state_from_corner_uv
from .paired_solver import PairProblem
from .types import MeshState


def embed_pair(
    base: V2State,
    solution: MeshState,
    original_faces: tuple[int, ...],
    pair: RegionPair,
    problem: PairProblem,
    group_id: str,
) -> V2State:
    if not np.array_equal(base.surface.mesh.vertices, solution.mesh.vertices) or not np.array_equal(
        base.surface.mesh.faces[list(original_faces)], solution.mesh.faces
    ):
        raise ValueError("LOCAL_SOLVE_CHANGED_ORIGINAL_GEOMETRY")
    if any(set(original_faces) & set(g.first_faces + g.second_faces) for g in base.groups):
        raise ValueError("EXISTING_REUSE_GROUP_CONFLICT")
    old = base.surface
    corners = old.uv[old.uv_faces].copy()
    identities = old.uv_faces.copy()
    local = solution.uv[solution.uv_faces].copy()
    scale = float(
        np.sqrt(np.abs(face_areas(old)[list(original_faces)]).sum() / np.abs(face_areas(solution)).sum())
    )
    local *= scale
    corners[list(original_faces)] = local
    identities[list(original_faces)] = solution.uv_faces + len(old.uv)
    surface = state_from_corner_uv(old.mesh, corners, corner_ids=identities)
    classes = np.asarray(
        base.contact_classes if base.contact_classes is not None else range(len(old.uv)), dtype=np.int64
    )
    per_corner = classes[old.uv_faces].copy()
    local_classes = np.asarray(
        problem.contact_classes if problem.contact_classes is not None else range(len(solution.uv)),
        dtype=np.int64,
    )
    per_corner[list(original_faces)] = local_classes[solution.uv_faces] + int(classes.max()) + 1
    final_classes = np.empty(len(surface.uv), dtype=np.int64)
    final_classes[surface.uv_faces.reshape(-1)] = per_corner.reshape(-1)
    split = len(pair.first_faces)
    group = ReuseGroup(group_id, original_faces[:split], original_faces[split:], pair.normal, pair.offset)
    constraints = tuple(
        Correspondence(
            original_faces[c.source_face],
            c.source_barycentric,
            original_faces[c.target_face],
            c.target_barycentric,
            c.weight,
        )
        for c in problem.constraints
    )
    return V2State(
        surface,
        base.groups + (group,),
        base.correspondences + constraints,
        base.version,
        {},
        tuple(map(int, final_classes)),
    )
