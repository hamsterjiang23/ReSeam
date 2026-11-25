"""Transfer segmentation labels onto existing faces, without transferring topology."""

from pathlib import Path

import numpy as np
import trimesh

from ..types import FloatArray
from .state import check_deadline, file_hash, write_json


def transfer_labels(triangles: FloatArray, source: Path, output: Path, deadline: float,
                    maximum_fraction: float = .02) -> tuple[list[dict[str, object]], dict[str, object]]:
    scene = trimesh.load_scene(source, process=False)
    meshes: list[trimesh.Trimesh] = []
    names: list[str] = []
    labels: list[int] = []
    for node in sorted(scene.graph.nodes_geometry):
        transform, name = scene.graph[node]
        geometry = scene.geometry[name]
        if not isinstance(geometry, trimesh.Trimesh):
            raise TypeError("SEGMENT_TRIANGLES_REQUIRED")
        mesh = geometry.copy()
        mesh.apply_transform(transform)
        meshes.append(mesh)
        labels.extend([len(names)] * len(mesh.faces))
        names.append(str(node))
    if not meshes:
        raise ValueError("SEGMENT_PARTS_REQUIRED")
    combined = trimesh.util.concatenate(meshes)
    label_array = np.asarray(labels, dtype=np.int64)
    scale = float(np.linalg.norm(np.ptp(triangles.reshape(-1, 3), axis=0)))
    if not np.isfinite(maximum_fraction) or maximum_fraction <= 0 or scale <= 0:
        raise ValueError("SEGMENT_POSITIVE_DISTANCE_FRACTION_REQUIRED")
    bary = np.array([[1/3, 1/3, 1/3], [.8, .1, .1], [.1, .8, .1], [.1, .1, .8]])
    assigned: list[np.ndarray] = []
    distances: list[FloatArray] = []
    for start in range(0, len(triangles), 256):
        check_deadline(deadline)
        samples = np.einsum("ki,fij->fkj", bary, triangles[start:start+256])
        _, distance, faces = trimesh.proximity.closest_point(combined, samples.reshape(-1, 3))
        if not np.isfinite(distance).all():
            raise ValueError("SEGMENT_NEAREST_SURFACE_FAILED")
        assigned.append(label_array[faces].reshape(-1, 4))
        distances.append(distance.reshape(-1, 4))
    chosen = np.concatenate(assigned)
    distance = np.concatenate(distances)
    disputed = np.flatnonzero(np.any(chosen != chosen[:, :1], axis=1))
    too_far = np.flatnonzero(np.max(distance, axis=1) > scale*maximum_fraction)
    report: dict[str, object] = {"source": str(source.resolve()), "sha256": file_hash(source),
        "method": "nearest_segment_surface_centroid_label_plus_three_interior_witnesses",
        "scope": "semantic label candidates only; not vertex welding or original face ancestry",
        "maximum_distance": float(distance.max()), "mean_distance": float(distance.mean()),
        "maximum_fraction": maximum_fraction, "normalization": "original_source_aabb_diagonal",
        "boundary_disputed_faces": disputed.tolist(), "too_far_faces": too_far.tolist(),
        "geometry_modified": False, "source_face_count": len(triangles)}
    write_json(output / "segmentation_transfer.json", report)
    np.savez_compressed(output / "segmentation_witnesses.npz", labels=chosen, distances=distance)
    if len(too_far):
        raise ValueError("SEGMENT_ALIGNMENT_DISTANCE_EXCEEDED:see segmentation_transfer.json")
    parts = [{"id": name, "name": name, "faces": np.flatnonzero(chosen[:, 0] == index).tolist()}
             for index, name in enumerate(names) if np.any(chosen[:, 0] == index)]
    report["empty_labels"] = [name for index, name in enumerate(names) if not np.any(chosen[:, 0] == index)]
    write_json(output / "segmentation_transfer.json", report)
    return parts, report
