"""Master seam domains survive different target triangulations and physical scale."""

import time
from pathlib import Path

import numpy as np
import pytest

from reseam.agent_tools.correspondence import chart_aware_transfer, project_triangle_surface
from reseam.agent_tools.geometry import check_pair
from reseam.agent_tools.state import load_snapshot, read_object, save_snapshot
from reseam.budget_state import BudgetState, fingerprint, owners_and_members
from reseam.mesh import chart_labels, make_mesh, state_from_corner_uv
from reseam.reuse_transform import similarity_transform


@pytest.mark.parametrize("scale", [1., 1e-5, 1e-9])
def test_plane_and_segment_projection_on_tiny_curved_patch_is_scale_invariant(scale: float) -> None:
    triangles = np.asarray([[[0., 0, 0], [1., 0, 0], [0., 1., 0]],
                            [[1., 0, 0], [1., 1., .3], [0., 1., 0]]])*scale
    normal = np.cross(triangles[1, 1]-triangles[1, 0], triangles[1, 2]-triangles[1, 0])
    normal /= np.linalg.norm(normal)
    weights = np.asarray([.2, .3, .5])
    point = weights @ triangles[1] + .02*scale*normal
    query = np.asarray([point, [-.2*scale, .3*scale, .1*scale]])
    result, face, bary, distance = project_triangle_surface(query, triangles, time.monotonic()+10)
    assert face.tolist() == [1, 0]
    assert np.allclose(bary[0], weights, atol=1e-12, rtol=0)
    assert np.allclose(result[0]/scale, weights @ (triangles[1]/scale), atol=1e-12, rtol=0)
    assert np.allclose(bary[1], [.7, 0, .3], atol=1e-12, rtol=0)
    assert distance[0]/scale == pytest.approx(.02)
    assert distance[1]/scale == pytest.approx(np.sqrt(.05))


@pytest.mark.parametrize("parity", [1, -1])
def test_chart_transfer_reconstructs_master_cuts_on_different_curved_triangulation(
    tmp_path: Path, parity: int,
) -> None:
    first = np.asarray([[0., y, z] for z in (0., 1.) for y in (0., 1., 2.)])
    first[first[:, 1] == 2, 0] = .35  # Two non-coplanar quads, each triangulated differently.
    q = np.asarray([[0., 0, 1], [1., 0, 0], [0, 1., 0]]) @ np.diag([parity, 1., 1.])
    transform = similarity_transform(np.column_stack((q, [3., 2., -1.])))
    vertices = np.concatenate((first, first @ q.T+np.asarray(transform)[:, 3]))
    a = np.asarray([[0, 1, 4], [0, 4, 3], [1, 2, 5], [1, 5, 4]], dtype=np.int64)
    b = np.asarray([[0, 1, 3], [1, 4, 3], [1, 2, 4], [2, 5, 4]], dtype=np.int64)
    if parity < 0:
        b = b[:, [0, 2, 1]]
    faces = np.concatenate((a, b+6))
    corners = np.concatenate((first[a, 1:]*.12+[.05, .1], first[b, 1:]*[parity*.12, .12]+[.7, .6]))
    corners[2:4, :, 0] += .2
    identities = faces.copy()
    identities[2:4] += 20
    surface = state_from_corner_uv(make_mesh(vertices, faces), corners, corner_ids=identities)
    initial = BudgetState(surface)
    original = fingerprint(initial)
    mapped, evidence = chart_aware_transfer(surface, (0, 1, 2, 3), (4, 5, 6, 7),
                                           np.asarray([1., 0, 0]), 0, time.monotonic()+10, transform)
    assert evidence["target_old_uv_identity_used"] is False
    selected = evidence["selected_master_charts"]
    assert isinstance(selected, list) and selected[0] == selected[1] != selected[2] == selected[3]
    assert fingerprint(initial) == original
    # Target vertex1/4 sit on the master seam and must acquire two distinct UV copies.
    keys = evidence["new_identity_keys"]
    assert isinstance(keys, list)
    assert sum(key[0] == 7 for key in keys) == 2
    assert sum(key[0] == 10 for key in keys) == 2
    assert len(np.unique(mapped[1])) == 8
    source = save_snapshot(initial, tmp_path / "source", {})
    result = check_pair({"snapshot": str(source.root), "first_faces": [0, 1, 2, 3],
                         "second_faces": [4, 5, 6, 7], "mode": "chart_aware",
                         "first_to_second": [list(row) for row in transform],
                         "profile": {"resolution": 128, "gap_pixels": 2, "border_pixels": 2}},
                        tmp_path / "checked", time.monotonic()+30)
    assert result["status"] == "OK" and result["geometry_passed"] is True, result
    proposal = read_object(Path(str(result["proposal"])))
    shared = load_snapshot(Path(str(proposal["candidate_snapshot"])))
    labels = chart_labels(shared.state.surface)
    assert len(shared.state.groups) == 2
    assert len(np.unique(owners_and_members(shared.state)[0])) == 2
    group_ids = result["shared_group_ids"]
    assert isinstance(group_ids, list) and len(group_ids) == 2
    assert all(len(np.unique(labels[list(group.first_faces)])) == 1 for group in shared.state.groups)
    assert all(len(np.unique(labels[list(group.second_faces)])) == 1 for group in shared.state.groups)
    assert len(np.unique(labels[:4])) == len(np.unique(labels[4:])) == 2
    assert labels[4] == labels[5] != labels[6] == labels[7]
    assert np.array_equal(shared.state.surface.mesh.vertices, surface.mesh.vertices)
    assert np.array_equal(shared.state.surface.mesh.faces, surface.mesh.faces)
    assert fingerprint(load_snapshot(source.root).state) == original


def test_normal_aware_centroid_selects_correct_side_of_thin_shell(tmp_path: Path) -> None:
    square = np.asarray([[0., 0, 0], [1., 0, 0], [1., 1., 0], [0., 1., 0]])
    master = np.concatenate((square+[0, 0, .02], square))
    target = np.concatenate((square+[3, 0, .005], square+[3, 0, -.005]))
    a = np.asarray([[0, 1, 2], [0, 2, 3], [4, 6, 5], [4, 7, 6]], dtype=np.int64)
    faces = np.concatenate((a, a+8))
    vertices = np.concatenate((master, target))
    corners = vertices[faces, :2]*.1+[.1, .1]
    corners[2:4, :, 0] = .5-corners[2:4, :, 0]
    corners[4:] += [.1, .5]
    corners[6:8, :, 0] *= -1
    surface = state_from_corner_uv(make_mesh(vertices, faces), corners, corner_ids=faces)
    transform = similarity_transform([[1., 0, 0, 3.], [0, 1., 0, 0], [0, 0, 1., 0]])
    _, evidence = chart_aware_transfer(surface, (0, 1, 2, 3), (4, 5, 6, 7),
                                       np.asarray([1., 0, 0]), 0, time.monotonic()+10, transform)
    assert evidence["normal_filter_changed_master_face_count"] == 2
    assert evidence["unfiltered_centroid_normal_angle_degrees"] == [180., 180., 0., 0.]
    assert evidence["centroid_normal_angle_degrees"] == [0., 0., 0., 0.]
    assert evidence["selected_master_faces"] == [0, 1, 2, 3]
    source = save_snapshot(BudgetState(surface), tmp_path/"source", {})
    result = check_pair({"snapshot": str(source.root), "first_faces": [0, 1, 2, 3],
                         "second_faces": [4, 5, 6, 7], "mode": "chart_aware",
                         "first_to_second": [list(row) for row in transform]},
                        tmp_path/"check", time.monotonic()+30)
    assert result["status"] == "OK" and result["geometry_passed"] is True, result
    proposal = read_object(Path(str(result["proposal"])))
    shared = load_snapshot(Path(str(proposal["candidate_snapshot"])))
    assert len(shared.state.groups) == 2
    assert np.array_equal(shared.state.surface.mesh.vertices, vertices)
    assert np.array_equal(shared.state.surface.mesh.faces, faces)


def test_chart_projection_rejects_degenerate_domain_and_expired_budget() -> None:
    triangle = np.asarray([[[0., 0, 0], [1., 0, 0], [0., 1., 0]]])
    with pytest.raises(TimeoutError):
        project_triangle_surface(np.zeros((1, 3)), triangle, time.monotonic()-1)
    triangle[0, 2] = triangle[0, 1]
    with pytest.raises(ValueError, match="DEGENERATE_MASTER_TRIANGLE"):
        project_triangle_surface(np.zeros((1, 3)), triangle, time.monotonic()+10)
