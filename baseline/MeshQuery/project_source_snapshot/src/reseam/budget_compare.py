"""Frozen, independently initialized budget matrix and traceable comparison report."""

import importlib
import importlib.metadata
import importlib.util
import json
import math
import os
import platform
import re
import shutil
import sys
import time
import traceback
from collections import Counter
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Protocol, cast

from .mesh import sha256
from .pipeline import write_json


class TargetSolver(Protocol):
    def __call__(
        self,
        inputs: object,
        method: str,
        seed: int,
        resolution: int,
        output: Path,
        /,
        *,
        seconds: float,
        options: object,
    ) -> dict[str, object]: ...


@dataclass(frozen=True)
class SolverBindings:
    options: Callable[[dict[str, object]], object]
    prepare: Callable[[Path, str, object, float], object]
    solve: TargetSolver


def _solver_bindings() -> SolverBindings:
    """Lazy boundary also permits small deterministic runner tests without assets."""
    module = importlib.import_module(".budget_solver", package=__package__)
    constructor = cast(Callable[..., object], module.SolverOptions)

    def options(raw: dict[str, object]) -> object:
        return constructor(**raw)

    return SolverBindings(
        options,
        cast(Callable[[Path, str, object, float], object], module.prepare_inputs),
        cast(TargetSolver, module.solve_budget_target),
    )


def _mapping(value: object, label: str) -> dict[str, object]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise ValueError(f"{label} must be an object with string keys")
    return cast(dict[str, object], value)


def _array(value: object, label: str) -> list[object]:
    if not isinstance(value, list):
        raise TypeError(f"{label} must be an array")
    return cast(list[object], value)


@dataclass(frozen=True)
class ComparisonConfig:
    round_id: str
    models: tuple[str, ...] = ("energy_blade", "dragon")
    methods: tuple[str, ...] = ("independent", "old_pair", "geometry_only", "joint")
    seeds: tuple[int, ...] = (17, 29, 43)
    resolutions: tuple[int, ...] = (64, 128, 256)
    seconds_per_target: float = 60.0
    setup_seconds_per_model: float = 120.0
    options: dict[str, object] = field(default_factory=dict)
    formal: bool = False

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[a-zA-Z0-9_-]+", self.round_id):
            raise ValueError("Portable nonempty round_id required")
        for label, values, allowed in (
            ("models", self.models, {"energy_blade", "dragon"}),
            ("methods", self.methods, {"independent", "old_pair", "geometry_only", "joint", "optcuts",
                                      "bake_only", "texture_only"}),
            ("seeds", self.seeds, {17, 29, 43}),
        ):
            if not values or len(values) != len(set(values)) or not set(values) <= allowed:
                raise ValueError(f"Unsupported, duplicate, or empty {label}")
        if not self.resolutions or len(self.resolutions) != len(set(self.resolutions)):
            raise ValueError("Distinct nonempty resolution budgets required")
        if any(type(value) is not int or value < 16 or value > 2048 for value in self.resolutions):
            raise ValueError("Integer resolutions in [16, 2048] required")
        if any(type(value) is not int for value in self.seeds):
            raise ValueError("Integer seeds required")
        for value in (self.seconds_per_target, self.setup_seconds_per_model):
            if isinstance(value, bool) or not math.isfinite(value) or value <= 0:
                raise ValueError("Positive finite independent budgets required")
        _mapping(self.options, "options")
        if type(self.formal) is not bool:
            raise ValueError("formal must be boolean")
        if self.formal and not (
            set(self.models) == {"energy_blade", "dragon"}
            and {"independent", "old_pair", "geometry_only", "joint"} <= set(self.methods)
            and set(self.seeds) == {17, 29, 43}
            and len(self.resolutions) >= 2
        ):
            raise ValueError("Formal comparison requires both assets, four methods, three seeds, two budgets")


def load_comparison_config(path: Path) -> ComparisonConfig:
    raw = _mapping(json.loads(path.read_text(encoding="utf-8-sig")), "config")
    known = set(ComparisonConfig.__dataclass_fields__)
    if set(raw) - known:
        raise ValueError(f"Unknown comparison settings: {sorted(set(raw) - known)}")
    for name in ("models", "methods", "seeds", "resolutions"):
        if name in raw:
            raw[name] = tuple(_array(raw[name], name))
    return ComparisonConfig(**raw)  # type: ignore[arg-type]


def _read_hashes(paths: list[Path]) -> tuple[dict[str, str], dict[str, str]]:
    values: dict[str, str] = {}
    errors: dict[str, str] = {}
    for path in paths:
        try:
            values[str(path)] = sha256(path)
        except OSError as error:
            errors[str(path)] = f"{type(error).__name__}: {error}"
    return values, errors


def _source_paths(root: Path, config: Path) -> list[Path]:
    paths = sorted((root / "src/reseam").rglob("*.py"))
    paths += [config, root / "derivation/v1.md", root / "assets/v1/manifest.json"]
    paths += [path for path in (root / "pyproject.toml", root / "uv.lock") if path.is_file()]
    build_script = root / "scripts/build_budget_mikktspace.py"
    if build_script.is_file():
        paths.append(build_script)
    plot_script = root / "scripts/plot_budget_comparison.py"
    if plot_script.is_file():
        paths.append(plot_script)
    gallery_script = root / "scripts/build_budget_texture_gallery.py"
    if gallery_script.is_file():
        paths.append(gallery_script)
    native = root / "artifacts/runtime/mikktspace"
    if native.is_dir():
        paths += [
            path for path in native.iterdir()
            if path.is_file() and (
                path.suffix.lower() in {".c", ".h", ".dll", ".so", ".dylib"}
                or path.name == "build_manifest.json"
            )
        ]
    return sorted(set(paths))


def _manifest_assets(root: Path, models: tuple[str, ...]) -> tuple[list[Path], dict[str, str]]:
    directory = (root / "assets/v1").resolve()
    manifest = _mapping(json.loads((directory / "manifest.json").read_text(encoding="utf-8-sig")), "manifest")
    records = [_mapping(item, "model") for item in _array(manifest.get("models"), "manifest models")]
    assets: list[Path] = []
    expected: dict[str, str] = {}
    for model in models:
        matches = [record for record in records if record.get("id") == model]
        if len(matches) != 1:
            raise ValueError(f"Expected one fixed manifest entry for {model}")
        for value in _array(matches[0].get("files"), f"{model} files"):
            record = _mapping(value, "manifest file")
            relative, digest = record.get("path"), record.get("sha256")
            if (
                not isinstance(relative, str)
                or not isinstance(digest, str)
                or not re.fullmatch(r"[0-9a-f]{64}", digest)
            ):
                raise ValueError("Invalid fixed input path/hash")
            path = (directory / relative).resolve()
            if not path.is_relative_to(directory):
                raise ValueError("Manifest path escapes fixed asset directory")
            assets.append(path)
            expected[str(path)] = digest
    return sorted(set(assets)), expected


def _snapshot(root: Path, config: Path, sources: list[Path], output: Path) -> None:
    for path in sources:
        relative = Path("config.json") if path == config else path.relative_to(root)
        target = output / "source_snapshot" / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)


def _small_report(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        return {"valid": False, "status": "MISSING_REPORT"}
    raw = cast(dict[str, object], value)
    excluded = {"faces", "per_face", "samples", "per_sample", "reference_samples", "views", "pixels"}
    result: dict[str, object] = {}
    for key, item in raw.items():
        if key in excluded and isinstance(item, list):
            result[key + "_count"] = len(item)
        elif isinstance(item, dict):
            result[key] = _small_report(item)
        else:
            result[key] = item
    return result


def _gate_valid(value: object) -> bool:
    return isinstance(value, dict) and value.get("valid") is True


def _material_valid(value: object) -> bool:
    return bool(
        _gate_valid(value)
        and isinstance(value, dict)
        and value.get("raster_checked") is True
        and value.get("raster_valid") is True
    )


def _metadata_value(value: object) -> object:
    """Persist descriptors, never serialize the prepared mesh/sample array cache."""
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    if isinstance(value, dict):
        return {str(key): _metadata_value(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_metadata_value(item) for item in value]
    return {"omitted_type": type(value).__name__}


def _prepared_metadata(prepared: object) -> dict[str, object]:
    metadata = {
        name: _metadata_value(getattr(prepared, name, None))
        for name in ("initialization", "region_diagnostics", "model", "setup_seconds")
    }
    reference = getattr(prepared, "reference", None)
    metadata["reference"] = _metadata_value(getattr(reference, "metadata", None))
    return metadata


def _payload(value: object) -> int | None:
    return value if type(value) is int and value > 0 else None


def _timely_delivery_proof(raw: dict[str, object], folder: Path, limit: float) -> bool:
    """Retain a completed delivery, never a new result created during cleanup."""
    completed = raw.get("accepted_completed_seconds")
    if (raw.get("accepted_before_target_deadline") is not True
            or isinstance(completed, bool) or not isinstance(completed, (float, int))
            or not math.isfinite(completed) or not 0 <= completed <= limit):
        return False
    try:
        archive = _mapping(json.loads((folder / "accepted_archive.json").read_text(encoding="utf-8")), "archive")
        if (archive.get("completed_seconds_from_start") != completed
                or archive.get("candidate_id") != raw.get("accepted_candidate_id")
                or archive.get("state_fingerprint") != raw.get("accepted_candidate_fingerprint")
                or archive.get("payload_bytes") != raw.get("payload_bytes")):
            return False
        provenance = _mapping(archive.get("provenance"), "provenance")
        directory = provenance.get("output_directory")
        if not isinstance(directory, str):
            return False
        snapshot = Path(directory).resolve()
        if not snapshot.is_relative_to(folder.resolve()):
            return False
        manifest = _array(provenance.get("files"), "delivery.files")
        seen: set[str] = set()
        for value in manifest:
            item = _mapping(value, "delivery.file")
            relative = item.get("path")
            if not isinstance(relative, str) or relative in seen:
                return False
            path = (snapshot / relative).resolve()
            if (not path.is_relative_to(snapshot) or not path.is_file()
                    or path.stat().st_size != item.get("bytes") or sha256(path) != item.get("sha256")):
                return False
            seen.add(relative)
        return ("state.json" in seen and "export.json" in seen
                and "material_lod0_preview.glb" in seen
                and {f"textures/{channel}_mip0.png" for channel in ("BaseColor", "Normal", "ORM")} <= seen)
    except (OSError, ValueError, TypeError):
        return False


def _planned_rows(config: ComparisonConfig) -> list[dict[str, object]]:
    return [
        {
            "model": model,
            "method": method,
            "seed": seed,
            "resolution": resolution,
            "target_id": f"{model}_{method}_s{seed}_{resolution}",
            "record_path": f"{model}/seed{seed}/{method}/{resolution}/record.json",
            "status": "NOT_RUN",
            "valid": False,
            "payload_bytes": None,
            "wall_seconds": 0.0,
            "setup_amortized_seconds": 0.0,
            "source_unchanged": None,
            "input_unchanged": None,
            "geometry": {"valid": False, "status": "NOT_RUN"},
            "material": {"valid": False, "status": "NOT_RUN"},
            "render": {"valid": False, "status": "NOT_RUN"},
            "final_groups": [],
            "final_group_count": 0,
        }
        for model in config.models
        for seed in config.seeds
        for resolution in sorted(config.resolutions)
        for method in config.methods
    ]


def _minimum_results(rows: list[dict[str, object]], config: ComparisonConfig) -> list[dict[str, object]]:
    results: list[dict[str, object]] = []
    for model in config.models:
        for seed in config.seeds:
            for method in config.methods:
                selected = [
                    r for r in rows if r["model"] == model and r["seed"] == seed and r["method"] == method
                ]
                accepted = [
                    r for r in selected if r.get("valid") is True and _payload(r.get("payload_bytes"))
                ]
                best = min(accepted, key=lambda r: cast(int, r["payload_bytes"])) if accepted else None
                results.append(
                    {
                        "model": model,
                        "seed": seed,
                        "method": method,
                        "status": "MINIMUM_AMONG_VALIDATED_CANDIDATES" if best else "NO_VALIDATED_RESULT",
                        "minimum_payload_bytes": None if best is None else best["payload_bytes"],
                        "resolution": None if best is None else best["resolution"],
                        "record_path": None if best is None else best["record_path"],
                        "validated_budgets": len(accepted),
                        "planned_budgets": len(selected),
                        "attempted_budgets": sum(
                            r["status"] != "NOT_RUN" and not str(r["status"]).startswith("NOT_RUN_")
                            for r in selected
                        ),
                    }
                )
    return results


def _comparisons(minima: list[dict[str, object]]) -> list[dict[str, object]]:
    comparisons: list[dict[str, object]] = []
    for entry in minima:
        if entry["method"] == "independent":
            continue
        baseline = next(
            (
                r
                for r in minima
                if r["model"] == entry["model"]
                and r["seed"] == entry["seed"]
                and r["method"] == "independent"
            ),
            None,
        )
        base_bytes = None if baseline is None else _payload(baseline["minimum_payload_bytes"])
        method_bytes = _payload(entry["minimum_payload_bytes"])
        comparisons.append(
            {
                "model": entry["model"],
                "seed": entry["seed"],
                "method": entry["method"],
                "baseline": "independent",
                "status": "COMPARABLE"
                if base_bytes is not None and method_bytes is not None
                else "NOT_COMPARABLE",
                "baseline_bytes": base_bytes,
                "method_bytes": method_bytes,
                "saving_fraction": None
                if base_bytes is None or method_bytes is None
                else 1 - method_bytes / base_bytes,
            }
        )
    return comparisons


def _text(value: object) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")


def _metric(value: object, paths: tuple[tuple[str, ...], ...]) -> str:
    for path in paths:
        item = value
        for key in path:
            if not isinstance(item, dict):
                item = None
                break
            item = item.get(key)
        if isinstance(item, (int, float)) and not isinstance(item, bool) and math.isfinite(item):
            return f"{item:.6g}"
    return "未报告"


def build_terminal_texture_gallery(root: Path, output: Path) -> dict[str, object]:
    """Build one immutable actual-map attachment after final records/matrix.

    Missing optional scripts and failed gallery generation remain explicit
    attachment statuses; numeric comparison results are left intact.
    """
    script = root / "scripts/build_budget_texture_gallery.py"
    folder = output / "texture_gallery"
    started = time.monotonic()
    result: dict[str, object] = {
        "status": "NOT_AVAILABLE", "script_path": str(script),
        "directory": "texture_gallery", "channel_contacts": {},
        "preview_seed": 17,
        "reason": "GALLERY_SOURCE_SCRIPT_NOT_AVAILABLE",
    }
    if script.is_file():
        try:
            digest = sha256(script)
            result["script_sha256"] = digest
            name = "_reseam_terminal_texture_gallery_" + digest[:16]
            spec = importlib.util.spec_from_file_location(name, script)
            if spec is None or spec.loader is None:
                raise ImportError("Cannot create texture gallery module specification")
            module = importlib.util.module_from_spec(spec)
            # Dataclasses resolve postponed annotations through sys.modules.
            sys.modules[name] = module
            spec.loader.exec_module(module)
            builder = getattr(module, "build_gallery", None)
            if not callable(builder):
                raise TypeError("Gallery script has no callable build_gallery")
            manifest = cast(Callable[[Path, Path], dict[str, object]], builder)(output, folder)
            manifest_path, gallery_path = folder / "texture_manifest.json", folder / "gallery.md"
            if not isinstance(manifest, dict) or not manifest_path.is_file() or not gallery_path.is_file():
                raise RuntimeError("Gallery builder did not produce its manifest and Markdown")
            contacts: dict[str, str] = {}
            raw_contacts = _mapping(manifest.get("channel_contacts", {}), "gallery channel_contacts")
            for channel in ("BaseColor", "Normal", "ORM"):
                relative = raw_contacts.get(channel)
                if not isinstance(relative, str):
                    continue
                contact = (folder / relative).resolve()
                if not contact.is_relative_to(folder.resolve()) or not contact.is_file():
                    raise ValueError(f"Gallery contact image missing or outside output: {channel}")
                contacts[channel] = contact.relative_to(output.resolve()).as_posix()
            result.update({
                "status": "BUILT", "reason": None,
                "gallery_path": "texture_gallery/gallery.md",
                "manifest_path": "texture_gallery/texture_manifest.json",
                "channel_contacts": contacts, "planned_rows": manifest.get("planned_rows"),
                "availability_counts": manifest.get("availability_counts"),
                "source_integrity": manifest.get("source_integrity"),
                "preview_seed": 17,
            })
        except Exception as error:  # noqa: BLE001 -- Attachments must not erase completed numeric results.
            result.update({"status": "FAILED", "reason": f"{type(error).__name__}: {error}"})
    result["wall_seconds"] = time.monotonic() - started
    write_json(output / "texture_gallery_status.json", result)
    return result


def _texture_gallery_report_lines(output: Path, summary: dict[str, object]) -> list[str]:
    raw = summary.get("texture_gallery")
    gallery = cast(dict[str, object], raw) if isinstance(raw, dict) else {}
    status = str(gallery.get("status", "NOT_AVAILABLE"))
    lines = [
        "", "## 实际产出贴图与完整 mip", "",
        (f"附件状态：`{_text(status)}`；预览固定 seed17，保留拒绝、失败和未产出状态。"
         "贴图存在及缩略图均不表示通过验收。"
         "`[verified: texture_gallery_status.json status/preview_seed；matrix.json status/valid]`"), "",
    ]
    if gallery.get("reason"):
        lines.extend([
            (f"附件原因：`{_text(gallery['reason'])}`；数值矩阵与接受结论仍按原记录报告。"
             "`[verified: texture_gallery_status.json reason；matrix.json]`"), "",
        ])
    if status != "BUILT":
        return lines
    lines.extend([
        ("[完整贴图画廊](texture_gallery/gallery.md) · "
         "[所有来源、原始 PNG 与完整 mip 清单](texture_gallery/texture_manifest.json) · "
         "[附件生成状态](texture_gallery_status.json) "
         "`[verified: texture_gallery_status.json gallery_path/manifest_path]`"), "",
        ("图像来源依次选择 case/textures、final_validation 候选、baseline_validation 诊断；"
         "具体来源、候选 ID、拒绝状态及原图链接保存在逐项清单。"
         "`[verified: texture_gallery/texture_manifest.json source_selection_policy/rows]`"), "",
    ])
    raw_contacts = gallery.get("channel_contacts", {})
    contacts = cast(dict[str, object], raw_contacts) if isinstance(raw_contacts, dict) else {}
    for channel in ("BaseColor", "Normal", "ORM"):
        relative = contacts.get(channel)
        if isinstance(relative, str) and (output / relative).is_file():
            lines.extend([
                (f"![{channel} seed17 实际贴图]({relative}) "
                 "`[verified: texture_gallery_status.json channel_contacts]`"), "",
            ])
        else:
            lines.extend([
                (f"{channel}：固定 seed17 项未产生可解码原图，不生成替代预览。"
                 "`[verified: texture_gallery/texture_manifest.json channel_contacts/rows]`"), "",
            ])
    return lines


def write_comparison_report(output: Path, summary: dict[str, object], rows: list[dict[str, object]]) -> None:
    """Report only observed validity, payload, and quality; never lowest-loss wins."""
    minima = cast(list[dict[str, object]], summary["minimum_results"])
    accepted = sum(row["valid"] is True for row in rows)
    lines = [
        "# ReSeam 新求解器 v1：预算—质量对比报告",
        "",
        (f"轮次 `{_text(summary['round_id'])}`，状态 `{_text(summary['status'])}`；预定{len(rows)}项，"
        f"已验收{accepted}项。`[verified: summary.json 与 matrix.json]`"),
        "",
        ("本报告使用固定 energy_blade/dragon 开发资产、普通单套 UV、CPU 软件采样与实际 RGBA8 编解码合同。"
        "全部实际材质通道及完整 mip 计入 payload；GPU/目标引擎与 artist 验收状态另列为未执行。"
        "两模型开发结果不构成未知资产泛化证据。`[verified: protocol.json 的 validation_contract]` `[opinion]`"),
        "",
        ("各图集预算独立从相同来源初始化，按完整字节数挑选已验收候选；失败不用于推断数学不可行，"
        "预算/求解成功不假定严格单调，不做二分。`[verified: protocol.json 的 initialization_policy]` `[opinion]`"),
        "",
        "## 各方法最小已验收存储",
        "",
        "|模型|种子|方法|最小 payload B|图集边长|通过/预定预算|结论|",
        "|---|---|---|---|---|---|---|",
    ]
    for row in minima:
        lines.append(
            f"|{_text(row['model'])}|{row['seed']}|{_text(row['method'])}|"
            f"{row['minimum_payload_bytes'] if row['minimum_payload_bytes'] is not None else '无合格结果'}|"
            f"{row['resolution'] if row['resolution'] is not None else '—'}|"
            f"{row['validated_budgets']}/{row['planned_budgets']}|"
            f"{_text(row['status'])} `[verified: summary.json minimum_results]`|"
        )
    lines += [
        "",
        ("只有双方在相同冻结质量条件下均有合格结果，才计算相对 independent 的字节收益；"
        "任一方缺少合格结果均记为不可比较。`[verified: summary.json comparisons_vs_independent]`"),
        "",
        "|模型|种子|方法|independent B|该方法 B|节省比例|状态|",
        "|---|---|---|---|---|---|---|",
    ]
    for row in cast(list[dict[str, object]], summary["comparisons_vs_independent"]):
        fraction = row["saving_fraction"]
        saving = "—" if fraction is None else f"{cast(float, fraction):.2%}"
        lines.append(
            f"|{row['model']}|{row['seed']}|{row['method']}|{row['baseline_bytes']}|"
            f"{row['method_bytes']}|{saving}|{row['status']} `[verified: summary.json comparisons_vs_independent]`|"
        )
    lines += [
        "",
        "## 完整矩阵与原始质量",
        "",
        ("误差列只是原始诊断摘要，接受必须同时满足 geometry/material/render 门禁、输入源码冻结和计算预算；"
        "全部逐面与渲染指标见各 record.json。低均值、几何通过或高复用比例均不单独构成接受。"
        "`[verified: matrix.json valid 字段构造；各行 record.json]`"),
        "",
        "|模型|方法|种子|边长|状态|payload B|几何/材质/渲染|颜色均值|法线最大角|target 秒|setup 摊销秒|记录|",
        "|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for row in rows:
        gates = "/".join(
            "通过" if (_material_valid(row.get(key)) if key == "material" else _gate_valid(row.get(key)))
            else "未通过" for key in ("geometry", "material", "render")
        )
        color = _metric(
            row.get("material"),
            (
                ("color_mean",),
                ("area_weighted_color_mean",),
                ("color", "mean"),
                ("global", "color", "mean"),
                ("global", "color_mean"),
                ("channels", "color", "area_weighted_mean"),
            ),
        )
        normal = _metric(
            row.get("material"),
            (
                ("normal_max_degrees",),
                ("normal", "max"),
                ("global", "normal", "max"),
                ("global", "normal_max_degrees"),
                ("channels", "normal", "max"),
            ),
        )
        lines.append(
            f"|{row['model']}|{row['method']}|{row['seed']}|{row['resolution']}|{row['status']}|"
            f"{row['payload_bytes']}|{gates}|{color}|{normal}|{cast(float, row['wall_seconds']):.3f}|"
            f"{cast(float, row['setup_amortized_seconds']):.3f}|"
            f"[record]({_text(row['record_path'])}) `[verified: matrix.json 与该 record]`|"
        )
    lines += [
        "",
        ("纹理有效载荷与导出网格属性、PNG 文件和 GLB 容器分别核算；GLB 内嵌 mip0，"
         "PNG 包含单列 mip，以下文件项存在内容重叠，不直接相加当作净资产存储。`[opinion]`"),
        "",
        "|模型|方法|种子|边长|纹理 payload B|导出网格属性 B|PNG 全 mip 文件 B|GLB 文件 B|",
        "|---|---|---|---|---|---|---|---|",
    ]
    for row in rows:
        lines.append(
            f"|{row['model']}|{row['method']}|{row['seed']}|{row['resolution']}|{row['payload_bytes']}|"
            f"{row.get('mesh_attribute_bytes')}|{row.get('package_texture_png_bytes')}|"
            f"{row.get('export_glb_package_bytes')} `[verified: matrix.json 与对应输出文件]`|"
        )
    lines += [
        "",
        "## 成本、失败与适用边界",
        "",
        (f"状态分布：`{_text(summary['status_counts'])}`。"
        "所有预定行均保留；未运行行有单独状态，不从成功率分母删除。`[verified: summary.json status_counts]`"),
        "",
        ("每方法的 target 秒上限相同，准备阶段只读缓存一次；实际 setup 成本单列并向该模型所有预定行等额摊销，"
        "管理/哈希/快照/报告开销另在 budget_account.json 计入。原始运行时间和摊销时间同时保留。"
        "`[verified: budget_account.json；matrix.json]`"),
        "",
        ("公共准备池可包含独立的 OptCuts 参考初值，其原生运行成本保留在 setup metadata 并纳入等额摊销。"
         "该摊销衡量本轮共同准备池，不代表每种方法单独部署的端到端运行成本，不能据此宣布效率优越性。"
         "新方法使用自身的独立初值，OptCuts 初值仅供 OptCuts 对照。"
         "`[verified: setup.json metadata.initialization；protocol.json cache_policy]` `[opinion]`"),
        "",
        ("源码、配置、推导与全部 manifest 资产均保留开始/结束 SHA-256；运行中检测到源码或输入变化时，"
        "该轮结果失效且不进入最小已验收集合。固定资产使用原对齐，不重做重拓扑或改动原始面。"
        "`[verified: source.json、integrity.json、protocol.json]`"),
        "",
    ]
    if not accepted:
        lines += [
            ("**本轮未取得满足全部合同的候选，因此没有可报告的同质量存储改善。**"
            "这是一轮保留失败的负结果，不是不可复用性或全局无解证明。"
            "`[verified: summary.json validated_rows=0]` `[opinion]`"),
            "",
        ]
    else:
        lines += [
            ("最小值仅限本轮实际已验收候选；完整矩阵、源快照和原始指标支持复核，"
            "不宣称全局最优、连续全域误差证明或 artist 批准。`[opinion]`"),
            "",
        ]
    lines += _texture_gallery_report_lines(output, summary)
    (output / "report.md").write_text("\n".join(lines), encoding="utf-8")


def run_budget_comparison(root: Path, config_path: Path, output: Path) -> dict[str, object]:
    """Execute a finite complete matrix, keeping every failure and unrun target."""
    root, config_path, output = root.resolve(), config_path.resolve(), output.resolve()
    config = load_comparison_config(config_path)
    if output.exists():
        raise FileExistsError(f"Refusing to overwrite comparison {output}")
    started = time.monotonic()
    output.mkdir(parents=True, exist_ok=False)
    rows = _planned_rows(config)
    source_paths: list[Path] = []
    assets: list[Path] = []
    before_source: dict[str, str] = {}
    before_assets: dict[str, str] = {}
    setups: list[dict[str, object]] = []
    status, reason = "RUNNING", None
    summary: dict[str, object] = {"round_id": config.round_id, "status": status}
    stop_marker = output / "STOP_AFTER_CURRENT_TARGET"
    write_json(output / "matrix.json", rows)
    write_json(output / "summary.json", summary)
    write_json(
        output / "protocol.json",
        {
            **asdict(config),
            "initialization_policy": "same_source_independent_each_target_no_cross_budget_warm_start",
            "cache_policy": "shared_read_only_input_preparation_actual_cost_reported_and_amortized_equally",
            "method_budget_policy": "identical_seconds_per_target_including_solve_bake_validation_export",
            "selection_policy": "minimum_actual_payload_among_fully_validated_candidates_only",
            "nonmonotonic_budget_scan": True,
            "validation_contract": {
                "codec": "RGBA8_actual_three_material_images_complete_mips",
                "channels": ["BaseColor", "Normal", "MetallicRoughness"],
                "sampler": "frozen_CPU_software_contract",
                "runtime_uv": "standard_single_uv_material",
                "gpu_or_engine_acceptance": "NOT_RUN",
                "human_acceptance": "NOT_REVIEWED",
                "scope": "two_fixed_development_assets_not_generalization",
                "actual_texel_center_checks_required": True,
            },
            "historical_accounts_modified": False,
            "cooperative_stop": {
                "marker_path": str(stop_marker),
                "policy": "finish current target; check before each model preparation and target",
                "terminal_status": "COMPARISON_STOPPED_BY_REQUEST",
                "remaining_row_status": "NOT_RUN_STOP_REQUESTED",
            },
        },
    )
    try:
        assets, expected = _manifest_assets(root, config.models)
        source_paths = _source_paths(root, config_path)
        before_source, source_errors = _read_hashes(source_paths)
        before_assets, asset_errors = _read_hashes(assets)
        write_json(
            output / "source.json",
            {
                "source_sha256": before_source,
                "input_sha256": before_assets,
                "expected_input_sha256": expected,
                "read_errors": {**source_errors, **asset_errors},
                "python": platform.python_version(),
                "platform": platform.platform(),
                "thread_environment": {name: os.environ.get(name) for name in (
                    "OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "PYTHONUNBUFFERED")},
                "packages": {
                    name: importlib.metadata.version(name)
                    for name in (
                        "numpy",
                        "scipy",
                        "Pillow",
                        "trimesh",
                        "shapely",
                        "xatlas",
                    )
                },
            },
        )
        if source_errors or asset_errors:
            raise OSError("INITIAL_HASH_READ_FAILED")
        if before_assets != expected:
            raise ValueError("ASSET_HASH_MISMATCH")
        _snapshot(root, config_path, source_paths, output)
        bindings = _solver_bindings()
        options = bindings.options(dict(config.options))
        for model in config.models:
            if stop_marker.is_file():
                status, reason = "COMPARISON_STOPPED_BY_REQUEST", "STOP_AFTER_CURRENT_TARGET_REQUESTED"
                break
            setup_started = time.monotonic()
            setup: dict[str, object] = {"model": model, "status": "RUNNING"}
            setups.append(setup)
            model_rows = [row for row in rows if row["model"] == model]
            try:
                prepared = bindings.prepare(
                    root, model, options, setup_started + config.setup_seconds_per_model
                )
                setup["metadata"] = _prepared_metadata(prepared)
                setup["wall_seconds"] = time.monotonic() - setup_started
                if cast(float, setup["wall_seconds"]) > config.setup_seconds_per_model:
                    raise TimeoutError("MODEL_SETUP_BUDGET_EXHAUSTED")
                setup["status"] = "PREPARED"
            except (ValueError, RuntimeError, OSError, TimeoutError) as error:
                setup.update(
                    {
                        "status": "SETUP_TIMEOUT" if isinstance(error, TimeoutError) else "INIT_FAILED",
                        "reason": f"{type(error).__name__}: {error}",
                        "wall_seconds": time.monotonic() - setup_started,
                    }
                )
                for row in model_rows:
                    row.update({"status": "NOT_RUN_" + str(setup["status"]), "reason": setup["reason"]})
                continue
            finally:
                elapsed_setup = float(cast(float, setup.get("wall_seconds", 0.0)))
                for row in model_rows:
                    row["setup_amortized_seconds"] = elapsed_setup / len(model_rows)
                write_json(output / "setup.json", setups)
                write_json(output / "matrix.json", rows)
            for row in model_rows:
                if stop_marker.is_file():
                    status, reason = "COMPARISON_STOPPED_BY_REQUEST", "STOP_AFTER_CURRENT_TARGET_REQUESTED"
                    break
                current_sources, errors = _read_hashes(_source_paths(root, config_path))
                if current_sources != before_source or errors:
                    status, reason = "SOURCE_OR_INPUT_CHANGED", "SOURCE_CHANGED_BEFORE_TARGET"
                    break
                row["status"] = "RUNNING"
                write_json(output / "matrix.json", rows)
                target_started = time.monotonic()
                target_folder = output / Path(cast(str, row["record_path"])).parent
                raw: dict[str, object] = {}
                try:
                    raw = bindings.solve(
                        prepared,
                        str(row["method"]),
                        cast(int, row["seed"]),
                        cast(int, row["resolution"]),
                        target_folder,
                        seconds=config.seconds_per_target,
                        options=options,
                    )
                    if not isinstance(raw, dict):
                        raise TypeError("Solver returned a non-object result")
                    row["solver_status"] = raw.get("status")
                    row["solver_claimed_valid"] = raw.get("valid") is True
                    row["solver_reported_wall_seconds"] = raw.get("wall_seconds")
                    row["mesh_attribute_bytes"] = raw.get("mesh_attribute_bytes")
                    row["mesh_attribute_policy"] = raw.get("mesh_attribute_policy")
                    row["payload_bytes"] = _payload(raw.get("payload_bytes"))
                    for key in ("geometry", "material", "render"):
                        row[key] = _small_report(raw.get(key))
                    groups = raw.get("final_groups", [])
                    row["final_groups"] = groups
                    row["final_group_count"] = (
                        len(groups) if isinstance(groups, list) else (groups if type(groups) is int else 0)
                    )
                    row["valid"] = bool(
                        raw.get("valid") is True
                        and row["payload_bytes"] is not None
                        and all(_gate_valid(row[key]) for key in ("geometry", "render"))
                        and _material_valid(row["material"])
                    )
                    row["status"] = (
                        "VALIDATED" if row["valid"] else (
                            "INVALID_ACCEPTANCE_CLAIM"
                            if raw.get("valid") is True or raw.get("status") == "VALIDATED"
                            else str(raw.get("status", "INVALID_RESULT"))
                        )
                    )
                    if "reason" in raw:
                        row["reason"] = raw["reason"]
                except (ValueError, RuntimeError, OSError, TimeoutError, TypeError) as error:
                    row.update(
                        {
                            "status": "TIME_BUDGET_EXHAUSTED"
                            if isinstance(error, TimeoutError)
                            else "TARGET_FAILED",
                            "valid": False,
                            "reason": f"{type(error).__name__}: {error}",
                        }
                    )
                except Exception as error:  # noqa: BLE001 -- Experiment boundary preserves the full traceback.
                    row.update({
                        "status": "UNEXPECTED_SOLVER_FAILURE",
                        "valid": False,
                        "reason": f"{type(error).__name__}: {error}",
                    })
                    raw["traceback"] = traceback.format_exc()
                finally:
                    wall = time.monotonic() - target_started
                    row["wall_seconds"] = wall
                    row["target_budget_seconds"] = config.seconds_per_target
                    row["target_budget_overrun"] = wall > config.seconds_per_target
                    row["accepted_completed_seconds"] = raw.get("accepted_completed_seconds")
                    row["timely_delivery_retained_after_cleanup"] = bool(
                        row["valid"] and row["target_budget_overrun"]
                        and _timely_delivery_proof(raw, target_folder, config.seconds_per_target)
                    )
                    if row["target_budget_overrun"] and not row["timely_delivery_retained_after_cleanup"]:
                        row.update({"valid": False, "status": "TIME_BUDGET_EXHAUSTED"})
                    target_folder.mkdir(parents=True, exist_ok=True)
                    row["package_texture_png_bytes"] = sum(
                        path.stat().st_size for path in (target_folder / "textures").glob("*.png")
                    )
                    row["export_glb_package_bytes"] = sum(
                        path.stat().st_size for path in target_folder.glob("*.glb")
                    )
                    write_json(target_folder / "record.json", {**raw, **row})
                    write_json(output / "matrix.json", rows)
            if status in {"SOURCE_OR_INPUT_CHANGED", "COMPARISON_STOPPED_BY_REQUEST"}:
                break
        if status == "RUNNING":
            status = "COMPARISON_COMPLETE"
    except (ValueError, RuntimeError, OSError, TimeoutError, TypeError, ImportError) as error:
        status = "COMPARISON_FAILED"
        reason = f"{type(error).__name__}: {error}"
    except Exception as error:  # noqa: BLE001 -- Finalize all planned rows and the independent budget ledger.
        status = "COMPARISON_FAILED"
        reason = f"{type(error).__name__}: {error}"
        write_json(output / "unexpected_failure.json", {
            "reason": reason, "traceback": traceback.format_exc(),
        })
    finally:
        final_source_paths = _source_paths(root, config_path)
        after_source, source_errors = _read_hashes(final_source_paths)
        after_assets, asset_errors = _read_hashes(assets)
        source_unchanged = bool(before_source) and before_source == after_source and not source_errors
        input_unchanged = bool(before_assets) and before_assets == after_assets and not asset_errors
        integrity_valid = source_unchanged and input_unchanged
        if (before_source and not source_unchanged) or (before_assets and not input_unchanged):
            status = "SOURCE_OR_INPUT_CHANGED"
        for row in rows:
            row["source_unchanged"] = source_unchanged
            row["input_unchanged"] = input_unchanged
            if not integrity_valid:
                if row["valid"]:
                    row["pre_integrity_status"] = row["status"]
                    row["status"] = "SOURCE_OR_INPUT_CHANGED"
                row["valid"] = False
            if row["status"] == "RUNNING":
                row["status"] = "NOT_RUN_INTERRUPTED"
            elif row["status"] == "NOT_RUN":
                row["status"] = (
                    "NOT_RUN_SOURCE_CHANGED" if status == "SOURCE_OR_INPUT_CHANGED" else
                    "NOT_RUN_STOP_REQUESTED" if status == "COMPARISON_STOPPED_BY_REQUEST" else "NOT_RUN_ABORTED"
                )
        observed = time.monotonic() - started
        setup_seconds = sum(float(cast(float, item.get("wall_seconds", 0.0))) for item in setups)
        solve_seconds = sum(cast(float, row["wall_seconds"]) for row in rows)
        bookkeeping = max(0.0, observed - setup_seconds - solve_seconds)
        for row in rows:
            row["bookkeeping_amortized_seconds"] = bookkeeping / len(rows)
            row["charged_seconds_with_setup"] = (
                cast(float, row["wall_seconds"])
                + cast(float, row["setup_amortized_seconds"])
                + cast(float, row["bookkeeping_amortized_seconds"])
            )
        minima = _minimum_results(rows, config)
        summary = {
            "schema_version": 1,
            "round_id": config.round_id,
            "status": status,
            "reason": reason,
            "stop_marker_path": str(stop_marker),
            "stop_requested": stop_marker.is_file(),
            "formal": config.formal,
            "planned_rows": len(rows),
            "validated_rows": sum(row["valid"] is True for row in rows),
            "status_counts": dict(Counter(str(row["status"]) for row in rows)),
            "source_unchanged": source_unchanged,
            "input_unchanged": input_unchanged,
            "minimum_results": minima,
            "comparisons_vs_independent": _comparisons(minima),
            "wall_seconds": observed,
            "gpu_or_engine_acceptance": "NOT_RUN",
            "human_acceptance": "NOT_REVIEWED",
            "global_optimality_claimed": False,
            "generalization_claimed": False,
        }
        write_json(
            output / "integrity.json",
            {
                "source_before": before_source,
                "source_after": after_source,
                "input_before": before_assets,
                "input_after": after_assets,
                "read_errors": {**source_errors, **asset_errors},
                "source_unchanged": source_unchanged,
                "input_unchanged": input_unchanged,
            },
        )
        write_json(
            output / "budget_account.json",
            {
                "account": "independent_new_solver_complete_comparison",
                "setup_limit_per_model_seconds": config.setup_seconds_per_model,
                "target_limit_seconds": config.seconds_per_target,
                "reserved_compute_seconds": len(config.models) * config.setup_seconds_per_model
                + len(rows) * config.seconds_per_target,
                "observed_total_wall_seconds": observed,
                "setup_seconds": setup_seconds,
                "target_seconds": solve_seconds,
                "bookkeeping_seconds": bookkeeping,
                "setup_costs": setups,
                "row_charges": [
                    {
                        "target_id": row["target_id"],
                        "charged_seconds_with_setup": row["charged_seconds_with_setup"],
                        "target_budget_overrun": row.get("target_budget_overrun", False),
                    }
                    for row in rows
                ],
                "historical_accounts_modified": False,
            },
        )
        for row in rows:
            path = output / cast(str, row["record_path"])
            path.parent.mkdir(parents=True, exist_ok=True)
            existing = (
                {} if not path.is_file() else _mapping(json.loads(path.read_text(encoding="utf-8")), "record")
            )
            write_json(path, {**existing, **row})
        write_json(output / "setup.json", setups)
        write_json(output / "matrix.json", rows)
        # These records and matrix are now immutable inputs to the gallery's
        # provenance hashes. Charge attachment work separately rather than
        # rewriting row charges and invalidating those hashes afterwards.
        gallery = build_terminal_texture_gallery(root, output)
        summary["texture_gallery"] = gallery
        gallery_seconds = float(cast(float, gallery.get("wall_seconds", 0.0)))
        summary["wall_seconds"] = observed + gallery_seconds
        account_path = output / "budget_account.json"
        account = _mapping(json.loads(account_path.read_text(encoding="utf-8")), "budget account")
        account["postprocessing_gallery_seconds"] = gallery_seconds
        account["observed_total_wall_seconds"] = observed + gallery_seconds
        account["standalone_charges"] = [{"kind": "terminal_texture_gallery", "seconds": gallery_seconds}]
        account["charged_total_seconds"] = observed + gallery_seconds
        write_json(account_path, account)
        write_json(output / "summary.json", summary)
        write_comparison_report(output, summary, rows)
    return summary
