"""Final delivery integration on complete, small constructed mesh evidence."""

import copy
import json
import time
from collections.abc import Mapping
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from pytest import MonkeyPatch

import reseam.budget_final as module
from reseam.budget_delivery import DeliveredArchive, DeliveredEntry
from reseam.budget_final import FinalCandidate, make_delivered_archive, validate_final_candidates
from reseam.budget_material import (
    MaterialAtlas,
    MaterialReference,
    MaterialThresholds,
    bake_material,
    frame_arrays,
    prepare_material_samples,
)
from reseam.budget_solver import BudgetInputs, SolverOptions
from reseam.budget_state import BudgetState, fingerprint
from reseam.budget_texture import ReferenceSurface
from reseam.mesh import corner_map, make_mesh
from reseam.types import MeshState

Report = dict[str, object]


def _inputs() -> BudgetInputs:
    mesh = make_mesh(np.array([[0., 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0]]),
                     np.array([[0, 1, 2], [0, 2, 3]], dtype=np.int64))
    mapping = corner_map(mesh, frozenset())
    uv = np.zeros((4, 2))
    uv[mapping.reshape(-1)] = mesh.vertices[mesh.faces.reshape(-1), :2] * .5 + .2
    surface = MeshState(mesh, frozenset(), uv, mapping)
    image = np.full((4, 4, 4), [128, 90, 60, 255], dtype=np.uint8)
    reference = MaterialReference(
        ReferenceSurface(mesh.vertices, mesh.faces, uv[mapping], image,
                         source_has_normal=True, source_has_orm=True),
        np.full_like(image, [128, 128, 255, 255]), np.full_like(image, [255, 200, 20, 255]),
        *frame_arrays(surface), metadata={"source": "constructed-source.glb"})
    caches = tuple(prepare_material_samples(surface, reference, samples_per_face=2, phase=phase)
                   for phase in (.13, .37, .73))
    return BudgetInputs(mesh, BudgetState(surface), {}, reference, caches[0], caches[1], caches[2],
                        (), {}, "constructed", 0.)


def _atlas(marker: int) -> MaterialAtlas:
    images = tuple(np.full((size, size, 4), [marker, 120, 130, 255], dtype=np.uint8)
                   for size in (4, 2, 1))
    return MaterialAtlas(images, copy.deepcopy(images), copy.deepcopy(images),
                         np.ones((4, 4), dtype=bool), {"support_state_sha256": "support"})


def _candidate(data: BudgetInputs, number: int, feasibility: float = 0.) -> FinalCandidate:
    state = data.initial.copy()
    state.surface.uv += number * .001
    state.texture = np.full((4, 4, 10), .2)
    return FinalCandidate(number, state, _atlas(100 + number), {"valid": True}, feasibility)


def _channel(name: str, mean: float, worst: float) -> Report:
    limits = MaterialThresholds()
    suffix = "_degrees" if name == "normal" else ""
    return {aggregate: float(getattr(limits, f"{name}_{aggregate}{suffix}")) *
            (mean if aggregate == "mean" else worst) for aggregate in ("mean", "p95", "max")}


def _reports(atlas: MaterialAtlas, mean: float = .2, worst: float = .6) -> tuple[Report, Report, Report]:
    channels = {name: {**_channel(name, mean, worst),
                       "area_weighted_mean": _channel(name, mean, worst)["mean"]}
                for name in ("color", "normal", "orm", "render")}
    records: list[Report] = []
    for face in (0, 1):
        for mip in (0, 1):
            records.append({"face": face, "mip": mip, "reference_valid": True,
                "valid_sample_fraction": 1., "important": False, "passed": True, "samples": 4,
                **{name: _channel(name, mean, worst) for name in channels}})
    material: Report = {
        "valid": True, "source_reference_valid": True, "bake_reference_valid": True,
        "support_state_matches": True, "raster_checked": True, "raster_valid": True,
        "reference_coverage": 1., "support_state_sha256": "support",
        "payload_bytes": atlas.payload_bytes, "mip_levels": [0, 1],
        "faces": records, "raster_faces": copy.deepcopy(records), "channels": channels,
        "raster_channels": copy.deepcopy(channels), "quality_violation": 0.,
    }
    render: Report = {
        "valid": True, "reference_coverage": 1., "views": 1, "lights": 1, "mip_levels": [0, 1],
        "thresholds": {name: getattr(MaterialThresholds(), f"render_{name}")
                       for name in ("mean", "p95", "max")},
        "per_view_light_mip": [{"view": 0, "light": 0, "mip": mip, "pixels": 10,
            "passed": True, "reference_valid_fraction": 1., "actual_valid_fraction": 1.,
            **_channel("render", mean, worst)} for mip in (0, 1)],
    }
    geometry: Report = {
        "valid": True, "errors": [], "mean_shape_distortion": .005,
        "shape_cvar95": .008, "shape_p95": .008, "face_shape_distortion": [.002, .008],
        "maximum_anisotropy": 1., "maximum_neighbor_density_ratio": 1.,
        "minimum_gap_pixels": 16., "minimum_border_pixels": 16.,
    }
    return geometry, material, render


def _patch_reports(
    monkeypatch: MonkeyPatch, values: dict[int, tuple[float, float]], seen: list[int], *,
    rejected: tuple[int, ...] = (),
) -> None:
    def material(*args: object, **kwargs: object) -> Report:
        atlas = args[2]
        assert isinstance(atlas, MaterialAtlas)
        number = int(atlas.base_color[0][0, 0, 0]) - 100
        assert kwargs["raster_checks"] is True
        seen.append(number)
        report = _reports(atlas, *values.get(number, (.2, .6)))[1]
        if number in rejected:
            report.update(valid=False, failed_faces=[1], status="CONTROLLED_MATERIAL_FAILURE")
        return report

    def render(*args: object, **kwargs: object) -> Report:
        atlas = args[1]
        assert isinstance(atlas, MaterialAtlas)
        number = int(atlas.base_color[0][0, 0, 0]) - 100
        return _reports(atlas, *values.get(number, (.2, .6)))[2]

    monkeypatch.setattr(module, "evaluate_material", material)
    monkeypatch.setattr(module, "prepare_render_reference", lambda *args, **kwargs: object())
    monkeypatch.setattr(module, "evaluate_render", render)


def _geometry(state: BudgetState) -> Report:
    return _reports(_atlas(100))[0]


def test_baseline_is_recorded_first_and_actual_signed_margin_selects_after_all_candidates(
    tmp_path: Path, monkeypatch: MonkeyPatch,
) -> None:
    data, options = _inputs(), SolverOptions(final_validation_candidates=3)
    seen: list[int] = []
    _patch_reports(monkeypatch, {-1: (.05, .9), 0: (.02, .8), 1: (.3, .6), 2: (.1, .7)}, seen)
    started = time.monotonic()
    deadline = started + 30
    baseline = validate_final_candidates(data, [_candidate(data, -1)], 32, options, tmp_path,
        deadline, started, geometry_validator=_geometry, promote=False, log_prefix="baseline_validation")
    assert seen == [-1]
    assert baseline.accepted is not None and baseline.accepted.candidate_id == -1
    assert not (tmp_path / "state.json").exists()
    result = validate_final_candidates(data,
        [_candidate(data, 0), _candidate(data, 1, 9.), _candidate(data, 2)], 32, options, tmp_path,
        deadline, started, geometry_validator=_geometry, archive=baseline.archive)
    assert seen == [-1, 0, 1, 2]
    assert all(row["status"] == "VALIDATED" for row in result.log)
    assert result.accepted is not None and result.accepted.candidate_id == 1
    assert result.selected.candidate_id == 1
    assert result.archive is not None and result.archive.best is not None
    assert result.archive.best.rank[1] == pytest.approx(-.4)
    accepted = json.loads((tmp_path / "accepted_archive.json").read_text(encoding="utf-8"))
    assert accepted["candidate_id"] == 1
    assert accepted["payload_bytes"] == result.selected.atlas.payload_bytes
    assert (tmp_path / "textures/Normal_mip2.png").exists()


@pytest.mark.parametrize("failure", ["timeout", "material", "export", "geometry"])
def test_prevalidated_baseline_survives_final_failure_and_promotes_its_full_snapshot(
    tmp_path: Path, monkeypatch: MonkeyPatch, failure: str,
) -> None:
    data, options = _inputs(), SolverOptions()
    seen: list[int] = []
    _patch_reports(monkeypatch, {}, seen, rejected=(0,) if failure == "material" else ())
    clock = [100.]
    monkeypatch.setattr(module, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    baseline_candidate = _candidate(data, -1)
    before = fingerprint(baseline_candidate.state)
    baseline = validate_final_candidates(data, [baseline_candidate], 32, options, tmp_path, 130., 100.,
        geometry_validator=_geometry, promote=False, log_prefix="baseline_validation")
    assert baseline.archive is not None and baseline.archive.best is not None
    baseline_folder = baseline.selected_folder
    assert baseline_folder is not None
    original_images = (baseline_folder / "textures/Normal_mip1.png").read_bytes()
    if failure == "timeout":
        clock[0] = 131.
    elif failure == "export":
        def broken_export(*args: object, **kwargs: object) -> Report:
            raise OSError("CONTROLLED_EXPORT_FAILURE")
        monkeypatch.setattr(module, "export_material_glb", broken_export)
    geometry_validator = ((lambda state: {"valid": False, "errors": ["ANISOTROPY_LIMIT"]})
                          if failure == "geometry" else _geometry)
    result = validate_final_candidates(data, [_candidate(data, 0)], 32, options, tmp_path, 130., 100.,
        geometry_validator=geometry_validator, archive=baseline.archive)
    assert result.status == "VALIDATED"
    assert result.accepted is not None and result.accepted.candidate_id == -1
    assert fingerprint(result.accepted.state) == before
    assert result.selected_folder == baseline_folder
    assert (tmp_path / "textures/Normal_mip1.png").read_bytes() == original_images
    assert (baseline_folder / "textures/Normal_mip1.png").read_bytes() == original_images
    if failure == "timeout":
        assert result.log[0]["status"] == "NOT_RUN_FINAL_BUDGET"
    else:
        assert result.log[0]["status"] != "VALIDATED"


def test_completion_after_export_and_manifest_is_separate_from_validation_time(
    tmp_path: Path, monkeypatch: MonkeyPatch,
) -> None:
    data, options = _inputs(), SolverOptions()
    seen: list[int] = []
    _patch_reports(monkeypatch, {}, seen)
    clock = [100.]
    monkeypatch.setattr(module, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    original_save = module.save_final_candidate

    def timed_save(folder: Path, entry: FinalCandidate, geometry: Report, material: Report) -> None:
        original_save(folder, entry, geometry, material)
        clock[0] += 2.

    monkeypatch.setattr(module, "save_final_candidate", timed_save)
    result = validate_final_candidates(data, [_candidate(data, 0)], 32, options, tmp_path, 130., 100.,
        geometry_validator=_geometry, promote=False)
    assert result.archive is not None and result.archive.best is not None
    delivered = result.archive.best
    provenance = delivered.provenance
    assert provenance["validation_completed_at"] == 100.
    assert provenance["export_completed_at"] == delivered.completed_at == 102.
    assert provenance["completed_seconds_from_start"] == 2.
    assert provenance["state_fingerprint"] == fingerprint(result.selected.state)
    assert provenance["encoded_material_sha256"] == module.encoded_fingerprint(result.selected.atlas)
    files = provenance["files"]
    assert isinstance(files, list)
    names = {item["path"] for item in files}
    assert {"material_lod0_preview.glb", "heldout_source_samples.npz", "source_material_faces.npz",
            "textures/BaseColor_mip2.png", "material.json", "render_report.json"} <= names
    source = provenance["source"]
    assert isinstance(source, dict)
    assert source["reference_metadata"] == {"source": "constructed-source.glb"}


@pytest.mark.parametrize("publication_time", [103., 131.])
def test_final_ranking_snapshot_time_controls_publication_and_preserves_baseline(
    tmp_path: Path, monkeypatch: MonkeyPatch, publication_time: float,
) -> None:
    data, options = _inputs(), SolverOptions()
    seen: list[int] = []
    _patch_reports(monkeypatch, {-1: (.2, .9), 0: (.1, .6)}, seen)
    clock = [100.]
    monkeypatch.setattr(module, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    baseline = validate_final_candidates(data, [_candidate(data, -1)], 32, options, tmp_path, 130., 98.,
        geometry_validator=_geometry, promote=False, log_prefix="baseline_validation")
    assert baseline.archive is not None and baseline.archive.best is not None
    original = DeliveredArchive._entry

    def slow_entry(
        self: DeliveredArchive, candidate_id: str, state: BudgetState, atlas: MaterialAtlas,
        geometry: Mapping[str, object], material: Mapping[str, object], render: Mapping[str, object],
        payload_bytes: int, completed_at: float, provenance: Mapping[str, object],
    ) -> DeliveredEntry:
        result = original(self, candidate_id, state, atlas, geometry, material, render,
                          payload_bytes, completed_at, provenance)
        clock[0] = publication_time
        return result

    monkeypatch.setattr(DeliveredArchive, "_entry", slow_entry)
    result = validate_final_candidates(data, [_candidate(data, 0)], 32, options, tmp_path, 130., 98.,
        geometry_validator=_geometry, archive=baseline.archive, promote=False)
    assert result.status == "VALIDATED" and result.accepted is not None
    assert result.archive is not None
    assert result.log[0]["completed_at"] == publication_time
    assert result.log[0]["completed_seconds_from_start"] == publication_time - 98.
    accepted = json.loads((tmp_path / "accepted_archive.json").read_text(encoding="utf-8"))
    if publication_time > 130.:
        assert result.accepted.candidate_id == -1
        assert result.log[0]["status"] == "BUDGET_EXHAUSTED"
        assert result.log[0]["completed_within_deadline"] is False
        assert len(result.archive.entries) == 1
        assert accepted["completed_at"] == 100.
        assert accepted["completed_seconds_from_start"] == 2.
    else:
        assert result.accepted.candidate_id == 0
        assert result.log[0]["status"] == "VALIDATED"
        assert result.archive.best is not None and result.archive.best.completed_at == publication_time
        assert accepted["completed_at"] == publication_time
        assert accepted["completed_seconds_from_start"] == publication_time - 98.
        assert accepted["provenance"]["completed_at"] == publication_time
        assert result.selected_folder is not None
        receipt = json.loads((result.selected_folder / "provenance.json").read_text(encoding="utf-8"))
        assert receipt["completed_at"] == publication_time
        assert receipt["evidence_files_persisted_at"] == 100.
        assert "accepted_archive.json" in receipt["postpublication_bookkeeping"]


def test_backward_clock_rejects_publication_without_negative_elapsed_claim(
    tmp_path: Path, monkeypatch: MonkeyPatch,
) -> None:
    data, options = _inputs(), SolverOptions()
    seen: list[int] = []
    _patch_reports(monkeypatch, {}, seen)
    clock = [100.]
    monkeypatch.setattr(module, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    original = DeliveredArchive._entry

    def backward_entry(
        self: DeliveredArchive, candidate_id: str, state: BudgetState, atlas: MaterialAtlas,
        geometry: Mapping[str, object], material: Mapping[str, object], render: Mapping[str, object],
        payload_bytes: int, completed_at: float, provenance: Mapping[str, object],
    ) -> DeliveredEntry:
        result = original(self, candidate_id, state, atlas, geometry, material, render,
                          payload_bytes, completed_at, provenance)
        clock[0] = 97.
        return result

    monkeypatch.setattr(DeliveredArchive, "_entry", backward_entry)
    result = validate_final_candidates(data, [_candidate(data, 0)], 32, options, tmp_path, 130., 98.,
        geometry_validator=_geometry, promote=False)
    assert result.accepted is None and result.status == "OUTPUT_VALIDATION_FAILED"
    assert result.log[0]["reason"] == "COMPLETION_CLOCK_MOVED_BACKWARDS"
    assert result.log[0]["completed_seconds_from_start"] is None
    assert result.log[0]["completion_elapsed_valid"] is False
    assert result.log[0]["wall_seconds"] is None
    assert result.log[0]["wall_clock_valid"] is False


def test_postpublication_receipt_failure_preserves_committed_delivery_status(
    tmp_path: Path, monkeypatch: MonkeyPatch,
) -> None:
    data, options = _inputs(), SolverOptions()
    seen: list[int] = []
    _patch_reports(monkeypatch, {}, seen)
    original = module.write_json
    writes = [0]

    def receipt_failure(path: Path, value: object) -> None:
        if path.name == "provenance.json":
            writes[0] += 1
            if writes[0] == 2:
                raise OSError("CONTROLLED_POSTPUBLICATION_RECEIPT_FAILURE")
        original(path, value)

    monkeypatch.setattr(module, "write_json", receipt_failure)
    result = validate_final_candidates(data, [_candidate(data, 0)], 32, options, tmp_path,
        time.monotonic() + 30, time.monotonic(), geometry_validator=_geometry, promote=False)
    assert result.status == "VALIDATED" and result.accepted is not None
    assert result.log[0]["status"] == "VALIDATED"
    assert result.log[0]["postpublication_receipt_error"] == "CONTROLLED_POSTPUBLICATION_RECEIPT_FAILURE"


def test_late_export_never_enters_archive_but_raw_failure_evidence_is_retained(
    tmp_path: Path, monkeypatch: MonkeyPatch,
) -> None:
    data, options = _inputs(), SolverOptions()
    seen: list[int] = []
    _patch_reports(monkeypatch, {}, seen)
    clock = [100.]
    monkeypatch.setattr(module, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    original_save = module.save_final_candidate

    def late_save(folder: Path, entry: FinalCandidate, geometry: Report, material: Report) -> None:
        original_save(folder, entry, geometry, material)
        clock[0] = 131.

    monkeypatch.setattr(module, "save_final_candidate", late_save)
    result = validate_final_candidates(data, [_candidate(data, 0)], 32, options, tmp_path, 130., 100.,
        geometry_validator=_geometry, promote=False)
    assert result.status == "BUDGET_EXHAUSTED" and result.accepted is None
    assert result.archive is not None and result.archive.best is None
    folder = tmp_path / "final_validation/candidate_000"
    material = json.loads((folder / "material.json").read_text(encoding="utf-8"))
    assert len(material["faces"]) == len(material["raster_faces"]) == 4
    assert (folder / "textures/ORM_mip2.png").exists()
    assert (folder / "source_material_faces.npz").exists()
    assert not (tmp_path / "accepted_archive.json").exists()
    assert result.log[0]["validation_completed_at"] == 100.
    assert result.log[0]["export_completed_at"] == 131.
    assert result.log[0]["completed_within_deadline"] is False


def test_material_timeout_still_preserves_actual_maps_and_invalid_source_faces(
    tmp_path: Path, monkeypatch: MonkeyPatch,
) -> None:
    data, options = _inputs(), SolverOptions()

    def timeout(*args: object, **kwargs: object) -> Report:
        raise TimeoutError("CONTROLLED_MATERIAL_TIMEOUT")

    data.heldout.reference.valid[0] = False
    monkeypatch.setattr(module, "evaluate_material", timeout)
    result = validate_final_candidates(data, [_candidate(data, 0)], 32, options, tmp_path,
        time.monotonic() + 30, time.monotonic(), geometry_validator=_geometry, promote=False)
    assert result.status == "BUDGET_EXHAUSTED" and result.accepted is None
    folder = tmp_path / "final_validation/candidate_000"
    with np.load(folder / "heldout_source_samples.npz") as raw:
        assert not raw["reference_valid"][0]
        assert np.array_equal(raw["source_face_ids"], data.heldout.reference.target_faces)
        assert np.array_equal(raw["source_alpha"], data.heldout.reference.base_color[:, 3])
        assert raw["projection_confidence_heuristic"][0] == 0.
        assert np.all((raw["projection_confidence_heuristic"] >= 0.)
                      & (raw["projection_confidence_heuristic"] <= 1.))
    source = json.loads((folder / "source_reference.json").read_text(encoding="utf-8"))
    assert source["projection_scale"] == data.reference.diagonal
    assert source["projection_distance_limit"] == options.max_projection_fraction * data.reference.diagonal
    assert source["projection_confidence_status"] == "HEURISTIC_NOT_CALIBRATED"
    assert source["visibility_status"] == "NOT_EVALUATED"
    assert (folder / "textures/Normal_mip0.png").exists()
    assert (folder / "source_material_faces.npz").exists()


def test_empty_later_pool_promotes_baseline_without_revalidation_and_preserves_previous_runs(
    tmp_path: Path, monkeypatch: MonkeyPatch,
) -> None:
    data, options = _inputs(), SolverOptions()
    seen: list[int] = []
    _patch_reports(monkeypatch, {}, seen)
    started, deadline = time.monotonic(), time.monotonic() + 30
    baseline = validate_final_candidates(data, [_candidate(data, -1)], 32, options, tmp_path,
        deadline, started, geometry_validator=_geometry, promote=False, log_prefix="baseline_validation")
    result = validate_final_candidates(data, [], 32, options, tmp_path, deadline, started,
        geometry_validator=_geometry, archive=baseline.archive)
    assert seen == [-1] and result.selected.candidate_id == -1 and result.accepted is not None
    previous_log = (tmp_path / "final_validation_log.json").read_bytes()
    again = validate_final_candidates(data, [], 32, options, tmp_path, deadline, started,
        archive=result.archive, promote=False)
    assert again.accepted is not None and seen == [-1]
    assert (tmp_path / "final_validation_log.json").read_bytes() == previous_log
    assert (tmp_path / "final_validation_001_log.json").exists()


def test_pool_is_snapshotted_before_heldout_feedback_and_same_payload_is_not_revalidated(
    tmp_path: Path, monkeypatch: MonkeyPatch,
) -> None:
    data, options = _inputs(), SolverOptions()
    candidates = [_candidate(data, 0), _candidate(data, 1)]
    seen: list[int] = []
    _patch_reports(monkeypatch, {}, seen)
    def mutate_original(*args: object, **kwargs: object) -> Report:
        atlas = args[2]
        assert isinstance(atlas, MaterialAtlas)
        number = int(atlas.base_color[0][0, 0, 0]) - 100
        seen.append(number)
        report = _reports(atlas)[1]
        candidates[1].atlas.base_color[0][:] = 0
        candidates[1].state.surface.uv[:] = 0
        return report

    monkeypatch.setattr(module, "evaluate_material", mutate_original)
    started, deadline = time.monotonic(), time.monotonic() + 30
    result = validate_final_candidates(data, candidates, 32, options, tmp_path,
        deadline, started, geometry_validator=_geometry, promote=False)
    assert seen == [0, 1]
    assert result.archive is not None and len(result.archive.entries) == 2
    # Candidate zero is unchanged, so a repeated call reuses its exact snapshot.
    repeated = validate_final_candidates(data, [candidates[0]], 32, options, tmp_path,
        deadline, started, geometry_validator=_geometry, archive=result.archive, promote=False)
    assert seen == [0, 1]
    assert repeated.log[0]["status"] == "ALREADY_VALIDATED_DELIVERY"


def test_default_protocol_includes_actual_tail_gap_border_and_payload() -> None:
    options = SolverOptions()
    archive = make_delivered_archive(options, 100.)
    assert archive.geometry_thresholds["shape_cvar95"] == .1
    assert archive.geometry_thresholds["minimum_gap_pixels"] == options.gap_pixels
    assert archive.geometry_thresholds["minimum_border_pixels"] == options.border_pixels
    assert archive.material_thresholds == MaterialThresholds()


def test_real_small_mesh_runs_geometry_heldout_raster_render_and_exports(tmp_path: Path) -> None:
    data, options = _inputs(), SolverOptions(render_size=16)
    atlas = bake_material(data.initial.surface, data.reference, 32)
    candidate = FinalCandidate(-1, data.initial, atlas, {"valid": True}, 0.)
    started = time.monotonic()
    result = validate_final_candidates(data, [candidate], 32, options, tmp_path,
        started + 30, started, promote=False, log_prefix="baseline_validation")
    assert result.accepted is not None, result.log
    assert result.geometry["valid"] and result.material["raster_valid"] and result.render["valid"]
    assert result.archive is not None and result.archive.best is not None
    metrics = {metric.name for metric in result.archive.best.ranking_metrics}
    assert "geometry.shape_cvar95" in metrics
    assert "raster.face0.mip0.color.max" in metrics
    assert "render.view0.light0.mip1.max" in metrics
    assert result.selected_folder is not None
    assert (result.selected_folder / "material_lod0_preview.glb").exists()


def test_failed_convenience_copy_does_not_revoke_known_delivery(
    tmp_path: Path, monkeypatch: MonkeyPatch,
) -> None:
    data, options = _inputs(), SolverOptions()
    seen: list[int] = []
    _patch_reports(monkeypatch, {}, seen)
    (tmp_path / "state.json").write_text("previous-output", encoding="utf-8")
    result = validate_final_candidates(data, [_candidate(data, 0)], 32, options, tmp_path,
        time.monotonic() + 30, time.monotonic(), geometry_validator=_geometry)
    assert result.status == "VALIDATED" and result.accepted is not None
    assert result.promotion["status"] == "OUTPUT_COPY_FAILED"
    assert (tmp_path / "state.json").read_text(encoding="utf-8") == "previous-output"
    assert result.selected_folder is not None and (result.selected_folder / "state.json").exists()


def test_empty_pool_without_delivered_baseline_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="EMPTY_FINAL_CANDIDATE_POOL"):
        validate_final_candidates(_inputs(), [], 32, SolverOptions(), tmp_path, 100., 0.,
                                  archive=DeliveredArchive(deadline=100.))


def test_mutating_final_validator_cannot_certify_different_payload_than_saved_maps(
    tmp_path: Path, monkeypatch: MonkeyPatch,
) -> None:
    data, options = _inputs(), SolverOptions()
    seen: list[int] = []
    _patch_reports(monkeypatch, {}, seen)

    def mutate(*args: object, **kwargs: object) -> Report:
        atlas = args[2]
        assert isinstance(atlas, MaterialAtlas)
        report = _reports(atlas)[1]
        atlas.base_color[0][:] = 0
        return report

    monkeypatch.setattr(module, "evaluate_material", mutate)
    result = validate_final_candidates(data, [_candidate(data, 0)], 32, options, tmp_path,
        time.monotonic() + 30, time.monotonic(), geometry_validator=_geometry, promote=False)
    assert result.accepted is None and result.status == "OUTPUT_VALIDATION_FAILED"
    assert result.log[0]["reason"] == "FINAL_EVIDENCE_PAYLOAD_MUTATED"
    assert (tmp_path / "final_validation/candidate_000/textures/BaseColor_mip0.png").exists()


def test_structural_rejection_survives_expected_invalid_preview_export(
    tmp_path: Path, monkeypatch: MonkeyPatch,
) -> None:
    data, options = _inputs(), SolverOptions()

    def invalid_geometry(state: BudgetState) -> Report:
        return {"valid": False, "errors": ["FLIPPED_TRIANGLE"]}

    def invalid_export(*args: object, **kwargs: object) -> Report:
        raise ValueError("FRAME_INVALID")

    monkeypatch.setattr(module, "export_material_glb", invalid_export)
    result = validate_final_candidates(data, [_candidate(data, 0)], 32, options, tmp_path,
        time.monotonic() + 30, time.monotonic(), geometry_validator=invalid_geometry, promote=False)
    assert result.status == "STRUCTURALLY_INVALID" and result.accepted is None
    assert result.log[0]["diagnostic_export_error"] == "FRAME_INVALID"
    folder = tmp_path / "final_validation/candidate_000"
    assert (folder / "textures/Normal_mip1.png").exists()
    assert json.loads((folder / "geometry.json").read_text(encoding="utf-8"))["errors"] == ["FLIPPED_TRIANGLE"]
