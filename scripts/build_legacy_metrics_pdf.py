"""Append three complete metric tables to the retained six-page visual PDF."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
from pathlib import Path
from typing import cast

from pypdf import PdfReader, PdfWriter
from reportlab.lib.colors import HexColor
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen.canvas import Canvas

from scripts.build_baseline_metrics_table import HIGHER_IS_BETTER, LOWER_IS_BETTER, best_methods, display

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "docs/assets/legacy-assets-reseam/metrics-round02"
BASE_PDF = ROOT / "output/pdf/legacy-assets-comparison/reseam-three-assets-uv-model-comparison.pdf"
LABELS = {"original": "原始参考", "xatlas": "xatlas",
          "optcuts": "OptCuts 无共享", "mirror_shared_final": "ReSeam 镜像共享",
          "mirror_unshared_final": "同镜像几何解除共享"}
WIDTH, HEIGHT, LEFT, LABEL_WIDTH = 1800., 1450., 32., 440.
NAMES = {"chloe": "Chloe 红刺", "energy_blade": "历史能量刀", "dragon": "历史幻想龙"}


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def mapping(value: object) -> dict[str, object]:
    return cast(dict[str, object], value) if isinstance(value, dict) else {}


def save(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def checked_text(canvas: Canvas, x: float, top: float, text: str, width: float,
                 size: float = 13., *, bold: bool = False, color: str = "#233e53") -> None:
    font = "MetricBold" if bold else "MetricRegular"
    if pdfmetrics.stringWidth(text, font, size) > width:
        raise ValueError("PDF_TEXT_OVERFLOW:" + text)
    canvas.setFont(font, size)
    canvas.setFillColor(HexColor(color))
    canvas.drawString(x, HEIGHT - top, text)


def comparison_rows(rows: list[dict[str, object]], asset: str) -> list[dict[str, object]]:
    methods = ["original", *([] if asset == "chloe" else ["xatlas"]),
               "optcuts", "mirror_shared_final", "mirror_unshared_final"]
    resolution = 256 if asset == "chloe" else 2048
    return [next(row for row in rows if row["asset"] == asset and row["method"] == method
                 and (method == "original" or row["resolution"] == resolution)) for method in methods]


def draw_table(canvas: Canvas, asset: str, selected: list[dict[str, object]],
               fields: list[list[str]], page: int) -> None:
    canvas.setPageSize((WIDTH, HEIGHT))
    canvas.setFillColor(HexColor("#f4f6f9"))
    canvas.rect(0, 0, WIDTH, HEIGHT, fill=1, stroke=0)
    checked_text(canvas, LEFT, 48, f"{NAMES[asset]} | 完整45项指标", 1720., 30., bold=True)
    checked_text(canvas, LEFT, 80, "原始与镜像分属不同几何；粗体仅比较同镜像几何的最后两列，不代表数值质量通过。",
                 1720., 15.)
    column_width = (WIDTH - 2 * LEFT - LABEL_WIDTH) / len(selected)
    header_top, row_top, row_height = 108., 176., 25.
    canvas.setFillColor(HexColor("#243e55"))
    canvas.rect(LEFT, HEIGHT - 164, WIDTH - 2 * LEFT, 58, fill=1, stroke=0)
    checked_text(canvas, LEFT + 12, 139, "指标与字段来源", LABEL_WIDTH - 20, 15., bold=True, color="#ffffff")
    for index, row in enumerate(selected):
        x = LEFT + LABEL_WIDTH + index * column_width + 10
        checked_text(canvas, x, header_top + 23, LABELS[str(row["method"])], column_width - 18,
                     14., bold=True, color="#ffffff")
        state = ("原低模无UV；8K仅为高模颜色来源" if row["layout_stage"] == "original_low_no_uv" else
                 "原生UV；排布拒绝" if row["layout_stage"] == "native_pack_rejected" else
                 "原作者UV；不记零重建误差" if row["original"] else f"{row['resolution']} px | diagnostic")
        checked_text(canvas, x, header_top + 44, state, column_width - 18, 10., color="#d8e5ee")
    assert len(fields) == 45
    for number, (key, title, kind) in enumerate(fields):
        top = row_top + number * row_height
        if number % 2 == 0:
            canvas.setFillColor(HexColor("#ffffff"))
            canvas.rect(LEFT, HEIGHT - top - row_height + 4, WIDTH - 2 * LEFT, row_height, fill=1, stroke=0)
        if key in LOWER_IS_BETTER and "↓" not in title:
            title += " ↓"
        elif key in HIGHER_IS_BETTER and "↑" not in title and kind != "bool":
            title += " ↑"
        checked_text(canvas, LEFT + 10, top + 9, title, LABEL_WIDTH - 20, 13.)
        checked_text(canvas, LEFT + 10, top + 21, f"[verified: summary.json values.{key}]",
                     LABEL_WIDTH - 20, 7.5, color="#677e90")
        pair = {str(row["method"]): mapping(row["values"])[key] for row in selected[-2:]}
        bold_methods = best_methods(key, kind, pair)
        for index, row in enumerate(selected):
            text = display(mapping(row["values"])[key], kind)
            checked_text(canvas, LEFT + LABEL_WIDTH + index * column_width + 10, top + 13,
                         text, column_width - 18, 14., bold=str(row["method"]) in bold_methods)
    notes = [
        "—：无原始低模UV、原生排布未通过、未烘焙或无对应验收；逐项原因见summary.json rows.null_reasons。",
        "原表“新增3D切缝”实际为state.seams总长，本页改称“记录切缝”；不表示相对作者UV的净新增。",
        "AO：seed17、1000点／样本集、每点8射线；面积畸变逐面倍率截10；镜像另列全方向K和仅正向K计入面数。",
        "所有历史综合数值拒绝状态保持原样。颜色／最差视图误差来自已有LOD 0/1/2独立验收，不重新烘焙。",
        "来源：docs/assets/legacy-assets-reseam/metrics-round02/summary.json；完整39个条目及全部尺寸见all-metrics.csv。",
    ]
    for number, note in enumerate(notes):
        checked_text(canvas, LEFT, 1324 + number * 21, note, WIDTH - 2 * LEFT, 12., color="#526a7d")
    checked_text(canvas, WIDTH - 105, 1430, f"{page} / 9", 80., 12.)
    key = "metrics_" + asset
    canvas.bookmarkPage(key)
    canvas.addOutlineEntry(NAMES[asset] + " - 45项指标", key)
    canvas.showPage()


def markdown_report(data: Path, rows: list[dict[str, object]], fields: list[list[str]]) -> str:
    relative = "../assets/legacy-assets-reseam/metrics-round02/"
    text = ["# 三模型45项UV指标补测", "",
            ("本轮对原始参考及36个方法／尺寸条目补测，共39行、45项字段；未生成新UV、贴图或数值质量结果。"
            "`[verified: summary.json row_count/new_bakes/new_quality_scores]`"), "",
            (f"[全部尺寸CSV]({relative}all-metrics.csv) · [原始数值与缺失原因]({relative}summary.json) · "
            f"[锁定协议]({relative}protocol.json)"), ""]
    for asset, name in NAMES.items():
        selected = comparison_rows(rows, asset)
        text += [f"## {name}", "", "|指标|" + "|".join(LABELS[str(row["method"])] for row in selected) + "|来源|",
                 "|" + "|".join("---" for _ in range(len(selected) + 2)) + "|"]
        for key, title, kind in fields:
            cells = [display(mapping(row["values"])[key], kind) for row in selected]
            text.append("|" + "|".join([title, *cells, f"`[verified: summary.json rows.values.{key}]`"]) + "|")
        text += ["", (f"[本模型CSV]({relative}{asset}-comparison.csv)。"
                 "`[verified: 对应CSV与summary.json]`"), ""]
    text += ["## 指标解释与边界", "",
             ("原低模无UV时，UV面积、畸变、密度等不适用；8K只表示高模颜色来源的图像边长。"
             "失败的xatlas原生UV可算形状指标，其最终排布覆盖和颜色重建误差保持空值。"
             "`[verified: summary.json rows.layout_stage/null_reasons]`"), "",
             ("旧表的“新增3D切缝”按state.seams总长度计算，原始作者UV也可能非零。"
             "本轮名称修正为“记录3D切缝”，公式和旧字段名保留，不将它冒称相对原UV的净新增。"
             "`[verified: protocol.json new_seam_scope；baseline/report.py distortion_metrics]`"), "",
             ("原始与镜像的几何不同；共享与解除共享使用同镜像几何。表内保留所有数值，不据此进行跨几何质量排名。"
             "`[verified: summary.json rows.geometry_hash；opinion: 比较边界]`"), "",
             ("AO是固定种子的采样比较分数；无显式切缝时为不适用，不能填成0。"
             "Jaggedness使用原始UV单位，面积畸变逐面倍率截10，K同时报告全方向和仅正向值与计入面数。"
             "`[verified: protocol.json；summary.json rows.metrics.paper/distortion]`"), "",
             ("Chloe与能量刀当前圆盘布局的全方向K约为9.97、11.88，面积畸变约3.71、4.63；"
             "相同镜像几何解除共享后这些数值基本不变。圆盘紧凑度接近1，同时保留较高畸变。"
             "`[verified: summary.json 对应共享/解除共享行values]`"), "",
             ("旧的外观预览判断和数值拒绝记录保持原样；新增指标不将它们升级为工业质量或同质量缩图通过。"
             "`[verified: summary.json all_historical_quality_states_preserved；opinion: 验收范围]`"), ""]
    return "\n".join(text)


def build(data: Path, output: Path) -> None:
    summary_path = data / "summary.json"
    before = {str(path): digest(path) for path in (summary_path, BASE_PDF)}
    summary = mapping(json.loads(summary_path.read_text(encoding="utf-8")))
    assert summary["row_count"] == 39 and summary["all_sources_unchanged"] is True
    rows = cast(list[dict[str, object]], summary["rows"])
    fields = cast(list[list[str]], mapping(summary["protocol"])["fields"])
    assert len(fields) == 45
    output.mkdir(parents=True, exist_ok=False)
    pdfmetrics.registerFont(TTFont("MetricRegular", "C:/Windows/Fonts/msyh.ttc"))
    pdfmetrics.registerFont(TTFont("MetricBold", "C:/Windows/Fonts/msyhbd.ttc"))
    buffer = io.BytesIO()
    canvas = Canvas(buffer, pageCompression=1)
    for page, asset in enumerate(NAMES, start=7):
        draw_table(canvas, asset, comparison_rows(rows, asset), fields, page)
    canvas.save()
    buffer.seek(0)
    base = PdfReader(BASE_PDF)
    counters = io.BytesIO()
    counter_canvas = Canvas(counters, pageCompression=1)
    for number, source_page in enumerate(base.pages, start=1):
        width, height = float(source_page.mediabox.width), float(source_page.mediabox.height)
        counter_canvas.setPageSize((width, height))
        counter_canvas.setFillColor(HexColor("#f4f6f9"))
        counter_canvas.rect(width - 120, 8, 104, 24, fill=1, stroke=0)
        counter_canvas.setFillColor(HexColor("#52677c"))
        counter_canvas.setFont("MetricRegular", 11)
        counter_canvas.drawRightString(width - 20, 17, f"{number} / 9")
        counter_canvas.showPage()
    counter_canvas.save()
    counters.seek(0)
    counter_pdf = PdfReader(counters)
    for source_page, counter_page in zip(base.pages, counter_pdf.pages, strict=True):
        source_page.merge_page(counter_page)
    writer = PdfWriter()
    writer.append(base)
    writer.append(buffer)
    writer.add_metadata({"/Title": "ReSeam 三模型渲染、UV与45项指标", "/Author": "ReSeam",
                         "/Subject": "6页真实图像加3页完整指标；原始与镜像分组，历史数值拒绝保留"})
    pdf = output / "reseam-three-assets-with-45-metrics.pdf"
    with pdf.open("wb") as stream:
        writer.write(stream)
    reader = PdfReader(pdf)
    assert len(reader.pages) == 9
    for page in reader.pages[6:]:
        extracted = page.extract_text() or ""
        assert "记录3D切缝长度" in extracted and "颜色P95" in extracted
        assert len(page.images) == 0
    assert all(digest(Path(path)) == expected for path, expected in before.items())
    report = ROOT / "docs/results/legacy-assets-45-metrics.md"
    report.write_text(markdown_report(data, rows, fields), encoding="utf-8")
    save(output / "evidence.json", {"pdf": pdf.name, "pages": 9, "visual_pages": 6,
        "metric_pages": 3, "metric_fields": 45, "all_cases": 39, "sources_unchanged": True,
        "source_sha256": before, "metric_source": summary_path.relative_to(ROOT).as_posix(),
        "pdf_sha256": digest(pdf), "historical_quality_changed": False,
        "bold_scope": "numerical extrema only within the same-geometry shared/unshared pair; no quality ranking"})
    print(json.dumps({"pdf": str(pdf), "pages": 9, "report": str(report)}, ensure_ascii=False))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=DATA)
    parser.add_argument("--output", type=Path, default=ROOT / "output/pdf/legacy-assets-with-metrics")
    args = parser.parse_args()
    build(args.data.resolve(), args.output.resolve())


if __name__ == "__main__":
    main()
