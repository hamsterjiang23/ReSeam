"""Tolerance-aware bijection of oriented triangles carrying per-corner attributes."""

from collections import deque
from itertools import product

import numpy as np


def triangle_bijection(actual: np.ndarray, expected: np.ndarray, tolerance: float) -> bool:
    """Match cyclic corner permutations; never reverse winding or reuse a face.

    Centroid buckets only find candidates. Every position and UV component must
    independently satisfy the declared tolerance, followed by a perfect matching.
    """
    if (actual.shape != expected.shape or actual.ndim != 3 or actual.shape[1] != 3
            or actual.shape[2] < 3 or not np.isfinite(actual).all()
            or not np.isfinite(expected).all() or not np.isfinite(tolerance) or tolerance <= 0):
        return False
    buckets: dict[tuple[int, ...], list[int]] = {}
    for index, center in enumerate(expected[:, :, :3].mean(axis=1)):
        key = tuple(np.floor(center / tolerance).astype(np.int64))
        buckets.setdefault(key, []).append(index)
    neighbors = tuple(product((-1, 0, 1), repeat=3))
    graph: list[list[int]] = []
    for triangle in actual:
        key = np.floor(triangle[:, :3].mean(axis=0) / tolerance).astype(np.int64)
        candidates: list[tuple[float, int]] = []
        for offset in neighbors:
            for index in buckets.get(tuple(key + offset), []):
                error = min(float(np.max(np.abs(triangle - np.roll(expected[index], shift, axis=0))))
                            for shift in range(3))
                if error <= tolerance:
                    candidates.append((error, index))
        if not candidates:
            return False
        graph.append([index for _, index in sorted(candidates)])
    owner: dict[int, int] = {}
    assigned: dict[int, int] = {}
    # Augmenting paths retain bijection even when nearby or duplicate triangles
    # have overlapping candidate sets. Greedy nearest matching is insufficient.
    for start in sorted(range(len(graph)), key=lambda row: len(graph[row])):
        queue = deque([start])
        visited = {start}
        routes: dict[int, int] = {}
        endpoint: int | None = None
        while queue and endpoint is None:
            row = queue.popleft()
            for target in graph[row]:
                if target in routes:
                    continue
                routes[target] = row
                if target not in owner:
                    endpoint = target
                    break
                following = owner[target]
                if following not in visited:
                    queue.append(following)
                    visited.add(following)
        if endpoint is None:
            return False
        while True:
            row = routes[endpoint]
            previous = assigned.get(row)
            owner[endpoint] = row
            assigned[row] = endpoint
            if previous is None:
                break
            endpoint = previous
    return len(owner) == len(actual)
