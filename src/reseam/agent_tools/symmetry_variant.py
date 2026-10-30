"""Identity-preserving half-space clipping and mirrored geometry variants.

This changes geometry deliberately and must start a new asset, not a UV snapshot
child. The two halves have separate geometry and UV identities, including the
midplane. No spatial welding, packing, baking, or input mutation is performed.
"""

from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray

from ..mesh import make_mesh
from ..types import FloatArray, IntArray, MeshState
from .state import check_deadline


@dataclass(frozen=True)
class SymmetryVariant:
    surface: MeshState
    source_face_ids: IntArray
    source_barycentric: FloatArray
    reflected_faces: NDArray[np.bool_]
    face_pairs: IntArray
    mirror_vertex_ids: IntArray
    mirror_uv_ids: IntArray
    retained_vertex_sources: tuple[tuple[int, ...], ...]
    retained_uv_sources: tuple[tuple[int, ...], ...]
    report: dict[str, object]


@dataclass(frozen=True)
class _Corner:
    geometry_key: tuple[int, ...]
    uv_key: tuple[int, ...]
    barycentric: FloatArray
    position: FloatArray
    uv: FloatArray


def _original_corner(source: MeshState, face_id: int, corner: int) -> _Corner:
    vertex = int(source.mesh.faces[face_id, corner])
    uv_id = int(source.uv_faces[face_id, corner])
    barycentric = np.zeros(3, dtype=np.float64)
    barycentric[corner] = 1.0
    return _Corner((0, vertex), (0, vertex, uv_id), barycentric,
                   source.mesh.vertices[vertex].copy(), source.uv[uv_id].copy())


def _intersection(
    source: MeshState, face_id: int, first: int, second: int,
    axis: int, offset: float,
) -> _Corner:
    face = source.mesh.faces[face_id]
    if source.mesh.vertices[face[first], axis] == offset:
        return _original_corner(source, face_id, first)
    if source.mesh.vertices[face[second], axis] == offset:
        return _original_corner(source, face_id, second)
    # Canonical original endpoint order gives adjacent faces exactly the same
    # intersection arithmetic, independently of their opposite edge winding.
    a, b = (first, second) if face[first] < face[second] else (second, first)
    va, vb = int(face[a]), int(face[b])
    pa, pb = source.mesh.vertices[va], source.mesh.vertices[vb]
    weight = float((offset - pa[axis]) / (pb[axis] - pa[axis]))
    barycentric = np.zeros(3, dtype=np.float64)
    barycentric[a], barycentric[b] = 1.0 - weight, weight
    position = pa + weight * (pb - pa)
    position[axis] = offset
    ua, ub = int(source.uv_faces[face_id, a]), int(source.uv_faces[face_id, b])
    uv = (1.0 - weight) * source.uv[ua] + weight * source.uv[ub]
    return _Corner((1, va, vb), (1, va, vb, ua, ub), barycentric, position, uv)


def _clip_face(
    source: MeshState, face_id: int, axis: int, offset: float, keep_positive: bool,
) -> list[_Corner]:
    coordinates = source.mesh.vertices[source.mesh.faces[face_id], axis] - offset
    inside = coordinates >= 0.0 if keep_positive else coordinates <= 0.0
    corners: list[_Corner] = []
    for current in range(3):
        previous = (current - 1) % 3
        if inside[current] != inside[previous]:
            corners.append(_intersection(source, face_id, previous, current, axis, offset))
        if inside[current]:
            corners.append(_original_corner(source, face_id, current))
    unique: list[_Corner] = []
    for corner in corners:
        if not unique or corner.geometry_key != unique[-1].geometry_key:
            unique.append(corner)
    if len(unique) > 1 and unique[0].geometry_key == unique[-1].geometry_key:
        unique.pop()
    return unique


def build_symmetry_variant(
    source: MeshState, *, plane_axis: int = 0, plane_offset: float = 0.0,
    keep_positive: bool = True, deadline: float,
) -> SymmetryVariant:
    """Clip to a closed half-space, then mirror every retained triangle.

    Source barycentric coordinates refer to the *unreflected* original face.
    For reflected faces apply the plane reflection after barycentric evaluation.
    Mirrored triangle corners follow [0, 2, 1], so ``face_pairs`` alone does not
    mean corner i corresponds to corner i. Vertex keys are (0, original vertex)
    or (1, original edge endpoint A, endpoint B); UV keys additionally name the
    original UV endpoint identities. Fully coplanar triangles require an explicit
    production policy and are rejected instead of silently doubling their surface.
    """
    check_deadline(deadline)
    if isinstance(plane_axis, bool) or plane_axis not in (0, 1, 2):
        raise ValueError("SYMMETRY_AXIS_MUST_BE_0_1_2")
    if not np.isfinite(plane_offset):
        raise ValueError("SYMMETRY_OFFSET_MUST_BE_FINITE")
    if (source.uv_faces.shape != source.mesh.faces.shape or source.uv.ndim != 2
            or source.uv.shape[1] != 2 or not np.isfinite(source.uv).all()
            or np.min(source.uv_faces) < 0 or np.max(source.uv_faces) >= len(source.uv)):
        raise ValueError("SYMMETRY_VALID_SOURCE_UV_REQUIRED")
    positions: list[FloatArray] = []
    uvs: list[FloatArray] = []
    faces: list[list[int]] = []
    uv_faces: list[list[int]] = []
    source_faces: list[int] = []
    barycentrics: list[FloatArray] = []
    vertex_indices: dict[tuple[int, ...], int] = {}
    uv_indices: dict[tuple[int, ...], int] = {}
    split_faces = clipped_faces = coplanar_faces = 0
    for face_id in range(len(source.mesh.faces)):
        check_deadline(deadline)
        polygon = _clip_face(source, face_id, plane_axis, plane_offset, keep_positive)
        if len(polygon) < 3:
            continue
        distances = source.mesh.vertices[source.mesh.faces[face_id], plane_axis] - plane_offset
        if np.all(distances == 0.0):
            raise ValueError(f"FULLY_COPLANAR_SOURCE_FACE_REQUIRES_POLICY:{face_id}")
        coplanar_faces += int(np.all(distances == 0.0))
        clipped_faces += int(np.any(distances > 0.0) and np.any(distances < 0.0))
        split_faces += int(len(polygon) == 4)
        for fan in range(1, len(polygon) - 1):
            triangle = [polygon[0], polygon[fan], polygon[fan + 1]]
            new_face: list[int] = []
            new_uv_face: list[int] = []
            for corner in triangle:
                if corner.geometry_key not in vertex_indices:
                    vertex_indices[corner.geometry_key] = len(positions)
                    positions.append(corner.position)
                if corner.uv_key not in uv_indices:
                    uv_indices[corner.uv_key] = len(uvs)
                    uvs.append(corner.uv)
                new_face.append(vertex_indices[corner.geometry_key])
                new_uv_face.append(uv_indices[corner.uv_key])
            faces.append(new_face)
            uv_faces.append(new_uv_face)
            source_faces.append(face_id)
            barycentrics.append(np.stack([corner.barycentric for corner in triangle]))
    if not faces:
        raise ValueError("SYMMETRY_EMPTY_RETAINED_HALF")
    check_deadline(deadline)
    retained_vertices = np.asarray(positions, dtype=np.float64)
    retained_faces = np.asarray(faces, dtype=np.int64)
    retained_uv = np.asarray(uvs, dtype=np.float64)
    retained_uv_faces = np.asarray(uv_faces, dtype=np.int64)
    mirrored_vertices = retained_vertices.copy()
    mirrored_vertices[:, plane_axis] = 2.0 * plane_offset - mirrored_vertices[:, plane_axis]
    corner_order = [0, 2, 1]
    all_faces = np.concatenate((retained_faces, retained_faces[:, corner_order] + len(positions)))
    all_uv_faces = np.concatenate((retained_uv_faces, retained_uv_faces[:, corner_order] + len(uvs)))
    mesh = make_mesh(np.concatenate((retained_vertices, mirrored_vertices)), all_faces)
    check_deadline(deadline)
    seams: set[int] = set()
    for edge_id, adjacent in enumerate(mesh.edge_faces):
        if edge_id % 256 == 0:
            check_deadline(deadline)
        if len(adjacent) != 2:
            continue
        first, second = adjacent[0][0], adjacent[1][0]
        for vertex in mesh.edges[edge_id]:
            a = int(np.flatnonzero(mesh.faces[first] == vertex)[0])
            b = int(np.flatnonzero(mesh.faces[second] == vertex)[0])
            if all_uv_faces[first, a] != all_uv_faces[second, b]:
                seams.add(edge_id)
                break
    surface = MeshState(mesh, frozenset(seams), np.concatenate((retained_uv, retained_uv)), all_uv_faces)
    count = len(faces)
    bary = np.asarray(barycentrics, dtype=np.float64)
    pairs = np.column_stack((np.arange(count, dtype=np.int64), np.arange(count, dtype=np.int64) + count))
    report: dict[str, object] = {
        "source_faces": len(source.mesh.faces), "retained_source_faces": len(set(source_faces)),
        "discarded_source_faces": len(source.mesh.faces) - len(set(source_faces)),
        "clipped_source_faces": clipped_faces, "split_source_faces": split_faces,
        "coplanar_source_faces": coplanar_faces, "retained_faces": count,
        "reflected_faces": count, "output_faces": 2 * count,
        "retained_vertices": len(positions), "output_vertices": 2 * len(positions),
        "retained_uv_vertices": len(uvs), "output_uv_vertices": 2 * len(uvs),
        "plane_axis": plane_axis, "plane_offset": plane_offset, "keep_positive": keep_positive,
        "midplane_geometry": "disconnected_explicit_source_identities",
        "mirrored_corner_permutation": corner_order, "coordinate_welding": False,
        "retained_area": float(mesh.areas[:count].sum()), "output_area": float(mesh.areas.sum()),
    }
    check_deadline(deadline)
    return SymmetryVariant(
        surface, np.tile(np.asarray(source_faces, dtype=np.int64), 2),
        np.concatenate((bary, bary[:, corner_order])),
        np.concatenate((np.zeros(count, dtype=np.bool_), np.ones(count, dtype=np.bool_))),
        pairs, np.arange(len(positions), dtype=np.int64) + len(positions),
        np.arange(len(uvs), dtype=np.int64) + len(uvs),
        tuple(vertex_indices), tuple(uv_indices), report,
    )
