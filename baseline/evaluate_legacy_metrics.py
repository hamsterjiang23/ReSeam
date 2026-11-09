"""Read-only 45-metric supplement for the retained three-asset experiment."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import cast

import numpy as np

from baseline.evaluate_paper_metrics import base_metrics
from baseline.paper_metrics import AOScene, chart_metrics
from reseam.agent_tools.state import load_snapshot
from scripts.build_baseline_metrics_table import SPECS, display, lookup

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "docs/assets/legacy-assets-reseam/round02"
OLD_ARTIFACTS = ROOT / "artifacts/legacy_assets_reseam/round02"
PREVIOUS_PROTOCOL = ROOT / "docs/assets/dragon-paper-baselines/with-original-input/paper-metrics-protocol.json"
NAMES = {"chloe": "Chloe 红刺", "energy_blade": "历史能量刀", "dragon": "历史幻想龙"}
EXCLUDED = {"valid_uv_reuse_factor", "paper.invalid_boundary_components"}
FIELDS = [spec for spec in SPECS[:47] if spec[0] not in EXCLUDED]
LABELS = {"resolution": "输入／目标贴图边长 px", "coverage": "排布覆盖率／原始覆盖率 ↑",
          "unit_square_coverage": "原生／最终单位方格覆盖 ↑", "charts": "UV连通岛数 ↓",
          "tiles": "独立图块数 ↓", "declared_shared_surface_fraction": "声明参与共享表面 ↑",
          "actual_shared_surface_fraction": "实际参与共享表面 ↑",
          "valid_duplicate_uv_area_saved_fraction": "有效重复UV占位节省 ↑",
          "distortion.new_seam_length_3d": "记录3D切缝长度 ↓（原表“新增”口径）",
          "distortion.new_seam_length_per_surface_area": "记录切缝长／表面积 ↓（原表口径）"}
FIELDS = [(key, LABELS.get(key, title), kind) for key, title, kind in FIELDS]
assert len(FIELDS) == 45


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def mapping(value: object) -> dict[str, object]:
    return cast(dict[str, object], value) if isinstance(value, dict) else {}


@dataclass
class Inputs:
    hashes: dict[str, str] = field(default_factory=dict)

    def track(self, path: Path) -> Path:
        key, actual = path.relative_to(ROOT).as_posix(), digest(path)
        expected = self.hashes.setdefault(key, actual)
        if actual != expected:
            raise ValueError("INPUT_CHANGED_DURING_EVALUATION:" + key)
        return path

    def read(self, path: Path) -> dict[str, object]:
        return mapping(json.loads(self.track(path).read_text(encoding="utf-8")))

    def portable(self, value: object) -> Path:
        path = Path(str(value))
        if path.is_relative_to(OLD_ARTIFACTS):
            path = PACKAGE / "evidence" / path.relative_to(OLD_ARTIFACTS)
        if not path.is_relative_to(PACKAGE) or not path.is_file():
            raise ValueError("RETAINED_PACKAGE_FILE_REQUIRED:" + str(path))
        return path

    def unchanged(self) -> bool:
        return all(digest(ROOT / path) == expected for path, expected in self.hashes.items())


@dataclass
class Case:
    asset: str
    key: str
    method: str
    resolution: int
    snapshot: Path
    uv_present: bool
    original: bool
    packed: bool
    historical: dict[str, object]

    @property
    def identifier(self) -> str:
        return self.asset + "/" + self.key


def cases(inputs: Inputs) -> list[Case]:
    evaluations = inputs.read(PACKAGE / "evaluations.json")
    attributes = inputs.read(PACKAGE / "source_attributes.json")
    geometry = inputs.read(PACKAGE / "geometry_stages.json")
    result: list[Case] = []
    for asset in NAMES:
        source = mapping(attributes[asset])
        original = mapping(mapping(geometry[asset])["import"])
        result.append(Case(asset, "original", "original", 256 if asset == "chloe" else 8192,
                           inputs.portable(original["snapshot"]), source["source_uv_present"] is True,
                           True, False, {}))
        for value in cast(list[object], mapping(evaluations[asset])["rows"]):
            row = mapping(value)
            packed = "snapshot" in row
            result.append(Case(asset, str(row["key"]), str(row["method"]), int(str(row["resolution"])),
                               inputs.portable(row["snapshot"] if packed else row["source_snapshot"]),
                               True, False, packed, row))
    return result


def measure(case: Case, inputs: Inputs, scenes: dict[str, AOScene], samples: int,
            timeout: float) -> dict[str, object]:
    started = time.monotonic()
    record = inputs.read(case.snapshot)
    inputs.track(case.snapshot.parent / "arrays.npz")
    snapshot = load_snapshot(case.snapshot)
    surface = snapshot.state.surface
    metrics: dict[str, object] = {"faces": len(surface.mesh.faces), "vertices": len(surface.mesh.vertices),
                                 "resolution": case.resolution, "declared_groups": 0,
                                 "declared_shared_surface_fraction": 0., "sharing_geometry_verified": False}
    details: dict[str, object] = {}
    reasons: dict[str, str] = {}
    if case.uv_present:
        metrics.update(base_metrics(surface, case.resolution))
        shape = chart_metrics(surface)
        details["charts"] = shape.pop("charts")
        geometry_key = hashlib.sha256(surface.mesh.vertices.tobytes() + surface.mesh.faces.tobytes()).hexdigest()
        if geometry_key not in scenes:
            scenes[geometry_key] = AOScene(surface.mesh, samples=samples)
        visibility = scenes[geometry_key].evaluate(surface, deadline=started + timeout)
        metrics["paper"] = {**shape, **visibility}
        members = {f for group in snapshot.state.groups for f in group.first_faces + group.second_faces}
        slaves = {f for group in snapshot.state.groups for f in group.second_faces}
        metrics["declared_groups"] = len(snapshot.state.groups)
        metrics["declared_shared_surface_fraction"] = (float(surface.mesh.areas[list(members)].sum()
                                                              / surface.mesh.areas.sum()) if members else 0.)
        inspection = mapping(case.historical.get("inspection"))
        if inspection:
            assert inspection["state_hash"] == snapshot.state_hash
            uv_metrics = inputs.read(inputs.portable(inspection["metrics_report"]))
            verified = uv_metrics.get("metrics_verified") is True
            metrics["sharing_geometry_verified"] = verified
            metrics["tiles"] = uv_metrics.get("actual_independent_tiles") if verified else None
            metrics["actual_shared_surface_fraction"] = (mapping(uv_metrics.get("global")).get(
                "actual_shared_surface_fraction") if verified else None)
            if verified:
                uv = surface.uv[surface.uv_faces]
                delta = uv[:, 1:] - uv[:, :1]
                area = abs(delta[:, 0, 0] * delta[:, 1, 1] - delta[:, 0, 1] * delta[:, 1, 0]) / 2
                metrics["valid_duplicate_uv_area_saved_fraction"] = (
                    float(area[list(slaves)].sum() / area.sum()) if slaves else 0.)
            assert int(str(metrics["charts"])) == inspection["chart_count"]
        metrics["coverage"] = metrics["unit_square_coverage"] if case.packed or case.original else None
        if not case.packed and not case.original:
            reasons["coverage"] = "PACK_REJECTED_NATIVE_UV_ONLY"
        for key in ("tiles", "actual_shared_surface_fraction", "valid_duplicate_uv_area_saved_fraction"):
            if metrics.get(key) is None:
                reasons[key] = "NO_MATCHING_ACCEPTED_REUSE_GEOMETRY_MEASUREMENT"
        for key, status in (("paper.seam_ao", "seam_ao_status"), ("paper.boundary_ao", "boundary_ao_status")):
            if lookup(metrics, key) is None:
                reasons[key] = str(visibility[status])
    else:
        reasons.update({key: "NO_ORIGINAL_LOW_UV; resolution describes the high-source image only"
                        for key, _, _ in FIELDS if key not in metrics})
    validation_result = mapping(case.historical.get("validation"))
    quality_accepted: object = None
    if validation_result.get("validation"):
        path = inputs.portable(validation_result["validation"])
        validation = inputs.read(path)
        assert validation["state_hash"] == snapshot.state_hash
        quality_accepted = validation["quality_accepted"]
        material = mapping(validation["material"])
        metrics.update({"color_" + key: material.get(key) for key in ("mean", "p95", "max")})
        views = cast(list[dict[str, object]], mapping(validation["render"]).get("views", []))
        metrics["worst_render_mean"] = max((float(str(view["mean"])) for view in views), default=None)
        metrics["worst_render_max"] = max((float(str(view["max"])) for view in views), default=None)
        details["color_protocol"] = validation["profile"]
        details["validation_source"] = path.relative_to(ROOT).as_posix()
    else:
        reasons.update({key: "NO_NEW_BAKE_OR_MATCHING_COLOR_VALIDATION; not imputed as zero"
                        for key in ("color_mean", "color_p95", "color_max", "worst_render_mean", "worst_render_max")})
    values = {key: lookup(metrics, key) for key, _, _ in FIELDS}
    for key, value in values.items():
        if value is None and key not in reasons:
            reasons[key] = "UNDEFINED_OR_NO_VALID_INCLUDED_PRIMITIVES"
        elif isinstance(value, float) and not np.isfinite(value):
            raise ValueError(f"NONFINITE_METRIC:{case.identifier}:{key}")
    return {"asset": case.asset, "case_id": case.identifier, "method": case.method,
            "key": case.key, "resolution": case.resolution, "original": case.original,
            "layout_stage": "original_uv" if case.original and case.uv_present else
                "original_low_no_uv" if case.original else "final_packed" if case.packed else "native_pack_rejected",
            "snapshot": case.snapshot.relative_to(ROOT).as_posix(), "state_hash": snapshot.state_hash,
            "geometry_hash": record["geometry_hash"], "historical_status": case.historical.get("status", "REFERENCE"),
            "historical_quality_accepted": quality_accepted, "metrics": metrics, "values": values,
            "null_reasons": reasons, "details": details, "seconds": time.monotonic() - started,
            "quality_status_changed": False}


def write_tables(output: Path, rows: list[dict[str, object]]) -> None:
    with (output / "all-metrics.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        columns = ["asset", "key", "layout_stage", "geometry_hash", "historical_quality_accepted", *[f[0] for f in FIELDS]]
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()
        writer.writerows({**{key: row[key] for key in columns[:5]}, **mapping(row["values"])} for row in rows)
    for asset, asset_name in NAMES.items():
        resolution = 256 if asset == "chloe" else 2048
        methods = ["original", *([] if asset == "chloe" else ["xatlas"]),
                   "optcuts", "mirror_shared_final", "mirror_unshared_final"]
        selected = [next(row for row in rows if row["asset"] == asset and row["method"] == method
                         and (method == "original" or row["resolution"] == resolution)) for method in methods]
        matrix = [["指标", *[str(row["method"]) for row in selected]]]
        for key, title, kind in FIELDS:
            matrix.append([title, *[display(mapping(row["values"])[key], kind) for row in selected]])
        with (output / f"{asset}-comparison.csv").open("w", encoding="utf-8-sig", newline="") as stream:
            csv.writer(stream).writerows(matrix)
        header, *body = matrix
        text = [f"# {asset_name}：45项指标", "", "|" + "|".join([*header, "来源"]) + "|",
                "|" + "|".join("---" for _ in [*header, "来源"]) + "|"]
        text.extend("|" + "|".join([*row, f"`[verified: summary.json rows.values.{spec[0]}]`"]) + "|"
                    for row, spec in zip(body, FIELDS, strict=True))
        text += ["", ("原始与镜像为不同几何；共享/解除共享为同几何对照。原低模无UV项不适用，失败项仅保留原生UV。"
                 "`[verified: summary.json rows.geometry_hash/layout_stage]`"),
                 ("原表“新增3D切缝”实际计算当前state.seams总长；本表改称“记录3D切缝”，不表示相对作者UV的净新增。"
                 "`[verified: protocol.json new_seam_scope]`"),
                 ("— 的逐项原因及所有尺寸详见summary.json；历史数值拒绝状态保留。"
                 "`[verified: summary.json rows.null_reasons/historical_quality_accepted]`"),
                 "不进行跨几何或失败方法的自动质量排名。`[opinion]`"]
        (output / f"{asset}-comparison.md").write_text("\n".join(text) + "\n", encoding="utf-8")


def run(output: Path, samples: int, timeout: float, only: list[str] | None) -> None:
    inputs = Inputs()
    selected = cases(inputs)
    if only:
        selected = [case for case in selected if case.identifier in only]
        if {case.identifier for case in selected} != set(only):
            raise ValueError("UNKNOWN_CASE_ID")
    output.mkdir(parents=True, exist_ok=False)
    previous = {key: value for key, value in inputs.read(PREVIOUS_PROTOCOL).items()
                if key not in ("measurement_sha256", "original_evidence_sha256")}
    protocol = {**previous, "scope": "three legacy assets, retained snapshots only; 45 fields; no new bake or quality",
                "ao_samples_per_population": samples, "timeout_per_case_seconds": timeout,
                "seed": 17, "ao_cosine_rays_per_point": 8,
                "previous_protocol": PREVIOUS_PROTOCOL.relative_to(ROOT).as_posix(),
                "previous_protocol_sha256": digest(PREVIOUS_PROTOCOL),
                "case_ids": [case.identifier for case in selected], "fields": FIELDS,
                "new_seam_scope": "existing state.seams measured from uncut mesh, NOT net addition relative to authored UV",
                "absent_low_uv": "geometry counts and source image size only; UV quantities remain N/A",
                "failed_pack_scope": "measure retained native UV; coverage final and color errors remain unavailable",
                "ranking": "none across geometry variants or failed quality; raw metrics remain visible",
                "code_sha256": {path: digest(ROOT / path) for path in (
                    "baseline/evaluate_legacy_metrics.py", "baseline/evaluate_paper_metrics.py",
                    "baseline/paper_metrics.py", "baseline/report.py", "scripts/build_baseline_metrics_table.py")}}
    save(output / "protocol.json", protocol)
    scenes: dict[str, AOScene] = {}
    rows: list[dict[str, object]] = []
    for case in selected:
        print("START", case.identifier, flush=True)
        row = measure(case, inputs, scenes, samples, timeout)
        save(output / "cases" / case.asset / f"{case.key.replace('/', '__')}.json", row)
        rows.append(row)
        print(f"DONE {case.identifier} seconds={row['seconds']:.3f}", flush=True)
    assert inputs.unchanged()
    assert all(digest(ROOT / path) == value for path, value in mapping(protocol["code_sha256"]).items())
    save(output / "summary.json", {"protocol": protocol, "rows": rows, "row_count": len(rows),
        "unique_ao_geometries": len(scenes), "source_sha256": inputs.hashes, "all_sources_unchanged": True,
        "all_historical_quality_states_preserved": True, "new_bakes": 0, "new_quality_scores": 0,
        "metric_seconds": sum(float(str(row["seconds"])) for row in rows)})
    if only is None:
        write_tables(output, rows)
    print(json.dumps({"rows": len(rows), "fields": len(FIELDS), "sources_unchanged": True,
                      "seconds": sum(float(str(row["seconds"])) for row in rows)}, ensure_ascii=False))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--samples", type=int, default=1000)
    parser.add_argument("--timeout", type=float, default=300)
    parser.add_argument("--only", nargs="+")
    args = parser.parse_args()
    if args.samples < 1 or args.timeout <= 0:
        parser.error("positive samples and timeout required")
    run(args.output.resolve(), args.samples, args.timeout, args.only)


if __name__ == "__main__":
    main()
