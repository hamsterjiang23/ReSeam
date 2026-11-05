"""Contour packing uses one master and preserves exact float64 shared UV values."""

import json
import time
from pathlib import Path

import numpy as np
import pytest
from shapely import Polygon, union_all

from reseam.agent_tools import compact_packing
from reseam.budget_state import BudgetState, fingerprint, owners_and_members
from reseam.joint_types import ReuseGroup
from reseam.mesh import make_mesh
from reseam.types import FloatArray, MeshState


def _paired_l_shapes() -> BudgetState:
    shape = np.array([[0., 0.], [2., 0.], [2., 1.], [1., 1.], [1., 2.], [0., 2.]])
    triangles = np.array([[0, 1, 3], [1, 2, 3], [0, 3, 5], [3, 4, 5]], dtype=np.int64)
    vertices, uvs, faces = [], [], []
    for copy in range(4):
        vertices.append(np.column_stack((shape + [copy * 3., 0.], np.zeros(6))))
        uvs.append(shape / 5. + [copy % 2 * .5, 0.])
        faces.append(triangles + 6 * copy)
    mesh = make_mesh(np.concatenate(vertices), np.concatenate(faces))
    state = MeshState(mesh, frozenset(), np.concatenate(uvs), np.concatenate(faces))
    groups = tuple(ReuseGroup(f"pair{i}", tuple(range(i * 4, i * 4 + 4)),
                              tuple(range((i + 2) * 4, (i + 2) * 4 + 4)), (1., 0., 0.), 0., 1, 1)
                   for i in range(2))
    return BudgetState(state, groups)


def test_similarity_is_proper_and_handles_skinny_float32_samples() -> None:
    source = np.array([[0., 0.], [.9, 0.], [.9, 1e-7], [.2, 2e-7]])
    angle = .7
    rotation = np.array([[np.cos(angle), np.sin(angle)], [-np.sin(angle), np.cos(angle)]])
    target = (source @ rotation * .3 + [.6, .2]).astype(np.float32).astype(np.float64)
    fit = compact_packing.fit_similarity(source, target)
    assert np.linalg.det(fit.rotation) == pytest.approx(1.)
    assert fit.scale == pytest.approx(.3, rel=1e-6)
    assert fit.maximum_error < 2e-6


@pytest.mark.parametrize("transform", [np.diag([1., 2.]), np.diag([-1., 1.])])
def test_similarity_rejects_anisotropic_scaling_and_reflection(transform: FloatArray) -> None:
    points = np.array([[0., 0.], [1., 0.], [0., 1.], [1., 1.]])
    with pytest.raises(ValueError, match="NON_SIMILAR_OR_REFLECTED"):
        compact_packing.fit_similarity(points, points @ transform)


def test_timeout_preserves_original_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    state = _paired_l_shapes()
    before = fingerprint(state)

    def expired(argv: list[str], folder: Path, deadline: float) -> float:
        assert argv and folder.exists() and deadline > 0
        raise TimeoutError("TEST_BACKEND_TIMEOUT")

    monkeypatch.setattr(compact_packing, "_run_blender", expired)
    fake = tmp_path / "worker"
    fake.write_text("not executed")
    with pytest.raises(TimeoutError, match="TEST_BACKEND_TIMEOUT"):
        compact_packing.compact_pack(state, 512, blender=fake, worker=fake,
                                     output=tmp_path / "output", deadline=time.monotonic() + 30)
    assert fingerprint(state) == before


def test_backend_nonuniform_scales_rejected_without_mutation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = _paired_l_shapes()
    before = fingerprint(state)

    def unequal_scales(argv: list[str], folder: Path, deadline: float) -> float:
        assert argv and deadline > 0
        with np.load(folder / "input.npz", allow_pickle=False) as arrays:
            faces, owners = arrays["faces"], arrays["owners"]
            corners = arrays["uv"][faces].copy()
            for i, owner in enumerate(np.unique(owners)):
                corners[owners == owner] *= .2 * (i + 1)
            np.savez_compressed(folder / "packed.npz", faces=faces, owners=owners,
                                source_faces=arrays["source_faces"], corner_uv=corners)
        (folder / "backend.json").write_text(json.dumps({"test_double": True}), encoding="utf-8")
        return .01

    monkeypatch.setattr(compact_packing, "_run_blender", unequal_scales)
    fake = tmp_path / "worker"
    fake.write_text("not executed")
    with pytest.raises(ValueError, match="BLENDER_PACK_NONUNIFORM_SCALE"):
        compact_packing.compact_pack(state, 512, blender=fake, worker=fake,
                                     output=tmp_path / "output", deadline=time.monotonic() + 30)
    assert fingerprint(state) == before


@pytest.mark.parametrize("seed", [0, 17])
def test_real_blender_contours_common_scale_identity_and_spacing(tmp_path: Path, seed: int) -> None:
    blender = Path("E:/Blender 4.5.11/blender.exe")
    if not blender.is_file():
        pytest.skip("Isolated Blender 4.5 runtime unavailable")
    worker = Path(__file__).with_name("agent_pack_worker.py")
    if not worker.is_file():
        worker = Path(__file__).resolve().parents[1] / "scripts/agent_pack_worker.py"
    source = _paired_l_shapes()
    before = fingerprint(source)
    result, report = compact_packing.compact_pack(
        source, 512, gap_pixels=8, border_pixels=6, blender=blender, worker=worker,
        output=tmp_path / "real", deadline=time.monotonic() + 300, seed=seed)
    assert fingerprint(source) == before
    np.testing.assert_array_equal(result.surface.mesh.vertices, source.surface.mesh.vertices)
    np.testing.assert_array_equal(result.surface.mesh.faces, source.surface.mesh.faces)
    np.testing.assert_array_equal(result.surface.uv_faces, source.surface.uv_faces)
    assert result.surface.seams == source.surface.seams
    assert result.groups == source.groups
    for group in result.groups:
        np.testing.assert_array_equal(result.surface.uv[result.surface.uv_faces[list(group.first_faces)]],
                                      result.surface.uv[result.surface.uv_faces[list(group.second_faces)]])
    owners, _ = owners_and_members(result)
    outlines = [union_all([Polygon(tri) for tri in result.surface.uv[result.surface.uv_faces[owners == owner]]])
                for owner in np.unique(owners)]
    assert outlines[0].intersection(outlines[1]).area == 0
    assert outlines[0].distance(outlines[1]) * 512 >= 8 - 1e-5
    assert result.surface.uv.min() * 512 >= 6 - 1e-5
    assert (1 - result.surface.uv.max()) * 512 >= 6 - 1e-5
    scale = report["common_scale_original_uv"]
    assert isinstance(scale, float)
    for original, packed in zip(source.surface.uv[source.surface.uv_faces],
                                result.surface.uv[result.surface.uv_faces], strict=True):
        a, b = np.linalg.norm(original[1] - original[0]), np.linalg.norm(packed[1] - packed[0])
        assert b / a == pytest.approx(scale, rel=1e-12)
    assert report["owner_count"] == 2
    assert report["unwrap_called"] is False
    assert report["packing_seed"] == seed


def test_seeded_starts_are_reproducible_rigid_and_keep_parent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = _paired_l_shapes()
    before = fingerprint(state)

    def identity_backend(argv: list[str], folder: Path, deadline: float) -> float:
        assert argv and deadline > 0
        with np.load(folder / "input.npz", allow_pickle=False) as arrays:
            np.savez_compressed(folder / "packed.npz", faces=arrays["faces"], owners=arrays["owners"],
                                source_faces=arrays["source_faces"], corner_uv=arrays["uv"][arrays["faces"]])
        (folder / "backend.json").write_text('{}', encoding="utf-8")
        return .01

    monkeypatch.setattr(compact_packing, "_run_blender", identity_backend)
    fake = tmp_path / "worker"
    fake.write_text("not executed")
    results = []
    for i, seed in enumerate([17, 17, 29]):
        result, report = compact_packing.compact_pack(
            state, 512, blender=fake, worker=fake, output=tmp_path / str(i),
            deadline=time.monotonic() + 30, seed=seed)
        results.append(result.surface.uv)
        assert report["initial_rotations_radians"]
        for group in result.groups:
            np.testing.assert_array_equal(result.surface.uv[result.surface.uv_faces[list(group.first_faces)]],
                                          result.surface.uv[result.surface.uv_faces[list(group.second_faces)]])
        scale = report["common_scale_original_uv"]
        assert isinstance(scale, float)
        for a, b in zip(state.surface.uv[state.surface.uv_faces],
                        result.surface.uv[result.surface.uv_faces], strict=True):
            np.testing.assert_allclose(np.linalg.norm(b[:, None] - b[None, :], axis=-1),
                                       np.linalg.norm(a[:, None] - a[None, :], axis=-1) * scale, atol=1e-12)
    np.testing.assert_array_equal(results[0], results[1])
    assert not np.array_equal(results[0], results[2])
    assert fingerprint(state) == before


@pytest.mark.parametrize("seed", [-1, 2**32, True, 1.5])
def test_invalid_seed_fails_before_backend(tmp_path: Path, seed: int) -> None:
    with pytest.raises(ValueError, match="INVALID_PACKING_SEED"):
        compact_packing.compact_pack(_paired_l_shapes(), 512, blender=tmp_path / "missing",
                                    worker=tmp_path / "missing", output=tmp_path,
                                    deadline=time.monotonic() + 30, seed=seed)
