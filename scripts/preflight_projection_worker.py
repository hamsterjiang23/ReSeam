"""Blender-only finite BVH probes, independent of Cycles bake coverage."""

import json
import math
import sys
import time
from pathlib import Path
from typing import Any

import bpy
import numpy as np
from mathutils import Vector
from mathutils.bvhtree import BVHTree


def write(path: Path, data: object) -> None:
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, allow_nan=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def barycentric_samples() -> list[list[float]]:
    return [[1 - math.sqrt((i + .5) / 4), math.sqrt((i + .5) / 4) * (1 - (j + .5) / 4),
             math.sqrt((i + .5) / 4) * (j + .5) / 4] for i in range(4) for j in range(4)]


def intersects(a: np.ndarray, b: np.ndarray, tolerance: float) -> bool:
    """Triangle SAT including in-plane axes for coplanar triangles."""
    ea, eb = np.roll(a, -1, axis=0) - a, np.roll(b, -1, axis=0) - b
    na, nb = np.cross(ea[0], ea[1]), np.cross(eb[0], eb[1])
    axes = [na, nb, *[np.cross(x, y) for x in ea for y in eb],
            *[np.cross(na, x) for x in ea], *[np.cross(nb, x) for x in eb]]
    for axis in axes:
        length = np.linalg.norm(axis)
        if length <= 1e-30:
            continue
        pa, pb = a @ (axis / length), b @ (axis / length)
        if pa.max() < pb.min() - tolerance or pb.max() < pa.min() - tolerance:
            return False
    return True


def run(config: dict[str, Any]) -> None:
    output = Path(config["output"])
    deadline = time.monotonic() + config["seconds"]
    with np.load(config["arrays"], allow_pickle=False) as data:
        low, faces = data["low_vertices"], data["low_faces"]
        cage, high, high_faces = data["cage_vertices"], data["high_vertices"], data["high_faces"]
    diagonal = config["diagonal"]
    tolerance = max(diagonal * 1e-7, 1e-12)
    low_triangles, cage_triangles = low[faces], cage[faces]
    low_cross = np.cross(low_triangles[:, 1] - low_triangles[:, 0], low_triangles[:, 2] - low_triangles[:, 0])
    cage_cross = np.cross(cage_triangles[:, 1] - cage_triangles[:, 0], cage_triangles[:, 2] - cage_triangles[:, 0])
    degenerate = np.flatnonzero(np.linalg.norm(cage_cross, axis=1) <= diagonal**2 * 1e-12).tolist()
    flipped = np.flatnonzero(np.sum(low_cross * cage_cross, axis=1) <= 0).tolist()
    collisions: list[list[int]] = []
    isolated_collisions: list[list[int]] = []
    face_targets: dict[int, set[str]] = {}
    for pairing in config["part_pairings"]:
        for face in pairing["low_faces"]:
            face_targets[face] = set(pairing["high_objects"])
    report: dict[str, Any] = {"status": "not_checked", "complete": False,
        "blender_version": bpy.app.version_string, "blender_build_hash": bpy.app.build_hash.decode(),
        "samples_expected": len(faces) * 16, "samples_completed": 0,
        "problem_faces": [], "counts": {"hit": 0, "backface_hit": 0, "out_of_range": 0, "miss": 0,
                                          "invalid_direction": 0},
        "cage": {"degenerate_faces": degenerate, "direction_reversal_faces": flipped,
                 "nonadjacent_intersections": collisions, "isolated_by_disjoint_part_filters": isolated_collisions,
                 "intersection_check_complete": False,
                 "scope": "exclude pairs sharing original vertices; local reversal is relative to low face normal"},
        "limits": "16 deterministic interior samples per low triangle; first hit; no continuous/Cycles certificate"}
    write(output / "projection.json", report)
    tree = BVHTree.FromPolygons(high.tolist(), high_faces.tolist(), all_triangles=True)
    cage_tree = BVHTree.FromPolygons(cage.tolist(), faces.tolist(), all_triangles=True)
    for a, b in cage_tree.overlap(cage_tree):
        if time.monotonic() >= deadline:
            report["reason"] = "CAGE_CHECK_DEADLINE"
            write(output / "projection.json", report)
            return
        if a >= b or set(faces[a]).intersection(faces[b]):
            continue
        if intersects(cage_triangles[a], cage_triangles[b], tolerance):
            destination = isolated_collisions if (a in face_targets and b in face_targets
                and face_targets[a].isdisjoint(face_targets[b])) else collisions
            destination.append([int(a), int(b)])
    report["cage"]["intersection_check_complete"] = True
    objects = config["high_objects"]
    object_ids = ["unassigned"] * len(high_faces)
    for obj in objects:
        for face in range(obj["face_start"], obj["face_start"] + obj["face_count"]):
            object_ids[face] = obj["name"]
    pair_trees: dict[int, tuple[BVHTree, list[int]]] = {}
    for pairing in config["part_pairings"]:
        selected = [i for i, name in enumerate(object_ids) if name in pairing["high_objects"]]
        subset = BVHTree.FromPolygons(high.tolist(), high_faces[selected].tolist(), all_triangles=True)
        for face in pairing["low_faces"]:
            pair_trees[face] = subset, selected
    problems = set(degenerate + flipped + [f for pair in collisions for f in pair])
    samples = barycentric_samples()
    with (output / "samples.jsonl").open("w", encoding="utf-8") as stream:
        for face_id, triangle in enumerate(low_triangles):
            ray_tree, source_ids = pair_trees.get(face_id, (tree, None))
            normal = low_cross[face_id] / np.linalg.norm(low_cross[face_id])
            for weights in samples:
                if time.monotonic() >= deadline:
                    stream.flush()
                    report.update(reason="PROJECTION_SAMPLE_DEADLINE", problem_faces=sorted(problems))
                    write(output / "projection.json", report)
                    return
                point = np.asarray(weights) @ triangle
                origin = np.asarray(weights) @ cage_triangles[face_id]
                direction = point - origin
                distance = float(np.linalg.norm(direction))
                maximum = distance + config["back_distance"]
                classification = "invalid_direction"
                hit = None
                nearest = ray_tree.find_nearest(Vector(point))
                if distance > tolerance:
                    direction /= distance
                    location, hit_normal, index, ray_distance = ray_tree.ray_cast(Vector(origin), Vector(direction), maximum)
                    if index is None:
                        extended = ray_tree.ray_cast(Vector(origin), Vector(direction), maximum + 2 * diagonal)
                        classification = "out_of_range" if extended[2] is not None else "miss"
                        if extended[2] is not None:
                            location, hit_normal, index, ray_distance = extended
                    else:
                        classification = "hit" if np.dot(normal, hit_normal) > 0 else "backface_hit"
                    if index is not None:
                        global_id = int(index if source_ids is None else source_ids[index])
                        hit = {"high_face": global_id, "high_object": object_ids[global_id],
                               "position": list(location), "distance": float(ray_distance),
                               "normal_angle_degrees": math.degrees(math.acos(float(np.clip(
                                   np.dot(normal, hit_normal), -1, 1)))),
                               "within_requested_range": classification in ("hit", "backface_hit")}
                row = {"sample_id": report["samples_completed"], "low_face": face_id, "barycentric": weights,
                       "point": point.tolist(), "origin": origin.tolist(), "direction": direction.tolist(),
                       "range": maximum, "classification": classification, "hit": hit,
                       "nearest_surface_distance": None if nearest[0] is None else float(nearest[3]),
                       "part_filter_applied": face_id in pair_trees}
                stream.write(json.dumps(row, allow_nan=False) + "\n")
                report["counts"][classification] += 1
                report["samples_completed"] += 1
                if classification != "hit":
                    problems.add(face_id)
            stream.flush()
            if face_id % 64 == 0:
                report["problem_faces"] = sorted(problems)
                write(output / "projection.json", report)
    failures = sum(report["counts"][k] for k in ("miss", "out_of_range", "invalid_direction"))
    report.update(complete=True, problem_faces=sorted(problems), status="fail" if failures or degenerate
                  or flipped or collisions else "warn" if report["counts"]["backface_hit"] else "pass")
    write(output / "projection.json", report)


if __name__ == "__main__":
    run(json.loads(Path(sys.argv[sys.argv.index("--") + 1]).read_text(encoding="utf-8")))
