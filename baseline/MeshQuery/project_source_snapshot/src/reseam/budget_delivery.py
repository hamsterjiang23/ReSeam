"""Validated delivery snapshots and the frozen lexicographic order of §7.1.

The caller runs the expensive validators before ``offer``. Development scores
are deliberately absent from this interface. Rejected, incomplete, or late
results cannot remove a previously validated delivery.
"""

import copy
import math
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from types import MappingProxyType

import numpy as np

from .budget_material import MaterialAtlas, MaterialThresholds
from .budget_state import BudgetState
from .validation import seam_metrics

DeliveryRank = tuple[int, float, float, float, str]
_CHANNELS = ("color", "normal", "orm", "render")
_AGGREGATES = ("mean", "p95", "max")
_GEOMETRY_DIRECTIONS = {
    "mean_shape_distortion": "upper",
    "shape_p95": "upper",
    "shape_cvar95": "upper",
    "maximum_anisotropy": "upper",
    "maximum_neighbor_density_ratio": "upper",
    "maximum_neighbor_log_density_difference": "upper",
    "minimum_gap_pixels": "lower",
    "minimum_border_pixels": "lower",
}


@dataclass(frozen=True)
class DeliveryQualityWeights:
    """Positive, predeclared channel and evaluation-source weights."""

    color: float = 1.0
    normal: float = 1.0
    orm: float = 1.0
    render: float = 1.0
    probes: float = 1.0
    raster: float = 1.0
    independent_render: float = 1.0

    def __post_init__(self) -> None:
        _positive_values(vars(self))


@dataclass(frozen=True)
class DeliverySeamWeights:
    """Cost = seam_length * normalized original-edge length + charts * count."""

    seam_length: float = 1.0
    charts: float = 1.0

    def __post_init__(self) -> None:
        _positive_values(vars(self))


@dataclass(frozen=True)
class RankingMetric:
    name: str
    value: float
    threshold: float
    direction: str
    normalized_violation: float


@dataclass(frozen=True)
class DeliveredEntry:
    """Complete snapshot; public mutable payload access always returns a copy."""

    candidate_id: str
    payload_bytes: int
    completed_at: float
    rank: DeliveryRank
    ranking_metrics: tuple[RankingMetric, ...]
    quality_components: tuple[tuple[str, float, float], ...]
    seam_components: tuple[tuple[str, float], ...]
    excluded_metrics: tuple[str, ...]
    _state: BudgetState = field(repr=False, compare=False)
    _atlas: MaterialAtlas = field(repr=False, compare=False)
    _geometry: dict[str, object] = field(repr=False, compare=False)
    _material: dict[str, object] = field(repr=False, compare=False)
    _render: dict[str, object] = field(repr=False, compare=False)
    _provenance: dict[str, object] = field(repr=False, compare=False)

    @property
    def state(self) -> BudgetState:
        return copy.deepcopy(self._state)

    @property
    def atlas(self) -> MaterialAtlas:
        return copy.deepcopy(self._atlas)

    @property
    def geometry(self) -> dict[str, object]:
        return copy.deepcopy(self._geometry)

    @property
    def material(self) -> dict[str, object]:
        return copy.deepcopy(self._material)

    @property
    def render(self) -> dict[str, object]:
        return copy.deepcopy(self._render)

    @property
    def provenance(self) -> dict[str, object]:
        return copy.deepcopy(self._provenance)


@dataclass(frozen=True)
class DeliveryOffer:
    accepted: bool
    reason: str
    rank: DeliveryRank | None = None
    completed_at: float | None = None


def _positive_values(values: Mapping[str, float]) -> None:
    if not all(math.isfinite(value) and value > 0 for value in values.values()):
        raise ValueError("Delivery weights and normalized thresholds must be finite and positive")


def _number(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value):
        raise ValueError(f"MISSING_OR_NONFINITE_METRIC:{name}")
    return float(value)


def _mapping(value: object, name: str) -> dict[str, object]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise ValueError(f"MISSING_REPORT:{name}")
    return value


def _records(value: object, name: str) -> list[dict[str, object]]:
    if not isinstance(value, list) or not value:
        raise ValueError(f"MISSING_RECORDS:{name}")
    return [_mapping(item, name) for item in value]


def _index(value: object, name: str) -> int:
    if type(value) is not int or value < 0:
        raise ValueError(f"INVALID_INDEX:{name}")
    return value


def _mips(report: Mapping[str, object], available: int, name: str) -> tuple[int, ...]:
    values = report.get("mip_levels")
    if not isinstance(values, list) or not values:
        raise ValueError(f"MISSING_MIP_PROTOCOL:{name}")
    result = tuple(_index(value, name) for value in values)
    if len(set(result)) != len(result) or max(result) >= available:
        raise ValueError(f"INVALID_MIP_PROTOCOL:{name}")
    return result


@dataclass(frozen=True, init=False)
class DeliveredArchive:
    """Keep all completed valid entries, sorted by the exact delivery contract.

    ``geometry_thresholds`` uses report names, with supported names listed in
    ``_GEOMETRY_DIRECTIONS``. Only configured scalar geometry inequalities enter
    the signed margin. Structural/exact-zero gates remain Boolean requirements.
    A ``None`` minimum gap is inapplicable when no separate packing groups exist.
    ``important_faces`` is frozen here, rather than inferred from candidate flags.
    Independent render thresholds default to the same frozen material limits.
    """

    deadline: float
    material_thresholds: MaterialThresholds
    geometry_thresholds: Mapping[str, float]
    render_thresholds: Mapping[str, float]
    quality_weights: DeliveryQualityWeights
    seam_weights: DeliverySeamWeights
    important_faces: frozenset[int]
    _entries: dict[str, DeliveredEntry] = field(repr=False, compare=False)

    def __init__(
        self, *, deadline: float, material_thresholds: MaterialThresholds | None = None,
        geometry_thresholds: Mapping[str, float] | None = None,
        render_thresholds: Mapping[str, float] | None = None,
        quality_weights: DeliveryQualityWeights | None = None,
        seam_weights: DeliverySeamWeights | None = None,
        important_faces: tuple[int, ...] = (),
    ) -> None:
        if not math.isfinite(deadline):
            raise ValueError("Finite absolute monotonic delivery deadline required")
        limits = material_thresholds or MaterialThresholds()
        geometry_bounds = dict(geometry_thresholds or {})
        if set(geometry_bounds) - _GEOMETRY_DIRECTIONS.keys():
            raise ValueError("Unknown normalized geometry metric")
        render_bounds = dict(render_thresholds or {
            aggregate: float(getattr(limits, f"render_{aggregate}"))
            for aggregate in _AGGREGATES
        })
        if set(render_bounds) != set(_AGGREGATES):
            raise ValueError("Independent render mean/p95/max thresholds required")
        _positive_values(geometry_bounds)
        _positive_values(render_bounds)
        if any(type(face) is not int or face < 0 for face in important_faces):
            raise ValueError("Invalid predeclared important face")
        object.__setattr__(self, "deadline", deadline)
        object.__setattr__(self, "material_thresholds", limits)
        object.__setattr__(self, "geometry_thresholds", MappingProxyType(geometry_bounds))
        object.__setattr__(self, "render_thresholds", MappingProxyType(render_bounds))
        object.__setattr__(self, "important_faces", frozenset(important_faces))
        object.__setattr__(self, "quality_weights", quality_weights or DeliveryQualityWeights())
        object.__setattr__(self, "seam_weights", seam_weights or DeliverySeamWeights())
        object.__setattr__(self, "_entries", {})

    @property
    def protocol(self) -> dict[str, object]:
        """Serializable declaration suitable for saving before the search starts."""
        return {
            "order": ["payload_bytes", "signed_max_normalized_violation", "quality_cost",
                      "seam_fragment_cost", "candidate_id"],
            "material_thresholds": dict(vars(self.material_thresholds)),
            "geometry_thresholds": dict(self.geometry_thresholds),
            "render_thresholds": dict(self.render_thresholds),
            "quality_weights": dict(vars(self.quality_weights)),
            "quality_cost": "weighted mean of normalized area-weighted channel means from probes and raster; "
                            "independent render contributes normalized pixel-weighted mean",
            "seam_weights": dict(vars(self.seam_weights)),
            "seam_fragment_cost": "seam_length * seam_length_normalized + charts * chart_count",
            "important_faces": sorted(self.important_faces),
            "upper_normalization": "value / positive_threshold - 1",
            "lower_normalization": "1 - value / positive_threshold",
            "strict_zero_constraints": "final validator Boolean gates; never divide by zero",
            "deadline": self.deadline,
        }

    @property
    def best(self) -> DeliveredEntry | None:
        return min(self._entries.values(), key=lambda entry: entry.rank, default=None)

    @property
    def entries(self) -> tuple[DeliveredEntry, ...]:
        return tuple(sorted(self._entries.values(), key=lambda entry: entry.rank))

    def offer(
        self, *, candidate_id: str, state: BudgetState, atlas: MaterialAtlas,
        geometry: Mapping[str, object], material: Mapping[str, object], render: Mapping[str, object],
        payload_bytes: int, completed_at: float, provenance: Mapping[str, object],
        completion_clock: Callable[[], float] | None = None,
    ) -> DeliveryOffer:
        """Prepare evidence fully, then check publication time before inserting.

        ``completed_at`` is the persisted-evidence timestamp. With a live clock,
        the final timestamp includes ranking and deep snapshot construction.
        Without one, callers own the supplied complete timestamp, which keeps
        deterministic standalone archive tests independent of wall-clock time.
        """
        if not math.isfinite(completed_at) or completed_at > self.deadline:
            return DeliveryOffer(False, "COMPLETED_AFTER_DEADLINE")
        if not candidate_id or candidate_id in self._entries:
            return DeliveryOffer(False, "EMPTY_OR_DUPLICATE_CANDIDATE_ID")
        try:
            entry = self._entry(candidate_id, state, atlas, geometry, material, render,
                                payload_bytes, completed_at, provenance)
        except (ValueError, TypeError, KeyError, IndexError) as error:
            return DeliveryOffer(False, str(error))
        if completion_clock is not None:
            published_at = completion_clock()
            if (isinstance(published_at, bool) or not isinstance(published_at, (int, float))
                    or not math.isfinite(published_at)):
                return DeliveryOffer(False, "INVALID_COMPLETION_CLOCK")
            if published_at < completed_at:
                return DeliveryOffer(False, "COMPLETION_CLOCK_MOVED_BACKWARDS", completed_at=published_at)
            if published_at > self.deadline:
                return DeliveryOffer(False, "COMPLETED_AFTER_DEADLINE", completed_at=published_at)
            completed_at = float(published_at)
            # _entry already owns the expensive deep copies. Updating scalar
            # publication metadata must not duplicate those snapshots again.
            entry._provenance["completed_at"] = completed_at
            started = entry._provenance.get("started_at")
            if started is not None:
                if (isinstance(started, bool) or not isinstance(started, (int, float))
                        or not math.isfinite(started) or completed_at < started):
                    return DeliveryOffer(False, "INVALID_COMPLETION_ELAPSED_TIME", completed_at=completed_at)
                entry._provenance["completed_seconds_from_start"] = completed_at - started
            entry = replace(entry, completed_at=completed_at)
        self._entries[candidate_id] = entry
        return DeliveryOffer(True, "VALIDATED_DELIVERY_RETAINED", entry.rank, completed_at)

    def _entry(
        self, candidate_id: str, state: BudgetState, atlas: MaterialAtlas,
        geometry: Mapping[str, object], material: Mapping[str, object], render: Mapping[str, object],
        payload_bytes: int, completed_at: float, provenance: Mapping[str, object],
    ) -> DeliveredEntry:
        for name, report in (("geometry", geometry), ("material", material), ("render", render)):
            if report.get("valid") is not True:
                raise ValueError(f"FINAL_GATE_NOT_PASSED:{name}")
        for flag in ("source_reference_valid", "bake_reference_valid", "support_state_matches",
                     "raster_checked", "raster_valid"):
            if material.get(flag) is not True:
                raise ValueError(f"FINAL_MATERIAL_FLAG_NOT_PASSED:{flag}")
        for name, report in (("material", material), ("render", render)):
            if _number(report.get("reference_coverage"), name + ".reference_coverage") != 1:
                raise ValueError(f"INCOMPLETE_REFERENCE_COVERAGE:{name}")
        support = material.get("support_state_sha256")
        if not isinstance(support, str) or not support or support != atlas.metadata.get("support_state_sha256"):
            raise ValueError("MISSING_OR_MISMATCHED_SUPPORT_PROVENANCE")
        if not provenance:
            raise ValueError("MISSING_OUTPUT_PROVENANCE")
        if type(payload_bytes) is not int or payload_bytes <= 0 or payload_bytes != atlas.payload_bytes:
            raise ValueError("PAYLOAD_BYTES_MISMATCH")
        if material.get("payload_bytes") != payload_bytes:
            raise ValueError("MATERIAL_PAYLOAD_BYTES_MISMATCH")
        self._check_atlas(atlas)
        mips = _mips(material, len(atlas.base_color), "material")
        if _mips(render, len(atlas.base_color), "render") != mips:
            raise ValueError("RENDER_MATERIAL_MIP_PROTOCOL_MISMATCH")
        face_count = len(state.surface.mesh.faces)
        if not self.important_faces.issubset(range(face_count)):
            raise ValueError("IMPORTANT_FACE_OUT_OF_RANGE")
        metrics: list[RankingMetric] = []
        components: list[tuple[str, float, float]] = []
        for source, rows, summaries in (
            ("probes", material.get("faces"), material.get("channels")),
            ("raster", material.get("raster_faces"), material.get("raster_channels")),
        ):
            records = _records(rows, source)
            self._material_metrics(metrics, records, source, face_count, mips)
            summary = _mapping(summaries, source + ".channels")
            for channel in _CHANNELS:
                measure = _mapping(summary.get(channel), source + "." + channel)
                value = _number(measure.get("area_weighted_mean"), source + "." + channel)
                if value < 0:
                    raise ValueError("NEGATIVE_ERROR_METRIC")
                bound = self._material_bound(channel, "mean")
                weight = float(getattr(self.quality_weights, source) * getattr(self.quality_weights, channel))
                components.append((f"{source}.{channel}.area_weighted_mean", value / bound, weight))
        self._render_metrics(metrics, components, render, mips)
        excluded = self._geometry_metrics(metrics, geometry, state)
        signed_max = max(item.normalized_violation for item in metrics)
        if signed_max > 0:
            raise ValueError("ACTUAL_NORMALIZED_CONSTRAINT_VIOLATION")
        quality = sum(value * weight for _, value, weight in components) / sum(
            weight for _, _, weight in components)
        seams = seam_metrics(state.surface)
        length, charts = float(seams["seam_length_normalized"]), float(seams["charts"])
        overhead = self.seam_weights.seam_length * length + self.seam_weights.charts * charts
        rank = (payload_bytes, signed_max, quality, overhead, candidate_id)
        return DeliveredEntry(
            candidate_id, payload_bytes, completed_at, rank, tuple(metrics), tuple(components),
            (("seam_length_normalized", length), ("charts", charts)), excluded,
            copy.deepcopy(state), copy.deepcopy(atlas), copy.deepcopy(dict(geometry)),
            copy.deepcopy(dict(material)), copy.deepcopy(dict(render)), copy.deepcopy(dict(provenance)),
        )

    @staticmethod
    def _check_atlas(atlas: MaterialAtlas) -> None:
        chains = (atlas.base_color, atlas.normal, atlas.orm)
        if not chains[0] or any(len(chain) != len(chains[0]) for chain in chains):
            raise ValueError("INCOMPLETE_MATERIAL_MIP_CHAINS")
        previous: tuple[int, int] | None = None
        for level in range(len(chains[0])):
            shape = chains[0][level].shape
            if len(shape) != 3 or shape[2] != 4 or min(shape[:2]) <= 0:
                raise ValueError("INVALID_ENCODED_MIP_SHAPE")
            if any(image[level].shape != shape or image[level].dtype != np.uint8 for image in chains):
                raise ValueError("ENCODED_CHANNEL_MIP_MISMATCH")
            if previous is not None and shape[:2] != tuple(max(1, edge // 2) for edge in previous):
                raise ValueError("INCOMPLETE_MATERIAL_MIP_CHAINS")
            previous = (shape[0], shape[1])
        if previous != (1, 1):
            raise ValueError("INCOMPLETE_MATERIAL_MIP_CHAINS")
        if atlas.coverage.shape != chains[0][0].shape[:2] or not atlas.coverage.any():
            raise ValueError("MISSING_RASTER_SUPPORT")

    def _material_bound(self, channel: str, aggregate: str) -> float:
        suffix = "_degrees" if channel == "normal" else ""
        return float(getattr(self.material_thresholds, f"{channel}_{aggregate}{suffix}"))

    @staticmethod
    def _append_metric(
        metrics: list[RankingMetric], name: str, value: object, bound: float, direction: str = "upper",
    ) -> None:
        actual = _number(value, name)
        if actual < 0:
            raise ValueError(f"NEGATIVE_METRIC:{name}")
        violation = actual / bound - 1 if direction == "upper" else 1 - actual / bound
        metrics.append(RankingMetric(name, actual, bound, direction, violation))

    def _material_metrics(
        self, metrics: list[RankingMetric], records: list[dict[str, object]], source: str,
        face_count: int, mips: tuple[int, ...],
    ) -> None:
        seen: set[tuple[int, int]] = set()
        for record in records:
            face, mip = _index(record.get("face"), source), _index(record.get("mip"), source)
            if (face, mip) in seen or face >= face_count or mip not in mips:
                raise ValueError(f"INVALID_OR_DUPLICATE_FACE_MIP:{source}")
            seen.add((face, mip))
            if record.get("passed") is not True:
                raise ValueError(f"FAILED_FACE_MIP:{source}")
            if source == "raster" and record.get("samples") == 0:
                if record.get("status") != "NO_TEXEL_CENTER_FIXED_PROBES_STILL_REQUIRED":
                    raise ValueError("UNEXPLAINED_EMPTY_RASTER_FACE")
                continue
            if record.get("reference_valid") is not True or record.get("valid_sample_fraction") != 1:
                raise ValueError(f"INVALID_FACE_REFERENCE_OR_RECONSTRUCTION:{source}")
            important = face in self.important_faces
            if source == "probes" and record.get("important") is not important:
                raise ValueError("IMPORTANT_FACE_PROTOCOL_MISMATCH")
            multiplier = self.material_thresholds.important_multiplier if important else 1.0
            for channel in _CHANNELS:
                channel_metrics = _mapping(record.get(channel), source + "." + channel)
                for aggregate in _AGGREGATES:
                    self._append_metric(metrics, f"{source}.face{face}.mip{mip}.{channel}.{aggregate}",
                                        channel_metrics.get(aggregate),
                                        self._material_bound(channel, aggregate) * multiplier)
        if seen != {(face, mip) for face in range(face_count) for mip in mips}:
            raise ValueError(f"INCOMPLETE_FACE_MIP_COVERAGE:{source}")

    def _render_metrics(
        self, metrics: list[RankingMetric], components: list[tuple[str, float, float]],
        render: Mapping[str, object], mips: tuple[int, ...],
    ) -> None:
        reported = _mapping(render.get("thresholds"), "render.thresholds")
        if any(reported.get(name) != bound for name, bound in self.render_thresholds.items()):
            raise ValueError("RENDER_THRESHOLD_PROTOCOL_MISMATCH")
        views, lights = _index(render.get("views"), "render.views"), _index(render.get("lights"), "render.lights")
        seen: set[tuple[int, int, int]] = set()
        weighted_mean, pixel_count = 0.0, 0
        for record in _records(render.get("per_view_light_mip"), "render"):
            view = _index(record.get("view"), "render.view")
            light = _index(record.get("light"), "render.light")
            mip = _index(record.get("mip"), "render.mip")
            identity = (view, light, mip)
            if identity in seen or view >= views or light >= lights or mip not in mips:
                raise ValueError("INVALID_OR_DUPLICATE_RENDER_RECORD")
            seen.add(identity)
            pixels = _index(record.get("pixels"), "render.pixels")
            if (record.get("passed") is not True or pixels == 0
                    or record.get("reference_valid_fraction") != 1 or record.get("actual_valid_fraction") != 1):
                raise ValueError("INCOMPLETE_RENDER_GATE")
            for aggregate in _AGGREGATES:
                self._append_metric(metrics, f"render.view{view}.light{light}.mip{mip}.{aggregate}",
                                    record.get(aggregate), self.render_thresholds[aggregate])
            weighted_mean += _number(record.get("mean"), "render.mean") * pixels
            pixel_count += pixels
        if seen != {(view, light, mip) for view in range(views) for light in range(lights) for mip in mips}:
            raise ValueError("INCOMPLETE_RENDER_RECORD_COVERAGE")
        components.append(("independent_render.pixel_weighted_mean",
                           weighted_mean / pixel_count / self.render_thresholds["mean"],
                           self.quality_weights.independent_render * self.quality_weights.render))

    def _geometry_metrics(
        self, metrics: list[RankingMetric], geometry: Mapping[str, object], state: BudgetState,
    ) -> tuple[str, ...]:
        excluded: list[str] = []
        for name, bound in self.geometry_thresholds.items():
            value = geometry.get(name)
            if name == "minimum_gap_pixels" and value is None:
                if "minimum_gap_pixels" not in geometry:
                    raise ValueError("MISSING_GEOMETRY_GAP_METRIC")
                excluded.append("geometry.minimum_gap_pixels: no separate packing groups")
                continue
            if name == "shape_p95" and value is None:
                array = geometry.get("face_shape_distortion")
                if not isinstance(array, list) or len(array) != len(state.surface.mesh.faces):
                    raise ValueError("MISSING_GEOMETRY_SHAPE_TAIL")
                values = np.asarray([_number(item, "face_shape_distortion") for item in array])
                order = np.argsort(values)
                weights = state.surface.mesh.areas[order]
                index = min(int(np.searchsorted(np.cumsum(weights), 0.95 * weights.sum())), len(values) - 1)
                value = float(values[order[index]])
            self._append_metric(metrics, "geometry." + name, value, bound, _GEOMETRY_DIRECTIONS[name])
        excluded.extend("geometry." + name + ": no predeclared scalar threshold"
                        for name in _GEOMETRY_DIRECTIONS if name not in self.geometry_thresholds)
        return tuple(excluded)
