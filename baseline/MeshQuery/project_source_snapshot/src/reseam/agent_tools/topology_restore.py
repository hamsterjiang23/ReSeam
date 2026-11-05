"""Restore selected source-index connectivity without moving any face corner."""

import hashlib
import shutil
from pathlib import Path

import numpy as np

from ..mesh import DisjointSet, make_mesh, state_from_corner_uv
from ..types import FloatArray, IntArray, MeshState
from ..validation import topology_errors
from .state import check_deadline, file_hash, load_snapshot, object_hash


def _array_hash(array: FloatArray | IntArray) -> str:
    return hashlib.sha256(np.ascontiguousarray(array).tobytes()).hexdigest()


def _regions(value: object, face_count: int) -> list[list[int]]:
    if not isinstance(value, list) or not value:
        raise ValueError("RESTORE_REGIONS_REQUIRED: nonempty disjoint face lists")
    result: list[list[int]] = []
    seen: set[int] = set()
    for region in value:
        if (not isinstance(region, list) or not region
                or any(type(face) is not int or not 0 <= face < face_count for face in region)):
            raise ValueError("INVALID_RESTORE_REGION_FACE_IDS")
        selected = set(region)
        if len(selected) != len(region) or seen.intersection(selected):
            raise ValueError("REPEATED_OR_OVERLAPPING_RESTORE_REGIONS")
        seen.update(selected)
        result.append(sorted(selected))
    return result


def _source_faces(path: Path, surface: MeshState) -> tuple[IntArray, dict[str, object]]:
    with np.load(path, allow_pickle=False) as data:
        vertices = np.asarray(data["vertices"], dtype=float)
        faces = np.asarray(data["faces"])
        uv = np.asarray(data["corner_uv"], dtype=float)
        corner_ids = np.asarray(data["corner_ids"])
    corners = surface.mesh.vertices[surface.mesh.faces]
    if (vertices.ndim != 2 or vertices.shape[1] != 3 or not np.isfinite(vertices).all()
            or faces.shape != surface.mesh.faces.shape or faces.dtype.kind not in "iu"
            or np.any(faces < 0) or np.any(faces >= len(vertices))
            or corner_ids.shape != faces.shape or corner_ids.dtype.kind not in "iu"
            or np.any(corner_ids < 0) or uv.shape != (*faces.shape, 2) or not np.isfinite(uv).all()):
        raise ValueError("INVALID_RESTORE_ORIGINAL_TOPOLOGY_ARRAYS")
    original_faces = np.asarray(faces, dtype=np.int64)
    order = np.argsort(corner_ids.ravel(), kind="stable")
    repeated = np.diff(corner_ids.ravel()[order]) == 0
    if (np.any(np.diff(original_faces.ravel()[order])[repeated] != 0)
            or np.any(np.diff(uv.reshape(-1, 2)[order], axis=0)[repeated] != 0)):
        raise ValueError("INCONSISTENT_RESTORE_ORIGINAL_CORNER_ID")
    diagonal = float(np.linalg.norm(np.ptp(corners.reshape(-1, 3), axis=0)))
    position_error = float(np.max(np.abs(vertices[original_faces] - corners)))
    uv_error = float(np.max(np.abs(uv - surface.uv[surface.uv_faces])))
    if position_error > max(1e-12, diagonal * 1e-6) or uv_error > 1e-6:
        raise ValueError("RESTORE_ORIGINAL_FACE_CORNER_WITNESS_MISMATCH")

    # The float bridge above authenticates raw FBX -> GLB ordering only. Merging
    # source IDs below requires bit-identical canonical coordinates, never a
    # tolerance, nearest neighbor search, averaging or a moved vertex.
    positions: dict[int, bytes] = {}
    ancestry: dict[int, int] = {}
    for old, original, point in zip(surface.mesh.faces.ravel(), original_faces.ravel(),
                                    corners.reshape(-1, 3), strict=True):
        old_id, original_id = int(old), int(original)
        encoded = point.tobytes()
        if positions.setdefault(original_id, encoded) != encoded:
            raise ValueError("RESTORE_CANONICAL_COPIES_NOT_EXACT")
        if ancestry.setdefault(old_id, original_id) != original_id:
            raise ValueError("RESTORE_CANONICAL_VERTEX_HAS_AMBIGUOUS_SOURCE_ID")
    return original_faces, {
        "face_corner_position_bridge_max_error": position_error,
        "face_corner_uv_bridge_max_error": uv_error,
        "canonical_copy_position_spread": 0,
        "original_corner_ids_sha256": _array_hash(np.asarray(corner_ids, dtype=np.int64)),
        "original_faces_sha256": _array_hash(original_faces),
        "position_witness_tolerance": max(1e-12, diagonal * 1e-6),
        "uv_witness_tolerance": 1e-6,
    }


def _manifold_region(faces: IntArray, selected: list[int], index: int) -> dict[str, object]:
    edges: dict[tuple[int, int], list[tuple[int, int, int]]] = {}
    incident: dict[int, set[int]] = {}
    for local, face in enumerate(selected):
        triangle = faces[face]
        if len(set(map(int, triangle))) != 3:
            raise ValueError(f"RESTORE_DEGENERATE_ORIGINAL_FACE:{face}")
        for corner in range(3):
            first, second = int(triangle[corner]), int(triangle[(corner + 1) % 3])
            edges.setdefault((min(first, second), max(first, second)), []).append((local, first, second))
            incident.setdefault(first, set()).add(local)
    connected = DisjointSet(len(selected))
    links: dict[int, dict[int, set[int]]] = {
        vertex: {face: set() for face in members} for vertex, members in incident.items()
    }
    boundary_degree: dict[int, int] = {}
    for edge, entries in edges.items():
        if len(entries) > 2:
            raise ValueError(f"RESTORE_NONMANIFOLD_REGION_EDGE:{index}:{edge}")
        if len(entries) == 2:
            (first, a, _), (second, b, _) = entries
            if a == b:
                raise ValueError(f"RESTORE_INCONSISTENT_REGION_ORIENTATION:{index}:{edge}")
            connected.union(first, second)
            for vertex in edge:
                links[vertex][first].add(second)
                links[vertex][second].add(first)
        else:
            for vertex in edge:
                boundary_degree[vertex] = boundary_degree.get(vertex, 0) + 1
    # Edge valence alone misses a bow-tie vertex. Its incident face fan must be
    # one path/cycle, with either zero or two boundary edges at that vertex.
    for vertex, fan in links.items():
        visited: set[int] = set()
        pending = [next(iter(fan))]
        while pending:
            item = pending.pop()
            if item not in visited:
                visited.add(item)
                pending.extend(fan[item] - visited)
        if len(visited) != len(fan) or boundary_degree.get(vertex, 0) not in (0, 2):
            raise ValueError(f"RESTORE_NONMANIFOLD_REGION_VERTEX:{index}:{vertex}")
    if len({connected.find(face) for face in range(len(selected))}) != 1:
        raise ValueError(f"RESTORE_REGION_NOT_CONNECTED:{index}")
    return {"region_index": index, "faces": selected, "face_count": len(selected),
            "original_vertex_count": len(incident), "edge_count": len(edges),
            "boundary_edge_count": sum(len(entries) == 1 for entries in edges.values()),
            "euler_characteristic": len(incident) - len(edges) + len(selected),
            "connected_components": 1, "manifold": True}


def restore_import_topology(
    surface: MeshState, request: dict[str, object], inputs: Path, deadline: float,
) -> tuple[MeshState, dict[str, object], dict[str, object]]:
    """Produce an independent normalization variant with unchanged UV copies."""
    raw_topology = request.get("original_topology")
    raw_parent = request.get("source_parent_snapshot")
    if not isinstance(raw_topology, str) or not isinstance(raw_parent, str):
        raise TypeError("RESTORE_REQUIRES_ORIGINAL_TOPOLOGY_AND_SOURCE_PARENT_SNAPSHOT")
    parent = load_snapshot(Path(raw_parent))
    previous = parent.state.surface
    if (not np.array_equal(surface.mesh.vertices, previous.mesh.vertices)
            or not np.array_equal(surface.mesh.faces, previous.mesh.faces)
            or not np.array_equal(surface.uv, previous.uv)
            or not np.array_equal(surface.uv_faces, previous.uv_faces)):
        raise ValueError("RESTORE_SOURCE_PARENT_GEOMETRY_OR_UV_IDENTITY_MISMATCH")
    check_deadline(deadline)
    regions = _regions(request.get("restore_regions"), len(surface.mesh.faces))
    path = Path(raw_topology).resolve()
    digest = file_hash(path)
    original, witness = _source_faces(path, surface)
    reports = []
    for index, region in enumerate(regions):
        check_deadline(deadline)
        reports.append(_manifold_region(original, region, index))
    membership = np.full(len(surface.mesh.faces), -1, dtype=np.int64)
    for index, region in enumerate(regions):
        membership[region] = index
    mapping: dict[tuple[int, int], int] = {}
    vertices: list[FloatArray] = []
    restored_faces = np.empty_like(surface.mesh.faces)
    for face, triangle in enumerate(surface.mesh.faces):
        group = int(membership[face])
        for corner, old in enumerate(triangle):
            key = (group, int(original[face, corner]) if group >= 0 else int(old))
            if key not in mapping:
                mapping[key] = len(vertices)
                vertices.append(surface.mesh.vertices[old].copy())
            restored_faces[face, corner] = mapping[key]
    restored_vertices = np.asarray(vertices, dtype=float)
    before = surface.mesh.vertices[surface.mesh.faces]
    after = restored_vertices[restored_faces]
    if before.tobytes() != after.tobytes():
        raise ValueError("RESTORE_CHANGED_FACE_CORNER_COORDINATES")
    uv_owner: dict[int, int] = {}
    for uv_id, geom_id in zip(surface.uv_faces.ravel(), restored_faces.ravel(), strict=True):
        if uv_owner.setdefault(int(uv_id), int(geom_id)) != int(geom_id):
            raise ValueError("RESTORE_BOUNDARY_CROSSES_EXISTING_UV_COPY: follow existing UV cuts")
    mesh = make_mesh(restored_vertices, restored_faces)
    connectivity = state_from_corner_uv(mesh, surface.uv[surface.uv_faces], corner_ids=surface.uv_faces)
    restored = MeshState(mesh, connectivity.seams, surface.uv.copy(), surface.uv_faces.copy())
    errors = topology_errors(restored)
    if errors:
        raise ValueError("RESTORE_UV_TOPOLOGY_INVALID:" + ";".join(errors))
    destination = inputs / "original_topology.npz"
    shutil.copy2(path, destination)
    if file_hash(destination) != digest or file_hash(path) != digest:
        raise ValueError("RESTORE_ORIGINAL_TOPOLOGY_CHANGED_DURING_COPY")
    before_hash = _array_hash(surface.mesh.vertices) + ":" + _array_hash(surface.mesh.faces)
    after_hash = _array_hash(mesh.vertices) + ":" + _array_hash(mesh.faces)
    report: dict[str, object] = {
        "method": "region-scoped authoritative source vertex incidence; exact canonical coordinates",
        "source_parent_snapshot": str(parent.root / "snapshot.json"),
        "source_parent_state_hash": parent.state_hash, "source_parent_asset_id": parent.asset_id,
        "source_parent_geometry_hash": hashlib.sha256(
            surface.mesh.vertices.tobytes() + surface.mesh.faces.tobytes()).hexdigest(),
        "geometry_representation_changed": before_hash != after_hash,
        "geometry_representation_hash": hashlib.sha256(mesh.vertices.tobytes() + mesh.faces.tobytes()).hexdigest(),
        "face_corner_geometry_unchanged": True, "face_corner_geometry_sha256": _array_hash(after),
        "face_order_and_winding_unchanged": True,
        "uv_values_and_copy_ids_unchanged": True, "uv_values_sha256": _array_hash(restored.uv),
        "uv_faces_sha256": _array_hash(restored.uv_faces),
        "original_topology": str(destination.resolve()), "original_topology_sha256": digest,
        "regions": reports, "restoration_config_hash": object_hash({"regions": regions, "method_version": 1}),
        "before_vertices": len(surface.mesh.vertices), "after_vertices": len(mesh.vertices),
        "region_boundary_policy": "outside retains canonical IDs in separate namespace; existing UV cuts required",
        "surface_positions_policy": "copy canonical indexed point; no averaging, coordinate welding or remeshing",
        "requires_new_baseline_round": True, "topology_errors": errors, "witness": witness,
    }
    source_record: dict[str, object] = {
        "path": str(destination.resolve()), "original_path": str(path), "sha256": digest,
    }
    check_deadline(deadline)
    return restored, report, source_record
