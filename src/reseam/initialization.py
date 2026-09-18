"""Convex-boundary positive-weight initialization for disk charts."""

import numpy as np
from scipy.sparse import lil_matrix
from scipy.sparse.linalg import spsolve

from .mesh import chart_labels, corner_map
from .types import Mesh, MeshState
from .validation import jacobians, topology_errors, validate


def initialize(
    mesh: Mesh,
    seams: frozenset[int],
    inherited: MeshState | None = None,
    affected_faces: frozenset[int] | None = None,
) -> MeshState:
    mapping = corner_map(mesh, seams)
    state = MeshState(mesh, seams, np.zeros((int(mapping.max()) + 1, 2)), mapping)
    errors = topology_errors(state)
    if errors:
        raise ValueError(";".join(errors))
    labels = chart_labels(state)
    offset = 0.0
    for chart in np.unique(labels):
        ids = np.flatnonzero(labels == chart)
        vertices = np.unique(mapping[ids])
        if (
            inherited is not None
            and affected_faces is not None
            and not any(int(fi) in affected_faces for fi in ids)
        ):
            for fi in ids:
                state.uv[mapping[fi]] = inherited.uv[inherited.uv_faces[fi]]
            continue
        faces = mapping[ids]
        neighbors: dict[int, set[int]] = {int(v): set() for v in vertices}
        directed: set[tuple[int, int]] = set()
        for face in faces:
            for c in range(3):
                a, b = int(face[c]), int(face[(c + 1) % 3])
                neighbors[a].add(b)
                neighbors[b].add(a)
                directed.add((a, b))
        boundary_edges = {a: b for a, b in directed if (b, a) not in directed}
        start = min(boundary_edges)
        boundary = [start]
        current = boundary_edges[start]
        while current != start:
            if current in boundary or current not in boundary_edges:
                raise ValueError("Invalid chart boundary")
            boundary.append(current)
            current = boundary_edges[current]
        if len(boundary) != len(boundary_edges) or len(boundary) < 3:
            raise ValueError("Multiple or degenerate boundaries")
        source_vertex: dict[int, int] = {}
        for fi in ids:
            for corner in range(3):
                source_vertex[int(mapping[fi, corner])] = int(mesh.faces[fi, corner])
        points = mesh.vertices[[source_vertex[v] for v in boundary]]
        lengths = np.linalg.norm(np.roll(points, -1, axis=0) - points, axis=1)
        angles = 2 * np.pi * np.r_[0, np.cumsum(lengths)[:-1]] / lengths.sum()
        radius = np.sqrt(float(mesh.areas[ids].sum()) / (mesh.areas.sum() * np.pi))
        state.uv[boundary] = np.column_stack((np.cos(angles), np.sin(angles))) * radius
        interior = sorted(set(map(int, vertices)) - set(boundary))
        if interior:
            index = {v: i for i, v in enumerate(interior)}
            matrix = lil_matrix((len(interior), len(interior)), dtype=float)
            rhs = np.zeros((len(interior), 2))
            for vertex, row in index.items():
                matrix[row, row] = len(neighbors[vertex])
                for neighbor in neighbors[vertex]:
                    if neighbor in index:
                        matrix[row, index[neighbor]] = -1
                    else:
                        rhs[row] += state.uv[neighbor]
            state.uv[interior] = np.asarray(spsolve(matrix.tocsr(), rhs))
        state.uv[vertices, 0] += offset
        offset += 4 * radius + 1
    # Minimize SD over one common scale per newly initialized chart, before freezing supports.
    for chart in np.unique(labels):
        ids = np.flatnonzero(labels == chart)
        if (
            inherited is not None
            and affected_faces is not None
            and not any(int(fi) in affected_faces for fi in ids)
        ):
            continue
        vertices = np.unique(mapping[ids])
        sigma = np.linalg.svd(jacobians(state)[ids], compute_uv=False)
        if np.any(sigma < 1e-14):
            raise ValueError("Degenerate harmonic initialization")
        weights = mesh.areas[ids]
        scale = (
            np.dot(weights, np.sum(sigma**-2, axis=1)) / np.dot(weights, np.sum(sigma**2, axis=1))
        ) ** 0.25
        center = state.uv[vertices].mean(axis=0)
        state.uv[vertices] = (state.uv[vertices] - center) * scale + center
    if not validate(state, tau=None)["valid"]:
        raise ValueError("Initialization failed injectivity checks")
    return state


def orient_and_scale(state: MeshState) -> MeshState:
    """Normalize baseline units; permit a whole-chart reflection, never a local fold."""
    result = state.copy()
    labels = chart_labels(result)
    for chart in np.unique(labels):
        ids = np.flatnonzero(labels == chart)
        vertices = np.unique(result.uv_faces[ids])
        det = np.linalg.det(jacobians(result)[ids])
        if np.all(det < 0):
            result.uv[vertices, 1] *= -1
    sigma = np.linalg.svd(jacobians(result), compute_uv=False)
    if np.any(sigma < 1e-14):
        return result
    weights = result.mesh.areas
    scale = (np.dot(weights, np.sum(sigma**-2, axis=1)) / np.dot(weights, np.sum(sigma**2, axis=1))) ** 0.25
    result.uv *= scale
    return result
