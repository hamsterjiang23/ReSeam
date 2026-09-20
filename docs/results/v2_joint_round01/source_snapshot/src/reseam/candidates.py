"""Bounded regional proposals on original mesh adjacency, never UV proximity."""

import heapq
import time
from collections import defaultdict
from collections.abc import Iterator

import numpy as np

from .constraints import Constraints
from .initialization import initialize
from .mesh import DisjointSet, chart_labels, corner_map
from .types import Candidate, MeshState, Proposal


def regions(state: MeshState, seed: int, limit: int = 40) -> list[tuple[int, ...]]:
    labels = chart_labels(state)
    adjacency: dict[int, set[int]] = defaultdict(set)
    shared: dict[tuple[int, int], float] = defaultdict(float)
    for ei in sorted(state.seams):
        pair = state.mesh.edge_faces[ei]
        if len(pair) != 2:
            continue
        a, b = int(labels[pair[0][0]]), int(labels[pair[1][0]])
        if a == b:
            continue
        adjacency[a].add(b)
        adjacency[b].add(a)
        v0, v1 = state.mesh.edges[ei]
        shared[(min(a, b), max(a, b))] += float(
            np.linalg.norm(state.mesh.vertices[v0] - state.mesh.vertices[v1])
        )
    areas = np.bincount(labels, weights=state.mesh.areas) / state.mesh.areas.sum()
    combinations: set[tuple[int, ...]] = set()
    for pair in shared:
        combinations.add(pair)
        current = set(pair)
        for _ in range(2):
            frontier = set().union(*(adjacency[x] for x in current)) - current
            if not frontier:
                break
            chosen = min(frontier, key=lambda x: (areas[x], x))
            current.add(chosen)
            combinations.add(tuple(sorted(current)))
    ordered = sorted(
        combinations,
        key=lambda group: (
            -sum(1 for x in group if areas[x] < 0.005),
            -sum(value for pair, value in shared.items() if set(pair) <= set(group)),
            group,
        ),
    )
    rng = np.random.default_rng(seed)
    priority_count = min(len(ordered), max(1, int(limit * 0.8)))
    selected = ordered[:priority_count]
    remainder = ordered[priority_count:]
    if remainder:
        chosen_indices = rng.choice(len(remainder), min(limit - len(selected), len(remainder)), replace=False)
        selected.extend(remainder[int(i)] for i in chosen_indices)
    # Non-separating cuts can exist within one chart; expose complete-chart regions too.
    singleton = sorted(
        {
            int(labels[pair[0][0]])
            for ei, pair in enumerate(state.mesh.edge_faces)
            if ei in state.seams and len(pair) == 2 and labels[pair[0][0]] == labels[pair[1][0]]
        }
    )
    return [
        tuple(map(int, np.flatnonzero(np.isin(labels, group))))
        for group in selected + [(label,) for label in singleton]
    ]


def shortest_path(
    state: MeshState, start: int, finish: int, allowed: set[int], excluded: set[int], constraints: Constraints
) -> set[int] | None:
    graph: dict[int, list[tuple[int, int, float]]] = defaultdict(list)
    for ei in sorted(allowed - excluded - set(constraints.must_join)):
        a, b = state.mesh.edges[ei]
        cost = float(
            np.linalg.norm(state.mesh.vertices[a] - state.mesh.vertices[b])
        ) * constraints.weights.get(ei, 1)
        graph[a].append((b, ei, cost))
        graph[b].append((a, ei, cost))
    queue = [(0.0, start)]
    distance = {start: 0.0}
    previous: dict[int, tuple[int, int]] = {}
    while queue:
        value, vertex = heapq.heappop(queue)
        if value != distance[vertex]:
            continue
        if vertex == finish:
            edges = set()
            while vertex != start:
                vertex, edge = previous[vertex]
                edges.add(edge)
            return edges
        for neighbor, edge, cost in graph[vertex]:
            candidate = value + cost
            if candidate < distance.get(neighbor, float("inf")):
                distance[neighbor] = candidate
                previous[neighbor] = (vertex, edge)
                heapq.heappush(queue, (candidate, neighbor))
    return None


def seam_chains(state: MeshState, edge_ids: set[int]) -> list[set[int]]:
    incident: dict[int, list[int]] = defaultdict(list)
    for ei in sorted(edge_ids):
        for vertex in state.mesh.edges[ei]:
            incident[vertex].append(ei)
    unused = set(edge_ids)
    chains: list[set[int]] = []
    while unused:
        endpoints = [(v, ei) for ei in sorted(unused) for v in state.mesh.edges[ei] if len(incident[v]) != 2]
        if endpoints:
            start, edge = min(endpoints)
        else:
            edge = min(unused)
            start = state.mesh.edges[edge][0]
        chain = set()
        vertex = start
        while edge in unused:
            unused.remove(edge)
            chain.add(edge)
            pair = state.mesh.edges[edge]
            vertex = pair[1] if pair[0] == vertex else pair[0]
            if len(incident[vertex]) != 2 or vertex == start:
                break
            options = [e for e in incident[vertex] if e in unused]
            if not options:
                break
            edge = options[0]
        chains.append(chain)
    return chains


def proposals(
    state: MeshState,
    constraints: Constraints,
    seed: int,
    region_limit: int = 40,
    deadline: float | None = None,
) -> Iterator[Proposal]:
    seen: set[frozenset[int]] = {state.seams}
    rng = np.random.default_rng(seed)
    for region in regions(state, seed, region_limit):
        if deadline is not None and time.monotonic() >= deadline:
            yield Proposal("region_generation", region, state.seams, "PROPOSAL_BUDGET_EXHAUSTED")
            return
        face_set = set(region)
        interior = {
            ei
            for ei, pair in enumerate(state.mesh.edge_faces)
            if len(pair) == 2 and all(fi in face_set for fi, _ in pair)
        }
        cuts = set(state.seams) & interior
        variants: list[tuple[str, set[int]]] = []
        removable = cuts - set(constraints.must_cut)
        variants.append(("merge_recut", set(state.seams) - removable))
        for chain in seam_chains(state, removable):
            variants.append(("merge_recut_segment", set(state.seams) - chain))
            degrees: dict[int, int] = defaultdict(int)
            for ei in chain:
                for v in state.mesh.edges[ei]:
                    degrees[v] += 1
            ends = sorted(v for v, degree in degrees.items() if degree == 1)
            if len(ends) == 2:
                replacement = shortest_path(state, ends[0], ends[1], interior, chain, constraints)
                if replacement:
                    variants.append(("path_replace", (set(state.seams) - chain) | replacement))
        # Conservative dual spanning tree is an explicit separate proposal, not a hidden oracle.
        dsu = DisjointSet(len(state.mesh.faces))
        joined: set[int] = set()
        required_joins = sorted(interior & set(constraints.must_join))
        possible = sorted(interior - set(constraints.must_cut) - set(required_joins))
        rng.shuffle(possible)
        for ei in required_joins + possible:
            pair = state.mesh.edge_faces[ei]
            if dsu.union(pair[0][0], pair[1][0]) or ei in constraints.must_join:
                joined.add(ei)
        variants.append(("dual_tree_rebuild", (set(state.seams) - interior) | (interior - joined)))
        for kind, seams in variants:
            if deadline is not None and time.monotonic() >= deadline:
                yield Proposal(kind, region, frozenset(seams), "PROPOSAL_BUDGET_EXHAUSTED")
                return
            if kind.startswith("merge_recut"):
                try:
                    seams = set(bridge_holes(state, frozenset(seams), region, constraints, deadline))
                except (ValueError, TimeoutError) as error:
                    yield Proposal(kind, region, frozenset(seams), str(error))
                    if isinstance(error, TimeoutError):
                        return
                    continue
            frozen = frozenset(seams)
            if frozen in seen or not constraints.must_cut <= frozen or constraints.must_join & frozen:
                continue
            seen.add(frozen)
            yield Proposal(kind, region, frozen)


def bridge_holes(
    state: MeshState,
    seams: frozenset[int],
    region: tuple[int, ...],
    constraints: Constraints,
    deadline: float | None = None,
) -> frozenset[int]:
    """Join boundary loops by shortest admissible interior edge paths before disk initialization."""
    mesh = state.mesh
    edge_lookup = {edge: index for index, edge in enumerate(mesh.edges)}
    current = seams
    region_set = set(region)
    for _ in range(16):
        if deadline is not None and time.monotonic() >= deadline:
            raise TimeoutError("PROPOSAL_BUDGET_EXHAUSTED")
        mapping = corner_map(mesh, current)
        trial = MeshState(mesh, current, np.zeros((int(mapping.max()) + 1, 2)), mapping)
        labels = chart_labels(trial)
        added: set[int] | None = None
        for label in np.unique(labels[list(region)]):
            face_ids = [int(fi) for fi in np.flatnonzero(labels == label) if int(fi) in region_set]
            uv_edges: dict[tuple[int, int], list[int]] = defaultdict(list)
            for fi in face_ids:
                for ci in range(3):
                    a, b = int(mapping[fi, ci]), int(mapping[fi, (ci + 1) % 3])
                    va, vb = int(mesh.faces[fi, ci]), int(mesh.faces[fi, (ci + 1) % 3])
                    uv_edges[(min(a, b), max(a, b))].append(edge_lookup[(min(va, vb), max(va, vb))])
            boundary = [edge for edge, entries in uv_edges.items() if len(entries) == 1]
            dsu = DisjointSet(len(trial.uv))
            for a, b in boundary:
                dsu.union(a, b)
            loops: dict[int, set[int]] = defaultdict(set)
            for edge in boundary:
                loops[dsu.find(edge[0])].update(edge)
            if len(loops) <= 1:
                continue
            ordered = sorted(loops.values(), key=lambda loop: (-len(loop), min(loop)))
            sources = ordered[0]
            targets = set().union(*ordered[1:])
            graph: dict[int, list[tuple[int, int, float]]] = defaultdict(list)
            for (a, b), entries in uv_edges.items():
                ei = entries[0]
                if len(entries) != 2 or ei in constraints.must_join or ei in current:
                    continue
                va, vb = mesh.edges[ei]
                weight = float(
                    np.linalg.norm(mesh.vertices[va] - mesh.vertices[vb])
                ) * constraints.weights.get(ei, 1)
                graph[a].append((b, ei, weight))
                graph[b].append((a, ei, weight))
            distance = dict.fromkeys(sources, 0.0)
            queue = [(0.0, vertex) for vertex in sorted(sources)]
            heapq.heapify(queue)
            previous: dict[int, tuple[int, int]] = {}
            while queue:
                value, vertex = heapq.heappop(queue)
                if value != distance[vertex]:
                    continue
                if vertex in targets:
                    added = set()
                    while vertex not in sources:
                        vertex, edge = previous[vertex]
                        added.add(edge)
                    break
                for neighbor, edge, weight in graph[vertex]:
                    new = value + weight
                    if new < distance.get(neighbor, float("inf")):
                        distance[neighbor] = new
                        previous[neighbor] = (vertex, edge)
                        heapq.heappush(queue, (new, neighbor))
            if not added:
                raise ValueError("No admissible path between candidate boundary loops")
            break
        if not added:
            return current
        current = current | frozenset(added)
    raise ValueError("Candidate boundary-bridge limit exceeded")


def build_candidate(
    state: MeshState, kind: str, region: tuple[int, ...], seams: frozenset[int], candidate_id: str
) -> Candidate:
    removed, added = state.seams - seams, seams - state.seams
    trial = initialize(state.mesh, seams, inherited=state, affected_faces=frozenset(region))
    changed = frozenset(vertex for edge in removed | added for vertex in state.mesh.edges[edge])
    return Candidate(candidate_id, kind, region, removed, added, trial, state.version, changed)
