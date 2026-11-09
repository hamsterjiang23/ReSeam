"""Canonical topology construction and mesh/UV interchange."""

import hashlib
import json
from pathlib import Path
from typing import Any

import numpy as np
from scipy.spatial import KDTree

from .types import FloatArray, IntArray, Mesh, MeshState


class DisjointSet:
    def __init__(self, count: int) -> None:
        self.parent = list(range(count))

    def find(self, item: int) -> int:
        while self.parent[item] != item:
            self.parent[item] = self.parent[self.parent[item]]
            item = self.parent[item]
        return item

    def union(self, first: int, second: int) -> bool:
        a, b = self.find(first), self.find(second)
        if a == b:
            return False
        self.parent[b] = a
        return True


def make_mesh(vertices: FloatArray, faces: IntArray) -> Mesh:
    v = np.asarray(vertices, dtype=np.float64).copy()
    f = np.asarray(faces, dtype=np.int64).copy()
    if v.ndim != 2 or v.shape[1] != 3 or f.ndim != 2 or f.shape[1] != 3:
        raise ValueError("Only triangular meshes with 3D coordinates are supported")
    if not len(f) or not np.isfinite(v).all() or f.min() < 0 or f.max() >= len(v):
        raise ValueError("Invalid mesh indices or coordinates")
    cross = np.cross(v[f[:, 1]] - v[f[:, 0]], v[f[:, 2]] - v[f[:, 0]])
    areas = np.linalg.norm(cross, axis=1) * 0.5
    if np.any(areas <= max(float(areas.sum()) * 1e-15, np.finfo(float).tiny)):
        raise ValueError("Degenerate input faces")
    adjacency: dict[tuple[int, int], list[tuple[int, int]]] = {}
    for fi, face in enumerate(f):
        for corner in range(3):
            a, b = int(face[corner]), int(face[(corner + 1) % 3])
            edge = (min(a, b), max(a, b))
            adjacency.setdefault(edge, []).append((fi, corner))
    for edge, entries in adjacency.items():
        if len(entries) > 2:
            raise ValueError(f"Nonmanifold edge {edge}")
        if len(entries) == 2:
            (a, ca), (b, cb) = entries
            if f[a, ca] == f[b, cb]:
                raise ValueError(f"Inconsistent face orientation at {edge}")
    edges = tuple(sorted(adjacency))
    v.setflags(write=False)
    f.setflags(write=False)
    areas.setflags(write=False)
    return Mesh(v, f, edges, tuple(tuple(adjacency[e]) for e in edges), areas, float(np.sqrt(areas.sum())))


def read_obj(path: Path) -> tuple[FloatArray, IntArray, FloatArray, IntArray]:
    vertices, faces, uvs, uv_faces = [], [], [], []
    for line in path.read_text(encoding="utf-8").splitlines():
        row = line.split()
        if not row:
            continue
        if row[0] == "v":
            vertices.append([float(x) for x in row[1:4]])
        elif row[0] == "vt":
            uvs.append([float(x) for x in row[1:3]])
        elif row[0] == "f":
            if len(row) != 4:
                raise ValueError("OBJ faces must already be triangulated")
            indices = [x.split("/") for x in row[1:]]
            faces.append([int(x[0]) - 1 if int(x[0]) > 0 else len(vertices) + int(x[0]) for x in indices])
            if all(len(x) > 1 and x[1] for x in indices):
                uv_faces.append([int(x[1]) - 1 if int(x[1]) > 0 else len(uvs) + int(x[1]) for x in indices])
    return (
        np.asarray(vertices, dtype=float),
        np.asarray(faces, dtype=np.int64),
        np.asarray(uvs, dtype=float).reshape(-1, 2),
        np.asarray(uv_faces, dtype=np.int64).reshape(-1, 3),
    )


def load_mesh(path: Path) -> Mesh:
    v, f, _, _ = read_obj(path)
    return make_mesh(v, f)


def corner_map(mesh: Mesh, seams: frozenset[int]) -> IntArray:
    dsu = DisjointSet(len(mesh.faces) * 3)
    for ei, entries in enumerate(mesh.edge_faces):
        if ei in seams or len(entries) != 2:
            continue
        (a, _), (b, _) = entries
        for vertex in mesh.edges[ei]:
            ca = int(np.flatnonzero(mesh.faces[a] == vertex)[0])
            cb = int(np.flatnonzero(mesh.faces[b] == vertex)[0])
            dsu.union(a * 3 + ca, b * 3 + cb)
    labels: dict[int, int] = {}
    output = np.empty(len(mesh.faces) * 3, dtype=np.int64)
    for corner in range(len(output)):
        root = dsu.find(corner)
        output[corner] = labels.setdefault(root, len(labels))
    return output.reshape(-1, 3)


def chart_labels(state: MeshState) -> IntArray:
    dsu = DisjointSet(len(state.mesh.faces))
    for ei, entries in enumerate(state.mesh.edge_faces):
        if len(entries) == 2 and ei not in state.seams:
            dsu.union(entries[0][0], entries[1][0])
    roots: dict[int, int] = {}
    return np.asarray(
        [roots.setdefault(dsu.find(i), len(roots)) for i in range(len(state.mesh.faces))], dtype=np.int64
    )


def state_from_corner_uv(
    mesh: Mesh,
    corner_uv: FloatArray,
    *,
    corner_ids: IntArray | None = None,
    seam_ids: frozenset[int] | None = None,
) -> MeshState:
    if corner_ids is None and seam_ids is None:
        raise ValueError("Explicit UV identities or backend seam connectivity are required")
    seams: set[int] = set()
    for ei, entries in enumerate(mesh.edge_faces):
        if len(entries) != 2:
            continue
        (a, _), (b, _) = entries
        for vertex in mesh.edges[ei]:
            ca = int(np.flatnonzero(mesh.faces[a] == vertex)[0])
            cb = int(np.flatnonzero(mesh.faces[b] == vertex)[0])
            cut = (
                ei in seam_ids
                if seam_ids is not None
                else (corner_ids is not None and corner_ids[a, ca] != corner_ids[b, cb])
            )
            if cut:
                seams.add(ei)
                break
    mapping = corner_map(mesh, frozenset(seams))
    uv = np.zeros((int(mapping.max()) + 1, 2))
    uv[mapping.reshape(-1)] = corner_uv.reshape(-1, 2)
    return MeshState(mesh, frozenset(seams), uv, mapping)


def import_uv_obj(mesh: Mesh, path: Path) -> MeshState:
    v, f, uv, fuv = read_obj(path)
    return import_uv_arrays(mesh, v, f, uv, fuv)


def import_uv_arrays(mesh: Mesh, v: FloatArray, f: IntArray, uv: FloatArray, fuv: IntArray) -> MeshState:
    if len(f) != len(mesh.faces) or fuv.shape != f.shape or not len(uv):
        raise ValueError("Backend changed face count or omitted UV")
    distances, mapping = KDTree(mesh.vertices).query(v, k=2)
    distances = np.asarray(distances, dtype=float)
    mapping = np.asarray(mapping, dtype=np.int64)
    tol = mesh.scale * 1e-6
    if np.max(distances[:, 0]) > tol or np.any(distances[:, 1] < tol):
        raise ValueError("Backend geometry changed or vertex identity is ambiguous")
    mapped = mapping[:, 0][f]
    face_lookup = {tuple(sorted(map(int, face))): i for i, face in enumerate(mesh.faces)}
    result = np.empty((len(f), 3, 2))
    reordered_ids = np.empty((len(f), 3), dtype=np.int64)
    visited: set[int] = set()
    for fi, face in enumerate(mapped):
        target = face_lookup.get(tuple(sorted(map(int, face))))
        if target is None or target in visited:
            raise ValueError("Backend face coverage differs")
        visited.add(target)
        for ci, vertex in enumerate(mesh.faces[target]):
            source_corner = int(np.flatnonzero(face == vertex)[0])
            result[target, ci] = uv[fuv[fi, source_corner]]
            reordered_ids[target, ci] = fuv[fi, source_corner]
    return state_from_corner_uv(mesh, result, corner_ids=reordered_ids)


def write_obj(path: Path, state: MeshState) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as stream:
        for vertex in state.mesh.vertices:
            stream.write("v " + " ".join(format(float(x), ".17g") for x in vertex) + "\n")
        for point in state.uv:
            stream.write("vt " + " ".join(format(float(x), ".17g") for x in point) + "\n")
        for face, face_uv in zip(state.mesh.faces, state.uv_faces, strict=True):
            stream.write(
                "f "
                + " ".join(f"{int(v) + 1}/{int(t) + 1}" for v, t in zip(face, face_uv, strict=True))
                + "\n"
            )


def state_dict(state: MeshState) -> dict[str, Any]:
    return {
        "vertices": state.mesh.vertices.tolist(),
        "faces": state.mesh.faces.tolist(),
        "uv": state.uv.tolist(),
        "uv_faces": state.uv_faces.tolist(),
        "seams": sorted(state.seams),
        "version": state.version,
    }


def save_state(path: Path, state: MeshState) -> None:
    path.write_text(json.dumps(state_dict(state), allow_nan=False) + "\n", encoding="utf-8")


def load_state(path: Path) -> MeshState:
    data = json.loads(path.read_text(encoding="utf-8"))
    return MeshState(
        make_mesh(np.asarray(data["vertices"]), np.asarray(data["faces"])),
        frozenset(data["seams"]),
        np.asarray(data["uv"], dtype=float),
        np.asarray(data["uv_faces"], dtype=np.int64),
        data.get("version", 0),
    )


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()
