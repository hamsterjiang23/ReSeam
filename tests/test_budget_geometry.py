"""Acceptance checks independent of the legacy density objective."""

import numpy as np
import pytest

from reseam.budget_geometry import validate_pair_geometry
from reseam.mesh import make_mesh
from reseam.types import IntArray, MeshState


def _errors(result: dict[str, object]) -> list[str]:
    raw = result["errors"]
    assert isinstance(raw, list)
    errors: list[str] = []
    for error in raw:
        assert isinstance(error, str)
        errors.append(error)
    return errors


def _panels() -> tuple[MeshState, IntArray, IntArray]:
    vertices = np.asarray(
        [
            [-1, 0, 0],
            [-1, 1, 0],
            [-1, 1, 1],
            [-1, 0, 1],
            [1, 0, 0],
            [1, 1, 0],
            [1, 1, 1],
            [1, 0, 1],
            [1, 0.5, 0.5],
        ],
        dtype=float,
    )
    faces = np.asarray([[0, 2, 1], [0, 3, 2], [4, 5, 8], [5, 6, 8], [6, 7, 8], [7, 4, 8]], dtype=np.int64)
    state = MeshState(make_mesh(vertices, faces), frozenset(), vertices[:, 1:] * 0.25 + 0.2, faces.copy())
    return (
        state,
        np.asarray([-1, -1, 1, 1, 1, 1], dtype=np.int64),
        np.asarray([0, 0, 1, 1, 1, 1], dtype=np.int64),
    )


def _checked(
    state: MeshState, orientations: IntArray, members: IntArray, shared: bool = True, *,
    maximum_anisotropy: float = 2, mean_shape_limit: float = .025,
    maximum_neighbor_density_ratio: float = 2,
) -> dict[str, object]:
    return validate_pair_geometry(
        state,
        orientations,
        members,
        shared=shared,
        plane_normal=(1, 0, 0),
        plane_offset=0,
        resolution=64,
        maximum_anisotropy=maximum_anisotropy,
        mean_shape_limit=mean_shape_limit,
        maximum_neighbor_density_ratio=maximum_neighbor_density_ratio,
    )


def test_actual_cells_authorize_different_triangulations_and_mirror() -> None:
    state, signs, members = _panels()
    before_vertices = state.mesh.vertices.copy()
    before_uv = state.uv.copy()
    result = _checked(state, signs, members)
    assert result["valid"], result
    assert result["overlap_cells"] == 4
    assert result["authorized_overlap_area"] == pytest.approx(0.0625)
    assert result["reuse_factor"] == pytest.approx(2)
    assert result["mean_shape_distortion"] == pytest.approx(0)
    assert result["minimum_gap_pixels"] is None
    assert result["quality_scope"] == "GEOMETRY_ONLY"
    assert np.array_equal(state.mesh.vertices, before_vertices)
    assert np.array_equal(state.uv, before_uv)


def test_shared_flag_does_not_authorize_wrong_surface_relation() -> None:
    state, signs, members = _panels()
    result = validate_pair_geometry(
        state,
        signs,
        members,
        shared=True,
        plane_normal=(1, 0, 0),
        plane_offset=0.2,
        resolution=64,
    )
    assert not result["valid"]
    assert any(error.startswith("OVERLAP_CORRESPONDENCE:") for error in _errors(result))
    assert not result["has_authorized_shared_overlap"]


def test_unshared_rejects_cross_member_overlap_and_enforces_gap() -> None:
    state, signs, members = _panels()
    overlapping = _checked(state, signs, members, shared=False)
    assert not overlapping["valid"]
    assert any(error.startswith("UNAUTHORIZED_MEMBER_CONTACT:") for error in _errors(overlapping))
    separated = state.copy()
    separated.uv[4:, 0] += 0.3
    good = _checked(separated, signs, members, shared=False)
    assert good["valid"], good
    assert good["minimum_gap_pixels"] == pytest.approx(3.2)
    close = state.copy()
    close.uv[4:, 0] += 0.26
    bad = _checked(close, signs, members, shared=False)
    assert "PACKING_GAP_LIMIT" in _errors(bad)


def test_member_flip_and_inconsistent_orientation_rejected() -> None:
    state, signs, members = _panels()
    assert "FLIPPED_OR_DEGENERATE" in _errors(_checked(state, np.ones(6, dtype=np.int64), members))
    signs[0] *= -1
    result = _checked(state, signs, members)
    assert any(error.startswith("INCONSISTENT_MEMBER_ORIENTATION:") for error in _errors(result))


def test_mean_shape_uses_per_face_scale_normalization() -> None:
    state, signs, members = _panels()
    state.uv[:4] = state.uv[:4] * 0.4
    state.uv[4:] = (state.uv[4:] - 0.2) * 0.8 + 0.5
    result = _checked(state, signs, members, shared=False)
    assert result["valid"], result
    assert result["mean_shape_distortion"] == pytest.approx(0)
    assert result["maximum_anisotropy"] == pytest.approx(1)


def test_anisotropy_and_mean_shape_reject_stretch() -> None:
    state, signs, members = _panels()
    state.uv[:, 0] *= 1.6
    result = _checked(state, signs, members, maximum_anisotropy=1.5)
    assert not result["valid"]
    assert "ANISOTROPY_LIMIT" in _errors(result)
    assert "MEAN_SHAPE_LIMIT" in _errors(result)
    assert result["mean_shape_distortion"] == pytest.approx(0.1125)


def test_neighbor_density_check_is_within_each_member() -> None:
    state, signs, members = _panels()
    state.uv[8] += [0.08, 0]
    result = _checked(
        state,
        signs,
        members,
        maximum_anisotropy=100,
        mean_shape_limit=100,
        maximum_neighbor_density_ratio=1.1,
    )
    assert not result["valid"]
    assert "NEIGHBOR_DENSITY_LIMIT" in _errors(result)
    assert result["neighbor_density_failed_faces"]


def test_atlas_border_and_nonfinite_profile_rejected() -> None:
    state, signs, members = _panels()
    state.uv -= 0.2
    assert "ATLAS_BORDER_LIMIT" in _errors(_checked(state, signs, members))
    invalid = _checked(state, signs, members, maximum_anisotropy=float("nan"))
    assert invalid["errors"] == ["INVALID_PAIR_GEOMETRY_PROFILE_OR_MEMBERS"]


def test_unauthorized_self_intersection_not_hidden_by_member_sign() -> None:
    state, signs, members = _panels()
    changed = state.copy()
    changed.uv[8] = [0.7, 0.9]
    result = _checked(changed, signs, members)
    assert not result["valid"]


def test_original_bbox_scale_includes_vertices_outside_roi() -> None:
    state, signs, members = _panels()
    vertices = np.vstack((state.mesh.vertices, [100, 0, 0]))
    state.mesh = make_mesh(vertices, state.mesh.faces)
    result = validate_pair_geometry(
        state,
        signs,
        members,
        shared=True,
        plane_normal=(1, 0, 0),
        plane_offset=0.02,
        resolution=64,
        correspondence_fraction=0.005,
    )
    assert result["valid"], result
    expected = 0.04 / np.linalg.norm(np.ptp(vertices, axis=0))
    assert result["maximum_overlap_geometry_error"] == pytest.approx(expected)


def test_unused_uv_coordinate_does_not_change_atlas_border() -> None:
    state, signs, members = _panels()
    state.uv = np.vstack((state.uv, [-1, -1]))
    assert _checked(state, signs, members)["valid"]


def test_disconnected_charts_of_same_member_cannot_overlap() -> None:
    vertices = np.asarray(
        [
            [-1, 0, 0],
            [-1, 0, 1],
            [-1, 1, 0],
            [-1, 0, 0],
            [-1, 0, 1],
            [-1, 1, 0],
            [1, 0, 0],
            [1, 1, 0],
            [1, 0, 1],
        ],
        dtype=float,
    )
    faces = np.arange(9, dtype=np.int64).reshape(3, 3)
    state = MeshState(make_mesh(vertices, faces), frozenset(), vertices[:, 1:] * 0.2 + 0.2, faces)
    result = _checked(state, np.asarray([-1, -1, 1], dtype=np.int64), np.asarray([0, 0, 1], dtype=np.int64))
    assert not result["valid"]
    assert "MEMBER_SELF_INTERSECTION:0:1" in _errors(result)


def test_member_must_be_separated_in_uv_copy_topology() -> None:
    state, signs, members = _panels()
    members[1] = 1
    result = _checked(state, signs, members)
    assert any(error.startswith("MEMBER_NOT_ISOLATED:") for error in _errors(result))
