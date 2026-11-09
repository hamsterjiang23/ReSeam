"""Consolidate retained original/reference and baseline metrics into one table."""

from __future__ import annotations

import csv
import hashlib
import io
import json
from decimal import Decimal
from pathlib import Path
from typing import cast

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "docs/assets/dragon-paper-baselines"
OUT = DATA / "with-original-input"
REPORT = ROOT / "docs/results/dragon-paper-baselines.md"
PAPER = OUT / "paper-metrics.json"
METHODS = ["original_tripo", "flatten_anything", "optcuts", "xatlas", "blender_smart", "partuv",
           "meshquery_project", "reseam_shared", "reseam_unshared"]
NAMES = ["原始输入†", "FlattenAnything", "OptCuts", "xatlas", "Smart UV", "PartUV",
         "MeshQuery*", "ReSeam共享", "ReSeam解除共享"]
SPECS = [
    ("faces", "三角面数", "int"), ("vertices", "顶点数", "int"),
    ("resolution", "输入/目标边长 px", "int"), ("charts", "UV连通岛数", "int"),
    ("tiles", "独立图块数", "int"), ("declared_groups", "声明共享组数", "int"),
    ("declared_shared_surface_fraction", "声明参与共享表面", "percent"),
    ("sharing_geometry_verified", "复用几何核验完成", "bool"),
    ("actual_shared_surface_fraction", "实际参与共享表面", "percent"),
    ("valid_duplicate_uv_area_saved_fraction", "有效重复UV占位节省", "percent"),
    ("valid_uv_reuse_factor", "有效复用倍数", "float"),
    ("coverage", "正式排布覆盖率", "percent"),
    ("unit_square_coverage", "原生/最终单位方格覆盖", "percent"),
    ("summed_uv_area", "UV三角面积之和", "float"),
    ("raw_uv_union_area", "UV面积并集", "float"),
    ("uv_outside_unit_square_area", "单位方格外UV面积", "float"),
    ("all_overlap_fraction_not_validated_reuse", "全部重叠率（含未许可）", "percent"),
    ("zero_uv_area_faces", "零UV面积面数", "int"),
    ("mixed_orientation_faces", "岛内混合方向面数", "int"),
    ("seam_edge_count", "记录切缝边数", "int"),
    ("aggregate_texel_density", "总面积口径密度 px/模型单位", "float"),
    ("paper.seam_ao", "AO显式切缝 ↑", "float"),
    ("paper.boundary_ao", "AO含原边界 ↑（附加范围）", "float"),
    ("paper.non_disk_fraction", "Non-disk非圆盘岛比例 ↓", "percent"),
    ("paper.compactness", "Compactness紧凑度 ↑", "float"),
    ("paper.convexity", "Convexity凸性 ↑", "float"),
    ("paper.jaggedness", "Jaggedness边界二阶差分 ↓", "float"),
    ("paper.angular_quality", "Angular orthogonality正交度 ↑", "float"),
    ("paper.shape_valid_charts", "形状指标计入岛数", "int"),
    ("paper.convexity_valid_charts", "凸性指标计入岛数", "int"),
    ("paper.invalid_boundary_components", "无法闭合为环的边界分量", "int"),
    ("paper.jaggedness_samples", "边界二阶差分采样点数", "int"),
    ("paper.angular_quality_faces", "正交度计入面数", "int"),
    ("distortion.area_distortion_face_mean", "面积畸变D_area ↓", "float"),
    ("distortion.area_distortion_included_faces", "面积畸变计入面数", "int"),
    ("distortion.conformal_mean_all_nondegenerate", "保角K（所有非退化方向）↓", "float"),
    ("distortion.conformal_mean_paper_positive_only", "论文保角K（仅正向）↓", "float"),
    ("distortion.conformal_paper_included_faces", "论文保角K计入面数", "int"),
    ("distortion.texel_spread_octaves", "纹素分配离散度 octave ↓", "float"),
    ("distortion.new_seam_length_3d", "新增3D切缝长度", "float"),
    ("distortion.new_seam_length_per_surface_area", "新增切缝长/表面积", "float"),
    ("distortion.uv_boundary_length", "UV边界长度", "float"),
    ("color_mean", "颜色均值误差 ↓", "float"), ("color_p95", "颜色P95误差 ↓", "float"),
    ("color_max", "颜色最大误差 ↓", "float"),
    ("worst_render_mean", "最差视图均值误差 ↓", "float"),
    ("worst_render_max", "最差视图最大误差 ↓", "float"),
    ("strict_geometry_valid", "严格几何通过", "bool"),
    ("material_valid", "颜色通过", "bool"), ("seams_valid", "接缝通过", "bool"),
    ("render_valid", "渲染通过", "bool"), ("quality_accepted", "综合质量通过", "bool"),
    ("full_mips", "已写出完整mip层数", "int"),
    ("payload_bytes", "RGBA8完整mip字节", "int"),
    ("png_mip_bytes", "PNG完整mip文件字节", "int"),
    ("glb_bytes", "导出GLB文件字节", "int"),
    ("native_cost_seconds", "原生算法工具耗时 s", "float"),
    ("planner_tool_cost_seconds", "独立规划工具耗时 s", "float"),
    ("evaluation_cost_seconds", "共同评估工具耗时 s", "float"),
    ("llm_reasoning_seconds", "LLM思考耗时 s", "float"),
    ("llm_tokens", "LLM token数", "int"),
    ("llm_fee", "LLM费用", "float"),
    ("metric_evaluation_seconds", "本轮附加指标评估耗时 s", "float"),
    ("paper.seam_ao_status", "显式切缝AO状态", "status"),
    ("paper.boundary_ao_status", "含原边界AO状态", "status"),
    ("algorithm_status", "原生算法状态", "status"),
    ("pack_status", "共同排布状态", "status"),
    ("pack_reason", "排布拒绝原因", "status"),
    ("export_status", "导出执行状态", "status"),
]

LOWER_IS_BETTER = {
    "charts", "tiles", "uv_outside_unit_square_area", "zero_uv_area_faces", "mixed_orientation_faces",
    "seam_edge_count", "paper.non_disk_fraction", "paper.jaggedness", "paper.invalid_boundary_components",
    "distortion.area_distortion_face_mean", "distortion.conformal_mean_all_nondegenerate",
    "distortion.conformal_mean_paper_positive_only", "distortion.texel_spread_octaves",
    "distortion.new_seam_length_3d", "distortion.new_seam_length_per_surface_area", "distortion.uv_boundary_length",
    "color_mean", "color_p95", "color_max", "worst_render_mean", "worst_render_max", "payload_bytes",
    "png_mip_bytes", "glb_bytes", "native_cost_seconds", "planner_tool_cost_seconds", "evaluation_cost_seconds",
    "llm_reasoning_seconds", "llm_tokens", "llm_fee", "metric_evaluation_seconds",
}
HIGHER_IS_BETTER = {
    "declared_shared_surface_fraction", "actual_shared_surface_fraction", "valid_duplicate_uv_area_saved_fraction",
    "valid_uv_reuse_factor", "coverage", "unit_square_coverage", "paper.seam_ao", "paper.boundary_ao",
    "paper.compactness", "paper.convexity", "paper.angular_quality", "sharing_geometry_verified",
    "strict_geometry_valid", "material_valid", "seams_valid", "render_valid", "quality_accepted",
}


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def lookup(record: dict[str, object], key: str) -> object:
    value: object = record
    for part in key.split("."):
        if not isinstance(value, dict):
            return None
        value = value.get(part)
    return value


def display(value: object, kind: str) -> str:
    if value is None:
        return "—"
    if kind == "bool":
        assert isinstance(value, bool)
        return "是" if value else "否"
    if kind == "percent":
        assert isinstance(value, (int, float))
        return f"{value * 100:.2f}%"
    if kind == "int":
        assert isinstance(value, (int, float)) and int(value) == value
        return f"{int(value):,}"
    if kind == "float":
        assert isinstance(value, (int, float))
        return f"{value:.6g}"
    return str(value)


def best_methods(key: str, kind: str, values: dict[str, object]) -> list[str]:
    """Highlight reported numerical extrema, including ties at displayed precision."""
    if key not in LOWER_IS_BETTER | HIGHER_IS_BETTER:
        return []
    if kind == "bool":
        return [name for name, value in values.items() if value is True]
    comparable = {name: Decimal(display(value, kind).replace(",", "").removesuffix("%"))
                  for name, value in values.items() if isinstance(value, (int, float))}
    if len(comparable) < 2:
        return []
    target = min(comparable.values()) if key in LOWER_IS_BETTER else max(comparable.values())
    return [name for name, value in comparable.items() if value == target]


def main() -> None:
    measurement = DATA / "measurements.json"
    verification = DATA / "verification.json"
    cost_path = DATA / "cost-summary.json"
    origin_path = OUT / "comparison-evidence.json"
    original_evidence = json.loads(origin_path.read_text(encoding="utf-8"))
    source = json.loads(measurement.read_text(encoding="utf-8"))
    checked = json.loads(verification.read_text(encoding="utf-8"))
    supplement = json.loads(PAPER.read_text(encoding="utf-8"))
    assert supplement["all_sources_unchanged"] is True
    paper_rows = {row["method"]: row for row in supplement["rows"]}
    source_hashes = {path.relative_to(ROOT).as_posix(): digest(path)
                     for path in [measurement, verification, origin_path, cost_path, PAPER]}
    source_rows = cast(list[dict[str, object]], source["rows"])
    rows = {str(row["method"]): dict(row) for row in source_rows}
    for exported in checked["models_reimported"]:
        rows[exported["method"]]["full_mips"] = exported["full_mips"]
    original = original_evidence["original"]
    rows["original_tripo"] = {"faces": original["faces"], "charts": original["charts"],
        "tiles": original["independent_tiles"], "declared_groups": original["groups"],
        "declared_shared_surface_fraction": original["authorized_shared_surface_fraction"],
        "resolution": original_evidence["texture_size"][0], "coverage": original["coverage"],
        "unit_square_coverage": original["coverage"]}
    assert set(rows) == set(METHODS)
    assert set(paper_rows) == set(METHODS)
    for method in METHODS:
        for key, value in paper_rows[method]["base_metrics"].items():
            if key not in rows[method]:
                rows[method][key] = value
        rows[method]["paper"] = paper_rows[method]["paper_metrics"]
        rows[method]["metric_evaluation_seconds"] = paper_rows[method]["seconds"]
    matrix: list[list[str]] = [["指标", *NAMES, "来源"]]
    raw: list[dict[str, object]] = []
    for key, title, kind in SPECS:
        values = {method: lookup(rows[method], key) for method in METHODS}
        cells = [display(values[method], kind) for method in METHODS]
        if key == "algorithm_status":
            cells[6] = "PLANNED¹"
        scope = "V.models_reimported.full_mips" if key == "full_mips" else f"M.rows.{key}"
        if key in {"llm_reasoning_seconds", "llm_tokens", "llm_fee"}:
            scope = "C." + key
        if key.startswith("paper."):
            scope = "P.rows.paper_metrics." + key.removeprefix("paper.")
        elif key == "metric_evaluation_seconds":
            scope = "P.rows.seconds"
        original_paths = {"faces": "original.faces", "charts": "original.charts", "tiles": "original.independent_tiles",
                          "declared_groups": "original.groups", "declared_shared_surface_fraction": "original.authorized_shared_surface_fraction",
                          "coverage": "original.coverage", "resolution": "texture_size[0]", "unit_square_coverage": "original.coverage"}
        if key in original_paths:
            scope += "; O." + original_paths[key]
        elif lookup(rows["original_tripo"], key) is not None and not key.startswith("paper.") and key != "metric_evaluation_seconds":
            scope += "; P.rows.original_tripo.base_metrics." + key
        matrix.append([title, *cells, f"`[verified: {scope}]`"])
        raw.append({"id": key, "metric": title, "format": kind, "values": values, "source": scope,
                    "best_direction": "min" if key in LOWER_IS_BETTER else "max" if key in HIGHER_IS_BETTER else None,
                    "best_methods": best_methods(key, kind, values)})
    csv_output = io.StringIO(newline="")
    csv.writer(csv_output).writerows(matrix)
    (OUT / "all-metrics.csv").write_text(csv_output.getvalue(), encoding="utf-8-sig", newline="")
    markdown = [list(row) for row in matrix]
    for metric, row in zip(raw, markdown[1:], strict=True):
        winners = cast(list[str], metric["best_methods"])
        for index, method in enumerate(METHODS, start=1):
            if method in winners:
                row[index] = "**" + row[index] + "**"
    table = "\n".join(["|" + "|".join(markdown[0]) + "|", "|" + "|".join(["---"] * len(markdown[0])) + "|",
                       *("|" + "|".join(row) + "|" for row in markdown[1:])])
    intro = f"全部{len(SPECS)}项指标合为一张表：指标为行、原始输入及八条路径为列。`[verified: all-metrics.json metrics、methods]`"
    aliases = ("来源简称：M=上级measurements.json；V=上级verification.json；O=comparison-evidence.json；C=上级cost-summary.json；P=paper-metrics.json。"
               "“—”表示未测、未核验或不适用，均不作零处理。†原始输入是跨几何参考；*MeshQuery是项目实现。`[verified: 对应JSON；opinion: 展示约定]`")
    notes = ("原始列几何、畸变与论文形状指标已从原始快照补算；覆盖率72.55%沿用原始UV测量。本轮ReSeam共同排布为62.93%，历史精调65.08%不混入。颜色/渲染误差相对完成龙的冻结2K参考，原始8K未按本轮协议验收。`[verified: P.rows.original_tripo、M.input、M.rows.reseam_shared、O.original]`\n\n"
             "论文指标按A.6公开公式做项目实现。AO为1000个切缝点与1000个全边参考点、每点8条余弦半球射线，seed17；显式切缝与含原边界分别列出，无显式切缝时前者不适用。Jaggedness为原始UV单位的二阶差分，步长0.005、8—4096点，未套用论文表1未声明的显示倍率；跳过边界与有效样本数均保留。详见paper-metrics-protocol.json。`[verified: P.protocol、P.rows.paper_metrics]`\n\n"
             "这是单龙评估，原始跨几何输入及失败未排布布局继续作为参考；不与论文50模型均值直接排名。Jaggedness受UV尺度及重采样设置影响，不能将原生失败布局的较小数值当作更优合法展开。`[verified: M.input、M.rows.uv_layout_stage、P.protocol；opinion: 比较边界]`\n\n"
             "¹MeshQuery顶层algorithm_status遗留PLANNED，本表保留原始值；实际规划调用与排布/验收/导出记录均已完成，不能仅凭该遗留字段解释为未运行。`[verified: M.rows.meshquery_project algorithm_status、planner_tool_cost_seconds、pack_status、quality_accepted、export_status]`\n\n"
             "PRECOMPUTED表示沿用既有ReSeam布局，历史制作成本未知；MeshQuery规划146.922秒与共同评估302.750秒分别列出。PLANNED、OK等为执行状态，不能代替质量结果。`[verified: M.rows对应字段]`\n\n"
             "全部重叠率含未许可自交，不计作有效复用；切缝、边界长度与密度使用模型/UV单位。PNG/GLB为实际文件字节，RGBA8完整mip为纹理载荷，均不是整个工程包体。六份烘焙均2048、12级mip、22369620字节，本轮没有同质量缩图对照。`[verified: M.rows、V.models_reimported；opinion: 口径解释]`")
    highlighting = ("**粗体表示已报告数值中的最优，并列值按显示精度全部加粗。** 原始输入与失败项均参与数值比较，"
                    "这不等于有效结果排名或最终质量通过。面数、顶点数、分辨率、共享组数、面积统计、密度与样本数等"
                    "没有统一最佳方向的统计项不强行选优；全部重叠率包含合法与非法重叠，不设单一优劣方向。"
                    "只有一项或没有数值的比较不标最佳；布尔验收只加粗通过项。CSV保持纯数据。`[opinion]`")
    body = intro + "\n\n" + aliases + "\n\n" + highlighting + "\n\n" + table + "\n\n" + notes + "\n"
    (OUT / "all-metrics.md").write_text("# 原始输入与全部基线：指标总表\n\n" + body, encoding="utf-8")
    save_json(OUT / "all-metrics.json", {"methods": METHODS, "metrics": raw, "source_sha256": source_hashes,
                                      "source_rows_untouched": True,
                                      "best_scope": "reported numerical values including original reference and failures; ties at displayed precision"})
    report = REPORT.read_text(encoding="utf-8")
    begin, end = "<!-- ALL_METRICS_START -->", "<!-- ALL_METRICS_END -->"
    report_body = body.replace(
        "来源简称：M=上级measurements.json；V=上级verification.json；O=comparison-evidence.json；C=上级cost-summary.json；P=paper-metrics.json。",
        "来源简称：M=[measurements.json](../assets/dragon-paper-baselines/measurements.json)；V=[verification.json](../assets/dragon-paper-baselines/verification.json)；O=[comparison-evidence.json](../assets/dragon-paper-baselines/with-original-input/comparison-evidence.json)；C=[cost-summary.json](../assets/dragon-paper-baselines/cost-summary.json)；P=[paper-metrics.json](../assets/dragon-paper-baselines/with-original-input/paper-metrics.json)。")
    if begin in report:
        first, remainder = report.split(begin, 1)
        _, last = remainder.split(end, 1)
        report = first + begin + "\n" + report_body + end + last
    else:
        lines = report.splitlines()
        replaced: list[str] = []
        prefixes = ["|方法|连通岛|", "|方法|颜色均值误差|", "|方法|面积畸变"]
        index = 0
        while index < len(lines):
            if any(lines[index].startswith(prefix) for prefix in prefixes):
                if lines[index].startswith(prefixes[0]):
                    replaced.extend([begin, report_body, end])
                while index < len(lines) and lines[index].startswith("|"):
                    index += 1
                continue
            replaced.append(lines[index])
            index += 1
        report = "\n".join(replaced) + "\n"
    REPORT.write_text(report, encoding="utf-8")
    parsed = list(csv.reader(io.StringIO((OUT / "all-metrics.csv").read_text(encoding="utf-8-sig"))))
    assert parsed == matrix and len(matrix) == len(SPECS) + 1
    assert all(digest(ROOT / path) == expected for path, expected in source_hashes.items())
    assert rows["blender_smart"]["render_valid"] is True and rows["partuv"]["render_valid"] is True
    assert rows["reseam_shared"]["render_valid"] is False
    assert REPORT.read_text(encoding="utf-8").count(begin) == 1
    save_json(OUT / "all-metrics-verification.json", {"metric_rows": len(SPECS), "method_columns": 9,
        "csv_roundtrip": "PASS", "source_hashes_unchanged": True, "status_assertions": "PASS",
        "full_mips_source_models": len(checked["models_reimported"]), "uv_algorithms_rerun": False,
        "new_metric_evaluation_completed": True, "metric_evaluation_cases": len(paper_rows),
        "markdown_best_cells": sum(len(cast(list[str], metric["best_methods"])) for metric in raw),
        "metric_rows_with_best": sum(bool(metric["best_methods"]) for metric in raw),
        "csv_contains_markdown": False})
    save_json(OUT / "manifest.json", {"files": [{"path": path.relative_to(OUT).as_posix(),
              "bytes": path.stat().st_size, "sha256": digest(path)} for path in sorted(OUT.rglob("*"))
              if path.is_file() and path.name != "manifest.json"]})
    print(f"PASS: {len(SPECS)} metrics x 9 methods; CSV roundtrip, source hashes and status assertions")


if __name__ == "__main__":
    main()
