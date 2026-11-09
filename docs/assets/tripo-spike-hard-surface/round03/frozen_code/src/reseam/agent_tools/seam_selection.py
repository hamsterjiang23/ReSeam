"""MeshQuery-style edge operators on immutable original mesh identities.

The anisotropic operator is an edge-snapped short-term-vector discretization;
it does not claim binary equivalence to the unpublished MeshQuery implementation.
"""

from __future__ import annotations

import heapq
import math
from itertools import pairwise
from pathlib import Path
from typing import cast

import numpy as np

from ..types import FloatArray, Mesh
from .state import Snapshot, check_deadline, require_snapshot, write_json

OPERATORS = (
    "topological_loop", "boundary_loop", "region_boundary", "thin_loop",
    "semantic_interface", "seamcut_seam", "crease_select", "find_loop",
    "find_extremities", "geodesic",
)


def _ids(value: object, count: int, name: str, *, empty: bool = False) -> list[int]:
    if (not isinstance(value, list) or any(type(x) is not int for x in value)
            or len(set(value)) != len(value) or (not value and not empty)):
        raise ValueError(f"{name}_UNIQUE_INTEGER_IDS_REQUIRED")
    result = cast(list[int], value)
    if result and (min(result) < 0 or max(result) >= count):
        raise ValueError(f"{name}_ID_OUT_OF_RANGE")
    return result


def _positive(value: object, name: str, *, zero: bool = False) -> float:
    if isinstance(value, bool):
        raise TypeError(f"{name}_FINITE_NUMBER_REQUIRED")
    result = float(str(value))
    if not math.isfinite(result) or result < 0 or (not zero and result == 0):
        raise ValueError(f"{name}_FINITE_NUMBER_REQUIRED")
    return result


def _geometry(mesh: Mesh) -> tuple[FloatArray, FloatArray, FloatArray]:
    tri = mesh.vertices[mesh.faces]
    normals = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
    norms = np.linalg.norm(normals, axis=1)
    normals /= np.maximum(norms[:, None], 1e-30)
    angles = np.zeros(len(mesh.edges))
    for i, entries in enumerate(mesh.edge_faces):
        if len(entries) == 2:
            angles[i] = np.arccos(np.clip(normals[entries[0][0]] @ normals[entries[1][0]], -1, 1))
    midpoints = mesh.vertices[np.asarray(mesh.edges)].mean(axis=1)
    return normals, angles, midpoints


def _vertex_edges(mesh: Mesh) -> list[list[int]]:
    result: list[list[int]] = [[] for _ in mesh.vertices]
    for i, (a, b) in enumerate(mesh.edges):
        result[a].append(i)
        result[b].append(i)
    return result


def _chains(mesh: Mesh, edges: list[int]) -> list[dict[str, object]]:
    """Connected edge sets with explicit branch/cycle evidence, never reordered IDs."""
    incident: dict[int, list[int]] = {}
    for edge in edges:
        for v in mesh.edges[edge]:
            incident.setdefault(v, []).append(edge)
    remaining = set(edges)
    result: list[dict[str, object]] = []
    while remaining:
        seed = min(remaining)
        remaining.remove(seed)
        group, pending = [seed], [seed]
        while pending:
            for v in mesh.edges[pending.pop()]:
                for other in incident[v]:
                    if other in remaining:
                        remaining.remove(other)
                        group.append(other)
                        pending.append(other)
        vertices = {v for edge in group for v in mesh.edges[edge]}
        degrees = {v: len(incident[v]) for v in vertices}
        result.append({"edge_ids": sorted(group), "closed": all(n == 2 for n in degrees.values()),
                       "branch_vertices": sorted(v for v, n in degrees.items() if n > 2),
                       "end_vertices": sorted(v for v, n in degrees.items() if n == 1)})
    return sorted(result, key=lambda x: (-len(cast(list[int], x["edge_ids"])),
                                        cast(list[int], x["edge_ids"])))


def _boundary(mesh: Mesh, faces: set[int], mesh_only: bool = False) -> list[int]:
    return [i for i, entries in enumerate(mesh.edge_faces)
            if sum(f in faces for f, _ in entries) == 1 and (not mesh_only or len(entries) == 1)]


def _face_components(mesh: Mesh, faces: set[int], blocked: set[int] | None = None) -> list[set[int]]:
    neighbors: dict[int, list[int]] = {f: [] for f in faces}
    for i, entries in enumerate(mesh.edge_faces):
        if blocked and i in blocked:
            continue
        if len(entries) == 2:
            a, b = entries[0][0], entries[1][0]
            if a in faces and b in faces:
                neighbors[a].append(b)
                neighbors[b].append(a)
    remaining = set(faces)
    result: list[set[int]] = []
    while remaining:
        seed = min(remaining)
        remaining.remove(seed)
        group, pending = {seed}, [seed]
        while pending:
            for other in neighbors[pending.pop()]:
                if other in remaining:
                    remaining.remove(other)
                    group.add(other)
                    pending.append(other)
        result.append(group)
    return sorted(result, key=lambda x: (-len(x), min(x)))


def _quad_topology(snapshot: Snapshot) -> tuple[dict[int, set[int]], dict[int, list[int]]]:
    mesh = snapshot.state.surface.mesh
    raw = snapshot.metadata.get("polygon_topology")
    if not isinstance(raw, dict):
        raise TypeError("QUAD_PROVENANCE_REQUIRED_USE_REGION_BOUNDARY_OR_GEODESIC")
    polygons = raw.get("polygons")
    mapping = raw.get("triangle_to_polygon")
    if not isinstance(polygons, list) or not isinstance(mapping, list) or len(mapping) != len(mesh.faces):
        raise ValueError("POLYGON_PROVENANCE_INVALID")
    if not any(isinstance(p, list) and len(p) == 4 for p in polygons):
        raise ValueError("QUAD_PROVENANCE_REQUIRED_USE_REGION_BOUNDARY_OR_GEODESIC")
    edge_lookup = {edge: i for i, edge in enumerate(mesh.edges)}
    memberships: dict[int, set[int]] = {}
    incident: dict[int, list[int]] = {}
    for p, polygon in enumerate(polygons):
        vertices = _ids(polygon, len(mesh.vertices), "POLYGON")
        if len(vertices) < 3:
            raise ValueError("POLYGON_TOO_SMALL")
        face_ids = {i for i, parent in enumerate(mapping) if type(parent) is int and parent == p}
        if not face_ids or any(not set(map(int, mesh.faces[f])) <= set(vertices) for f in face_ids):
            raise ValueError("POLYGON_TRIANGLE_MAPPING_INVALID")
        perimeter = {(min(a, b), max(a, b)) for a, b in zip(vertices, vertices[1:] + vertices[:1], strict=True)}
        if {mesh.edges[e] for e in _boundary(mesh, face_ids)} != perimeter:
            raise ValueError("POLYGON_TRIANGULATION_BOUNDARY_MISMATCH")
        for edge in perimeter:
            if edge not in edge_lookup:
                raise ValueError("POLYGON_EDGE_MISSING")
            e = edge_lookup[edge]
            memberships.setdefault(e, set()).add(p)
            for v in edge:
                if e not in incident.setdefault(v, []):
                    incident[v].append(e)
    if any(type(p) is not int or p < 0 or p >= len(polygons) for p in mapping):
        raise ValueError("POLYGON_TRIANGLE_MAPPING_INVALID")
    return memberships, incident


def _topological(snapshot: Snapshot, seed: int) -> list[int]:
    memberships, incident = _quad_topology(snapshot)
    mesh = snapshot.state.surface.mesh
    if seed not in memberships:
        raise ValueError("SEED_IS_TRIANGULATION_DIAGONAL")
    result = {seed}
    for start in mesh.edges[seed]:
        edge, vertex = seed, start
        visited: set[tuple[int, int]] = set()
        while (edge, vertex) not in visited:
            visited.add((edge, vertex))
            local = incident[vertex]
            if len(local) != 4 or any(len(memberships[e]) != 2 for e in local):
                break
            polygons = cast(dict[str, object], snapshot.metadata["polygon_topology"])["polygons"]
            if any(len(cast(list[list[int]], polygons)[p]) != 4 for e in local for p in memberships[e]):
                break
            opposite = [e for e in local if e != edge and not memberships[e] & memberships[edge]]
            if len(opposite) != 1:
                break
            edge = opposite[0]
            result.add(edge)
            a, b = mesh.edges[edge]
            vertex = b if a == vertex else a
    return sorted(result)


def _thin(mesh: Mesh, faces: set[int], deadline: float) -> tuple[list[int], dict[str, object]]:
    normals, angles, _ = _geometry(mesh)
    if len(_face_components(mesh, faces)) != 1:
        raise ValueError("THIN_LOOP_REQUIRES_ONE_CONNECTED_REGION")
    partitions: list[tuple[set[int], str]] = []
    for threshold in np.linspace(0., np.pi, 33):
        check_deadline(deadline)
        components = _face_components(mesh, faces, set(np.flatnonzero(angles > threshold).tolist()))
        if len(components) == 2:
            partitions.append((components[0], f"fold_threshold_{float(threshold):.9g}"))
    vertices = np.unique(mesh.faces[sorted(faces)])
    points = mesh.vertices[vertices]
    _, _, axes = np.linalg.svd(points - points.mean(axis=0), full_matrices=False)
    front = {f for f in faces if normals[f] @ axes[-1] >= 0}
    if front and front != faces:
        partitions.append((front, "pca_normal_sign"))
    candidates: list[tuple[float, list[int], str, set[int]]] = []
    for side, method in partitions:
        other = faces - side
        if len(_face_components(mesh, side)) != 1 or len(_face_components(mesh, other)) != 1:
            continue
        edges = [e for e, entries in enumerate(mesh.edge_faces) if len(entries) == 2
                 and {entries[0][0], entries[1][0]} <= faces
                 and ((entries[0][0] in side) != (entries[1][0] in side))]
        chains = _chains(mesh, edges)
        if len(chains) != 1 or not chains[0]["closed"]:
            continue
        a = float(mesh.areas[list(side)].sum())
        b = float(mesh.areas[list(other)].sum())
        balance = min(a, b) / max(a, b, 1e-30)
        candidates.append((balance, edges, method, side))
    if not candidates:
        raise ValueError("THIN_LOOP_NO_SINGLE_CLOSED_TWO_SIDED_SPLIT")
    balance, edges, method, side = max(candidates, key=lambda x: (x[0], -len(x[1]), x[2]))
    return sorted(edges), {"method": method, "area_balance": balance, "splits_tested": 34,
                           "first_faces": sorted(side), "second_faces": sorted(faces - side)}


def _path(
    mesh: Mesh, start: int, end: int, allowed: set[int], angles: FloatArray,
    midpoints: FloatArray, deadline: float, beta: float = 0., turn_weight: float = .05,
) -> tuple[list[int], float]:
    """A* over edge adjacency; anisotropic search retains three-edge direction history."""
    if start not in allowed or end not in allowed:
        raise ValueError("WAYPOINT_OUTSIDE_ALLOWED_EDGES")
    incident = _vertex_edges(mesh)
    # Cost lower bound includes the maximal possible crease discount.
    def heuristic(edge: int) -> float:
        return float(np.linalg.norm(midpoints[edge] - midpoints[end])) / (1. + beta)

    history_size = 3 if beta > 0 else 2
    initial = (start,)
    costs: dict[tuple[int, ...], float] = {initial: 0.}
    parents: dict[tuple[int, ...], tuple[int, ...]] = {}
    queue: list[tuple[float, float, tuple[int, ...]]] = [(heuristic(start), 0., initial)]
    final: tuple[int, ...] | None = None
    steps = 0
    while queue:
        steps += 1
        if steps % 128 == 0:
            check_deadline(deadline)
        _, cost, state = heapq.heappop(queue)
        if cost != costs[state]:
            continue
        current = state[-1]
        if current == end:
            final = state
            break
        neighbors = sorted({e for v in mesh.edges[current] for e in incident[v]} & allowed)
        for nxt in neighbors:
            if nxt in state:
                continue
            if len(state) > 1:
                entered = set(mesh.edges[state[-2]]) & set(mesh.edges[current])
                leaving = set(mesh.edges[nxt]) & set(mesh.edges[current])
                if entered == leaving:
                    continue
            delta = midpoints[nxt] - midpoints[current]
            length = float(np.linalg.norm(delta))
            if length <= 1e-30:
                # Zero-length mesh adjacency is not an identity merge.
                length = max(mesh.scale * 1e-12, 1e-15)
            turn = 0.
            if len(state) > 1:
                past = midpoints[current] - midpoints[state[0]]
                turn = 1. - float(np.clip(past @ delta / max(np.linalg.norm(past)*length, 1e-30), -1, 1))
            # High dihedral edge directions define a local curvature ridge tensor.
            ridge_strength = float((angles[current] + angles[nxt]) / (2*np.pi))
            anisotropy = 1.
            if beta > 0:
                vicinity = set(neighbors) | {current}
                tensor = np.zeros((3, 3))
                for edge in vicinity:
                    a, b = mesh.edges[edge]
                    vector = mesh.vertices[b] - mesh.vertices[a]
                    norm = float(np.linalg.norm(vector))
                    if norm > 1e-30:
                        tensor += angles[edge] * np.outer(vector / norm, vector / norm)
                values, axes = np.linalg.eigh(tensor)
                alignment = float((delta @ axes[:, -1] / length)**2) if values[-1] > 1e-12 else 1.
                anisotropy = (1. + beta * (1. - alignment)) / (1. + beta * ridge_strength)
            candidate = cost + length * (anisotropy + turn_weight * turn)
            next_state = (*state, nxt)[-history_size:]
            if candidate < costs.get(next_state, math.inf):
                costs[next_state] = candidate
                parents[next_state] = state
                heapq.heappush(queue, (candidate + heuristic(nxt), candidate, next_state))
    check_deadline(deadline)
    if final is None:
        raise ValueError("NO_CONNECTED_EDGE_PATH")
    reverse = [final[-1]]
    state = final
    while state != initial:
        state = parents[state]
        reverse.append(state[-1])
    return reverse[::-1], costs[final]


def _extremities(mesh: Mesh, faces: set[int], deadline: float, fraction: float) -> list[dict[str, object]]:
    result: list[dict[str, object]] = []
    incident = _vertex_edges(mesh)
    for component in _face_components(mesh, faces):
        check_deadline(deadline)
        vertices = set(map(int, np.unique(mesh.faces[sorted(component)])))
        center = mesh.vertices[sorted(vertices)].mean(axis=0)
        root = min(vertices, key=lambda v: (float(np.linalg.norm(mesh.vertices[v] - center)), v))
        distances = {v: math.inf for v in vertices}
        distances[root] = 0.
        queue = [(0., root)]
        neighbors: dict[int, set[int]] = {}
        for v in vertices:
            neighbors[v] = {w for e in incident[v] for w in mesh.edges[e] if w in vertices and w != v}
        steps = 0
        while queue:
            steps += 1
            if steps % 128 == 0:
                check_deadline(deadline)
            dist, v = heapq.heappop(queue)
            if dist != distances[v]:
                continue
            for w in neighbors[v]:
                cost = dist + float(np.linalg.norm(mesh.vertices[w] - mesh.vertices[v]))
                if cost < distances[w]:
                    distances[w] = cost
                    heapq.heappush(queue, (cost, w))
        peaks = [v for v in vertices if all(distances[v] >= distances[w] for w in neighbors[v])]
        for peak in sorted(peaks, key=lambda v: (-distances[v], v)):
            threshold = distances[peak] * (1. - fraction)
            cap_vertices = {peak}
            pending = [peak]
            while pending:
                for w in neighbors[pending.pop()]:
                    if distances[w] >= threshold and w not in cap_vertices:
                        pending.append(w)
                        cap_vertices.add(w)
            cap = {f for f in component if all(int(v) in cap_vertices for v in mesh.faces[f])}
            if not cap or cap == component:
                continue
            edges = _boundary(mesh, cap)
            if edges and not any(set(edges) == set(cast(list[int], c["edge_ids"])) for c in result):
                result.append({"edge_ids": edges, "face_ids": sorted(cap), "tip_vertex": peak,
                               "surface_distance": distances[peak], "center_vertex": root})
    return result


def select_edges(snapshot: Snapshot, operator: str, arguments: dict[str, object], deadline: float) -> dict[str, object]:
    """Return original-edge selections; never edit the snapshot or its metadata."""
    check_deadline(deadline)
    if operator not in OPERATORS:
        raise ValueError("UNKNOWN_SEAM_OPERATOR:" + operator)
    mesh = snapshot.state.surface.mesh
    arguments = dict(arguments)
    for entity in ("edge", "face"):
        query = arguments.get(f"{entity}_query")
        if query is not None:
            from .mesh_query import query_selection
            if not isinstance(query, str) or not isinstance(arguments.get("index"), str):
                raise ValueError("QUERY_REQUIRES_SQL_AND_INDEX_PATH")
            if f"{entity}_ids" in arguments:
                raise ValueError("QUERY_AND_DIRECT_SELECTION_ARE_EXCLUSIVE")
            selection = query_selection(snapshot, str(arguments["index"]), query, deadline)
            if selection["entity"] != entity:
                raise ValueError("SQL_SELECTION_ENTITY_MISMATCH")
            arguments[f"{entity}_ids"] = selection["ids"]
    edges = _ids(arguments.get("edge_ids", list(range(len(mesh.edges)))), len(mesh.edges), "EDGE")
    faces = set(_ids(arguments.get("face_ids", list(range(len(mesh.faces)))), len(mesh.faces), "FACE"))
    diagnostics: dict[str, object] = {}
    candidates: list[dict[str, object]] = []
    chosen: list[int]
    if operator in ("boundary_loop", "region_boundary"):
        chosen = _boundary(mesh, faces, mesh_only=operator == "boundary_loop")
    elif operator == "topological_loop":
        chosen = sorted({e for seed in edges for e in _topological(snapshot, seed)})
    elif operator == "thin_loop":
        if "face_ids" not in arguments and "edge_ids" in arguments:
            touching = {f for e in edges for f, _ in mesh.edge_faces[e]}
            components = _face_components(mesh, faces)
            relevant = [c for c in components if c & touching]
            faces = set().union(*relevant)
        chosen, diagnostics = _thin(mesh, faces, deadline)
    elif operator == "crease_select":
        _, angles, _ = _geometry(mesh)
        angle = arguments.get("angle_degrees")
        if angle is None:
            # Use rank on the whole mesh, not the candidate subset; flat meshes select nothing.
            _, inverse = np.unique(angles, return_inverse=True)
            counts = np.bincount(inverse)
            minimum_rank = np.r_[0, np.cumsum(counts)[:-1]][inverse] / max(1, len(angles)-1)
            chosen = [e for e in edges if minimum_rank[e] >= .9 and angles[e] > 0]
            diagnostics["threshold"] = "global_min_rank>=0.9"
        else:
            threshold = math.radians(_positive(angle, "ANGLE", zero=True))
            if threshold > np.pi:
                raise ValueError("ANGLE_EXCEEDS_180")
            chosen = [e for e in edges if angles[e] >= threshold and len(mesh.edge_faces[e]) == 2]
            diagnostics["angle_degrees"] = float(str(angle))
    elif operator == "semantic_interface":
        first, second = arguments.get("first_label"), arguments.get("second_label")
        if not isinstance(first, str) or not isinstance(second, str) or first == second:
            raise ValueError("TWO_DISTINCT_SEMANTIC_LABELS_REQUIRED")
        labels: dict[int, set[str]] = {f: set() for f in faces}
        for region in cast(list[dict[str, object]], snapshot.metadata.get("regions", [])):
            for f in cast(list[int], region["faces"]):
                if f in labels:
                    labels[f].add(str(region["name"]))
        chosen = [e for e in edges if len(mesh.edge_faces[e]) == 2
                  and ((first in labels.get(mesh.edge_faces[e][0][0], set())
                        and second in labels.get(mesh.edge_faces[e][1][0], set()))
                       or (second in labels.get(mesh.edge_faces[e][0][0], set())
                           and first in labels.get(mesh.edge_faces[e][1][0], set())))]
    elif operator == "find_loop":
        _quad_topology(snapshot)
        _, _, midpoints = _geometry(mesh)
        seeds = [min(edges)]
        while len(seeds) < min(24, len(edges)):
            distances = np.min(np.linalg.norm(midpoints[edges, None] - midpoints[seeds][None], axis=2), axis=1)
            remaining = [(float(distances[i]), -e, e) for i, e in enumerate(edges) if e not in seeds]
            seeds.append(max(remaining)[2])
        seen: set[tuple[int, ...]] = set()
        for seed in seeds:
            check_deadline(deadline)
            try:
                selected = _topological(snapshot, seed)
            except ValueError as error:
                if str(error) == "SEED_IS_TRIANGULATION_DIAGONAL":
                    continue
                raise
            key = tuple(selected)
            if key in seen or len(selected) < 3:
                continue
            seen.add(key)
            points = midpoints[selected]
            _, singular, _ = np.linalg.svd(points - points.mean(axis=0), full_matrices=False)
            flatness = float(singular[-1] / max(singular[0], 1e-30))
            candidates.append({"edge_ids": selected, "seed_edge": seed, "flatness": flatness,
                               "coverage": len(set(selected) & set(edges)) / len(edges)})
        candidates.sort(key=lambda c: (-float(str(c["coverage"])), float(str(c["flatness"]))))
        chosen = cast(list[int], candidates[0]["edge_ids"]) if candidates else []
        diagnostics["seed_count"] = len(seeds)
    elif operator == "find_extremities":
        fraction = _positive(arguments.get("cap_fraction", .3), "CAP_FRACTION")
        if fraction >= 1:
            raise ValueError("CAP_FRACTION_MUST_BE_LESS_THAN_ONE")
        if "face_ids" not in arguments and "edge_ids" in arguments:
            touching = {f for e in edges for f, _ in mesh.edge_faces[e]}
            faces = set().union(*(c for c in _face_components(mesh, faces) if c & touching))
        candidates = _extremities(mesh, faces, deadline, fraction)
        chosen = cast(list[int], candidates[0]["edge_ids"]) if candidates else []
    else:
        waypoints = _ids(arguments.get("waypoint_edges"), len(mesh.edges), "WAYPOINT")
        if not 2 <= len(waypoints) <= 8:
            raise ValueError("TWO_TO_EIGHT_ORDERED_WAYPOINTS_REQUIRED")
        _, angles, midpoints = _geometry(mesh)
        beta = _positive(arguments.get("beta", 4.), "BETA") if operator == "seamcut_seam" else 0.
        turn = _positive(arguments.get("turn_weight", .05), "TURN_WEIGHT", zero=True)
        chosen = []
        segments: list[dict[str, object]] = []
        allowed = set(edges)
        if "face_ids" in arguments:
            allowed &= {e for e, entries in enumerate(mesh.edge_faces) if any(f in faces for f, _ in entries)}
        for a, b in pairwise(waypoints):
            path, cost = _path(mesh, a, b, allowed, angles, midpoints, deadline, beta, turn)
            chosen.extend(path)
            segments.append({"ordered_edge_ids": path, "cost": cost})
        diagnostics = {"segments": segments, "beta": beta, "turn_weight": turn,
                       "lookback": 3 if beta else 2,
                       "discretization": "original_edge_midpoint_graph",
                       "metric": "curvature_ridge_anisotropic" if beta else "length_plus_turn"}
    check_deadline(deadline)
    chosen = sorted(set(chosen))
    diagnostics["chains"] = _chains(mesh, chosen)
    return {"operator": operator, "edge_ids": chosen, "edges": [list(mesh.edges[e]) for e in chosen],
            "edge_count": len(chosen), "diagnostics": diagnostics, "candidates": candidates,
            "asset_id": snapshot.asset_id, "state_hash": snapshot.state_hash,
            "geometry_modified": False, "uv_modified": False}


def select_seam(request: dict[str, object], output: Path, deadline: float) -> dict[str, object]:
    snapshot = require_snapshot(request)
    operator = request.get("operator")
    arguments = request.get("arguments", {})
    if not isinstance(operator, str) or not isinstance(arguments, dict):
        raise TypeError("OPERATOR_AND_ARGUMENT_OBJECT_REQUIRED")
    result = select_edges(snapshot, operator, arguments, deadline)
    output.mkdir(parents=True, exist_ok=True)
    write_json(output / "selection.json", result)
    return {**result, "selection": str((output / "selection.json").resolve())}
