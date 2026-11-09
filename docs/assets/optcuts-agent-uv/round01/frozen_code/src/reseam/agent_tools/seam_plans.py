"""Versioned seam programs: observation and planning never mutate an asset."""

import time
from pathlib import Path
from typing import cast

import numpy as np

from ..mesh import make_mesh
from ..types import Mesh
from .regional_cut import cut_region
from .regions import face_selection
from .state import Snapshot, check_deadline, object_hash, read_object, require_snapshot, write_json


def _ids(value: object, count: int) -> list[int]:
    return face_selection(value, count, allow_empty=True)


def _nonmanifold_vertices(mesh: Mesh) -> list[int]:
    """A triangular 2-manifold has one path or cycle as every vertex link."""
    links: dict[int, list[tuple[int, int]]] = {}
    for face in mesh.faces:
        for corner in range(3):
            links.setdefault(int(face[corner]), []).append(
                (int(face[(corner + 1) % 3]), int(face[(corner + 2) % 3])))
    invalid: list[int] = []
    for vertex, edges in links.items():
        adjacency: dict[int, list[int]] = {}
        for a, b in edges:
            adjacency.setdefault(a, []).append(b)
            adjacency.setdefault(b, []).append(a)
        pending = [next(iter(adjacency))]
        reached: set[int] = set()
        while pending:
            node = pending.pop()
            if node not in reached:
                reached.add(node)
                pending.extend(adjacency[node])
        degrees = [len(neighbors) for neighbors in adjacency.values()]
        if len(reached) != len(adjacency) or any(d > 2 for d in degrees) or degrees.count(1) not in (0, 2):
            invalid.append(vertex)
    return sorted(invalid)


def _semantic_boundary_edges(snapshot: Snapshot) -> set[int]:
    labels: dict[int, set[str]] = {}
    for region in cast(list[dict[str, object]], snapshot.metadata.get("regions", [])):
        for face in cast(list[int], region.get("faces", [])):
            labels.setdefault(face, set()).add(str(region.get("name", "")))
    return {index for index, entries in enumerate(snapshot.state.surface.mesh.edge_faces)
            if len(entries) == 2 and labels.get(entries[0][0], set()) != labels.get(entries[1][0], set())}


def cut_topology(snapshot: Snapshot, faces: list[int], edges: list[int], deadline: float) -> dict[str, object]:
    surface = snapshot.state.surface
    cut = cut_region(surface, tuple(faces), [surface.mesh.edges[i] for i in edges], deadline)
    mesh = cut.surface.mesh
    neighbors: list[list[int]] = [[] for _ in mesh.faces]
    for entries in mesh.edge_faces:
        if len(entries) == 2:
            a, b = entries[0][0], entries[1][0]
            neighbors[a].append(b)
            neighbors[b].append(a)
    remaining = set(range(len(faces)))
    charts: list[dict[str, object]] = []
    while remaining:
        check_deadline(deadline)
        pending = [min(remaining)]
        members: set[int] = set()
        while pending:
            face = pending.pop()
            if face in members:
                continue
            members.add(face)
            pending.extend(n for n in neighbors[face] if n not in members)
        remaining.difference_update(members)
        local = make_mesh(mesh.vertices, mesh.faces[sorted(members)])
        boundary = [edge for edge, entries in zip(local.edges, local.edge_faces, strict=True) if len(entries) == 1]
        boundary_neighbors: dict[int, set[int]] = {}
        for a, b in boundary:
            boundary_neighbors.setdefault(a, set()).add(b)
            boundary_neighbors.setdefault(b, set()).add(a)
        unseen = set(boundary_neighbors)
        loops = 0
        while unseen:
            loops += 1
            queue = [unseen.pop()]
            while queue:
                for n in boundary_neighbors[queue.pop()]:
                    if n in unseen:
                        unseen.remove(n)
                        queue.append(n)
        chi = len(np.unique(local.faces)) - len(local.edges) + len(local.faces)
        manifold = all(len(e) <= 2 for e in local.edge_faces)
        bad_vertices = _nonmanifold_vertices(local)
        regular = all(len(n) == 2 for n in boundary_neighbors.values())
        charts.append({"faces": [faces[i] for i in sorted(members)], "euler": chi,
                       "boundary_loops": loops, "boundary_regular": regular,
                       "manifold_edges": manifold, "manifold_vertices": not bad_vertices,
                       "nonmanifold_vertex_ids": bad_vertices,
                       "disk": manifold and not bad_vertices and regular and chi == 1 and loops == 1})
    degrees: dict[int, int] = {}
    for index in edges:
        for vertex in surface.mesh.edges[index]:
            degrees[vertex] = degrees.get(vertex, 0) + 1
    boundary_vertices = {v for e, entries in zip(surface.mesh.edges, surface.mesh.edge_faces, strict=True)
                         if len(entries) == 1 for v in e}
    return {"charts": charts, "chart_count": len(charts), "all_disks": all(c["disk"] for c in charts),
            "free_seam_ends": sorted(v for v, d in degrees.items() if d == 1 and v not in boundary_vertices),
            "non_disk_count": sum(not c["disk"] for c in charts)}


def _mirror(snapshot: Snapshot, edges: list[int], settings: dict[str, object]) -> list[int]:
    mesh = snapshot.state.surface.mesh
    normal = np.asarray(settings.get("normal", [1, 0, 0]), dtype=float)
    if normal.shape != (3,) or not np.isfinite(normal).all() or np.linalg.norm(normal) <= 0:
        raise ValueError("INVALID_MIRROR_PLANE")
    normal /= np.linalg.norm(normal)
    offset = float(str(settings.get("offset", 0)))
    tolerance = float(str(settings.get("tolerance_fraction", .02))) * mesh.scale
    if not np.isfinite(offset) or not np.isfinite(tolerance) or tolerance <= 0:
        raise ValueError("INVALID_MIRROR_TOLERANCE")
    from scipy.spatial import KDTree

    mids = mesh.vertices[np.asarray(mesh.edges)].mean(axis=1)
    reflected = mids[edges] - 2 * (mids[edges] @ normal - offset)[:, None] * normal
    distance, ids = KDTree(mids).query(reflected)
    if np.any(distance > tolerance):
        raise ValueError("MIRROR_EDGE_CORRESPONDENCE_OUTSIDE_TOLERANCE")
    result = [int(i) for i in np.atleast_1d(ids)]
    if len(set(result)) != len(result):
        raise ValueError("MIRROR_EDGE_CORRESPONDENCE_NOT_INJECTIVE")
    for source, target in zip(edges, result, strict=True):
        a = mesh.vertices[list(mesh.edges[source])]
        a = a - 2 * (a @ normal - offset)[:, None] * normal
        b = mesh.vertices[list(mesh.edges[target])]
        if min(np.linalg.norm(a-b, axis=1).max(), np.linalg.norm(a-b[::-1], axis=1).max()) > tolerance:
            raise ValueError("MIRROR_EDGE_ENDPOINT_MISMATCH")
    return sorted(result)


def _resolve(value: object, results: dict[str, dict[str, object]], field: str = "") -> object:
    if isinstance(value, dict):
        if set(value) == {"ref"}:
            if field not in ("edge_ids", "waypoint_edges"):
                raise TypeError("EDGE_REFERENCE_ARGUMENT_TYPE_MISMATCH:" + field)
            name = str(value["ref"])
            if name not in results:
                raise KeyError(name)
            return results[name]["edge_ids"]
        return {str(k): _resolve(v, results, str(k)) for k, v in value.items()}
    if isinstance(value, list):
        return [_resolve(v, results, field) for v in value]
    return value


def evaluate_program(snapshot: Snapshot, program: object, deadline: float) -> tuple[list[int], list[dict[str, object]]]:
    from .seam_selection import select_edges

    if not isinstance(program, list) or not program or any(not isinstance(s, dict) for s in program):
        raise ValueError("SEAM_PROGRAM_REQUIRES_STATEMENTS")
    statements = cast(list[dict[str, object]], program)
    names = [s.get("name") for s in statements]
    if any(not isinstance(n, str) or not n for n in names) or len(set(names)) != len(names):
        raise ValueError("UNIQUE_STATEMENT_NAMES_REQUIRED")
    results: dict[str, dict[str, object]] = {}
    pending = statements.copy()
    traces: list[dict[str, object]] = []
    selected: set[int] = set()
    while pending:
        progressed = False
        for statement in pending.copy():
            check_deadline(deadline)
            if type(statement.get("emit", True)) is not bool:
                raise TypeError("STATEMENT_EMIT_MUST_BE_BOOLEAN")
            if not isinstance(statement.get("mirror", False), (bool, dict)):
                raise TypeError("STATEMENT_MIRROR_MUST_BE_BOOLEAN_OR_PLANE")
            try:
                args = _resolve(statement.get("arguments", {}), results)
            except KeyError:
                continue
            if not isinstance(args, dict):
                raise TypeError("STATEMENT_ARGUMENTS_MUST_BE_OBJECT")
            started = time.monotonic()
            result = select_edges(snapshot, str(statement.get("operator")), args, deadline)
            if args.get("index") is not None:
                from .mesh_query import load_index
                connection, index_record = load_index(args, snapshot)
                connection.close()
                result["index_binding"] = {"index": args["index"], "index_hash": index_record["index_hash"]}
            edges = _ids(result["edge_ids"], len(snapshot.state.surface.mesh.edges))
            mirror = statement.get("mirror", False)
            if mirror:
                settings = mirror if isinstance(mirror, dict) else {}
                mirrored = _mirror(snapshot, edges, settings)
                result["mirrored_edge_ids"] = mirrored
                edges = sorted(set(edges).union(mirrored))
                result["edge_ids"] = edges
            results[str(statement["name"])] = result
            if statement.get("emit", True):
                selected.update(edges)
            traces.append({"name": statement["name"], "statement": statement, "result": result,
                           "seconds": time.monotonic()-started})
            pending.remove(statement)
            progressed = True
        if not progressed:
            raise ValueError("CYCLIC_OR_UNKNOWN_STATEMENT_REFERENCE")
    return sorted(selected), traces


def _selection(request: dict[str, object], snapshot: Snapshot, deadline: float) -> tuple[list[int], list[dict[str, object]]]:
    if "program" in request:
        return evaluate_program(snapshot, request["program"], deadline)
    return _ids(request.get("edge_ids", []), len(snapshot.state.surface.mesh.edges)), []


def _faces(request: dict[str, object], snapshot: Snapshot) -> list[int]:
    return face_selection(request.get("faces", list(range(len(snapshot.state.surface.mesh.faces)))),
                          len(snapshot.state.surface.mesh.faces))


def _load_plan(request: dict[str, object], snapshot: Snapshot) -> dict[str, object]:
    plan = read_object(Path(str(request["plan"])))
    digest = plan.pop("plan_hash", None)
    if digest != object_hash(plan):
        raise ValueError("PLAN_HASH_MISMATCH")
    if plan["state_hash"] != snapshot.state_hash or plan["asset_id"] != snapshot.asset_id:
        raise ValueError("STALE_SEAM_PLAN")
    if plan.get("index") is not None:
        from .mesh_query import load_index
        connection, _ = load_index({"index": plan["index"], "index_hash": plan["index_hash"]}, snapshot)
        connection.close()
    for binding in cast(list[dict[str, object]], plan.get("index_bindings", [])):
        from .mesh_query import load_index
        connection, _ = load_index(binding, snapshot)
        connection.close()
    plan["plan_hash"] = digest
    return plan


def inspect_cut_topology(request: dict[str, object], output: Path, deadline: float) -> dict[str, object]:
    snapshot = require_snapshot(request)
    source = _load_plan(request, snapshot) if "plan" in request else request
    edges, _ = _selection(source, snapshot, deadline)
    report = cut_topology(snapshot, _faces(source, snapshot), edges, deadline)
    write_json(output / "cut_topology.json", report)
    return {**report, "report": str(output / "cut_topology.json"), "state_hash": snapshot.state_hash}


def preview_seams(request: dict[str, object], output: Path, deadline: float) -> dict[str, object]:
    from .views import render_views

    snapshot = require_snapshot(request)
    source = _load_plan(request, snapshot) if "plan" in request else request
    edges, traces = _selection(source, snapshot, deadline)
    directory = output / "views"
    directory.mkdir(parents=True, exist_ok=False)
    view = render_views({**request, "mode": "wireframe", "seam_edge_ids": edges,
                         "selected_faces": _faces(source, snapshot)}, directory, deadline)
    write_json(output / "selection.json", {"edge_ids": edges, "traces": traces})
    return {**view, "edge_ids": edges, "selection": str(output / "selection.json"), "uv_modified": False}


def _merge_thin_bands(
    snapshot: Snapshot, faces: list[int], edges: list[int], topology: dict[str, object], deadline: float,
) -> tuple[list[int], dict[str, object], list[dict[str, object]]]:
    """Remove a complete redundant interface, never an arbitrary single edge.

    A candidate band has two neighboring charts, every face touches a selected
    interface, and at most 10% of the selected surface area. This conservative
    project threshold is explicit; semantic region boundaries remain protected.
    The resulting charts must all remain disks and their count must fall by one.
    """
    mesh = snapshot.state.surface.mesh
    triangles = mesh.vertices[mesh.faces]
    areas = .5 * np.linalg.norm(np.cross(triangles[:, 1]-triangles[:, 0],
                                        triangles[:, 2]-triangles[:, 0]), axis=1)
    total_area = float(areas[faces].sum())
    semantic_boundaries = _semantic_boundary_edges(snapshot)
    refinements: list[dict[str, object]] = []
    while topology["all_disks"]:
        check_deadline(deadline)
        charts = cast(list[dict[str, object]], topology["charts"])
        owners = {face: i for i, chart in enumerate(charts) for face in cast(list[int], chart["faces"])}
        interfaces: dict[tuple[int, int], list[int]] = {}
        touching: set[int] = set()
        for edge in edges:
            entries = mesh.edge_faces[edge]
            if len(entries) != 2:
                continue
            a, b = entries[0][0], entries[1][0]
            if a not in owners or b not in owners or owners[a] == owners[b]:
                continue
            pair = (min(owners[a], owners[b]), max(owners[a], owners[b]))
            interfaces.setdefault(pair, []).append(edge)
            touching.update((a, b))
        accepted = False
        for owner, chart in enumerate(charts):
            members = cast(list[int], chart["faces"])
            boundaries = [pair for pair in interfaces if owner in pair]
            if len(boundaries) != 2 or not set(members) <= touching:
                continue
            if float(areas[members].sum()) > total_area * .1:
                continue
            for pair in sorted(boundaries, key=lambda p: (len(interfaces[p]), p)):
                removed = interfaces[pair]
                if semantic_boundaries.intersection(removed):
                    continue
                proposal = sorted(set(edges).difference(removed))
                candidate = cut_topology(snapshot, faces, proposal, deadline)
                if candidate["all_disks"] and int(str(candidate["chart_count"])) == len(charts)-1:
                    refinements.append({"kind": "thin_parallel_band", "removed": removed, "added": [],
                                        "band_faces": members, "maximum_area_fraction": .1})
                    edges, topology = proposal, candidate
                    accepted = True
                    break
            if accepted:
                break
        if not accepted:
            break
    return edges, topology, refinements


def submit_plan(request: dict[str, object], output: Path, deadline: float) -> dict[str, object]:
    snapshot = require_snapshot(request)
    index_hash: object = None
    if request.get("index") is not None:
        from .mesh_query import load_index
        connection, manifest = load_index(request, snapshot)
        connection.close()
        index_hash = manifest["index_hash"]
    edges, traces = _selection(request, snapshot, deadline)
    faces = _faces(request, snapshot)
    protected = set(_ids(request.get("protected_faces", []), len(snapshot.state.surface.mesh.faces)))
    if protected.intersection(faces):
        raise ValueError("SEAM_EDIT_REGION_INTERSECTS_PROTECTED_FACES")
    topology = cut_topology(snapshot, faces, edges, deadline)
    # A topologically safe local simplifier only removes a two-edge zigzag when
    # the third edge of its triangle shortens it and preserves every disk chart.
    before = edges.copy()
    refinements: list[dict[str, object]] = []
    if request.get("refine", True) and topology["all_disks"]:
        mesh = snapshot.state.surface.mesh
        semantic_boundaries = _semantic_boundary_edges(snapshot)
        lookup: dict[tuple[int, ...], int] = {edge: i for i, edge in enumerate(mesh.edges)}
        for face in faces:
            check_deadline(deadline)
            vertices = mesh.faces[face]
            tri = [lookup[tuple(sorted((int(vertices[i]), int(vertices[(i+1) % 3]))))] for i in range(3)]
            present = set(tri).intersection(edges)
            if len(present) != 2:
                continue
            if semantic_boundaries.intersection(present):
                continue
            missing = next(iter(set(tri)-present))
            old_length = sum(np.linalg.norm(mesh.vertices[mesh.edges[e][0]]-mesh.vertices[mesh.edges[e][1]]) for e in present)
            a, b = mesh.edges[missing]
            if np.linalg.norm(mesh.vertices[a]-mesh.vertices[b]) >= old_length * .95:
                continue
            proposal = sorted(set(edges).difference(present).union({missing}))
            candidate = cut_topology(snapshot, faces, proposal, deadline)
            if candidate["all_disks"] and candidate["chart_count"] == topology["chart_count"]:
                refinements.append({"removed": sorted(present), "added": [missing], "kind": "triangle_zigzag"})
                edges, topology = proposal, candidate
        edges, topology, band_refinements = _merge_thin_bands(snapshot, faces, edges, topology, deadline)
        refinements.extend(band_refinements)
    record: dict[str, object] = {
        "schema_version": 1, "asset_id": snapshot.asset_id, "state_hash": snapshot.state_hash,
        "faces": faces, "edge_ids": edges, "input_edge_ids": before, "traces": traces,
        "refinements": refinements, "topology": topology, "note": request.get("note", ""),
        "protected_faces": sorted(protected), "index": request.get("index"), "index_hash": index_hash,
        "parent_plan": request.get("parent_plan"), "geometry_modified": False,
        "index_bindings": [cast(dict[str, object], t["result"])["index_binding"]
                           for t in traces if "index_binding" in cast(dict[str, object], t["result"])],
    }
    record["plan_hash"] = object_hash(record)
    write_json(output / "plan.json", record)
    return {"plan": str(output / "plan.json"), "plan_hash": record["plan_hash"],
            "topology": topology, "edge_ids": edges, "uv_modified": False,
            "status": "OK" if topology["all_disks"] else "REJECTED",
            "execution_success": True, "reason": None if topology["all_disks"] else "NON_DISK_CHARTS"}


def apply_seams(request: dict[str, object], output: Path, deadline: float) -> dict[str, object]:
    from .geometry import inspect_uv
    from .reparameterize import reparameterize

    snapshot = require_snapshot(request)
    plan = _load_plan(request, snapshot)
    faces = _faces(plan, snapshot)
    edges = _ids(plan["edge_ids"], len(snapshot.state.surface.mesh.edges))
    topology = cut_topology(snapshot, faces, edges, deadline)
    if not topology["all_disks"]:
        return {"status": "REJECTED", "reason": "NON_DISK_CHARTS", "topology": topology}
    folder = output / "unwrap"
    folder.mkdir(parents=True, exist_ok=False)
    result = reparameterize({**request, "faces": faces,
                             "seam_edges": [list(snapshot.state.surface.mesh.edges[i]) for i in edges],
                             "backend": request.get("backend", "blender_lscm")}, folder, deadline)
    if result.get("status", "OK") != "OK":
        return result
    feedback_dir = output / "feedback"
    feedback_dir.mkdir()
    feedback = inspect_uv({**request, "snapshot": result["snapshot"], "state_hash": result["state_hash"]},
                          feedback_dir, deadline)
    result.update(plan_hash=plan["plan_hash"], feedback=feedback, requires_rebake=True)
    write_json(output / "application.json", result)
    return result


def manage_plan(request: dict[str, object], output: Path, deadline: float) -> dict[str, object]:
    snapshot = require_snapshot(request)
    check_deadline(deadline)
    action = request.get("action", "get")
    plan = _load_plan(request, snapshot)
    result: dict[str, object] = {"plan": plan, "snapshot": str(snapshot.root / "snapshot.json"),
                                 "state_hash": snapshot.state_hash, "uv_modified": False}
    if action == "compare":
        other = _load_plan({"plan": request["other_plan"]}, snapshot)
        a, b = set(cast(list[int], plan["edge_ids"])), set(cast(list[int], other["edge_ids"]))
        result.update(added=sorted(b-a), removed=sorted(a-b), other_plan=other)
    elif action not in ("get", "select"):
        raise ValueError("UNKNOWN_PLAN_ACTION")
    write_json(output / "plan_selection.json", result)
    return result
