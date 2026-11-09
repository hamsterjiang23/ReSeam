"""Copy a generated mirror's UV through its hash-bound face/corner provenance."""

from pathlib import Path

import numpy as np

from ..surface_match import reflect
from ..types import FloatArray, IntArray
from .state import Snapshot, file_hash, load_snapshot


def copy_mirror_identity(
    snapshot: Snapshot, first: tuple[int, ...], second: tuple[int, ...],
    normal: FloatArray, offset: float, source: object,
) -> tuple[tuple[FloatArray, IntArray], dict[str, object]]:
    if not isinstance(source, dict) or not isinstance(source.get("snapshot"), str):
        raise TypeError("MIRROR_SOURCE_SNAPSHOT_REQUIRED")
    origin = load_snapshot(Path(source["snapshot"]))
    if source.get("state_hash") != origin.state_hash:
        raise ValueError("MIRROR_SOURCE_STATE_HASH_MISMATCH")
    mesh, original = snapshot.state.surface.mesh, origin.state.surface.mesh
    if (snapshot.asset_id != origin.asset_id or not np.array_equal(mesh.vertices, original.vertices)
            or not np.array_equal(mesh.faces, original.faces)):
        raise ValueError("MIRROR_SOURCE_INDEXED_GEOMETRY_MISMATCH")
    metadata = origin.metadata
    if metadata.get("geometry_variant") != "plane_clip_then_exact_mirror":
        raise ValueError("MIRROR_SOURCE_VARIANT_REQUIRED")
    raw_path = metadata.get("source_correspondence")
    if not isinstance(raw_path, str):
        raise TypeError("MIRROR_SOURCE_CORRESPONDENCE_REQUIRED")
    path = Path(raw_path).resolve()
    sources = metadata.get("source_manifest", [])
    if not isinstance(sources, list) or not any(
        isinstance(item, dict) and isinstance(item.get("path"), str)
        and Path(item["path"]).resolve() == path and item.get("sha256") == file_hash(path)
        for item in sources
    ):
        raise ValueError("MIRROR_SOURCE_CORRESPONDENCE_NOT_HASH_BOUND")
    with np.load(path, allow_pickle=False) as data:
        pairs = data["face_pairs"]
    if (pairs.ndim != 2 or pairs.shape[1] != 2 or pairs.dtype.kind not in "iu"
            or not np.array_equal(np.sort(pairs.reshape(-1)), np.arange(len(mesh.faces)))):
        raise ValueError("MIRROR_SOURCE_FACE_PAIRS_NOT_BIJECTIVE")
    # Only the creator's axis-aligned reflection and fixed corner permutation
    # are supported. No nearest-coordinate welding or face matching happens.
    if np.count_nonzero(np.abs(normal) > 1e-12) != 1:
        raise ValueError("MIRROR_SOURCE_AXIS_ALIGNED_REFLECTION_REQUIRED")
    first_points = mesh.vertices[mesh.faces[pairs[:, 0]]]
    second_points = mesh.vertices[mesh.faces[pairs[:, 1]]][:, [0, 2, 1]]
    expected = reflect(first_points, normal, offset)
    numerical = np.finfo(np.float64).eps * max(1., float(np.abs(mesh.vertices).max()), abs(offset)) * 8
    if np.array_equal(mesh.vertices, mesh.vertices.astype(np.float32).astype(np.float64)):
        # Both generated halves were stored as float32. Each rounded input can
        # differ from the pre-export coordinate by at most half its local ULP.
        bound = .5 * (np.spacing(np.abs(first_points).astype(np.float32)).astype(np.float64)
                      + np.spacing(np.abs(second_points).astype(np.float32)).astype(np.float64)) + numerical
    else:
        bound = np.full_like(first_points, numerical)
    error = np.abs(expected - second_points)
    if np.any(error > bound):
        raise ValueError("MIRROR_SOURCE_REFLECTION_MISMATCH")
    lookup = {int(a): int(b) for a, b in pairs}
    lookup.update({int(b): int(a) for a, b in pairs})
    if len(first) != len(second) or {lookup[face] for face in first} != set(second):
        raise ValueError("MIRROR_SOURCE_SELECTION_NOT_PAIRED")
    reverse = {lookup[face]: face for face in first}
    surface = snapshot.state.surface
    corners = np.empty((len(second), 3, 2))
    identities = np.empty((len(second), 3), dtype=np.int64)
    vertex_pairs: dict[int, int] = {}
    for row, target in enumerate(second):
        source_face = reverse[target]
        source_corners = [0, 2, 1]
        for target_vertex, source_vertex in zip(mesh.faces[target], mesh.faces[source_face, source_corners], strict=True):
            a, b = int(target_vertex), int(source_vertex)
            if a in vertex_pairs and vertex_pairs[a] != b:
                raise ValueError("MIRROR_SOURCE_INCONSISTENT_VERTEX_PAIR")
            vertex_pairs[a] = b
        source_ids = surface.uv_faces[source_face, source_corners]
        corners[row] = surface.uv[source_ids]
        identities[row] = source_ids + len(surface.uv)
    if len(set(vertex_pairs.values())) != len(vertex_pairs):
        raise ValueError("MIRROR_SOURCE_VERTEX_PAIRS_NOT_BIJECTIVE")
    record: dict[str, object] = {
        "snapshot": str(origin.root / "snapshot.json"), "state_hash": origin.state_hash,
        "correspondence": str(path), "sha256": file_hash(path), "face_pairs": len(second),
        "vertex_pairs": len(vertex_pairs), "corner_permutation": [0, 2, 1],
        "maximum_reflection_error": float(error.max()), "maximum_rounding_bound": float(bound.max()),
        "coordinate_welding": False,
    }
    return (corners, identities), record
