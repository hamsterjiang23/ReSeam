"""Independent, bounded ROI development experiment for the new derivation/v1.

Geometry proposals and Base Color diagnostics are deliberately not full material
acceptance. Historical algorithms, accounts and output directories stay untouched.
"""

import importlib.metadata
import json
import math
import platform
import re
import shutil
import time
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

from .budget_geometry import validate_pair_geometry
from .budget_pair import BudgetPairProblem, solve_budget_pair
from .budget_texture import bake_atlas, evaluate_atlas, load_reference, texture_payload_bytes
from .joint_regions import RegionPair, paired_problem, planar_regions, refine_pair, region_pairs
from .mesh import save_state, sha256, write_obj
from .pipeline import export_glb, write_json
from .types import IntArray, Mesh, MeshState


@dataclass(frozen=True)
class BudgetExperimentConfig:
    round_id: str
    model: str = "dragon"
    seed: int = 17
    seconds: float = 300
    solver_seconds: float = 30
    solver_iterations: int = 240
    maximum_region_faces: int = 32
    maximum_roi_attempts: int = 15
    resolutions: tuple[int, ...] = (32, 64, 128)
    mip_levels: tuple[int, ...] = (0, 1, 2)
    samples_per_face: int = 16
    gap_pixels: float = 2
    border_pixels: float = 2
    padding_pixels: int = 2
    outer_fraction: float = 0.20
    correspondence_fraction: float = 0.005
    normal_angle_degrees: float = 45
    maximum_anisotropy: float = 2
    mean_shape_limit: float = 0.025
    maximum_neighbor_density_ratio: float = 2
    max_projection_fraction: float = 0.02
    color_mean_limit: float = 0.025
    color_p95_limit: float = 0.08
    color_max_limit: float = 0.20
    coupling_weight: float = 10

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[a-zA-Z0-9_-]+", self.round_id):
            raise ValueError("round_id must be a nonempty portable directory identifier")
        if self.model not in ("dragon", "energy_blade"):
            raise ValueError("Only fixed v1 assets are supported")
        integer_fields = (
            self.seed,
            self.solver_iterations,
            self.maximum_region_faces,
            self.maximum_roi_attempts,
            self.samples_per_face,
            self.padding_pixels,
        )
        if any(type(value) is not int for value in integer_fields):
            raise ValueError("Integer settings must not be booleans or floats")
        if self.seed < 0 or min(integer_fields[1:]) < 1 or self.maximum_region_faces < 8:
            raise ValueError("Invalid count or seed")
        positive = (
            self.seconds,
            self.solver_seconds,
            self.gap_pixels,
            self.border_pixels,
            self.outer_fraction,
            self.correspondence_fraction,
            self.normal_angle_degrees,
            self.maximum_anisotropy,
            self.mean_shape_limit,
            self.maximum_neighbor_density_ratio,
            self.max_projection_fraction,
            self.color_mean_limit,
            self.color_p95_limit,
            self.color_max_limit,
            self.coupling_weight,
        )
        if not all(math.isfinite(x) and x > 0 for x in positive):
            raise ValueError("Positive finite experimental settings required")
        if self.solver_seconds >= self.seconds or self.seconds < 10 or self.maximum_anisotropy < 1:
            raise ValueError("Budget or anisotropy settings outside supported range")
        if not 0 < self.outer_fraction < 0.5 or self.maximum_neighbor_density_ratio < 1:
            raise ValueError("Invalid region or density setting")
        if not self.resolutions or len(set(self.resolutions)) != len(self.resolutions):
            raise ValueError("Distinct resolutions required")
        if any(type(n) is not int or n < 16 or n > 1024 for n in self.resolutions):
            raise ValueError("Development resolution must be an integer in [16, 1024]")
        if not self.mip_levels or any(type(n) is not int or n < 0 for n in self.mip_levels):
            raise ValueError("Nonnegative integer mip levels required")
        if max(self.mip_levels) > int(math.log2(min(self.resolutions))):
            raise ValueError("Requested mip is unavailable in at least one budget")
        if min(self.resolutions) <= 2 * self.border_pixels + self.gap_pixels:
            raise ValueError("Atlas has no interior area")


def load_budget_config(path: Path) -> BudgetExperimentConfig:
    raw = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(raw, dict):
        raise TypeError("Expected JSON configuration object")
    for key in ("resolutions", "mip_levels"):
        if key in raw:
            if not isinstance(raw[key], list):
                raise ValueError(f"{key} must be a JSON array")
            raw[key] = tuple(raw[key])
    return BudgetExperimentConfig(**raw)


def pack_pair(
    state: MeshState,
    member_ids: IntArray,
    shared: bool,
    resolution: int,
    border_pixels: float,
    gap_pixels: float,
) -> MeshState:
    """One common positive scale, deterministic shelf baseline; no per-member scaling."""
    if member_ids.shape != (len(state.mesh.faces),) or not np.isfinite(state.uv).all():
        raise ValueError("Invalid pair state")
    groups = (
        [np.unique(state.uv_faces)]
        if shared
        else [np.unique(state.uv_faces[member_ids == label]) for label in np.unique(member_ids)]
    )
    if sum(len(group) for group in groups) != len(state.uv):
        raise ValueError("Packing groups cross UV identities")
    minimum = [state.uv[ids].min(axis=0) for ids in groups]
    extents = [np.ptp(state.uv[ids], axis=0) for ids in groups]
    if any(np.any(extent <= 1e-12) for extent in extents):
        raise ValueError("Degenerate packing extent")
    interior = resolution - 2 * border_pixels
    scale = min(
        (interior - gap_pixels * (len(groups) - 1)) / sum(e[0] for e in extents),
        interior / max(e[1] for e in extents),
    )
    if scale <= 0:
        raise ValueError("No packing area")
    packed = state.copy()
    cursor = border_pixels
    for ids, low, extent in zip(groups, minimum, extents, strict=True):
        offset = np.array([cursor, border_pixels + (interior - scale * extent[1]) / 2])
        packed.uv[ids] = ((state.uv[ids] - low) * scale + offset) / resolution
        cursor += scale * extent[0] + gap_pixels
    return packed


def select_wing_pair(
    mesh: Mesh,
    config: BudgetExperimentConfig,
    deadline: float,
    diagnostics_path: Path | None = None,
) -> tuple[RegionPair, dict[str, object]]:
    """Geometry-only heuristic; not artist confirmation or a material-selected ROI."""
    candidates, planes = region_pairs(mesh, deadline)
    patches = planar_regions(mesh, candidates, config.maximum_region_faces)
    eligible: list[tuple[float, int, RegionPair]] = []
    records: list[dict[str, object]] = []
    for index, pair in enumerate(patches):
        if time.monotonic() >= deadline:
            raise TimeoutError("ROI_SELECTION_TIMEOUT")
        ids = list(pair.first_faces + pair.second_faces)
        positions = mesh.vertices[mesh.faces[ids]]
        normal = np.asarray(pair.normal)
        span = float(np.ptp(mesh.vertices @ normal))
        outer = float(np.mean(np.abs(positions @ normal - pair.offset))) / max(span, 1e-15)
        area = float(mesh.areas[ids].sum())
        passed = outer >= config.outer_fraction
        records.append(
            {
                "index": index,
                "area": area,
                "mean_outer_fraction": outer,
                "eligible": passed,
                "first_faces": pair.first_faces,
                "second_faces": pair.second_faces,
            }
        )
        if passed:
            eligible.append((-area, index, pair))
    if not eligible:
        raise ValueError("NO_ELIGIBLE_OUTER_REGION")
    diagnostics: dict[str, object] = {
        "selection": "area_ordered_geometry_only_direct_then_refined_initialization",
        "semantic_status": "GEOMETRIC_HEURISTIC_NOT_ARTIST_CONFIRMED",
        "plane_search": planes,
        "patches": records,
    }
    attempts: list[dict[str, object]] = []
    diagnostics["initialization_attempts"] = attempts
    ordered = sorted(eligible, key=lambda item: (item[0], item[1]))[: config.maximum_roi_attempts]
    for _, index, original in ordered:
        for mode in ("direct", "refined"):
            attempt: dict[str, object] = {
                "index": index,
                "mode": mode,
                "original_first_faces": original.first_faces,
                "original_second_faces": original.second_faces,
            }
            attempts.append(attempt)
            try:
                if time.monotonic() >= deadline:
                    raise TimeoutError("ROI_INITIALIZATION_BUDGET")
                pair = original
                if mode == "refined":
                    pair, history = refine_pair(mesh, original, deadline)
                    attempt["refinement"] = history
                attempt["first_faces"] = pair.first_faces
                attempt["second_faces"] = pair.second_faces
                prepared, _ = paired_problem(mesh, pair, deadline, planar=True)
                member = np.r_[
                    np.zeros(len(pair.first_faces), dtype=np.int64),
                    np.ones(len(pair.second_faces), dtype=np.int64),
                ]
                BudgetPairProblem(
                    prepared.initial,
                    prepared.orientations,
                    prepared.constraints,
                    member_ids=member,
                    contact_classes=prepared.contact_classes,
                    maximum_anisotropy=config.maximum_anisotropy,
                )
                attempt["status"] = "INITIALIZED"
                diagnostics.update(
                    {
                        "selected_index": index,
                        "initialization_mode": mode,
                        "plane_normal": pair.normal,
                        "plane_offset": pair.offset,
                        "original_first_faces": original.first_faces,
                        "original_second_faces": original.second_faces,
                        "selected_first_faces": pair.first_faces,
                        "selected_second_faces": pair.second_faces,
                        "retained_area_fraction": float(
                            mesh.areas[list(pair.first_faces + pair.second_faces)].sum()
                            / mesh.areas[list(original.first_faces + original.second_faces)].sum()
                        ),
                    }
                )
                if diagnostics_path is not None:
                    write_json(diagnostics_path, diagnostics)
                return pair, diagnostics
            except (ValueError, TimeoutError, np.linalg.LinAlgError) as error:
                attempt["status"] = "INIT_FAILED"
                attempt["reason"] = str(error)
                if diagnostics_path is not None:
                    write_json(diagnostics_path, diagnostics)
                if isinstance(error, TimeoutError):
                    raise
    raise ValueError("NO_GEOMETRIC_ROI_INITIALIZATION")


def _draw_packed(state: MeshState, path: Path, members: IntArray, size: int = 512) -> None:
    image = Image.new("RGB", (size, size), "white")
    drawing = ImageDraw.Draw(image)
    for fi, face in enumerate(state.uv_faces):
        points = state.uv[face] * (size - 1)
        points[:, 1] = size - 1 - points[:, 1]
        drawing.polygon(
            [tuple(map(float, p)) for p in points],
            outline=(40, 90, 140) if members[fi] == 0 else (180, 90, 40),
        )
    image.save(path)


def _geometry(
    state: MeshState,
    orientations: IntArray,
    members: IntArray,
    pair: RegionPair,
    shared: bool,
    resolution: int,
    config: BudgetExperimentConfig,
    contacts: tuple[int, ...] | None,
) -> dict[str, object]:
    return validate_pair_geometry(
        state,
        orientations,
        members,
        shared=shared,
        plane_normal=pair.normal,
        plane_offset=pair.offset,
        resolution=resolution,
        gap_pixels=config.gap_pixels,
        border_pixels=config.border_pixels,
        correspondence_fraction=config.correspondence_fraction,
        normal_angle_degrees=config.normal_angle_degrees,
        maximum_anisotropy=config.maximum_anisotropy,
        mean_shape_limit=config.mean_shape_limit,
        maximum_neighbor_density_ratio=config.maximum_neighbor_density_ratio,
        contact_classes=contacts,
    )


def run_budget_experiment(
    root: Path,
    config_path: Path,
    output: Path,
) -> dict[str, object]:
    config = load_budget_config(config_path)
    if output.exists():
        raise FileExistsError(f"Refusing to overwrite {output}")
    started = time.monotonic()
    deadline = started + config.seconds
    output.mkdir(parents=True, exist_ok=False)
    result: dict[str, object] = {
        "round_id": config.round_id,
        "model": config.model,
        "status": "RUNNING",
        "scope": "fixed_real_ROI_only",
        "full_v1_validated": False,
        "human_acceptance": "NOT_REVIEWED",
        "normal_status": "NOT_MEASURED",
        "orm_status": "NOT_MEASURED",
        "records": [],
    }
    write_json(output / "result.json", result)
    records: list[dict[str, object]] = []
    assets: list[Path] = []
    sources: list[Path] = []
    before: dict[str, str] = {}
    try:
        write_json(
            output / "protocol.json",
            {
                **asdict(config),
                "codec": "RGBA8_SRGB_RGB_LINEAR_ALPHA",
                "scope": "fixed_real_ROI_only",
                "formal_experiment": False,
                "required_material_channels": ["BaseColor", "Normal", "MetallicRoughness"],
                "implemented_material_channels": ["BaseColor"],
                "mip_reference": "fixed_high_surface_point_samples_not_matched_filter_footprints",
                "full_v1_validation_enabled": False,
                "optcuts_used": False,
                "packing": "one_common_scale_deterministic_shelf",
                "budget_account": "independent_new_solver_development",
            },
        )
        manifest = json.loads((root / "assets/v1/manifest.json").read_text(encoding="utf-8-sig"))
        model = next(item for item in manifest["models"] if item["id"] == config.model)
        assets = [root / "assets/v1" / item["path"] for item in model["files"]]
        expected = {str(root / "assets/v1" / item["path"]): item["sha256"] for item in model["files"]}
        sources = sorted((root / "src/reseam").glob("*.py")) + [config_path]
        before = {str(path): sha256(path) for path in assets + sources}
        snapshot = output / "source_snapshot"
        for path in sources:
            relative = (
                Path("src/reseam") / path.name if path.parent == root / "src/reseam" else Path("config.json")
            )
            destination = snapshot / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, destination)
        shutil.copyfile(root / "derivation/v1.md", snapshot / "derivation_v1.md")
        write_json(
            output / "source.json",
            {
                "sha256": before,
                "python": platform.python_version(),
                "platform": platform.platform(),
                "packages": {
                    name: importlib.metadata.version(name)
                    for name in ("numpy", "scipy", "Pillow", "shapely", "trimesh")
                },
                "derivation_sha256": sha256(root / "derivation/v1.md"),
                "legacy_pair_module_role": "initialization_adapter_only",
            },
        )
        if any(before[path] != digest for path, digest in expected.items()):
            raise ValueError("ASSET_HASH_MISMATCH")
        from .mesh import load_mesh

        mesh = load_mesh(root / "assets/v1" / config.model / "low.obj")
        pair, roi = select_wing_pair(mesh, config, min(deadline - 10, started + 30), output / "roi.json")
        write_json(output / "roi.json", roi)
        preparation, ids = paired_problem(mesh, pair, min(deadline - 10, time.monotonic() + 30), planar=True)
        members = np.r_[
            np.zeros(len(pair.first_faces), dtype=np.int64), np.ones(len(pair.second_faces), dtype=np.int64)
        ]
        write_json(
            output / "face_mapping.json",
            {
                "original_face_ids": ids,
                "original_face_count": len(mesh.faces),
                "roi_face_count": len(ids),
                "original_vertices_unchanged": True,
                "note": "ROI output retains exactly the selected original faces; not a full-model deliverable",
            },
        )
        save_state(output / "initial_state.json", preparation.initial)
        problem = BudgetPairProblem(
            preparation.initial,
            preparation.orientations,
            preparation.constraints,
            member_ids=members,
            contact_classes=preparation.contact_classes,
            coupling_weight=config.coupling_weight,
            maximum_anisotropy=config.maximum_anisotropy,
        )
        solved = solve_budget_pair(
            problem, min(config.solver_seconds, deadline - time.monotonic() - 10), config.solver_iterations
        )
        save_state(output / "solved_geometry.json", solved.state)
        solver_record = {
            "status": solved.status,
            "history": solved.history,
            "wall_seconds": solved.wall_seconds,
            "coupling_rms": solved.coupling_rms,
            "maximum_anisotropy": solved.maximum_anisotropy,
            "role": "geometry_proposal_not_material_acceptance",
        }
        write_json(output / "solver.json", solver_record)
        result["solver"] = solver_record
        if time.monotonic() >= deadline - 5:
            raise TimeoutError("BEFORE_REFERENCE_TIMEOUT")
        reference = load_reference(root / "assets/v1" / config.model / "high.glb")
        bounds = np.stack(
            (
                mesh.vertices[mesh.faces[list(ids)]].min(axis=(0, 1)),
                mesh.vertices[mesh.faces[list(ids)]].max(axis=(0, 1)),
            )
        )
        distance = config.max_projection_fraction * float(np.linalg.norm(np.ptp(mesh.vertices, axis=0)))
        reference = reference.restrict_bounds(bounds, distance)
        for resolution in sorted(config.resolutions):
            for track, surface, shared in (
                ("independent", preparation.initial, False),
                ("shared", solved.state, True),
            ):
                if time.monotonic() >= deadline - 5:
                    raise TimeoutError("EVALUATION_BUDGET")
                folder = output / f"{track}_{resolution}"
                folder.mkdir()
                record: dict[str, object] = {
                    "track": track,
                    "resolution": resolution,
                    "base_color_payload_bytes": texture_payload_bytes(resolution, resolution),
                    "full_v1_validated": False,
                }
                records.append(record)
                try:
                    packed = pack_pair(
                        surface, members, shared, resolution, config.border_pixels, config.gap_pixels
                    )
                    geometry = _geometry(
                        packed,
                        preparation.orientations,
                        members,
                        pair,
                        shared,
                        resolution,
                        config,
                        preparation.contact_classes,
                    )
                    record["geometry"] = geometry
                    save_state(folder / "state.json", packed)
                    write_obj(folder / "uv.obj", packed)
                    _draw_packed(packed, folder / "UV.png", members)
                    if not geometry["valid"]:
                        record["status"] = "GEOMETRY_REJECTED"
                    else:
                        atlas = bake_atlas(
                            packed,
                            reference,
                            resolution,
                            resolution,
                            folder / "texture",
                            member_ids=members,
                            max_projection_fraction=config.max_projection_fraction,
                            padding_pixels=config.padding_pixels,
                            deadline=deadline - 3,
                        )
                        material = evaluate_atlas(
                            packed,
                            reference,
                            atlas,
                            samples_per_face=config.samples_per_face,
                            mip_levels=config.mip_levels,
                            max_projection_fraction=config.max_projection_fraction,
                            color_mean_limit=config.color_mean_limit,
                            color_p95_limit=config.color_p95_limit,
                            color_max_limit=config.color_max_limit,
                            deadline=deadline - 3,
                        )
                        write_json(folder / "material.json", material)
                        record["material"] = material
                        record["status"] = (
                            "BASE_COLOR_DIAGNOSTIC_PASS"
                            if material["base_color_valid"]
                            else "BASE_COLOR_DIAGNOSTIC_REJECTED"
                        )
                        record["package_png_bytes"] = sum(
                            p.stat().st_size for p in (folder / "texture").glob("*.png")
                        )
                        base = folder / "texture/BaseColor_mip0.png"
                        if base.exists():
                            export_glb(packed, folder / "base_color_only.glb", base)
                except (ValueError, RuntimeError, TimeoutError, np.linalg.LinAlgError) as error:
                    record["status"] = "EVALUATION_FAILED"
                    record["reason"] = f"{type(error).__name__}: {error}"
                write_json(folder / "record.json", record)
                write_json(output / "matrix.json", records)
        result["status"] = "DEVELOPMENT_DIAGNOSTICS_COMPLETE"
    except (ValueError, RuntimeError, TimeoutError, OSError, np.linalg.LinAlgError) as error:
        result["status"] = "TIMEOUT_WITH_RECORDS" if isinstance(error, TimeoutError) else "DEVELOPMENT_FAILED"
        result["reason"] = f"{type(error).__name__}: {error}"
    finally:
        after: dict[str, str] = {}
        integrity_errors: dict[str, str] = {}
        for path in assets + sources:
            try:
                after[str(path)] = sha256(path)
            except OSError as error:
                integrity_errors[str(path)] = f"{type(error).__name__}: {error}"
        result["integrity_read_errors"] = integrity_errors
        result["source_unchanged"] = bool(before) and before == after and not integrity_errors
        result["input_unchanged"] = bool(assets) and all(
            str(path) in before and before[str(path)] == after.get(str(path)) for path in assets
        )
        if before != after or integrity_errors:
            result["status"] = "SOURCE_OR_INPUT_CHANGED"
        result["records"] = records
        geometries = [row.get("geometry") for row in records]
        result["geometry_valid_rows"] = sum(
            isinstance(geometry, dict) and geometry.get("valid") is True for geometry in geometries
        )
        result["base_color_diagnostic_pass_rows"] = sum(
            row.get("status") == "BASE_COLOR_DIAGNOSTIC_PASS" for row in records
        )
        result["accepted_full_material_reuse_groups"] = 0
        result["wall_seconds"] = time.monotonic() - started
        result["budget_overrun"] = float(result["wall_seconds"]) > config.seconds
        if result["budget_overrun"]:
            result["status"] = "BUDGET_EXHAUSTED_WITH_RECORDS"
        write_json(
            output / "budget_account.json",
            {
                "account": "independent_new_solver_development",
                "limit_seconds": config.seconds,
                "charged_seconds": result["wall_seconds"],
                "includes": ["selection", "initialization", "solve", "bake", "evaluation", "export"],
                "historical_accounts_modified": False,
            },
        )
        write_json(output / "result.json", result)
    return result
