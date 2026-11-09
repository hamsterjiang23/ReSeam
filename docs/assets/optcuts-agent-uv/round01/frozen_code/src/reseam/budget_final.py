"""Complete final evidence and immutable, baseline-first delivery selection.

The caller freezes the development candidate pool before this entry point.
Heldout evidence never changes that pool or feeds another optimization step.
"""

from __future__ import annotations

import copy
import hashlib
import math
import shutil
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np

from .budget_delivery import DeliveredArchive, DeliveredEntry
from .budget_material import (
    MaterialAtlas,
    MaterialThresholds,
    evaluate_material,
    export_material_glb,
    save_material,
)
from .budget_render import evaluate_render, prepare_render_reference
from .budget_state import BudgetState, fingerprint, validate_budget_geometry
from .mesh import save_state, write_obj
from .pipeline import write_json

if TYPE_CHECKING:
    from .budget_solver import BudgetInputs, SolverOptions


@dataclass(frozen=True)
class FinalCandidate:
    candidate_id: int
    state: BudgetState
    atlas: MaterialAtlas
    development: dict[str, object]
    feasibility: float

    def rank(self) -> tuple[int, float, int]:
        """Development-only order used by the caller before final validation."""
        return (0 if self.development.get("valid") is True else 1,
                self.feasibility, self.candidate_id)


@dataclass
class FinalValidationResult:
    status: str
    selected: FinalCandidate
    accepted: FinalCandidate | None
    geometry: dict[str, object]
    material: dict[str, object]
    render: dict[str, object]
    log: list[dict[str, object]]
    reason: str | None = None
    archive: DeliveredArchive | None = None
    selected_folder: Path | None = None
    promotion: dict[str, object] = field(default_factory=dict)


def encoded_fingerprint(atlas: MaterialAtlas) -> str:
    digest = hashlib.sha256()
    for chain in (atlas.base_color, atlas.normal, atlas.orm):
        for image in chain:
            digest.update(str((image.shape, image.dtype.str)).encode("ascii"))
            digest.update(image.tobytes())
    digest.update(atlas.coverage.tobytes())
    return digest.hexdigest()


def _save_raw_candidate(folder: Path, entry: FinalCandidate) -> None:
    save_state(folder / "state.json", entry.state.surface)
    write_obj(folder / "mesh_uv.obj", entry.state.surface)
    write_json(folder / "groups.json", [asdict(group) for group in entry.state.groups])
    write_json(folder / "correspondences.json", [asdict(item) for item in entry.state.correspondences])
    write_json(folder / "correspondence_records.json", entry.state.correspondence_records)
    write_json(folder / "state_evidence.json", entry.state.evidence)
    if entry.state.texture is not None:
        np.save(folder / "state_texture.npy", entry.state.texture, allow_pickle=False)
    write_json(folder / "development.json", entry.development)
    sizes = save_material(entry.atlas, folder / "textures")
    write_json(folder / "texture_file_sizes.json", sizes)


def save_final_candidate(
    folder: Path, entry: FinalCandidate, geometry: dict[str, object], material: dict[str, object],
) -> None:
    """Save raw evidence before the preview exporter can reject invalid geometry."""
    if not (folder / "state.json").exists():
        _save_raw_candidate(folder, entry)
    write_json(folder / "material.json", material)
    write_json(folder / "geometry.json", geometry)
    exported = export_material_glb(entry.state.surface, entry.atlas, folder / "material_lod0_preview.glb")
    write_json(folder / "export.json", exported)


def promote_final_candidate(folder: Path, output: Path) -> None:
    """Copy convenience deliverables without modifying the accepted snapshot."""
    for path in folder.iterdir():
        if path.name == "record.json":
            continue
        target = output / path.name
        if target.exists():
            raise FileExistsError(target)
        if path.is_dir():
            shutil.copytree(path, target)
        else:
            shutil.copyfile(path, target)


def _compact(report: dict[str, object]) -> dict[str, object]:
    return {key: value for key, value in report.items() if key not in ("faces", "raster_faces", "raster")}


def make_delivered_archive(options: SolverOptions, deadline: float) -> DeliveredArchive:
    """Freeze all scalar geometry and material gates before baseline evaluation."""
    return DeliveredArchive(deadline=deadline,
        material_thresholds=MaterialThresholds(**options.material_thresholds),
        geometry_thresholds={
            "mean_shape_distortion": options.mean_shape_limit,
            "shape_cvar95": options.tail_shape_limit,
            "maximum_anisotropy": options.maximum_anisotropy,
            "maximum_neighbor_density_ratio": options.maximum_density_ratio,
            "minimum_gap_pixels": options.gap_pixels,
            "minimum_border_pixels": options.border_pixels,
        })


def _geometry(state: BudgetState, resolution: int, options: SolverOptions) -> dict[str, object]:
    return validate_budget_geometry(state, resolution,
        gap_pixels=options.gap_pixels, border_pixels=options.border_pixels,
        maximum_anisotropy=options.maximum_anisotropy, mean_shape_limit=options.mean_shape_limit,
        tail_shape_limit=options.tail_shape_limit, maximum_density_ratio=options.maximum_density_ratio,
        correspondence_fraction=options.correspondence_fraction,
        normal_angle_degrees=options.normal_angle_degrees)


def _geometry_status(geometry: dict[str, object]) -> str:
    text = str(geometry.get("errors", []))
    structural = ("FLIPPED", "DEGENERATE", "INTERSECTION", "UNAUTHORIZED", "INVALID_UV",
                  "CONNECTIVITY", "NONMANIFOLD", "NON_PLANAR", "UNREALIZED", "NOT_ISOLATED")
    return "STRUCTURALLY_INVALID" if any(token in text for token in structural) else "GEOMETRY_REJECTED"


def _check_payload(entry: FinalCandidate, row: dict[str, object]) -> None:
    if (fingerprint(entry.state) != row["state_fingerprint"]
            or encoded_fingerprint(entry.atlas) != row["encoded_material_sha256"]):
        raise ValueError("FINAL_EVIDENCE_PAYLOAD_MUTATED")


def _source_provenance(inputs: BudgetInputs, folder: Path, projection_fraction: float) -> dict[str, object]:
    """Preserve heldout/source identity, including invalid projection records."""
    cache, reference = inputs.heldout, inputs.reference
    target = cache.reference
    projection_scale = reference.diagonal
    distance_limit = projection_fraction * projection_scale
    confidence = np.maximum(0., 1. - target.distances / distance_limit) * target.valid
    np.savez_compressed(folder / "heldout_source_samples.npz",
        low_face_ids=cache.face_ids, low_barycentric=cache.barycentric,
        weights=cache.weights, seam_samples=cache.seam_samples,
        source_face_ids=target.target_faces, source_barycentric=target.barycentric,
        reference_valid=target.valid, reference_distances=target.distances,
        source_alpha=target.base_color[:, 3], projection_confidence_heuristic=confidence,
        reference_base_color=target.base_color, reference_normal_world=target.normal_world,
        reference_orm=target.orm)
    ids = np.unique(target.target_faces)
    ids = ids[(ids >= 0) & (ids < len(reference.normals))]
    # The original IDs are retained even when projection is invalid. A subset
    # with explicit triangle positions avoids duplicating the entire high mesh.
    original = reference.geometry.original_face_ids
    assert original is not None
    lookup = {int(face_id): index for index, face_id in enumerate(original)}
    available = np.asarray([face_id for face_id in ids if int(face_id) in lookup], dtype=np.int64)
    rows = np.asarray([lookup[int(face_id)] for face_id in available], dtype=np.int64)
    np.savez_compressed(folder / "source_material_faces.npz", source_face_ids=available,
        source_triangles=reference.geometry.vertices[reference.geometry.faces[rows]],
        source_corner_uv=reference.metadata_corner_uv(available),
        source_normals=reference.normals[available], source_tangents=reference.tangents[available],
        source_signs=reference.signs[available])
    digest = hashlib.sha256()
    for array in (reference.geometry.vertices, reference.geometry.faces,
                  reference.geometry.corner_uv, reference.geometry.image,
                  reference.normal_image, reference.orm_image):
        digest.update(str((array.shape, array.dtype.str)).encode("ascii"))
        digest.update(array.tobytes())
    if reference.occlusion_image is not None:
        digest.update(reference.occlusion_image.tobytes())
    source: dict[str, object] = {
        "model": inputs.model, "reference_metadata": copy.deepcopy(reference.metadata),
        "source_geometry_material_sha256": digest.hexdigest(),
        "heldout_geometry_sha256": cache.geometry_sha256,
        "source_face_count": len(reference.geometry.faces),
        "sampled_source_face_count": len(available),
        "normal_scale": reference.normal_scale, "metallic_factor": reference.metallic_factor,
        "roughness_factor": reference.roughness_factor, "occlusion_strength": reference.occlusion_strength,
        "projection_normalization": "source all-vertex bounding-box diagonal",
        "projection_scale": projection_scale, "max_projection_fraction": projection_fraction,
        "projection_distance_limit": distance_limit,
        "projection_confidence_rule": "max(0, 1 - distance / projection_distance_limit) * reference_valid",
        "projection_confidence_status": "HEURISTIC_NOT_CALIBRATED",
        "visibility_status": "NOT_EVALUATED",
        "heldout_feedback_to_optimization": False,
    }
    write_json(folder / "source_reference.json", source)
    return source


def _file_manifest(folder: Path) -> list[dict[str, object]]:
    return [{"path": path.relative_to(folder).as_posix(), "bytes": path.stat().st_size,
             "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
            for path in sorted(folder.rglob("*")) if path.is_file()
            and path.name not in ("record.json", "provenance.json")]


def _candidate_from_delivery(entry: DeliveredEntry) -> FinalCandidate:
    provenance = entry.provenance
    number = provenance.get("candidate_id")
    development = provenance.get("development")
    feasibility = provenance.get("development_feasibility")
    return FinalCandidate(
        int(number) if isinstance(number, int) else int(entry.candidate_id), entry.state, entry.atlas,
        development if isinstance(development, dict) else {},
        float(feasibility) if isinstance(feasibility, (int, float)) else math.inf)


def _choose_delivery(result: FinalValidationResult, archive: DeliveredArchive) -> None:
    best = archive.best
    if best is None:
        return
    result.selected = result.accepted = _candidate_from_delivery(best)
    result.geometry, result.material, result.render = best.geometry, best.material, best.render
    result.status, result.reason = "VALIDATED", None
    folder = best.provenance.get("output_directory")
    result.selected_folder = Path(folder) if isinstance(folder, str) else None


def _write_archive(archive: DeliveredArchive, output: Path, started: float) -> None:
    best = archive.best
    if best is None:
        return
    write_json(output / "accepted_archive.json", {
        "candidate_id": best.provenance.get("candidate_id", best.candidate_id),
        "archive_candidate_id": best.candidate_id,
        "state_fingerprint": fingerprint(best.state),
        "encoded_material_sha256": encoded_fingerprint(best.atlas),
        "payload_bytes": best.payload_bytes, "rank": best.rank,
        "completed_at": best.completed_at,
        "completed_seconds_from_start": best.completed_at - started,
        "validated_seconds_from_start": best.provenance.get("validation_seconds_from_start"),
        "geometry": best.geometry, "material": _compact(best.material), "render": best.render,
        "provenance": best.provenance, "heldout_feedback_to_optimization": False,
        "entries": [{"candidate_id": item.candidate_id, "payload_bytes": item.payload_bytes,
                     "rank": item.rank, "completed_at": item.completed_at,
                     "output_directory": item.provenance.get("output_directory")}
                    for item in archive.entries],
    })


def _unique_run(output: Path, prefix: str) -> tuple[Path, Path]:
    if not prefix or Path(prefix).name != prefix:
        raise ValueError("FINAL_LOG_PREFIX_MUST_BE_A_SINGLE_PATH_COMPONENT")
    index = 0
    while True:
        label = prefix if index == 0 else f"{prefix}_{index:03d}"
        folder = output / label
        logfile = output / f"{label}_log.json"
        if not folder.exists() and not logfile.exists():
            folder.mkdir(parents=True, exist_ok=False)
            return folder, logfile
        index += 1


def validate_final_candidates(
    inputs: BudgetInputs, candidates: list[FinalCandidate], resolution: int,
    options: SolverOptions, output: Path, deadline: float, started: float, *,
    geometry_validator: Callable[[BudgetState], dict[str, object]] | None = None,
    archive: DeliveredArchive | None = None, promote: bool = True,
    log_prefix: str = "final_validation",
) -> FinalValidationResult:
    """Validate every pre-frozen DEV top-K entry, then select exact delivery rank.

    A baseline call uses ``promote=False`` before search. The subsequent call
    shares its archive and can pass an empty pool to promote that known result.
    Late or failed work never removes a completed accepted snapshot.
    """
    if not math.isfinite(deadline) or not math.isfinite(started) or deadline < started:
        raise ValueError("FINITE_FINAL_DEADLINE_REQUIRED")
    archive = archive if archive is not None else make_delivered_archive(options, deadline)
    if archive.deadline != deadline:
        raise ValueError("DELIVERY_DEADLINE_PROTOCOL_MISMATCH")
    # Copy the complete selected pool before the first heldout evaluation.
    frozen = copy.deepcopy(candidates[:options.final_validation_candidates])
    if not frozen and archive.best is None:
        raise ValueError("EMPTY_FINAL_CANDIDATE_POOL")
    selected = frozen[0] if frozen else _candidate_from_delivery(archive.entries[0])
    diagnostic = copy.deepcopy(selected.development)
    diagnostic.update(valid=False, development_valid=diagnostic.get("valid") is True,
                      status="FINAL_NOT_COMPLETED_DEV_DIAGNOSTICS_ONLY")
    result = FinalValidationResult("QUALITY_REJECTED", selected, None, {}, diagnostic,
        {"valid": False, "status": "NOT_RUN"}, [], archive=archive)
    root, logfile = _unique_run(output, log_prefix)
    write_json(root / "delivery_protocol.json", archive.protocol)
    log = [{"candidate_id": entry.candidate_id, "rank": rank,
            "development_valid": entry.development.get("valid") is True,
            "development_feasibility": entry.feasibility,
            "state_fingerprint": fingerprint(entry.state),
            "encoded_material_sha256": encoded_fingerprint(entry.atlas),
            "payload_bytes": entry.atlas.payload_bytes,
            "status": "NOT_RUN_FINAL_BUDGET"} for rank, entry in enumerate(frozen)]
    result.log = log
    write_json(logfile, log)
    prepared_render = None
    for rank, entry in enumerate(frozen):
        row = log[rank]
        if time.monotonic() >= deadline:
            result.status, result.reason = "BUDGET_EXHAUSTED", "FINAL_VALIDATION_BUDGET_EXHAUSTED"
            break
        # Exact previously completed snapshots do not need another heldout pass.
        existing = next((item for item in archive.entries
            if item.provenance.get("state_fingerprint") == row["state_fingerprint"]
            and item.provenance.get("encoded_material_sha256") == row["encoded_material_sha256"]), None)
        if existing is not None:
            row.update(status="ALREADY_VALIDATED_DELIVERY", archive_candidate_id=existing.candidate_id)
            continue
        folder = root / f"candidate_{entry.candidate_id:03d}"
        folder.mkdir(parents=True, exist_ok=False)
        result.selected_folder = result.selected_folder or folder
        validation_started = time.monotonic()
        geometry: dict[str, object] = {}
        material: dict[str, object] = {}
        render: dict[str, object] = {"valid": False, "status": "NOT_RUN"}
        validation_completed: float | None = None
        try:
            if not math.isfinite(validation_started) or validation_started < started:
                raise ValueError("INVALID_FINAL_VALIDATION_CLOCK")
            # Persist the exact candidate and projection identities first, so a
            # validator timeout still has real maps and source-face evidence.
            _save_raw_candidate(folder, entry)
            source = _source_provenance(inputs, folder, options.max_projection_fraction)
            if time.monotonic() >= deadline:
                raise TimeoutError("FINAL_SNAPSHOT_CROSSED_DEADLINE")
            geometry = (geometry_validator(entry.state) if geometry_validator is not None
                        else _geometry(entry.state, resolution, options))
            if geometry.get("valid") is True:
                material = evaluate_material(entry.state.surface, inputs.reference, entry.atlas,
                    prepared=inputs.heldout, mip_levels=tuple(options.mip_levels),
                    thresholds=archive.material_thresholds,
                    important_faces=tuple(sorted(archive.important_faces)),
                    max_projection_fraction=options.max_projection_fraction,
                    raster_checks=True, deadline=deadline)
                if time.monotonic() >= deadline:
                    raise TimeoutError("FINAL_MATERIAL_CHECK_CROSSED_DEADLINE")
                if prepared_render is None:
                    prepared_render = prepare_render_reference(inputs.mesh, inputs.reference,
                        size=options.render_size, max_projection_fraction=options.max_projection_fraction,
                        deadline=deadline)
                limits = archive.render_thresholds
                render = evaluate_render(entry.state.surface, entry.atlas, prepared_render, folder / "render",
                    mean_limit=limits["mean"], p95_limit=limits["p95"], max_limit=limits["max"],
                    mip_levels=tuple(options.mip_levels), deadline=deadline)
            validation_completed = time.monotonic()
            if not math.isfinite(validation_completed) or validation_completed < validation_started:
                raise ValueError("INVALID_FINAL_VALIDATION_CLOCK")
            row.update(validation_completed_at=validation_completed,
                       validation_seconds_from_start=validation_completed - started)
            if validation_completed >= deadline:
                raise TimeoutError("FINAL_VALIDATION_CROSSED_DEADLINE")
            _check_payload(entry, row)
            # Diagnostic exports include failed material/geometry evidence too.
            save_final_candidate(folder, entry, geometry, material)
            write_json(folder / "render_report.json", render)
            exported_at = time.monotonic()
            if not math.isfinite(exported_at) or exported_at < validation_completed:
                raise ValueError("INVALID_FINAL_EXPORT_CLOCK")
            row.update(export_completed_at=exported_at, export_seconds_from_start=exported_at - started)
            if exported_at >= deadline:
                raise TimeoutError("FINAL_EXPORT_CROSSED_DEADLINE")
            _check_payload(entry, row)
            provenance: dict[str, object] = {
                "candidate_id": entry.candidate_id, "output_directory": str(folder.resolve()),
                "state_fingerprint": row["state_fingerprint"],
                "encoded_material_sha256": row["encoded_material_sha256"],
                "payload_bytes": entry.atlas.payload_bytes,
                "started_at": started,
                "development": entry.development, "development_feasibility": entry.feasibility,
                "validation_completed_at": validation_completed,
                "validation_seconds_from_start": validation_completed - started,
                "export_completed_at": exported_at, "export_seconds_from_start": exported_at - started,
                "source": source, "files": _file_manifest(folder),
                "heldout_feedback_to_optimization": False,
                "completion_scope": "persisted evidence plus final ranking and immutable snapshot preparation",
                "postpublication_bookkeeping": ["record.json", "provenance.json", "accepted_archive.json",
                                                 "convenience_root_copy"],
            }
            write_json(folder / "provenance.json", provenance)
            row["status"] = "FINAL_EVIDENCE_EXPORTED"
            write_json(folder / "record.json", row)
            persisted_at = time.monotonic()
            if not math.isfinite(persisted_at) or persisted_at < exported_at:
                raise ValueError("INVALID_COMPLETION_ELAPSED_TIME")
            provenance["evidence_files_persisted_at"] = persisted_at
            row["evidence_files_persisted_at"] = persisted_at
            offered = archive.offer(candidate_id=str(entry.candidate_id), state=entry.state, atlas=entry.atlas,
                geometry=geometry, material=material, render=render, payload_bytes=entry.atlas.payload_bytes,
                completed_at=persisted_at, provenance=provenance, completion_clock=time.monotonic)
            if offered.completed_at is not None:
                completed_at = offered.completed_at
                elapsed = completed_at - started if completed_at >= started else None
                row.update(completed_at=completed_at, completed_seconds_from_start=elapsed,
                           completion_elapsed_valid=elapsed is not None,
                           completed_within_deadline=started <= completed_at <= deadline)
                provenance.update(completed_at=completed_at,
                                  completed_seconds_from_start=elapsed)
            if offered.accepted:
                row.update(status="VALIDATED", delivery_rank=offered.rank)
                # This receipt records the committed timestamp; all payloads,
                # validation reports and manifest hashes were generated first.
                try:
                    write_json(folder / "provenance.json", provenance)
                except OSError as error:
                    row["postpublication_receipt_error"] = str(error)
            else:
                row.update(status=("BUDGET_EXHAUSTED" if offered.reason == "COMPLETED_AFTER_DEADLINE" else
                    "OUTPUT_VALIDATION_FAILED" if offered.reason in ("INVALID_COMPLETION_CLOCK",
                        "COMPLETION_CLOCK_MOVED_BACKWARDS", "INVALID_COMPLETION_ELAPSED_TIME") else
                    _geometry_status(geometry) if geometry.get("valid") is not True else "QUALITY_REJECTED"),
                    reason=offered.reason)
        except TimeoutError as error:
            row.update(status="BUDGET_EXHAUSTED", reason=str(error))
            result.status, result.reason = "BUDGET_EXHAUSTED", str(error)
        except (OSError, ValueError, RuntimeError) as error:
            if geometry and geometry.get("valid") is not True:
                row.update(status=_geometry_status(geometry), diagnostic_export_error=str(error))
            else:
                row.update(status="OUTPUT_VALIDATION_FAILED", reason=str(error))
        finally:
            # Raw reports and encoded maps already saved remain untouched. The
            # small diagnostic records are allowed to finish after the deadline.
            finished_at = time.monotonic()
            clock_valid = (math.isfinite(finished_at) and math.isfinite(validation_started)
                           and finished_at >= validation_started)
            row.update(wall_seconds=finished_at - validation_started if clock_valid else None,
                       wall_clock_valid=clock_valid,
                       geometry=geometry, material=_compact(material), render=render)
            row.setdefault("completed_within_deadline", False)
            write_json(folder / "record.json", row)
            if not (folder / "geometry.json").exists():
                write_json(folder / "geometry.json", geometry)
            if not (folder / "material.json").exists():
                write_json(folder / "material.json", material)
            write_json(logfile, log)
            if rank == 0:
                result.geometry = geometry
                if material:
                    result.material = material
                result.render = render
        if row["status"] == "BUDGET_EXHAUSTED":
            break
    _choose_delivery(result, archive)
    if result.accepted is None and result.status != "BUDGET_EXHAUSTED":
        statuses = {row["status"] for row in log}
        for status in ("QUALITY_REJECTED", "OUTPUT_VALIDATION_FAILED", "STRUCTURALLY_INVALID",
                       "GEOMETRY_REJECTED"):
            if status in statuses:
                result.status = status
                break
    write_json(logfile, log)
    _write_archive(archive, output, started)
    if promote and result.selected_folder is not None:
        try:
            promote_final_candidate(result.selected_folder, output)
            result.promotion = {"status": "COPIED", "source": str(result.selected_folder)}
        except (OSError, ValueError, RuntimeError) as error:
            # Root convenience-copy failure cannot revoke the archived delivery.
            result.promotion = {"status": "OUTPUT_COPY_FAILED", "reason": str(error),
                                "source": str(result.selected_folder)}
    return result


# Keep the former solver helper names available to thin compatibility wrappers.
_encoded_fingerprint = encoded_fingerprint
_save_final_candidate = save_final_candidate
_promote_final_candidate = promote_final_candidate
_validate_final_candidates = validate_final_candidates
