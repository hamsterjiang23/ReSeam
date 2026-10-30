"""Temporary regional cut meshes retain exact original face/corner provenance."""

from dataclasses import dataclass

import numpy as np

from ..mesh import corner_map, make_mesh, state_from_corner_uv
from ..types import IntArray, Mesh, MeshState
from .state import check_deadline


@dataclass(frozen=True)
class CutRegion:
    surface: MeshState
    original_vertex_ids: IntArray
    face_ids: tuple[int, ...]
    seam_edges: tuple[tuple[int, int], ...]


def edge_selection(value: object, mesh: Mesh) -> tuple[tuple[int, int], ...]:
    """Parse original vertex-ID edges; coordinates never identify an edge."""
    if not isinstance(value, (list, tuple)):
        raise TypeError("ORIGINAL_VERTEX_EDGE_PAIRS_REQUIRED")
    edges: list[tuple[int, int]] = []
    for pair in value:
        if (not isinstance(pair, (list, tuple)) or len(pair) != 2
                or any(type(vertex) is not int for vertex in pair) or pair[0] == pair[1]):
            raise ValueError("ORIGINAL_VERTEX_EDGE_PAIRS_REQUIRED")
        edges.append((min(pair), max(pair)))
    if len(set(edges)) != len(edges):
        raise ValueError("DUPLICATE_ORIGINAL_EDGE")
    if not set(edges) <= set(mesh.edges):
        raise ValueError("UNKNOWN_ORIGINAL_EDGE")
    return tuple(sorted(edges))


def cut_region(
    surface: MeshState, faces: tuple[int, ...], seam_edges: object, deadline: float | None = None,
) -> CutRegion:
    """Rebuild selected corner connectivity, joining only across uncut mesh edges.

    The returned geometry is temporary: duplicate positions express cut vertex
    identities. Its face rows/corners map exactly back to ``faces`` on input.
    An explicit empty edge list discards every old UV seam in the selection.
    """
    if deadline is not None:
        check_deadline(deadline)
    if (not faces or any(type(face) is not int for face in faces)
            or len(set(faces)) != len(faces) or min(faces) < 0
            or max(faces) >= len(surface.mesh.faces)):
        raise ValueError("UNIQUE_REGIONAL_FACE_IDS_REQUIRED")
    edges = edge_selection(seam_edges, surface.mesh)
    selected = make_mesh(surface.mesh.vertices, surface.mesh.faces[list(faces)])
    if deadline is not None:
        check_deadline(deadline)
    if not set(edges) <= set(selected.edges):
        raise ValueError("CUT_EDGE_OUTSIDE_REGION")
    selected_indices = {edge: index for index, edge in enumerate(selected.edges)}
    identities = corner_map(selected, frozenset(selected_indices[edge] for edge in edges))
    if deadline is not None:
        check_deadline(deadline)
    original_ids = np.empty(int(identities.max()) + 1, dtype=np.int64)
    original_ids[identities.reshape(-1)] = selected.faces.reshape(-1)
    temporary = make_mesh(surface.mesh.vertices[original_ids], identities)
    # All copies have independent identity even if their positions coincide.
    # The initial UV is only a warm start; the backend computes the new map.
    uv = np.zeros((len(original_ids), 2))
    uv[identities.reshape(-1)] = surface.uv[surface.uv_faces[list(faces)]].reshape(-1, 2)
    local = state_from_corner_uv(temporary, uv[identities], seam_ids=frozenset())
    if deadline is not None:
        check_deadline(deadline)
    original_ids.setflags(write=False)
    return CutRegion(local, original_ids, faces, edges)
