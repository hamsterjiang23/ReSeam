"""Explicit part assembly with source ancestry and independently chosen UV reuse."""

import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import cast

import numpy as np

from ..budget_state import BudgetState
from ..joint_types import ReuseGroup
from ..mesh import DisjointSet, chart_labels, make_mesh, state_from_corner_uv
from ..types import FloatArray, IntArray, MeshState
from .assets import import_asset
from .mirror_asset import _reference_glb
from .part_labels import transfer_labels
from .parts_material import ColorTile, atlas_tiles, source_tiles
from .regions import face_selection
from .state import (
    Snapshot,
    check_deadline,
    file_hash,
    load_snapshot,
    object_hash,
    save_snapshot,
    snapshot_result,
    write_json,
)
from .symmetry_variant import build_symmetry_variant


@dataclass
class Part:
    record: dict[str, object]
    surface: MeshState
    source_faces: IntArray
    barycentric: FloatArray
    reflected: np.ndarray[tuple[int, ...], np.dtype[np.bool_]]
    source_index: int
    source_uv: FloatArray
    pairs: IntArray


def _objects(value: object, name: str) -> list[dict[str, object]]:
    if not isinstance(value, list) or not value or any(not isinstance(row, dict) for row in value):
        raise ValueError(f"PARTS_{name}_NONEMPTY_OBJECT_LIST_REQUIRED")
    return cast(list[dict[str, object]], value)


def _id(value: object) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 120:
        raise ValueError("PARTS_NONEMPTY_ID_REQUIRED")
    return value


def _protected(source: Snapshot) -> set[int]:
    protected: set[int] = set()
    for region in cast(list[dict[str, object]], source.metadata.get("regions", [])):
        protected.update(cast(list[int], region.get("protected_faces", [])))
        if region.get("role") == "protected":
            protected.update(cast(list[int], region["faces"]))
    for lock in cast(list[dict[str, object]], source.metadata.get("artist_locks", [])):
        if set(cast(list[str], lock["kinds"])) - {"color"}:
            raise ValueError("PARTS_SOURCE_POSITION_OR_SHAPE_LOCK_REQUIRES_EXPLICIT_UV_WORKFLOW")
        protected.update(cast(list[int], lock["faces"]))
    return protected


def _transform(value: object) -> FloatArray:
    matrix = np.asarray(value, dtype=float)
    if (matrix.shape != (4, 4) or not np.isfinite(matrix).all()
            or not np.array_equal(matrix[3], [0, 0, 0, 1])
            or abs(float(np.linalg.det(matrix[:3, :3]))) < 1e-12):
        raise ValueError("PARTS_INVERTIBLE_AFFINE_TRANSFORM_REQUIRED")
    return matrix


def _extract(source: Snapshot, faces: IntArray, transform: FloatArray) -> MeshState:
    parent = source.state.surface
    vertices, inverse = np.unique(parent.mesh.faces[faces], return_inverse=True)
    xyz = parent.mesh.vertices[vertices] @ transform[:3, :3].T + transform[:3, 3]
    tri = inverse.reshape(-1, 3)
    corners = parent.uv[parent.uv_faces[faces]]
    ids = parent.uv_faces[faces]
    if np.linalg.det(transform[:3, :3]) < 0:
        tri, corners, ids = tri[:, [0, 2, 1]], corners[:, [0, 2, 1]], ids[:, [0, 2, 1]]
    # BaseColor scope uses source corner ancestry; no shading-normal claim.
    return state_from_corner_uv(make_mesh(xyz, tri), corners, corner_ids=ids)


def _plane(record: dict[str, object]) -> tuple[int, float]:
    axis, offset = record.get("axis", "x"), record.get("offset", 0.)
    if axis not in ("x", "y", "z") or not isinstance(offset, (int, float)) or isinstance(offset, bool) or not np.isfinite(offset):
        raise ValueError("PARTS_FINITE_AXIS_PLANE_REQUIRED")
    return ("x", "y", "z").index(str(axis)), float(offset)


def _mirror(part: Part, strategy: dict[str, object], master: Part | None, deadline: float) -> Part:
    axis, offset = _plane(strategy)
    if master is None:
        positive = strategy.get("keep_positive", True)
        if type(positive) is not bool:
            raise ValueError("PARTS_BOOLEAN_KEEP_POSITIVE_REQUIRED")
        variant = build_symmetry_variant(part.surface, plane_axis=axis, plane_offset=offset,
                                         keep_positive=positive, deadline=deadline)
        part.record["mirror_report"] = variant.report
        return Part(part.record, variant.surface, part.source_faces[variant.source_face_ids],
                    variant.source_barycentric @ part.barycentric[variant.source_face_ids],
                    variant.reflected_faces, part.source_index,
                    variant.source_barycentric @ part.source_uv[variant.source_face_ids], variant.face_pairs)
    if master.record["geometry"] != {"mode": "keep"}:
        raise ValueError("PARTS_MIRROR_MASTER_MUST_KEEP_GEOMETRY")
    parent = master.surface
    xyz = parent.mesh.vertices.copy()
    xyz[:, axis] = 2 * offset - xyz[:, axis]
    surface = state_from_corner_uv(make_mesh(xyz, parent.mesh.faces[:, [0, 2, 1]]),
                                  parent.uv[parent.uv_faces][:, [0, 2, 1]],
                                  corner_ids=parent.uv_faces[:, [0, 2, 1]])
    part.record["replaced_input_faces"] = part.source_faces.tolist()
    return Part(part.record, surface, master.source_faces.copy(), master.barycentric[:, [0, 2, 1]],
                np.ones(len(parent.mesh.faces), dtype=np.bool_), master.source_index,
                master.source_uv[:, [0, 2, 1]], np.empty((0, 2), dtype=np.int64))


def _groups(surface: MeshState, pairs: IntArray, name: str, axis: int, offset: float) -> list[ReuseGroup]:
    labels = chart_labels(surface)
    uv = surface.uv[surface.uv_faces]
    edge = uv[:, 1:] - uv[:, :1]
    signs = np.sign(edge[:, 0, 0]*edge[:, 1, 1] - edge[:, 0, 1]*edge[:, 1, 0]).astype(int)
    normal = tuple(1. if index == axis else 0. for index in range(3))
    result: list[ReuseGroup] = []
    for label in np.unique(labels[pairs[:, 0]]):
        selected = pairs[labels[pairs[:, 0]] == label]
        first, second = selected[:, 0], selected[:, 1]
        unique = np.unique(signs[first])
        if len(unique) != 1 or unique[0] == 0 or not np.all(signs[second] == -unique[0]):
            raise ValueError("PARTS_SHARED_CHART_ORIENTATION_INVALID")
        result.append(ReuseGroup(f"part:{name}:{int(label)}", tuple(map(int, first)), tuple(map(int, second)),
                                 cast(tuple[float, float, float], normal), offset, int(unique[0]), -int(unique[0])))
    return result


def prepare_parts(request: dict[str, object], output: Path, deadline: float) -> dict[str, object]:
    """Freeze sources, extract disjoint selections and assemble a new asset."""
    check_deadline(deadline)
    entries = _objects(request.get("sources"), "SOURCES")
    scope = request.get("material_scope", "basecolor")
    if scope not in ("basecolor", "geometry"):
        raise ValueError("PARTS_SCOPE_BASECOLOR_OR_GEOMETRY_REQUIRED")
    if request.get("placement") != "source_world":
        raise ValueError("PARTS_EXPLICIT_SOURCE_WORLD_PLACEMENT_REQUIRED")
    sources: list[Snapshot] = []
    matrices: list[FloatArray] = []
    source_ids: list[str] = []
    tiles: list[ColorTile] = []
    tile_counts: list[int] = []
    source_corners: list[FloatArray] = []
    inventory: list[dict[str, object]] = []
    source_manifest: list[dict[str, object]] = []
    # Import through the existing tool rather than duplicating FBX/UV handling.
    for index, entry in enumerate(entries):
        check_deadline(deadline)
        identity = _id(entry.get("id"))
        if identity in source_ids:
            raise ValueError("PARTS_DUPLICATE_SOURCE_ID")
        source_ids.append(identity)
        matrix = _transform(entry.get("transform", np.eye(4)))
        matrices.append(matrix)
        folder = output / "sources" / str(index)
        folder.mkdir(parents=True)
        args = {key: value for key, value in entry.items() if key not in {"id", "transform"}}
        if "blender" in request:
            args.setdefault("blender", request["blender"])
        if scope == "geometry":
            args.setdefault("initialize_uv", "xatlas")
        if "snapshot" in args:
            if "path" in args:
                raise ValueError("PARTS_SOURCE_PATH_OR_SNAPSHOT")
            source = load_snapshot(Path(str(args["snapshot"])))
            if args.get("state_hash", source.state_hash) != source.state_hash:
                raise ValueError("PARTS_STALE_SOURCE_SNAPSHOT")
        else:
            receipt = import_asset(args, folder, deadline)
            source = load_snapshot(Path(str(receipt["snapshot"])))
        sources.append(source)
        _protected(source)  # do not silently drop locks while changing index namespaces
        source_manifest.extend(cast(list[dict[str, object]], source.metadata["source_manifest"]))
        source_manifest.extend({"path": str(path.resolve()), "sha256": file_hash(path)}
                               for path in (source.root / "snapshot.json", source.root / "arrays.npz"))
        if scope == "basecolor":
            new_tiles, _ = source_tiles(source, deadline)
            tiles.extend(new_tiles)
            tile_counts.append(len(new_tiles))
        source_corners.append(source.state.surface.uv[source.state.surface.uv_faces].copy())
        inventory.append({"id": identity, **snapshot_result(source), "objects": source.metadata.get("objects"),
                          "transform": matrix.tolist(), "source_uv_present": source.metadata.get("source_uv_present"),
                          "face_count": len(source.state.surface.mesh.faces)})
    atlas = None
    tile_records: list[dict[str, object]] = []
    if scope == "basecolor":
        atlas, mapped, tile_records = atlas_tiles(tiles, deadline)
        cursor = 0
        for index, count in enumerate(tile_counts):
            source_corners[index] = np.concatenate(mapped[cursor:cursor+count])
            cursor += count
    raw_parts = request.get("parts")
    segmentation_report: dict[str, object] | None = None
    if "segmentation_reference" in request:
        if len(sources) != 1 or raw_parts is not None:
            raise ValueError("SEGMENT_LABELS_REQUIRE_ONE_SOURCE_AND_NO_EXPLICIT_PARTS")
        source = sources[0]
        matrix = matrices[0]
        mesh = source.state.surface.mesh
        source_triangles = (mesh.vertices @ matrix[:3, :3].T + matrix[:3, 3])[mesh.faces]
        segmentation = output / "segmentation.glb"
        shutil.copy2(Path(str(request["segmentation_reference"])), segmentation)
        raw_parts, segmentation_report = transfer_labels(source_triangles, segmentation, output, deadline)
        for row in raw_parts:
            row["source"] = source_ids[0]
        source_manifest.extend({"path": str(path.resolve()), "sha256": file_hash(path)} for path in
                               (segmentation, output / "segmentation_transfer.json", output / "segmentation_witnesses.npz"))
    if raw_parts is None:
        raw_parts = []
        for source_id, source in zip(source_ids, sources, strict=True):
            if "parts" in source.metadata:
                raw_parts.extend({"id": f"{source_id}:{part['id']}", "source": source_id,
                                  "faces": part["faces"], "name": part["name"]}
                                 for part in cast(list[dict[str, object]], source.metadata["parts"]))
            elif "objects" in source.metadata:
                raw_parts.extend({"id": f"{source_id}:{obj['node']}", "source": source_id, "objects": [obj["node"]]}
                                 for obj in cast(list[dict[str, object]], source.metadata["objects"]))
            else:
                raise ValueError("PARTS_EXPLICIT_SELECTION_REQUIRED")
    settings = _objects(raw_parts, "PARTS")
    parts: dict[str, Part] = {}
    used = [set[int]() for _ in sources]
    for setting in settings:
        check_deadline(deadline)
        identity = _id(setting.get("id"))
        if identity in parts or setting.get("source") not in source_ids:
            raise ValueError("PARTS_DUPLICATE_PART_OR_UNKNOWN_SOURCE")
        index = source_ids.index(str(setting["source"]))
        source = sources[index]
        count = len(source.state.surface.mesh.faces)
        if ("faces" in setting) == ("objects" in setting):
            raise ValueError("PARTS_SELECT_FACES_OR_OBJECTS")
        if "faces" in setting:
            selected = face_selection(setting["faces"], count)
        else:
            names = setting["objects"]
            objects = cast(list[dict[str, object]], source.metadata["objects"])
            if (not isinstance(names, list) or not names or any(not isinstance(n, str) for n in names)
                    or len(set(names)) != len(names) or set(names) - {obj["node"] for obj in objects}):
                raise ValueError("PARTS_UNKNOWN_OR_DUPLICATE_OBJECT")
            selected = [face for obj in objects if obj["node"] in names
                        for face in range(int(str(obj["face_start"])), int(str(obj["face_start"]))+int(str(obj["face_count"])))]
        if used[index].intersection(selected):
            raise ValueError("PARTS_OVERLAPPING_SOURCE_SELECTIONS")
        used[index].update(selected)
        strategy = setting.get("geometry", {"mode": "keep"})
        if not isinstance(strategy, dict) or strategy.get("mode") not in ("keep", "mirror_half", "mirror_from"):
            raise ValueError("PARTS_GEOMETRY_MODE_REQUIRED")
        if strategy["mode"] != "keep" and request.get("allow_geometry_change") is not True:
            raise ValueError("PARTS_MIRROR_REQUIRES_EXPLICIT_GEOMETRY_CHANGE")
        protected = face_selection(setting.get("protected_faces", []), count, allow_empty=True)
        caps = face_selection(setting.get("cap_faces", []), count, allow_empty=True)
        if not set(protected+caps) <= set(selected):
            raise ValueError("PARTS_TAGGED_FACES_OUTSIDE_SELECTION")
        protected = sorted(set(protected) | (_protected(source) & set(selected)))
        if protected and strategy["mode"] != "keep":
            raise ValueError("PARTS_MIRROR_CONFLICTS_WITH_PROTECTED_FACES")
        policy = setting.get("uv_policy", "exclusive")
        if policy not in ("exclusive", "candidate", "shared"):
            raise ValueError("PARTS_UV_POLICY_INVALID")
        if policy == "shared" and strategy["mode"] == "keep":
            raise ValueError("PARTS_KEEP_USE_REUSE_CHECK_PAIR_FOR_SHARED_PERMISSION")
        faces = np.asarray(selected, dtype=np.int64)
        transform = matrices[index]
        surface = _extract(source, faces, transform)
        bary = np.broadcast_to(np.eye(3), (len(faces), 3, 3)).copy()
        corners = source_corners[index][faces].copy()
        if np.linalg.det(transform[:3, :3]) < 0:
            bary, corners = bary[:, [0, 2, 1]], corners[:, [0, 2, 1]]
        parts[identity] = Part({"id": identity, "name": setting.get("name", identity), "geometry": strategy,
                                "uv_policy": policy, "source": setting["source"], "input_faces": selected,
                                "protected_source_faces": protected, "cap_source_faces": caps,
                                "rationale": setting.get("rationale", ""), "share_with": setting.get("share_with")},
                               surface, faces, bary, np.zeros(len(faces), dtype=np.bool_), index,
                               corners, np.empty((0, 2), dtype=np.int64))
    omitted = {identity: sorted(set(range(len(source.state.surface.mesh.faces))) - selection)
               for identity, source, selection in zip(source_ids, sources, used, strict=True)}
    if any(omitted.values()) and request.get("allow_partial") is not True:
        raise ValueError("PARTS_UNASSIGNED_FACES_REQUIRE_ALLOW_PARTIAL")
    # Resolve masters from the original selections; chains would obscure lineage.
    originals = dict(parts)
    for identity, part in originals.items():
        strategy = cast(dict[str, object], part.record["geometry"])
        if strategy["mode"] == "mirror_half":
            parts[identity] = _mirror(part, strategy, None, deadline)
        elif strategy["mode"] == "mirror_from":
            master_id = strategy.get("master_part")
            if master_id == identity or master_id not in originals:
                raise ValueError("PARTS_DISTINCT_EXISTING_MIRROR_MASTER_REQUIRED")
            parts[identity] = _mirror(part, strategy, originals[str(master_id)], deadline)
    vertices: list[FloatArray] = []
    triangles: list[IntArray] = []
    uvs: list[FloatArray] = []
    uv_ids: list[IntArray] = []
    ref_uv: list[FloatArray] = []
    lineage_faces: list[IntArray] = []
    barycentrics: list[FloatArray] = []
    reflected: list[np.ndarray] = []
    source_indices: list[IntArray] = []
    rows: list[dict[str, object]] = []
    face_offset = vertex_offset = uv_offset = 0
    for part in parts.values():
        check_deadline(deadline)
        surface = part.surface
        count = len(surface.mesh.faces)
        components = DisjointSet(count)
        for adjacent in surface.mesh.edge_faces:
            if len(adjacent) == 2:
                components.union(adjacent[0][0], adjacent[1][0])
        part.record.update(face_start=face_offset, face_count=count, faces=list(range(face_offset, face_offset+count)),
                           connected_components=len({components.find(face) for face in range(count)}),
                           source_index=part.source_index, geometry_hash=object_hash({"vertices": surface.mesh.vertices.tolist(),
                                                                                   "faces": surface.mesh.faces.tolist()}))
        vertices.append(surface.mesh.vertices)
        triangles.append(surface.mesh.faces + vertex_offset)
        uvs.append(surface.uv)
        uv_ids.append(surface.uv_faces + uv_offset)
        ref_uv.append(part.source_uv)
        lineage_faces.append(part.source_faces)
        barycentrics.append(part.barycentric)
        reflected.append(part.reflected)
        source_indices.append(np.full(count, part.source_index, dtype=np.int64))
        rows.append(part.record)
        face_offset += count
        vertex_offset += len(surface.mesh.vertices)
        uv_offset += len(surface.uv)
    xyz = np.concatenate(vertices)
    quantized = xyz.astype(np.float32).astype(float)
    surface = state_from_corner_uv(make_mesh(quantized, np.concatenate(triangles)),
                                  np.concatenate(uvs)[np.concatenate(uv_ids)], corner_ids=np.concatenate(uv_ids))
    groups: list[ReuseGroup] = []
    occupied: set[int] = set()
    for identity, part in parts.items():
        if part.record["uv_policy"] != "shared":
            continue
        strategy = cast(dict[str, object], part.record["geometry"])
        axis, offset = _plane(strategy)
        start = int(str(part.record["face_start"]))
        if strategy["mode"] == "mirror_half":
            pairs = part.pairs + start
        else:
            master = parts[str(strategy["master_part"])]
            pairs = np.column_stack((np.arange(len(part.surface.mesh.faces))+int(str(master.record["face_start"])),
                                     np.arange(len(part.surface.mesh.faces))+start))
        selected_faces = set(map(int, pairs.ravel()))
        if occupied & selected_faces:
            raise ValueError("PARTS_OVERLAPPING_SHARED_GROUPS_USE_REUSE_TOOLS")
        occupied.update(selected_faces)
        groups.extend(_groups(surface, pairs, identity, axis, offset))
    correspondence = output / "part_lineage.npz"
    np.savez_compressed(correspondence, source_index=np.concatenate(source_indices),
                        source_face=np.concatenate(lineage_faces), source_corner_barycentric=np.concatenate(barycentrics),
                        reflected=np.concatenate(reflected))
    source_manifest.append({"path": str(correspondence.resolve()), "sha256": file_hash(correspondence)})
    metadata: dict[str, object] = {"parts": rows, "part_sources": inventory, "source_manifest": source_manifest,
                                  "material_scope": scope, "basecolor_only": True, "requires_rebake": True,
                                  "acceptance_policy": "visual", "part_lineage": str(correspondence.resolve()),
                                  "regions": [{"name": row["name"], "faces": row["faces"], "region_id": "part:"+str(row["id"]),
                                               "protected_faces": [], "rationale": row["rationale"]} for row in rows]}
    for region, part in zip(cast(list[dict[str, object]], metadata["regions"]), parts.values(), strict=True):
        region["protected_faces"] = [int(str(part.record["face_start"]))+i for i, f in enumerate(part.source_faces)
                                     if int(f) in cast(list[int], part.record["protected_source_faces"])]
    if atlas is not None:
        reference = output / "assembled_source.glb"
        _reference_glb(surface, np.concatenate(ref_uv), atlas, np.ones(4), False, reference)
        metadata["reference_glb"] = str(reference.resolve())
        source_manifest.append({"path": str(reference.resolve()), "sha256": file_hash(reference)})
    projected_reference = request.get("projected_reference")
    if projected_reference is not None:
        if scope != "geometry" or not isinstance(projected_reference, str):
            raise ValueError("PARTS_PROJECTED_REFERENCE_REQUIRES_GEOMETRY_SCOPE_AND_GLB_PATH")
        from ..budget_texture import load_reference

        reference = output / "projected_reference.glb"
        shutil.copy2(Path(projected_reference), reference)
        load_reference(reference)  # fail early on unsupported/missing source texture
        original_triangles = []
        offsets = []
        face_offset = 0
        for source, matrix in zip(sources, matrices, strict=True):
            mesh = source.state.surface.mesh
            original_triangles.append((mesh.vertices @ matrix[:3, :3].T + matrix[:3, 3])[mesh.faces])
            offsets.append(face_offset)
            face_offset += len(mesh.faces)
        lineage = output / "projected_lineage.npz"
        low_faces = np.concatenate([part.source_faces + offsets[part.source_index] for part in parts.values()])
        np.savez_compressed(lineage, original_low_triangles=np.concatenate(original_triangles),
                            source_low_faces=low_faces, source_low_corner_barycentric=np.concatenate(barycentrics))
        fraction = request.get("projection_max_fraction", .02)
        if not isinstance(fraction, (int, float)) or isinstance(fraction, bool) or not np.isfinite(fraction) or fraction <= 0:
            raise ValueError("PARTS_POSITIVE_FINITE_PROJECTION_FRACTION_REQUIRED")
        metadata["reference_glb"] = str(reference.resolve())
        metadata["projected_source"] = {"schema_version": 1, "reference_glb": str(reference.resolve()),
            "reference_sha256": file_hash(reference), "projection_max_fraction": fraction,
            "lineage_npz": str(lineage.resolve()), "lineage_sha256": file_hash(lineage)}
        source_manifest.extend({"path": str(path.resolve()), "sha256": file_hash(path)} for path in (reference, lineage))
    check_deadline(deadline)
    root = save_snapshot(BudgetState(surface, groups=tuple(groups)), output / "snapshot", metadata)
    control = save_snapshot(BudgetState(surface.copy()), output / "unshared", metadata, root)
    counts = np.concatenate(triangles)
    # Coordinate equality here is a diagnostic only, never a topology weld.
    canonical = np.array([triangle[np.lexsort(triangle.T[::-1])] for triangle in quantized[counts]])
    _, multiplicity = np.unique(canonical.reshape(-1, 9), axis=0, return_counts=True)
    report: dict[str, object] = {"parts": rows, "sources": inventory, "omitted_faces": omitted,
        "segmentation_transfer": segmentation_report,
        "source_tiles": tile_records, "faces": len(surface.mesh.faces), "shared_groups": len(groups),
        "float32_position_max_abs_error": float(np.max(np.abs(xyz-quantized))),
        "duplicate_triangle_candidates": int(np.sum(multiplicity-1)),
        "boundary_edges": sum(len(adjacent) == 1 for adjacent in surface.mesh.edge_faces),
        "segmentation_added_caps": "not_checked_without_presegmentation_correspondence; explicit cap_source_faces retained",
        "connection_appearance": "not_reviewed", "full_pbr_passed": False, "quality_accepted": None,
        "scope": ("UV+projected BaseColor diagnostic; nearest surface is not original face identity" if projected_reference
                  else "geometry" if atlas is None else "UV+BaseColor; material factors explicitly normalized to RGBA8"),
        "lineage": str(correspondence.resolve()), "snapshot": snapshot_result(root), "unshared": snapshot_result(control)}
    write_json(output / "parts.json", report)
    return {**snapshot_result(root), "unshared_snapshot": str(control.root / "snapshot.json"),
            "report": str((output / "parts.json").resolve()), "part_count": len(parts),
            "reference_glb": metadata.get("reference_glb"), "requires_rebake": True, "final_quality_passed": None}
