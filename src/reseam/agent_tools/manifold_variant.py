"""Connectivity-only manifold variants with exact face-corner provenance."""

import numpy as np

from ..mesh import DisjointSet, make_mesh
from ..types import FloatArray, IntArray


def split_manifold_fans(vertices: FloatArray, faces: IntArray) -> tuple[FloatArray, IntArray, dict[str, object]]:
    """Cut ambiguous edge adjacencies, without welding, moving or deleting faces.

    Corners connect only through oppositely oriented, exactly two-sided edges.
    If a cut reconnects around a handle, reject rather than inventing a repair.
    """
    adjacency: dict[tuple[int, int], list[tuple[int, int]]] = {}
    for fi, face in enumerate(faces):
        for ci in range(3):
            a, b = int(face[ci]), int(face[(ci + 1) % 3])
            adjacency.setdefault((min(a, b), max(a, b)), []).append((fi, ci))
    dsu = DisjointSet(faces.size)
    cuts: list[dict[str, object]] = []
    for edge, entries in sorted(adjacency.items()):
        if len(entries) == 1:
            continue
        if len(entries) != 2 or faces[entries[0][0], entries[0][1]] == faces[entries[1][0], entries[1][1]]:
            cuts.append({"edge": list(edge), "faces": [f for f, _ in entries],
                         "reason": "nonmanifold" if len(entries) > 2 else "orientation"})
            continue
        (a, _), (b, _) = entries
        for v in edge:
            ca, cb = int(np.flatnonzero(faces[a] == v)[0]), int(np.flatnonzero(faces[b] == v)[0])
            dsu.union(a * 3 + ca, b * 3 + cb)
    roots: dict[int, int] = {}
    ancestry: list[int] = []
    new_faces = np.empty_like(faces)
    for fi, face in enumerate(faces):
        for ci, vertex in enumerate(face):
            root = dsu.find(fi * 3 + ci)
            if root not in roots:
                roots[root] = len(ancestry)
                ancestry.append(int(vertex))
            new_faces[fi, ci] = roots[root]
    original_vertices = np.asarray(ancestry, dtype=np.int64)
    new_vertices = vertices[original_vertices].copy()
    if not np.array_equal(new_vertices[new_faces], vertices[faces]):
        raise ValueError("CONNECTIVITY_REPAIR_CHANGED_FACE_CORNERS")
    try:
        make_mesh(new_vertices, new_faces)
    except ValueError as exc:
        raise ValueError("CONNECTIVITY_FAN_SPLIT_INSUFFICIENT:" + str(exc)) from exc
    return new_vertices, new_faces, {
        "method": "split_manifold_fans", "cut_edges": cuts,
        "original_vertex_for_new_vertex": ancestry,
        "original_face_for_new_face": list(range(len(faces))),
        "original_faces": faces.tolist(),
        "vertices_before": len(vertices), "vertices_after": len(new_vertices),
        "face_corner_positions_exact": True, "faces_deleted": 0, "faces_added": 0,
        "coordinate_welding": False,
    }
