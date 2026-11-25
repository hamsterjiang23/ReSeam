"""Publish retained dragon production branches and baseline evidence, with new views only."""

from __future__ import annotations

import argparse
import csv
import html
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import cast

import matplotlib

matplotlib.use("Agg")

from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.backends.backend_pdf import PdfPages
from matplotlib.figure import Figure
from matplotlib.font_manager import FontProperties
from matplotlib.patches import FancyBboxPatch
from matplotlib.text import Text
from PIL import Image

from reseam.agent_tools.runner import run_tool
from reseam.agent_tools.state import file_hash, read_object, write_json

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "docs/assets/dragon-paper-baselines"
FONT = "C:/Windows/Fonts/msyh.ttc"
BOLD = "C:/Windows/Fonts/msyhbd.ttc"
CAMERAS = {
    "basecolor": {"name": "oblique", "azimuth": 35, "elevation": 15},
    "checker": {"name": "front", "azimuth": 0, "elevation": 0},
}


def mapping(value: object) -> dict[str, object]:
    return cast(dict[str, object], value)


def objects(value: object) -> list[dict[str, object]]:
    return cast(list[dict[str, object]], value)


def number(value: object) -> float:
    return float(str(value))


@dataclass
class Case:
    key: str
    title: str
    family: str
    snapshot: Path
    texture: Path | None
    model: Path | None
    metrics: dict[str, object]
    accepted: bool | None
    status: str
    action: str
    sources: list[Path]
    native_uv: Path | None = None
    tone: str = "normal"
    images: dict[str, Path] = field(default_factory=dict)


def baked_case(key: str, title: str, family: str, snapshot: Path, bake_dir: Path,
               metrics: dict[str, object], accepted: bool | None, status: str, action: str,
               sources: list[Path], model: Path | None = None, tone: str = "normal") -> Case:
    bake = read_object(bake_dir / "bake.json")
    snap = read_object(snapshot)
    assert bake["state_hash"] == snap["state_hash"], f"Bake/snapshot mismatch: {key}"
    metrics = dict(metrics, payload_bytes=bake["payload_bytes"], resolution=bake["resolution"],
                   padding_pixels=bake["padding_pixels"], target_hash=bake.get("target_hash"),
                   target=bake.get("target"))
    validation_dir = (bake_dir.parent / "validate" if bake_dir.name == "bake" else
                      bake_dir.with_name(bake_dir.name.removesuffix("_bake") + "_validate"))
    validation_path = validation_dir / "validation.json"
    if validation_path.exists():
        validation = read_object(validation_path)
        assert validation["state_hash"] == snap["state_hash"]
        assert validation["quality_accepted"] == accepted
        metrics["validation_protocol"] = validation["profile"]
        sources = [*sources, validation_path]
        for name in ["request.json", "result.json"]:
            if (validation_dir / name).exists():
                sources.append(validation_dir / name)
    return Case(key, title, family, snapshot, bake_dir / "texture/BaseColor_mip0.png", model,
                metrics, accepted, status, action, [*sources, bake_dir / "bake.json"], tone=tone)


def collect_cases() -> tuple[dict[str, Case], list[str], list[str]]:
    cases: dict[str, Case] = {}
    original_source = BASE / "with-original-input/comparison-evidence.json"
    original = mapping(read_object(original_source)["original"])
    cases["original"] = Case(
        "original", "原始 Tripo · 参考", "原始 10,832 面", Path(str(original["snapshot"])),
        Path(str(original["texture"])), ROOT / "model/dragon/tripo_p2/blue dragon 3d model/blue_dragon_3d_model.fbx",
        {"faces": original["faces"], "islands": original["charts"], "tiles": original["independent_tiles"],
         "groups": 0, "coverage": original["coverage"], "resolution": 8192}, None,
        "原始参考 · 未重烘焙", "保留原始 UV、8K BaseColor 与输入几何。", [original_source], tone="reference")
    baseline_source = BASE / "measurements.json"
    baseline_keys: list[str] = []
    for row in objects(read_object(baseline_source)["rows"]):
        key = str(row["method"])
        baseline_keys.append(key)
        paths = {str(k): BASE / str(v).replace("\\", "/") for k, v in mapping(row["delivery_artifacts"]).items()}
        accepted = cast(bool | None, row["quality_accepted"])
        failed = row["pack_status"] != "OK"
        status = "排布失败 · 无烘焙输出" if failed else "UV＋BaseColor 通过" if accepted else "数值拒绝 · 诊断输出"
        title = str(row["name"]).replace("ReSeam ", "历史 ReSeam ")
        metrics = {"faces": row["faces"], "islands": row["charts"], "tiles": row["tiles"],
                   "groups": row["declared_groups"], "coverage": row["coverage"], "resolution": row["resolution"],
                   "shared_surface_fraction": row["actual_shared_surface_fraction"],
                   "payload_bytes": row.get("payload_bytes"), "native_cost_seconds": row["native_cost_seconds"],
                   "evaluation_cost_seconds": row["evaluation_cost_seconds"], "pack_status": row["pack_status"],
                   "pack_reason": row["pack_reason"], "strict_geometry_valid": row.get("strict_geometry_valid"),
                   "material_valid": row["material_valid"], "render_valid": row["render_valid"], "seams_valid": row["seams_valid"]}
        sources = [baseline_source, BASE / "protocol.json"]
        for fail in objects(row["failures"]):
            sources.append(Path(str(fail["path"])))
        snapshot = Path(str(row["snapshot"]))
        if not failed:
            bake_path = Path(str(mapping(row["artifacts"])["texture"])).parents[1] / "bake.json"
            assert read_object(bake_path)["state_hash"] == read_object(snapshot)["state_hash"]
            sources.append(bake_path)
            validation_dir = bake_path.parent.parent / "validate"
            validation = read_object(validation_dir / "validation.json")
            assert validation["state_hash"] == read_object(snapshot)["state_hash"]
            assert validation["quality_accepted"] == accepted
            metrics["validation_protocol"] = validation["profile"]
            sources += [validation_dir / name for name in ["request.json", "result.json", "validation.json"]]
        cases[key] = Case(key, title, "历史镜像 11,338 面", snapshot, paths.get("texture"), paths.get("model"),
                          metrics, accepted, status,
                          "同一冻结镜像几何上的历史原生展开／排布结果；未给其它 baseline 追加 ReSeam 共享。",
                          sources, paths["uv"] if failed else None, "rejected" if failed else "normal")
    for branch, labels in [
        ("components", [("no_share", "修复父状态 · 无共享"), ("agent_paired", "固定几何 · 部件复用")]),
        ("raw121", [("agent_paired", "保留原 UV · 部件复用")]),
    ]:
        source = ROOT / f"artifacts/dragon_reuse_development/{branch}_color02/matrix.json"
        import json

        rows = objects(json.loads(source.read_text(encoding="utf-8")))
        for method, title in labels:
            row = next(item for item in rows if item["method"] == method and item["resolution"] == 2048)
            inspect, validation = mapping(row["inspect"]), mapping(row["validation"])
            key = f"{branch}_{method}"
            cases[key] = baked_case(
                key, title, "固定原几何 10,832 面", Path(str(row["snapshot"])), Path(str(mapping(row["bake"])["bake"])).parent,
                {"faces": 10832, "islands": inspect["uv_connected_charts"], "tiles": inspect["actual_independent_tiles"],
                 "groups": inspect["shared_group_count"], "coverage": inspect["unique_footprint"]},
                cast(bool | None, validation["quality_accepted"]), "数值拒绝 · 外观预览",
                "203 岛修复父状态上局部展开与共享。" if branch == "components" else "从原始 121 岛保留可用布局，局部重展后成对共享。",
                [source], Path(str(mapping(row["export"])["asset"])))
    whole_source = ROOT / "artifacts/meshquery_v1/whole_half_report01/report.json"
    whole = next(row for row in objects(read_object(whole_source)["cases"]) if row["name"] == "shared_blender_concave")
    whole_root = Path(str(whole["root"]))
    cases["whole_mirror"] = baked_case(
        "whole_mirror", "整半镜像 · 紧凑排布", "整半镜像 11,450 面", whole_root / "pack/snapshot/snapshot.json", whole_root / "bake",
        {"faces": 11450, "islands": whole["charts"], "tiles": whole["tiles"], "groups": whole["groups"], "coverage": whole["coverage"]},
        cast(bool, whole["quality_accepted"]), "该轮 BaseColor 协议通过", "沿中线裁切、镜像成新几何，左右整块堆叠；原资产保留。", [whole_source], whole_root / "export/asset.glb")
    import json

    merged_source = ROOT / "docs/assets/dragon-uv-final/measurements.json"
    merged = next(row for row in objects(json.loads(merged_source.read_text(encoding="utf-8"))) if row["name"] == "current")
    merged_snapshot = Path(str(merged["snapshot"]))
    cases["merged92"] = baked_case(
        "merged92", "真实合岛 · 脚底修正", "整半镜像 11,450 面", merged_snapshot,
        Path(str(merged["texture"])).parents[1],
        {"faces": merged["faces"], "islands": merged["charts"], "tiles": merged["independent_tiles"],
         "groups": merged["groups"], "coverage": merged["coverage"]}, False,
        "颜色／接缝过 · 渲染拒绝", "合并头冠、脚部内部接缝并重展开；修正脚底开口。", [merged_source, ROOT / "docs/assets/dragon-uv-final/experiment-summary.json"])
    packing_source = ROOT / "docs/assets/dragon-packing-search/summary.json"
    packed = next(row for row in objects(read_object(packing_source)["cases"]) if row["label"] == "gap8_seed0")
    # Locate the actual immutable state through the selected bake rather than a report label.
    packing_root = ROOT / "artifacts/meshquery_v1/compact_search01"
    matching_bakes: list[Path] = []
    if packing_root.exists():
        matching_bakes = [p for p in packing_root.glob("**/bake.json") if read_object(p).get("state_hash") == packed["state_hash"] and read_object(p).get("resolution") == 2048]
    if matching_bakes:
        bake = matching_bakes[0]
        cases["packed92"] = baked_case(
            "packed92", "固定岛形状 · 重排", "整半镜像 11,450 面", Path(str(read_object(bake)["snapshot"])), bake.parent,
            {"faces": 11450, "islands": packed["charts"], "tiles": 46, "groups": packed["groups"], "coverage": packed["coverage"]},
            False, "颜色／接缝过 · 渲染拒绝", "固定几何与岛形状，多起点搜索间距与摆放；2K 字节不变。", [packing_source])
    mid_source = ROOT / "docs/assets/dragon-midplane-cleanup/summary.json"
    mid = read_object(mid_source)
    audit = mapping(mid["audit"])
    mid_root = ROOT / "artifacts/meshquery_v1/midplane_cleanup01/seed29"
    cases["midplane80"] = baked_case(
        "midplane80", "中线薄条修复 · 40 图块", "历史镜像 11,338 面", mid_root / "pack/snapshot/snapshot.json", mid_root / "bake",
        {"faces": audit["new_faces"], "islands": audit["new_charts"], "tiles": audit["shared_groups"],
         "groups": audit["shared_groups"], "coverage": mid["coverage"]}, False, "颜色／接缝过 · 渲染拒绝",
        "在授权新镜像变体中修正裁切平面，消除 12 个已定位薄条岛。", [mid_source], ROOT / "docs/assets/dragon-midplane-cleanup/dragon.glb")
    refine_source = ROOT / "artifacts/dragon_wholebody_refine/round03/summary.json"
    refine = read_object(refine_source)
    candidate = mapping(refine["candidate"])
    cases["refine38"] = baked_case(
        "refine38", "历史继续优化 · 38 图块", "历史镜像 11,338 面", Path(str(candidate["snapshot"])),
        ROOT / "artifacts/dragon_wholebody_refine/round03/appearance/candidate_bake",
        {"faces": 11338, "islands": candidate["charts"], "tiles": candidate["groups"], "groups": candidate["groups"], "coverage": candidate["coverage"]},
        False, "Agent 保留 · 渲染仍拒绝", "两组跨部位角刺合并共享；80 个表面保持，40 份图案减到 38 份。", [refine_source],
        ROOT / "output/dragon-wholebody-refine/fewer-tiles-38/model.glb")
    old_source = ROOT / "artifacts/dragon_parts/round03/summary.json"
    old = next(row for row in objects(read_object(old_source)["rows"]) if row["name"] == "mirror_shared2048")
    cases["parts_old199"] = baked_case(
        "parts_old199", "分 part 早期 · 旧 UV", "早期局部镜像 10,836 面", Path(str(old["snapshot"])),
        ROOT / "artifacts/dragon_parts/round03/mirror_shared2048/bake",
        {"faces": old["faces"], "islands": old["uv_islands"], "tiles": None, "packing_owners": old["packing_owners"],
         "groups": old["shared_groups"], "coverage": old["coverage"]}, False, "数值拒绝 · 未完成部件重建",
        "先识别分割部件并沿用旧 UV；仍有大量碎片，后续被分 part 重建替代。", [old_source], Path(str(old["model"])))
    final_source = ROOT / "artifacts/dragon_parts/final/summary.json"
    for row in objects(read_object(final_source)["rows"]):
        key = str(row["name"])
        title, action = {
            "original2048": ("分 part 重建 · 无共享", "归位 25 面标签、合并小 part_8，独立变体删 5 个孤立面；逐部件切口重建与合岛。"),
            "mirror_unshared2048": ("局部镜像 · 无共享", "只镜像翼膜及翼支撑，保留其它部件；为相同新几何建立独占对照。"),
            "mirror_shared2048": ("最终分 part · 共享", "同一局部镜像几何上共享 6 组翼膜与 1 组翼支撑；统一图集并重烘焙。"),
        }[key]
        cases[key] = baked_case(
            key, title, f"分 part {'原' if key == 'original2048' else '局部镜像'} {int(str(row['faces'])):,} 面",
            Path(str(row["snapshot"])), ROOT / f"artifacts/dragon_parts/final/{key}/bake",
            {"faces": row["faces"], "islands": row["uv_islands"], "tiles": row["independent_tiles"], "groups": row["shared_groups"],
             "coverage": row["coverage"], "shared_surface_fraction": row["shared_member_surface_fraction"]},
            cast(bool, row["numeric_accepted"]), "本轮 UV＋BaseColor 通过", action, [final_source], Path(str(row["model"])),
            "final" if key == "mirror_shared2048" else "current")
    overview = ["original", *baseline_keys, "refine38", "original2048", "mirror_unshared2048", "mirror_shared2048"]
    history = ["original", "components_no_share", "components_agent_paired", "raw121_agent_paired", "whole_mirror", "merged92"]
    if "packed92" in cases:
        history.append("packed92")
    history += ["midplane80", "refine38", "parts_old199", "original2048", "mirror_unshared2048", "mirror_shared2048"]
    return cases, overview, history


def prepare_views(case: Case, output: Path) -> dict[str, object]:
    snapshot = read_object(case.snapshot)
    receipt: dict[str, object] = {"state_hash": snapshot["state_hash"], "geometry_hash": snapshot["geometry_hash"], "views": []}
    view_receipts: list[dict[str, object]] = []
    for mode, camera in CAMERAS.items():
        if mode == "basecolor" and case.texture is None:
            continue
        request: dict[str, object] = {"snapshot": str(case.snapshot.resolve()), "state_hash": snapshot["state_hash"],
            "mode": mode, "size": 800, "seams": False, "annotate": False, "uv_labels": "owners",
            "cameras": [camera], "timeout_seconds": 180}
        if mode == "basecolor":
            assert case.texture is not None
            request["texture"] = str(case.texture.resolve())
        folder = output / "views" / case.key / mode
        if folder.exists():
            assert read_object(folder / "request.json") == request
            assert read_object(folder / "result.json")["status"] == "OK"
        else:
            request_path = output / f"request-{case.key}-{mode}.json"
            write_json(request_path, request)
            result = run_tool("view.render", request_path, folder)
            if result.get("status") != "OK":
                raise RuntimeError(f"{case.key}: {mode}: {result.get('reason')}")
        views = read_object(folder / "views.json")
        assert views["state_hash"] == snapshot["state_hash"] and views["cameras"] == [camera]
        case.images[mode] = folder / (str(camera["name"]) + ".png")
        if mode == "checker":
            case.images["uv"] = case.native_uv if case.native_uv else folder / "uv.png"
        view_receipts.append({"mode": mode, "request": str(folder / "request.json"),
                              "views": str(folder / "views.json"), "camera": camera, "status": "OK"})
    receipt["views"] = view_receipts
    for key, path in case.images.items():
        with Image.open(path) as picture:
            assert picture.width > 0 and picture.height > 0
        target = output / "media" / case.key / f"{key}.png"
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
        case.images[key] = target
    if case.texture:
        target = output / "media" / case.key / ("texture" + case.texture.suffix)
        shutil.copy2(case.texture, target)
        case.images["texture"] = target
    return receipt


def metric_lines(case: Case) -> list[str]:
    m = case.metrics
    tiles = str(m["tiles"]) if m.get("tiles") is not None else "未核验"
    first = f"岛 {m['islands']} / 图块 {tiles}"
    if m.get("packing_owners") is not None:
        first = f"岛 {m['islands']} / 排布代表 {m['packing_owners']}"
    coverage = f"{number(m['coverage']):.2%}" if m.get("coverage") is not None else "无合格排布"
    sharing = f"{number(m['shared_surface_fraction']):.1%}" if m.get("shared_surface_fraction") is not None else "—"
    return [first, f"覆盖 {coverage} / 共享组 {m['groups']}",
            f"共享成员表面 {sharing}" if "shared_surface_fraction" in m else f"{int(str(m['faces'])):,} 面 / {m['resolution']}²",
            case.status]


def make_figure(cases: list[Case], output: Path, stem: str, mode: str, history: bool = False) -> tuple[Figure, dict[str, object]]:
    panel, gap, margin, dpi = 440, 14, 24, 125
    width, height = 2 * margin + len(cases) * panel + (len(cases) - 1) * gap, 1390
    fig = Figure(figsize=(width / dpi, height / dpi), dpi=dpi, facecolor="#f5f7fa")
    canvas = FigureCanvasAgg(fig)
    texts: list[tuple[Text, float]] = []

    def text(x: float, y: float, value: str, size: float, color: str = "#25445e",
             bold: bool = False, available: float | None = None) -> None:
        artist = fig.text(x / width, 1 - y / height, value, va="top", color=color,
                          fontproperties=FontProperties(fname=BOLD if bold else FONT, size=size))
        texts.append((artist, available if available is not None else width - x - 24))

    title = ("恐龙历史制作记录" if history else "原始输入、全部 baseline 与分 part 结果") + ("：棋盘格 / UV / 复用对照" if mode == "checker" else "：同视角 BaseColor / 真实贴图")
    text(30, 18, title, 25, bold=True)
    text(30, 78, "历史 baseline：同一 11,338 面镜像几何 / 2K；原始参考 10,832 面 / 8K；新分 part 结果为另一独立几何分支，按列标明。", 13.5)
    text(30, 118, "本图重读已有快照、实际贴图与历史验收，只补渲染；未重新展开、排布、烘焙。跨几何、跨颜色目标、跨验收协议的列只作制作记录参考。", 12.5, "#62788d")
    colors = {"normal": ("#ffffff", "#d4dfe8", "#25445e"), "reference": ("#fffaf0", "#c18a36", "#936020"),
              "rejected": ("#fff9fa", "#d5b0b6", "#aa4d58"), "current": ("#f4faff", "#8caac2", "#25445e"),
              "final": ("#eaf8f5", "#078984", "#067f7a")}
    for index, case in enumerate(cases):
        x = margin + index * (panel + gap)
        fill, border, accent = colors[case.tone]
        fig.add_artist(FancyBboxPatch((x / width, 1 - 1265 / height), panel / width, 1085 / height,
            boxstyle="round,pad=0,rounding_size=0.005", transform=fig.transFigure, facecolor=fill,
            edgecolor=border, linewidth=1.3, zorder=-1))
        text(x + 12, 196, case.title, 14.1, accent, True, panel - 24)
        text(x + 12, 231, case.family, 10.5, "#62788d", available=panel - 24)
        paths = [case.images.get("checker"), case.images["uv"]] if mode == "checker" else [case.images.get("basecolor"), case.images.get("texture")]
        labels = ["同一正视角棋盘格", "彩色 UV · 排布代表标号"] if mode == "checker" else ["同一斜视角 · 真实颜色", "实际 BaseColor 贴图"]
        for row, path in enumerate(paths):
            top = 270 + row * 440
            text(x + 12, top - 20, labels[row], 9.5, "#62788d", available=panel - 24)
            axes = fig.add_axes(((x + 10) / width, 1 - (top + 415) / height, 420 / width, 415 / height))
            axes.set_facecolor("#edf1f5")
            axes.set_xticks([])
            axes.set_yticks([])
            for spine in axes.spines.values():
                spine.set_visible(False)
            if path is None:
                axes.text(0.5, 0.5, "排布失败\n未生成重烘焙贴图", ha="center", va="center", color="#ac5962",
                          fontproperties=FontProperties(fname=FONT, size=12.5), linespacing=1.8)
            else:
                with Image.open(path) as picture:
                    rgba = picture.convert("RGBA")
                    if mode == "basecolor" and row == 1:
                        rgba = Image.alpha_composite(Image.new("RGBA", rgba.size, (0, 0, 0, 255)), rgba)
                    rgba.thumbnail((950, 950), Image.Resampling.LANCZOS)
                    axes.imshow(rgba.convert("RGB"), interpolation="lanczos")
        for n, line in enumerate(metric_lines(case)):
            status_color = "#b04f5a" if case.accepted is False else accent
            text(x + 12, 1150 + n * 27, line, 10.8, status_color if n == 3 else "#36556d", n == 3, panel - 24)
    text(30, 1300, "连通 UV 岛、独立图块、共享组分别统计；未许可交叠不算复用。历史 40／38 图块与新 11 图块含几何和切缝差异，不能作为同几何改进比例。", 12, "#62788d")
    text(30, 1340, "2K RGBA8 完整 mip 均为 22,369,620 字节；图块减少不等于内存减少。旧数值拒绝与新协议通过分开；完整 PBR、GPU、人工验收均不由此图认定。", 12, "#087f7c")
    canvas.draw()
    renderer = canvas.get_renderer()
    for artist, available in texts:
        bounds = artist.get_window_extent(renderer)
        assert bounds.width <= available + 1, f"Text overflow: {artist.get_text()}"
        assert bounds.x0 >= 0 and bounds.x1 <= width and bounds.y0 >= 0 and bounds.y1 <= height
    fig.savefig(output / f"{stem}.png", dpi=dpi)
    fig.savefig(output / f"{stem}.pdf")
    with Image.open(output / f"{stem}.png") as image:
        assert image.size == (width, height)
    return fig, {"stem": stem, "columns": [case.key for case in cases], "mode": mode,
                 "size": [width, height], "text_boxes_checked": len(texts), "sha256": file_hash(output / f"{stem}.png")}


def copy_receipts(case: Case, output: Path) -> list[dict[str, object]]:
    paths = list(case.sources)
    paths.append(case.snapshot)
    for name in ["request.json", "result.json"]:
        candidate = case.snapshot.parents[1] / name
        if candidate.exists():
            paths.append(candidate)
    for source in list(paths):
        if source.name == "bake.json":
            for sibling in ["request.json", "result.json"]:
                candidate = source.parent / sibling
                if candidate.exists():
                    paths.append(candidate)
    records: list[dict[str, object]] = []
    for index, source in enumerate(dict.fromkeys(paths)):
        assert source.is_file(), f"Missing evidence: {source}"
        target = output / "records" / case.key / f"{index:02d}-{source.name}"
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        records.append({"source": str(source), "copy": target.relative_to(output).as_posix(), "sha256": file_hash(source)})
    return records


def write_report(cases: dict[str, Case], overview: list[str], history: list[str], output: Path) -> None:
    historical_pdf = ROOT / "output/pdf/dragon-wholebody-refine/镜像龙制作流程图解_含各UV实际渲染.pdf"
    if historical_pdf.exists():
        shutil.copy2(historical_pdf, output / "historical-workflow.pdf")
    lines = ["# 恐龙历史制作记录与 baseline 对照", "", "本包整理本地已有历史结果，只新增同视角渲染和图表，不重新运行展开、排布或烘焙。`[verified: evidence.json execution_scope]`", "",
        "- [BaseColor 总览](all-methods-basecolor.png)", "- [棋盘格与 UV 总览](all-methods-checker-uv.png)",
        "- [历史制作 BaseColor](history-basecolor.png)", "- [历史制作棋盘格与 UV](history-checker-uv.png)",
        "- [图集 PDF](comparison-book.pdf)", "- [离线查看页面](index.html)",
        "- [历史制作流程图解 PDF](historical-workflow.pdf)（旧轮次原件，原验收边界保留）",
        "- [历史九列完整指标 CSV](baseline-all-metrics.csv)（原始输入＋八条 baseline；保留旧口径）", "",
        "## 比较边界", "",
        "历史八条 baseline 固定 11,338 面镜像几何、2048 BaseColor、8px gap/border/padding；ReSeam 共享布局为已有结果，制作成本未知，不作为从零等预算对照。原始输入和这次分 part 属于不同几何／颜色目标／验收协议。`[verified: records/*；evidence.json cases]`", "",
        "历史 80／40 → 80／38 表示共享图块减少；新分 part 镜像 18／18 → 18／11 才是本轮同几何共享对照。所有 2K 贴图完整 RGBA8 mip 仍为 22,369,620 字节，不把岛数、覆盖率或共享面积当作字节节省。`[verified: evidence.json cases；records/refine38；records/mirror_shared2048]`", "",
        "旧 baseline 验收使用 16 采样／面、96px CPU 渲染探针；新分 part 结果使用 4 采样／面、32px 探针，均检查 LOD0/1/2，形状阈值为 advisory。通过标签保留各轮协议，不视为统一门限重测；每列完整 profile 在 evidence.json 与原始 validation.json。`[verified: evidence.json cases.*.metrics.validation_protocol]`", "",
        "## 全部 baseline 与最终方案", "", "|方案|几何|UV 岛|图块|覆盖|验收记录|", "|---|---|---|---|---|---|"]
    for key in overview:
        case = cases[key]
        m = case.metrics
        coverage = f"{number(m['coverage']):.2%}" if m.get("coverage") is not None else "无合格排布"
        lines.append(f"|{case.title}|{case.family}|{m['islands']}|{m.get('tiles') or '未核验'}|{coverage}|{case.status} `[verified: evidence.json cases.{key}]`|")
    lines += ["", "## 历史制作分支", "", "按生产路线排列；原 UV 保留、整半镜像与分割部件是分支，不能把相邻行都解释为同一父状态的直接收益。`[opinion]`", ""]
    for key in history:
        case = cases[key]
        m = case.metrics
        lines += [f"### {case.title}", "", f"{case.action} `[verified: evidence.json cases.{key} sources；所列原报告]`", "",
            f"- 结果：{m['islands']} 岛，{m.get('tiles') if m.get('tiles') is not None else '未核验'} 图块，{m['groups']} 共享组；{case.status}。`[verified: evidence.json cases.{key}]`",
            f"- 继续处理快照：`{case.snapshot}`。`[verified: evidence.json cases.{key} state_hash]`"]
        if case.model:
            lines.append(f"- 原模型入口：[打开模型]({case.model.as_uri()})。`[verified: evidence.json cases.{key} model]`")
        lines += ["- 本轮预览：" + f"[颜色](media/{key}/basecolor.png) / [棋盘格](media/{key}/checker.png) / [UV](media/{key}/uv.png)。`[verified: evidence.json cases.{key} images]`", ""]
    lines += ["## 保留的失败经验", "",
        "- 早期 203 岛是修复父状态，不是原始 121 岛；169／150 不能描述为从原始岛数下降。`[verified: docs/results/dragon-component-reuse.md L7–L13；records/components_agent_paired]`",
        "- 原 UV 路线保留可用冲突，117／98 仍为 diagnostic；颜色重建通过不覆盖接缝／渲染拒绝。`[verified: docs/results/dragon-raw-uv-reuse.md L14–L36；records/raw121_agent_paired]`",
        "- 整半镜像先改变几何，再对该新几何做共享／无共享；合岛、改模、排布和缩图收益分别记录。`[verified: docs/results/dragon-whole-half-reuse.md L12–L24；records/whole_mirror]`",
        "- 本轮额外删 68 面和 15 面翼部小片造成缺口，已回退；只删除新变体中 5 张孤立面。`[verified: docs/results/dragon-parts-clean-final.md L20–L23]`",
        "- 原几何共享尝试失败后保留独占；局部镜像共享最终保留 7 组。1K 是未完整验收的诊断附件。`[verified: docs/results/dragon-parts-clean-final.md L31–L38]`", "",
        "## 原始报告入口", ""]
    reports = ["dragon-final-workflow-retrospective", "dragon-component-reuse", "dragon-raw-uv-reuse", "dragon-whole-half-reuse", "dragon-uv-island-merge", "dragon-packing-search", "dragon-midplane-cleanup", "dragon-wholebody-refine", "dragon-paper-baselines", "dragon-segmented-parts", "dragon-parts-clean-final"]
    for stem in reports:
        path = ROOT / f"docs/results/{stem}.md"
        target = output / "records/reports" / path.name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
        lines.append(f"- [{stem} 原报告副本](records/reports/{path.name}) · [本机原入口]({path.as_uri()})")
    for suffix in ["csv", "json", "md"]:
        shutil.copy2(BASE / f"with-original-input/all-metrics.{suffix}", output / f"baseline-all-metrics.{suffix}")
    lines += ["", "记录内原模型与原报告链接指向本机 checkout；ZIP 自带对照图、预览素材、CSV 与引用记录，不打包全部历史模型和运行缓存。`[verified: manifest.json files]`", "",
              "Verification: 见 verification.json；各图生成后的目视结果单列，历史验收状态保持原记录。"]
    (output / "制作记录.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    fields = ["key", "title", "family", "faces", "islands", "tiles", "groups", "coverage", "resolution", "payload_bytes", "accepted", "status", "snapshot", "model"]
    with (output / "metrics.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for case in cases.values():
            writer.writerow({"key": case.key, "title": case.title, "family": case.family, **{k:case.metrics.get(k) for k in fields[3:10]},
                             "payload_bytes": case.metrics.get("payload_bytes"), "accepted": case.accepted, "status": case.status,
                             "snapshot": str(case.snapshot), "model": str(case.model) if case.model else ""})
    rows = []
    for case in cases.values():
        m = case.metrics
        model = f'<a href="{html.escape(case.model.as_uri())}">原模型</a>' if case.model else "—"
        rows.append(f'<tr data-family="{html.escape(case.family)}"><td>{html.escape(case.title)}</td><td>{html.escape(case.family)}</td><td>{m["islands"]}</td><td>{m.get("tiles") if m.get("tiles") is not None else "—"}</td><td>{html.escape(case.status)}</td><td>{html.escape(case.action)}</td><td>{model} · <a href="media/{case.key}/checker.png">棋盘格</a> · <a href="media/{case.key}/uv.png">UV</a></td></tr>')
    galleries = "".join(f'<section><h2>{title}</h2><a href="{stem}.png" target="_blank"><img src="{stem}.png" alt="{title}"></a><p><a href="{stem}.png">打开完整 PNG</a> · <a href="{stem}.pdf">PDF</a></p></section>' for stem, title in [
        ("all-methods-basecolor", "全部 baseline 与分 part · BaseColor"), ("all-methods-checker-uv", "全部 baseline 与分 part · 棋盘格和 UV"),
        ("history-basecolor", "历史制作分支 · BaseColor"), ("history-checker-uv", "历史制作分支 · 棋盘格和 UV")])
    page = '''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>恐龙制作记录与 baseline</title>
<style>body{font:16px/1.7 "Microsoft YaHei",sans-serif;background:#f5f7fa;color:#23435d;margin:28px}h1{font-size:30px}a{color:#087f83}section{background:white;padding:18px;margin:24px 0;border:1px solid #d4dfe8;border-radius:12px}img{width:100%;height:auto}table{border-collapse:collapse;width:100%;background:white;font-size:14px}td,th{padding:9px;border:1px solid #dae2e9;text-align:left}input{font:inherit;padding:7px;width:420px;max-width:90%}p{max-width:1200px}</style>
<h1>恐龙历史制作记录与 baseline 对照</h1><p>八条历史 baseline 固定 11,338 面；新分 part 是独立几何分支。图表保留每轮通过／拒绝，不进行跨几何排名。只补渲染，没有重新展开、排布或烘焙。旧 baseline 为 16 采样／面、96px 探针；新分 part 为 4 采样／面、32px 探针，均检查 LOD0/1/2。</p><p><a href="制作记录.md">详细制作记录</a> · <a href="historical-workflow.pdf">历史制作流程图解</a> · <a href="metrics.csv">全版本指标 CSV</a> · <a href="baseline-all-metrics.csv">历史 baseline 完整指标</a> · <a href="comparison-book.pdf">全部图 PDF</a> · <a href="evidence.json">证据索引</a></p>
''' + galleries + '<h2>制作记录与模型入口</h2><input id="filter" placeholder="筛选方案、几何、状态或操作"><table><thead><tr><th>方案</th><th>几何</th><th>岛</th><th>图块</th><th>状态</th><th>实际操作</th><th>产物</th></tr></thead><tbody>' + "".join(rows) + '''</tbody></table><p>图中“共享成员表面”不等于字节节省；通过仅属于对应轮次的 UV＋BaseColor 协议。完整 PBR、GPU、人工认可独立。原模型链接指向本机路径，归档不包含全部历史模型。</p><script>document.querySelector('#filter').addEventListener('input',e=>{const q=e.target.value.toLowerCase();document.querySelectorAll('tbody tr').forEach(r=>r.hidden=!r.textContent.toLowerCase().includes(q))})</script></html>'''
    (output / "index.html").write_text(page, encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "output/dragon-history-baselines-20261125")
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    cases, overview, history = collect_cases()
    sources = {path for case in cases.values() for path in [case.snapshot, *case.sources, *([case.texture] if case.texture else [])]}
    hashes = {str(path): file_hash(path) for path in sources}
    receipts: dict[str, object] = {}
    for case in cases.values():
        receipts[case.key] = prepare_views(case, output)
        print(f"Views {case.key}: {case.metrics['islands']} islands", flush=True)
    # Hash identity explicitly distinguishes the historical fixed-geometry baseline block.
    baseline_geometry = {mapping(receipts[key])["geometry_hash"] for key in overview[1:9]}
    assert len(baseline_geometry) == 1
    assert mapping(receipts["refine38"])["geometry_hash"] in baseline_geometry
    assert mapping(receipts["mirror_unshared2048"])["geometry_hash"] == mapping(receipts["mirror_shared2048"])["geometry_hash"]
    records: dict[str, object] = {}
    for case in cases.values():
        records[case.key] = {
            "title": case.title, "geometry_family": case.family, "snapshot": str(case.snapshot),
            "state_hash": mapping(receipts[case.key])["state_hash"], "geometry_hash": mapping(receipts[case.key])["geometry_hash"],
            "texture": str(case.texture) if case.texture else None, "model": str(case.model) if case.model else None,
            "metrics": case.metrics, "accepted": case.accepted, "status": case.status, "action": case.action,
            "sources": copy_receipts(case, output), "images": {k:v.relative_to(output).as_posix() for k,v in case.images.items()},
        }
    figures: list[dict[str, object]] = []
    with PdfPages(output / "comparison-book.pdf") as book:
        for stem, keys, mode, historic in [
            ("all-methods-basecolor", overview, "basecolor", False), ("all-methods-checker-uv", overview, "checker", False),
            ("history-basecolor", history, "basecolor", True), ("history-checker-uv", history, "checker", True),
        ]:
            fig, metadata = make_figure([cases[key] for key in keys], output, stem, mode, historic)
            book.savefig(fig)
            figures.append(metadata)
            fig.clear()
            print(f"Figure {stem}: {len(keys)} columns", flush=True)
    write_report(cases, overview, history, output)
    assert hashes == {str(path): file_hash(Path(path)) for path in hashes}
    write_json(output / "evidence.json", {"execution_scope": "Only read retained results and render views; no unwrap, packing, baking or quality revalidation.",
        "cameras": CAMERAS, "overview_order": overview, "history_order": history, "cases": records, "render_receipts": receipts,
        "source_sha256": hashes, "figures": figures, "checks": {"historical_baseline_geometry_hash_equal": True,
        "new_mirror_shared_unshared_geometry_hash_equal": True, "bake_snapshot_state_hash_equal": True,
        "all_render_cameras_checked": True, "all_text_within_bounds": True, "sources_unchanged": True}, "visual_review": "PENDING"})
    print({"output": str(output), "cases": len(cases), "history_stages": len(history)}, flush=True)


if __name__ == "__main__":
    main()
