"""Small geometric checks for the independent upstream edge-flip variant."""

import numpy as np
import pytest

from reseam import bake_topology
from reseam.bake_topology import BakeTopologyOptions, bake_mesh_quality, optimize_bake_topology
from reseam.mesh import make_mesh
from reseam.types import FloatArray, IntArray, Mesh


def _quad(height: float = 0, scale: float = 1) -> Mesh:
    vertices = np.array([[0., 0, 0], [2, 0, 0], [1, 1, height], [0, 1, 0]]) * scale
    faces = np.array([[0, 1, 3], [1, 2, 3]], dtype=np.int64)
    return make_mesh(vertices, faces)


def _reasons(result: bake_topology.BakeTopologyResult) -> list[str]:
    reasons: list[str] = []
    for operation in result.operations:
        reason = operation["reason"]
        assert isinstance(reason, str)
        reasons.append(reason)
    return reasons


@pytest.mark.parametrize("scale", [1e-6, 1, 1e6])
def test_planar_flip_improves_worst_triangle_with_immutable_fixed_positions(scale: float) -> None:
    parent = _quad(scale=scale)
    before_vertices, before_faces = parent.vertices.copy(), parent.faces.copy()
    result = optimize_bake_topology(parent)
    assert result.diagnostics["accepted_operations"] == 1
    assert result.diagnostics["status"] == "ANGLE_GOAL_REACHED"
    assert result.before["minimum_angle_degrees"] == pytest.approx(18.4349488229)
    assert result.after["minimum_angle_degrees"] == pytest.approx(45)
    assert result.before["sliver_count"] == 1
    assert result.after["sliver_count"] == 0
    assert result.diagnostics["surface_geometry_changed"] is False
    assert result.diagnostics["face_count_preserved"] is True
    assert result.diagnostics["original_face_corner_identity_preserved"] is False
    assert result.diagnostics["face_ancestry"] == [[0, 1], [0, 1]]
    assert result.diagnostics["parent_mesh_sha256"] != result.diagnostics["variant_mesh_sha256"]
    assert np.array_equal(result.mesh.vertices, before_vertices)
    assert np.array_equal(parent.vertices, before_vertices)
    assert np.array_equal(parent.faces, before_faces)
    assert not parent.vertices.flags.writeable and not parent.faces.flags.writeable
    assert not np.shares_memory(parent.vertices, result.mesh.vertices)
    assert np.array_equal(result.mesh.faces, [[0, 1, 2], [0, 2, 3]])
    operation = result.operations[0]
    assert operation["original_parent_face_ids"] == [0, 1]
    assert operation["minimum_orientation_dot"] == pytest.approx(1)
    assert result.runtime_seconds >= 0


def test_reproducible_proposals_and_distinct_parent_identity() -> None:
    parent = _quad()
    first, second = optimize_bake_topology(parent), optimize_bake_topology(parent)
    assert first.operations == second.operations
    assert first.before == second.before and first.after == second.after
    assert first.diagnostics == second.diagnostics
    assert np.array_equal(first.mesh.faces, second.mesh.faces)


def test_sharp_fold_rejects_flip_without_mutation() -> None:
    parent = _quad(height=1)
    result = optimize_bake_topology(parent, BakeTopologyOptions(minimum_angle_goal_degrees=50))
    assert result.diagnostics["accepted_operations"] == 0
    assert _reasons(result) == ["SHARP_FEATURE_GUARD"]
    assert np.array_equal(parent.faces, result.mesh.faces)
    assert np.array_equal(parent.vertices, result.mesh.vertices)
    assert result.before == result.after


def test_mild_nonplanar_flip_reports_changed_surface_and_parent_information() -> None:
    result = optimize_bake_topology(_quad(height=.05))
    assert result.diagnostics["accepted_operations"] == 1
    assert result.diagnostics["surface_geometry_changed"] is True
    assert result.diagnostics["fixed_vertex_positions"] is True
    operation = result.operations[0]
    nonplanarity = operation["nonplanarity_fraction"]
    assert isinstance(nonplanarity, float) and nonplanarity > 0
    assert operation["original_parent_face_ids"] == [0, 1]
    area_change = operation["local_surface_area_change_fraction"]
    normal_change = operation["maximum_normal_change_degrees"]
    assert isinstance(area_change, float) and area_change <= .01
    assert isinstance(normal_change, float) and normal_change <= 15
    assert result.diagnostics["global_self_intersection_checked"] is False
    assert result.diagnostics["bake_quality_verified"] is False


def test_zero_normal_change_guard_rejects_nonplanar_patch() -> None:
    result = optimize_bake_topology(_quad(height=.05), BakeTopologyOptions(normal_change_degrees=0))
    assert result.diagnostics["accepted_operations"] == 0
    assert _reasons(result) == ["NORMAL_CHANGE_GUARD"]


def test_local_surface_area_bound_rejects_nonplanar_diagonal_change() -> None:
    parent = _quad(height=.05)
    result = optimize_bake_topology(parent, BakeTopologyOptions(maximum_local_area_change_fraction=0))
    assert result.diagnostics["accepted_operations"] == 0
    assert _reasons(result) == ["LOCAL_SURFACE_AREA_CHANGE_GUARD"]
    assert np.array_equal(parent.faces, result.mesh.faces)


def test_opposite_edge_already_present_is_rejected() -> None:
    vertices = np.array([[0., 0, 0], [2, 0, 0], [1, 1, 0], [0, 1, 1]])
    faces = np.array([[0, 2, 1], [0, 1, 3], [1, 2, 3], [2, 0, 3]], dtype=np.int64)
    parent = make_mesh(vertices, faces)
    result = optimize_bake_topology(parent, BakeTopologyOptions(minimum_angle_goal_degrees=60))
    assert result.diagnostics["accepted_operations"] == 0
    assert set(_reasons(result)) == {"OPPOSITE_EDGE_ALREADY_EXISTS"}
    assert np.array_equal(parent.faces, result.mesh.faces)


def test_disconnected_vertex_link_rejects_an_otherwise_good_flip() -> None:
    quad = _quad()
    vertices = np.vstack((quad.vertices, [0, -1, 0], [-1, -1, 0]))
    faces = np.vstack((quad.faces, [0, 4, 5]))
    parent = make_mesh(vertices, faces)
    result = optimize_bake_topology(parent)
    assert result.diagnostics["accepted_operations"] == 0
    assert _reasons(result) == ["NONMANIFOLD_VERTEX_LINK"]
    assert np.array_equal(parent.faces, result.mesh.faces)


def test_degenerate_new_triangle_is_rejected_before_committing() -> None:
    vertices = np.array([[0., 0, 0], [1, 0, 0], [2, 0, 0], [0, 1, 0]])
    faces = np.array([[0, 1, 3], [1, 2, 3]], dtype=np.int64)
    parent = make_mesh(vertices, faces)
    result = optimize_bake_topology(parent)
    assert _reasons(result) == ["DEGENERATE_OR_TOO_SMALL_PROPOSAL"]
    assert result.diagnostics["accepted_operations"] == 0
    assert np.array_equal(parent.faces, result.mesh.faces)


def test_concave_new_diagonal_cannot_flip_face_orientation() -> None:
    vertices = np.array([[0., 0, 0], [.2, .2, 0], [1, 0, 0], [0, 1, 0]])
    faces = np.array([[0, 1, 3], [1, 2, 3]], dtype=np.int64)
    parent = make_mesh(vertices, faces)
    result = optimize_bake_topology(parent)
    assert _reasons(result) == ["ORIENTATION_FLIP"]
    assert result.diagnostics["accepted_operations"] == 0
    assert np.array_equal(parent.faces, result.mesh.faces)


def test_atomic_validation_failure_rolls_back_all_parent_arrays(monkeypatch: pytest.MonkeyPatch) -> None:
    parent = _quad()
    original_make_mesh = make_mesh

    def rejecting_proposal(vertices: FloatArray, faces: IntArray) -> Mesh:
        if not np.array_equal(faces, parent.faces):
            raise ValueError("injected atomic validation failure")
        return original_make_mesh(vertices, faces)

    monkeypatch.setattr(bake_topology, "make_mesh", rejecting_proposal)
    before_vertices, before_faces = parent.vertices.copy(), parent.faces.copy()
    result = optimize_bake_topology(parent)
    assert _reasons(result) == ["MAKE_MESH_REJECTED"]
    assert result.operations[0]["validation_error"] == "injected atomic validation failure"
    assert result.diagnostics["accepted_operations"] == 0
    assert np.array_equal(parent.vertices, before_vertices)
    assert np.array_equal(parent.faces, before_faces)
    assert np.array_equal(result.mesh.faces, before_faces)
    assert result.diagnostics["parent_mesh_sha256"] == result.diagnostics["variant_mesh_sha256"]


def test_boundary_and_coincident_vertices_are_reported_without_manufacturing_repair() -> None:
    vertices = np.array([[0., 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 0]])
    parent = make_mesh(vertices, np.array([[0, 1, 2]], dtype=np.int64))
    result = optimize_bake_topology(parent, BakeTopologyOptions(minimum_angle_goal_degrees=60))
    assert result.diagnostics["accepted_operations"] == 0
    assert result.diagnostics["skipped_edge_counts"] == {"BOUNDARY_OR_NONMANIFOLD_EDGE": 3}
    assert result.diagnostics["coincident_vertex_count"] == 1
    assert result.diagnostics["coincident_vertices_repaired"] is False
    assert result.diagnostics["collapse_implemented"] is False
    assert result.after["sliver_count"] == 1
    assert result.diagnostics["status"] == "NO_ACCEPTABLE_IMPROVING_FLIP"


def test_zero_budget_returns_unchanged_independent_variant() -> None:
    parent = _quad()
    result = optimize_bake_topology(parent, BakeTopologyOptions(operation_budget=0))
    assert not result.operations
    assert result.diagnostics["status"] == "OPERATION_BUDGET_EXHAUSTED"
    assert result.before == result.after
    assert np.array_equal(parent.faces, result.mesh.faces)


def test_attempt_budget_keeps_best_valid_output_and_reports_unreached_goal() -> None:
    result = optimize_bake_topology(_quad(), BakeTopologyOptions(
        minimum_angle_goal_degrees=60, operation_budget=1))
    assert result.diagnostics["status"] == "OPERATION_BUDGET_EXHAUSTED"
    assert result.diagnostics["attempted_operations"] == 1
    assert result.diagnostics["accepted_operations"] == 1
    assert result.after["minimum_angle_degrees"] == pytest.approx(45)
    assert result.after["sliver_count"] == 2


def test_quality_shape_R_is_one_for_equilateral_triangle() -> None:
    vertices = np.array([[0., 0, 0], [1, 0, 0], [.5, np.sqrt(3) / 2, 0]])
    parent = make_mesh(vertices, np.array([[0, 1, 2]], dtype=np.int64))
    quality = bake_mesh_quality(parent)
    assert quality["shape_R_max"] == pytest.approx(1)
    assert quality["shape_R_area_mean"] == pytest.approx(1)
    assert quality["minimum_angle_degrees"] == pytest.approx(60)


@pytest.mark.parametrize("field", ["feature", "area", "normal"])
def test_invalid_numeric_options_rejected(field: str) -> None:
    with pytest.raises(ValueError, match="INVALID_BAKE_TOPOLOGY_OPTIONS"):
        if field == "feature":
            BakeTopologyOptions(feature_angle_degrees=90)
        elif field == "area":
            BakeTopologyOptions(relative_area_floor=0)
        else:
            BakeTopologyOptions(normal_change_degrees=float("nan"))
