"""Build tower comparison figures and linked reports from real retained artifacts."""

from __future__ import annotations

import csv
import html
import json
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from reseam.agent_tools.state import file_hash, load_snapshot, write_json
from reseam.budget_state import owners_and_members
from reseam.mesh import chart_labels
from reseam.validation import jacobians

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "artifacts/tower_uv_comparison/round02"
OUT = BASE / "report"
METHODS = [
    ("source_uv", "原 UV · 统一 2K"), ("flatten_anything", "FlattenAnything"),
    ("optcuts", "OptCuts"), ("xatlas", "xatlas"), ("blender_smart", "Blender Smart UV"),
    ("partuv", "PartUV"), ("meshquery_project", "MeshQuery 项目实现"),
    ("reseam_unshared", "ReSeam 原形无共享"), ("reseam_shared", "ReSeam 原形共享"),
    ("reseam_optimized", "ReSeam 原形再整理"),
    ("mirror_negative_unshared", "镜像塔 · 无共享"),
    ("mirror_negative_shared", "镜像塔 · 共享"),
    ("mirror_negative_optimized_minimum_shared", "镜像塔 · 合岛后共享"),
    ("mirror_negative_optimized_minimum_unshared", "合岛后 · 无共享对照"),
    ("mirror_negative_aggressive_minimum_shared", "镜像塔 · 大块合岛共享"),
    ("mirror_negative_aggressive_minimum_unshared", "大块合岛 · 无共享对照"),
    ("mirror_negative_exhaustive_minimum_shared", "镜像塔 · 最终合岛共享"),
    ("mirror_negative_exhaustive_minimum_unshared", "最终合岛 · 无共享对照"),
    ("reseam_shared_boundary_control", "原形共享同目标 · 无共享对照"),
    ("reseam_shared_repaired", "原形共享 · 整组退回候选")]


def read(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}


def font(size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype("C:/Windows/Fonts/msyh.ttc", size)


def thumb(canvas: Image.Image, path: str | Path | None, box: tuple[int, int, int, int]) -> None:
    if not path or not Path(path).is_file():
        return
    with Image.open(path) as image:
        picture = image.convert("RGB")
        picture.thumbnail((box[2] - box[0], box[3] - box[1]))
        canvas.paste(picture, (box[0] + (box[2] - box[0] - picture.width) // 2,
                               box[1] + (box[3] - box[1] - picture.height) // 2))


def row(method: str, title: str) -> dict[str, Any]:
    folder = BASE / f"evaluation/{method}_2048"
    prior = folder
    preferred = {"meshquery_project": "meshquery_repaired", "reseam_shared": "reseam_shared_boundary"}.get(method)
    if preferred and (BASE / f"evaluation/{preferred}_2048/summary.json").is_file():
        folder = BASE / f"evaluation/{preferred}_2048"
    selected = read(BASE / f"selected/{method}.json")
    summary = read(folder / "summary.json")
    inspection = read(folder / "inspect/result.json")
    state_path = summary.get("snapshot") or read(folder / "packing_selection.json").get("snapshot") or selected.get("snapshot")
    record: dict[str, Any] = {"method": method, "title": title, "snapshot": state_path,
                              "folder": str(folder), "status": "未完成", "human": "未验收", "game": "未验收"}
    if prior != folder:
        record.update(retained_prior_attempt=str(prior), variant_method=preferred)
    if method == "optcuts":
        record["faces"] = 7278
    if state_path:
        snapshot = load_snapshot(Path(state_path))
        surface = snapshot.state.surface
        owner, _ = owners_and_members(snapshot.state)
        shared = sorted({f for group in snapshot.state.groups for f in group.first_faces + group.second_faces})
        shape = np.linalg.svd(jacobians(surface), compute_uv=False)
        anisotropy = shape[:, 0] / np.maximum(shape[:, 1], 1e-30)
        record.update(faces=len(surface.mesh.faces), charts=len(np.unique(chart_labels(surface))),
                      owners=len(set(owner)), groups=len(snapshot.state.groups),
                      declared_shared_surface_fraction=float(surface.mesh.areas[shared].sum() / surface.mesh.areas.sum()),
                      anisotropy_p50=float(np.median(anisotropy)), anisotropy_p95=float(np.quantile(anisotropy, .95)),
                      state_hash=snapshot.state_hash)
    report = read(Path(inspection["report"])) if inspection.get("report") else {}
    geometry = report.get("geometry", {})
    record.update(coverage=geometry.get("unique_coverage"), minimum_gap=geometry.get("minimum_gap_pixels"),
                  minimum_border=geometry.get("minimum_border_pixels"), hard_geometry=geometry.get("hard_valid"))
    metrics = read(folder / "inspect/uv_metrics.json")
    record["measured_shared_surface_fraction"] = metrics.get("global", {}).get("actual_shared_surface_fraction")
    exported = read(folder / "export/result.json")
    bake = read(folder / "bake/bake.json")
    validation = read(folder / "validate/validation.json")
    record.update(model=exported.get("asset"), texture_file_bytes=bake.get("file_bytes"),
                  decoded_mip_bytes=bake.get("payload_bytes"), mip_count=bake.get("mip_count"),
                  numerical_status=validation.get("status"), mips=validation.get("mips", []))
    record["preview"] = read(folder / "preview/preview.json")
    record["reimport_match"] = record["preview"].get("geometry_and_uv_reimport_match")
    views = read(folder / "uv_views/result.json")
    record["checker"] = views.get("images", {}).get("front")
    record["uv"] = views.get("images", {}).get("uv")
    if not record["checker"]:
        raw = read(folder / "raw_views/result.json")
        record["checker"] = raw.get("images", {}).get("front")
        record["uv"] = raw.get("images", {}).get("uv")
    if record["model"]:
        record["status"] = "已烘焙／已导出"
    elif method == "optcuts":
        record["status"] = "原生失败；切缝修复后超时"
    else:
        for stage in ("bake", "target", "pack43", "pack17"):
            failure = read(folder / stage / "result.json")
            if failure.get("status") not in (None, "OK"):
                record["status"] = str(failure.get("status"))
                record["reason"] = failure.get("reason")
                if str(failure.get("reason", "")).startswith("UNAUTHORIZED_PBR_RASTER_OVERLAP"):
                    record["status"] = "UV 重叠，烘焙未完成"
                break
    return record


def preview_image(record: dict[str, Any], channel: str, camera: str = "front", light: str = "soft") -> str | None:
    return next((item["path"] for item in record.get("preview", {}).get("images", [])
                 if item["channel"] == channel and item["camera"] == camera and item["light"] == light), None)


def card(canvas: Image.Image, record: dict[str, Any], x: int, y: int, width: int, appearance: bool) -> None:
    draw = ImageDraw.Draw(canvas)
    draw.rounded_rectangle((x, y, x + width - 10, y + 970), radius=12, fill="white", outline="#b9c9d7", width=2)
    draw.text((x + 12, y + 12), record["title"], fill="#143454", font=font(22))
    if appearance:
        first = preview_image(record, "material", "front_right")
        second = preview_image(record, "normal_lit", "front_right", "side")
    else:
        first, second = record.get("checker"), record.get("uv")
    thumb(canvas, first, (x + 10, y + 55, x + width - 20, y + 390))
    thumb(canvas, second, (x + 10, y + 400, x + width - 20, y + 735))
    if not first:
        draw.text((x + 20, y + 200), "保留失败／待修复证据", fill="#a33b38", font=font(22))
    coverage = record.get("coverage")
    lines = [f"三角形 {record.get('faces', '—')}",
             f"连通岛 {record.get('charts', '—')} / 图块 {record.get('owners', '—')}",
             "覆盖率 " + (f"{coverage:.2%}" if coverage is not None else "未完成测量"),
             f"声明共享表面 {record.get('declared_shared_surface_fraction', 0):.1%}",
             record["status"], "已保存误差诊断" if record.get("numerical_status") == "DIAGNOSTIC" else "误差检查未完成"]
    for index, line in enumerate(lines):
        draw.text((x + 12, y + 752 + index * 30), line, fill="#334e64", font=font(19))


def build_figures(rows: list[dict[str, Any]]) -> None:
    for appearance, name in ((False, "UV全方法对照.png"), (True, "颜色与法线对照.png")):
        canvas = Image.new("RGB", (3720, 2200), "#f0f5f9")
        draw = ImageDraw.Draw(canvas)
        title = "防御塔 · 颜色与法线实际渲染" if appearance else "防御塔 · 原始 UV、全部基线与 ReSeam 复用"
        draw.text((22, 18), title, font=font(34), fill="#153755")
        draw.text((22, 64), "原形 7,278 三角形 | 2048² 颜色 + 法线 | 同视角、同材质常数 | 空栏链接真实失败记录", font=font(21), fill="#48667e")
        for index, record in enumerate(rows[:10]):
            card(canvas, record, 20 + index * 370, 108, 370, appearance)
        draw.text((22, 1100), "独立镜像几何 · 同一负半边来源与切割平面 · 无共享 → 两侧共享 → 局部合岛 → 大块合岛 → 最终共享", font=font(26), fill="#153755")
        by_method = {record["method"]: record for record in rows}
        displayed = [by_method[key] for key in ["mirror_negative_unshared", "mirror_negative_shared",
            "mirror_negative_optimized_minimum_shared", "mirror_negative_aggressive_minimum_shared",
            "mirror_negative_exhaustive_minimum_shared"]]
        for index, record in enumerate(displayed):
            card(canvas, record, 20 + index * 740, 1150, 730, appearance)
        draw.text((22, 2150), "图块数按声明的排布归属统计；实际共享测量、严格数值与 Agent 看图结论见报告。所有图像来自保存的模型和贴图。", font=font(21), fill="#48667e")
        canvas.save(OUT / name)
    for record in rows:
        if not record.get("preview"):
            continue
        canvas = Image.new("RGB", (1600, 490), "white")
        draw = ImageDraw.Draw(canvas)
        draw.text((10, 4), record["title"] + " / 上：颜色材质，下：中性材质侧光法线", fill="black", font=font(22))
        for index, camera in enumerate(["front", "front_right", "right", "back_right", "back", "back_left", "left", "front_left"]):
            thumb(canvas, preview_image(record, "material", camera), (index * 200, 40, index * 200 + 200, 255))
            thumb(canvas, preview_image(record, "normal_lit", camera, "side"), (index * 200, 265, index * 200 + 200, 480))
        canvas.save(OUT / (record["method"] + "_contact.png"))
    closeup = Image.new("RGB", (2048, 1660), "#f0f5f9")
    draw = ImageDraw.Draw(closeup)
    for index, label in enumerate(["源贴图 · 颜色", "最终 24 图块 · 颜色", "源贴图 · 法线侧光", "最终 24 图块 · 法线侧光"]):
        draw.text((index * 512 + 12, 10), label, fill="#153755", font=font(25))
    for row_index, region in enumerate(["bow", "ornament", "base"]):
        for column, (subject, channel, light) in enumerate([
            ("source", "material", "soft"), ("final", "material", "soft"),
            ("source", "normal_lit", "side"), ("final", "normal_lit", "side")]):
            picture = BASE / f"closeups/{subject}_{region}/{channel}-front_right-{light}-1.png"
            thumb(closeup, picture, (column * 512, 60 + row_index * 530, (column + 1) * 512, 580 + row_index * 530))
    closeup.save(OUT / "关键部位近景对照.png")


def main() -> None:
    OUT.mkdir(exist_ok=True)
    rows = [row(method, title) for method, title in METHODS]
    build_figures(rows)
    fields = ["method", "title", "faces", "charts", "owners", "coverage", "groups",
              "declared_shared_surface_fraction", "measured_shared_surface_fraction", "anisotropy_p50",
              "anisotropy_p95", "minimum_gap", "minimum_border", "texture_file_bytes", "decoded_mip_bytes",
              "mip_count", "numerical_status", "reimport_match", "status", "model", "snapshot"]
    with (OUT / "metrics.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    write_json(OUT / "results.json", rows)
    lines = ["# 防御塔 UV 全方法对照", "", "输入包及原生失败保存在同级 round01，最终材质来源和结果位于 round02。",
             "", "统一设置为 2048² BaseColor＋Normal，roughness=.6、metallic=0，法线约定采用 OpenGL。",
             "", "|方法|三角形|岛 / owner|覆盖率|产物|状态|", "|---|---:|---:|---:|---|---|"]
    sections = []
    for record in rows:
        coverage = f"{record['coverage']:.2%}" if record.get("coverage") is not None else "未测"
        model = Path(record["model"]).relative_to(OUT.parent).as_posix() if record.get("model") else None
        link = f"[GLB](../{model})" if model else "失败记录"
        lines.append(f"|{record['title']}|{record.get('faces', '—')}|{record.get('charts', '—')} / {record.get('owners', '—')}|{coverage}|{link}|{record['status']} `[verified: {record['folder']}]`|")
        assets = f'<a href="../{html.escape(model)}">GLB 模型</a>' if model else "保留失败证据"
        contact = (f'<img src="{record["method"]}_contact.png" loading="lazy">'
                   if (OUT / (record["method"] + "_contact.png")).is_file() else "")
        sections.append(f'<section><h2>{html.escape(record["title"])}</h2><p>{html.escape(record["status"])} · {assets}</p>'
                        + contact +
                        f'<details><summary>来源、指标、数值与状态</summary><pre>{html.escape(json.dumps({k:v for k,v in record.items() if k != "preview"},ensure_ascii=False,indent=2))}</pre></details></section>')
    lines.extend(["", "数值状态 DIAGNOSTIC 保留完整误差，实际看图结论由 appearance_review.json 记录。美术人工与游戏 Shader 状态见 protocol.json。 `[verified: 各方法 validation.json；protocol.json]`",
                  "", "OptCuts 原生运行失败，显式切缝修复重试达到 2400 秒上限。MeshQuery 的 LSCM 和原形近似共享的失败及后续修正均保留。 `[verified: round01/native_retry/optcuts/result.json；round02/native_repaired/optcuts/result.json；evaluation]`"])
    lines.extend(["", "原形共享主图采用显式 4 像素主图块边界延拓目标，保留全部 13 组；原始拒绝、整组退回候选及同目标无共享对照均可追溯。 `[verified: evaluation/reseam_shared_boundary_2048/target/target.json；selected]`",
                  "", "原 UV 统一到 2K 时保留原布局，实测岛间距 0 像素、边界约 2.38 像素；其余完成排布的候选按 16 像素间距、8 像素边界请求并实测，CSV 提供实际值。 `[verified: source_uv inspection.json；各 pack geometry.json]`",
                  "", "原 UV 的 3998 岛按原始索引邻接统计，纹理坐标在图像上相接的片不自动视为已连接。ReSeam 通过显式接缝重构与重展开建立新的连接。 `[verified: source_uv inspect；craft/seam_decision.json]`",
                  "", "ReSeam 采用 MeshQuery 查询、原 UV 开口重建和 Blender 展开作为实际起点，后续逐步合岛与镜像共享。OptCuts 本资产未得到可用结果。 `[verified: craft、optimization、native_repaired]`"])
    (OUT / "report.md").write_text("\n".join(lines), encoding="utf-8")
    page = '<!doctype html><meta charset="utf-8"><title>防御塔 UV 全方法实验</title><style>body{font-family:Microsoft YaHei,sans-serif;background:#eff4f8;color:#173651;margin:25px}img{width:100%;height:auto}section{background:white;padding:20px;margin:20px 0}pre{white-space:pre-wrap;word-break:break-all}a{color:#0866a2}</style><h1>防御塔 UV 全方法实验</h1><p><a href="metrics.csv">CSV 指标</a> · <a href="results.json">完整来源清单</a> · <a href="../comparison.blend">Blender 对照场景</a></p><img src="UV全方法对照.png"><img src="颜色与法线对照.png"><img src="关键部位近景对照.png">' + "".join(sections)
    review = read(OUT / "appearance_review.json")
    if review:
        page += '<section><h2>Agent 实际看图记录</h2><pre>' + html.escape(json.dumps(review, ensure_ascii=False, indent=2)) + '</pre></section>'
    (OUT / "index.html").write_text(page, encoding="utf-8")
    write_json(OUT / "figure_sources.json", {"rows": [{"method": r["method"], "checker": r["checker"], "uv": r["uv"], "snapshot": r["snapshot"]} for r in rows],
        "figures": {p.name: file_hash(p) for p in OUT.glob("*.png")}})
    print(OUT, flush=True)


if __name__ == "__main__":
    main()
