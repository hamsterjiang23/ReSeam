"""Budget feasibility search with complete fixed-mesh candidates and rollback.

The final certificate is deliberately limited to the frozen CPU RGBA8 contract.
No status below asserts continuous-domain, GPU, artist, or global optimality.
"""

import copy
import math
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np

from .budget_baselines import prepare_baseline, solve_roi_baseline
from .budget_correspondence import update_uv_correspondences
from .budget_experiment import BudgetExperimentConfig, select_wing_pair
from .budget_final import (
    FinalCandidate,
    FinalValidationResult,
    make_delivered_archive,
    promote_final_candidate,
    validate_final_candidates,
)
from .budget_material import (
    MaterialAtlas,
    MaterialReference,
    MaterialThresholds,
    PreparedMaterialSamples,
    atlas_from_arrays,
    bake_material,
    evaluate_material,
    frame_arrays,
    interpolated_frames,
    load_material_reference,
    prepare_material_samples,
    refresh_material_support,
)
from .budget_optimize import FeatureSamples
from .budget_restoration import RestorationOptions, restore_budget_feasibility
from .budget_state import (
    BudgetState,
    contact_motion_ties,
    embed_region,
    exclusive_faces,
    fingerprint,
    owners_and_members,
    pack_budget_state,
    planar_merge,
    repair_independent_initialization,
    validate_budget_geometry,
    validate_budget_safety,
)
from .budget_texture import decode_rgba8, texture_payload_bytes
from .joint_regions import RegionPair
from .joint_types import ReuseGroup
from .mesh import load_mesh, save_state
from .pipeline import write_json
from .surface_match import project_surface, reflect
from .types import FloatArray, Mesh, MeshState
from .validation import seam_metrics


@dataclass(frozen=True)
class SolverOptions:
    iterations: int = 30
    geometry_iterations: int = 120
    candidate_limit: int = 6
    work_archive_size: int = 3
    final_validation_candidates: int = 3
    maximum_region_faces: int = 32
    maximum_anisotropy: float = 2
    mean_shape_limit: float = 0.025
    tail_shape_limit: float = 0.1
    maximum_density_ratio: float = 2
    correspondence_fraction: float = 0.005
    normal_angle_degrees: float = 45
    gap_pixels: float = 8
    border_pixels: float = 4
    padding_pixels: int = 4
    max_projection_fraction: float = 0.02
    training_samples: int = 16
    development_samples: int = 12
    final_samples: int = 24
    mip_levels: tuple[int, ...] = (0, 1)
    uv_step_pixels: float = 0.35
    restoration_local_iterations: int = 300
    restoration_trust_weight: float = 1e-4
    texture_trust_radius: float = 0.1
    shape_weight: float = 0.01
    coupling_weight: float = 10
    render_size: int = 48
    include_optcuts_reference: bool = False
    material_thresholds: dict[str, float] = field(default_factory=dict)

    def __post_init__(self) -> None:
        counts = (self.iterations, self.geometry_iterations, self.candidate_limit,
                  self.work_archive_size, self.final_validation_candidates, self.restoration_local_iterations,
                  self.maximum_region_faces, self.training_samples,
                  self.development_samples, self.final_samples, self.padding_pixels, self.render_size)
        if any(type(v) is not int or v < 1 for v in counts):
            raise ValueError("Positive integer solver counts required")
        scalars = (self.maximum_anisotropy, self.mean_shape_limit, self.tail_shape_limit, self.maximum_density_ratio,
                   self.correspondence_fraction, self.normal_angle_degrees, self.gap_pixels,
                   self.border_pixels, self.max_projection_fraction, self.uv_step_pixels,
                    self.shape_weight, self.coupling_weight, self.restoration_trust_weight,
                    self.texture_trust_radius)
        if not all(math.isfinite(v) and v > 0 for v in scalars):
            raise ValueError("Positive finite solver coefficients required")
        if (self.maximum_anisotropy < 1 or self.maximum_density_ratio < 1
                or self.normal_angle_degrees > 180 or not self.mip_levels
                or min(self.mip_levels) < 0):
            raise ValueError("Invalid solver geometry or mip contract")
        MaterialThresholds(**self.material_thresholds)
        if type(self.include_optcuts_reference) is not bool:
            raise ValueError("include_optcuts_reference must be boolean")


@dataclass
class BudgetInputs:
    mesh: Mesh
    initial: BudgetState
    initialization: dict[str, object]
    reference: MaterialReference
    training: PreparedMaterialSamples
    development: PreparedMaterialSamples
    heldout: PreparedMaterialSamples
    pairs: tuple[RegionPair, ...]
    region_diagnostics: dict[str, object]
    model: str
    setup_seconds: float
    optcuts_initial: BudgetState | None = None


def prepare_inputs(root: Path, model: str, options: SolverOptions, deadline: float) -> BudgetInputs:
    started = time.monotonic()
    if model not in ("energy_blade", "dragon") or not math.isfinite(deadline):
        raise ValueError("Fixed asset and finite setup deadline required")
    mesh = load_mesh(root / "assets/v1" / model / "low.obj")
    raw = prepare_baseline(mesh, "independent", 17, deadline)
    initial, repaired = repair_independent_initialization(raw, maximum_anisotropy=options.maximum_anisotropy)
    optcuts_initial = None
    if options.include_optcuts_reference:
        from .budget_optcuts import prepare_optcuts_reference

        try:
            optcuts_initial, metadata = prepare_optcuts_reference(
                root, root / "assets/v1" / model / "low.obj", mesh, 17, deadline,
                maximum_anisotropy=options.maximum_anisotropy,
            )
            repaired["optcuts_reference"] = metadata
        except (OSError, ValueError, RuntimeError, TimeoutError) as error:
            repaired["optcuts_reference"] = {"status": "INITIALIZATION_FAILED", "reason": str(error)}
    reference = load_material_reference(root / "assets/v1" / model / "high.glb")
    caches = tuple(prepare_material_samples(
        initial.surface, reference, samples_per_face=count, phase=phase,
        max_projection_fraction=options.max_projection_fraction, deadline=deadline,
    ) for count, phase in ((options.training_samples, .13),
                           (options.development_samples, .37), (options.final_samples, .73)))
    pairs: tuple[RegionPair, ...] = ()
    diagnostics: dict[str, object] = {}
    try:
        remaining = max(10.1, deadline - time.monotonic())
        config = BudgetExperimentConfig(
            "complete_solver_common_roi", model=model, seconds=remaining,
            solver_seconds=min(5, remaining / 2), maximum_region_faces=options.maximum_region_faces,
            maximum_roi_attempts=15,
        )
        pair, diagnostics = select_wing_pair(mesh, config, deadline)
        pairs = (pair,)
    except (ValueError, TimeoutError, np.linalg.LinAlgError) as error:
        diagnostics = {"status": "NO_INITIALIZED_SHARED_REGION", "reason": str(error)}
    if time.monotonic() >= deadline:
        raise TimeoutError("COMMON_INPUT_PREPARATION_BUDGET_EXHAUSTED")
    return BudgetInputs(mesh, initial, repaired, reference, caches[0], caches[1], caches[2],
                        pairs, diagnostics, model, time.monotonic() - started, optcuts_initial)


def _geometry(state: BudgetState, resolution: int, options: SolverOptions) -> dict[str, object]:
    return validate_budget_geometry(
        state, resolution, gap_pixels=options.gap_pixels, border_pixels=options.border_pixels,
        maximum_anisotropy=options.maximum_anisotropy, mean_shape_limit=options.mean_shape_limit,
        tail_shape_limit=options.tail_shape_limit,
        maximum_density_ratio=options.maximum_density_ratio,
        correspondence_fraction=options.correspondence_fraction,
        normal_angle_degrees=options.normal_angle_degrees,
    )


def _safety(state: BudgetState, resolution: int, options: SolverOptions) -> dict[str, object]:
    return validate_budget_safety(
        state, resolution, gap_pixels=options.gap_pixels, border_pixels=options.border_pixels,
        correspondence_fraction=options.correspondence_fraction,
        normal_angle_degrees=options.normal_angle_degrees,
    )


def _atlas_features(atlas: MaterialAtlas) -> FloatArray:
    return np.concatenate((decode_rgba8(atlas.base_color[0]),
                           atlas.normal[0][..., :3].astype(float) / 127.5 - 1,
                           atlas.orm[0][..., :3].astype(float) / 255), axis=2)


def _encode_texture(texture: FloatArray, original: MaterialAtlas) -> MaterialAtlas:
    return atlas_from_arrays(texture[..., :4], texture[..., 4:7], texture[..., 7:10],
                             original.coverage, original.metadata)


def _features(state: MeshState, cache: PreparedMaterialSamples, options: SolverOptions,
              face_emphasis: set[int] | None = None) -> FeatureSamples:
    frames, valid = interpolated_frames(*frame_arrays(state), cache.face_ids, cache.barycentric)
    if not valid.all() or not cache.reference.valid.all():
        raise ValueError("TRAINING_REFERENCE_OR_FRAME_INVALID")
    normal = np.einsum("nji,nj->ni", frames, cache.reference.normal_world)
    values = np.column_stack((cache.reference.base_color, normal, cache.reference.orm))
    weight = cache.weights.copy()
    if face_emphasis:
        weight[np.isin(cache.face_ids, sorted(face_emphasis))] *= 4
    count = len(cache.face_ids)
    levels = tuple(options.mip_levels)
    return FeatureSamples(
        np.tile(cache.face_ids, len(levels)), np.tile(cache.barycentric, (len(levels), 1)),
        np.tile(values, (len(levels), 1)), np.tile(weight, len(levels)),
        np.repeat(np.asarray(levels, dtype=np.int64), count),
        np.asarray([1., 1, 1, .1, .25, .25, .25, .25, .25, .25]),
    )


def _proposal_reuse(inputs: BudgetInputs, base: BudgetState, method: str, options: SolverOptions,
                    deadline: float, index: int = 0) -> tuple[BudgetState, dict[str, object]]:
    if not inputs.pairs:
        raise ValueError("NO_INITIALIZED_SHARED_REGION")
    pair = inputs.pairs[index % len(inputs.pairs)]
    proposal_method = "old_pair" if method == "old_pair" else "geometry_only"
    solved = solve_roi_baseline(inputs.mesh, pair, proposal_method, deadline,
                                options.geometry_iterations, options.maximum_anisotropy,
                                options.coupling_weight)
    first = int(solved.orientations[0])
    second = int(solved.orientations[len(pair.first_faces)])
    group = ReuseGroup(f"reuse_{base.version}_{index}", pair.first_faces, pair.second_faces,
                       pair.normal, pair.offset, first, second)
    candidate = embed_region(base, solved.state, solved.original_face_ids, group, solved.constraints)
    candidate.correspondence_records = _association_records(candidate)
    return candidate, solved.metadata


def _association_records(state: BudgetState) -> tuple[dict[str, object], ...]:
    mesh = state.surface.mesh
    triangles = mesh.vertices[mesh.faces]
    diagonal = float(np.linalg.norm(np.ptp(mesh.vertices, axis=0)))
    normals = np.cross(triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0])
    normals /= np.linalg.norm(normals, axis=1)[:, None]
    records: list[dict[str, object]] = []
    for c in state.correspondences:
        group = next(g for g in state.groups
                     if c.source_face in g.first_faces + g.second_faces
                     and c.target_face in g.first_faces + g.second_faces)
        normal = np.asarray(group.plane_normal)
        source = np.asarray(c.source_barycentric) @ triangles[c.source_face]
        target = np.asarray(c.target_barycentric) @ triangles[c.target_face]
        source_member = group.first_faces if c.source_face in group.first_faces else group.second_faces
        returned, _, _ = project_surface(
            reflect(target[None], normal, group.plane_offset), triangles[list(source_member)])
        angle = float(np.degrees(np.arccos(np.clip(
            reflect(normals[c.source_face], normal, 0) @ normals[c.target_face], -1, 1))))
        corner = int(np.argmax(c.source_barycentric))
        source_vertex = int(mesh.faces[c.source_face, corner]) if c.source_barycentric[corner] > 1 - 1e-10 else None
        records.append({
            "group": group.group_id, "source_original_face_id": c.source_face,
            "source_original_vertex_id": source_vertex,
            "source_face_vertex_ids": mesh.faces[c.source_face].tolist(),
            "target_original_face_id": c.target_face,
            "source_barycentric": c.source_barycentric, "target_barycentric": c.target_barycentric,
            "position_error": float(np.linalg.norm(reflect(source, normal, group.plane_offset) - target) / diagonal),
            "cycle_error": float(np.linalg.norm(returned[0] - source) / diagonal),
            "normal_angle_degrees": angle, "normalization": "complete_original_bbox_diagonal",
            "normalization_scale": diagonal, "weight": c.weight,
            "association": "fixed_geometric_surface_projection", "global_bijection_proven": False,
        })
    return tuple(records)


def _coupling_objective(state: BudgetState, weight: float) -> tuple[FloatArray, FloatArray]:
    # Sparse associations stay attached to original face/corner IDs. Empty is legal.
    matrix = np.zeros((len(state.correspondences), len(state.surface.uv)))
    weights = np.zeros(len(matrix))
    for i, c in enumerate(state.correspondences):
        np.add.at(matrix[i], state.surface.uv_faces[c.source_face], c.source_barycentric)
        np.add.at(matrix[i], state.surface.uv_faces[c.target_face], -np.asarray(c.target_barycentric))
        weights[i] = c.weight
    if len(weights):
        weights *= weight / weights.sum()
    return matrix, weights


def _development_feasibility(
    report: dict[str, object], mesh: Mesh, limits: MaterialThresholds,
) -> float:
    """DEV-only work priority; not the Eq.(19) restoration objective or certificate."""
    rows = report.get("faces", [])
    if not isinstance(rows, list) or not rows:
        return _violation(report)
    score, total_weight = 0.0, 0.0
    for row in rows:
        if not isinstance(row, dict) or type(row.get("face")) is not int:
            raise ValueError("INVALID_DEVELOPMENT_FACE_METRIC")
        weight = float(mesh.areas[row["face"]])
        total_weight += weight
        for channel in ("color", "normal", "orm", "render"):
            metrics = row.get(channel)
            if not isinstance(metrics, dict):
                raise TypeError("MISSING_DEVELOPMENT_CHANNEL_METRIC")
            suffix = "_degrees" if channel == "normal" else ""
            for aggregate in ("mean", "p95", "max"):
                value = metrics.get(aggregate)
                if not isinstance(value, (int, float)):
                    raise TypeError("INVALID_DEVELOPMENT_AGGREGATE")
                bound = getattr(limits, f"{channel}_{aggregate}{suffix}")
                score += weight * max(0., value / bound - 1) ** 2
    missing = not (report.get("source_reference_valid") and report.get("bake_reference_valid")
                   and report.get("support_state_matches"))
    return score / max(total_weight, 1e-30) + float(missing)


def _owner_boxes(state: BudgetState) -> tuple[FloatArray, FloatArray]:
    owners, _ = owners_and_members(state)
    low, high = np.empty_like(state.surface.uv), np.empty_like(state.surface.uv)
    for owner in np.unique(owners):
        ids = np.unique(state.surface.uv_faces[owners == owner])
        low[ids] = state.surface.uv[ids].min(axis=0)
        high[ids] = state.surface.uv[ids].max(axis=0)
    return low, high


def _optimize_candidate(
    inputs: BudgetInputs, candidate: BudgetState, resolution: int, options: SolverOptions,
    deadline: float, method: str, folder: Path, emphasis: set[int] | None = None,
) -> tuple[BudgetState, MaterialAtlas, dict[str, object], dict[str, object]]:
    _, members = owners_and_members(candidate)
    atlas = bake_material(candidate.surface, inputs.reference, resolution, member_ids=members,
                          padding_pixels=options.padding_pixels,
                          max_projection_fraction=options.max_projection_fraction, deadline=deadline)
    texture = _atlas_features(atlas) if candidate.texture is None else candidate.texture.copy()
    limits = MaterialThresholds(**options.material_thresholds)
    lower, upper = np.zeros(10), np.ones(10)
    lower[4:7] = -1

    def legal(surface: MeshState) -> bool:
        if time.monotonic() >= deadline:
            return False
        temporary = candidate.copy()
        temporary.surface = surface
        return bool(_safety(temporary, resolution, options)["valid"])

    def targets(surface: MeshState) -> FeatureSamples:
        return _features(surface, inputs.training, options, emphasis)

    def quality(surface: MeshState, image: FloatArray) -> tuple[bool, float]:
        encoded = _encode_texture(image, atlas)
        if not np.array_equal(surface.uv, candidate.surface.uv):
            encoded = refresh_material_support(
                surface, inputs.reference, encoded, member_ids=members,
                padding_pixels=options.padding_pixels,
                max_projection_fraction=options.max_projection_fraction,
            )
        report = evaluate_material(surface, inputs.reference, encoded, prepared=inputs.development,
                                   mip_levels=options.mip_levels, thresholds=limits,
                                   max_projection_fraction=options.max_projection_fraction)
        return bool(report["valid"]), _development_feasibility(report, inputs.mesh, limits)

    result = restore_budget_feasibility(
        candidate.surface, targets(candidate.surface), texture,
        options=RestorationOptions(
            seconds=max(0.0, deadline - time.monotonic()), iterations=options.iterations,
            local_iterations=options.restoration_local_iterations,
            trust_weight=options.restoration_trust_weight,
            texture_trust_radius=options.texture_trust_radius,
            optimize_uv=method in ("joint", "independent", "optcuts"),
            uv_trust_pixels=options.uv_step_pixels, thresholds=limits,
            mean_shape_limit=options.mean_shape_limit, tail_shape_limit=options.tail_shape_limit,
            maximum_anisotropy=options.maximum_anisotropy,
            maximum_neighbor_density_ratio=options.maximum_density_ratio,
            quality_check_interval=10,
        ),
        deadline=deadline, target_update=targets, legal_uv=legal, quality_check=quality,
        channel_bounds=(lower, upper),
        uv_bounds=_owner_boxes(candidate),
        motion_ties=contact_motion_ties(candidate),
    )
    # Always evaluate the final proxy result: a periodic DEV archive may contain
    # only iteration zero in short runs. Never silently discard all new steps.
    surface, chosen = result.state, result.texture
    optimized = candidate.copy()
    optimized.surface, optimized.texture = surface.copy(), chosen.copy()
    optimized.evidence.clear()
    encoded = _encode_texture(chosen, atlas)
    encoded = refresh_material_support(
        optimized.surface, inputs.reference, encoded, member_ids=members,
        padding_pixels=options.padding_pixels,
        max_projection_fraction=options.max_projection_fraction,
    )
    # This post-block DEV gate can consume cleanup reserve, but no final archive
    # may be accepted after the enclosing target deadline.
    material = evaluate_material(optimized.surface, inputs.reference, encoded,
                                 prepared=inputs.development, mip_levels=options.mip_levels, thresholds=limits,
                                 max_projection_fraction=options.max_projection_fraction)
    dev_archive_checked = False
    if (result.best_quality_state is not None and result.best_quality_texture is not None
            and (not np.array_equal(result.best_quality_state.uv, surface.uv)
                 or not np.array_equal(result.best_quality_texture, chosen))):
        previous_atlas = refresh_material_support(
            result.best_quality_state, inputs.reference, _encode_texture(result.best_quality_texture, atlas),
            member_ids=members, padding_pixels=options.padding_pixels,
            max_projection_fraction=options.max_projection_fraction,
        )
        previous_material = evaluate_material(
            result.best_quality_state, inputs.reference, previous_atlas, prepared=inputs.development,
            mip_levels=options.mip_levels, thresholds=limits,
            max_projection_fraction=options.max_projection_fraction,
        )
        dev_archive_checked = True
        if ((not bool(previous_material["valid"]), _violation(previous_material))
                < (not bool(material["valid"]), _violation(material))):
            optimized.surface = result.best_quality_state.copy()
            optimized.texture = result.best_quality_texture.copy()
            encoded, material = previous_atlas, previous_material
    material["development_feasibility_score"] = _development_feasibility(material, inputs.mesh, limits)
    history: dict[str, object] = {"status": result.status, "initial_proxy": result.initial_proxy,
                                "final_proxy": result.final_proxy, "seconds": result.wall_seconds,
                                "quality_archive_valid_on_development": result.quality_valid,
                                "history": result.history, "reason": result.reason,
                                "xi": result.xi, "metadata": result.metadata,
                                "objective": "Eq19_max_normalized_constraint_plus_fixed_origin_trust",
                                "final_proxy_state_development_evaluated": True,
                                "distinct_periodic_development_archive_compared": dev_archive_checked,
                                "local_model_certificate_is_not_final_quality": True}
    write_json(folder / "optimization.json", history)
    return optimized, encoded, material, history


def _compact_material(report: dict[str, object]) -> dict[str, object]:
    return {key: value for key, value in report.items() if key not in ("faces", "raster_faces")}


def _violation(report: dict[str, object]) -> float:
    value = report.get("quality_violation")
    if not isinstance(value, (float, int)) or not math.isfinite(value):
        raise ValueError("INVALID_MATERIAL_VIOLATION")
    return float(value)


def _geometry_rejection(geometry: dict[str, object]) -> str:
    errors = geometry.get("errors", [])
    text = str(errors)
    structural = ("FLIPPED", "DEGENERATE", "INTERSECTION", "UNAUTHORIZED", "INVALID_UV",
                  "CONNECTIVITY", "NONMANIFOLD", "NON_PLANAR", "UNREALIZED", "NOT_ISOLATED")
    return "STRUCTURALLY_INVALID" if any(token in text for token in structural) else "GEOMETRY_REJECTED"


def _failed_target_status(records: list[dict[str, object]]) -> str:
    for status in ("BUDGET_EXHAUSTED", "STRUCTURALLY_INVALID", "GEOMETRY_REJECTED",
                   "NUMERICAL_FAILURE", "INITIALIZATION_FAILED"):
        if any(record.get("status") == status for record in records):
            return status
    return "INITIALIZATION_FAILED"


def solve_budget_target(
    inputs: BudgetInputs, method: str, seed: int, resolution: int, output: Path, *,
    seconds: float, options: SolverOptions,
) -> dict[str, object]:
    """Solve one independently initialized finite budget; preserve every failure."""
    if method not in ("independent", "old_pair", "geometry_only", "joint", "optcuts",
                      "bake_only", "texture_only"):
        raise ValueError("UNKNOWN_BUDGET_METHOD")
    if not math.isfinite(seconds) or seconds < 5 or resolution < 16:
        raise ValueError("Target budget must allow bounded cleanup")
    output.mkdir(parents=True, exist_ok=False)
    started = time.monotonic()
    deadline = started + seconds
    if method == "optcuts" and inputs.optcuts_initial is None:
        raise ValueError("OPTCUTS_REFERENCE_NOT_PREPARED")
    base = inputs.optcuts_initial.copy() if method == "optcuts" and inputs.optcuts_initial else inputs.initial.copy()
    frozen_base = fingerprint(base)
    rng = np.random.default_rng(seed)
    records: list[dict[str, object]] = []
    archive: list[tuple[float, BudgetState, MaterialAtlas, dict[str, object]]] = []
    shared_work: BudgetState | None = None
    last_material: dict[str, object] = {}
    final_pool: list[FinalCandidate] = []
    status = "QUALITY_REJECTED"
    final_geometry: dict[str, object] = {}
    final_material: dict[str, object] = {}
    render: dict[str, object] = {"valid": False, "status": "NOT_RUN"}
    reason: str | None = None
    limit = options.candidate_limit if method == "joint" else (0 if method == "bake_only" else 1)
    cleanup = seconds * .45
    delivered = make_delivered_archive(options, deadline)
    baseline_result: FinalValidationResult | None = None

    def check_final_geometry(state: BudgetState) -> dict[str, object]:
        if (not np.array_equal(state.surface.mesh.faces, inputs.mesh.faces)
                or not np.array_equal(state.surface.mesh.vertices, inputs.mesh.vertices)):
            return {"valid": False, "errors": ["ORIGINAL_GEOMETRY_OR_FACE_IDENTITY_CHANGED"]}
        return _geometry(state, resolution, options)

    # Freeze a real delivery before any search. Its heldout outcome is NEVER
    # used to choose proposals, weights, or development priorities below.
    baseline_started = time.monotonic()
    baseline_record: dict[str, object] = {"status": "NOT_COMPLETED", "before_search": True}
    try:
        packed = pack_budget_state(base, resolution, gap_pixels=options.gap_pixels,
                                   border_pixels=options.border_pixels)
        _, members = owners_and_members(packed)
        initial_atlas = bake_material(
            packed.surface, inputs.reference, resolution, member_ids=members,
            padding_pixels=options.padding_pixels,
            max_projection_fraction=options.max_projection_fraction, deadline=deadline,
        )
        baseline = FinalCandidate(-1, packed, initial_atlas, {}, math.inf)
        baseline_result = validate_final_candidates(
            inputs, [baseline], resolution, options, output, deadline, started,
            geometry_validator=check_final_geometry, archive=delivered, promote=False,
            log_prefix="baseline_validation",
        )
        baseline_record.update(status=baseline_result.status, reason=baseline_result.reason,
                               log=baseline_result.log)
    except (ValueError, RuntimeError, OSError, TimeoutError, FloatingPointError) as error:
        baseline_record.update(status="BUDGET_EXHAUSTED" if isinstance(error, TimeoutError)
                               else "INITIALIZATION_FAILED", reason=str(error))
    baseline_record["wall_seconds"] = time.monotonic() - baseline_started
    write_json(output / "baseline_record.json", baseline_record)
    for index in range(limit):
        if time.monotonic() >= deadline - cleanup:
            records.append({"candidate": index, "status": "BUDGET_EXHAUSTED", "action": "NOT_STARTED"})
            break
        folder = output / f"candidate_{index:03d}"
        folder.mkdir()
        record: dict[str, object] = {"candidate": index, "status": "NOT_RUN", "action": "independent_start"}
        records.append(record)
        before = fingerprint(base)
        action_started = time.monotonic()
        candidate: BudgetState | None = None
        try:
            emphasis: set[int] = set()
            if index == 0 and method in ("old_pair", "geometry_only"):
                record["action"] = "shared_geometry_from_common_planar_start"
                candidate, record["proposal"] = _proposal_reuse(inputs, base, method, options, deadline - cleanup)
            elif index == 0:
                candidate = base.copy()
            elif index == 1:
                record["action"] = "shared_geometry_from_common_planar_start"
                candidate, record["proposal"] = _proposal_reuse(inputs, base, method, options, deadline - cleanup)
            elif index == 2 and shared_work is not None:
                record["action"] = "whole_face_exclusive_exceptions"
                shared = {f for group in shared_work.groups for f in group.first_faces + group.second_faces}
                failed_raw = last_material.get("failed_faces", [])
                failed = [int(f) for f in failed_raw] if isinstance(failed_raw, list) else []
                selected = sorted(shared.intersection(failed))
                if not selected:
                    selected = sorted(shared)
                # Finite, complete-face move. Same priority but seeded ties.
                rng.shuffle(selected)
                selected = selected[:max(1, len(selected) // 3)]
                record["exclusive_original_faces"] = selected
                candidate = exclusive_faces(shared_work, tuple(selected))
            elif index == 3 and shared_work is not None:
                record["action"] = "UV_induced_surface_correspondence"
                update = update_uv_correspondences(shared_work, deadline=deadline - cleanup,
                                                   position_fraction=options.correspondence_fraction,
                                                   normal_angle_degrees=options.normal_angle_degrees)
                record["correspondence_update"] = update.diagnostics
                write_json(folder / "correspondence_records.json", update.records)
                if update.state is None:
                    raise ValueError(update.status)
                candidate = update.state
            elif index == 4:
                record["action"] = "original_edge_seam_deletion"
                source = archive[0][1] if archive else base
                edges = sorted(source.surface.seams)
                rng.shuffle(edges)
                for edge in edges[:20]:
                    try:
                        candidate = planar_merge(source, edge)
                        record["removed_original_edge"] = edge
                        break
                    except ValueError:
                        continue
                if candidate is None:
                    raise ValueError("NO_SEAM_DELETION_PROPOSAL")
            else:
                record["action"] = "development_violation_reweighting"
                if not archive:
                    raise ValueError("WORK_ARCHIVE_EMPTY")
                candidate = archive[index % len(archive)][1].copy()
                failed_raw = archive[index % len(archive)][3].get("failed_faces", [])
                emphasis = set(map(int, failed_raw)) if isinstance(failed_raw, list) else set()
            if candidate is None:
                raise ValueError("EMPTY_CANDIDATE")
            # Every geometry mutation invalidates texture/cache. Reweighting alone
            # preserves the same packed UV and texture as a warm internal move.
            if candidate.texture is None:
                candidate = pack_budget_state(candidate, resolution, gap_pixels=options.gap_pixels,
                                               border_pixels=options.border_pixels)
            geometry = _safety(candidate, resolution, options)
            record["geometry"] = geometry
            if not geometry["valid"]:
                record["status"] = _geometry_rejection(geometry)
                continue
            write_json(folder / "groups.json", [asdict(g) for g in candidate.groups])
            allocation = (deadline - cleanup - time.monotonic()) / max(1, limit - index)
            candidate_stop = min(deadline - cleanup, time.monotonic() + max(0.1, allocation))
            optimized, atlas, material, optimization = _optimize_candidate(
                inputs, candidate, resolution, options, candidate_stop, method, folder, emphasis,
            )
            optimized_geometry = _safety(optimized, resolution, options)
            if not optimized_geometry["valid"]:
                record.update(status=_geometry_rejection(optimized_geometry), geometry=optimized_geometry)
                continue
            record.update(status="DEV_VALIDATED" if material["valid"] else "QUALITY_REJECTED",
                          material=_compact_material(material), geometry=optimized_geometry,
                          optimization={key: value for key, value in optimization.items() if key != "history"})
            write_json(folder / "development_material.json", material)
            save_state(folder / "state.json", optimized.surface)
            write_json(folder / "correspondences.json", [asdict(c) for c in optimized.correspondences])
            if optimized.groups:
                shared_work, last_material = optimized.copy(), material
            violation = _development_feasibility(
                material, inputs.mesh, MaterialThresholds(**options.material_thresholds))
            archive.append((violation, optimized.copy(), atlas, material))
            archive.sort(key=lambda item: item[0])
            archive = archive[:options.work_archive_size]
            final_pool.append(FinalCandidate(index, optimized.copy(), copy.deepcopy(atlas),
                                              copy.deepcopy(material), violation))
            final_pool.sort(key=lambda entry: entry.rank())
            final_pool = final_pool[:options.final_validation_candidates]
        except TimeoutError as error:
            record.update(status="BUDGET_EXHAUSTED", reason=str(error))
        except (ValueError, RuntimeError, FloatingPointError, np.linalg.LinAlgError) as error:
            record.update(status="INITIALIZATION_FAILED" if candidate is None else "NUMERICAL_FAILURE",
                          reason=str(error))
        finally:
            record["wall_seconds"] = time.monotonic() - action_started
            record["full_base_rollback_verified"] = fingerprint(base) == before == frozen_base
            write_json(folder / "record.json", record)
            write_json(output / "candidate_log.json", records)
    final_result: FinalValidationResult | None = None
    if final_pool or delivered.best is not None:
        final_result = validate_final_candidates(
            inputs, sorted(final_pool, key=lambda entry: entry.rank()), resolution,
            options, output, deadline, started, geometry_validator=check_final_geometry, archive=delivered,
        )
        status, reason = final_result.status, final_result.reason
        final_geometry, final_material, render = final_result.geometry, final_result.material, final_result.render
    elif baseline_result is not None:
        final_result = baseline_result
        if baseline_result.selected_folder is not None:
            try:
                promote_final_candidate(baseline_result.selected_folder, output)
            except (OSError, ValueError, RuntimeError) as error:
                baseline_result.promotion = {"status": "OUTPUT_COPY_FAILED", "reason": str(error)}
        status, reason = (baseline_result.status, baseline_result.reason) if method == "bake_only" else (
            _failed_target_status(records), "NO_SEARCH_CANDIDATE_PASSED_HARD_SAFETY")
        final_geometry, final_material, render = baseline_result.geometry, baseline_result.material, baseline_result.render
    else:
        reason = "NO_GEOMETRICALLY_LEGAL_EVALUATED_CANDIDATE"
        status = "BUDGET_EXHAUSTED" if time.monotonic() >= deadline - cleanup else _failed_target_status(records)
    if time.monotonic() > deadline and delivered.best is None:
        status = "BUDGET_EXHAUSTED"
    final_state = final_result.selected.state if final_result is not None else None
    report: dict[str, object] = {
        "model": inputs.model, "method": method, "seed": seed, "resolution": resolution,
        "status": status, "reason": reason, "valid": status == "VALIDATED",
        "payload_bytes": 3 * texture_payload_bytes(resolution, resolution),
        "wall_seconds": time.monotonic() - started, "seconds_limit": seconds,
        "common_preparation_seconds": inputs.setup_seconds,
        "baseline_validation": baseline_record,
        "delivery_protocol": delivered.protocol,
        "delivered_entries": len(delivered.entries),
        "accepted_completed_seconds": delivered.best.completed_at - started if delivered.best is not None else None,
        "accepted_before_target_deadline": delivered.best is not None,
        "delivery_fallback_used": final_result is not None and final_result.accepted is not None
                                  and final_result.accepted.candidate_id == -1,
        "geometry": final_geometry, "material": _compact_material(final_material), "render": render,
        "final_groups": len(final_state.groups) if final_state else 0,
        "candidate_count": len(records), "candidates": records,
        "final_validation_candidates_limit": options.final_validation_candidates,
        "final_validation_reserve_seconds": cleanup,
        "final_validation_order": [entry.candidate_id for entry in sorted(final_pool, key=lambda item: item.rank())],
        "final_validation_log": final_result.log if final_result is not None else [],
        "accepted_candidate_id": final_result.accepted.candidate_id
                                 if final_result is not None and final_result.accepted is not None else None,
        "accepted_candidate_fingerprint": fingerprint(final_result.accepted.state)
                                          if final_result is not None and final_result.accepted is not None else None,
        "full_rollback_verified": all(r.get("full_base_rollback_verified", True) for r in records),
        "scope": "all_original_faces", "original_face_count": len(inputs.mesh.faces),
        "geometry_unchanged": final_state is None or (
            np.array_equal(final_state.surface.mesh.faces, inputs.mesh.faces)
            and np.array_equal(final_state.surface.mesh.vertices, inputs.mesh.vertices)),
        "seams": seam_metrics(final_state.surface) if final_state is not None else {},
        "mesh_attribute_bytes": len(inputs.mesh.faces) * 3 * 48 + len(inputs.mesh.faces) * 12
                                if final_state is not None else None,
        "mesh_attribute_policy": "actual_export_explicit_per_corner_48_bytes_plus_uint32_indices",
        "mesh_attribute_delta_vs_baseline_bytes": 0 if final_state is not None else None,
        "mesh_attribute_delta_reason": "identical_per_original_corner_attribute_layout_all_methods",
        "bake_texture_ablation": "bake_only" if method == "bake_only" else
                                 "fixed_U_texture_restoration" if method == "texture_only" else
                                 "method_with_shared_restoration",
        "codec": "three_RGBA8_images_complete_mips",
        "accepted_scope": "frozen_finite_CPU_contract_only", "gpu_validated": False,
        "artist_reviewed": False, "global_optimality_proven": False,
        "independent_budget_initialization": True, "heldout_used_for_candidate_feedback": False,
        "method_seed_role": "proposal tie order and exception ordering; xatlas deterministic",
    }
    write_json(output / "record.json", report)
    return report
