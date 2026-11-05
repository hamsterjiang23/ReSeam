"""Analytic symmetric-Dirichlet + barycentric coupling with guarded backtracking."""

import time
from dataclasses import dataclass, field
from typing import Any

import numpy as np
from scipy.sparse import csr_matrix

from .joint_contacts import sheet_intersections
from .joint_types import Correspondence
from .types import FloatArray, IntArray, MeshState
from .validation import intersection_errors, topology_errors


def member_intersection_errors(state: MeshState, orientations: IntArray) -> list[str]:
    errors = intersection_errors(state)
    if errors:
        return errors
    return sheet_intersections(state, orientations)


@dataclass
class PairProblem:
    initial: MeshState
    orientations: IntArray
    constraints: tuple[Correspondence, ...]
    reference_scale: float | None = None
    diagnostics: dict[str, Any] = field(default_factory=dict)
    contact_classes: tuple[int, ...] | None = None
    target_areas: FloatArray | None = None
    density_penalty: float = 10.0
    inverse_rest: FloatArray = field(init=False)
    weights: FloatArray = field(init=False)
    coupling: csr_matrix = field(init=False)
    coupling_weights: FloatArray = field(init=False)
    initial_area: float = field(init=False)

    def __post_init__(self) -> None:
        if topology_errors(self.initial):
            raise ValueError("Invalid paired UV topology")
        tri = self.initial.mesh.vertices[self.initial.mesh.faces] / (
            self.reference_scale or self.initial.mesh.scale
        )
        a, b = tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0]
        length = np.linalg.norm(a, axis=1)
        rest = np.zeros((len(tri), 2, 2))
        rest[:, 0, 0] = length
        rest[:, 0, 1] = np.sum(a * b, axis=1) / length
        rest[:, 1, 1] = np.linalg.norm(np.cross(a, b), axis=1) / length
        self.inverse_rest = np.asarray(np.linalg.inv(rest), dtype=np.float64)
        self.weights = self.initial.mesh.areas / self.initial.mesh.areas.sum()
        if self.orientations.shape != (len(tri),) or not np.isin(self.orientations, [-1, 1]).all():
            raise ValueError("One signed orientation is required per face")
        rows, cols, values, weights = [], [], [], []
        for index, item in enumerate(self.constraints):
            if min(item.source_face, item.target_face) < 0 or max(item.source_face, item.target_face) >= len(
                tri
            ):
                raise ValueError("Correspondence face out of range")
            for face, bary, sign in (
                (item.source_face, item.source_barycentric, 1),
                (item.target_face, item.target_barycentric, -1),
            ):
                if not np.isfinite(bary).all() or min(bary) < -1e-10 or abs(sum(bary) - 1) > 1e-8:
                    raise ValueError("Invalid correspondence barycentric coordinates")
                for vertex, weight in zip(self.initial.uv_faces[face], bary, strict=True):
                    rows.append(index)
                    cols.append(int(vertex))
                    values.append(sign * weight)
            weights.append(item.weight)
        self.coupling = csr_matrix((values, (rows, cols)), shape=(len(weights), len(self.initial.uv)))
        self.coupling_weights = np.asarray(weights, dtype=float)
        if len(weights):
            if not np.isfinite(weights).all() or min(weights) <= 0:
                raise ValueError("Positive finite correspondence weights required")
            self.coupling_weights /= self.coupling_weights.sum()
        self.initial_area = self.area(self.initial.uv)
        if self.target_areas is not None:
            target = np.asarray(self.target_areas, dtype=np.float64)
            if target.shape != (len(tri),) or not np.isfinite(target).all() or np.any(target <= 0):
                raise ValueError("Invalid target UV areas")
            self.target_areas = target / target.sum() * self.initial_area
        if not self.positive(self.initial.uv) or self.initial_area <= 1e-14:
            raise ValueError("Paired initialization has flipped/degenerate faces")
        if sheet_intersections(self.initial, self.orientations, self.contact_classes):
            raise ValueError("Paired initialization self-intersects")

    def jacobians(self, uv: FloatArray) -> FloatArray:
        p = uv[self.initial.uv_faces]
        return np.stack((p[:, 1] - p[:, 0], p[:, 2] - p[:, 0]), axis=2) @ self.inverse_rest

    def positive(self, uv: FloatArray) -> bool:
        return bool(
            np.isfinite(uv).all() and np.all(np.linalg.det(self.jacobians(uv)) * self.orientations > 1e-12)
        )

    def area(self, uv: FloatArray) -> float:
        p = uv[self.initial.uv_faces]
        a, b = p[:, 1] - p[:, 0], p[:, 2] - p[:, 0]
        return float(0.5 * np.abs(a[:, 0] * b[:, 1] - a[:, 1] * b[:, 0]).sum())

    def energy_gradient(self, uv: FloatArray, coefficient: float) -> tuple[float, FloatArray]:
        jac = self.jacobians(uv)
        inv = np.linalg.inv(jac)
        inv_t = np.swapaxes(inv, 1, 2)
        energy = float(
            self.weights @ (0.25 * (np.sum(jac * jac, axis=(1, 2)) + np.sum(inv * inv, axis=(1, 2))) - 1)
        )
        grad_j = 0.5 * (jac - inv_t @ inv @ inv_t) * self.weights[:, None, None]
        grad_y = grad_j @ np.swapaxes(self.inverse_rest, 1, 2)
        gradient = np.zeros_like(uv)
        for corner, grad in ((1, grad_y[:, :, 0]), (2, grad_y[:, :, 1]), (0, -grad_y.sum(axis=2))):
            np.add.at(gradient, self.initial.uv_faces[:, corner], grad)
        residual = self.coupling @ uv
        energy += float(
            coefficient / self.initial_area * np.sum(self.coupling_weights[:, None] * residual**2)
        )
        gradient += np.asarray(
            self.coupling.T
            @ (2 * coefficient / self.initial_area * self.coupling_weights[:, None] * residual)
        )
        if self.target_areas is not None:
            points = uv[self.initial.uv_faces]
            a, b = points[:, 1] - points[:, 0], points[:, 2] - points[:, 0]
            signed = 0.5 * (a[:, 0] * b[:, 1] - a[:, 1] * b[:, 0])
            delta = np.log(np.abs(signed) / self.target_areas)
            energy += float(self.density_penalty * (self.weights @ delta**2))
            factor = 2 * self.density_penalty * self.weights * delta / signed
            g1 = np.column_stack((b[:, 1], -b[:, 0])) * 0.5 * factor[:, None]
            g2 = np.column_stack((-a[:, 1], a[:, 0])) * 0.5 * factor[:, None]
            for corner, g in ((1, g1), (2, g2), (0, -g1 - g2)):
                np.add.at(gradient, self.initial.uv_faces[:, corner], g)
        return energy, gradient

    def density_violation(self, uv: FloatArray) -> float:
        if self.target_areas is None:
            return 0.0
        points = uv[self.initial.uv_faces]
        a, b = points[:, 1] - points[:, 0], points[:, 2] - points[:, 0]
        areas = 0.5 * np.abs(a[:, 0] * b[:, 1] - a[:, 1] * b[:, 0])
        ratio = np.sqrt(areas / self.target_areas)
        return float(max(0.0, 0.9 - ratio.min(), ratio.max() - 1.1))


@dataclass
class PairSolve:
    status: str
    state: MeshState
    history: list[dict[str, float | int]]
    wall_seconds: float
    coupling_rms: float


def solve_pair(problem: PairProblem, seconds: float, iterations: int = 200) -> PairSolve:
    start = time.monotonic()
    deadline = start + seconds
    uv = problem.initial.uv.copy()
    best = uv.copy()
    best_violation = problem.density_violation(best)
    best_residual = float(np.sqrt(np.sum(problem.coupling_weights[:, None] * (problem.coupling @ best) ** 2)))
    anchor0, anchor1 = map(int, problem.initial.uv_faces[0, :2])
    uv -= uv[anchor0]
    angle = float(np.arctan2(uv[anchor1, 1], uv[anchor1, 0]))
    c, s = np.cos(angle), np.sin(angle)
    uv = uv @ np.asarray([[c, -s], [s, c]])
    history: list[dict[str, float | int]] = []
    status = "SOLVED"

    def constrained_gradient(points: FloatArray, coefficient: float) -> tuple[float, FloatArray]:
        value, gradient = problem.energy_gradient(points, coefficient)
        if problem.contact_classes is not None:
            # Retain inherited endpoint continuity across clipped fans without welding UV identities.
            _, classes = np.unique(problem.contact_classes, return_inverse=True)
            counts = np.bincount(classes)
            total = np.zeros((len(counts), 2))
            np.add.at(total, classes, gradient)
            gradient = total[classes] / counts[classes, None]
            gradient[classes == classes[anchor0]] = 0
            gradient[classes == classes[anchor1], 1] = 0
        else:
            gradient[anchor0] = 0
            gradient[anchor1, 1] = 0
        return value, gradient

    for coefficient in (1.0, 10.0, 100.0):
        memory: list[tuple[FloatArray, FloatArray, float]] = []
        for iteration in range(max(1, iterations // 3)):
            if time.monotonic() >= deadline:
                status = "TIMEOUT_WITH_BEST"
                break
            value, gradient = constrained_gradient(uv, coefficient)
            direction = gradient.copy()
            alphas = []
            for dx, dg, rho in reversed(memory):
                alpha = rho * float(np.sum(dx * direction))
                alphas.append(alpha)
                direction -= alpha * dg
            if memory:
                dx, dg, _ = memory[-1]
                direction *= float(np.sum(dx * dg) / np.sum(dg * dg))
            for (dx, dg, rho), alpha in zip(memory, reversed(alphas), strict=True):
                direction += dx * (alpha - rho * float(np.sum(dg * direction)))
            slope = float(np.sum(gradient * direction))
            if slope <= 0:
                direction, slope = gradient.copy(), float(np.sum(gradient**2))
                memory.clear()
            step = min(1.0, 0.1 / max(float(np.linalg.norm(direction, axis=1).max()), 1e-12))
            candidate = None
            for _ in range(30):
                if time.monotonic() >= deadline:
                    status = "TIMEOUT_WITH_BEST"
                    break
                trial = uv - step * direction
                if trial[anchor1, 0] > 1e-12 and problem.positive(trial):
                    trial_energy, trial_gradient = constrained_gradient(trial, coefficient)
                    if trial_energy <= value - 1e-4 * step * slope:
                        check = problem.initial.copy()
                        check.uv = trial
                        if not sheet_intersections(check, problem.orientations, problem.contact_classes):
                            candidate = trial
                            break
                step *= 0.5
            if candidate is None:
                break
            dx, dg = candidate - uv, trial_gradient - gradient
            curvature = float(np.sum(dx * dg))
            if curvature > 1e-12:
                memory.append((dx, dg, 1 / curvature))
                memory = memory[-10:]
            uv = candidate
            restored = uv * np.sqrt(problem.initial_area / problem.area(uv))
            residual = float(
                np.sqrt(np.sum(problem.coupling_weights[:, None] * (problem.coupling @ restored) ** 2))
            )
            history.append(
                {
                    "coefficient": coefficient,
                    "iteration": iteration,
                    "energy": trial_energy,
                    "coupling_rms": residual,
                }
            )
            checked = problem.initial.copy()
            checked.uv = restored
            if (
                (problem.density_violation(restored), residual) < (best_violation, best_residual)
                and not sheet_intersections(checked, problem.orientations, problem.contact_classes)
                and time.monotonic() < deadline
            ):
                best, best_residual = restored.copy(), residual
                best_violation = problem.density_violation(restored)
            if slope < 1e-14:
                break
        if status == "TIMEOUT_WITH_BEST":
            break
    if time.monotonic() >= deadline:
        status = "TIMEOUT_WITH_BEST"
    result = problem.initial.copy()
    result.uv = best
    rms = float(np.sqrt(np.sum(problem.coupling_weights[:, None] * (problem.coupling @ best) ** 2)))
    return PairSolve(status, result, history, time.monotonic() - start, rms)
