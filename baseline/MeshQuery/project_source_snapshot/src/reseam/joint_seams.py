"""Packing-guided seam proposals, including inherited-shape splits and reversible recuts."""

import heapq
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import numpy as np
from shapely import Polygon, union_all

from .candidates import proposals
from .constraints import Constraints
from .fixed_uv import face_areas
from .initialization import initialize, orient_and_scale
from .joint_contacts import sheet_intersections
from .joint_types import V2State
from .mesh import DisjointSet, chart_labels, state_from_corner_uv
from .solver import solve, supports
from .types import Candidate, MeshState, Proposal
from .validation import validate


def remap_provenance(previous: V2State, surface: MeshState) -> tuple[int, ...]:
    classes = np.asarray(
        previous.contact_classes if previous.contact_classes is not None else range(len(previous.surface.uv))
    )
    old = classes[previous.surface.uv_faces].reshape(-1)
    new = surface.uv_faces.reshape(-1)
    dsu = DisjointSet(len(surface.uv))
    first = {}
    for origin, vertex in zip(old, new, strict=True):
        if int(origin) in first:
            dsu.union(first[int(origin)], int(vertex))
        else:
            first[int(origin)] = int(vertex)
    return tuple(dsu.find(i) for i in range(len(surface.uv)))


def split_proposals(state: MeshState, seed: int, limit: int = 16) -> Iterator[Proposal]:
    labels = chart_labels(state)
    scored = []
    for label in np.unique(labels):
        faces = np.flatnonzero(labels == label)
        if len(faces) < 12:
            continue
        shape = union_all([Polygon(state.uv[f]) for f in state.uv_faces[faces]])
        box = shape.bounds
        score = (box[2] - box[0]) * (box[3] - box[1]) / max(shape.area, 1e-15)
        scored.append((score, int(label), faces))
    scored.sort(key=lambda x: -x[0])
    rng = np.random.default_rng(seed)
    priority = min(len(scored), max(1, int(limit * 0.8)))
    selected = scored[:priority]
    remaining = scored[priority:]
    if remaining:
        selected += [
            remaining[int(i)]
            for i in rng.choice(len(remaining), min(limit - priority, len(remaining)), replace=False)
        ]
    for _, label, faces in selected[:limit]:
        boundary = set()
        graph: dict[int, list[tuple[int, int, float]]] = {}
        uv_to_original = {
            int(state.uv_faces[f, c]): int(state.mesh.faces[f, c]) for f in faces for c in range(3)
        }
        for ei, entries in enumerate(state.mesh.edge_faces):
            inside = [(f, c) for f, c in entries if labels[f] == label]
            if not inside:
                continue
            if len(inside) == 1 or ei in state.seams:
                for fi, corner in inside:
                    boundary.update(int(state.uv_faces[fi, c]) for c in (corner, (corner + 1) % 3))
            else:
                fi, corner = inside[0]
                a, b = int(state.uv_faces[fi, corner]), int(state.uv_faces[fi, (corner + 1) % 3])
                v, w = state.mesh.edges[ei]
                weight = float(np.linalg.norm(state.mesh.vertices[v] - state.mesh.vertices[w]))
                graph.setdefault(a, []).append((b, ei, weight))
                graph.setdefault(b, []).append((a, ei, weight))
        if len(boundary) < 4:
            continue
        vertices = np.asarray(sorted(boundary))
        uv = state.uv[vertices]
        center = uv.mean(axis=0)
        _, axes = np.linalg.eigh((uv - center).T @ (uv - center))
        coordinates = (uv - center) @ axes
        for fraction in (0.35, 0.5, 0.65):
            target = np.quantile(coordinates[:, 1], fraction)
            near = np.abs(coordinates[:, 1] - target) <= max(float(np.ptp(coordinates[:, 1])) * 0.15, 1e-8)
            options = np.flatnonzero(near)
            if len(options) < 2:
                continue
            start = int(vertices[options[np.argmin(coordinates[options, 0])]])
            finish = int(vertices[options[np.argmax(coordinates[options, 0])]])
            if start == finish:
                continue
            queue = [(0.0, start)]
            distance = {start: 0.0}
            predecessor = {}
            while queue:
                cost, vertex = heapq.heappop(queue)
                if cost != distance[vertex]:
                    continue
                if vertex == finish:
                    break
                for neighbor, edge, length in graph.get(vertex, []):
                    new = cost + length
                    if new < distance.get(neighbor, float("inf")):
                        distance[neighbor] = new
                        predecessor[neighbor] = (vertex, edge)
                        heapq.heappush(queue, (new, neighbor))
            if finish not in predecessor:
                continue
            edges = set()
            vertex = finish
            while vertex != start:
                vertex, edge = predecessor[vertex]
                edges.add(edge)
            if not edges or uv_to_original[start] == uv_to_original[finish]:
                continue
            seams = state.seams | frozenset(edges)
            topology = MeshState(state.mesh, seams, state.uv, state.uv_faces)
            if len(np.unique(chart_labels(topology))) <= len(np.unique(labels)):
                continue
            yield Proposal("packing_width_split", tuple(map(int, faces)), seams)


def seam_proposals(state: V2State, seed: int, deadline: float) -> Iterator[Proposal]:
    yield from split_proposals(state.surface, seed)
    yield from proposals(state.surface, Constraints(), seed, region_limit=12, deadline=deadline)


def seam_candidate(
    base: V2State, proposal: Proposal, runtime: dict[str, Any], folder: Path, seconds: float
) -> tuple[V2State, dict[str, Any]]:
    start = time.monotonic()
    deadline = start + seconds
    if proposal.error:
        raise ValueError(proposal.error)
    affected = set(proposal.region_faces)
    released = [g for g in base.groups if affected.intersection(g.first_faces + g.second_faces)]
    for g in released:
        affected.update(g.first_faces + g.second_faces)
    remaining = tuple(g for g in base.groups if g not in released)
    source = base.surface.copy()
    for g in released:
        for faces, orientation in (
            (g.first_faces, g.first_orientation),
            (g.second_faces, g.second_orientation),
        ):
            if orientation < 0:
                ids = np.unique(source.uv_faces[list(faces)])
                source.uv[ids, 0] *= -1
    if proposal.kind == "packing_width_split" and proposal.seams >= source.seams:
        surface = state_from_corner_uv(source.mesh, source.uv[source.uv_faces], seam_ids=proposal.seams)
        phases = []
    else:
        native = orient_and_scale(source)
        native_scale = float(np.sqrt(np.abs(face_areas(native)).sum() / np.abs(face_areas(source)).sum()))

        def quality_check(trial: MeshState) -> dict[str, Any]:
            q = validate(trial, tau=None, intersections=False)
            if q["valid"]:
                q["errors"] = sheet_intersections(trial, chart_labels(trial), remap_provenance(base, trial))
                q["valid"] = not q["errors"]
            return q

        initialized = initialize(
            native.mesh,
            proposal.seams,
            inherited=native,
            affected_faces=frozenset(affected),
            quality_check=quality_check,
        )
        changed = frozenset(v for ei in source.seams ^ proposal.seams for v in source.mesh.edges[ei])
        candidate = Candidate(
            "seam",
            proposal.kind,
            tuple(sorted(affected)),
            source.seams - proposal.seams,
            proposal.seams - source.seams,
            initialized,
            base.version,
            changed,
        )
        best = initialized
        phases = []
        for level, free in enumerate(supports(candidate)):
            remaining_seconds = deadline - time.monotonic() - 1
            if remaining_seconds < 2:
                break
            trial = best.copy()
            fixed = sorted(set(range(len(trial.uv))) - free)
            trial.uv[fixed] = initialized.uv[fixed]
            solved = solve(
                trial,
                free,
                runtime,
                folder / f"support_{level}",
                remaining_seconds / (3 - level),
                iterations=(20, 40, 100)[level],
                quality_check=quality_check,
            )
            phases.append({"level": level, "status": solved.status, "seconds": solved.seconds})
            if solved.state is not None:
                best = solved.state
        surface = best.copy()
        surface.uv /= native_scale
        for group in remaining:
            faces = list(group.first_faces + group.second_faces)
            surface.uv[surface.uv_faces[faces].reshape(-1)] = base.surface.uv[
                base.surface.uv_faces[faces]
            ].reshape(-1, 2)
    active_faces = {f for g in remaining for f in g.first_faces + g.second_faces}
    correspondences = tuple(c for c in base.correspondences if c.source_face in active_faces)
    result = V2State(surface, remaining, correspondences, base.version, {}, remap_provenance(base, surface))
    return result, {
        "kind": proposal.kind,
        "added": sorted(proposal.seams - base.surface.seams),
        "removed": sorted(base.surface.seams - proposal.seams),
        "released_groups": [g.group_id for g in released],
        "supports": phases,
        "seconds": time.monotonic() - start,
    }
