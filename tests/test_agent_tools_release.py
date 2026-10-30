"""Whole-patch release and real bounded coordinate corrections keep strict gates."""

import time
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

from reseam.agent_tools import geometry
from reseam.agent_tools.geometry import geometry_report, pack_uv
from reseam.agent_tools.release import release_patches, repair_packing_roundoff
from reseam.agent_tools.state import read_object, save_snapshot
from reseam.budget_state import BudgetState, contact_motion_ties, fingerprint
from reseam.joint_types import ReuseGroup
from reseam.mesh import chart_labels, make_mesh, state_from_corner_uv
from reseam.reuse_transform import similarity_transform
from reseam.validation import jacobians


def _two_patches() -> BudgetState:
    original = np.asarray([[0., 0, 0], [1., 0, 0], [1., 1., 0], [0., 1., 0]])
    vertices = np.concatenate((original, original+[3., 0, 0]))
    faces = np.asarray([[0, 1, 2], [0, 2, 3], [4, 5, 6], [4, 6, 7]])
    corners = np.asarray([[[.1, .1], [.3, .1], [.3, .3]], [[.1, .1], [.3, .3], [.1, .3]]]*2)
    mesh = make_mesh(vertices, faces)
    group = ReuseGroup("patch", (0, 1), (2, 3), (1., 0, 0), 0, 1, 1,
                       similarity_transform([[1, 0, 0, 3], [0, 1, 0, 0], [0, 0, 1, 0]]))
    return BudgetState(state_from_corner_uv(mesh, corners, corner_ids=faces), (group,))


@pytest.mark.parametrize("negative", [False, True])
def test_adjacent_released_faces_remain_one_original_uv_patch(negative: bool) -> None:
    original = _two_patches()
    # Independent negative charts are also restored as a whole, never facewise.
    original.groups = ()
    if negative:
        original.surface.uv[:, 0] *= -1
    before = fingerprint(original)
    old = original.surface.uv[original.surface.uv_faces].copy()
    released, record = release_patches(original, (0, 1))
    labels = chart_labels(released.surface)
    assert record["patch_count"] == 1
    assert labels[0] == labels[1]
    expected = old[:2].copy()
    if negative:
        expected[:, :, 0] *= -1
    assert np.array_equal(released.surface.uv[released.surface.uv_faces[:2]], expected)
    assert np.array_equal(released.surface.uv[released.surface.uv_faces[2:]], old[2:])
    assert np.allclose(np.linalg.svd(jacobians(released.surface), compute_uv=False),
                       np.linalg.svd(jacobians(original.surface), compute_uv=False))
    assert np.array_equal(released.surface.mesh.vertices, original.surface.mesh.vertices)
    assert np.array_equal(released.surface.mesh.faces, original.surface.mesh.faces)
    assert fingerprint(original) == before


def test_whole_master_release_records_legacy_remaining_negative_member_flip() -> None:
    initial = _two_patches()
    vertices, faces = initial.surface.mesh.vertices.copy(), initial.surface.mesh.faces.copy()
    vertices[4:, 0] = 6-vertices[4:, 0]
    faces[2:] = faces[2:][:, [0, 2, 1]]
    corners = initial.surface.uv[initial.surface.uv_faces].copy()
    corners[2:] = corners[2:][:, [0, 2, 1]]
    group = replace(initial.groups[0], second_orientation=-1,
                    first_to_second=similarity_transform([[-1., 0, 0, 3], [0, 1., 0, 0], [0, 0, 1., 0]]))
    initial = BudgetState(state_from_corner_uv(make_mesh(vertices, faces), corners, corner_ids=faces), (group,))
    assert geometry_report(initial)["valid"] is True
    released, record = release_patches(initial, (0, 1))
    assert record["unselected_orientation_normalized_faces"] == [2, 3]
    assert record["dissolved_group_ids"] == ["patch"]
    assert record["unselected_change_reason"] == "legacy exclusive orientation normalization on dissolved group"
    expected = corners[2:].copy()
    expected[:, :, 0] *= -1
    assert np.array_equal(released.surface.uv[released.surface.uv_faces[2:]], expected)
    assert np.all(np.linalg.det(jacobians(released.surface)) > 0)
    assert released.groups == ()


def _vertex_contact_state() -> BudgetState:
    uv = np.asarray([[[.1, .1], [.3, .1], [.1, .3]], [[.3, .1], [.5, .1], [.5, .3]]])
    a = np.column_stack((uv[0], np.zeros(3)))
    b = np.column_stack((uv[1], np.zeros(3)))
    angle = np.pi/3
    rotation = np.asarray([[1., 0, 0], [0, np.cos(angle), -np.sin(angle)],
                            [0, np.sin(angle), np.cos(angle)]])
    b = (b-b[0]) @ rotation.T+a[1]
    master = np.concatenate((a, b[1:]))
    faces = np.asarray([[0, 1, 2], [1, 3, 4], [5, 6, 7], [6, 8, 9]])
    mesh = make_mesh(np.concatenate((master, master+[4., 0, 0])), faces)
    group = ReuseGroup("contact", (0, 1), (2, 3), (1., 0, 0), 0, 1, 1,
                       similarity_transform([[1., 0, 0, 4], [0, 1., 0, 0], [0, 0, 1., 0]]))
    return BudgetState(state_from_corner_uv(mesh, np.concatenate((uv, uv)), corner_ids=mesh.faces), (group,))


def test_ulp_repair_moves_real_target_coordinates_and_keeps_master_fixed() -> None:
    before = _vertex_contact_state()
    profile: dict[str, object] = {"resolution": 1024, "gap_pixels": 0, "border_pixels": 0}
    assert geometry_report(before, profile)["valid"] is True
    packed = before.copy()
    ties = contact_motion_ties(packed)
    nodes = np.flatnonzero(np.isin(ties, ties[packed.surface.uv_faces[3]]))
    packed.surface.uv[nodes, 0] -= 2*np.spacing(1.)
    assert geometry_report(packed, profile)["hard_valid"] is False
    fixed = packed.surface.uv[np.unique(packed.surface.uv_faces[:2])].copy()
    result, report, evidence = repair_packing_roundoff(before, packed, profile, time.monotonic()+20, geometry_report)
    assert evidence["status"] == "VALID", evidence
    assert report["valid"] is True, report
    assert np.array_equal(result.surface.uv[np.unique(result.surface.uv_faces[:2])], fixed)
    assert np.any(result.surface.uv != packed.surface.uv)
    assert np.linalg.norm(result.surface.uv-packed.surface.uv, axis=1).max() <= 128*np.spacing(1.)
    assert np.array_equal(result.surface.mesh.vertices, before.surface.mesh.vertices)
    assert np.array_equal(result.surface.mesh.faces, before.surface.mesh.faces)


def test_ulp_repair_refuses_substantive_or_preexisting_intersection() -> None:
    before = _vertex_contact_state()
    profile: dict[str, object] = {"resolution": 1024, "gap_pixels": 0, "border_pixels": 0}
    packed = before.copy()
    ties = contact_motion_ties(packed)
    nodes = np.flatnonzero(np.isin(ties, ties[packed.surface.uv_faces[3]]))
    packed.surface.uv[nodes, 0] -= .001
    _, report, evidence = repair_packing_roundoff(before, packed, profile, time.monotonic()+20, geometry_report)
    assert evidence["reason"] == "SEPARATION_EXCEEDS_ULP_BOUND"
    assert report["hard_valid"] is False
    _, _, evidence = repair_packing_roundoff(packed, packed, profile, time.monotonic()+20, geometry_report)
    assert evidence["reason"] == "POSITIVE_BEFORE_PACKING"


@pytest.mark.parametrize(("divisor", "step"), [(7, 2.), (5, 1.)])
def test_public_pack_repairs_rotated_contact_with_original_full_validator(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, divisor: int, step: float,
) -> None:
    before = _vertex_contact_state()
    angle = np.pi/divisor
    rotation = np.asarray([[np.cos(angle), -np.sin(angle)], [np.sin(angle), np.cos(angle)]])
    before.surface.uv = (before.surface.uv-[.3, .2]) @ rotation.T+[.3, .2]
    profile: dict[str, object] = {"resolution": 8192, "gap_pixels": 0, "border_pixels": 0}
    assert geometry_report(before, profile)["valid"] is True

    def rounded_pack(
        candidate: BudgetState, profile: dict[str, object], density_normalize: bool = True,
        deadline: float | None = None,
    ) -> BudgetState:
        candidate = candidate.copy()
        ties = contact_motion_ties(candidate)
        nodes = np.flatnonzero(np.isin(ties, ties[candidate.surface.uv_faces[3]]))
        candidate.surface.uv[nodes] -= step*np.spacing(1.)*rotation[:, 0]
        return candidate

    monkeypatch.setattr(geometry, "_packed", rounded_pack)
    source = save_snapshot(before, tmp_path/"source", {})
    request: dict[str, object] = {"snapshot": str(source.root), "profile": profile}
    rejected = pack_uv(request, tmp_path/"rejected", time.monotonic()+30)
    assert rejected["status"] == "REJECTED"
    request["repair_packing_roundoff"] = True
    result = pack_uv(request, tmp_path/"corrected", time.monotonic()+30)
    evidence = read_object(tmp_path/"corrected/packing_roundoff.json")
    assert result["status"] == "OK" and result["geometry_passed"] is True, evidence
    assert evidence["status"] == "VALID"
    attempts = evidence["attempts"]
    assert isinstance(attempts, list) and attempts
    if divisor == 5:
        assert any(cell["clearance_ulps"] > 0 for attempt in attempts for cell in attempt["cells"])
