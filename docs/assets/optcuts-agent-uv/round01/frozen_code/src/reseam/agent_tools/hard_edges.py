"""Read-only hard-edge provenance; imported normals and geometric proxies stay distinct."""

import shutil
import sqlite3
from pathlib import Path
from typing import cast

import numpy as np
from PIL import Image

from ..types import FloatArray, IntArray
from .seam_selection import _geometry
from .state import (
    Snapshot,
    check_deadline,
    file_hash,
    load_snapshot,
    read_object,
    require_snapshot,
    write_json,
)


def source_fields(
    snapshot: Snapshot, source: Snapshot | None = None, depth: int = 0,
) -> tuple[FloatArray | None, IntArray | None, IntArray, dict[str, object]]:
    """Use explicit face/corner lineage, never coordinate-nearest matching."""
    if depth > 8:
        raise ValueError("HARD_EDGE_LINEAGE_TOO_DEEP")
    mesh = snapshot.state.surface.mesh
    record = snapshot.metadata.get("hard_surface_source")
    if isinstance(record, dict):
        normals = np.asarray(record["corner_normals"], dtype=float)
        if normals.shape != (*mesh.faces.shape, 3) or not np.isfinite(normals).all():
            raise ValueError("SOURCE_CORNER_NORMALS_INVALID")
        lengths = np.linalg.norm(normals, axis=2)
        if np.any(lengths < 1e-12):
            raise ValueError("SOURCE_CORNER_NORMALS_ZERO")
        normals = normals / lengths[:, :, None]
        topology = cast(dict[str, object], snapshot.metadata.get("polygon_topology", {}))
        polygons = np.asarray(topology.get("triangle_to_polygon", []), dtype=np.int64)
        if polygons.shape != (len(mesh.faces),):
            raise ValueError("SOURCE_POLYGON_MAPPING_INVALID")
        custom = np.zeros(len(mesh.faces), dtype=np.int64)
        for obj in cast(list[dict[str, object]], record["objects"]):
            start = int(str(obj["face_start"]))
            custom[start:start + int(str(obj["face_count"]))] = int(bool(obj["has_custom_normals"]))
        return normals, polygons, np.zeros(len(mesh.faces), dtype=np.int64), {
            "source_snapshot": str(snapshot.root / "snapshot.json"), "source_state_hash": snapshot.state_hash,
            "custom_normal_faces": custom.tolist(), "imported_sharp_vertex_edges": record["sharp_vertex_edges"],
            "lineage": [], "normal_source": "Blender imported custom split normals where has_custom_normals=true"}
    if source is not None and np.array_equal(mesh.vertices, source.state.surface.mesh.vertices) and np.array_equal(
        mesh.faces, source.state.surface.mesh.faces
    ):
        return source_fields(source, None, depth + 1)
    lineage = snapshot.metadata.get("source_correspondence")
    parent_record = snapshot.metadata.get("derived_from")
    if isinstance(lineage, str) and isinstance(parent_record, dict):
        manifest = cast(list[dict[str, object]], snapshot.metadata.get("source_manifest", []))
        hashed = next((item for item in manifest if item["path"] == lineage), None)
        if hashed is None or file_hash(Path(lineage)) != hashed["sha256"]:
            raise ValueError("HARD_EDGE_LINEAGE_HASH_MISSING_OR_CHANGED")
        parent = load_snapshot(Path(str(parent_record["snapshot"])))
        if parent.state_hash != parent_record["state_hash"]:
            raise ValueError("HARD_EDGE_LINEAGE_PARENT_MISMATCH")
        normals, polygons, sides, evidence = source_fields(parent, source, depth + 1)
        with np.load(lineage, allow_pickle=False) as data:
            ids, bary, pairs = data["source_face_ids"], data["source_barycentric"], data["face_pairs"]
        if (ids.shape != (len(mesh.faces),) or bary.shape != (*mesh.faces.shape, 3)
                or ids.min() < 0 or ids.max() >= len(parent.state.surface.mesh.faces)
                or not np.isfinite(bary).all() or not np.allclose(bary.sum(axis=2), 1., atol=1e-8)):
            raise ValueError("HARD_EDGE_CORRESPONDENCE_INVALID")
        output_sides = sides[ids] * 2
        output_sides[pairs[:, 1]] += 1
        transferred = None if normals is None else np.einsum("fck,fkj->fcj", bary, normals[ids])
        if transferred is not None:
            transferred /= np.maximum(np.linalg.norm(transferred, axis=2)[:, :, None], 1e-30)
        custom = np.asarray(evidence.get("custom_normal_faces", [0] * len(parent.state.surface.mesh.faces)))
        inherited_sharp = {tuple(sorted(edge)) for edge in
                           cast(list[list[int]], evidence["imported_sharp_vertex_edges"])}
        transferred_sharp: set[tuple[int, int]] = set()
        parent_faces = parent.state.surface.mesh.faces
        for face, corners in enumerate(mesh.faces):
            for corner in range(3):
                nxt = (corner + 1) % 3
                for opposite in range(3):
                    parent_edge = tuple(sorted(int(v) for i, v in enumerate(parent_faces[ids[face]]) if i != opposite))
                    if (parent_edge in inherited_sharp
                            and max(abs(bary[face, corner, opposite]), abs(bary[face, nxt, opposite])) < 1e-10):
                        a, b = int(corners[corner]), int(corners[nxt])
                        transferred_sharp.add((min(a, b), max(a, b)))
        return transferred, None if polygons is None else polygons[ids], output_sides, {
            **evidence, "custom_normal_faces": custom[ids].tolist(),
            "imported_sharp_vertex_edges": [list(edge) for edge in sorted(transferred_sharp)],
            "sharp_flag_transfer": "explicit source-edge barycentric subsegments; no coordinate matching",
            "lineage": [*cast(list[object], evidence.get("lineage", [])), {
                "path": lineage, "sha256": file_hash(Path(lineage)), "parent_hash": parent.state_hash}],
            "normal_comparison": "source-space angles within each mirror side; generated joins use geometry"}
    if source is not None:
        raise ValueError("HARD_EDGE_SOURCE_GEOMETRY_MISMATCH")
    topology = snapshot.metadata.get("polygon_topology")
    polygons = None
    if isinstance(topology, dict):
        values = np.asarray(topology.get("triangle_to_polygon", []), dtype=np.int64)
        if values.shape == (len(mesh.faces),):
            polygons = values
    return None, polygons, np.zeros(len(mesh.faces), dtype=np.int64), {
        "normal_source": "UNAVAILABLE: geometric dihedral proxy only", "lineage": [],
        "custom_normal_faces": [0] * len(mesh.faces), "imported_sharp_vertex_edges": []}


def classify_edges(
    snapshot: Snapshot, angle_degrees: float, normal_epsilon_degrees: float, source: Snapshot | None = None,
) -> dict[str, object]:
    if not 0 < angle_degrees <= 180 or not 0 < normal_epsilon_degrees <= 180:
        raise ValueError("HARD_EDGE_ANGLE_MUST_BE_IN_0_180")
    mesh = snapshot.state.surface.mesh
    _, angles, _ = _geometry(mesh)
    normals, polygons, sides, evidence = source_fields(snapshot, source)
    custom = cast(list[int], evidence["custom_normal_faces"])
    sharp = {tuple(sorted(edge)) for edge in cast(list[list[int]], evidence["imported_sharp_vertex_edges"])}
    rows: list[dict[str, object]] = []
    selected: list[int] = []
    for edge_id, entries in enumerate(mesh.edge_faces):
        geometric = float(np.degrees(angles[edge_id]))
        diagonal = False
        normal_angle: float | None = None
        reasons: list[str] = []
        if len(entries) == 2:
            a, b = entries[0][0], entries[1][0]
            same_side = sides[a] == sides[b]
            diagonal = polygons is not None and polygons[a] == polygons[b] and same_side
            if not diagonal:
                if normals is not None and same_side and custom[a] and custom[b]:
                    values = []
                    for vertex in mesh.edges[edge_id]:
                        ca = int(np.flatnonzero(mesh.faces[a] == vertex)[0])
                        cb = int(np.flatnonzero(mesh.faces[b] == vertex)[0])
                        values.append(float(np.degrees(np.arccos(np.clip(normals[a, ca] @ normals[b, cb], -1, 1)))))
                    normal_angle = max(values)
                    if max(values) >= normal_epsilon_degrees:
                        reasons.append("imported_custom_normal_discontinuity")
                if mesh.edges[edge_id] in sharp:
                    reasons.append("imported_sharp_flag")
                if geometric >= angle_degrees:
                    reasons.append("geometric_dihedral_proxy" if same_side else "generated_mirror_join_dihedral")
        if reasons:
            selected.append(edge_id)
        rows.append({"edge_id": edge_id, "vertices": list(mesh.edges[edge_id]),
            "faces": [f for f, _ in entries], "geometric_angle_degrees": geometric,
            "source_normal_angle_degrees": normal_angle, "triangulation_diagonal": bool(diagonal),
            "boundary": len(entries) == 1, "reasons": reasons, "suggested_cut": bool(reasons)})
    return {"state_hash": snapshot.state_hash, "asset_id": snapshot.asset_id,
        "angle_degrees": angle_degrees, "normal_epsilon_degrees": normal_epsilon_degrees,
        "source_evidence": evidence, "edges": rows, "edge_ids": selected,
        "vertex_edges": [list(mesh.edges[e]) for e in selected],
        "excluded_triangulation_diagonals": [row["edge_id"] for row in rows if row["triangulation_diagonal"]],
        "policy": "candidate cuts only; Agent may retain connected sides after stretch and appearance checks",
        "snapshot_modified": False, "source_normals_exported": False}


def inspect_hard_edges(request: dict[str, object], output: Path, deadline: float) -> dict[str, object]:
    snapshot = require_snapshot(request)
    raw_source = request.get("source_snapshot")
    source = load_snapshot(Path(raw_source)) if isinstance(raw_source, str) else None
    report = classify_edges(snapshot, float(str(request.get("angle_degrees", 45))),
                            float(str(request.get("normal_epsilon_degrees", .1))), source)
    add_surface_cues(snapshot, report, request, output, deadline)
    maximum = request.get("patch_polygons", 0)
    if type(maximum) is not int or maximum < 0 or maximum > 64:
        raise ValueError("PATCH_POLYGONS_INTEGER_0_64_REQUIRED")
    if maximum:
        report["patch_plan"] = patch_plan(snapshot, report, maximum, source)
    check_deadline(deadline)
    write_json(output / "hard_edges.json", report)
    return {"status": "OK", "execution_success": True, "report": str(output / "hard_edges.json"),
            "edge_ids": report["edge_ids"], "snapshot_modified": False}


def patch_plan(
    snapshot: Snapshot, report: dict[str, object], maximum: int, source: Snapshot | None = None,
) -> dict[str, object]:
    """Bounded normal-cone polygon fans with a dual-tree opening, no UV edits."""
    mesh = snapshot.state.surface.mesh
    normals, _, _ = _geometry(mesh)
    _, polygons, sides, _ = source_fields(snapshot, source)
    if polygons is None:
        raise ValueError("POLYGON_PROVENANCE_REQUIRED_FOR_PATCH_PLAN")
    keys = [(int(side), int(polygon)) for side, polygon in zip(sides, polygons, strict=True)]
    members: dict[tuple[int, int], list[int]] = {}
    for face, key in enumerate(keys):
        members.setdefault(key, []).append(face)
    blocked = set(cast(list[int], report["edge_ids"]))
    adjacency: dict[tuple[int, int], list[tuple[int, tuple[int, int]]]] = {key: [] for key in members}
    for edge, entries in enumerate(mesh.edge_faces):
        if len(entries) == 2 and edge not in blocked:
            a, b = keys[entries[0][0]], keys[entries[1][0]]
            if a != b:
                adjacency[a].append((edge, b))
                adjacency[b].append((edge, a))
    remaining = set(members)
    keep: set[int] = set()
    patches: list[list[int]] = []
    while remaining:
        seed = min(remaining)
        remaining.remove(seed)
        chosen = [seed]
        pending = [seed]
        seed_normal = (normals[members[seed]] * mesh.areas[members[seed], None]).sum(axis=0)
        seed_normal /= max(float(np.linalg.norm(seed_normal)), 1e-30)
        while pending:
            edge_rows = cast(list[dict[str, object]], report["edges"])
            for edge, neighbor in sorted(adjacency[pending.pop()],
                                         key=lambda item: (float(str(edge_rows[item[0]].get("cue_priority", 0))), item[0])):
                if neighbor not in remaining or len(chosen) >= maximum:
                    continue
                # All triangles of an original polygon participate in the bound.
                # No internal polygon diagonal is introduced to satisfy it.
                if np.min(normals[members[neighbor]] @ seed_normal) < np.cos(np.radians(45.)):
                    continue
                remaining.remove(neighbor)
                chosen.append(neighbor)
                pending.append(neighbor)
                keep.add(edge)
        patches.append(sorted(face for key in chosen for face in members[key]))
    cuts = [edge for edge, entries in enumerate(mesh.edge_faces) if len(entries) == 2
            and keys[entries[0][0]] != keys[entries[1][0]] and edge not in keep]
    return {"face_patches": patches, "edge_ids": cuts, "vertex_edges": [list(mesh.edges[e]) for e in cuts],
        "maximum_source_polygons": maximum, "normal_cone_degrees": 45,
        "method": "source-polygon bounded normal-cone fans; dual-tree openings avoid closed wraps",
        "diagonals_cut": False, "geometry_modified": False, "heuristic_not_optimality_claim": True}


def add_surface_cues(
    snapshot: Snapshot, report: dict[str, object], request: dict[str, object], output: Path, deadline: float,
) -> None:
    """Supplement seam suggestions with separately sourced AO/height/normal cues.

    These are inspection cues, not discontinuity proofs or PBR acceptance.
    Explicit map bindings use frozen source BaseColor UV coordinates; arbitrary
    Unity UV transforms and packed-channel conventions are never guessed.
    """
    rows = cast(list[dict[str, object]], report["edges"])
    channels: dict[str, object] = {"geometry_ao": "not_requested", "ao": "not_bound",
                                  "normal": "not_bound", "height": "not_bound"}
    raw_index = request.get("mesh_index")
    if isinstance(raw_index, str):
        from .state import object_hash

        index = read_object(Path(raw_index))
        digest = index.pop("index_hash", None)
        if digest != object_hash(index) or index["state_hash"] != snapshot.state_hash:
            raise ValueError("SURFACE_CUE_INDEX_STATE_OR_HASH_MISMATCH")
        if file_hash(Path(str(index["database"]))) != index["database_sha256"]:
            raise ValueError("SURFACE_CUE_DATABASE_CHANGED")
        connection = sqlite3.connect(Path(str(index["database"])).resolve().as_uri() + "?mode=ro", uri=True)
        try:
            ao_values = connection.execute("SELECT id, ao FROM edge ORDER BY id").fetchall()
        finally:
            connection.close()
        if len(ao_values) != len(rows):
            raise ValueError("SURFACE_CUE_EDGE_COUNT_MISMATCH")
        for edge, value in ao_values:
            rows[edge]["geometry_ao_occlusion"] = value
            rows[edge]["cue_priority"] = 0. if value is None else float(value)
        channels["geometry_ao"] = {"index": raw_index, "index_hash": digest,
            "rays": index["ao_rays"], "definition": cast(dict[str, object], index["definitions"])["ao"],
            "role": "occluded crease placement cue; not an authored texture or mandatory cut"}
    maps = request.get("maps", {})
    if not isinstance(maps, dict) or set(maps) - {"ao", "normal", "height"}:
        raise ValueError("SURFACE_CUE_MAPS_REQUIRE_EXPLICIT_AO_NORMAL_HEIGHT_BINDINGS")
    if maps:
        from .views import _texture

        _, source_uv, _, repeat, _ = _texture({}, snapshot)
        mesh = snapshot.state.surface.mesh
        for role, raw in maps.items():
            if not isinstance(raw, dict) or not isinstance(raw.get("path"), str):
                raise TypeError("SURFACE_CUE_MAP_PATH_REQUIRED")
            path = Path(str(raw["path"]))
            if not path.is_absolute():
                raise ValueError("SURFACE_CUE_MAP_ABSOLUTE_PATH_REQUIRED")
            digest = file_hash(path)
            frozen = output / "cue_inputs" / (str(role) + path.suffix)
            frozen.parent.mkdir(exist_ok=True)
            shutil.copy2(path, frozen)
            if file_hash(frozen) != digest or file_hash(path) != digest:
                raise ValueError("SURFACE_CUE_MAP_CHANGED")
            with Image.open(frozen) as image:
                image_mode = image.mode
                if role != "normal" and (image.mode.startswith("I;16") or image.mode in ("I", "F")):
                    # Preserve 16-bit/scientific scalar maps instead of clipping
                    # them through an 8-bit RGB conversion.
                    pixels = np.asarray(image, dtype=float)
                    pixels = pixels[:, :, None]
                    scalar_scale = 1. if image.mode == "F" else 65535.
                    pixels = pixels / scalar_scale
                else:
                    pixels = np.asarray(image.convert("RGB"), dtype=float) / 255.
            channel = raw.get("channel", 0)
            if type(channel) is not int or not 0 <= channel <= 2:
                raise ValueError("SURFACE_CUE_MAP_CHANNEL_0_2_REQUIRED")
            if channel >= pixels.shape[2] or not np.isfinite(pixels).all():
                raise ValueError("SURFACE_CUE_MAP_CHANNEL_OR_VALUES_INVALID")
            convention = raw.get("convention", "opengl")
            if role == "normal" and convention not in ("opengl", "directx"):
                raise ValueError("SURFACE_CUE_NORMAL_CONVENTION_REQUIRED")
            for row in rows:
                faces = cast(list[int], row["faces"])
                if len(faces) != 2 or row["triangulation_diagonal"]:
                    continue
                sampled = []
                for face in faces:
                    bary = np.full(3, .02)
                    for vertex in cast(list[int], row["vertices"]):
                        bary[int(np.flatnonzero(mesh.faces[face] == vertex)[0])] = .49
                    uv = bary @ source_uv[face]
                    uv = uv % 1 if repeat else np.clip(uv, 0., 1.)
                    x = min(int(uv[0] * pixels.shape[1]), pixels.shape[1] - 1)
                    y = min(int((1. - uv[1]) * pixels.shape[0]), pixels.shape[0] - 1)
                    color = pixels[y, x]
                    if role == "normal":
                        tangent_normal = color * 2 - 1
                        if convention == "directx":
                            tangent_normal[1] *= -1
                        tri = mesh.vertices[mesh.faces[face]]
                        duv = source_uv[face, 1:] - source_uv[face, :1]
                        if abs(float(np.linalg.det(duv))) < 1e-14:
                            sampled = []
                            break
                        frame = np.linalg.solve(duv, tri[1:] - tri[:1])
                        n = np.cross(tri[1] - tri[0], tri[2] - tri[0])
                        n /= max(float(np.linalg.norm(n)), 1e-30)
                        t = frame[0] - n * (frame[0] @ n)
                        t /= max(float(np.linalg.norm(t)), 1e-30)
                        b = np.cross(n, t) * np.sign(np.linalg.det(duv))
                        world = tangent_normal @ np.stack((t, b, n))
                        sampled.append(world / max(float(np.linalg.norm(world)), 1e-30))
                    else:
                        sampled.append(color[channel])
                if len(sampled) != 2:
                    continue
                if role == "normal":
                    delta = float(np.degrees(np.arccos(np.clip(np.asarray(sampled[0]) @ sampled[1], -1, 1))))
                    row["normal_map_world_angle_degrees"] = delta
                    priority = delta / 180
                else:
                    delta = abs(float(sampled[0]) - float(sampled[1]))
                    row[str(role) + "_map_difference"] = delta
                    priority = delta
                    if role == "ao":
                        row["ao_map_darkness"] = 1. - (float(sampled[0]) + float(sampled[1])) / 2
                        priority = max(priority, float(str(row["ao_map_darkness"])))
                row["cue_priority"] = max(float(str(row.get("cue_priority", 0))), priority)
            channels[str(role)] = {"path": str(frozen.resolve()), "sha256": digest,
                "image_mode": image_mode,
                "declared_binding": raw, "uv": "frozen source UV, no implicit shader transform",
                "sampling": "two edge-midpoint samples, inset 2%; linear data; height normalized to stored 0..1",
                "scope": "candidate cue only; tangent-normal frame approximation is not Mikk/PBR acceptance"}
            check_deadline(deadline)
    report["surface_cue_sources"] = channels
