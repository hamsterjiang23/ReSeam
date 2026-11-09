"""Continuous surface-matched region proposals on unchanged original faces."""

import time
from dataclasses import dataclass
from typing import Any

import numpy as np

from .candidates import bridge_holes
from .constraints import Constraints
from .initialization import initialize
from .joint_types import Correspondence
from .mesh import DisjointSet, corner_map, make_mesh, state_from_corner_uv
from .paired_solver import PairProblem
from .surface_match import (
    barycentric_coordinates,
    fit_offset,
    match_surfaces,
    project_surface,
    reflect,
    vertex_normals,
)
from .types import Mesh, MeshState


@dataclass(frozen=True)
class RegionPair:
    first_faces: tuple[int, ...]
    second_faces: tuple[int, ...]
    normal: tuple[float, float, float]
    offset: float


def components(mesh: Mesh, allowed: set[int]) -> list[tuple[int, ...]]:
    dsu = DisjointSet(len(mesh.faces))
    for entries in mesh.edge_faces:
        if len(entries) == 2:
            a, b = entries[0][0], entries[1][0]
            if a in allowed and b in allowed:
                dsu.union(a, b)
    groups: dict[int, list[int]] = {}
    for face in sorted(allowed):
        groups.setdefault(dsu.find(face), []).append(face)
    return sorted((tuple(x) for x in groups.values()), key=lambda x: -float(mesh.areas[list(x)].sum()))


def region_pairs(mesh: Mesh, deadline: float) -> tuple[list[RegionPair], list[dict[str, Any]]]:
    center = mesh.vertices.mean(axis=0)
    _, axes = np.linalg.eigh((mesh.vertices - center).T @ (mesh.vertices - center))
    trials = []
    for normal in np.concatenate((np.eye(3), axes.T)):
        if time.monotonic() >= deadline:
            break
        try:
            offset, _, _ = fit_offset(mesh, normal, float(np.median(mesh.vertices @ normal)))
            mapping = match_surfaces(mesh, normal, offset)
            vertex_weight = np.zeros(len(mesh.vertices))
            for c in range(3):
                np.add.at(vertex_weight, mesh.faces[:, c], mesh.areas / 3)
            order = np.argsort(mapping.surface_error)
            selected = order[: max(1, int(0.8 * len(order)))]
            w = vertex_weight[mapping.source_ids[selected]]
            cost = float(np.sum(mapping.surface_error[selected] ** 2 * w) / w.sum())
            trials.append((cost, normal, offset, mapping))
        except (ValueError, IndexError, np.linalg.LinAlgError) as error:
            trials.append((float("inf"), normal, 0, str(error)))
    logs = [
        {
            "normal": n.tolist(),
            "offset": d,
            "score": score if np.isfinite(score) else None,
            "status": "VALID" if not isinstance(m, str) else m,
        }
        for score, n, d, m in trials
    ]
    if not trials or not np.isfinite(min(t[0] for t in trials)):
        return [], logs
    _, normal, offset, mapping = min(trials, key=lambda t: t[0])
    if isinstance(mapping, str):
        return [], logs
    good = (
        (mapping.surface_error <= 0.005)
        & (mapping.cycle_error <= 0.005)
        & (mapping.normal_angle_degrees <= 45)
    )
    valid = np.zeros(len(mesh.vertices), dtype=bool)
    valid[mapping.source_ids] = good
    projected = np.zeros_like(mesh.vertices)
    projected[mapping.source_ids] = mapping.target_points
    side = mesh.vertices @ normal - offset
    eligible = np.all(valid[mesh.faces], axis=1) & np.all(side[mesh.faces] > 0, axis=1)
    p = mesh.vertices[mesh.faces]
    q = projected[mesh.faces]
    for edge in range(3):
        before = np.linalg.norm(p[:, edge] - p[:, (edge + 1) % 3], axis=1)
        ratio = np.linalg.norm(q[:, edge] - q[:, (edge + 1) % 3], axis=1) / before
        eligible &= (ratio >= 0.5) & (ratio <= 2)
    original_cross = np.cross(p[:, 1] - p[:, 0], p[:, 2] - p[:, 0])
    mapped_cross = np.cross(q[:, 1] - q[:, 0], q[:, 2] - q[:, 0])
    eligible &= np.sum(mapped_cross * reflect(original_cross, normal, 0), axis=1) < 0
    target = np.flatnonzero(np.all(side[mesh.faces] < 0, axis=1))
    bary = np.asarray(
        [[1 / 3] * 3, [0.5, 0.5, 0], [0, 0.5, 0.5], [0.5, 0, 0.5], [1, 0, 0], [0, 1, 0], [0, 0, 1]]
    )
    face_ids = np.flatnonzero(eligible)
    if not len(face_ids):
        return [], logs
    samples = np.einsum("sc,fcd->fsd", bary, p[face_ids])
    _, local, error = project_surface(reflect(samples.reshape(-1, 3), normal, offset), p[target])
    diagonal = float(np.linalg.norm(np.ptp(mesh.vertices, axis=0)))
    passed = np.all(error.reshape(-1, len(bary)) / diagonal <= 0.005, axis=1)
    support = {
        int(fi): set(map(int, target[row]))
        for fi, row in zip(face_ids, local.reshape(-1, len(bary)), strict=True)
    }
    eligible[face_ids[~passed]] = False
    pairs = []
    for first in components(mesh, set(map(int, np.flatnonzero(eligible)))):
        if len(first) < 8:
            continue
        target_faces = set().union(*(support[f] for f in first))
        for second in components(mesh, target_faces):
            selected = tuple(f for f in first if support[f] <= set(second))
            for connected in components(mesh, set(selected)):
                if (
                    len(connected) >= 8
                    and len(second) >= 8
                    and max(mesh.areas[list(connected)].sum(), mesh.areas[list(second)].sum())
                    / mesh.areas.sum()
                    >= 0.005
                ):
                    pairs.append(
                        RegionPair(
                            connected, second, (float(normal[0]), float(normal[1]), float(normal[2])), offset
                        )
                    )
    return pairs, logs


def paired_problem(
    mesh: Mesh, pair: RegionPair, deadline: float, inherited: MeshState | None = None, planar: bool = False
) -> tuple[PairProblem, tuple[int, ...]]:
    ids = pair.first_faces + pair.second_faces
    local_mesh = make_mesh(mesh.vertices, mesh.faces[list(ids)])
    if planar:
        mapping = corner_map(local_mesh, frozenset())
        positions = np.empty((int(mapping.max()) + 1, 3))
        positions[mapping.reshape(-1)] = local_mesh.vertices[local_mesh.faces.reshape(-1)]
        split_faces = len(pair.first_faces)
        a_ids = np.unique(mapping[:split_faces])
        b_ids = np.unique(mapping[split_faces:])
        tri = mesh.vertices[mesh.faces[list(pair.first_faces)]]
        plane = np.sum(np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0]), axis=0)
        plane /= np.linalg.norm(plane)
        axis = np.eye(3)[int(np.argmin(np.abs(plane)))]
        axis -= plane * np.dot(axis, plane)
        axis /= np.linalg.norm(axis)
        basis = np.column_stack((axis, np.cross(plane, axis)))
        positions[b_ids] = reflect(positions[b_ids], np.asarray(pair.normal), pair.offset)
        uv = (positions - positions[a_ids].mean(axis=0)) @ basis / mesh.scale
        state = MeshState(local_mesh, frozenset(), uv, mapping)
    elif inherited is None:
        mapping = corner_map(local_mesh, frozenset())
        dummy = MeshState(local_mesh, frozenset(), np.zeros((int(mapping.max()) + 1, 2)), mapping)
        cuts = bridge_holes(dummy, frozenset(), tuple(range(len(ids))), Constraints(), deadline)
        state = initialize(local_mesh, cuts)
    else:
        if not np.array_equal(mesh.faces, inherited.mesh.faces) or not np.array_equal(
            mesh.vertices, inherited.mesh.vertices
        ):
            raise ValueError("INHERITED_GEOMETRY_MISMATCH")
        state = state_from_corner_uv(
            local_mesh, inherited.uv[inherited.uv_faces[list(ids)]], corner_ids=inherited.uv_faces[list(ids)]
        )
    split = len(pair.first_faces)
    first_vertices = np.unique(state.uv_faces[:split])
    second_vertices = np.unique(state.uv_faces[split:])
    if not planar:
        state.uv[first_vertices] -= state.uv[first_vertices].mean(axis=0)
        state.uv[second_vertices] -= state.uv[second_vertices].mean(axis=0)
        state.uv[second_vertices, 0] *= -1
    contact_classes = None
    if planar:
        classes = np.empty(len(state.uv), dtype=np.int64)
        classes[state.uv_faces.reshape(-1)] = local_mesh.faces.reshape(-1)
        contact_classes = tuple(map(int, classes))
    elif inherited is not None:
        classes = np.empty(len(state.uv), dtype=np.int64)
        classes[state.uv_faces.reshape(-1)] = inherited.uv_faces[list(ids)].reshape(-1)
        contact_classes = tuple(map(int, classes))
    constraints = []
    samples = np.asarray([[1.0, 0, 0], [0, 1.0, 0], [0, 0, 1.0], [1 / 3] * 3])
    normal = np.asarray(pair.normal)
    normals = vertex_normals(mesh)
    diagonal = float(np.linalg.norm(np.ptp(mesh.vertices, axis=0)))
    rejected = []
    # Bidirectional face-corner and interior samples, weighted by original face area.
    for source_ids, target_ids in (
        (np.arange(split), np.arange(split, len(ids))),
        (np.arange(split, len(ids)), np.arange(split)),
    ):
        target_tri = local_mesh.vertices[local_mesh.faces[target_ids]]
        for fi in source_ids:
            if time.monotonic() >= deadline:
                raise TimeoutError("CORRESPONDENCE_BUILD_BUDGET")
            points = samples @ local_mesh.vertices[local_mesh.faces[fi]]
            projected, targets, errors = project_surface(reflect(points, normal, pair.offset), target_tri)
            coords = barycentric_coordinates(target_tri[targets], projected)
            returned, _, _ = project_surface(
                reflect(projected, normal, pair.offset), local_mesh.vertices[local_mesh.faces[source_ids]]
            )
            n0 = samples @ normals[local_mesh.faces[fi]]
            n1 = np.einsum("sc,scd->sd", coords, normals[local_mesh.faces[target_ids[targets]]])
            n0 /= np.maximum(np.linalg.norm(n0, axis=1)[:, None], 1e-15)
            n1 /= np.maximum(np.linalg.norm(n1, axis=1)[:, None], 1e-15)
            angles = np.degrees(np.arccos(np.clip(np.sum(reflect(n0, normal, 0) * n1, axis=1), -1, 1)))
            cycles = np.linalg.norm(returned - points, axis=1) / diagonal
            for si, (a, b, face) in enumerate(zip(samples, coords, target_ids[targets], strict=True)):
                if errors[si] / diagonal > 0.005 or cycles[si] > 0.005 or angles[si] > 45:
                    rejected.append(
                        {
                            "source_face": int(fi),
                            "sample": si,
                            "distance": float(errors[si] / diagonal),
                            "cycle": float(cycles[si]),
                            "angle": float(angles[si]),
                        }
                    )
                    continue
                constraints.append(
                    Correspondence(
                        int(fi),
                        (float(a[0]), float(a[1]), float(a[2])),
                        int(face),
                        (float(b[0]), float(b[1]), float(b[2])),
                        float(local_mesh.areas[fi]) / len(samples),
                    )
                )
    if {c.source_face for c in constraints} != set(range(len(ids))):
        raise ValueError(
            "INCOMPLETE_REGIONAL_CORRESPONDENCE:"
            + str(sorted(set(range(len(ids))) - {c.source_face for c in constraints}))
        )
    orientations = np.r_[np.ones(split, dtype=np.int64), -np.ones(len(ids) - split, dtype=np.int64)]
    if inherited is not None and not planar:
        left, right, weights = [], [], []
        for item in constraints:
            a = np.asarray(item.source_barycentric) @ state.uv[state.uv_faces[item.source_face]]
            b = np.asarray(item.target_barycentric) @ state.uv[state.uv_faces[item.target_face]]
            left.append(a if item.source_face < split else b)
            right.append(b if item.source_face < split else a)
            weights.append(item.weight)
        w = np.asarray(weights)
        w /= w.sum()
        a, b = np.asarray(left), np.asarray(right)
        ca, cb = np.sum(a * w[:, None], axis=0), np.sum(b * w[:, None], axis=0)
        u, _, vt = np.linalg.svd((b - cb).T @ (w[:, None] * (a - ca)))
        fix = np.diag([1.0, float(np.linalg.det(u @ vt))])
        state.uv[second_vertices] = (state.uv[second_vertices] - cb) @ (u @ fix @ vt) + ca
    best = None
    score = float("inf")
    phase_failures = []
    for phase in range(8):
        trial = state.copy()
        angle = phase * np.pi / 4
        c, s = np.cos(angle), np.sin(angle)
        trial.uv[second_vertices] = trial.uv[second_vertices] @ np.asarray([[c, -s], [s, c]])
        try:
            problem = PairProblem(
                trial,
                orientations,
                tuple(constraints),
                reference_scale=mesh.scale,
                contact_classes=contact_classes,
            )
        except ValueError as error:
            phase_failures.append({"phase": phase, "reason": str(error)})
            continue
        problem.diagnostics = {
            "initialization": "planar_geometry"
            if planar
            else "inherited_uv"
            if inherited is not None
            else "harmonic_circle",
            "rejected_samples": rejected,
            "constraint_count": len(constraints),
        }
        residual = float(np.sum(problem.coupling_weights[:, None] * (problem.coupling @ trial.uv) ** 2))
        if residual < score:
            best = problem
            score = residual
    if best is None:
        raise ValueError("NO_PAIRED_INITIALIZATION:" + str(phase_failures))
    best.diagnostics["phase_failures"] = phase_failures
    return best, ids


def refine_pair(mesh: Mesh, pair: RegionPair, deadline: float) -> tuple[RegionPair, list[dict[str, int]]]:
    """Remove unsupported boundary faces explicitly; never force invalid regional projections."""
    first, second = pair.first_faces, pair.second_faces
    normal = np.asarray(pair.normal)
    tri = mesh.vertices[mesh.faces]
    normals = vertex_normals(mesh)
    diagonal = float(np.linalg.norm(np.ptp(mesh.vertices, axis=0)))
    samples = np.asarray([[1.0, 0, 0], [0, 1.0, 0], [0, 0, 1.0], [1 / 3] * 3])
    history = []
    for iteration in range(6):
        if time.monotonic() >= deadline:
            raise TimeoutError("REGION_REFINEMENT_BUDGET")
        keep = []
        for source, target in ((first, second), (second, first)):
            points = np.einsum("sc,fcd->fsd", samples, tri[list(source)]).reshape(-1, 3)
            q, ids, error = project_surface(reflect(points, normal, pair.offset), tri[list(target)])
            bary = barycentric_coordinates(tri[np.asarray(target)[ids]], q)
            back, _, _ = project_surface(reflect(q, normal, pair.offset), tri[list(source)])
            na = np.einsum("sc,fcd->fsd", samples, normals[mesh.faces[list(source)]]).reshape(-1, 3)
            nb = np.einsum("sc,scd->sd", bary, normals[mesh.faces[np.asarray(target)[ids]]])
            na /= np.maximum(np.linalg.norm(na, axis=1)[:, None], 1e-15)
            nb /= np.maximum(np.linalg.norm(nb, axis=1)[:, None], 1e-15)
            cosine = np.sum(reflect(na, normal, 0) * nb, axis=1)
            valid = (
                (error / diagonal <= 0.005)
                & (np.linalg.norm(back - points, axis=1) / diagonal <= 0.005)
                & (cosine >= np.cos(np.pi / 4))
            )
            faces = np.asarray(source)[np.any(valid.reshape(-1, 4), axis=1)]
            groups = components(mesh, set(map(int, faces)))
            if not groups:
                raise ValueError("EMPTY_REFINED_REGION")
            keep.append(groups[0])
        history.append(
            {
                "iteration": iteration,
                "first_before": len(first),
                "second_before": len(second),
                "first_after": len(keep[0]),
                "second_after": len(keep[1]),
            }
        )
        unchanged = first == keep[0] and second == keep[1]
        first, second = keep
        if (
            min(len(first), len(second)) < 8
            or max(mesh.areas[list(first)].sum(), mesh.areas[list(second)].sum()) / mesh.areas.sum() < 0.005
        ):
            raise ValueError("REFINED_REGION_BELOW_MINIMUM")
        if unchanged:
            return RegionPair(first, second, pair.normal, pair.offset), history
    raise ValueError("REGION_REFINEMENT_NOT_CONVERGED")


def planar_regions(mesh: Mesh, pairs: list[RegionPair], maximum_faces: int = 64) -> list[RegionPair]:
    """Bounded normal-cone patches; original face IDs and both sides are retained explicitly."""
    tri = mesh.vertices[mesh.faces]
    normals = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
    normals /= np.linalg.norm(normals, axis=1)[:, None]
    neighbors: dict[int, set[int]] = {i: set() for i in range(len(tri))}
    for entries in mesh.edge_faces:
        if len(entries) == 2:
            a, b = entries[0][0], entries[1][0]
            neighbors[a].add(b)
            neighbors[b].add(a)
    output = []
    sample = np.asarray([[1, 0, 0], [0, 1, 0], [0, 0, 1], [1 / 3] * 3], dtype=float)
    for pair in pairs:
        remaining = set(pair.first_faces)
        while remaining:
            seed = min(remaining, key=lambda i: (-mesh.areas[i], i))
            chosen = {seed}
            queue = [seed]
            remaining.remove(seed)
            while queue and len(chosen) < maximum_faces:
                current = queue.pop(0)
                for other in sorted(neighbors[current] & remaining):
                    if (
                        normals[other] @ normals[seed] >= np.cos(np.radians(25))
                        and len(chosen) < maximum_faces
                    ):
                        chosen.add(other)
                        queue.append(other)
                        remaining.remove(other)
            if len(chosen) < 8:
                continue
            first = tuple(sorted(chosen))
            target = np.asarray(
                [
                    f
                    for f in pair.second_faces
                    if normals[f] @ reflect(normals[seed], np.asarray(pair.normal), 0)
                    >= np.cos(np.radians(30))
                ],
                dtype=np.int64,
            )
            if len(target) < 8:
                continue
            points = np.einsum("sc,fcd->fsd", sample, tri[list(first)]).reshape(-1, 3)
            _, indices, _ = project_surface(
                reflect(points, np.asarray(pair.normal), pair.offset), tri[target]
            )
            support = set(map(int, target[indices]))
            allowed = set(map(int, target))
            support |= set().union(*(neighbors[f] & allowed for f in list(support)))
            for second in components(mesh, support):
                if (
                    len(second) >= 8
                    and max(mesh.areas[list(first)].sum(), mesh.areas[list(second)].sum()) / mesh.areas.sum()
                    >= 0.005
                ):
                    output.append(RegionPair(first, second, pair.normal, pair.offset))
    return sorted(output, key=lambda p: -float(mesh.areas[list(p.first_faces)].sum()))
