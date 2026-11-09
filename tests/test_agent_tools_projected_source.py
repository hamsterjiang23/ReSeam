"""High-reference color is sampled at actual low barycentric queries, never a proxy atlas."""

import time
from pathlib import Path

import numpy as np
import pytest

from reseam.agent_tools.color_reuse import (
    AtlasSignal,
    ColorTarget,
    _box,
    _source,
    bake_color_reuse,
    freeze_target,
    validate_color_reuse,
)
from reseam.agent_tools.projected_source import ProjectedSource, projected_source_metadata
from reseam.agent_tools.state import Snapshot, file_hash, read_object, save_snapshot
from reseam.budget_material import atlas_from_arrays, export_material_glb
from reseam.budget_state import BudgetState
from reseam.budget_texture import ReferenceSurface, sample_rgba8
from reseam.joint_types import ReuseGroup
from reseam.mesh import make_mesh, state_from_corner_uv
from reseam.types import FloatArray, IntArray


def _folder(root: Path, name: str) -> Path:
    path = root / name
    path.mkdir()
    return path


def _deadline() -> float:
    return time.monotonic() + 90


def _different_mesh_source(root: Path, offset: float = .001) -> Snapshot:
    vertices = np.array([[0., 0, 0], [1., 0, 0], [1., 1, 0], [0., 1, 0], [.5, .5, 0]])
    high_faces = np.array([[0, 1, 4], [1, 2, 4], [2, 3, 4], [3, 0, 4]], dtype=np.int64)
    high_uv = np.array([[.1, .1], [.9, .1], [.9, .9], [.1, .9], [.3, .7]])
    high = state_from_corner_uv(make_mesh(vertices, high_faces), high_uv[high_faces], seam_ids=frozenset())
    y, x = np.indices((32, 32))
    color = np.empty((32, 32, 4))
    color[..., 0], color[..., 1], color[..., 2], color[..., 3] = x / 31, y / 31, .2, 1.
    atlas = atlas_from_arrays(color, np.full((32, 32, 3), [0., 0, 1]),
                              np.full((32, 32, 3), [1., 1., 0]))
    path = root / "high.glb"
    export_material_glb(high, atlas, path)
    low_vertices = vertices[:4].copy()
    low_vertices[:, 2] = offset
    low_faces = np.array([[0, 1, 2], [0, 2, 3]], dtype=np.int64)
    low_uv = .15 + .7 * low_vertices[:, :2]
    low = state_from_corner_uv(make_mesh(low_vertices, low_faces), low_uv[low_faces], seam_ids=frozenset())
    return save_snapshot(BudgetState(low), root / "low", {"reference_glb": str(path)})


def test_high_projection_is_explicit_and_queries_actual_high_triangles(tmp_path: Path) -> None:
    snapshot = _different_mesh_source(tmp_path)
    with pytest.raises(ValueError, match="ORIGINAL_FACE_CORNER_SOURCE_IDENTITY"):
        freeze_target({"snapshot": str(snapshot.root)}, _folder(tmp_path, "legacy"), _deadline())
    frozen = freeze_target({"snapshot": str(snapshot.root), "source_mode": "projected_high"},
                           _folder(tmp_path, "projected"), _deadline())
    target = ColorTarget(snapshot, Path(str(frozen["target"])))
    assert target.projected_source is not None
    assert target.reference.triangles.shape != snapshot.state.surface.mesh.faces.shape
    bary = np.array([[.2, .3, .5], [.6, .1, .3]])
    actual = target.projected_source.sample(0, bary)
    points = bary @ snapshot.state.surface.mesh.vertices[snapshot.state.surface.mesh.faces[0]]
    expected = target.reference.sample(points, .1)
    assert np.allclose(actual.rgba_linear, expected.rgba_linear)
    assert np.allclose(actual.distances, .001)
    assert np.array_equal(actual.target_faces, expected.target_faces)
    assert np.allclose(actual.barycentric, expected.barycentric)
    assert np.allclose(actual.source_low_barycentric, bary)
    # This high UV map is piecewise affine with an interior bend. A low-face
    # corner-UV proxy would interpolate a different color at the query point.
    proxy = sample_rgba8(target.reference.image, bary @ target.source_uv[0], repeat=target.reference.repeat)
    assert np.max(np.abs(actual.rgba_linear[:, :3] - proxy[:, :3])) > .1
    with np.load(tmp_path / "projected/projection/source_map.npz", allow_pickle=False) as data:
        assert np.all(data["valid"])
        assert np.isclose(data["distances"].max(), .001)
        witness_points = np.einsum("nc,ncd->nd", data["high_barycentric"],
                                  target.reference.triangles[data["high_faces"]])
        assert np.allclose(np.linalg.norm(witness_points - data["source_points"], axis=1), data["distances"])


def test_projection_distance_failure_retains_actual_witnesses(tmp_path: Path) -> None:
    snapshot = _different_mesh_source(tmp_path, offset=.1)
    output = _folder(tmp_path, "outside")
    with pytest.raises(ValueError, match="PROJECTED_SOURCE_OUT_OF_RANGE"):
        freeze_target({"snapshot": str(snapshot.root), "source_mode": "projected_high"}, output, _deadline())
    evidence = read_object(output / "projection/source_map.json")
    assert evidence["invalid_queries"] == 6
    assert float(str(evidence["distance_max"])) > float(str(evidence["max_distance"]))
    assert not (output / "target.json").exists()


def test_projection_source_rule_hash_and_original_high_are_bound(tmp_path: Path) -> None:
    snapshot = _different_mesh_source(tmp_path)
    frozen = freeze_target({"snapshot": str(snapshot.root), "source_mode": "projected_high"},
                           _folder(tmp_path, "first"), _deadline())
    second = freeze_target({"snapshot": str(snapshot.root), "source_mode": "projected_high",
                             "projection_max_fraction": .03}, _folder(tmp_path, "second"), _deadline())
    first_record = read_object(Path(str(frozen["target"])))
    second_record = read_object(Path(str(second["target"])))
    assert first_record["source_projection"] != second_record["source_projection"]
    source = Path(str(snapshot.metadata["reference_glb"]))
    source.write_bytes(source.read_bytes() + b" ")
    with pytest.raises(ValueError, match="TARGET_SOURCE_CHANGED"):
        ColorTarget(snapshot, Path(str(frozen["target"])))


def test_projected_target_and_footprint_are_independent_of_candidate_uv_and_texture(tmp_path: Path) -> None:
    snapshot = _different_mesh_source(tmp_path)
    frozen = freeze_target({"snapshot": str(snapshot.root), "source_mode": "projected_high"},
                           _folder(tmp_path, "target"), _deadline())
    path = Path(str(frozen["target"]))
    initial = ColorTarget(snapshot, path)
    bary = np.array([[.2, .3, .5], [.6, .1, .3]])
    before = initial.original(0, bary)
    state = snapshot.state.copy()
    state.surface.uv = .05 + .5 * state.surface.uv
    state.texture = np.full((8, 8, 4), .99)
    candidate = save_snapshot(state, tmp_path / "candidate", snapshot.metadata, snapshot)
    reloaded = ColorTarget(candidate, path)
    assert np.array_equal(reloaded.original(0, bary), before)
    # The footprint's source signal follows the exact projected sampler too.
    triangle = candidate.state.surface.uv[candidate.state.surface.uv_faces[0]]
    center = np.array([[.3, .3, .4]]) @ triangle
    projected = _box(AtlasSignal(reloaded, candidate.state.surface), 0, triangle, center, 32, 0)
    offsets = np.array([[-.25, -.25], [.25, -.25], [-.25, .25], [.25, .25]]) / 32
    matrix = np.column_stack((triangle[1] - triangle[0], triangle[2] - triangle[0]))
    tail = (center + offsets - triangle[0]) @ np.linalg.inv(matrix).T
    probes = np.column_stack((1 - tail.sum(axis=1), tail))
    assert np.allclose(projected[0], reloaded.original(0, probes).mean(axis=0))


def test_mirror_lineage_addresses_retained_original_low_side(tmp_path: Path) -> None:
    original = _different_mesh_source(tmp_path)
    metadata = projected_source_metadata(original, tmp_path / "original_lineage.npz")
    with np.load(Path(str(metadata["lineage_npz"])), allow_pickle=False) as data:
        triangles = data["original_low_triangles"].copy()
    state = original.state.copy()
    # A new mirrored geometry may be far from the original high surface. Its
    # lineage, rather than its reflected world position, supplies source color.
    vertices = state.surface.mesh.vertices.copy()
    vertices[:, 0] = 10. - vertices[:, 0]
    state.surface.mesh = make_mesh(vertices, state.surface.mesh.faces)
    ancestry = np.broadcast_to(np.eye(3)[[1, 0, 2]], (2, 3, 3)).copy()
    lineage = tmp_path / "mirrored_lineage.npz"
    np.savez_compressed(lineage, original_low_triangles=triangles,
                        source_low_faces=np.array([1, 0], dtype=np.int64),
                        source_low_corner_barycentric=ancestry)
    metadata = {**metadata, "lineage_npz": str(lineage), "lineage_sha256": file_hash(lineage)}
    mirrored = save_snapshot(state, tmp_path / "mirrored", {**original.metadata, "projected_source": metadata})
    reference, _, _ = _source(mirrored, allow_projected=True)
    sampler = ProjectedSource.from_snapshot(mirrored, reference)
    bary = np.array([[.2, .3, .5]])
    actual = sampler.sample(0, bary)
    assert actual.source_low_faces.tolist() == [1]
    assert np.allclose(actual.source_low_barycentric, [[.3, .2, .5]])
    assert np.allclose(actual.source_points, np.array([[.3, .2, .5]]) @ triangles[1])
    frozen = freeze_target({"snapshot": str(mirrored.root)}, _folder(tmp_path, "mirror_target"), _deadline())
    target = ColorTarget(mirrored, Path(str(frozen["target"])))
    assert np.allclose(target.original(0, bary), actual.rgba_linear)


def test_actual_bake_and_evaluation_emit_projection_source_maps(tmp_path: Path) -> None:
    snapshot = _different_mesh_source(tmp_path)
    baked = bake_color_reuse({"snapshot": str(snapshot.root), "source_mode": "projected_high",
                              "resolution": 16, "padding_pixels": 1}, _folder(tmp_path, "bake"), _deadline())
    assert baked["status"] == "OK"
    bake_map = read_object(tmp_path / "bake/projection/source_map.json")
    assert bake_map["stage"] == "actual_bake_source_queries"
    assert int(str(bake_map["queries"])) > 0
    assert bake_map["invalid_queries"] == 0
    validation = validate_color_reuse({"snapshot": str(snapshot.root), "bake": baked["bake"],
                                      "render_size": 16, "mip_levels": [0]},
                                     _folder(tmp_path, "evaluation"), _deadline())
    assert validation["status"] in ("OK", "REJECTED")
    evaluation_map = read_object(tmp_path / "evaluation/projection/source_map.json")
    assert evaluation_map["stage"] == "actual_evaluation_source_queries"
    assert int(str(evaluation_map["queries"])) > 0
    assert evaluation_map["invalid_queries"] == 0


def test_projected_shared_target_uses_master_signal_on_unshared_control(tmp_path: Path) -> None:
    original = _different_mesh_source(tmp_path)
    surface = original.state.surface
    corners = surface.uv[surface.uv_faces].copy()
    corners[1] = corners[0, [0, 2, 1]]
    shared_surface = state_from_corner_uv(surface.mesh, corners,
                                         seam_ids=frozenset(range(len(surface.mesh.edges))))
    group = ReuseGroup("diagonal", (0,), (1,), (2**-.5, -2**-.5, 0.), 0., 1, -1)
    shared = save_snapshot(BudgetState(shared_surface, groups=(group,)), tmp_path / "shared",
                           original.metadata, original)
    frozen = freeze_target({"snapshot": str(shared.root), "source_mode": "projected_high"},
                           _folder(tmp_path, "shared_target"), _deadline())
    shared_target = ColorTarget(shared, Path(str(frozen["target"])))
    control_target = ColorTarget(original, shared_target.path)
    bary = np.array([[.2, .3, .5], [.6, .1, .3]])
    expected_master = shared_target.original(0, bary[:, [0, 2, 1]])
    assert np.allclose(shared_target.sample(1, bary), expected_master)
    assert np.allclose(control_target.sample(1, bary), expected_master)
    assert np.max(np.abs(expected_master - shared_target.original(1, bary))) > .1


def _grid_triangles(side: int) -> tuple[FloatArray, IntArray]:
    xx, yy = np.meshgrid(np.linspace(0., 1., side + 1), np.linspace(0., 1., side + 1))
    vertices = np.column_stack((xx.ravel(), yy.ravel(), np.zeros(xx.size)))
    faces = []
    for row in range(side):
        for col in range(side):
            start = row * (side + 1) + col
            faces.extend(((start, start + 1, start + side + 2),
                          (start, start + side + 2, start + side + 1)))
    return vertices, np.asarray(faces, dtype=np.int64)


def test_fixed_original_face_roi_matches_full_randomized_nearest_queries() -> None:
    high_vertices, high_faces = _grid_triangles(12)
    low_vertices, low_faces = _grid_triangles(2)
    y, x = np.indices((32, 32))
    image = np.empty((32, 32, 4), dtype=np.uint8)
    image[..., 0], image[..., 1], image[..., 2], image[..., 3] = x * 8, y * 8, 80, 255
    reference = ReferenceSurface(high_vertices, high_faces, high_vertices[high_faces, :2], image)
    triangles = low_vertices[low_faces]
    sampler = ProjectedSource(reference, triangles, np.arange(len(low_faces), dtype=np.int64),
                              np.broadcast_to(np.eye(3), (len(low_faces), 3, 3)))
    rng = np.random.default_rng(17)
    ids = rng.integers(0, len(low_faces), 2000, dtype=np.int64)
    bary = rng.dirichlet(np.ones(3), len(ids))
    points = np.einsum("nc,ncd->nd", bary, triangles[ids])
    full = reference.sample(points, sampler.max_distance)
    actual = sampler.sample(ids, bary)
    assert np.array_equal(actual.target_faces, full.target_faces)
    assert np.allclose(actual.barycentric, full.barycentric, rtol=0, atol=1e-12)
    assert np.allclose(actual.distances, full.distances, rtol=0, atol=1e-12)
    assert np.allclose(actual.rgba_linear, full.rgba_linear, rtol=0, atol=1e-12)
    assert sampler.roi_queries == len(ids)
    assert sampler.full_reference_queries == 0
    assert all(roi is not None and len(roi.faces) < len(reference.faces) for roi in sampler.roi_cache.values())
    # Reversing order and warming a different set of current-face queries do
    # not change any ROI bounds or source samples.
    reversed_values = sampler.sample(ids[::-1], bary[::-1])
    assert np.array_equal(reversed_values.target_faces[::-1], actual.target_faces)
    assert np.allclose(reversed_values.rgba_linear[::-1], actual.rgba_linear, rtol=0, atol=1e-12)
    fresh = ProjectedSource(reference, triangles, np.arange(len(low_faces), dtype=np.int64),
                            np.broadcast_to(np.eye(3), (len(low_faces), 3, 3)))
    assert np.array_equal(sampler.roi_bounds, fresh.roi_bounds)
    assert sampler.rule_hash == fresh.rule_hash


def test_roi_distance_failure_falls_back_to_true_global_nearest_witness() -> None:
    vertices = np.array([[.8, .8, 0.], [.9, .8, 0.], [.8, .9, 0.],
                         [-.2, 0., 0.], [-.1, 0., 0.], [-.1, 1., 0.]])
    faces = np.array([[0, 1, 2], [3, 4, 5]], dtype=np.int64)
    reference = ReferenceSurface(vertices, faces, np.full((2, 3, 2), .5),
                                 np.full((2, 2, 4), 255, dtype=np.uint8))
    triangle = np.array([[[0., 0., 0.], [1., 0., 0.], [0., 1., 0.]]])
    sampler = ProjectedSource(reference, triangle, np.array([0], dtype=np.int64), np.eye(3)[None], .001)
    bary = np.array([[.98, .01, .01]])
    actual = sampler.sample(0, bary, require_valid=False)
    full = reference.sample(bary @ triangle[0], sampler.max_distance)
    assert not actual.valid.any()
    assert actual.target_faces.tolist() == [1]
    assert np.array_equal(actual.target_faces, full.target_faces)
    assert np.allclose(actual.distances, full.distances)
    assert np.allclose(actual.barycentric, full.barycentric)
    assert sampler.roi_distance_fallback_queries == 1
    assert sampler.full_reference_queries == 1


def test_roi_outside_fixed_bounds_uses_full_reference() -> None:
    vertices, faces = _grid_triangles(2)
    reference = ReferenceSurface(vertices, faces, vertices[faces, :2],
                                 np.full((2, 2, 4), 255, dtype=np.uint8))
    triangle = vertices[faces[:1]]
    sampler = ProjectedSource(reference, triangle, np.array([0], dtype=np.int64), np.eye(3)[None])
    # The query API permits tiny barycentric roundoff. This point falls just
    # outside the fixed original triangle AABB, so no ROI guarantee is assumed.
    bary = np.array([[-1e-9, .5, .500000001]])
    actual = sampler.sample(0, bary)
    full = reference.sample(bary @ triangle[0], sampler.max_distance)
    assert sampler.roi_outside_bounds_queries == 1
    assert sampler.roi_queries == 0
    assert sampler.full_reference_queries == 1
    assert np.array_equal(actual.target_faces, full.target_faces)
    assert np.allclose(actual.barycentric, full.barycentric)


def test_mirrored_current_faces_share_original_low_face_roi() -> None:
    vertices, faces = _grid_triangles(4)
    reference = ReferenceSurface(vertices, faces, vertices[faces, :2],
                                 np.full((2, 2, 4), 255, dtype=np.uint8))
    triangles = vertices[faces[:2]]
    lineage = np.stack((np.eye(3), np.eye(3)[[1, 0, 2]]))
    sampler = ProjectedSource(reference, triangles, np.array([1, 1], dtype=np.int64), lineage)
    bary = np.array([[.2, .3, .5]])
    first = sampler.sample(0, bary)
    second = sampler.sample(1, bary[:, [1, 0, 2]])
    assert set(sampler.roi_cache) == {1}
    assert np.array_equal(first.target_faces, second.target_faces)
    assert np.allclose(first.barycentric, second.barycentric)
    assert np.array_equal(sampler.roi_bounds[1], [triangles[1].min(axis=0), triangles[1].max(axis=0)])
