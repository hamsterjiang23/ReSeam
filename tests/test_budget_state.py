import numpy as np
import pytest

from reseam.budget_state import (
    BudgetState,
    contact_motion_ties,
    embed_region,
    exclusive_faces,
    fingerprint,
    pack_budget_state,
    planar_merge,
    validate_budget_geometry,
    validate_budget_safety,
)
from reseam.joint_types import Correspondence, ReuseGroup
from reseam.mesh import corner_map, make_mesh, state_from_corner_uv
from reseam.types import MeshState


def _errors(result: dict[str, object]) -> list[str]:
    raw = result["errors"]
    assert isinstance(raw, list)
    errors: list[str] = []
    for error in raw:
        assert isinstance(error, str)
        errors.append(error)
    return errors


def square() -> BudgetState:
    mesh = make_mesh(np.array([[0., 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0]]),
                     np.array([[0, 1, 2], [0, 2, 3]], dtype=np.int64))
    mapping = corner_map(mesh, frozenset())
    uv = np.zeros((int(mapping.max()) + 1, 2))
    uv[mapping.reshape(-1)] = mesh.vertices[mesh.faces.reshape(-1), :2]
    return BudgetState(MeshState(mesh, frozenset(), uv, mapping))


def test_pack_whole_mesh_identity_and_geometry() -> None:
    initial = square()
    before = fingerprint(initial)
    packed = pack_budget_state(initial, 64, gap_pixels=2, border_pixels=2)
    assert fingerprint(initial) == before
    assert np.array_equal(packed.surface.mesh.faces, initial.surface.mesh.faces)
    assert validate_budget_geometry(packed, 64, gap_pixels=2, border_pixels=2)["valid"]


def test_whole_face_exception_and_seam_deletion() -> None:
    initial = square()
    separated = exclusive_faces(initial, (0,))
    assert len(separated.surface.mesh.faces) == 2
    assert len(separated.surface.seams) == 1
    edge = next(iter(separated.surface.seams))
    merged = planar_merge(separated, edge)
    assert not merged.surface.seams
    packed = pack_budget_state(merged, 64, gap_pixels=2, border_pixels=2)
    assert validate_budget_geometry(packed, 64, gap_pixels=2, border_pixels=2)["valid"]
    assert not initial.surface.seams


def test_full_copy_rolls_back_texture_and_evidence() -> None:
    initial = square()
    initial.texture = np.ones((4, 4, 10))
    initial.evidence = {"gate": {"valid": True}}
    candidate = initial.copy()
    candidate.surface.uv[:] = 0
    assert candidate.texture is not None
    candidate.texture[:] = 0
    gate = candidate.evidence["gate"]
    assert isinstance(gate, dict)
    gate["valid"] = False
    assert initial.texture is not None and initial.texture.min() == 1
    assert initial.evidence == {"gate": {"valid": True}}
    candidate.invalidate()
    assert candidate.texture is None and not candidate.evidence


def test_unauthorized_overlapping_independent_islands_rejected() -> None:
    state = exclusive_faces(square(), (0,))
    corners = np.array([[[.1, .1], [.8, .1], [.8, .8]], [[.1, .1], [.8, .1], [.8, .8]]])
    state.surface = state_from_corner_uv(state.surface.mesh, corners, corner_ids=np.arange(6).reshape(2, 3))
    verdict = validate_budget_geometry(state, 64, maximum_anisotropy=100, mean_shape_limit=100,
                                       maximum_density_ratio=100)
    assert not verdict["valid"]
    assert any("UNAUTHORIZED_OVERLAP" in error for error in _errors(verdict))


def test_embed_rejects_subset_identity_change() -> None:
    initial = square()
    with pytest.raises(ValueError, match="IDENTITY"):
        embed_region(initial, initial.surface, (0,), None)


def test_dissolve_mirrored_group_keeps_all_original_faces() -> None:
    mesh = make_mesh(np.array([[-1., 0, 0], [-1, 1, 0], [-1, 0, 1],
                               [1., 0, 0], [1, 1, 0], [1, 0, 1]]),
                     np.array([[0, 1, 2], [3, 5, 4]], dtype=np.int64))
    corners = np.array([[[0., 0], [1, 0], [0, 1]], [[0., 0], [0, 1], [1, 0]]])
    surface = state_from_corner_uv(mesh, corners, corner_ids=np.arange(6).reshape(2, 3))
    state = BudgetState(surface, (ReuseGroup("pair", (0,), (1,), (1., 0, 0), 0),))
    exclusive = exclusive_faces(state, (0,))
    assert not exclusive.groups and len(exclusive.surface.mesh.faces) == 2
    packed = pack_budget_state(exclusive, 64, gap_pixels=2, border_pixels=2)
    assert validate_budget_geometry(packed, 64, gap_pixels=2, border_pixels=2)["valid"]


def test_motion_ties_keep_copy_ids_and_require_owner_original_and_exact_uv() -> None:
    mesh = make_mesh(np.array([[0., 0, 0], [1, 0, 0], [0, 1, 0], [-1, 0, 0], [0, -1, 0]]),
                     np.array([[0, 1, 2], [0, 3, 4]], dtype=np.int64))
    mapping = corner_map(mesh, frozenset())
    uv = np.zeros((6, 2))
    uv[mapping.reshape(-1)] = mesh.vertices[mesh.faces.reshape(-1), :2]
    state = BudgetState(MeshState(mesh, frozenset(), uv, mapping))
    first, second = int(mapping[0, 0]), int(mapping[1, 0])
    assert first != second
    assert contact_motion_ties(state)[first] != contact_motion_ties(state)[second]
    state.groups = (ReuseGroup("authorized", (0,), (1,), (1., 0, 0), 0),)
    before = state.surface.uv_faces.copy()
    assert contact_motion_ties(state)[first] == contact_motion_ties(state)[second]
    assert np.array_equal(before, state.surface.uv_faces)
    state.surface.uv[second, 0] += 1e-14
    assert contact_motion_ties(state)[first] != contact_motion_ties(state)[second]


def differently_triangulated_overlay() -> BudgetState:
    mesh = make_mesh(np.array([[-1., 0, 0], [-1, 1, 0], [-1, 1, 1], [-1, 0, 1],
                               [1., 0, 0], [1, 1, 0], [1, 1, 1], [1, 0, 1]]),
                     np.array([[0, 1, 2], [0, 2, 3], [4, 7, 5], [5, 7, 6]], dtype=np.int64))
    corners = .1 + .7 * mesh.vertices[mesh.faces, 1:]
    surface = state_from_corner_uv(mesh, corners, seam_ids=frozenset())
    return BudgetState(surface, (ReuseGroup("different", (0, 1), (2, 3), (1., 0, 0), 0),))


def test_full_validator_authorizes_different_triangulation_at_all_cell_vertices() -> None:
    state = differently_triangulated_overlay()
    before = fingerprint(state)
    verdict = validate_budget_geometry(state, 64)
    assert verdict["valid"], verdict["errors"]
    assert verdict["authorized_overlap_area"] == pytest.approx(.49)
    assert verdict["overlap_cells"] == 4
    assert verdict["nominal_reuse_groups"] == verdict["active_reuse_groups"] == 1
    assert verdict["active_reuse_group_ids"] == ["different"]
    assert fingerprint(state) == before
    assert not state.surface.mesh.vertices.flags.writeable
    assert not state.surface.mesh.faces.flags.writeable


def test_same_group_identity_does_not_authorize_wrong_reflected_geometry() -> None:
    state = differently_triangulated_overlay()
    vertices = state.surface.mesh.vertices.copy()
    vertices[4:, 0] += .1
    changed = make_mesh(vertices, state.surface.mesh.faces)
    state.surface = MeshState(changed, state.surface.seams, state.surface.uv, state.surface.uv_faces)
    verdict = validate_budget_geometry(state, 64)
    assert not verdict["valid"]
    assert any(error.startswith("OVERLAP_CORRESPONDENCE:") for error in _errors(verdict))
    assert verdict["authorized_overlap_area"] == 0
    assert verdict["nominal_reuse_groups"] == 1
    assert verdict["active_reuse_groups"] == 0


def test_group_permission_without_overlap_is_reported_as_nominal_only() -> None:
    state = differently_triangulated_overlay()
    state.surface.uv = .1 + (state.surface.uv - .1) * (2 / 7)
    second_ids = np.unique(state.surface.uv_faces[2:])
    state.surface.uv[second_ids] += .45
    verdict = validate_budget_geometry(state, 64)
    assert verdict["valid"], verdict["errors"]
    assert verdict["nominal_reuse_groups"] == 1
    assert verdict["active_reuse_groups"] == 0
    assert verdict["authorized_overlap_area"] == 0


def test_positive_unauthorized_overlap_below_legacy_area_cutoff_is_rejected() -> None:
    mesh = make_mesh(np.array([[0., 0, 0], [1, 0, 0], [0, 1, 0],
                               [2., 0, 0], [3, 0, 0], [2, 1, 0]]),
                     np.arange(6, dtype=np.int64).reshape(2, 3))
    epsilon = 1e-7
    corners = np.array([[[.1, .1], [.4, .1], [.1, .4]],
                        [[.4 - epsilon, .1], [.7, .1], [.4 - epsilon, .4]]])
    state = BudgetState(state_from_corner_uv(mesh, corners, seam_ids=frozenset()))
    verdict = validate_budget_geometry(state, 64, gap_pixels=0)
    assert not verdict["valid"]
    assert "UNAUTHORIZED_OVERLAP:0:1" in _errors(verdict)
    assert verdict["positive_overlap_cells"] == 1
    protocol = verdict["geometry_protocol"]
    assert isinstance(protocol, dict)
    assert protocol["positive_overlap_area_floor"] == 0


def test_exact_original_point_contact_is_legal_without_welding_distinct_copies() -> None:
    mesh = make_mesh(np.array([[0., 0, 0], [1, 0, 0], [0, 1, 0], [-1, 0, 0], [0, -1, 0]]),
                     np.array([[0, 1, 2], [0, 3, 4]], dtype=np.int64))
    corners = .5 + .3 * mesh.vertices[mesh.faces, :2]
    state = BudgetState(state_from_corner_uv(mesh, corners, seam_ids=frozenset()))
    mapping = state.surface.uv_faces.copy()
    assert mapping[0, 0] != mapping[1, 0]
    assert np.array_equal(state.surface.uv[mapping[0, 0]], state.surface.uv[mapping[1, 0]])
    verdict = validate_budget_geometry(state, 64, gap_pixels=0)
    assert verdict["valid"], verdict["errors"]
    assert np.array_equal(state.surface.uv_faces, mapping)
    assert len(state.surface.uv) == 6


def test_point_proximity_cannot_authorize_unrelated_original_vertices() -> None:
    mesh = make_mesh(np.array([[0., 0, 0], [1, 0, 0], [0, 1, 0],
                               [0., 0, 1], [-1, 0, 1], [0, -1, 1]]),
                     np.arange(6, dtype=np.int64).reshape(2, 3))
    corners = .5 + .3 * mesh.vertices[mesh.faces, :2]
    state = BudgetState(state_from_corner_uv(mesh, corners, seam_ids=frozenset()))
    verdict = validate_budget_geometry(state, 64, gap_pixels=0)
    assert "UNAUTHORIZED_CONTACT:0:1" in _errors(verdict)


def test_exact_original_edge_contact_keeps_seam_and_its_distinct_uv_copies() -> None:
    state = square()
    corners = .1 + .7 * state.surface.uv[state.surface.uv_faces]
    state.surface = state_from_corner_uv(state.surface.mesh, corners,
                                        corner_ids=np.arange(6, dtype=np.int64).reshape(2, 3))
    before = state.surface.uv_faces.copy()
    assert len(state.surface.seams) == 1
    assert len(state.surface.uv) == 6
    verdict = validate_budget_geometry(state, 64, gap_pixels=0)
    assert verdict["valid"], verdict["errors"]
    assert np.array_equal(state.surface.uv_faces, before)
    assert len(state.surface.seams) == 1


def test_area_weighted_shape_tail_rejects_small_region_below_mean_limit() -> None:
    # The distorted region is 3% of original area; its CVaR95 is 0.15,
    # although its global mean is 0.0075 and its weighted P95 is zero.
    small = np.sqrt(.03 / .97)
    mesh = make_mesh(np.array([[0., 0, 0], [1, 0, 0], [0, 1, 0],
                               [2., 0, 0], [2 + small, 0, 0], [2, small, 0]]),
                     np.arange(6, dtype=np.int64).reshape(2, 3))
    corners = np.array([[[.1, .1], [.5, .1], [.1, .5]],
                        [[.65, .65], [.65 + .4 * small * 2, .65], [.65, .65 + .4 * small]]])
    state = BudgetState(state_from_corner_uv(mesh, corners, seam_ids=frozenset()))
    verdict = validate_budget_geometry(state, 64)
    assert not verdict["valid"]
    assert verdict["errors"] == ["TAIL_SHAPE_LIMIT"]
    assert verdict["mean_shape_distortion"] == pytest.approx(.0075)
    assert verdict["shape_p95"] == pytest.approx(0)
    assert verdict["shape_cvar95"] == pytest.approx(.15)
    assert verdict["shape_max"] == pytest.approx(.25)
    assert verdict["face_shape_distortion"] == pytest.approx([0, .25])
    assert verdict["face_anisotropy"] == pytest.approx([1, 2])


def test_safety_H_excludes_recoverable_anisotropy_and_density_quality() -> None:
    state = exclusive_faces(square(), (0,))
    state = exclusive_faces(state, (1,))
    corners = np.array([[[.1, .1], [.2, .1], [.2, .2]],
                        [[.4, .4], [.9, .6], [.4, .6]]])
    state.surface = state_from_corner_uv(state.surface.mesh, corners, seam_ids=state.surface.seams)
    safety = validate_budget_safety(state, 64)
    assert safety["valid"], safety["errors"]
    assert not safety["quality_constraints_checked"]
    assert validate_budget_geometry(state, 64, quality_constraints=False)["valid"]
    final = validate_budget_geometry(state, 64)
    assert not final["valid"]
    assert "ANISOTROPY_LIMIT" in _errors(final)
    assert "NEIGHBOR_DENSITY_LIMIT" in _errors(final)


def test_safety_H_still_rejects_inversion_and_original_corner_topology_change() -> None:
    state = square()
    state.surface.uv = .1 + .7 * state.surface.uv
    state.surface.uv[:, 0] = 1 - state.surface.uv[:, 0]
    assert validate_budget_safety(state, 64)["errors"] == ["FLIPPED_OR_DEGENERATE"]
    state = square()
    state.surface.uv_faces[1, 0] = state.surface.uv_faces[1, 1]
    assert "UV_COPY_CONNECTIVITY_MISMATCH" in _errors(validate_budget_safety(state, 64))


@pytest.mark.parametrize("released", [(0,), (0, 1)])
def test_exclusive_transaction_removes_stale_records_for_changed_or_dissolved_domains(
    released: tuple[int, ...], caplog: pytest.LogCaptureFixture,
) -> None:
    initial = differently_triangulated_overlay()
    mesh = initial.surface.mesh
    vertices = np.vstack((mesh.vertices, mesh.vertices + [0, 0, 3]))
    faces = np.vstack((mesh.faces, mesh.faces + len(mesh.vertices)))
    whole = make_mesh(vertices, faces)
    corners = np.vstack((initial.surface.uv[initial.surface.uv_faces],
                         initial.surface.uv[initial.surface.uv_faces] + .05))
    initial.surface = state_from_corner_uv(whole, corners, seam_ids=frozenset())
    initial.groups += (ReuseGroup("untouched", (4, 5), (6, 7), (1., 0, 0), 0),)
    barycentric = (1 / 3, 1 / 3, 1 / 3)
    initial.correspondences = tuple(Correspondence(source, barycentric, target, barycentric, 1)
                                    for source, target in ((0, 2), (1, 3), (4, 6), (5, 7)))
    initial.correspondence_records = (
        {"group": "different", "source_original_face_id": 0, "target_original_face_id": 2},
        {"group_id": "different", "source_face": 1, "target_face": 3, "round_trip_source_face": 1},
        {"group": "untouched", "source_original_face_id": 4, "target_original_face_id": 6},
        {"group_id": "untouched", "source_face": 5, "target_face": 7, "round_trip_source_face": 5},
        {"unsupported_legacy_association": True},
        {"group_id": "untouched", "source_face": 4, "target_face": 7},
    )
    initial.evidence = {"old_gate": True}
    before = initial.copy()
    with caplog.at_level("INFO", logger="reseam.budget_state"):
        changed = exclusive_faces(initial, released)
    assert len(changed.correspondence_records) == 2
    assert changed.correspondence_records == before.correspondence_records[2:4]
    assert all(record.get("group_id", record.get("group")) == "untouched"
               for record in changed.correspondence_records)
    assert all(item.source_face not in released and item.target_face not in released
               for item in changed.correspondences)
    if released == (0, 1):
        assert all(item.source_face not in (2, 3) and item.target_face not in (2, 3)
                   for item in changed.correspondences)
        assert all(group.group_id != "different" for group in changed.groups)
    cleanup = changed.evidence["correspondence_record_cleanup"]
    assert isinstance(cleanup, dict)
    assert cleanup["removed_count"] == 4
    assert cleanup["reason_counts"] == {
        "REUSE_GROUP_DOMAIN_CHANGED_OR_DISSOLVED": 2,
        "UNSUPPORTED_ASSOCIATION_RECORD_SCHEMA": 1,
        "ASSOCIATION_NOT_SUPPORTED_BY_SURVIVING_DOMAIN": 1,
    }
    assert "old_gate" not in changed.evidence
    assert "correspondence record invalidation" in caplog.text
    assert initial.correspondence_records == before.correspondence_records
    assert initial.correspondences == before.correspondences
    assert initial.evidence == before.evidence
    assert np.array_equal(initial.surface.uv_faces, before.surface.uv_faces)
    changed.correspondence_records[0]["mutated_candidate"] = True
    assert "mutated_candidate" not in initial.correspondence_records[2]
