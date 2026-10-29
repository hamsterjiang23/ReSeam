import json
import time
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import pytest
from pytest import MonkeyPatch

import reseam.budget_final as final_module
from reseam.budget_material import (
    MaterialAtlas,
    MaterialReference,
    MaterialThresholds,
    PreparedMaterialSamples,
    bake_material,
    frame_arrays,
    prepare_material_samples,
)
from reseam.budget_optimize import FeatureSamples
from reseam.budget_restoration import BudgetRestorationResult
from reseam.budget_solver import BudgetInputs, SolverOptions, _development_feasibility, solve_budget_target
from reseam.budget_state import BudgetState, fingerprint
from reseam.budget_texture import ReferenceSurface
from reseam.mesh import corner_map, load_state, make_mesh
from reseam.types import FloatArray, MeshState


def _mapping(value: object) -> dict[str, object]:
    assert isinstance(value, dict)
    return value


def _rows(value: object) -> list[dict[str, object]]:
    assert isinstance(value, list)
    return [_mapping(item) for item in value]


def inputs() -> BudgetInputs:
    mesh = make_mesh(np.array([[0., 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0]]),
                     np.array([[0, 1, 2], [0, 2, 3]], dtype=np.int64))
    mapping = corner_map(mesh, frozenset())
    uv = np.zeros((4, 2))
    uv[mapping.reshape(-1)] = mesh.vertices[mesh.faces.reshape(-1), :2]
    state = MeshState(mesh, frozenset(), uv, mapping)
    geometry = ReferenceSurface(mesh.vertices, mesh.faces, uv[mapping],
        np.full((4, 4, 4), [128, 90, 60, 255], dtype=np.uint8),
        source_has_normal=True, source_has_orm=True)
    reference = MaterialReference(geometry,
        np.full((4, 4, 4), [128, 128, 255, 255], dtype=np.uint8),
        np.full((4, 4, 4), [255, 200, 20, 255], dtype=np.uint8), *frame_arrays(state))
    samples = tuple(prepare_material_samples(state, reference, samples_per_face=count, phase=phase)
                    for count, phase in ((2, .13), (3, .37), (4, .73)))
    return BudgetInputs(mesh, BudgetState(state), {}, reference, samples[0], samples[1], samples[2],
                        (), {}, "synthetic", 0)


def test_complete_independent_target_passes_and_keeps_original_input(tmp_path: Path) -> None:
    data = inputs()
    before = fingerprint(data.initial)
    report = solve_budget_target(data, "independent", 17, 32, tmp_path / "target", seconds=20,
                                options=SolverOptions(iterations=2, candidate_limit=1, render_size=16))
    assert report["valid"], report
    assert all(_mapping(report[key])["valid"] for key in ("geometry", "material", "render"))
    assert _mapping(report["material"])["raster_checked"] and _mapping(report["material"])["raster_valid"]
    assert fingerprint(data.initial) == before
    assert report["full_rollback_verified"] and report["geometry_unchanged"]
    assert report["original_face_count"] == 2
    assert (tmp_path / "target/textures/Normal_mip5.png").exists()
    assert report["heldout_used_for_candidate_feedback"] is False
    assert report["mesh_attribute_bytes"] == 2 * 3 * 48 + 2 * 12


def test_failed_discrete_moves_cannot_change_accepted_or_base_state(tmp_path: Path) -> None:
    data = inputs()
    before = fingerprint(data.initial)
    report = solve_budget_target(data, "joint", 17, 32, tmp_path / "target", seconds=20,
                                options=SolverOptions(iterations=1, candidate_limit=6, render_size=16))
    assert report["valid"], report
    assert fingerprint(data.initial) == before
    assert report["full_rollback_verified"]
    assert any(r.get("reason") == "NO_INITIALIZED_SHARED_REGION" for r in _rows(report["candidates"]))
    assert report["final_groups"] == 0


def test_refuses_to_overwrite_target(tmp_path: Path) -> None:
    folder = tmp_path / "existing"
    folder.mkdir()
    with pytest.raises(FileExistsError):
        solve_budget_target(inputs(), "independent", 17, 32, folder, seconds=10,
                            options=SolverOptions())


def test_common_caches_are_disjoint_fixed_world_probes() -> None:
    data = inputs()
    for first, second in ((data.training, data.development), (data.training, data.heldout)):
        a = {tuple(row) for row in np.round(first.barycentric, 12)}
        b = {tuple(row) for row in np.round(second.barycentric, 12)}
        assert not a.intersection(b)
    assert time.monotonic() > 0


def test_geometry_rejection_is_not_mislabeled_initialization_failure(
    tmp_path: Path, monkeypatch: MonkeyPatch,
) -> None:
    def reject(*args: object, **kwargs: object) -> dict[str, object]:
        return {"valid": False, "errors": ["ANISOTROPY_LIMIT"]}

    monkeypatch.setattr("reseam.budget_solver._geometry", reject)
    report = solve_budget_target(inputs(), "independent", 17, 32, tmp_path / "reject", seconds=10,
                                options=SolverOptions(candidate_limit=1))
    assert report["status"] == "GEOMETRY_REJECTED"
    assert not report["valid"]


def test_feasibility_proxy_uses_area_weighted_positive_constraint_hinges() -> None:
    data = inputs()
    rows = []
    limits = MaterialThresholds()
    for face in range(2):
        metrics: dict[str, object] = {"face": face}
        for channel in ("color", "normal", "orm", "render"):
            suffix = "_degrees" if channel == "normal" else ""
            metrics[channel] = {aggregate: getattr(limits, f"{channel}_{aggregate}{suffix}")
                                for aggregate in ("mean", "p95", "max")}
        rows.append(metrics)
    rows[0]["color"]["mean"] = 2 * limits.color_mean
    report = {"faces": rows, "source_reference_valid": True,
              "bake_reference_valid": True, "support_state_matches": True}
    assert _development_feasibility(report, data.mesh, limits) == pytest.approx(.5)


def test_nondefault_projection_fraction_reaches_every_material_gate(
    tmp_path: Path, monkeypatch: MonkeyPatch,
) -> None:
    import reseam.budget_solver as module

    original = module.evaluate_material
    observed: list[float] = []

    def check(
        state: MeshState, reference: MaterialReference, atlas: MaterialAtlas, *,
        prepared: PreparedMaterialSamples, mip_levels: tuple[int, ...], thresholds: MaterialThresholds,
        max_projection_fraction: float, raster_checks: bool = False, deadline: float | None = None,
        important_faces: tuple[int, ...] = (),
    ) -> dict[str, object]:
        observed.append(max_projection_fraction)
        return original(state, reference, atlas, prepared=prepared, mip_levels=mip_levels,
                        thresholds=thresholds, max_projection_fraction=max_projection_fraction,
                        raster_checks=raster_checks, important_faces=important_faces, deadline=deadline)

    monkeypatch.setattr(module, "evaluate_material", check)
    monkeypatch.setattr(final_module, "evaluate_material", check)
    report = solve_budget_target(inputs(), "independent", 17, 32, tmp_path / "target", seconds=20,
        options=SolverOptions(iterations=1, candidate_limit=1, render_size=16, max_projection_fraction=.03))
    assert report["valid"], report
    assert len(observed) >= 3 and observed == [.03] * len(observed)
    assert _mapping(report["material"])["support_state_matches"] is True


def patch_two_legal_candidates(monkeypatch: MonkeyPatch) -> None:
    def proposal(
        inputs: BudgetInputs, base: BudgetState, method: str, options: SolverOptions,
        deadline: float, index: int = 0,
    ) -> tuple[BudgetState, dict[str, object]]:
        return base.copy(), {}

    def optimize(
        inputs: BudgetInputs, candidate: BudgetState, resolution: int, options: SolverOptions,
        deadline: float, method: str, folder: Path, emphasis: set[int] | None = None,
    ) -> tuple[BudgetState, MaterialAtlas, dict[str, object], dict[str, object]]:
        optimized = candidate.copy()
        index = int(folder.name.rsplit("_", 1)[1])
        optimized.version = optimized.surface.version = index + 1
        optimized.texture = np.full((resolution, resolution, 10), .2 + index * .01)
        atlas = bake_material(optimized.surface, inputs.reference, resolution,
                              padding_pixels=options.padding_pixels,
                              max_projection_fraction=options.max_projection_fraction)
        return optimized, atlas, {"valid": True, "quality_violation": 0.0}, {}

    monkeypatch.setattr("reseam.budget_solver._proposal_reuse", proposal)
    monkeypatch.setattr("reseam.budget_solver._optimize_candidate", optimize)


def test_second_development_candidate_can_pass_when_first_final_rejects(
    tmp_path: Path, monkeypatch: MonkeyPatch,
) -> None:
    patch_two_legal_candidates(monkeypatch)
    original = final_module.evaluate_material
    checked: list[int] = []

    def reject_first(
        state: MeshState, reference: MaterialReference, atlas: MaterialAtlas, *,
        prepared: PreparedMaterialSamples, mip_levels: tuple[int, ...], thresholds: MaterialThresholds,
        max_projection_fraction: float, raster_checks: bool, deadline: float,
        important_faces: tuple[int, ...] = (),
    ) -> dict[str, object]:
        checked.append(state.version)
        report = original(state, reference, atlas, prepared=prepared, mip_levels=mip_levels,
                          thresholds=thresholds, max_projection_fraction=max_projection_fraction,
                          raster_checks=raster_checks, important_faces=important_faces, deadline=deadline)
        if state.version in (0, 1):
            report.update(valid=False, raster_valid=False, status="CONTROLLED_FINAL_REJECTION")
        return report

    monkeypatch.setattr(final_module, "evaluate_material", reject_first)
    report = solve_budget_target(inputs(), "joint", 17, 32, tmp_path / "target", seconds=20,
        options=SolverOptions(candidate_limit=2, work_archive_size=1, iterations=1, render_size=16))
    assert report["valid"] and report["accepted_candidate_id"] == 1, report
    assert checked == [1, 1, 2]
    assert report["final_validation_order"] == [0, 1]
    assert [row["status"] for row in _rows(report["final_validation_log"])] == ["QUALITY_REJECTED", "VALIDATED"]
    assert load_state(tmp_path / "target/state.json").version == 2
    accepted = json.loads((tmp_path / "target/accepted_archive.json").read_text())
    assert accepted["candidate_id"] == 1
    assert accepted["state_fingerprint"] == report["accepted_candidate_fingerprint"]
    assert (tmp_path / "target/final_validation/candidate_000/render").exists()


def test_later_timeout_is_checked_and_retains_first_accepted_search_candidate(
    tmp_path: Path, monkeypatch: MonkeyPatch,
) -> None:
    patch_two_legal_candidates(monkeypatch)
    original = final_module.evaluate_material
    checked: list[int] = []

    def only_first(
        state: MeshState, reference: MaterialReference, atlas: MaterialAtlas, *,
        prepared: PreparedMaterialSamples, mip_levels: tuple[int, ...], thresholds: MaterialThresholds,
        max_projection_fraction: float, raster_checks: bool, deadline: float,
        important_faces: tuple[int, ...] = (),
    ) -> dict[str, object]:
        checked.append(state.version)
        if state.version == 2:
            raise TimeoutError("later candidate cannot erase accepted result")
        report = original(state, reference, atlas, prepared=prepared, mip_levels=mip_levels,
                        thresholds=thresholds, max_projection_fraction=max_projection_fraction,
                        raster_checks=raster_checks, important_faces=important_faces, deadline=deadline)
        if len(checked) == 1:
            report.update(valid=False, status="CONTROLLED_BASELINE_REJECTION")
        return report

    monkeypatch.setattr(final_module, "evaluate_material", only_first)
    report = solve_budget_target(inputs(), "joint", 17, 32, tmp_path / "target", seconds=20,
        options=SolverOptions(candidate_limit=2, iterations=1, render_size=16))
    assert report["valid"] and report["accepted_candidate_id"] == 0
    assert checked == [1, 1, 2]
    assert _rows(report["final_validation_log"])[1]["status"] == "BUDGET_EXHAUSTED"
    assert load_state(tmp_path / "target/state.json").version == 1


def test_final_check_finishing_after_deadline_cannot_enter_archive(
    tmp_path: Path, monkeypatch: MonkeyPatch,
) -> None:
    import reseam.budget_solver as module

    patch_two_legal_candidates(monkeypatch)
    original = final_module.evaluate_material
    now = [time.monotonic()]

    def clock() -> float:
        return now[0]

    def late(
        state: MeshState, reference: MaterialReference, atlas: MaterialAtlas, *,
        prepared: PreparedMaterialSamples, mip_levels: tuple[int, ...], thresholds: MaterialThresholds,
        max_projection_fraction: float, raster_checks: bool, deadline: float,
        important_faces: tuple[int, ...] = (),
    ) -> dict[str, object]:
        report = original(state, reference, atlas, prepared=prepared, mip_levels=mip_levels,
                          thresholds=thresholds, max_projection_fraction=max_projection_fraction,
                          raster_checks=raster_checks, important_faces=important_faces, deadline=deadline)
        now[0] = deadline + 1
        return report

    monkeypatch.setattr(module, "time", SimpleNamespace(monotonic=clock))
    monkeypatch.setattr(final_module, "time", SimpleNamespace(monotonic=clock))
    monkeypatch.setattr(final_module, "evaluate_material", late)
    report = solve_budget_target(inputs(), "joint", 17, 32, tmp_path / "target", seconds=20,
        options=SolverOptions(candidate_limit=2, iterations=1, render_size=16))
    assert not report["valid"] and report["status"] == "BUDGET_EXHAUSTED"
    assert report["accepted_candidate_id"] is None
    assert not (tmp_path / "target/accepted_archive.json").exists()
    assert _rows(_mapping(report["baseline_validation"])["log"])[0]["status"] == "BUDGET_EXHAUSTED"
    assert report["final_validation_order"] == []
    assert _rows(report["candidates"])[0]["action"] == "NOT_STARTED"
    assert report["material"] and (tmp_path / "target/state.json").exists()


def test_all_final_rejections_keep_best_development_metrics_and_images(
    tmp_path: Path, monkeypatch: MonkeyPatch,
) -> None:
    patch_two_legal_candidates(monkeypatch)
    original = final_module.evaluate_material

    def reject(
        state: MeshState, reference: MaterialReference, atlas: MaterialAtlas, *,
        prepared: PreparedMaterialSamples, mip_levels: tuple[int, ...], thresholds: MaterialThresholds,
        max_projection_fraction: float, raster_checks: bool, deadline: float,
        important_faces: tuple[int, ...] = (),
    ) -> dict[str, object]:
        report = original(state, reference, atlas, prepared=prepared, mip_levels=mip_levels,
                          thresholds=thresholds, max_projection_fraction=max_projection_fraction,
                          raster_checks=raster_checks, important_faces=important_faces, deadline=deadline)
        report.update(valid=False, status="CONTROLLED_FINAL_REJECTION")
        return report

    monkeypatch.setattr(final_module, "evaluate_material", reject)
    report = solve_budget_target(inputs(), "joint", 17, 32, tmp_path / "target", seconds=20,
        options=SolverOptions(candidate_limit=2, iterations=1, render_size=16))
    assert not report["valid"] and report["accepted_candidate_id"] is None
    assert report["status"] == "QUALITY_REJECTED"
    color = _mapping(_mapping(_mapping(report["material"])["channels"])["color"])
    mean = color["mean"]
    assert isinstance(mean, (int, float)) and mean >= 0
    assert _mapping(report["render"])["per_view_light_mip"]
    assert load_state(tmp_path / "target/state.json").version == 1
    assert list((tmp_path / "target/render").glob("*.png"))


def test_presearch_valid_baseline_survives_better_development_but_final_bad_search(
    tmp_path: Path, monkeypatch: MonkeyPatch,
) -> None:
    patch_two_legal_candidates(monkeypatch)
    original = final_module.evaluate_material
    checked: list[int] = []

    def final_bad(
        state: MeshState, reference: MaterialReference, atlas: MaterialAtlas, *,
        prepared: PreparedMaterialSamples, mip_levels: tuple[int, ...], thresholds: MaterialThresholds,
        max_projection_fraction: float, raster_checks: bool, deadline: float,
        important_faces: tuple[int, ...] = (),
    ) -> dict[str, object]:
        checked.append(state.version)
        report = original(state, reference, atlas, prepared=prepared, mip_levels=mip_levels,
                          thresholds=thresholds, max_projection_fraction=max_projection_fraction,
                          raster_checks=raster_checks, important_faces=important_faces, deadline=deadline)
        if len(checked) > 1:
            report.update(valid=False, raster_valid=False, status="CONTROLLED_FINAL_REJECTION")
        return report

    monkeypatch.setattr(final_module, "evaluate_material", final_bad)
    report = solve_budget_target(inputs(), "joint", 17, 32, tmp_path / "target", seconds=20,
        options=SolverOptions(candidate_limit=2, iterations=1, render_size=16))
    assert checked == [1, 1, 2]
    assert report["valid"] and report["accepted_candidate_id"] == -1
    assert report["delivery_fallback_used"] is True and report["delivered_entries"] == 1
    assert _mapping(report["baseline_validation"])["before_search"] is True
    assert _mapping(report["baseline_validation"])["status"] == "VALIDATED"
    assert all(row["status"] == "QUALITY_REJECTED" for row in _rows(report["final_validation_log"]))
    assert load_state(tmp_path / "target/state.json").version == 1
    accepted = json.loads((tmp_path / "target/accepted_archive.json").read_text(encoding="utf-8"))
    assert accepted["candidate_id"] == -1
    assert Path(accepted["provenance"]["output_directory"]).name == "candidate_-01"


def test_default_solver_runs_actual_eq19_restoration_with_finite_slack(tmp_path: Path) -> None:
    import reseam.budget_solver as module

    with patch.object(module, "restore_budget_feasibility", wraps=module.restore_budget_feasibility) as spy:
        report = solve_budget_target(inputs(), "independent", 17, 32, tmp_path / "target", seconds=20,
            options=SolverOptions(candidate_limit=1, iterations=1, render_size=16))
    assert spy.call_count == 1
    assert report["valid"]
    generated = json.loads((tmp_path / "target/candidate_000/optimization.json").read_text(encoding="utf-8"))
    assert generated["objective"] == "Eq19_max_normalized_constraint_plus_fixed_origin_trust"
    assert np.isfinite(generated["xi"]) and generated["xi"] >= 0
    assert generated["metadata"]["scope"] == "EQ19_SEQUENTIAL_JOINT_FROZEN_PROXY_RESTORATION"
    assert generated["metadata"]["proxy_evaluated"] is True
    assert generated["metadata"]["full_final_validation"] is False


def test_bake_only_and_texture_only_keep_same_uv_and_record_real_restoration(tmp_path: Path) -> None:
    data = inputs()
    # A structured source creates actual reconstruction error at coarse target
    # mips. The optimizer must alter material bytes while all UV copies stay put.
    data.reference.geometry.image[:, :, :3] = np.array([0, 255, 0, 255], dtype=np.uint8)[None, :, None]
    data.training = prepare_material_samples(data.initial.surface, data.reference, samples_per_face=2, phase=.13)
    data.development = prepare_material_samples(data.initial.surface, data.reference, samples_per_face=3, phase=.37)
    data.heldout = prepare_material_samples(data.initial.surface, data.reference, samples_per_face=4, phase=.73)
    options = SolverOptions(candidate_limit=1, iterations=10, render_size=16)
    baked = solve_budget_target(data, "bake_only", 17, 16, tmp_path / "baked", seconds=20, options=options)
    restored = solve_budget_target(data, "texture_only", 17, 16, tmp_path / "restored", seconds=20, options=options)
    baked_uv = load_state(tmp_path / "baked/state.json")
    optimized_uv = load_state(tmp_path / "restored/candidate_000/state.json")
    assert np.array_equal(baked_uv.uv, optimized_uv.uv)
    assert np.array_equal(baked_uv.uv_faces, optimized_uv.uv_faces)
    assert baked["bake_texture_ablation"] == "bake_only" and baked["candidate_count"] == 0
    assert restored["bake_texture_ablation"] == "fixed_U_texture_restoration"
    generated = json.loads((tmp_path / "restored/candidate_000/optimization.json").read_text(encoding="utf-8"))
    assert generated["metadata"]["scope"] == "EQ19_SEQUENTIAL_JOINT_FROZEN_PROXY_RESTORATION"
    assert generated["metadata"]["proxy_evaluated"] is True
    assert np.isfinite(generated["xi"])
    before = (tmp_path / "restored/baseline_validation/candidate_-01/textures/BaseColor_mip0.png").read_bytes()
    after = (tmp_path / "restored/final_validation/candidate_000/textures/BaseColor_mip0.png").read_bytes()
    assert before != after


def test_two_iteration_restoration_final_proxy_texture_gets_actual_development_evaluation(
    tmp_path: Path, monkeypatch: MonkeyPatch,
) -> None:
    import reseam.budget_solver as module

    data = inputs()
    data.reference.geometry.image[:, :, :3] = np.array([0, 255, 0, 255], dtype=np.uint8)[None, :, None]
    data.training = prepare_material_samples(data.initial.surface, data.reference, samples_per_face=2, phase=.13)
    data.development = prepare_material_samples(data.initial.surface, data.reference, samples_per_face=3, phase=.37)
    options = SolverOptions(iterations=2)
    candidate = module.pack_budget_state(data.initial, 16, gap_pixels=options.gap_pixels,
                                         border_pixels=options.border_pixels)
    initial_atlas = bake_material(candidate.surface, data.reference, 16)
    original_restore: Callable[..., BudgetRestorationResult] = module.restore_budget_feasibility
    original_material: Callable[..., dict[str, object]] = module.evaluate_material
    restored: list[BudgetRestorationResult] = []
    inspected: list[tuple[bool, str]] = []

    def restore(
        initial: MeshState, samples: FeatureSamples, initial_texture: FloatArray, **kwargs: object,
    ) -> BudgetRestorationResult:
        result = original_restore(initial, samples, initial_texture, **kwargs)
        restored.append(result)
        return result

    def material(
        state: MeshState, reference: MaterialReference, atlas: MaterialAtlas, **kwargs: object,
    ) -> dict[str, object]:
        inspected.append((bool(restored), final_module.encoded_fingerprint(atlas)))
        return original_material(state, reference, atlas, **kwargs)

    monkeypatch.setattr(module, "restore_budget_feasibility", restore)
    monkeypatch.setattr(module, "evaluate_material", material)
    folder = tmp_path / "optimization"
    folder.mkdir()
    _, _, _, history = module._optimize_candidate(data, candidate, 16, options,
        time.monotonic() + 20, "texture_only", folder)
    assert len(restored) == 1
    result = restored[0]
    assert result.final_proxy < result.initial_proxy
    assert not np.array_equal(result.texture, module._atlas_features(initial_atlas))
    expected = final_module.encoded_fingerprint(module._encode_texture(result.texture, initial_atlas))
    assert (True, expected) in inspected
    assert history["final_proxy_state_development_evaluated"] is True
    assert np.array_equal(result.state.uv, candidate.surface.uv)
    assert result.best_quality_texture is not None
    assert not np.array_equal(result.best_quality_texture, result.texture)


@pytest.mark.parametrize("limit", [0, True, 1.5])
def test_final_validation_candidate_limit_is_a_positive_integer(limit: object) -> None:
    with pytest.raises(ValueError, match="integer solver counts"):
        SolverOptions(final_validation_candidates=limit)  # type: ignore[arg-type]
