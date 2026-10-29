"""Independent, scale-gauged geometry proposal solver for derivation/v1.

This module validates member injectivity, not final material quality or storage.
It does not call OptCuts or the historical paired optimizer.
"""

import math
import time
from dataclasses import dataclass, field

import numpy as np

from .joint_contacts import sheet_intersections
from .joint_types import Correspondence
from .mesh import chart_labels
from .types import FloatArray, IntArray, MeshState
from .validation import topology_errors


def first_positive_determinant_root(matrix: FloatArray, direction: FloatArray) -> float:
    """First positive zero on a 2D matrix path, including even-multiplicity roots."""
    k = np.asarray(matrix, dtype=float)
    d = np.asarray(direction, dtype=float)
    if k.shape != (2, 2) or d.shape != (2, 2) or not np.isfinite(k).all() or not np.isfinite(d).all():
        raise ValueError("Finite 2x2 matrices required")
    c = float(k[0, 0] * k[1, 1] - k[0, 1] * k[1, 0])
    b = float(k[0, 0] * d[1, 1] + d[0, 0] * k[1, 1] - k[0, 1] * d[1, 0] - d[0, 1] * k[1, 0])
    a = float(d[0, 0] * d[1, 1] - d[0, 1] * d[1, 0])
    if c <= 0:
        raise ValueError("Path must start with positive determinant")
    if a == 0:
        return -c / b if b < 0 else math.inf
    # Normalize before forming the discriminant to avoid coefficient overflow.
    scale = max(abs(a), abs(b), abs(c))
    a, b, c = a / scale, b / scale, c / scale
    discriminant = b * b - 4 * a * c
    tolerance = 32 * np.finfo(float).eps * (b * b + abs(4 * a * c))
    if discriminant < -tolerance:
        return math.inf
    root = math.sqrt(max(0.0, discriminant))
    q = -0.5 * (b + math.copysign(root, b))
    candidates = [-b / (2 * a)] if q == 0 else [q / a, c / q]
    positive = [value for value in candidates if value > 0 and math.isfinite(value)]
    return min(positive, default=math.inf)


@dataclass
class BudgetPairProblem:
    initial: MeshState
    orientations: IntArray
    correspondences: tuple[Correspondence, ...]
    member_ids: IntArray | None = None
    contact_classes: tuple[int, ...] | None = None
    shape_weight: float = 1.0
    coupling_weight: float = 10.0
    maximum_anisotropy: float | None = None
    determinant_relative_floor: float = 1e-10
    inverse_rest: FloatArray = field(init=False)
    weights: FloatArray = field(init=False)
    members: IntArray = field(init=False)
    coupling: FloatArray = field(init=False)
    coupling_weights: FloatArray = field(init=False)
    determinant_floor: FloatArray = field(init=False)
    anchors: tuple[int, int] = field(init=False)
    reference_length: float = field(init=False)

    def __post_init__(self) -> None:
        self.initial = self.initial.copy()
        errors = topology_errors(self.initial)
        if errors:
            raise ValueError("Invalid UV topology: " + ";".join(errors))
        count = len(self.initial.mesh.faces)
        self.orientations = np.asarray(self.orientations, dtype=np.int64).copy()
        self.members = np.asarray(
            chart_labels(self.initial) if self.member_ids is None else self.member_ids, dtype=np.int64
        ).copy()
        if self.orientations.shape != (count,) or not np.isin(self.orientations, [-1, 1]).all():
            raise ValueError("One signed orientation is required per face")
        if self.members.shape != (count,) or np.any(self.members < 0):
            raise ValueError("One nonnegative member ID is required per face")
        for member in np.unique(self.members):
            if len(np.unique(self.orientations[self.members == member])) != 1:
                raise ValueError("Member orientation must be consistent")
        coefficients = (self.shape_weight, self.coupling_weight, self.determinant_relative_floor)
        if not all(math.isfinite(x) and x > 0 for x in coefficients):
            raise ValueError("Positive finite objective coefficients required")
        if self.determinant_relative_floor >= 1:
            raise ValueError("Relative determinant floor must be below one")
        if self.maximum_anisotropy is not None and (
            not math.isfinite(self.maximum_anisotropy) or self.maximum_anisotropy < 1
        ):
            raise ValueError("Anisotropy bound must be finite and at least one")
        if self.contact_classes is not None:
            if len(self.contact_classes) != len(self.initial.uv):
                raise ValueError("Invalid contact provenance")
            classes = np.asarray(self.contact_classes, dtype=np.int64)
            for label in np.unique(classes):
                points = self.initial.uv[classes == label]
                if not np.allclose(points, points[0], atol=1e-12, rtol=0):
                    raise ValueError("Contact classes must initially have identical positions")
        triangles = self.initial.mesh.vertices[self.initial.mesh.faces] / self.initial.mesh.scale
        a, b = triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0]
        lengths = np.linalg.norm(a, axis=1)
        rest = np.zeros((count, 2, 2), dtype=np.float64)
        rest[:, 0, 0] = lengths
        rest[:, 0, 1] = np.sum(a * b, axis=1) / lengths
        rest[:, 1, 1] = np.linalg.norm(np.cross(a, b), axis=1) / lengths
        self.inverse_rest = np.asarray(np.linalg.inv(rest), dtype=np.float64)
        self.weights = self.initial.mesh.areas / self.initial.mesh.areas.sum()
        initial_determinants = np.linalg.det(self.jacobians(self.initial.uv)) * self.orientations
        if not np.isfinite(initial_determinants).all() or np.any(initial_determinants <= 0):
            raise ValueError("Flipped or degenerate initial member")
        self.determinant_floor = initial_determinants * self.determinant_relative_floor
        if sheet_intersections(self.initial, self.members, self.contact_classes):
            raise ValueError("Initial member self-intersects")
        if self.maximum_anisotropy is not None and self.anisotropy(self.initial.uv) > self.maximum_anisotropy:
            raise ValueError("Initial anisotropy exceeds hard bound")
        first_member_faces = self.initial.uv_faces[self.members == self.members[0]]
        vertices = np.unique(first_member_faces)
        anchor0 = int(vertices[0])
        distances = np.linalg.norm(self.initial.uv[vertices] - self.initial.uv[anchor0], axis=1)
        anchor1 = int(vertices[int(np.argmax(distances))])
        self.reference_length = float(distances.max())
        if self.reference_length <= 0:
            raise ValueError("Degenerate common-frame gauge")
        self.anchors = (anchor0, anchor1)
        self.coupling = np.zeros((len(self.correspondences), len(self.initial.uv)), dtype=np.float64)
        weights = []
        source_faces: set[int] = set()
        for index, item in enumerate(self.correspondences):
            if (
                min(item.source_face, item.target_face) < 0
                or max(item.source_face, item.target_face) >= count
            ):
                raise ValueError("Correspondence face out of range")
            if self.members[item.source_face] == self.members[item.target_face]:
                raise ValueError("Correspondence must connect distinct members")
            if not math.isfinite(item.weight) or item.weight <= 0:
                raise ValueError("Positive finite correspondence weight required")
            for face, bary, sign in (
                (item.source_face, item.source_barycentric, 1.0),
                (item.target_face, item.target_barycentric, -1.0),
            ):
                if (
                    len(bary) != 3
                    or not np.isfinite(bary).all()
                    or min(bary) < -1e-10
                    or abs(sum(bary) - 1) > 1e-8
                ):
                    raise ValueError("Invalid correspondence barycentric coordinates")
                np.add.at(self.coupling[index], self.initial.uv_faces[face], sign * np.asarray(bary))
            source_faces.add(item.source_face)
            weights.append(item.weight)
        if source_faces != set(range(count)):
            raise ValueError("CORRESPONDENCE_INCOMPLETE: every member face requires a source sample")
        self.coupling_weights = np.asarray(weights, dtype=np.float64)
        self.coupling_weights /= self.coupling_weights.sum()
        for member in np.unique(self.members):
            points = np.asarray(
                [
                    np.asarray(c.source_barycentric) @ self.initial.uv[self.initial.uv_faces[c.source_face]]
                    for c in self.correspondences
                    if self.members[c.source_face] == member
                ]
            )
            centered = points - points.mean(axis=0)
            eigenvalues = np.linalg.eigvalsh(centered.T @ centered)
            if eigenvalues[0] <= max(float(eigenvalues[-1]) * 1e-12, np.finfo(float).tiny):
                raise ValueError("CORRESPONDENCE_RANK_DEFICIENT: member source samples must span a domain")

    def jacobians(self, uv: FloatArray) -> FloatArray:
        points = uv[self.initial.uv_faces]
        return (
            np.stack((points[:, 1] - points[:, 0], points[:, 2] - points[:, 0]), axis=2) @ self.inverse_rest
        )

    def positive(self, uv: FloatArray) -> bool:
        return bool(
            np.isfinite(uv).all()
            and np.all(np.linalg.det(self.jacobians(uv)) * self.orientations > self.determinant_floor)
        )

    def anisotropy(self, uv: FloatArray) -> float:
        singular = np.linalg.svd(self.jacobians(uv), compute_uv=False)
        return float(np.max(singular[:, 0] / singular[:, 1]))

    def energy_gradient(self, uv: FloatArray) -> tuple[float, FloatArray]:
        jacobian = self.jacobians(uv)
        oriented = jacobian.copy()
        oriented[:, 0] *= self.orientations[:, None]
        determinants = np.linalg.det(oriented)
        if np.any(determinants <= 0) or not np.isfinite(determinants).all():
            raise ValueError("Shape gradient requires nondegenerate correctly oriented members")
        area_scale = np.sqrt(determinants)
        normalized = oriented / area_scale[:, None, None]
        inverse = np.linalg.inv(normalized)
        inverse_t = inverse.swapaxes(1, 2)
        density = 0.25 * (np.sum(normalized**2, axis=(1, 2)) + np.sum(inverse**2, axis=(1, 2))) - 1
        energy = float(self.shape_weight * (self.weights @ density))
        gradient_normalized = 0.5 * (normalized - inverse_t @ inverse @ inverse_t)
        gradient_oriented = gradient_normalized / area_scale[:, None, None]
        contraction = np.sum(gradient_normalized * normalized, axis=(1, 2))
        gradient_oriented -= 0.5 * contraction[:, None, None] * np.linalg.inv(oriented).swapaxes(1, 2)
        gradient_oriented *= (self.shape_weight * self.weights)[:, None, None]
        gradient_oriented[:, 0] *= self.orientations[:, None]
        gradient_edges = gradient_oriented @ self.inverse_rest.swapaxes(1, 2)
        gradient = np.zeros_like(uv)
        for corner, values in (
            (1, gradient_edges[:, :, 0]),
            (2, gradient_edges[:, :, 1]),
            (0, -gradient_edges.sum(axis=2)),
        ):
            np.add.at(gradient, self.initial.uv_faces[:, corner], values)
        residual = self.coupling @ uv
        factor = self.coupling_weight / self.reference_length**2
        energy += float(factor * np.sum(self.coupling_weights[:, None] * residual**2))
        gradient += self.coupling.T @ (2 * factor * self.coupling_weights[:, None] * residual)
        return energy, gradient

    def coupling_rms(self, uv: FloatArray) -> float:
        return float(np.sqrt(np.sum(self.coupling_weights[:, None] * (self.coupling @ uv) ** 2)))

    def safe_step(self, uv: FloatArray, direction: FloatArray, maximum: float = 1.0) -> float:
        current, delta = self.jacobians(uv), self.jacobians(direction)
        current[:, 0] *= self.orientations[:, None]
        delta[:, 0] *= self.orientations[:, None]
        root = min(
            (first_positive_determinant_root(k, d) for k, d in zip(current, delta, strict=True)),
            default=math.inf,
        )
        return min(maximum, 0.95 * root)

    def geometry_errors(self, uv: FloatArray) -> list[str]:
        if not self.positive(uv):
            return ["FLIPPED_OR_DEGENERATE"]
        if self.maximum_anisotropy is not None and self.anisotropy(uv) > self.maximum_anisotropy:
            return ["ANISOTROPY_LIMIT"]
        candidate = self.initial.copy()
        candidate.uv = uv
        return sheet_intersections(candidate, self.members, self.contact_classes)


@dataclass
class BudgetPairSolve:
    status: str
    state: MeshState
    history: list[dict[str, float | int]]
    wall_seconds: float
    coupling_rms: float
    maximum_anisotropy: float
    geometry_valid: bool
    material_status: str = "NOT_EVALUATED"


def solve_budget_pair(problem: BudgetPairProblem, seconds: float, iterations: int = 200) -> BudgetPairSolve:
    """Produce guarded geometry proposals; a returned solve is not a material certificate."""
    if not math.isfinite(seconds) or seconds < 0 or type(iterations) is not int or iterations < 0:
        raise ValueError("Nonnegative finite seconds and nonnegative integer iterations required")
    started = time.monotonic()
    deadline = started + seconds
    original = problem.initial.uv.copy()
    best = original.copy()
    history: list[dict[str, float | int]] = []
    status = "TIMEOUT_WITH_BEST" if seconds == 0 else "ITERATION_LIMIT"
    if seconds > 0 and iterations > 0:
        anchor0, anchor1 = problem.anchors
        origin = original[anchor0].copy()
        relative = original - origin
        angle = float(np.arctan2(relative[anchor1, 1], relative[anchor1, 0]))
        c, s = math.cos(angle), math.sin(angle)
        rotation = np.asarray([[c, -s], [s, c]])
        uv = relative @ rotation
        fixed_positions = uv[[anchor0, anchor1]].copy()
        best_energy, _ = problem.energy_gradient(uv)
        memory: list[tuple[FloatArray, FloatArray, float]] = []

        def project_gradient(gradient: FloatArray) -> FloatArray:
            result = gradient.copy()
            if problem.contact_classes is None:
                result[[anchor0, anchor1]] = 0
            else:
                _, classes = np.unique(problem.contact_classes, return_inverse=True)
                counts = np.bincount(classes)
                total = np.zeros((len(counts), 2))
                np.add.at(total, classes, result)
                result = total[classes] / counts[classes, None]
                result[np.isin(classes, classes[[anchor0, anchor1]])] = 0
            return result

        for iteration in range(iterations):
            if time.monotonic() >= deadline:
                status = "TIMEOUT_WITH_BEST"
                break
            energy, raw_gradient = problem.energy_gradient(uv)
            gradient = project_gradient(raw_gradient)
            norm = float(np.linalg.norm(gradient))
            if norm <= 1e-9:
                status = "GEOMETRY_STATIONARY"
                break
            direction = gradient.copy()
            alphas: list[float] = []
            for dx, dg, rho in reversed(memory):
                alpha = rho * float(np.sum(dx * direction))
                alphas.append(alpha)
                direction -= alpha * dg
            if memory:
                dx, dg, _ = memory[-1]
                direction *= float(np.sum(dx * dg) / np.sum(dg**2))
            for (dx, dg, rho), alpha in zip(memory, reversed(alphas), strict=True):
                direction += dx * (alpha - rho * float(np.sum(dg * direction)))
            direction = -project_gradient(direction)
            slope = float(np.sum(gradient * direction))
            if not math.isfinite(slope) or slope >= 0:
                direction, slope = -gradient, -float(np.sum(gradient**2))
                memory.clear()
            maximum = min(
                1.0,
                0.25 * problem.reference_length / max(float(np.linalg.norm(direction, axis=1).max()), 1e-15),
            )
            step = problem.safe_step(uv, direction, maximum)
            trial = None
            trial_gradient = None
            trial_energy = energy
            for _ in range(35):
                if time.monotonic() >= deadline:
                    status = "TIMEOUT_WITH_BEST"
                    break
                proposed = uv + step * direction
                proposed[[anchor0, anchor1]] = fixed_positions
                if problem.positive(proposed):
                    proposed_energy, proposed_gradient = problem.energy_gradient(proposed)
                    if proposed_energy <= energy + 1e-4 * step * slope and not problem.geometry_errors(
                        proposed
                    ):
                        # Validation itself may cross the deadline: never archive such a trial.
                        if time.monotonic() >= deadline:
                            status = "TIMEOUT_WITH_BEST"
                            break
                        trial, trial_energy = proposed, proposed_energy
                        trial_gradient = project_gradient(proposed_gradient)
                        break
                step *= 0.5
            if trial is None or trial_gradient is None:
                if status != "TIMEOUT_WITH_BEST":
                    status = "LINE_SEARCH_STALLED"
                break
            dx, dg = trial - uv, trial_gradient - gradient
            curvature = float(np.sum(dx * dg))
            if curvature > 1e-12:
                memory.append((dx, dg, 1 / curvature))
                memory = memory[-10:]
            uv = trial
            if trial_energy < best_energy:
                best_energy = trial_energy
                # Only undo the common rigid frame; no per-member or area restoration.
                best = uv @ rotation.T + origin
            history.append(
                {
                    "iteration": iteration,
                    "energy": trial_energy,
                    "coupling_rms": problem.coupling_rms(uv),
                    "maximum_anisotropy": problem.anisotropy(uv),
                    "step": step,
                    "projected_gradient_norm": norm,
                }
            )
    result = problem.initial.copy()
    result.uv = best
    errors = problem.geometry_errors(best)
    return BudgetPairSolve(
        status,
        result,
        history,
        time.monotonic() - started,
        problem.coupling_rms(best),
        problem.anisotropy(best),
        not errors,
    )
