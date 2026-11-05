"""Build measured baseline tables and presentation figures from real tool artifacts."""

from __future__ import annotations

import argparse
import colorsys
import hashlib
import json
import math
import shutil
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont
from shapely import Polygon, union_all
from shapely.geometry import box

from reseam.agent_tools.state import load_snapshot, read_object, write_json
from reseam.mesh import chart_labels
from reseam.types import MeshState
from reseam.validation import jacobians

ROOT = Path(__file__).resolve().parents[1]
NAMES = {"flatten_anything": "FlattenAnything", "optcuts": "OptCuts", "xatlas": "xatlas",
         "blender_smart": "Blender Smart UV", "partuv": "PartUV",
         "meshquery_project": "MeshQuery 项目实现", "reseam_shared": "ReSeam 共享",
         "reseam_unshared": "ReSeam 解除共享"}
FONT = Path("C:/Windows/Fonts/msyh.ttc")


def read_if(path: Path) -> dict[str, object]:
    return read_object(path) if path.is_file() else {}


def distortion_metrics(surface: MeshState) -> dict[str, object]:
    """Appendix A.6 scalars plus orientation-independent K for mirrored UV."""
    uv = surface.uv[surface.uv_faces]
    delta = uv[:, 1:] - uv[:, :1]
    det = delta[:, 0, 0] * delta[:, 1, 1] - delta[:, 0, 1] * delta[:, 1, 0]
    area = abs(det) * .5
    physical = surface.mesh.areas
    keep = (physical >= physical.mean() * 1e-4) & (area > 0)
    ratio = area[keep] * physical.sum() / (physical[keep] * area.sum())
    area_dist = np.minimum(10., np.maximum(ratio, 1 / ratio))
    singular = np.linalg.svd(jacobians(surface), compute_uv=False)
    nonsingular = keep & (singular[:, 1] > 1e-14)
    positive = nonsingular & (det > 1e-12)
    conformal = singular[nonsingular, 0] / singular[nonsingular, 1]
    paper_conformal = singular[positive, 0] / singular[positive, 1]
    weights = physical[keep] / physical[keep].sum()
    log_density = np.log2(area[keep] / physical[keep])
    spread = math.sqrt(float(np.dot(weights, (log_density - np.dot(weights, log_density))**2)))
    cut_length = 0.
    uv_length = 0.
    for edge_id, incidence in enumerate(surface.mesh.edge_faces):
        if edge_id in surface.seams:
            a, b = surface.mesh.edges[edge_id]
            cut_length += float(np.linalg.norm(surface.mesh.vertices[a] - surface.mesh.vertices[b]))
        if edge_id in surface.seams or len(incidence) == 1:
            for face, corner in incidence:
                uv_length += float(np.linalg.norm(uv[face, corner] - uv[face, (corner + 1) % 3]))
    return {"area_distortion_face_mean": float(area_dist.mean()) if len(area_dist) else None,
            "area_distortion_included_faces": int(keep.sum()),
            "conformal_mean_all_nondegenerate": float(conformal.mean()) if len(conformal) else None,
            "conformal_mean_paper_positive_only": float(paper_conformal.mean()) if len(paper_conformal) else None,
            "conformal_paper_included_faces": int(positive.sum()),
            "texel_spread_octaves": spread, "new_seam_length_3d": cut_length,
            "new_seam_length_per_surface_area": cut_length / float(physical.sum()),
            "uv_boundary_length": uv_length,
            "scope": "A.6 area cap10/plain face mean; paper K positive-only plus all-sign K; excluded faces remain explicit failures"}


def measured_row(base: Path, method: str, resolution: int) -> dict[str, object]:
    evaluation = base / "evaluation" / f"{method}_{resolution}"
    result = read_if(base / "methods" / method / "result.json")
    if method.startswith("reseam_"):
        native_path = base / ("parent" if method == "reseam_shared" else "unshared_parent")
    elif method == "meshquery_project":
        selected = read_if(base / "meshquery/selected.json")
        native_path = Path(str(selected.get("snapshot", base / "NO_MESHQUERY_OUTPUT")))
    else:
        native_path = Path(str(result.get("snapshot", base / "NO_NATIVE_OUTPUT")))
    pack = read_if(evaluation / "pack/result.json")
    current = Path(str(pack.get("snapshot", native_path)))
    row: dict[str, object] = {"method": method, "name": NAMES[method], "resolution": resolution,
        "native_snapshot": str(native_path), "snapshot": str(current),
        "algorithm_status": result.get("status", "PRECOMPUTED" if method.startswith("reseam_") else "PLANNED"),
        "pack_status": pack.get("status", "NOT_RUN"), "pack_reason": pack.get("reason"),
        "quality_accepted": None, "material_valid": None, "render_valid": None, "seams_valid": None,
        "sharing_geometry_verified": False, "actual_shared_surface_fraction": None,
        "valid_duplicate_uv_area_saved_fraction": None, "valid_uv_reuse_factor": None,
        "native_cost_seconds": result.get("native_cost_seconds", result.get("wall_seconds")),
        "native_settings": result.get("settings", {}),
        "artifacts": {}, "failures": []}
    if not current.exists():
        row["reason"] = result.get("reason", "NO_NATIVE_OUTPUT")
        return row
    snapshot = load_snapshot(current)
    surface = snapshot.state.surface
    labels = chart_labels(surface)
    uv = surface.uv[surface.uv_faces]
    delta = uv[:, 1:] - uv[:, :1]
    det = delta[:, 0, 0] * delta[:, 1, 1] - delta[:, 0, 1] * delta[:, 1, 0]
    area = abs(det) * .5
    total_uv = float(area.sum())
    uv_union = union_all([Polygon(triangle) for triangle in uv])
    union_uv = float(uv_union.area)
    inside = float(uv_union.intersection(box(0., 0., 1., 1.)).area)
    mixed = 0
    for label in np.unique(labels):
        ids = np.flatnonzero(labels == label)
        positive = float(area[ids][det[ids] > 0].sum())
        negative = float(area[ids][det[ids] < 0].sum())
        mixed += int(np.count_nonzero(det[ids] < 0 if positive >= negative else det[ids] > 0))
    members = {f for group in snapshot.state.groups for f in group.first_faces + group.second_faces}
    slaves = {f for group in snapshot.state.groups for f in group.second_faces}
    metrics = read_if(evaluation / "inspect/uv_metrics.json")
    global_metrics = metrics.get("global", {})
    if not isinstance(global_metrics, dict):
        global_metrics = {}
    verified = metrics.get("metrics_verified") is True
    duplicate_uv = float(area[list(slaves)].sum()) if slaves else 0.
    row.update({"faces": len(surface.mesh.faces), "vertices": len(surface.mesh.vertices),
        "charts": len(np.unique(labels)), "declared_groups": len(snapshot.state.groups),
        "tiles": metrics.get("actual_independent_tiles"),
        "declared_shared_surface_fraction": float(surface.mesh.areas[list(members)].sum()
                                                   / surface.mesh.areas.sum()) if members else 0.,
        "actual_shared_surface_fraction": global_metrics.get("actual_shared_surface_fraction") if verified else None,
        "sharing_geometry_verified": verified,
        "valid_duplicate_uv_area_saved_fraction": duplicate_uv / total_uv if verified and total_uv else None,
        "valid_uv_reuse_factor": total_uv / (total_uv - duplicate_uv) if verified and total_uv else None,
        "coverage": inside if "snapshot" in pack else None, "summed_uv_area": total_uv,
        "raw_uv_union_area": union_uv, "unit_square_coverage": inside,
        "uv_outside_unit_square_area": max(0., union_uv - inside),
        "all_overlap_fraction_not_validated_reuse": max(0., 1 - union_uv / total_uv) if total_uv else None,
        "zero_uv_area_faces": int(np.count_nonzero(abs(det) < 1e-18)),
        "mixed_orientation_faces": mixed, "seam_edge_count": len(surface.seams),
        "aggregate_texel_density": math.sqrt(total_uv / float(surface.mesh.areas.sum())) * resolution,
        "uv_layout_stage": "common_packed" if "snapshot" in pack else "native_unaccepted"})
    row["distortion"] = distortion_metrics(surface)
    validation = read_if(evaluation / "validate/validation.json")
    if validation:
        row["quality_accepted"] = validation.get("quality_accepted", validation.get("valid"))
        row["payload_bytes"] = validation.get("payload_bytes")
        for section in ("material", "render", "seams"):
            value = validation.get(section, {})
            if isinstance(value, dict):
                row[section + "_valid"] = value.get("valid")
                if section == "material":
                    for metric in ("mean", "p95", "max"):
                        row["color_" + metric] = value.get(metric)
                if section == "render" and isinstance(value.get("views"), list):
                    views = value["views"]
                    row["worst_render_mean"] = max(float(v["mean"]) for v in views)
                    row["worst_render_max"] = max(float(v["max"]) for v in views)
        geometry = validation.get("geometry", {})
        if isinstance(geometry, dict):
            row["strict_geometry_valid"] = geometry.get("strict_hard_valid", geometry.get("hard_valid"))
            row["geometry_acceptance_scope"] = geometry.get("geometry_acceptance_scope")
    receipts = [read_object(p) for p in evaluation.rglob("receipt.json")]
    row["evaluation_cost_seconds"] = sum(float(str(r["seconds"])) for r in receipts)
    if method == "meshquery_project":
        row["planner_tool_cost_seconds"] = sum(float(str(read_object(p)["seconds"]))
                                               for p in (base / "meshquery").rglob("receipt.json"))
        row["llm_reasoning_seconds"] = None
        row["llm_tokens"] = None
    row["failures"] = [{"path": str(p), "status": d.get("status"), "reason": d.get("reason")}
                        for p in evaluation.rglob("result.json")
                        if (d := read_object(p)).get("status") in {"REJECTED", "ERROR", "TIMEOUT", "DEPENDENCY_MISSING"}]
    uv_view = evaluation / ("uv_views" if "snapshot" in pack else "native_views")
    views = read_if(uv_view / "result.json").get("images", {})
    colors = read_if(evaluation / "color_views/result.json").get("images", {})
    clean_colors = read_if(evaluation / "clean_color_views/result.json").get("images", {})
    if isinstance(clean_colors, dict) and clean_colors.get("oblique"):
        colors = clean_colors
    exports = read_if(evaluation / "export/result.json")
    artifacts: dict[str, str] = {}
    if isinstance(views, dict):
        for key in ("uv", "front", "oblique"):
            if isinstance(views.get(key), str):
                artifacts[key] = str(views[key])
    if isinstance(colors, dict) and isinstance(colors.get("oblique"), str):
        artifacts["color"] = str(colors["oblique"])
    texture = evaluation / "bake/texture/BaseColor_mip0.png"
    if texture.exists():
        artifacts["texture"] = str(texture)
        row["png_mip_bytes"] = sum(p.stat().st_size for p in texture.parent.glob("*.png"))
    model = evaluation / "export/asset.glb"
    if model.exists():
        artifacts["model"] = str(model)
        row["glb_bytes"] = model.stat().st_size
    row["export_status"] = exports.get("status", "NOT_RUN")
    row["artifacts"] = artifacts
    return row


def font(size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(FONT), size)


def native_uv_preview(row: dict[str, object], output: Path) -> None:
    """Show the complete native domain; never clip failed neural UV to the unit square."""
    snapshot = load_snapshot(Path(str(row["snapshot"])))
    surface = snapshot.state.surface
    labels = chart_labels(surface)
    corners = surface.uv[surface.uv_faces]
    low, high = corners.reshape(-1, 2).min(axis=0), corners.reshape(-1, 2).max(axis=0)
    extent = max(float((high - low).max()), 1e-12)
    xy = (corners - (low + high) * .5) / extent * 900 + 512
    xy[:, :, 1] = 1024 - xy[:, :, 1]
    picture = Image.new("RGB", (1024, 1024), "white")
    draw = ImageDraw.Draw(picture)
    for face, triangle in enumerate(xy):
        rgb = colorsys.hsv_to_rgb(float(labels[face]) * .61803398875 % 1., .55, .92)
        color = tuple(round(c * 255) for c in rgb)
        draw.polygon([tuple(v) for v in triangle], fill=color, outline="#525d6b", width=1)
    draw.rectangle((0, 0, 1024, 46), fill="white")
    draw.text((12, 5), f"原生域 [{low[0]:.2f},{high[0]:.2f}]×[{low[1]:.2f},{high[1]:.2f}]；未完成排布", fill="#8b4141", font=font(25))
    picture.save(output)


def figure(rows: list[dict[str, object]], output: Path, kind: str) -> None:
    width, panel, gap = 4000, 480, 16
    canvas = Image.new("RGB", (width, 1320), "#f5f7fa")
    draw = ImageDraw.Draw(canvas)
    title = "完成龙：全部基线 UV 与复用对照" if kind == "uv" else "完成龙：同镜头实际 BaseColor 重建"
    draw.text((30, 20), title, fill="#15344e", font=font(46))
    draw.text((30, 82), "同一11338面几何 / 2048 BaseColor / MeshQuery为项目实现 / 数值通过与外观预览分开", fill="#52677b", font=font(25))
    for i, row in enumerate(rows):
        x, y = 24 + i * (panel + gap), 145
        draw.rounded_rectangle((x, y, x + panel, 1280), radius=14, fill="white", outline="#d5dfe8", width=2)
        draw.text((x + 12, y + 14), str(row["name"]), fill="#15344e", font=font(28))
        files = row.get("artifacts", {})
        if not isinstance(files, dict):
            files = {}
        keys = ("front", "uv") if kind == "uv" else ("color", "texture")
        for j, key in enumerate(keys):
            top = y + 67 + j * 460
            source = files.get(key)
            if isinstance(source, str) and Path(source).exists():
                with Image.open(source) as original:
                    picture = original.convert("RGB")
                    picture.thumbnail((panel - 20, 450))
                    canvas.paste(picture, (x + (panel - picture.width) // 2, top + (450 - picture.height) // 2))
            else:
                draw.rectangle((x + 10, top, x + panel - 10, top + 450), fill="#eef1f5")
                draw.text((x + 25, top + 180), "无合格重烘焙输出" if kind == "color" else "尚无图像", fill="#a84848", font=font(28))
        charts = row.get("charts", "—")
        tiles = row.get("tiles")
        shared = row.get("actual_shared_surface_fraction")
        sharing = f"{float(shared)*100:.1f}%" if isinstance(shared, (int, float)) else "未核验"
        coverage = row.get("coverage")
        occupancy = f"{float(coverage)*100:.2f}%" if isinstance(coverage, (int, float)) else "—"
        lines = [f"岛 {charts} / 图块 {tiles if tiles is not None else '未核验'}",
                 f"实际共享 {sharing} / 覆盖 {occupancy}",
                 f"质量：{'通过' if row.get('quality_accepted') is True else '拒绝' if row.get('quality_accepted') is False else '未通过流程'}"]
        for j, line in enumerate(lines):
            draw.text((x + 14, 1158 + j * 34), line, fill="#36526a", font=font(24))
    canvas.save(output)


def text_metric(value: object, percent: bool = False) -> str:
    if value is None:
        return "未核验"
    if isinstance(value, (float, int)):
        return f"{float(value)*100:.2f}%" if percent else f"{float(value):.4g}"
    return str(value)


def reuse_figure(rows: list[dict[str, object]], output: Path) -> None:
    picture = Image.new("RGB", (1900, 1040), "#f5f7fa")
    draw = ImageDraw.Draw(picture)
    draw.text((45, 25), "UV复用率：与岛数、图块数分别比较", fill="#15344e", font=font(44))
    draw.text((45, 85), "几何口径的已确认堆叠；材质/渲染质量另行验收，非法交叠不算复用", fill="#52677b", font=font(26))
    draw.text((350, 150), "参与共享的表面积", fill="#15344e", font=font(30))
    draw.text((1100, 150), "有效重复UV面积节省", fill="#15344e", font=font(30))
    for i, row in enumerate(rows):
        y = 220 + i * 88
        draw.text((45, y), str(row["name"]), fill="#15344e", font=font(27))
        for start, field in [(350, "actual_shared_surface_fraction"), (1100, "valid_duplicate_uv_area_saved_fraction")]:
            value = row.get(field)
            draw.rounded_rectangle((start, y + 4, start + 560, y + 39), radius=5, fill="#e2e8ee")
            if isinstance(value, (int, float)):
                if value > 0:
                    draw.rounded_rectangle((start, y + 4, start + max(2, value * 560), y + 39), radius=5, fill="#008d9a")
                draw.text((start + 575, y), f"{value*100:.1f}%", fill="#007d89", font=font(27))
            else:
                draw.text((start + 18, y + 1), "未核验；无声明共享组", fill="#6e7784", font=font(23))
    draw.text((45, 960), "100%参与左右共享 ≠ 节省100%贴图；成对完整堆叠对应约50%重复UV面积节省、2倍复用。", fill="#52677b", font=font(27))
    picture.save(output)


def build(base: Path, destination: Path, resolution: int) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    rows = [measured_row(base, method, resolution) for method in NAMES]
    for row in rows:
        method = str(row["method"])
        files = row["artifacts"]
        assert isinstance(files, dict)
        if row.get("uv_layout_stage") == "native_unaccepted":
            native_picture = base / "evaluation" / f"{method}_{resolution}" / "native-domain-full.png"
            native_picture.parent.mkdir(parents=True, exist_ok=True)
            native_uv_preview(row, native_picture)
            files["uv"] = str(native_picture)
        copied: dict[str, str] = {}
        for name, value in files.items():
            source = Path(str(value))
            target = destination / method / (str(name) + source.suffix)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            copied[str(name)] = str(target.relative_to(destination))
            if name == "texture":
                for mip in source.parent.glob("BaseColor_mip*.png"):
                    mip_target = destination / method / "mips" / mip.name
                    mip_target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(mip, mip_target)
        row["delivery_artifacts"] = copied
    inputs = destination / "input"
    inputs.mkdir(exist_ok=True)
    for name in ("snapshot.json", "arrays.npz"):
        shutil.copy2(base / "parent" / name, inputs / name)
    shutil.copy2(base / "inputs/completed_dragon.glb", inputs / "completed_dragon.glb")
    shutil.copy2(base / "protocol.json", destination / "protocol.json")
    shutil.copy2(base / "frozen_code_manifest.json", destination / "frozen_code_manifest.json")
    figure(rows, destination / "uv-and-checker-comparison.png", "uv")
    figure(rows, destination / "basecolor-comparison.png", "color")
    reuse_figure(rows, destination / "reuse-rate-comparison.png")
    write_json(destination / "measurements.json", {"input": read_if(base / "protocol.json"), "rows": rows,
        "reuse_metric_scope": "authorized compatible positive overlap, after geometry validation; material acceptance separate",
        "invalid_overlap_not_reuse": True, "human_accepted": False, "gpu_material_verified": False})
    development: list[dict[str, object]] = []
    for folder in base.parent.iterdir():
        if not folder.is_dir() or folder == base or folder.name.startswith("replay"):
            continue
        for path in folder.rglob("result.json"):
            if folder.name == "round01" and path.parent.parent == folder / "methods":
                continue  # Five original algorithm calls are already counted per method, not twice.
            result = read_object(path)
            if isinstance(result.get("tool"), str) and isinstance(result.get("wall_seconds"), (int, float)):
                development.append({"path": str(path), "tool": result["tool"], "status": result.get("status"),
                                    "seconds": result["wall_seconds"]})
    write_json(destination / "cost-summary.json", {
        "native_algorithm_calls_seconds": sum(float(str(r["native_cost_seconds"])) for r in rows
                                              if r.get("native_cost_seconds") is not None),
        "common_evaluation_calls_seconds": sum(float(str(r["evaluation_cost_seconds"])) for r in rows),
        "meshquery_planner_calls_seconds": sum(float(str(r.get("planner_tool_cost_seconds", 0))) for r in rows),
        "development_smoke_and_failures_seconds": sum(float(str(r["seconds"])) for r in development),
        "development_calls": development, "source_acquisition_and_install_seconds": None,
        "coding_seconds": None, "llm_reasoning_seconds": None, "llm_tokens": None, "llm_fee": None})
    lines = ["# 完成龙：MeshQuery 全基线对照", "", "同一份已完成镜像龙的11338面几何与冻结2048 BaseColor参考；各原生算法输出从独立输入派生。`[verified: measurements.json input；各原生输入SHA256]`", "",
        "![UV与棋盘格](../assets/dragon-paper-baselines/uv-and-checker-comparison.png)", "",
        "上排为相同正视棋盘格，红线是实际切缝叠加；下排为UV。流程拒绝项展示完整原生坐标域并标记未完成排布，不与已排图集覆盖率混排。颜色对照图关闭切缝叠加，使用相同斜视镜头。`[verified: 各view.render请求；native-domain-full.png；clean_color_views请求]`", "",
        "|方法|连通岛|独立图块|几何已确认共享表面|有效重复UV面积节省|复用倍数|覆盖率|质量|",
        "|---|---:|---:|---:|---:|---:|---:|---|"]
    for row in rows:
        values = [str(row["name"]), str(row.get("charts", "—")), str(row.get("tiles") if row.get("tiles") is not None else "未核验"),
                  text_metric(row.get("actual_shared_surface_fraction"), True),
                  text_metric(row.get("valid_duplicate_uv_area_saved_fraction"), True),
                  text_metric(row.get("valid_uv_reuse_factor")), text_metric(row.get("coverage"), True),
                  "通过" if row.get("quality_accepted") is True else "拒绝" if row.get("quality_accepted") is False else "未评估/流程拒绝"]
        lines.append("|" + "|".join(values) + f" `[verified: measurements.json rows.{row['method']}]`|")
    lines += ["", "共享表面比例计入共享组的两侧；整龙左右共享可为100%，对应重复UV面积节省约50%、复用倍数约2。三者均不是同质量内存节省。无有效共享组的基线声明共享率为0；若合法性失败，实测共享留空，不把自交当复用。`[verified: measurements.json共享与拒绝字段；opinion: 指标解释]`", "",
        "![UV复用率对比](../assets/dragon-paper-baselines/reuse-rate-comparison.png)", "",
        "![实际颜色与贴图](../assets/dragon-paper-baselines/basecolor-comparison.png)", "",
        "MeshQuery未找到官方公开代码，使用现有ReSeam工具的项目实现：当前Agent观察几何，预览接缝计划，依据4个环带的拓扑拒绝反馈添加测地连接，再由Minimum Stretch展开。此路径不等同论文作者Claude/PartField/Blender5.1.2配置，也不作为作者结果。`[verified: baseline/MeshQuery/README.md；本轮meshquery计划与调用；opinion: 结果边界]`", "",
        "FlattenAnything直接调用官方train_fam与模型，按公开mesh_verts测试思路在原始顶点推理UV；其点式神经映射到传统三角UV的转换并非MeshQuery作者公开实现，未发明额外切缝修补算法来伪造合法结果。原生点映射、权重、混合方向和后续拒绝均保留。`[verified: baseline/adapters/flatten_anything.py；FlattenAnything官方test_mesh.py；measurements.json native_settings]`", "",
        "原生算法设置按论文附录A.4；所有方法后续统一使用本机Blender4.5.11轮廓排布、8px间距/边界、8px烘焙padding与LOD0/1/2。同论文.001间距存在明确差异。本例输入为完成的镜像三角网格，不能代表论文四边形数据集的排名或泛化。`[verified: protocol.json；各request.json；opinion: 适用范围]`", "",
        "ReSeam共享与解除共享使用同一已完成布局，仅去掉共享关系后重排；历史制作成本不是0，也未计作本轮从零优化时间。源码优先官方，集中在baseline/，版本与许可见sources.json；开发失败、依赖适配和完整调用成本保留。`[verified: baseline/sources.json；各native_cache.json与receipt.json；opinion: 成本归因]`", "",
        "为展示实际外观，所有路径使用相同规则：可保留各自原生输出中已有、未改变局部布局的冲突进行诊断重采样；新冲突仍检查。此策略不表示这些原生冲突符合严格几何协议，strict_geometry_valid、最终质量及失败原因另外保留。共享率使用声明的几何检查口径，不自动等于材质通过或人工接受。`[verified: 各请求geometry_profile.preserve_source_uv；measurements.json严格几何及质量字段；opinion: 解释边界]`", "",
        "完整数字与产物索引：[measurements.json](../assets/dragon-paper-baselines/measurements.json)。人工艺术认可、完整PBR、GPU材质验证均未自动通过。`[verified: measurements.json各状态字段]`", ""]
    lines += ["|方法|颜色均值误差|最差视图均值误差|完整mip字节|原生算法调用秒|共同评估调用秒|",
              "|---|---:|---:|---:|---:|---:|"]
    for row in rows:
        lines.append("|" + "|".join([str(row["name"]), text_metric(row.get("color_mean")),
            text_metric(row.get("worst_render_mean")), text_metric(row.get("payload_bytes")),
            text_metric(row.get("native_cost_seconds")), text_metric(row.get("evaluation_cost_seconds"))])
            + f" `[verified: measurements.json rows.{row['method']}]`|")
    lines += ["", "|方法|面积畸变D_area↓|保角K（所有非退化方向）↓|论文K（仅正向）↓|纹素分配离散度/octave↓|新增3D切缝长度|UV边界长度|",
              "|---|---:|---:|---:|---:|---:|---:|"]
    for row in rows:
        data = row.get("distortion", {})
        assert isinstance(data, dict)
        values = [str(row["name"])] + [text_metric(data.get(key)) for key in [
            "area_distortion_face_mean", "conformal_mean_all_nondegenerate", "conformal_mean_paper_positive_only",
            "texel_spread_octaves", "new_seam_length_3d", "uv_boundary_length"]]
        lines.append("|" + "|".join(values) + f" `[verified: measurements.json rows.{row['method']}.distortion]`|")
    lines += ["", "面积畸变采用论文A.6的全局面积比归一化、10封顶和保留面普通平均；K同时给出原文正方向筛选和镜像友好的所有非退化方向。被剔除的退化/反向面数量保留在JSON，不能因有限平均较低而把非法候选算通过。纹素分配离散度按3D面积加权。切缝统计使用稳定原边；UV边界在共享两侧分别计数，不能将其当唯一图块占位。`[verified: report.py distortion_metrics；论文A.6；opinion: 解释边界]`", ""]
    lines += ["", "成本为实际工具调用，包含已记录的失败；MeshQuery规划调用另列planner_tool_cost_seconds，LLM思考时间/token/费用未知，不能写为0。重用原生输出的调用成本按原记录计入。`[verified: measurements.json成本字段、native_cache.json、receipt.json]`", "",
        "开发轮冒烟、修正与失败另汇总在[cost-summary.json](../assets/dragon-paper-baselines/cost-summary.json)，不把已计入的5项原生算法重复相加。源码获取/安装、编程和LLM思考耗时未完整测量，留空；这些不是零成本。`[verified: cost-summary.json]`", "",
        "复现：先按baseline/sources.json准备源码与runtime配置，再运行 `uv run python baseline/replay.py --input docs/assets/dragon-paper-baselines/input --output artifacts/dragon_paper_baselines/replay01` 重建本机路径的不可变父快照，然后使用baseline/README.md中的各阶段命令。`[verified: baseline/replay.py；opinion: 复现顺序]`", ""]
    lines += ["|方法|UV|真实颜色预览|实际BaseColor|模型|", "|---|---|---|---|---|"]
    for row in rows:
        files = row["delivery_artifacts"]
        assert isinstance(files, dict)
        cells = [str(row["name"])]
        for key in ("uv", "color", "texture", "model"):
            item = files.get(key)
            cells.append(f"[{key}](../assets/dragon-paper-baselines/{item})" if item else "未产生")
        lines.append("|" + "|".join(cells) + f" `[verified: manifest.json；measurements.json rows.{row['method']}.delivery_artifacts]`|")
    lines += ["", "本轮固定几何有80个按原边连接的表面分量；OptCuts和MeshQuery项目实现也达到80岛。共享版的区别在40个实际排布图块，而非宣称比所有基线更少连通岛。这里比较的是原生展开管线；未给其它基线额外添加本项目镜像堆叠阶段，0%不证明这些后端理论上不能配合共享工具。`[verified: meshquery/natural_plan/plan.json chart_count；measurements.json；opinion: 比较边界]`", "",
        "共享版本轮颜色和接缝通过、严格渲染拒绝。当前共同排布种子0得到62.93%覆盖，区别于历史单独搜索种子29的65.08%；历史最好布局保留，不混入共同排布对照。本轮所有成功烘焙仍是2048 RGBA8及完整mip22369620字节，未做同质量缩图搜索，不声称实测节省运行时纹理内存。`[verified: measurements.json；dragon-midplane-cleanup.md历史排布记录；opinion: 结论边界]`", ""]
    report = ROOT / "docs/results/dragon-paper-baselines.md"
    report.write_text("\n".join(lines), encoding="utf-8")
    manifest = [{"path": str(p.relative_to(destination)), "bytes": p.stat().st_size,
                 "sha256": hashlib.sha256(p.read_bytes()).hexdigest()} for p in destination.rglob("*")
                if p.is_file() and p.name != "manifest.json"]
    write_json(destination / "manifest.json", {"files": manifest})
    print(json.dumps([{k:row.get(k) for k in ["method","charts","tiles","actual_shared_surface_fraction",
                      "coverage","quality_accepted"]} for row in rows], ensure_ascii=False))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, default=ROOT / "artifacts/dragon_paper_baselines/round02")
    parser.add_argument("--output", type=Path, default=ROOT / "docs/assets/dragon-paper-baselines")
    parser.add_argument("--resolution", type=int, default=2048)
    args = parser.parse_args()
    build(args.input.resolve(), args.output.resolve(), args.resolution)


if __name__ == "__main__":
    main()
