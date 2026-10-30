"""Whole-face release preserving authored UV patches and bounded numerical repair."""

import time
from collections.abc import Callable

import numpy as np
from scipy.optimize import Bounds, minimize
from shapely import Polygon

from ..budget_state import BudgetState, contact_motion_ties, exclusive_faces
from ..mesh import state_from_corner_uv
from ..types import FloatArray
from ..validation import jacobians


def release_patches(state: BudgetState, face_ids: tuple[int, ...]) -> tuple[BudgetState, dict[str, object]]:
    """Detach old-UV patches; record legacy dissolved-group orientation changes.

    Unselected UV stays fixed except the existing exclusive_faces convention:
    dissolving a group reflects its remaining negative member as a whole. This
    is a compatibility convention, not a requirement of the signed-chart gate.
    """
    result = exclusive_faces(state, face_ids)
    selected = set(face_ids)
    source = state.surface
    adjacent = {face: set() for face in selected}
    for entries in source.mesh.edge_faces:
        if len(entries) != 2:
            continue
        first, second = entries[0][0], entries[1][0]
        if (first in selected and second in selected
                and len(set(source.uv_faces[first]) & set(source.uv_faces[second])) == 2):
            adjacent[first].add(second)
            adjacent[second].add(first)
    components: list[list[int]] = []
    unseen = set(selected)
    while unseen:
        stack, component = [min(unseen)], []
        unseen.remove(stack[0])
        while stack:
            face = stack.pop()
            component.append(face)
            neighbors = sorted(adjacent[face] & unseen)
            unseen.difference_update(neighbors)
            stack.extend(neighbors)
        components.append(sorted(component))
    corners = result.surface.uv[result.surface.uv_faces].copy()
    identities = result.surface.uv_faces.copy()
    original = source.uv[source.uv_faces]
    dissolved = {group.group_id for group in state.groups}-{group.group_id for group in result.groups}
    allowed_normalization = {face for group in state.groups if group.group_id in dissolved
                             for faces, sign in ((group.first_faces, group.first_orientation),
                                                 (group.second_faces, group.second_orientation))
                             if sign < 0 for face in faces if face not in selected}
    changed_unselected = [face for face in range(len(source.mesh.faces)) if face not in selected
                          and not np.array_equal(corners[face], original[face])]
    for face in changed_unselected:
        reflected_original = original[face].copy()
        reflected_original[:, 0] *= -1
        if face not in allowed_normalization or not np.array_equal(corners[face], reflected_original):
            raise ValueError("UNEXPECTED_UNSELECTED_RELEASE_UV_CHANGE")
    signs = np.sign(np.linalg.det(jacobians(source)))
    next_id = int(identities.max())+1
    reflected = []
    for index, component in enumerate(components):
        orientation = np.unique(signs[component])
        if len(orientation) != 1 or orientation[0] == 0:
            raise ValueError("RELEASE_PATCH_INCONSISTENT_ORIENTATION")
        corners[component] = original[component]
        if orientation[0] < 0:
            corners[component, :, 0] *= -1
            reflected.append(index)
        # Keyed by explicit old identity inside one edge-connected component.
        nodes = np.unique(source.uv_faces[component])
        lookup = {int(node): next_id+i for i, node in enumerate(nodes)}
        next_id += len(nodes)
        identities[component] = [[lookup[int(node)] for node in source.uv_faces[face]] for face in component]
    result.surface = state_from_corner_uv(source.mesh, corners, corner_ids=identities)
    result.invalidate()
    return result, {"method": "preserve_original_uv_edge_connected_patches", "released_faces": list(face_ids),
                    "patches": components, "patch_count": len(components), "reflected_patch_indices": reflected,
                    "unselected_orientation_normalized_faces": changed_unselected,
                    "unselected_change_reason": "legacy exclusive orientation normalization on dissolved group",
                    "dissolved_group_ids": sorted(dissolved),
                    "geometry_modified": False, "projection_recomputed": False,
                    "identity_key": "released original-edge UV component, old UV node ID"}


Validator = Callable[[BudgetState, dict[str, object]], dict[str, object]]


def repair_packing_roundoff(
    before_pack: BudgetState, packed: BudgetState, profile: dict[str, object], deadline: float,
    validate: Validator,
) -> tuple[BudgetState, dict[str, object], dict[str, object]]:
    """Move target UV nodes by bounded ULPs, then apply the unchanged validator.

    Only cross-member cells having exactly zero area before packing are eligible.
    The separation constraints are on actual coordinates, not an overlap tolerance.
    Original identity contact ties move together. All first-member nodes stay fixed.
    """
    candidate = packed.copy()
    reference = packed.surface.uv.copy()
    previous = before_pack.surface.uv[before_pack.surface.uv_faces]
    quantum = float(np.spacing(1.0))
    maximum_step = min(128*quantum, 1e-12)
    ties = contact_motion_ties(packed)
    fixed = {int(ties[node]) for group in packed.groups
             for node in np.unique(packed.surface.uv_faces[list(group.first_faces)])}
    target_faces = {face for group in packed.groups for face in group.second_faces}
    membership = {face: (group.group_id, side) for group in packed.groups
                  for side, faces in enumerate((group.first_faces, group.second_faces)) for face in faces}
    constraints: list[tuple[int, FloatArray, float]] = []
    visits: dict[tuple[int, int], int] = {}
    attempts: list[dict[str, object]] = []
    report = validate(candidate, profile)
    evidence: dict[str, object] = {"maximum_coordinate_step": maximum_step, "quantum": quantum,
                                  "maximum_passes": 5, "attempts": attempts,
                                  "area_tolerance_added": False, "first_member_nodes_fixed": True}
    for iteration in range(5):
        if time.monotonic() >= deadline:
            raise TimeoutError("ROUNDOFF_REPAIR_DEADLINE")
        if report.get("valid") is True:
            evidence["status"] = "VALID"
            return candidate, report, evidence
        errors = report.get("hard_errors", [])
        if (not isinstance(errors, list) or not errors
                or any(not str(error).startswith("OVERLAP_CORRESPONDENCE:") for error in errors)):
            evidence.update(status="REJECTED", reason="NON_ROUNDOFF_HARD_FAILURE_OR_QUALITY_FAILURE")
            return candidate, report, evidence
        current = candidate.surface.uv[candidate.surface.uv_faces]
        cells: list[dict[str, object]] = []
        for error in errors:
            a, b = map(int, str(error).split(":")[1:])
            if membership.get(a, (None, -1))[1] != 0:
                a, b = b, a
            if (a not in membership or b not in membership or b not in target_faces
                    or membership[a][0] != membership[b][0] or membership[a][1] != 0):
                evidence.update(status="REJECTED", reason="NOT_EXPLICIT_FIRST_SECOND_CELL")
                return candidate, report, evidence
            original_area = Polygon(previous[a]).intersection(Polygon(previous[b])).area
            if original_area != 0:
                evidence.update(status="REJECTED", reason="POSITIVE_BEFORE_PACKING", faces=[a, b])
                return candidate, report, evidence
            previous_attempts = visits.get((a, b), 0)
            visits[(a, b)] = previous_attempts+1
            clearance = 0. if previous_attempts == 0 else float(4*2**(previous_attempts-1))
            master, target = current[a], current[b]
            options: list[tuple[float, FloatArray, float]] = []
            for triangle in (master, target):
                for edge in np.roll(triangle, -1, axis=0)-triangle:
                    axis = np.asarray([-edge[1], edge[0]])
                    length = float(np.linalg.norm(axis))
                    if length == 0:
                        continue
                    axis /= length
                    for direction in (axis, -axis):
                        support = float(np.max(master @ direction))
                        required = support-target @ direction
                        options.append((max(0., float(required.max())), direction.copy(), support))
            amount, axis, support = min(options, key=lambda item: item[0])
            if amount > maximum_step:
                evidence.update(status="REJECTED", reason="SEPARATION_EXCEEDS_ULP_BOUND", faces=[a, b])
                return candidate, report, evidence
            for node in candidate.surface.uv_faces[b]:
                node = int(node)
                # Start with exact contact. A surviving cell gets an explicit
                # representable clearance, still subject to the total step bound.
                lower = (support-float(reference[node] @ axis))/quantum+clearance
                if int(ties[node]) in fixed:
                    if lower > 0:
                        evidence.update(status="REJECTED", reason="SEPARATION_WOULD_MOVE_MASTER")
                        return candidate, report, evidence
                    continue
                if lower > -256:
                    constraints.append((int(ties[node]), axis, lower))
            cells.append({"faces": [a, b], "before_pack_area": original_area,
                          "before_correction_area": Polygon(master).intersection(Polygon(target)).area,
                          "axis": axis.tolist(), "support": support, "minimum_axis_step": amount,
                          "clearance_ulps": clearance})
        variables = sorted({node for node, _, _ in constraints})
        lookup = {node: index for index, node in enumerate(variables)}
        matrix = np.zeros((len(constraints), 2*len(variables)))
        lower = np.empty(len(constraints))
        for row, (node, axis, bound) in enumerate(constraints):
            index = lookup[node]*2
            matrix[row, index:index+2], lower[row] = axis, bound
        initial = np.zeros(2*len(variables))
        solved = minimize(lambda x: .5*float(x @ x), initial, jac=lambda x: x, method="SLSQP",
                          bounds=Bounds(-128., 128.),
                          constraints={"type": "ineq", "fun": lambda x, a=matrix, b=lower: a @ x-b,
                                       "jac": lambda x, a=matrix: a},
                          options={"ftol": 1e-12, "maxiter": 100})
        if not solved.success or np.min(matrix @ solved.x-lower) < -1e-7:
            evidence.update(status="REJECTED", reason="ULP_SEPARATION_QP_FAILED", solver_message=str(solved.message))
            return candidate, report, evidence
        coordinates = reference.copy()
        for node, index in lookup.items():
            coordinates[ties == node] += quantum*solved.x[index*2:index*2+2]
        delta = coordinates-reference
        maximum = float(np.linalg.norm(delta, axis=1).max())
        if maximum > maximum_step:
            evidence.update(status="REJECTED", reason="ROUNDED_STEP_EXCEEDS_ULP_BOUND")
            return candidate, report, evidence
        candidate.surface.uv = coordinates
        candidate.invalidate()
        report = validate(candidate, profile)
        changed = np.flatnonzero(np.any(delta != 0, axis=1))
        attempts.append({"pass": iteration+1, "cells": cells, "maximum_step": maximum,
                         "changes": [{"uv_node": int(node), "before": reference[node].tolist(),
                                      "after": coordinates[node].tolist(), "delta": delta[node].tolist()}
                                     for node in changed],
                         "postcheck_valid": report.get("valid"), "postcheck_errors": report.get("errors")})
    evidence["status"] = "VALID" if report.get("valid") is True else "PASS_LIMIT_REJECTED"
    return candidate, report, evidence
