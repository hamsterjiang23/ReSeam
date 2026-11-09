"""Candidate UV maps with explicit identities; final acceptance stays independent."""

import time
from itertools import permutations

import numpy as np
from scipy.optimize import linear_sum_assignment
from scipy.spatial import KDTree

from ..joint_types import SimilarityTransform
from ..mesh import chart_labels, make_mesh, state_from_corner_uv
from ..reuse_transform import mapped_normals, mapped_points, transform_parity
from ..surface_match import barycentric_coordinates, project_surface
from ..types import FloatArray, IntArray, MeshState


def project_triangle_surface(
    points: FloatArray, triangles: FloatArray, deadline: float,
) -> tuple[FloatArray, IntArray, FloatArray, FloatArray]:
    """Exhaustive closest triangle via plane barycentrics and exact edge minima.

    Normalize each triangle by its edge scale before cross products. No absolute
    geometric epsilon classifies small faces as edges or changes their barycentrics.
    Original face ordering deterministically resolves equal-distance candidates.
    """
    if (points.ndim != 2 or points.shape[1] != 3 or triangles.ndim != 3
            or triangles.shape[1:] != (3, 3) or not len(triangles)
            or not np.isfinite(points).all() or not np.isfinite(triangles).all()):
        raise ValueError("FINITE_POINTS_AND_TRIANGLES_REQUIRED")
    origin = triangles[:, 0]
    edge_a, edge_b = triangles[:, 1]-origin, triangles[:, 2]-origin
    scale = np.maximum(np.linalg.norm(edge_a, axis=1), np.linalg.norm(edge_b, axis=1))
    if np.any(scale == 0):
        raise ValueError("DEGENERATE_MASTER_TRIANGLE")
    a, b = edge_a/scale[:, None], edge_b/scale[:, None]
    normal = np.cross(a, b)
    denominator = np.sum(normal**2, axis=1)
    if np.any(denominator == 0):
        raise ValueError("DEGENERATE_MASTER_TRIANGLE")
    hit_points = np.empty_like(points)
    hit_faces = np.empty(len(points), dtype=np.int64)
    hit_bary = np.empty((len(points), 3))
    hit_distance = np.empty(len(points))
    for start in range(0, len(points), 32):
        if time.monotonic() >= deadline:
            raise TimeoutError("CHART_SURFACE_PROJECTION_TIMEOUT")
        query = points[start:start+32]
        relative = (query[:, None]-origin[None])/scale[None, :, None]
        v = np.einsum("ntd,td->nt", np.cross(relative, b[None]), normal)/denominator[None]
        w = np.einsum("ntd,td->nt", np.cross(a[None], relative), normal)/denominator[None]
        bary = np.stack((1-v-w, v, w), axis=2)
        candidates = np.einsum("ntc,tcd->ntd", bary, triangles)
        distance = np.sum((candidates-query[:, None])**2, axis=2)
        distance[np.any(bary < 0, axis=2)] = np.inf
        for first, second in ((0, 1), (1, 2), (2, 0)):
            edge = (triangles[:, second]-triangles[:, first])/scale[:, None]
            delta = (query[:, None]-triangles[None, :, first])/scale[None, :, None]
            t = np.clip(np.einsum("ntd,td->nt", delta, edge)/np.sum(edge**2, axis=1)[None], 0, 1)
            projected = triangles[None, :, first] + t[:, :, None]*(triangles[:, second]-triangles[:, first])[None]
            squared = np.sum((projected-query[:, None])**2, axis=2)
            improve = squared < distance
            edge_bary = np.zeros_like(bary)
            edge_bary[:, :, first], edge_bary[:, :, second] = 1-t, t
            candidates[improve], bary[improve], distance[improve] = (
                projected[improve], edge_bary[improve], squared[improve])
        selected = np.argmin(distance, axis=1)
        rows = np.arange(len(query))
        stop = start+len(query)
        hit_points[start:stop] = candidates[rows, selected]
        hit_faces[start:stop] = selected
        hit_bary[start:stop] = bary[rows, selected]
        hit_distance[start:stop] = np.sqrt(distance[rows, selected])
    return hit_points, hit_faces, hit_bary, hit_distance


def chart_aware_transfer(
    surface: MeshState, first: tuple[int, ...], second: tuple[int, ...],
    normal: FloatArray, offset: float, deadline: float,
    transform: SimilarityTransform | None = None, normal_angle_degrees: float = 45,
) -> tuple[tuple[FloatArray, IntArray], dict[str, object]]:
    """Transfer master chart domains using original identities, never old target cuts.

    A target face chooses a normal-compatible master chart through its projected centroid. Every
    (target original vertex, master chart) key is projected once, independent of
    incident face normals. Different selected charts create cuts on original edges.
    Final cell validation still rejects folds, unsupported boundaries and distortion.
    """
    mesh = surface.mesh
    triangles = mesh.vertices[mesh.faces]
    labels = chart_labels(surface)
    first_ids = np.asarray(first, dtype=np.int64)
    centers = mapped_points(triangles[list(second)].mean(axis=1), normal, offset, transform, inverse=True)
    _, nearest, center_bary, center_distance = project_triangle_surface(centers, triangles[first_ids], deadline)
    if not np.isfinite(normal_angle_degrees) or not 0 <= normal_angle_degrees <= 180:
        raise ValueError("CHART_NORMAL_ANGLE_OUT_OF_RANGE")
    normals = np.cross(triangles[:, 1]-triangles[:, 0], triangles[:, 2]-triangles[:, 0])
    normals /= np.linalg.norm(normals, axis=1)[:, None]
    target_normals = mapped_normals(normals[list(second)], normal, transform, inverse=True)
    unfiltered_faces = first_ids[nearest].copy()
    unfiltered_angles = np.degrees(np.arccos(np.clip(
        np.einsum("ij,ij->i", target_normals, normals[unfiltered_faces]), -1, 1)))
    for row in np.flatnonzero(unfiltered_angles > normal_angle_degrees+1e-8):
        angles = np.degrees(np.arccos(np.clip(normals[first_ids] @ target_normals[row], -1, 1)))
        compatible = np.flatnonzero(angles <= normal_angle_degrees+1e-8)
        if not len(compatible):
            raise ValueError(f"CHART_NO_NORMAL_COMPATIBLE_MASTER_FACE:{second[row]}")
        _, matched, bary, distance = project_triangle_surface(
            centers[row:row+1], triangles[first_ids[compatible]], deadline)
        nearest[row], center_bary[row], center_distance[row] = compatible[matched[0]], bary[0], distance[0]
    source_faces = first_ids[nearest]
    selected_angles = np.degrees(np.arccos(np.clip(
        np.einsum("ij,ij->i", target_normals, normals[source_faces]), -1, 1)))
    selected_charts = labels[source_faces]
    keys = sorted({(int(vertex), int(selected_charts[row])) for row, face in enumerate(second)
                   for vertex in mesh.faces[face]})
    lookup = {key: index for index, key in enumerate(keys)}
    coordinates = np.empty((len(keys), 2))
    vertices_evidence: list[dict[str, object]] = []
    for chart in np.unique(selected_charts):
        ids = np.asarray([index for index, (_, label) in enumerate(keys) if label == chart], dtype=np.int64)
        original_vertices = np.asarray([keys[index][0] for index in ids], dtype=np.int64)
        source_domain = first_ids[labels[first_ids] == chart]
        query = mapped_points(mesh.vertices[original_vertices], normal, offset, transform, inverse=True)
        _, nearest_faces, bary, distance = project_triangle_surface(query, triangles[source_domain], deadline)
        matched = source_domain[nearest_faces]
        coordinates[ids] = np.einsum("nc,ncd->nd", bary, surface.uv[surface.uv_faces[matched]])
        vertices_evidence.extend({"target_original_vertex": int(vertex), "master_chart": int(chart),
            "master_face": int(face), "barycentric": weights.tolist(), "distance": float(error)}
            for vertex, face, weights, error in zip(original_vertices, matched, bary, distance, strict=True))
    indices = np.asarray([[lookup[(int(vertex), int(selected_charts[row]))] for vertex in mesh.faces[face]]
                          for row, face in enumerate(second)], dtype=np.int64)
    record: dict[str, object] = {
        "method": "normal-compatible target centroid selects master chart; one coordinate per original vertex/chart key",
        "target_faces": list(second), "selected_master_faces": source_faces.tolist(),
        "selected_master_charts": selected_charts.tolist(), "centroid_projection_barycentric": center_bary.tolist(),
        "centroid_projection_distance": center_distance.tolist(), "vertex_projections": vertices_evidence,
        "centroid_normal_angle_degrees": selected_angles.tolist(),
        "centroid_normal_angle_limit_degrees": normal_angle_degrees,
        "unfiltered_centroid_master_faces": unfiltered_faces.tolist(),
        "unfiltered_centroid_normal_angle_degrees": unfiltered_angles.tolist(),
        "normal_filter_changed_master_face_count": int(np.count_nonzero(source_faces != unfiltered_faces)),
        "new_identity_keys": [[vertex, chart] for vertex, chart in keys],
        "target_old_uv_identity_used": False, "geometry_modified": False,
        "source_chart_count": len(np.unique(labels[first_ids])),
        "selected_master_chart_count": len(np.unique(selected_charts)),
        "projection": "exhaustive closest plane/segments with per-triangle scale normalization",
        "cuts": "only original target edges between differently selected master charts",
        "acceptance": "full signed UV, injectivity, stretch, density, mapped overlap-cell checks required",
    }
    return (coordinates[indices], indices+len(surface.uv)), record


def topology_copy(
    surface: MeshState, first: tuple[int, ...], second: tuple[int, ...],
    normal: FloatArray, offset: float, fraction: float, deadline: float,
    transform: SimilarityTransform | None = None,
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
    reflected = mapped_points(mesh.vertices[bv], normal, offset, transform, inverse=True)
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
    transform: SimilarityTransform | None = None,
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
        compatible = np.flatnonzero(normals[list(first)] @ mapped_normals(
            normals[fi], normal, transform, inverse=True) >= threshold)
        if not len(compatible):
            raise ValueError("NO_COMPATIBLE_CORNER_DOMAIN")
        points, indices, _ = project_surface(mapped_points(triangles[fi], normal, offset, transform,
                                                         inverse=True), source_triangles[compatible])
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
    transform: SimilarityTransform | None = None,
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
    b = mapped_points(mesh.vertices[mesh.faces[list(second)]], normal, offset, transform, inverse=True)
    scale = float(np.linalg.norm(np.ptp(mesh.vertices, axis=0)))
    count = len(first)
    costs = np.full((count, count), np.inf)
    chosen = np.zeros((count, count, 3), dtype=np.int64)
    parity = transform_parity(transform)
    orders = [p for p in permutations(range(3)) if
              (-1 if sum(p[i] > p[j] for i in range(3) for j in range(i+1, 3)) % 2 else 1) == parity]
    an = np.cross(a[:, 1]-a[:, 0], a[:, 2]-a[:, 0])
    bn = parity * np.cross(b[:, 1]-b[:, 0], b[:, 2]-b[:, 0])
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
    transform: SimilarityTransform | None = None,
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
    target = mapped_points(surface.mesh.vertices[second_loop],normal,offset,transform,inverse=True)
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
