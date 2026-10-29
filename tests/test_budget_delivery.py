"""The archive consumes final-validator evidence; no asset experiments run here."""

import copy
from collections.abc import Callable, Mapping
from dataclasses import FrozenInstanceError

import numpy as np
import pytest

from reseam.budget_delivery import (
    DeliveredArchive,
    DeliveredEntry,
    DeliveryQualityWeights,
    DeliverySeamWeights,
)
from reseam.budget_material import (
    MaterialAtlas,
    MaterialReference,
    MaterialThresholds,
    bake_material,
    evaluate_material,
    frame_arrays,
)
from reseam.budget_render import evaluate_render, prepare_render_reference
from reseam.budget_state import BudgetState, exclusive_faces, validate_budget_geometry
from reseam.budget_texture import ReferenceSurface
from reseam.joint_types import ReuseGroup
from reseam.mesh import corner_map, make_mesh
from reseam.types import MeshState

Report = dict[str, object]


def _state(*, separated: bool = False) -> BudgetState:
    mesh = make_mesh(np.asarray([[0., 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0]]),
                     np.asarray([[0, 1, 2], [0, 2, 3]], dtype=np.int64))
    corners = corner_map(mesh, frozenset())
    uv = np.zeros((int(corners.max()) + 1, 2))
    uv[corners.reshape(-1)] = mesh.vertices[mesh.faces.reshape(-1), :2] * 0.5 + 0.2
    state = BudgetState(MeshState(mesh, frozenset(), uv, corners),
                        texture=np.ones((4, 4, 10)), evidence={"training": {"quality": 9.0}})
    return exclusive_faces(state, (0,)) if separated else state


def _atlas(size: int = 4) -> MaterialAtlas:
    images = []
    while True:
        images.append(np.full((size, size, 4), [100, 120, 130, 255], dtype=np.uint8))
        if size == 1:
            break
        size //= 2
    return MaterialAtlas(tuple(images), tuple(image.copy() for image in images),
                         tuple(image.copy() for image in images),
                         np.ones(images[0].shape[:2], dtype=bool),
                         {"support_state_sha256": "validated-support"})


def _channel(channel: str, mean: float, worst: float, limits: MaterialThresholds) -> Report:
    suffix = "_degrees" if channel == "normal" else ""
    return {name: float(getattr(limits, f"{channel}_{name}{suffix}")) * (mean if name == "mean" else worst)
            for name in ("mean", "p95", "max")}


def _reports(
    atlas: MaterialAtlas, *, mean: float = 0.2, worst: float = 0.6,
    important: tuple[int, ...] = (), multiplier: float = 1.0,
) -> tuple[Report, Report, Report]:
    limits = MaterialThresholds(important_multiplier=multiplier)
    channels: Report = {}
    for name in ("color", "normal", "orm", "render"):
        summary = _channel(name, mean, worst, limits)
        summary["area_weighted_mean"] = summary["mean"]
        channels[name] = summary
    probes: list[Report] = []
    raster: list[Report] = []
    for mip in (0, 1):
        for face in (0, 1):
            row: Report = {"face": face, "mip": mip, "reference_valid": True,
                           "valid_sample_fraction": 1.0, "important": face in important, "passed": True}
            row.update({name: _channel(name, mean, worst, limits)
                        for name in ("color", "normal", "orm", "render")})
            probes.append(row)
            raster.append({**copy.deepcopy(row), "samples": 4})
    material: Report = {
        "valid": True, "source_reference_valid": True, "bake_reference_valid": True,
        "support_state_matches": True, "raster_checked": True, "raster_valid": True,
        "reference_coverage": 1.0, "support_state_sha256": "validated-support",
        "payload_bytes": atlas.payload_bytes, "mip_levels": [0, 1],
        "faces": probes, "raster_faces": raster,
        "channels": channels, "raster_channels": copy.deepcopy(channels),
        # Production validators historically clamp this value; ranking must not use it.
        "quality_violation": 0.0,
    }
    render_rows: list[Report] = []
    for mip in (0, 1):
        row = {"view": 0, "light": 0, "mip": mip, "pixels": 10, "passed": True,
               "reference_valid_fraction": 1.0, "actual_valid_fraction": 1.0}
        row.update(_channel("render", mean, worst, limits))
        render_rows.append(row)
    render: Report = {
        "valid": True, "reference_coverage": 1.0, "views": 1, "lights": 1, "mip_levels": [0, 1],
        "thresholds": {name: getattr(limits, f"render_{name}") for name in ("mean", "p95", "max")},
        "per_view_light_mip": render_rows,
    }
    geometry: Report = {
        "valid": True, "mean_shape_distortion": 0.005, "face_shape_distortion": [0.002, 0.008],
        "maximum_anisotropy": 1.1, "maximum_neighbor_density_ratio": 1.1,
        "minimum_gap_pixels": 8.0, "minimum_border_pixels": 8.0,
    }
    return geometry, material, render


def _offer(
    archive: DeliveredArchive, name: str, *, state: BudgetState | None = None,
    atlas: MaterialAtlas | None = None, reports: tuple[Report, Report, Report] | None = None,
    completed_at: float = 9.0,
    completion_clock: Callable[[], float] | None = None,
) -> bool:
    atlas = atlas or _atlas()
    geometry, material, render = reports or _reports(atlas)
    return archive.offer(candidate_id=name, state=state or _state(), atlas=atlas, geometry=geometry,
                         material=material, render=render, payload_bytes=atlas.payload_bytes,
                         completed_at=completed_at,
                         provenance={"output_directory": f"validated/{name}", "exported": True},
                         completion_clock=completion_clock).accepted


def _best(archive: DeliveredArchive) -> DeliveredEntry:
    entry = archive.best
    assert entry is not None
    return entry


def test_final_bad_dev_better_cannot_replace_complete_valid_baseline() -> None:
    archive = DeliveredArchive(deadline=10)
    baseline = _state()
    baseline.evidence["development_cost"] = 9.0
    assert _offer(archive, "baseline", state=baseline)
    before = _best(archive)
    bad = baseline.copy()
    bad.surface.uv += 0.1
    bad.evidence["development_cost"] = 0.0
    reports = _reports(_atlas(), mean=0.01, worst=0.01)
    reports[1]["valid"] = False
    assert not _offer(archive, "dev-better-final-bad", state=bad, reports=reports)
    after = _best(archive)
    assert after.candidate_id == "baseline"
    assert np.array_equal(before.state.surface.uv, after.state.surface.uv)
    assert np.array_equal(before.atlas.normal[1], after.atlas.normal[1])
    assert after.state.evidence["development_cost"] == 9.0
    assert after.geometry["valid"] is True and after.material["valid"] is True
    assert after.render["valid"] is True
    assert after.provenance["output_directory"] == "validated/baseline"


def test_exact_order_uses_signed_margin_then_quality_then_actual_seams_then_id() -> None:
    archive = DeliveredArchive(deadline=10)
    atlas = _atlas()
    assert _offer(archive, "first-valid", atlas=atlas, reports=_reports(atlas, mean=.1, worst=.8))
    assert _offer(archive, "safer-margin", atlas=atlas, reports=_reports(atlas, mean=.3, worst=.6),
                  state=_state(separated=True))
    assert _best(archive).candidate_id == "safer-margin"
    assert _best(archive).rank[1] == pytest.approx(-.4)
    assert _offer(archive, "lower-quality-cost", atlas=atlas, reports=_reports(atlas, mean=.2, worst=.6),
                  state=_state(separated=True))
    assert _best(archive).candidate_id == "lower-quality-cost"
    assert _offer(archive, "fewer-seams", atlas=atlas, reports=_reports(atlas, mean=.2, worst=.6))
    assert _best(archive).candidate_id == "fewer-seams"
    assert dict(_best(archive).seam_components) == {"seam_length_normalized": 0.0, "charts": 1.0}
    assert _offer(archive, "a-deterministic-last-tie", atlas=atlas, reports=_reports(atlas, mean=.2, worst=.6))
    assert _best(archive).candidate_id == "a-deterministic-last-tie"
    assert [entry.rank for entry in archive.entries] == sorted(entry.rank for entry in archive.entries)


def test_bytes_precede_quality_and_late_result_preserves_best() -> None:
    archive = DeliveredArchive(deadline=10)
    assert _offer(archive, "larger-safer", atlas=_atlas(8), reports=_reports(_atlas(8), mean=.1, worst=.1))
    assert _offer(archive, "smaller", atlas=_atlas(), reports=_reports(_atlas(), mean=.3, worst=.9))
    assert _best(archive).candidate_id == "smaller"
    assert not _offer(archive, "late", atlas=_atlas(), reports=_reports(_atlas(), mean=.01, worst=.01),
                      completed_at=10.00001)
    assert _best(archive).candidate_id == "smaller" and len(archive.entries) == 2
    assert _offer(archive, "at-deadline", completed_at=10)
    assert not _offer(archive, "nonfinite", completed_at=float("nan"))


def test_ranking_snapshot_finishing_after_deadline_cannot_replace_prior_delivery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    archive = DeliveredArchive(deadline=130.)
    assert _offer(archive, "baseline", completed_at=100.)
    baseline = _best(archive)
    clock = [100.]
    original = DeliveredArchive._entry

    def slow_entry(
        self: DeliveredArchive, candidate_id: str, state: BudgetState, atlas: MaterialAtlas,
        geometry: Mapping[str, object], material: Mapping[str, object], render: Mapping[str, object],
        payload_bytes: int, completed_at: float, provenance: Mapping[str, object],
    ) -> DeliveredEntry:
        result = original(self, candidate_id, state, atlas, geometry, material, render,
                          payload_bytes, completed_at, provenance)
        clock[0] = 131.
        return result

    monkeypatch.setattr(DeliveredArchive, "_entry", slow_entry)
    assert not _offer(archive, "better-but-late", reports=_reports(_atlas(), mean=.01, worst=.01),
                      completed_at=100., completion_clock=lambda: clock[0])
    assert archive.entries == (baseline,)
    assert _best(archive).completed_at == 100.


def test_live_publication_timestamp_follows_snapshot_and_replaces_stale_provenance() -> None:
    archive = DeliveredArchive(deadline=130.)
    atlas = _atlas()
    geometry, material, render = _reports(atlas)
    offered = archive.offer(candidate_id="timed", state=_state(), atlas=atlas, geometry=geometry,
        material=material, render=render, payload_bytes=atlas.payload_bytes, completed_at=100.,
        provenance={"started_at": 98., "completed_at": 100., "completed_seconds_from_start": 2.},
        completion_clock=lambda: 102.)
    assert offered.accepted and offered.completed_at == 102.
    assert _best(archive).completed_at == 102.
    assert _best(archive).provenance["completed_at"] == 102.
    assert _best(archive).provenance["completed_seconds_from_start"] == 4.


@pytest.mark.parametrize("observed, reason", [
    (float("nan"), "INVALID_COMPLETION_CLOCK"),
    (float("inf"), "INVALID_COMPLETION_CLOCK"),
    (float("-inf"), "INVALID_COMPLETION_CLOCK"),
    (True, "INVALID_COMPLETION_CLOCK"),
    (99., "COMPLETION_CLOCK_MOVED_BACKWARDS"),
    (131., "COMPLETED_AFTER_DEADLINE"),
])
def test_invalid_live_completion_clock_never_enters_archive(observed: float, reason: str) -> None:
    archive = DeliveredArchive(deadline=130.)
    atlas = _atlas()
    geometry, material, render = _reports(atlas)
    offered = archive.offer(candidate_id="invalid-time", state=_state(), atlas=atlas, geometry=geometry,
        material=material, render=render, payload_bytes=atlas.payload_bytes, completed_at=100.,
        provenance={"started_at": 98.}, completion_clock=lambda: observed)
    assert not offered.accepted and offered.reason == reason
    assert archive.best is None


def test_negative_elapsed_live_completion_time_is_rejected() -> None:
    archive = DeliveredArchive(deadline=130.)
    atlas = _atlas()
    geometry, material, render = _reports(atlas)
    offered = archive.offer(candidate_id="future-origin", state=_state(), atlas=atlas, geometry=geometry,
        material=material, render=render, payload_bytes=atlas.payload_bytes, completed_at=100.,
        provenance={"started_at": 103.}, completion_clock=lambda: 102.)
    assert not offered.accepted and offered.reason == "INVALID_COMPLETION_ELAPSED_TIME"
    assert archive.best is None


def test_absolute_monotonic_epoch_can_be_negative_when_elapsed_is_consistent() -> None:
    archive = DeliveredArchive(deadline=0.)
    atlas = _atlas()
    geometry, material, render = _reports(atlas)
    offered = archive.offer(candidate_id="negative-epoch", state=_state(), atlas=atlas, geometry=geometry,
        material=material, render=render, payload_bytes=atlas.payload_bytes, completed_at=-100.,
        provenance={"started_at": -102.}, completion_clock=lambda: -98.)
    assert offered.accepted and _best(archive).completed_at == -98.
    assert _best(archive).provenance["completed_seconds_from_start"] == 4.


@pytest.mark.parametrize("flag", ["valid", "source_reference_valid", "bake_reference_valid",
                                   "support_state_matches", "raster_checked", "raster_valid"])
def test_missing_required_material_flags_are_rejected_without_erasing_best(flag: str) -> None:
    archive = DeliveredArchive(deadline=10)
    assert _offer(archive, "baseline")
    reports = _reports(_atlas())
    del reports[1][flag]
    assert not _offer(archive, "missing-flag", reports=reports)
    assert _best(archive).candidate_id == "baseline"


def test_probe_raster_and_independent_view_metrics_all_enter_signed_margin() -> None:
    archive = DeliveredArchive(deadline=10)
    reports = _reports(_atlas(), mean=.1, worst=.3)
    raster_rows = reports[1]["raster_faces"]
    assert isinstance(raster_rows, list)
    raster_rows[0]["color"]["max"] = MaterialThresholds().color_max * .9
    assert _offer(archive, "raster-bottleneck", reports=reports)
    assert _best(archive).rank[1] == pytest.approx(-.1)
    names = {metric.name for metric in _best(archive).ranking_metrics}
    assert "probes.face0.mip0.color.mean" in names
    assert "raster.face0.mip0.color.max" in names
    assert "render.view0.light0.mip1.max" in names
    invalid = copy.deepcopy(reports)
    view_rows = invalid[2]["per_view_light_mip"]
    assert isinstance(view_rows, list)
    view_rows[0]["max"] = MaterialThresholds().render_max * 1.01
    # A stale passed=True cannot override the actual measured threshold.
    assert not _offer(archive, "actual-render-over-limit", reports=invalid)
    assert _best(archive).candidate_id == "raster-bottleneck"


def test_importance_multiplier_is_applied_to_probe_and_raster_thresholds() -> None:
    limits = MaterialThresholds(important_multiplier=.5)
    archive = DeliveredArchive(deadline=10, material_thresholds=limits, important_faces=(0,))
    reports = _reports(_atlas(), mean=.1, worst=.4, important=(0,), multiplier=.5)
    assert _offer(archive, "important", reports=reports)
    assert _best(archive).rank[1] == pytest.approx(-.2)
    included = {metric.name: metric for metric in _best(archive).ranking_metrics}
    assert included["probes.face0.mip0.color.max"].threshold == limits.color_max * .5
    assert included["raster.face0.mip0.color.max"].threshold == limits.color_max * .5
    assert included["probes.face1.mip0.color.max"].threshold == limits.color_max
    reports = _reports(_atlas(), mean=.1, worst=.6, important=(0,), multiplier=.5)
    assert not _offer(archive, "important-over-limit", reports=reports)


def test_configured_geometry_shape_tail_density_gap_border_and_kappa_are_ranked() -> None:
    bounds = {"mean_shape_distortion": .025, "shape_p95": .02, "maximum_anisotropy": 2.,
              "maximum_neighbor_density_ratio": 2., "minimum_gap_pixels": 4., "minimum_border_pixels": 4.}
    archive = DeliveredArchive(deadline=10, geometry_thresholds=bounds)
    assert _offer(archive, "geometry-ranked")
    metrics = {metric.name: metric for metric in _best(archive).ranking_metrics}
    assert {"geometry." + name for name in bounds}.issubset(metrics)
    assert metrics["geometry.shape_p95"].value == pytest.approx(.008)
    assert metrics["geometry.minimum_gap_pixels"].normalized_violation == -1
    reports = _reports(_atlas())
    reports[0]["maximum_anisotropy"] = 2.01
    assert not _offer(archive, "geometry-over-limit", reports=reports)
    del reports[0]["maximum_anisotropy"]
    assert not _offer(archive, "geometry-missing", reports=reports)


def test_quality_weights_and_seam_weights_are_declared_and_positive() -> None:
    with pytest.raises(ValueError, match="positive"):
        DeliveryQualityWeights(normal=0)
    with pytest.raises(ValueError, match="positive"):
        DeliverySeamWeights(charts=-1)
    with pytest.raises(ValueError, match="positive"):
        DeliveredArchive(deadline=10, geometry_thresholds={"mean_shape_distortion": 0})
    with pytest.raises(ValueError, match="Unknown"):
        DeliveredArchive(deadline=10, geometry_thresholds={"mystery": 1})
    archive = DeliveredArchive(deadline=10, quality_weights=DeliveryQualityWeights(color=2),
                               seam_weights=DeliverySeamWeights(seam_length=3, charts=4))
    assert _offer(archive, "weighted", state=_state(separated=True))
    entry = _best(archive)
    components = dict(entry.seam_components)
    assert entry.rank[3] == pytest.approx(3 * components["seam_length_normalized"] + 4 * components["charts"])
    assert entry.rank[2] == pytest.approx(sum(value * weight for _, value, weight in entry.quality_components)
                                        / sum(weight for _, _, weight in entry.quality_components))


def test_deep_snapshot_preserves_original_geometry_corners_groups_and_all_encoded_mips() -> None:
    archive = DeliveredArchive(deadline=10)
    original = _state()
    original.groups = (ReuseGroup("saved-group", (0,), (1,), (1., 0, 0), .5),)
    original.correspondence_records = ({"source_vertices": [0, 1], "barycentric": [.2, .3, .5]},)
    atlas = _atlas()
    reports = _reports(atlas)
    before = copy.deepcopy(original)
    original_images = copy.deepcopy((atlas.base_color, atlas.normal, atlas.orm))
    assert _offer(archive, "snapshot", state=original, atlas=atlas, reports=reports)
    original.surface.uv[:] = 0
    original.surface.uv_faces[:] = 0
    for array in (original.surface.mesh.vertices, original.surface.mesh.faces, original.surface.mesh.areas):
        array.setflags(write=True)
    original.surface.mesh.vertices[:] = 99
    original.surface.mesh.faces[:] = 0
    original.surface.mesh.areas[:] = 99
    original.groups = ()
    original.correspondence_records[0]["source_vertices"] = [99]
    original.evidence.clear()
    assert original.texture is not None
    original.texture[:] = 0
    for chain in (atlas.base_color, atlas.normal, atlas.orm):
        for image in chain:
            image[:] = 0
    reports[0]["valid"] = False
    entry = _best(archive)
    for actual, expected in ((entry.state.surface.uv, before.surface.uv),
                             (entry.state.surface.uv_faces, before.surface.uv_faces),
                             (entry.state.surface.mesh.vertices, before.surface.mesh.vertices),
                             (entry.state.surface.mesh.faces, before.surface.mesh.faces),
                             (entry.state.surface.mesh.areas, before.surface.mesh.areas)):
        assert np.array_equal(actual, expected)
    assert entry.state.groups == before.groups
    assert entry.state.correspondence_records == before.correspondence_records
    archived_texture = entry.state.texture
    assert archived_texture is not None and before.texture is not None
    assert np.array_equal(archived_texture, before.texture)
    assert entry.geometry["valid"] is True
    for actual, expected in zip((entry.atlas.base_color, entry.atlas.normal, entry.atlas.orm),
                                original_images, strict=True):
        assert all(np.array_equal(left, right) for left, right in zip(actual, expected, strict=True))
    # Mutating a returned payload or report also cannot mutate the archived entry.
    entry.state.surface.uv[:] = -10
    entry.atlas.orm[1][:] = 3
    entry.material["valid"] = False
    entry.provenance["output_directory"] = "changed"
    assert np.array_equal(_best(archive).state.surface.uv, before.surface.uv)
    assert np.array_equal(_best(archive).atlas.orm[1], original_images[2][1])
    assert _best(archive).material["valid"] is True
    assert _best(archive).provenance["output_directory"] == "validated/snapshot"
    with pytest.raises(FrozenInstanceError):
        entry.candidate_id = "changed"  # type: ignore[misc]


@pytest.mark.parametrize("damage", ["probe-coverage", "raster-coverage", "view-coverage", "support",
                                     "material-reference", "render-reference", "atlas-chain"])
def test_incomplete_final_evidence_is_not_deliverable(damage: str) -> None:
    archive = DeliveredArchive(deadline=10)
    atlas = _atlas()
    reports = _reports(atlas)
    if damage == "probe-coverage":
        records = reports[1]["faces"]
        assert isinstance(records, list)
        records.pop()
    elif damage == "raster-coverage":
        records = reports[1]["raster_faces"]
        assert isinstance(records, list)
        records.pop()
    elif damage == "view-coverage":
        records = reports[2]["per_view_light_mip"]
        assert isinstance(records, list)
        records.pop()
    elif damage == "support":
        atlas.metadata["support_state_sha256"] = "other-state"
    elif damage == "material-reference":
        reports[1]["reference_coverage"] = .99
    elif damage == "render-reference":
        reports[2]["reference_coverage"] = .99
    else:
        atlas = MaterialAtlas(atlas.base_color[:-1], atlas.normal[:-1], atlas.orm[:-1],
                              atlas.coverage, atlas.metadata)
        reports[1]["payload_bytes"] = atlas.payload_bytes
    assert not _offer(archive, "incomplete", atlas=atlas, reports=reports)
    assert archive.best is None


def test_frozen_thresholds_do_not_follow_external_protocol_mutations() -> None:
    bounds = {"mean_shape_distortion": .025}
    archive = DeliveredArchive(deadline=10, geometry_thresholds=bounds)
    bounds["mean_shape_distortion"] = 100
    assert archive.geometry_thresholds["mean_shape_distortion"] == .025
    with pytest.raises(TypeError):
        archive.geometry_thresholds["mean_shape_distortion"] = 100  # type: ignore[index]
    with pytest.raises(FrozenInstanceError):
        archive.deadline = 100  # type: ignore[misc]
    protocol = archive.protocol
    assert protocol["order"] == ["payload_bytes", "signed_max_normalized_violation", "quality_cost",
                                  "seam_fragment_cost", "candidate_id"]
    assert _offer(archive, "frozen-protocol")


def test_actual_small_mesh_final_validator_reports_can_be_archived() -> None:
    state = _state()
    color = np.full((4, 4, 4), [128, 80, 40, 255], dtype=np.uint8)
    normal = np.full_like(color, [128, 128, 255, 255])
    orm = np.full_like(color, [255, 128, 0, 255])
    source = ReferenceSurface(state.surface.mesh.vertices, state.surface.mesh.faces,
                              state.surface.uv[state.surface.uv_faces], color,
                              source_has_normal=True, source_has_orm=True)
    reference = MaterialReference(source, normal, orm, *frame_arrays(state.surface))
    atlas = bake_material(state.surface, reference, 32, padding_pixels=4)
    geometry = validate_budget_geometry(state, 32, gap_pixels=2, border_pixels=2)
    material = evaluate_material(state.surface, reference, atlas, samples_per_face=2, raster_checks=True)
    limits = MaterialThresholds()
    prepared = prepare_render_reference(state.surface.mesh, reference, size=8, azimuths=(17.,))
    render = evaluate_render(state.surface, atlas, prepared, mean_limit=limits.render_mean,
                             p95_limit=limits.render_p95, max_limit=limits.render_max)
    archive = DeliveredArchive(deadline=10, geometry_thresholds={
        "mean_shape_distortion": .025, "maximum_anisotropy": 2.,
        "maximum_neighbor_density_ratio": 2., "minimum_gap_pixels": 2., "minimum_border_pixels": 2.,
    })
    offered = archive.offer(candidate_id="real-small-mesh", state=state, atlas=atlas, geometry=geometry,
                             material=material, render=render, payload_bytes=atlas.payload_bytes,
                             completed_at=1., provenance={"output": "small-constructed-mesh"})
    assert offered.accepted, offered.reason
    assert _best(archive).rank[1] < 0
