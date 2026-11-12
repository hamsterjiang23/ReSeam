"""Snapshot-bound adapter for isolated Blender BVH diagnostics."""

import os
import subprocess
import time
from pathlib import Path
from typing import cast

import numpy as np
from PIL import Image, ImageDraw

from .preflight import finite_number, mapping
from .state import Snapshot, file_hash, load_snapshot, read_object, write_json


def _objects(snapshot: Snapshot) -> list[dict[str, object]]:
    records = cast(list[dict[str, object]], snapshot.metadata.get("objects", []))
    if not records:
        return [{"name": "all", "face_start": 0, "face_count": len(snapshot.state.surface.mesh.faces)}]
    result = [{"name": str(o.get("node", o.get("name", f"object_{i}"))),
               "face_start": o["face_start"], "face_count": o["face_count"]} for i, o in enumerate(records)]
    if len({o["name"] for o in result}) != len(result):
        raise ValueError("PROJECTION_AMBIGUOUS_HIGH_OBJECT_NAMES")
    return result


def _ray_picture(path: Path, arrays: Path, samples: Path) -> None:
    """X-ray orthographic overlay; full sample records remain authoritative."""
    import json
    with np.load(arrays, allow_pickle=False) as data:
        vertices, faces, cage = data["low_vertices"], data["low_faces"], data["cage_vertices"]
    basis = np.array([[.819, 0., -.574], [.243, .906, .346]])
    points = np.concatenate((vertices, cage)) @ basis.T
    low, high = points.min(axis=0), points.max(axis=0)
    scale = 460 / max(float((high - low).max()), 1e-10)
    def pixel(xyz: list[float] | np.ndarray) -> tuple[float, float]:
        value = (np.asarray(xyz) @ basis.T - (low + high) / 2) * scale
        return float(256 + value[0]), float(270 - value[1])
    picture = Image.new("RGB", (512, 512), "#f7f7f7")
    draw = ImageDraw.Draw(picture)
    for face in faces:
        coordinates = [pixel(vertices[v]) for v in face]
        draw.line([*coordinates, coordinates[0]], fill="#b9c0c7", width=1)
    displayed = 0
    if samples.is_file():
        with samples.open(encoding="utf-8") as stream:
            for line in stream:
                row = json.loads(line)
                if row["sample_id"] % 16:
                    continue
                end = np.asarray(row["origin"]) + np.asarray(row["direction"]) * row["range"]
                draw.line([pixel(row["origin"]), pixel(end)],
                          fill="#208550" if row["classification"] == "hit" else "#df3540", width=2)
                displayed += 1
                if displayed == 512:
                    break
    draw.text((12, 12), "Projection rays: X-ray diagnostic (not visibility/quality)", fill="black")
    draw.text((12, 30), f"{displayed} rays displayed; full records: samples.jsonl", fill="black")
    picture.save(path)


def projection_audit(low: Snapshot, request: dict[str, object], output: Path, deadline: float) -> dict[str, object]:
    if "high_snapshot" not in request:
        return {"status": "not_checked", "reason": "HIGH_SNAPSHOT_REQUIRED"}
    high = load_snapshot(Path(str(request["high_snapshot"])))
    if "high_state_hash" in request and high.state_hash != request["high_state_hash"]:
        raise ValueError("HIGH_STATE_HASH_MISMATCH")
    config = mapping(request.get("projection", {}), "projection")
    front_fraction = finite_number(config, "front_fraction", .02)
    back_fraction = finite_number(config, "back_fraction", .02)
    seconds = finite_number(config, "timeout_seconds", 240., .001)
    vertices, faces = low.state.surface.mesh.vertices, low.state.surface.mesh.faces
    diagonal = float(np.linalg.norm(np.ptp(vertices, axis=0)))
    front, back = front_fraction * diagonal, back_fraction * diagonal
    if front <= 0:
        raise ValueError("PROJECTION_POSITIVE_FRONT_FRACTION_REQUIRED")
    cage_hash: str | None = None
    if "cage_snapshot" in request:
        cage = load_snapshot(Path(str(request["cage_snapshot"])))
        if "cage_state_hash" in request and cage.state_hash != request["cage_state_hash"]:
            raise ValueError("CAGE_STATE_HASH_MISMATCH")
        if not np.array_equal(cage.state.surface.mesh.faces, faces) or len(
            cage.state.surface.mesh.vertices
        ) != len(vertices):
            raise ValueError("CAGE_INDEXED_TOPOLOGY_MISMATCH")
        if cage.metadata.get("cage_for_state_hash") != low.state_hash:
            raise ValueError("CAGE_REQUIRES_EXPLICIT_CAGE_FOR_STATE_HASH_IDENTITY")
        cage_vertices = cage.state.surface.mesh.vertices
        cage_hash = cage.state_hash
        mode = "explicit_indexed_cage"
    else:
        triangles = vertices[faces]
        weighted = np.cross(triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0])
        normals = np.zeros_like(vertices)
        for corner in range(3):
            np.add.at(normals, faces[:, corner], weighted)
        lengths = np.linalg.norm(normals, axis=1)
        if np.any(lengths[np.unique(faces)] <= diagonal * diagonal * 1e-14):
            raise ValueError("CAGE_ZERO_AVERAGED_GEOMETRIC_NORMAL")
        cage_vertices = vertices + front * normals / np.maximum(lengths[:, None], 1e-30)
        mode = "area_weighted_original_vertex_normals"
    objects = _objects(high)
    pairings = request.get("part_pairings", [])
    if not isinstance(pairings, list):
        raise TypeError("PART_PAIRINGS_LIST_REQUIRED")
    assigned: set[int] = set()
    for raw in pairings:
        pair = mapping(raw, "part_pairings")
        ids, names = pair.get("low_faces"), pair.get("high_objects")
        if (not isinstance(ids, list) or not ids or any(type(f) is not int or not 0 <= f < len(faces) for f in ids)
                or len(set(ids)) != len(ids) or assigned.intersection(ids)
                or not isinstance(names, list) or not names
                or any(name not in {o["name"] for o in objects} for name in names)):
            raise ValueError("INVALID_OR_OVERLAPPING_PART_PAIRING")
        assigned.update(ids)
    output.mkdir(parents=True, exist_ok=False)
    arrays = output / "geometry.npz"
    np.savez_compressed(arrays, low_vertices=vertices, low_faces=faces, cage_vertices=cage_vertices,
                        high_vertices=high.state.surface.mesh.vertices, high_faces=high.state.surface.mesh.faces)
    settings = {"arrays": str(arrays), "output": str(output), "back_distance": back,
                "diagonal": diagonal, "high_objects": objects, "part_pairings": pairings,
                "samples_per_face": 16, "seconds": max(.001, min(seconds, deadline - time.monotonic() - 12))}
    write_json(output / "request.json", settings)
    script = Path(__file__).resolve().parents[3] / "scripts/preflight_projection_worker.py"
    blender = Path(str(request.get("blender", "E:/Blender 4.5.11/blender.exe")))
    provenance = {"low_state_hash": low.state_hash, "high_state_hash": high.state_hash,
                  "cage_state_hash": cage_hash, "cage_mode": mode, "settings": settings,
                  "front_fraction": front_fraction, "front_distance": front,
                  "back_fraction": back_fraction, "back_distance": back,
                  "arrays_sha256": file_hash(arrays), "worker_sha256": file_hash(script),
                  "coordinates": "imported snapshot world coordinates; no normalization or transform reapplied",
                  "part_assignment": "explicit for listed faces; remainder global" if pairings else "NOT_CHECKED",
                  "samples": str(output / "samples.jsonl"), "ray_image": None}
    write_json(output / "provenance.json", provenance)
    if not blender.is_file():
        return {**provenance, "status": "not_checked", "reason": "BLENDER_MISSING", "incomplete": False}
    command = [str(blender), "--background", "--factory-startup", "--threads", "1", "--python-exit-code", "1",
               "--python", str(script), "--", str(output / "request.json")]
    write_json(output / "command.json", command)
    timed_out = False
    with (output / "process.log").open("w", encoding="utf-8") as log:
        try:
            result = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, check=False,
                timeout=max(.01, min(seconds + 4, deadline - time.monotonic() - 6)),
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
            exit_code = result.returncode
        except subprocess.TimeoutExpired:
            timed_out, exit_code = True, None
    result_path = output / "projection.json"
    report = read_object(result_path) if result_path.is_file() else {
        "status": "not_checked", "complete": False, "samples_completed": 0, "reason": "WORKER_NO_REPORT"}
    if timed_out or exit_code != 0:
        report.update(status="not_checked", complete=False,
                      reason="WORKER_TIMEOUT" if timed_out else "WORKER_FAILED")
        sample_file = output / "samples.jsonl"
        if sample_file.is_file():
            with sample_file.open(encoding="utf-8") as stream:
                report["samples_completed"] = sum(1 for line in stream if line.endswith("\n"))
    report.update(provenance)
    report["incomplete"] = not report.get("complete", False)
    if request.get("views", True):
        _ray_picture(output / "rays.png", arrays, output / "samples.jsonl")
        report["ray_image"] = str(output / "rays.png")
    for snapshot in (low, high):
        if load_snapshot(snapshot.root).state_hash != snapshot.state_hash:
            raise ValueError("PROJECTION_INPUT_CHANGED")
    if "cage_snapshot" in request and load_snapshot(Path(str(request["cage_snapshot"]))).state_hash != cage_hash:
        raise ValueError("PROJECTION_CAGE_CHANGED")
    write_json(result_path, report)
    return report
