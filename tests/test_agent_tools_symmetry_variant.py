"""Geometry clipping must preserve source/UV identities rather than weld positions."""

import time

import numpy as np
import pytest

from reseam.agent_tools import symmetry_variant
from reseam.agent_tools.symmetry_variant import build_symmetry_variant
from reseam.mesh import chart_labels, make_mesh
from reseam.types import FloatArray, MeshState


def _surface(vertices: list[list[float]], faces: list[list[int]], *, split_uv: bool = False) -> MeshState:
    mesh = make_mesh(np.asarray(vertices, dtype=np.float64), np.asarray(faces, dtype=np.int64))
    uv = mesh.vertices[:, :2].copy()
    if split_uv:
        return MeshState(mesh, frozenset(i for i, e in enumerate(mesh.edge_faces) if len(e) == 2),
                         uv[mesh.faces].reshape(-1, 2), np.arange(len(faces) * 3).reshape(-1, 3))
    return MeshState(mesh, frozenset(), uv, mesh.faces.copy())


def _deadline() -> float:
    return time.monotonic() + 10.0


def test_cross_plane_quad_split_interpolation_and_exact_reflection() -> None:
    source = _surface([[-1., 0., 0.], [1., 0., 0.], [1., 2., 0.]], [[0, 1, 2]])
    result = build_symmetry_variant(source, deadline=_deadline())
    out = result.surface
    assert result.report["retained_faces"] == 2
    assert result.report["split_source_faces"] == 1
    assert result.report["clipped_source_faces"] == 1
    assert result.report["output_area"] == pytest.approx(3.0)
    assert result.face_pairs.tolist() == [[0, 2], [1, 3]]
    original_triangles = source.mesh.vertices[source.mesh.faces[result.source_face_ids]]
    reconstructed = np.einsum("fci,fij->fcj", result.source_barycentric, original_triangles)
    reconstructed[result.reflected_faces, :, 0] *= -1
    np.testing.assert_allclose(reconstructed, out.mesh.vertices[out.mesh.faces], atol=1e-15)
    original_uv = source.uv[source.uv_faces[result.source_face_ids]]
    expected_uv = np.einsum("fci,fij->fcj", result.source_barycentric, original_uv)
    np.testing.assert_allclose(out.uv[out.uv_faces], expected_uv, atol=1e-15)
    for first, mirrored in result.face_pairs:
        np.testing.assert_array_equal(out.uv[out.uv_faces[first]], out.uv[out.uv_faces[mirrored, [0, 2, 1]]])
    n = len(result.mirror_vertex_ids)
    np.testing.assert_array_equal(out.mesh.vertices[n:, 0], -out.mesh.vertices[:n, 0])
    triangles = out.mesh.vertices[out.mesh.faces]
    normals = np.cross(triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0])
    assert np.all(normals[:, 2] > 0)
    assert len(set(chart_labels(out))) == 2  # One connected original chart in each half.
    assert set(out.uv_faces[:2].ravel()).isdisjoint(out.uv_faces[2:].ravel())


@pytest.mark.parametrize("split_uv", [False, True])
def test_original_edge_intersection_reused_but_uv_seam_preserved(split_uv: bool) -> None:
    source = _surface([[-1., 0., 0.], [1., 0., 0.], [1., 2., 0.], [-1., 2., 0.]],
                      [[0, 1, 2], [0, 2, 3]], split_uv=split_uv)
    result = build_symmetry_variant(source, deadline=_deadline())
    assert result.report["retained_vertices"] == 5
    assert result.report["retained_faces"] == 3
    intersection_id = result.retained_vertex_sources.index((1, 0, 2))
    locations = np.argwhere(result.surface.mesh.faces[:3] == intersection_id)
    assert len(locations) >= 2
    uv_ids = {int(result.surface.uv_faces[f, c]) for f, c in locations}
    assert len(uv_ids) == (2 if split_uv else 1)
    assert len(set(chart_labels(result.surface))) == (4 if split_uv else 2)
    assert result.report["output_area"] == pytest.approx(4.0)


def test_coincident_geometry_never_welds_unrelated_source_identities() -> None:
    vertices = [[-1., 0., 0.], [1., 0., 0.], [1., 2., 0.]]
    source = _surface(vertices + vertices, [[0, 1, 2], [3, 4, 5]])
    result = build_symmetry_variant(source, deadline=_deadline())
    assert result.report["retained_vertices"] == 8
    assert result.report["retained_uv_vertices"] == 8
    assert (1, 0, 1) in result.retained_vertex_sources
    assert (1, 3, 4) in result.retained_vertex_sources
    assert len(set(chart_labels(result.surface))) == 4


@pytest.mark.parametrize("axis", [0, 1, 2])
@pytest.mark.parametrize("positive", [False, True])
def test_nonzero_plane_and_half_selection(axis: int, positive: bool) -> None:
    vertices = np.asarray([[-1., 0., 0.], [1., 0., 0.], [1., 2., 0.]])
    vertices[:, [0, axis]] = vertices[:, [axis, 0]]
    vertices[:, axis] += 4.0
    source = _surface(vertices.tolist(), [[0, 1, 2]])
    result = build_symmetry_variant(source, plane_axis=axis, plane_offset=4.0,
                                    keep_positive=positive, deadline=_deadline())
    n = len(result.mirror_vertex_ids)
    signed = result.surface.mesh.vertices[:n, axis] - 4.0
    assert np.all(signed >= 0) if positive else np.all(signed <= 0)
    np.testing.assert_array_equal(result.surface.mesh.vertices[n:, axis],
                                  8.0 - result.surface.mesh.vertices[:n, axis])


def test_plane_vertex_retains_identity_and_no_degenerate_extra_triangle() -> None:
    source = _surface([[0., 0., 0.], [1., 1., 0.], [-1., 1., 0.]], [[0, 1, 2]])
    result = build_symmetry_variant(source, deadline=_deadline())
    assert result.report["retained_faces"] == 1
    assert result.retained_vertex_sources.count((0, 0)) == 1
    assert result.report["output_area"] == pytest.approx(1.0)


def test_empty_half_coplanar_and_invalid_axis_rejected() -> None:
    source = _surface([[1., 0., 0.], [2., 0., 0.], [2., 1., 0.]], [[0, 1, 2]])
    with pytest.raises(ValueError, match="EMPTY_RETAINED_HALF"):
        build_symmetry_variant(source, keep_positive=False, deadline=_deadline())
    with pytest.raises(ValueError, match="FULLY_COPLANAR_SOURCE_FACE_REQUIRES_POLICY:0"):
        build_symmetry_variant(source, plane_axis=2, deadline=_deadline())
    with pytest.raises(ValueError, match="AXIS_MUST"):
        build_symmetry_variant(source, plane_axis=3, deadline=_deadline())


def test_deadline_mid_operation_never_mutates_source(monkeypatch: pytest.MonkeyPatch) -> None:
    source = _surface([[-1., 0., 0.], [1., 0., 0.], [1., 2., 0.]], [[0, 1, 2]])
    before: list[FloatArray] = [source.mesh.vertices.copy(), source.uv.copy()]
    faces, uv_faces = source.mesh.faces.copy(), source.uv_faces.copy()
    calls = 0

    def expire_after_clipping(deadline: float) -> None:
        nonlocal calls
        assert deadline > 0
        calls += 1
        if calls == 3:
            raise TimeoutError("TEST_EXPIRED_AFTER_CLIPPING")

    monkeypatch.setattr(symmetry_variant, "check_deadline", expire_after_clipping)
    with pytest.raises(TimeoutError, match="AFTER_CLIPPING"):
        build_symmetry_variant(source, deadline=_deadline())
    np.testing.assert_array_equal(source.mesh.vertices, before[0])
    np.testing.assert_array_equal(source.uv, before[1])
    np.testing.assert_array_equal(source.mesh.faces, faces)
    np.testing.assert_array_equal(source.uv_faces, uv_faces)
