"""Candidate UV maps with explicit identities; final acceptance stays independent."""

import time
from itertools import permutations

import numpy as np
from scipy.optimize import linear_sum_assignment
from scipy.spatial import KDTree

from ..mesh import make_mesh, state_from_corner_uv
from ..surface_match import barycentric_coordinates, project_surface, reflect
from ..types import FloatArray, IntArray, MeshState


def topology_copy(
    surface: MeshState, first: tuple[int, ...], second: tuple[int, ...],
    normal: FloatArray, offset: float, fraction: float, deadline: float,
) -> tuple[FloatArray, IntArray] | None:
    """Bounded adjacency-preserving bijection, including coincident distinct IDs."""
    mesh = surface.mesh
    av, bv = np.unique(mesh.faces[list(first)]), np.unique(mesh.faces[list(second)])
    if len(av) != len(bv) or len(first) != len(second):
        return None
    neighbors: dict[int, set[int]] = {int(v): set() for v in np.concatenate((av, bv))}
    for face in mesh.faces[list(first + second)]:
        for vertex in face:
            neighbors[int(vertex)].update(int(v) for v in face if v != vertex)
    radius = max(1e-12, fraction * float(np.linalg.norm(np.ptp(mesh.vertices, axis=0))))
    tree = KDTree(mesh.vertices[av])
    reflected = reflect(mesh.vertices[bv], normal, offset)
    domains: dict[int, list[int]] = {}
    for vertex, position in zip(bv, reflected, strict=True):
        candidates = [int(av[i]) for i in tree.query_ball_point(position, radius)
                      if len(neighbors[int(av[i])]) == len(neighbors[int(vertex)])]
        domains[int(vertex)] = sorted(candidates, key=lambda v: float(np.linalg.norm(mesh.vertices[v]-position)))
        if not candidates:
            return None
    lookup = {frozenset(map(int, mesh.faces[fi])): fi for fi in first}
    target_faces = [tuple(map(int, mesh.faces[fi])) for fi in second]
    mapping: dict[int, int] = {}
    used: set[int] = set()
    visited = 0

    def search() -> bool:
        nonlocal visited
        visited += 1
        if visited > 100000 or time.monotonic() >= deadline:
            return False
        if len(mapping) == len(bv):
            return all(frozenset(mapping[v] for v in face) in lookup for face in target_faces)
        possible: list[tuple[int, int, list[int]]] = []
        for vertex in domains.keys() - mapping.keys():
            compatible = [v for v in domains[vertex] if v not in used and all(
                (other in neighbors[vertex]) == (mapped in neighbors[v])
                for other, mapped in mapping.items())]
            if not compatible:
                return False
            possible.append((len(compatible), vertex, compatible))
        _, vertex, candidates = min(possible)
        for candidate in candidates:
            mapping[vertex] = candidate
            used.add(candidate)
            if search():
                return True
            used.remove(candidate)
            del mapping[vertex]
        return False

    if not search():
        return None
    uv, ids = np.empty((len(second), 3, 2)), np.empty((len(second), 3), dtype=np.int64)
    for row, fi in enumerate(second):
        mapped = [mapping[int(v)] for v in mesh.faces[fi]]
        source = lookup[frozenset(mapped)]
        for ci, vertex in enumerate(mapped):
            corner = int(np.flatnonzero(mesh.faces[source] == vertex)[0])
            uid = surface.uv_faces[source, corner]
            uv[row, ci], ids[row, ci] = surface.uv[uid], uid + len(surface.uv)
    return uv, ids


def normal_corner_transfer(
    surface: MeshState, first: tuple[int, ...], second: tuple[int, ...],
    normal: FloatArray, offset: float, angle_limit: float, deadline: float,
) -> tuple[FloatArray, IntArray]:
    """Project each original corner on its compatible side, then reconcile UV identities.

    A mesh vertex can belong to opposite sides of a folded surface. Its position
    alone cannot choose the source side. The face normal supplies that evidence;
    reconciliation averages only corners already sharing an explicit UV identity.
    """
    mesh = surface.mesh
    triangles = mesh.vertices[mesh.faces]
    normals = np.cross(triangles[:, 1]-triangles[:, 0], triangles[:, 2]-triangles[:, 0])
    normals /= np.linalg.norm(normals, axis=1)[:, None]
    source_triangles = triangles[list(first)]
    source_uv = surface.uv[surface.uv_faces[list(first)]]
    values: dict[int, list[FloatArray]] = {}
    threshold = np.cos(np.radians(angle_limit))
    for fi in second:
        if time.monotonic() >= deadline:
            raise TimeoutError("CORNER_TRANSFER_TIMEOUT")
        compatible = np.flatnonzero(normals[list(first)] @ reflect(normals[fi], normal, 0) >= threshold)
        if not len(compatible):
            raise ValueError("NO_COMPATIBLE_CORNER_DOMAIN")
        points, indices, _ = project_surface(reflect(triangles[fi], normal, offset), source_triangles[compatible])
        indices = compatible[indices]
        bary = barycentric_coordinates(source_triangles[indices], points)
        mapped = np.einsum("nc,ncd->nd", bary, source_uv[indices])
        for uid, value in zip(surface.uv_faces[fi], mapped, strict=True):
            values.setdefault(int(uid), []).append(value)
    reconciled = {uid: np.mean(samples, axis=0) for uid, samples in values.items()}
    return np.asarray([[reconciled[int(uid)] for uid in surface.uv_faces[fi]] for fi in second]), (
        surface.uv_faces[list(second)] + len(surface.uv))


def local_pair(
    surface: MeshState, first: tuple[int, ...], second: tuple[int, ...],
    mapped: tuple[FloatArray, IntArray],
) -> MeshState:
    corners = np.concatenate((surface.uv[surface.uv_faces[list(first)]], mapped[0]))
    identity = np.concatenate((surface.uv_faces[list(first)], mapped[1]))
    return state_from_corner_uv(make_mesh(surface.mesh.vertices, surface.mesh.faces[list(first+second)]),
                               corners, corner_ids=identity)


def face_corner_assignment(
    surface: MeshState, first: tuple[int, ...], second: tuple[int, ...], normal: FloatArray,
    offset: float, fraction: float, angle_limit: float, deadline: float,
) -> tuple[FloatArray, IntArray] | None:
    """Bijective face matching permits seams where triangle adjacency differs.

    This is not a vertex/topology isomorphism. Every target face receives one
    distinct source triangle domain, and inconsistent edge mappings become cuts
    on existing edges. Full affine-cell geometry checks must still accept it.
    """
    if len(first) != len(second):
        return None
    mesh = surface.mesh
    a = mesh.vertices[mesh.faces[list(first)]]
    b = reflect(mesh.vertices[mesh.faces[list(second)]], normal, offset)
    scale = float(np.linalg.norm(np.ptp(mesh.vertices, axis=0)))
    count = len(first)
    costs = np.full((count, count), np.inf)
    chosen = np.zeros((count, count, 3), dtype=np.int64)
    # Opposite winding is required after reflection, so only odd permutations.
    orders = [p for p in permutations(range(3)) if sum(p[i] > p[j] for i in range(3) for j in range(i+1, 3)) % 2]
    an = np.cross(a[:, 1]-a[:, 0], a[:, 2]-a[:, 0])
    bn = -np.cross(b[:, 1]-b[:, 0], b[:, 2]-b[:, 0])
    an /= np.linalg.norm(an, axis=1)[:, None]
    bn /= np.linalg.norm(bn, axis=1)[:, None]
    compatible = bn @ an.T >= np.cos(np.radians(angle_limit))
    for order in orders:
        if time.monotonic() >= deadline:
            raise TimeoutError("FACE_ASSIGNMENT_TIMEOUT")
        errors = np.linalg.norm(b[:, None]-a[None, :, order], axis=3) / scale
        score = np.mean(errors**2, axis=2)
        valid = compatible & (np.max(errors, axis=2) <= fraction)
        improve = valid & (score < costs)
        costs[improve] = score[improve]
        chosen[improve] = order
    if np.any(~np.isfinite(costs).any(axis=0)) or np.any(~np.isfinite(costs).any(axis=1)):
        return None
    try:
        rows, cols = linear_sum_assignment(costs)
    except ValueError:
        return None
    if not np.isfinite(costs[rows, cols]).all():
        return None
    ids = np.asarray([surface.uv_faces[first[int(col)]][chosen[row, col]] for row, col in zip(rows, cols, strict=True)])
    return surface.uv[ids], ids + len(surface.uv)


def common_harmonic_pair(
    surface: MeshState, first: tuple[int, ...], second: tuple[int, ...], normal: FloatArray, offset: float,
) -> MeshState:
    """Two disk interiors solve on one boundary domain without changing faces."""
    from .reparameterize import boundary_loop, harmonic_uv
    first_uv, first_ids, first_loop = harmonic_uv(surface, first)
    second_loop = boundary_loop(surface.mesh.faces[list(second)])
    first_vertices = np.unique(surface.mesh.faces[list(first)])
    source_by_vertex = {int(vertex): first_uv.reshape(-1,2)[np.flatnonzero(
        surface.mesh.faces[list(first)].reshape(-1) == vertex)[0]] for vertex in first_vertices}
    source_boundary = np.asarray([source_by_vertex[v] for v in first_loop])
    positions = surface.mesh.vertices[first_loop]
    edges = np.roll(positions,-1,axis=0)-positions
    target = reflect(surface.mesh.vertices[second_loop],normal,offset)
    raw = target[:,None]-positions[None]
    t = np.clip(np.einsum("nmd,md->nm",raw,edges)/np.sum(edges**2,axis=1)[None],0,1)
    distances = np.linalg.norm(raw-t[:,:,None]*edges[None],axis=2)
    selected = np.argmin(distances,axis=1)
    fraction = t[np.arange(len(t)),selected]
    # Boundary mapping must traverse the source cycle in one direction once.
    parameter = (selected+fraction)/len(first_loop)
    increments = (np.roll(parameter,-1)-parameter+.5)%1-.5
    if np.any(np.abs(increments)<1e-12) or not (
            np.all(increments>0) or np.all(increments<0)) or abs(abs(increments.sum())-1)>1e-8:
        raise ValueError("COMMON_BOUNDARY_NOT_MONOTONE")
    next_boundary = np.roll(source_boundary,-1,axis=0)
    mapped_boundary = (1-fraction[:,None])*source_boundary[selected]+fraction[:,None]*next_boundary[selected]
    second_uv, second_ids, _ = harmonic_uv(surface,second,mapped_boundary)
    corners = np.concatenate((first_uv,second_uv))
    identities = np.concatenate((first_ids,second_ids+int(first_ids.max())+1))
    return state_from_corner_uv(make_mesh(surface.mesh.vertices,surface.mesh.faces[list(first+second)]),
                               corners,corner_ids=identities)
