import time
from pathlib import Path

import numpy as np
import pytest

from reseam.fixed_uv import FixedAtlas, PackingProfile, certify, raster_mask, search_scale, shape_distortion
from reseam.mesh import make_mesh, save_state
from reseam.packing_benchmark import run_case
from reseam.types import MeshState


def sample() -> MeshState:
    vertices = np.asarray([[0, 0, 0], [1, 0, 0], [0, 1, 0], [2, 0, 0], [3, 0, 0], [2, 1, 0]], dtype=float)
    faces = np.asarray([[0, 1, 2], [3, 4, 5]], dtype=np.int64)
    uv = np.asarray([[0.1, 0.1], [0.3, 0.1], [0.1, 0.3], [0.6, 0.6], [0.8, 0.6], [0.6, 0.8]])
    return MeshState(make_mesh(vertices, faces), frozenset(), uv, faces.copy())


def test_shared_scale_area_density_and_shape() -> None:
    atlas = FixedAtlas.from_state(sample())
    profile = PackingProfile()
    state = atlas.reconstruct(np.asarray([0.1, -0.2]), atlas.centers, atlas.scale * 1.1)
    checked = certify(atlas, state, profile)
    assert checked["valid"]
    assert checked["occupancy"] == pytest.approx((atlas.scale * 1.1) ** 2)
    assert checked["density_gain"] == pytest.approx(1.1)
    assert checked["relative_density_error"] < 1e-10
    assert shape_distortion(state) == pytest.approx(shape_distortion(atlas.source))


@pytest.mark.parametrize("change", ["scale", "shear", "flip", "overlap", "border", "gap", "shrink", "mesh"])
def test_independent_validator_rejects(change: str) -> None:
    atlas = FixedAtlas.from_state(sample())
    state = atlas.source.copy()
    if change == "scale":
        state.uv[:3] = (state.uv[:3] - atlas.centers[0]) * 1.2 + atlas.centers[0]
    elif change == "shear":
        state.uv[1, 1] += 0.02
    elif change == "flip":
        state.uv[:3, 0] = 0.4 - state.uv[:3, 0]
    elif change == "overlap":
        state.uv[3:] = state.uv[:3]
    elif change == "border":
        state.uv[:3, 0] -= 0.1
    elif change == "gap":
        state.uv[3:] -= [0.49, 0.295]
    elif change == "shrink":
        state = atlas.reconstruct(np.zeros(2), atlas.centers, atlas.scale * 0.9)
    else:
        state.mesh = make_mesh(state.mesh.vertices * 2, state.mesh.faces)
    assert not certify(atlas, state, PackingProfile())["valid"]


def test_search_and_pixel_centres() -> None:
    atlas = FixedAtlas.from_state(sample())
    profile = PackingProfile()
    scale = search_scale(atlas, np.zeros(2), atlas.centers, profile, atlas.scale, time.monotonic() + 3)
    assert scale is not None and scale >= atlas.scale
    assert certify(atlas, atlas.reconstruct(np.zeros(2), atlas.centers, scale), profile)["valid"]
    assert abs(raster_mask(atlas.source, 1024).mean() - atlas.scale**2) < 0.001
    assert search_scale(atlas, np.zeros(2), atlas.centers, profile, atlas.scale, 0) is None


def test_backend_failure_retains_feasible_and_no_overwrite(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail(*args: object, **kwargs: object) -> None:
        raise TimeoutError("controlled timeout")

    monkeypatch.setattr("reseam.packing_benchmark.backend_pose", fail)
    source = tmp_path / "input.json"
    save_state(source, sample())
    output = tmp_path / "run"
    result = run_case(tmp_path, source, output, "xatlas", 17, PackingProfile(), 10, 2)
    assert result["status"] == "FALLBACK"
    assert result["quality"]["valid"] and result["input_unchanged"]
    assert all(t["status"] == "FAILED" for t in result["trials"])
    assert (output / "packed.glb").exists()
    with pytest.raises(FileExistsError):
        run_case(tmp_path, source, output, "xatlas", 17, PackingProfile())
