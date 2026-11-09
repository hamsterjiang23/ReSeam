"""Small, reversible artist operations. Visual decisions are explicit evidence."""

from dataclasses import replace
from pathlib import Path
from typing import cast

import numpy as np

from ..budget_state import BudgetState, owners_and_members
from ..joint_types import ReuseGroup, ReuseMember
from ..mesh import chart_labels
from ..reuse_transform import similarity_transform
from ..types import FloatArray
from .regions import face_selection, selected_faces
from .state import (
    Snapshot,
    check_deadline,
    load_snapshot,
    object_hash,
    read_object,
    require_snapshot,
    save_snapshot,
    snapshot_result,
    write_json,
)


def member_faces(group: ReuseGroup) -> tuple[tuple[int, ...], ...]:
    return (group.first_faces, *(m.faces for m in group.member_maps)) if group.member_maps else (
        group.first_faces, group.second_faces)


def locks(snapshot: Snapshot) -> list[dict[str, object]]:
    return cast(list[dict[str, object]], snapshot.metadata.get("artist_locks", []))


def color_locked_faces(snapshot: Snapshot) -> list[int]:
    return sorted({f for lock in locks(snapshot) if "color" in cast(list[str], lock["kinds"])
                   for f in cast(list[int], lock["faces"])})


def enforce_locks(state: BudgetState, parent: Snapshot) -> None:
    """Called by snapshot persistence, including legacy mutation entry points."""
    for lock in locks(parent):
        reference = load_snapshot(Path(str(lock["snapshot"])))
        if reference.state_hash != lock["state_hash"]:
            raise ValueError("ARTIST_LOCK_REFERENCE_CHANGED")
        ids = cast(list[int], lock["faces"])
        old = reference.state.surface
        new = state.surface
        a, b = old.uv[old.uv_faces[ids]], new.uv[new.uv_faces[ids]]
        kinds = cast(list[str], lock["kinds"])
        if "position" in kinds and not np.allclose(a, b, rtol=0, atol=1e-10):
            raise ValueError(f"ARTIST_POSITION_LOCK:{lock['name']}")
        if "shape" in kinds:
            # Per chart common similarity permits layout movement, but no local distortion.
            labels = chart_labels(old)[ids]
            for label in np.unique(labels):
                x, y = a[labels == label].reshape(-1, 2), b[labels == label].reshape(-1, 2)
                x, y = x - x.mean(axis=0), y - y.mean(axis=0)
                u, _, vt = np.linalg.svd(x.T @ y)
                rotation = u @ vt
                scale = float(np.sum((x @ rotation) * y) / max(float(np.sum(x * x)), 1e-30))
                if np.linalg.det(rotation) < 0 or not np.allclose(x @ rotation * scale, y, atol=1e-8):
                    raise ValueError(f"ARTIST_SHAPE_LOCK:{lock['name']}")
        if "density" in kinds:
            aa = np.abs(np.linalg.det(a[:, 1:] - a[:, :1]))
            bb = np.abs(np.linalg.det(b[:, 1:] - b[:, :1]))
            if np.any(bb + 1e-12 < aa):
                raise ValueError(f"ARTIST_DENSITY_LOCK:{lock['name']}")


def basic_check(snapshot: Snapshot) -> dict[str, object]:
    surface = snapshot.state.surface
    corners = surface.uv[surface.uv_faces]
    return {"finite_uv": bool(np.isfinite(corners).all()), "faces": len(corners),
            "zero_area_faces": np.flatnonzero(np.abs(np.linalg.det(
                corners[:, 1:] - corners[:, :1])) <= 1e-16).tolist(),
            "uv_charts": len(np.unique(chart_labels(surface))),
            "packing_owners": len(np.unique(owners_and_members(snapshot.state)[0])),
            "shared_groups": len(snapshot.state.groups)}


def visual_policy(request: dict[str, object], snapshot: Snapshot) -> bool:
    policy = request.get("acceptance_policy", snapshot.metadata.get("artist_acceptance_policy", "numeric"))
    if policy not in ("visual", "numeric"):
        raise ValueError("ACCEPTANCE_POLICY_VISUAL_OR_NUMERIC")
    if policy == "visual":
        checked = basic_check(snapshot)
        if not checked["finite_uv"] or checked["zero_area_faces"]:
            raise ValueError("UNRENDERABLE_UV_REPAIR_ZERO_AREA_OR_NONFINITE")
        if snapshot.metadata.get("requires_pack"):
            raise ValueError("PACK_RELEASED_MEMBERS_BEFORE_BAKING")
    return policy == "visual"


def finish(snapshot: Snapshot, state: BudgetState, output: Path, operation: str,
           details: dict[str, object], *, metadata: dict[str, object] | None = None,
           changed: bool = True) -> dict[str, object]:
    meta = dict(snapshot.metadata) if metadata is None else metadata
    meta["artist_acceptance_policy"] = "visual"
    if changed:
        for key in ("bake", "bake_result", "quality", "quality_result", "validation", "diagnostic_candidate"):
            meta.pop(key, None)
        meta.update(requires_rebake=True, artist_review_status="pending")
        state.invalidate()
    meta.update(last_operation=operation)
    child = save_snapshot(state, output / "snapshot", meta, snapshot)
    record = {"operation": operation, "before": snapshot_result(snapshot), "after": snapshot_result(child),
              "before_counts": basic_check(snapshot), "after_counts": basic_check(child), **details,
              "appearance_decision": "pending" if changed else "unchanged",
              "numeric_quality_is_advisory": True, "requires_rebake": meta.get("requires_rebake", False)}
    write_json(output / "change.json", record)
    return {**snapshot_result(child), "change_card": str((output / "change.json").resolve()), **details}


def protect(request: dict[str, object], output: Path, deadline: float) -> dict[str, object]:
    check_deadline(deadline)
    snapshot = require_snapshot(request)
    name = request.get("name")
    if not isinstance(name, str) or not name.strip():
        raise ValueError("PROTECTION_NAME_REQUIRED")
    action = request.get("action", "set")
    current = [dict(item) for item in locks(snapshot) if item["name"] != name]
    if action == "set":
        kinds = request.get("kinds", ["shape", "color"])
        if (not isinstance(kinds, list) or not kinds or any(
                kind not in ("shape", "position", "color", "density") for kind in kinds)):
            raise ValueError("LOCK_KINDS_SHAPE_POSITION_COLOR_DENSITY")
        if "selected_faces" not in request and "region_id" not in request:
            raise ValueError("EXPLICIT_PROTECTION_SELECTION_REQUIRED")
        faces = selected_faces(request, snapshot)
        previous = next((item for item in locks(snapshot) if item["name"] == name), None)
        if previous is not None and previous["faces"] != faces and request.get("replace") is not True:
            raise ValueError("PROTECTION_NAME_IN_USE:choose another name or explicit replace=true")
        current.append({"name": name, "faces": faces, "kinds": kinds,
                        **snapshot_result(snapshot)})
    elif action != "remove" or not any(item["name"] == name for item in locks(snapshot)):
        raise ValueError("UNKNOWN_PROTECTION_OR_ACTION")
    meta = {**snapshot.metadata, "artist_locks": current}
    return finish(snapshot, snapshot.state.copy(), output, "artist.protect", {"locks": current},
                  metadata=meta, changed=False)


def select(request: dict[str, object], output: Path, deadline: float) -> dict[str, object]:
    check_deadline(deadline)
    snapshot = require_snapshot(request)
    surface = snapshot.state.surface
    modes = sum(key in request for key in ("uv_point", "view_pick", "selected_faces", "region_id"))
    if modes != 1:
        raise ValueError("SELECT_ONE_OF_UV_POINT_VIEW_PICK_FACES_REGION")
    ids: list[int]
    if "uv_point" in request:
        point = np.asarray(request["uv_point"], dtype=float)
        if point.shape != (2,) or not np.isfinite(point).all():
            raise ValueError("FINITE_UV_POINT_REQUIRED")
        tri = surface.uv[surface.uv_faces]
        matrices = np.transpose(tri[:, 1:] - tri[:, :1], (0, 2, 1))
        valid = np.abs(np.linalg.det(matrices)) > 1e-16
        tail = np.zeros((len(tri), 2))
        tail[valid] = np.linalg.solve(matrices[valid], (point - tri[valid, 0])[..., None])[..., 0]
        ids = np.flatnonzero(valid & (tail.min(axis=1) >= -1e-8) & (tail.sum(axis=1) <= 1+1e-8)).tolist()
    elif "view_pick" in request:
        pick = cast(dict[str, object], request["view_pick"])
        report = read_object(Path(str(pick["report"])))
        if report["state_hash"] != snapshot.state_hash:
            raise ValueError("STALE_VIEW_SELECTION")
        rasters = cast(dict[str, str], report["face_rasters"])
        with np.load(rasters[str(pick["camera"])], allow_pickle=False) as data:
            x, y = cast(list[int], pick["pixel"])
            raster = data["face_ids"]
            if type(x) is not int or type(y) is not int or not (0 <= y < raster.shape[0] and 0 <= x < raster.shape[1]):
                raise ValueError("PICK_OUT_OF_BOUNDS")
            face = int(raster[y, x])
            ids = [] if face < 0 else [face]
    else:
        ids = selected_faces(request, snapshot)
    labels = chart_labels(surface)
    expand = request.get("expand", "chart")
    if expand == "chart":
        ids = np.flatnonzero(np.isin(labels, labels[ids])).tolist()
    elif expand != "face":
        raise ValueError("SELECT_EXPAND_FACE_OR_CHART")
    groups = [{"group_id": g.group_id, "members": [list(m) for m in member_faces(g)]}
              for g in snapshot.state.groups if set(ids).intersection(g.first_faces + g.second_faces)]
    linked = sorted(set(ids) | {f for g in groups for m in cast(list[list[int]], g["members"]) for f in m})
    result = {**snapshot_result(snapshot), "selected_faces": ids, "linked_faces": linked,
              "chart_ids": np.unique(labels[ids]).tolist(), "groups": groups,
              "uv_triangles": surface.uv[surface.uv_faces[ids]].tolist(),
              "model_centers": surface.mesh.vertices[surface.mesh.faces[ids]].mean(axis=1).tolist()}
    write_json(output / "selection.json", result)
    return {**result, "selection": str((output / "selection.json").resolve())}


def checkpoint(request: dict[str, object], output: Path, deadline: float) -> dict[str, object]:
    check_deadline(deadline)
    snapshot = require_snapshot(request)
    action = request.get("action", "save")
    if action == "save":
        files = request.get("files", {})
        if not isinstance(files, dict) or any(not isinstance(v, str) for v in files.values()):
            raise ValueError("CHECKPOINT_FILES_REQUIRE_PATH_MAP")
        from .state import file_hash
        dependencies: dict[str, str] = {}
        for value in files.values():
            path = Path(str(value))
            if path.suffix.lower() == ".json":
                attached = read_object(path)
                if attached.get("scope") == "basecolor_reuse" and "target" in attached and "resolution" in attached:
                    from .color_reuse import _load
                    _load(snapshot, path)
                    dependencies.update({str((path.parent / name).resolve()): digest
                                         for name, digest in cast(dict[str, str], attached["files"]).items()})
                    target = Path(str(attached["target"]))
                    dependencies[str(target.resolve())] = file_hash(target)
                    for dependency in target.parent.glob("*.npz"):
                        dependencies[str(dependency.resolve())] = file_hash(dependency)
        record = {**snapshot_result(snapshot), "label": str(request.get("label", "checkpoint")),
                  "dependencies": dependencies,
                  "files": {str(k): {"path": str(Path(str(v)).resolve()), "sha256": file_hash(Path(str(v)))}
                            for k, v in files.items()}}
        write_json(output / "checkpoint.json", {**record, "checkpoint_hash": object_hash(record)})
        return {**snapshot_result(snapshot), "checkpoint": str((output / "checkpoint.json").resolve())}
    if action != "restore":
        raise ValueError("CHECKPOINT_ACTION_SAVE_OR_RESTORE")
    record = read_object(Path(str(request["checkpoint"])))
    digest = record.pop("checkpoint_hash", None)
    if digest != object_hash(record):
        raise ValueError("CHECKPOINT_HASH_MISMATCH")
    restored = require_snapshot(record)
    if restored.asset_id != snapshot.asset_id or not np.array_equal(
            restored.state.surface.mesh.faces, snapshot.state.surface.mesh.faces) or not np.array_equal(
            restored.state.surface.mesh.vertices, snapshot.state.surface.mesh.vertices):
        raise ValueError("CHECKPOINT_ASSET_MISMATCH")
    from .state import file_hash
    for item in cast(dict[str, dict[str, str]], record["files"]).values():
        if file_hash(Path(item["path"])) != item["sha256"]:
            raise ValueError("CHECKPOINT_ATTACHED_FILE_CHANGED")
    for dependency, digest in cast(dict[str, str], record.get("dependencies", {})).items():
        if file_hash(Path(dependency)) != digest:
            raise ValueError("CHECKPOINT_DEPENDENCY_CHANGED")
    # Restore the whole immutable bundle, including old locks and baked files. Do not replay partial UV edits.
    result = {**snapshot_result(restored), "restored_from": snapshot.state_hash, "files": record["files"],
              "restored_complete_state": True}
    write_json(output / "restore.json", result)
    return result


def _matrices(group: ReuseGroup) -> list[FloatArray]:
    if group.member_maps:
        transforms = [m.first_to_member for m in group.member_maps]
    elif group.first_to_second is not None:
        transforms = [group.first_to_second]
    else:
        normal = np.asarray(group.plane_normal, dtype=float)
        normal /= np.linalg.norm(normal)
        transform = np.column_stack((np.eye(3) - 2*np.outer(normal, normal), 2*group.plane_offset*normal))
        transforms = [similarity_transform(transform)]
    result = [np.eye(4)]
    for transform in transforms:
        value = np.eye(4)
        value[:3] = transform
        result.append(value)
    return result


def groups(request: dict[str, object], output: Path, deadline: float) -> dict[str, object]:
    check_deadline(deadline)
    snapshot = require_snapshot(request)
    listing = [{"group_id": g.group_id, "master_member": 0,
                "members": [list(m) for m in member_faces(g)]} for g in snapshot.state.groups]
    action = request.get("action", "inspect")
    if action == "inspect":
        write_json(output / "groups.json", listing)
        return {"groups": listing, "state_hash": snapshot.state_hash}
    group = next((g for g in snapshot.state.groups if g.group_id == request.get("group_id")), None)
    if group is None:
        raise ValueError("UNKNOWN_SHARED_GROUP")
    domains, matrices = member_faces(group), _matrices(group)
    index = request.get("member", 0)
    if type(index) is not int or not 0 <= index < len(domains):
        raise ValueError("MEMBER_INDEX_OUT_OF_RANGE")
    if action not in ("set_master", "release_member", "unshare"):
        raise ValueError("GROUP_ACTION_INSPECT_SET_MASTER_RELEASE_MEMBER_UNSHARE")
    order = ([index] + [i for i in range(len(domains)) if i != index] if action == "set_master"
             else [i for i in range(len(domains)) if i != index] if action == "release_member" else [])
    replacement: ReuseGroup | None = None
    if len(order) >= 2:
        orientations = [group.first_orientation] + ([m.orientation for m in group.member_maps]
                        if group.member_maps else [group.second_orientation])
        root = order[0]
        members = tuple(ReuseMember(domains[i], orientations[i], similarity_transform(
            (matrices[i] @ np.linalg.inv(matrices[root]))[:3])) for i in order[1:])
        replacement = replace(group, first_faces=domains[root],
            second_faces=tuple(f for m in members for f in m.faces), first_orientation=orientations[root],
            second_orientation=members[0].orientation,
            first_to_second=members[0].first_to_member if len(members) == 1 else None,
            member_maps=members if len(members) > 1 else ())
    state = snapshot.state.copy()
    state.groups = tuple(g for g in state.groups if g.group_id != group.group_id) + (
        (replacement,) if replacement is not None else ())
    touched = set(group.first_faces + group.second_faces)
    state.correspondences = tuple(c for c in state.correspondences
                                 if c.source_face not in touched and c.target_face not in touched)
    state.correspondence_records = tuple(r for r in state.correspondence_records
                                        if r.get("group_id", r.get("group")) != group.group_id)
    needs_pack = action != "set_master" or snapshot.metadata.get("requires_pack") is True
    meta = {**snapshot.metadata, "requires_pack": needs_pack}
    return finish(snapshot, state, output, "artist.groups", {"action": action,
                  "affected_faces": sorted(touched), "requires_pack": needs_pack}, metadata=meta)


def relax(request: dict[str, object], output: Path, deadline: float) -> dict[str, object]:
    """Pinned Laplacian relaxation; UV lookup transfers motion to every shared member."""
    snapshot = require_snapshot(request)
    if "selected_faces" not in request and "region_id" not in request:
        raise ValueError("EXPLICIT_RELAX_SELECTION_REQUIRED")
    state = snapshot.state.copy()
    surface = state.surface
    labels = chart_labels(surface)
    selected = selected_faces(request, snapshot)
    selected = np.flatnonzero(np.isin(labels, labels[selected])).tolist()
    root_faces = set(selected)
    touched: list[ReuseGroup] = []
    for group in state.groups:
        if root_faces.intersection(group.first_faces + group.second_faces):
            touched.append(group)
            root_faces.difference_update(group.second_faces)
            root_faces.update(group.first_faces)
    faces = sorted(root_faces)
    old_uv = surface.uv.copy()
    active = set(map(int, np.unique(surface.uv_faces[faces])))
    pins = set(face_selection(request.get("pin_uv_ids", []), len(old_uv), allow_empty=True))
    requested_pins = sorted(pins)
    # A slave pin is a constraint on the corresponding master location too.
    # Freeze contributing master vertices without welding either member's identity.
    for group in touched:
        slave_ids = set(map(int, np.unique(surface.uv_faces[list(group.second_faces)])))
        root_ids = surface.uv_faces[list(group.first_faces)]
        tri = old_uv[root_ids]
        inverse = np.linalg.inv(np.transpose(tri[:, 1:] - tri[:, :1], (0, 2, 1)))
        for vertex in sorted(pins & slave_ids):
            tail = np.einsum("nij,nj->ni", inverse, old_uv[vertex] - tri[:, 0])
            bary = np.column_stack((1-tail.sum(axis=1), tail))
            candidates = np.flatnonzero(bary.min(axis=1) >= -1e-7)
            if not len(candidates):
                raise ValueError("PIN_OUTSIDE_SHARED_MASTER")
            hit = int(candidates[0])
            pins.update(map(int, root_ids[hit][np.abs(bary[hit]) > 1e-8]))
    edges: dict[tuple[int, int], int] = {}
    adjacent: dict[int, set[int]] = {i: set() for i in active}
    for tri in surface.uv_faces[faces]:
        for a, b in zip(tri, np.roll(tri, -1), strict=True):
            a, b = int(a), int(b)
            edge = (min(a, b), max(a, b))
            edges[edge] = edges.get(edge, 0) + 1
            adjacent[a].add(b)
            adjacent[b].add(a)
    if request.get("pin_boundary", True):
        pins.update(i for edge, count in edges.items() if count == 1 for i in edge)
    outside = sorted(set(range(len(surface.mesh.faces))) - set(faces))
    pins.update(map(int, np.unique(surface.uv_faces[outside])))
    movable = sorted(active - pins)
    iterations, strength = request.get("iterations", 20), request.get("strength", .25)
    if type(iterations) is not int or not 1 <= iterations <= 200:
        raise ValueError("RELAX_ITERATIONS_1_TO_200")
    if isinstance(strength, bool) or not isinstance(strength, (int, float)) or not 0 < strength <= 1:
        raise ValueError("RELAX_STRENGTH_0_TO_1")
    signs = np.linalg.det(old_uv[surface.uv_faces[faces, 1:]] - old_uv[surface.uv_faces[faces, :1]])
    for _ in range(iterations):
        check_deadline(deadline)
        proposal = surface.uv.copy()
        for vertex in movable:
            mean = surface.uv[sorted(adjacent[vertex])].mean(axis=0)
            proposal[vertex] += float(strength) * (mean - proposal[vertex])
        for _backtrack in range(12):
            tri = proposal[surface.uv_faces[faces]]
            determinants = np.linalg.det(tri[:, 1:] - tri[:, :1])
            if np.all(determinants * signs > 0) and np.all(np.abs(determinants) >= np.abs(signs)*1e-6):
                surface.uv[:] = proposal
                break
            proposal = .5*(proposal + surface.uv)
    for group in touched:
        root = list(group.first_faces)
        tri = old_uv[surface.uv_faces[root]]
        matrices = np.transpose(tri[:, 1:] - tri[:, :1], (0, 2, 1))
        inverse = np.linalg.inv(matrices)
        for vertex in np.unique(surface.uv_faces[list(group.second_faces)]):
            check_deadline(deadline)
            point = old_uv[vertex]
            tail = np.einsum("nij,nj->ni", inverse, point - tri[:, 0])
            bary = np.column_stack((1 - tail.sum(axis=1), tail))
            candidates = np.flatnonzero(np.min(bary, axis=1) >= -1e-7)
            if not len(candidates):
                raise ValueError("SHARED_POINT_OUTSIDE_MASTER:release member before local relaxation")
            hit = int(candidates[0])
            surface.uv[vertex] = bary[hit] @ surface.uv[surface.uv_faces[root[hit]]]
    affected = sorted(set(faces) | {f for g in touched for f in g.second_faces})
    if not np.allclose(surface.uv[requested_pins], old_uv[requested_pins], atol=1e-8, rtol=0):
        raise ValueError("SHARED_PIN_MOVED")
    return finish(snapshot, state, output, "artist.relax", {"affected_faces": affected,
                  "pinned_uv_ids": sorted(pins & active), "requested_pin_uv_ids": requested_pins,
                  "movable_uv_count": len(movable),
                  "max_uv_motion": float(np.max(np.linalg.norm(surface.uv - old_uv, axis=1))),
                  "synchronized_groups": [g.group_id for g in touched]})


def trials(request: dict[str, object], output: Path, deadline: float) -> dict[str, object]:
    """All alternatives start at exactly the same immutable parent."""
    snapshot = require_snapshot(request)
    alternatives = request.get("alternatives")
    if not isinstance(alternatives, list) or not 1 <= len(alternatives) <= 8:
        raise ValueError("TRIALS_REQUIRE_1_TO_8_ALTERNATIVES")
    from .worker import execute
    results: list[dict[str, object]] = []
    for index, raw in enumerate(alternatives):
        check_deadline(deadline)
        if not isinstance(raw, dict):
            raise TypeError("TRIAL_OBJECT_REQUIRED")
        alternative = cast(dict[str, object], raw)
        operation = alternative.get("tool")
        params = alternative.get("request", {})
        if not isinstance(params, dict):
            raise TypeError("TRIAL_REQUEST_OBJECT_REQUIRED")
        subrequest = {**params, **snapshot_result(snapshot), "acceptance_policy": "visual"}
        folder = output / f"trial-{index + 1:02d}"
        folder.mkdir(parents=True)
        write_json(folder / "request.json", subrequest)
        if operation in ("artist.relax", "artist.groups", "uv.reparameterize", "uv.pack"):
            # Execute catches per-alternative failures, preserving subsequent independent trials.
            result = execute(str(operation), subrequest, folder, deadline)
        else:
            raise ValueError("TRIAL_TOOL_RELAX_GROUPS_PACK_REPARAMETERIZE")
        results.append({"label": str(alternative.get("label", f"方案 {index + 1}")), "tool": operation,
                        "result": result})
    report = {"parent": snapshot_result(snapshot), "alternatives": results,
              "selected": None, "decision": "pending_visual_review", "automatic_numeric_winner": False}
    write_json(output / "trials.json", report)
    return {"report": str((output / "trials.json").resolve()), **report}
