from dataclasses import replace

import numpy as np

from reseam.mesh import make_mesh
from reseam.symmetry import ReuseProfile, apply_reuse, barycentric_samples, propose_reuse, validate_reuse
from reseam.types import MeshState


def symmetric_panels() -> MeshState:
    vertices = np.asarray(
        [[x, y, z] for x in (-1, 1) for y, z in ((0, 0), (1, 0), (1, 1), (0, 1))], dtype=float
    )
    faces = np.asarray([[0, 2, 1], [0, 3, 2], [4, 5, 6], [4, 6, 7]], dtype=np.int64)
    uv = np.asarray(
        [[0.05, 0.1], [0.05, 0.4], [0.35, 0.4], [0.35, 0.1], [0.6, 0.1], [0.9, 0.1], [0.9, 0.4], [0.6, 0.4]]
    )
    return MeshState(make_mesh(vertices, faces), frozenset(), uv, faces.copy())


def test_symmetric_reuse_preserves_geometry_and_counts_unique_area() -> None:
    state = symmetric_panels()
    texture = np.ones((64, 64, 3)) * 0.4
    result = propose_reuse(state, texture, replace(ReuseProfile(), minimum_patch_faces=2))
    candidate = next(c for c in result["candidates"] if c["status"] == "PENDING_HUMAN")
    output = apply_reuse(state, candidate["pairs"])
    checked = validate_reuse(state, output, candidate["pairs"])
    assert checked["valid"]
    assert np.array_equal(state.mesh.vertices, output.mesh.vertices)
    assert abs(checked["reuse_factor"] - 2) < 1e-10
    assert checked["flipped_faces"] == 2
    assert abs(checked["freed_unique_area"] - 0.09) < 1e-10


def test_asymmetric_texture_blocks_automatic_candidate() -> None:
    texture = np.zeros((64, 64, 3))
    texture[:, 32:] = 1
    result = propose_reuse(symmetric_panels(), texture, replace(ReuseProfile(), minimum_patch_faces=2))
    assert result["candidates"]
    assert all(c["status"] == "DIAGNOSTIC_REJECTED" for c in result["candidates"])


def test_unapproved_uv_change_and_geometry_change_are_rejected() -> None:
    source = symmetric_panels()
    pair = [{"donor": 2, "owner": 0, "permutation": [0, 2, 1]}]
    changed = apply_reuse(source, pair)
    changed.uv[0, 0] += 0.02
    assert not validate_reuse(source, changed, pair)["valid"]
    changed = apply_reuse(source, pair)
    changed.mesh = make_mesh(source.mesh.vertices * 2, source.mesh.faces)
    assert not validate_reuse(source, changed, pair)["valid"]


def test_sample_pattern_is_permutation_invariant() -> None:
    samples = barycentric_samples()
    assert np.allclose(samples.sum(axis=1), 1)
    assert samples.min() > 0
    assert {tuple(x) for x in samples} == {tuple(x) for x in samples[:, [2, 0, 1]]}
