"""Color sphere membership and source-bound read-only sampling."""

import time
from pathlib import Path

import numpy as np
import pytest

from reseam.agent_tools.color_reuse import freeze_target
from reseam.agent_tools.color_tolerance import (
    BOUNDARY_ROUNDOFF,
    PRESET_RADII,
    check_color,
    color_distances,
    linear_rgb_to_oklab,
    sphere_summary,
    tolerance,
)
from reseam.agent_tools.geometry import apply_reuse, check_pair
from reseam.agent_tools.state import Snapshot, file_hash, load_snapshot, read_object, save_snapshot
from reseam.budget_material import atlas_from_arrays, export_material_glb
from reseam.budget_state import BudgetState, fingerprint
from reseam.mesh import make_mesh, state_from_corner_uv


def _folder(root: Path, name: str) -> Path:
    result = root / name
    result.mkdir()
    return result


def _source(root: Path) -> Snapshot:
    vertices = np.array([[-1., 0, 0], [-1, 1, 0], [-1, 1, 1], [-1, 0, 1],
                         [1., 0, 0], [1, 1, 0], [1, 1, 1], [1, 0, 1]])
    faces = np.array([[0, 1, 2], [0, 2, 3], [4, 6, 5], [4, 7, 6]], dtype=np.int64)
    corners = .1 + .25 * vertices[faces, 1:]
    corners[2:, :, 0] += .5
    corners[2:, :, 1] = .45 - corners[2:, :, 1]
    surface = state_from_corner_uv(make_mesh(vertices, faces), corners, seam_ids=frozenset())
    color = np.full((32, 32, 4), [.7, .1, .1, 1.])
    color[:, 16:] = [.65, .1, .1, 1.]
    atlas = atlas_from_arrays(color, np.full((32, 32, 3), [0., 0, 1]),
                              np.full((32, 32, 3), [1., 1., 0]))
    path = root / "source.glb"
    export_material_glb(surface, atlas, path)
    return save_snapshot(BudgetState(surface), root / "source", {"reference_glb": str(path)})


def test_conversion_identity_black_white_and_primary_reference_values() -> None:
    # W3C conversion formulas yield these sRGB primary Oklab coordinates.
    colors = np.eye(3)
    expected = np.array([[.62795536, .22486306, .12584630],
                         [.86643961, -.23388757, .17949848],
                         [.45201372, -.03245698, -.31152815]])
    assert np.allclose(linear_rgb_to_oklab(colors), expected, atol=2e-7, rtol=0)
    assert np.array_equal(color_distances(colors, colors), np.zeros(3))
    assert color_distances(np.zeros((1, 3)), np.ones((1, 3)))[0] == pytest.approx(100, abs=2e-6)
    with pytest.raises(ValueError, match="FINITE_LINEAR_RGB"):
        linear_rgb_to_oklab(np.array([[float("nan"), 0., 0.]]))
    with pytest.raises(ValueError, match="SHAPES_MUST_MATCH"):
        color_distances(np.zeros((1, 3)), np.zeros((2, 3)))


def test_inclusive_radius_and_documented_roundoff() -> None:
    boundary = np.array([[5., 5. + BOUNDARY_ROUNDOFF / 2]])
    assert sphere_summary(boundary, np.ones(1), 5.)["color_accepted"] is True
    outside = np.array([[5. + BOUNDARY_ROUNDOFF * 2]])
    assert sphere_summary(outside, np.ones(1), 5.)["color_accepted"] is False


def test_preset_monotonicity_and_area_weighting() -> None:
    values = np.array([[0., 4.], [8., 16.], [22., 22.]])
    areas = np.array([1., 2., 3.])
    reports = [sphere_summary(values, areas, radius) for radius in PRESET_RADII.values()]
    assert [report["outside_samples"] for report in reports] == [4, 3, 2]
    assert [report["outside_faces"] for report in reports] == [2, 2, 1]
    assert reports[0]["outside_area_fraction"] == pytest.approx(5 / 6)
    assert reports[-1]["outside_area_fraction"] == pytest.approx(.5)
    assert tolerance({}) == ("medium", 10.)
    assert tolerance({"level": "low", "radius": 8}) == ("low", 8.)


@pytest.mark.parametrize("value", [0, -1, True, float("nan"), float("inf"), "10"])
def test_invalid_radius(value: object) -> None:
    with pytest.raises(ValueError, match="POSITIVE_FINITE_COLOR_RADIUS"):
        tolerance({"radius": value})


@pytest.mark.parametrize("level", ["middle", "", None, []])
def test_invalid_level(level: object) -> None:
    with pytest.raises(ValueError, match="LEVEL_MUST_BE"):
        tolerance({"level": level})


def test_frozen_target_color_sphere_is_readonly_and_custom_radius_changes_acceptance(tmp_path: Path) -> None:
    source = _source(tmp_path)
    deadline = time.monotonic() + 30
    checked = check_pair({"snapshot": str(source.root), "first_faces": [0, 1], "second_faces": [2, 3],
                          "profile": {"resolution": 128, "gap_pixels": 8, "border_pixels": 8}},
                         _folder(tmp_path, "pair"), deadline)
    applied = apply_reuse({"snapshot": str(source.root), "proposal": checked["proposal"]},
                          _folder(tmp_path, "apply"), deadline)
    shared = load_snapshot(Path(str(applied["snapshot"])))
    frozen = freeze_target({"snapshot": str(shared.root)}, _folder(tmp_path, "target"), deadline)
    target_path = Path(str(frozen["target"]))
    before = fingerprint(shared.state), file_hash(target_path), file_hash(target_path.parent / "target.npz")
    common: dict[str, object] = {"snapshot": str(shared.root), "target": str(target_path)}
    result = check_color(common, _folder(tmp_path, "medium"), deadline)
    assert result["status"] == "OK" and result["color_accepted"] is True
    record = read_object(Path(str(result["color_check"])))
    assert record["selected_faces"] == [2, 3]
    assert record["samples_per_face"] == 16
    assert record["target_hash"] == frozen["target_hash"]
    assert record["reconstruction_quality_checked"] is False
    assert len(record["groups"]) == 1
    tight = check_color({**common, "radius": .01}, _folder(tmp_path, "tight"), deadline)
    assert tight["status"] == "OK" and tight["color_accepted"] is False
    assert tight["exceedance_faces"] == [2, 3]
    assert before == (fingerprint(load_snapshot(shared.root).state), file_hash(target_path),
                      file_hash(target_path.parent / "target.npz"))
    with pytest.raises(ValueError, match="STALE_SNAPSHOT"):
        check_color({**common, "state_hash": "stale"}, _folder(tmp_path, "stale"), deadline)
    with pytest.raises(TimeoutError, match="DEADLINE"):
        check_color(common, _folder(tmp_path, "timeout"), time.monotonic() - 1)


def test_zero_modification_target_has_zero_default_samples_and_explicit_protection(tmp_path: Path) -> None:
    source = _source(tmp_path)
    deadline = time.monotonic() + 30
    target = freeze_target({"snapshot": str(source.root), "policy": "source_original", "protected_faces": [0]},
                           _folder(tmp_path, "target"), deadline)
    common: dict[str, object] = {"snapshot": str(source.root), "target": target["target"]}
    result = check_color(common, _folder(tmp_path, "empty"), deadline)
    assert result["color_accepted"] is True
    assert result["exceedance_faces"] == []
    assert result["summary"]["samples"] == 0
    explicit = check_color({**common, "selected_faces": [0]}, _folder(tmp_path, "protected"), deadline)
    record = read_object(Path(str(explicit["color_check"])))
    assert record["protected_faces_evaluated"] == 1
    assert record["protected_faces_changed_in_evaluated_samples"] == 0
    assert explicit["color_accepted"] is True


@pytest.mark.parametrize("count", [True, 0, 15, 257, 16.5])
def test_invalid_sampling_count_is_rejected_before_sampling(tmp_path: Path, count: object) -> None:
    with pytest.raises(ValueError, match="SAMPLES_PER_FACE"):
        check_color({"samples_per_face": count}, tmp_path, time.monotonic() + 30)
