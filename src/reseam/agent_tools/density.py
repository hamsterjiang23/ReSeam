"""Uniform owner scales restore adjacent texel density without changing chart shape."""

import math
import time

import numpy as np
from scipy.optimize import LinearConstraint, minimize

from ..budget_state import BudgetState, owners_and_members, pack_budget_state
from ..validation import jacobians
from .state import check_deadline


def balance_density(
    state: BudgetState, maximum_ratio: float, resolution: int, gap_pixels: float,
    border_pixels: float, deadline: float,
) -> tuple[BudgetState, dict[str, object]]:
    """Minimize squared log scale with exact adjacent-face linear inequalities.

    Owner scale is shared by all members. A same-owner conflict cannot be
    repaired by scaling and is returned as evidence, never relaxed.
    """
    check_deadline(deadline)
    if not math.isfinite(maximum_ratio) or maximum_ratio <= 1:
        raise ValueError("DENSITY_BALANCE_REQUIRES_RATIO_GREATER_THAN_ONE")
    owner, _ = owners_and_members(state)
    unique, compact = np.unique(owner, return_inverse=True)
    density = np.sqrt(np.abs(np.linalg.det(jacobians(state.surface))))
    if np.any(density <= 0) or not np.isfinite(density).all():
        raise ValueError("DENSITY_BALANCE_DEGENERATE_INPUT")
    logarithms = np.log(density)
    limit = math.log(maximum_ratio) - 1e-8
    bounds: dict[tuple[int, int], list[float]] = {}
    conflicts: list[list[int]] = []
    for adjacent in state.surface.mesh.edge_faces:
        if len(adjacent) != 2:
            continue
        first, second = adjacent[0][0], adjacent[1][0]
        a, b = int(compact[first]), int(compact[second])
        difference = float(logarithms[first] - logarithms[second])
        if a == b:
            if abs(difference) > math.log(maximum_ratio) + 1e-9:
                conflicts.append([first, second])
            continue
        if a > b:
            a, b, difference = b, a, -difference
        bound = bounds.setdefault((a, b), [-math.inf, math.inf])
        bound[0] = max(bound[0], -limit - difference)
        bound[1] = min(bound[1], limit - difference)
    if conflicts or any(lo > hi for lo, hi in bounds.values()):
        return state.copy(), {"status": "REJECTED", "reason": "OWNER_SCALING_CANNOT_RESOLVE_LOCAL_DENSITY",
                              "same_owner_conflicts": conflicts}
    if not bounds:
        return state.copy(), {"status": "UNCHANGED", "owner_count": len(unique)}
    matrix = np.zeros((len(bounds), len(unique)))
    low, high = np.zeros(len(bounds)), np.zeros(len(bounds))
    for row, ((a, b), (lo, hi)) in enumerate(bounds.items()):
        matrix[row, a], matrix[row, b], low[row], high[row] = 1, -1, lo, hi

    def objective(values: np.ndarray) -> float:
        check_deadline(deadline)
        return float(values @ values) / 2

    def derivative(values: np.ndarray) -> np.ndarray:
        check_deadline(deadline)
        return values

    started = time.monotonic()
    solved = minimize(objective, np.zeros(len(unique)), jac=derivative, method="SLSQP",
                      # SciPy accepts vector bounds; its installed stub incorrectly says scalar.
                      constraints=[LinearConstraint(matrix, low, high)],  # pyright: ignore[reportArgumentType]
                      options={"maxiter": 200, "ftol": 1e-12})
    values = np.asarray(solved.x, dtype=float)
    violation = max(float(np.max(low - matrix @ values)), float(np.max(matrix @ values - high)), 0.)
    if not np.isfinite(values).all() or violation > 1e-9:
        return state.copy(), {"status": "REJECTED", "reason": "DENSITY_SCALE_SOLVE_FAILED",
                              "solver_message": str(solved.message), "maximum_violation": violation}
    result = state.copy()
    used: set[int] = set()
    for index in range(len(unique)):
        ids = np.unique(result.surface.uv_faces[compact == index])
        if used.intersection(ids.tolist()):
            raise ValueError("UV_ID_SHARED_BETWEEN_DIFFERENT_OWNERS")
        used.update(ids.tolist())
        center = result.surface.uv[ids].mean(axis=0)
        result.surface.uv[ids] = (result.surface.uv[ids] - center) * math.exp(float(values[index])) + center
    result.invalidate()
    result = pack_budget_state(result, resolution, gap_pixels=gap_pixels, border_pixels=border_pixels,
                               density_normalize=False)
    return result, {"status": "SCALED", "owner_count": len(unique), "constraints": len(bounds),
                    "maximum_log_violation": violation, "log_scales": values.tolist(),
                    "owner_ids": unique.tolist(), "wall_seconds": time.monotonic() - started,
                    "shape_preserved": True, "shared_member_relative_mapping_preserved": True}
