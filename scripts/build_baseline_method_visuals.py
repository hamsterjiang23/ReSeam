"""Explain baseline mechanisms using diagrams and actual retained dragon assets."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from itertools import pairwise
from pathlib import Path
from typing import cast

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "docs/assets/dragon-paper-baselines"
OUT = DATA / "method-visuals"
BG, NAVY, INK, MUTED, LINE = "#F2F6FA", "#15344E", "#29485F", "#667F92", "#D8E3ED"
W, H = 1800, 1520


@dataclass(frozen=True)
class Method:
    key: str
    title: str
    family: str
    color: str
    headline: str
    steps: tuple[str, str, str]
    icons: tuple[str, str, str]
    setting: str


METHODS = [
    Method("flatten_anything", "FlattenAnything / FAM", "神经映射", "#7955AA", "让网络学习三维表面到UV的映射",
           ("表面点采样", "当前资产训练", "原顶点UV推理"), ("points", "network", "tile"),
           "10,000点 / 10,000次迭代 / seed 17"),
    Method("optcuts", "OptCuts", "几何优化", "#3B78AB", "切缝与二维展开一起优化",
           ("三角网格", "切缝＋参数化", "畸变约束UV"), ("mesh", "cut", "tile"),
           "官方后端 / λ = 0.999 / 畸变界 4.1 / seed 0"),
    Method("xatlas", "xatlas", "几何工具", "#3B78AB", "自动分图，再展开与排布",
           ("输入网格", "自动分成charts", "参数化与排布"), ("mesh", "parts", "tile"),
           "官方Python绑定 / 默认chart options / 保留原面角身份"),
    Method("blender_smart", "Blender Smart UV", "DCC原生工具", "#3B78AB", "按面夹角分组，沿平均法线投影",
           ("网格面夹角", "角度阈值分组", "分组投影UV"), ("angle", "parts", "tile"),
           "Blender 4.5.11 / Angle Limit 66° / 原生间距 0.03"),
    Method("partuv", "PartUV", "部件先验", "#8A64AC", "先得到部件层次，再递归展开",
           ("预训练PartField", "部件层次树", "畸变约束展开"), ("parts", "tree", "tile"),
           "PartUV 0.1.2 / 预训练PartField / 阈值 1.25 / seed 42"),
    Method("meshquery_project", "MeshQuery 项目实现", "Agent规划", "#C57E30", "Agent选接缝，工具展开，反馈修订",
           ("查询网格属性", "Agent切缝计划", "展开＋反馈"), ("query", "agent", "feedback"),
           "项目工具链 / 4个环带补切口 / Minimum Stretch"),
    Method("reseam_shared", "ReSeam 共享", "区域共享", "#078B91", "精确镜像堆叠，再真实合岛与排布",
           ("授权镜像变体", "精确左右UV共享", "合岛＋紧凑排布"), ("mirror", "shared", "tile"),
           "既有完成布局 / 精确面角对应 / 共用40个实际图块"),
    Method("reseam_unshared", "ReSeam 解除共享", "对照路径", "#738599", "保留相同制作布局，拆开重复占位",
           ("同一完成布局", "解除共享关系", "分别排布烘焙"), ("shared", "unshared", "tile"),
           "同几何、同来源目标 / 用于分离堆叠贡献"),
]


def font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype("C:/Windows/Fonts/msyhbd.ttc" if bold else "C:/Windows/Fonts/msyh.ttc", size)


def text(draw: ImageDraw.ImageDraw, xy: tuple[int, int], value: str, size: int,
         color: str = INK, bold: bool = False, width: int = 1700) -> None:
    f = font(size, bold)
    assert draw.textlength(value, font=f) <= width, f"Text overflow: {value}"
    draw.text(xy, value, fill=color, font=f)


def arrow(draw: ImageDraw.ImageDraw, start: tuple[int, int], end: tuple[int, int], color: str) -> None:
    draw.line((start, end), fill=color, width=5)
    x, y = end
    draw.polygon([(x, y), (x-15, y-10), (x-15, y+10)], fill=color)


def icon(draw: ImageDraw.ImageDraw, kind: str, x: int, y: int, color: str) -> None:
    if kind == "network":
        layers = [[(x, y+25), (x, y+75)], [(x+45, y+10), (x+45, y+50), (x+45, y+90)],
                  [(x+90, y+25), (x+90, y+75)]]
        for first, second in pairwise(layers):
            for a in first:
                for b in second:
                    draw.line((a, b), fill=LINE, width=3)
        for layer in layers:
            for a, b in layer:
                draw.ellipse((a-7, b-7, a+7, b+7), fill=color)
    elif kind == "points":
        for i in range(21):
            a = i * 2.4
            radius = 8 + math.sqrt(i) * 8
            u, v = x+48+math.cos(a)*radius, y+50+math.sin(a)*radius
            draw.ellipse((u-4, v-4, u+4, v+4), fill=color)
    elif kind in {"mesh", "cut", "angle"}:
        points = [(x+3, y+28), (x+47, y+5), (x+90, y+30), (x+87, y+79), (x+43, y+102), (x+3, y+78)]
        draw.polygon(points, outline=color, width=4)
        for i in [0, 2, 4]:
            draw.line((points[i], points[(i+2) % 6]), fill=color, width=2)
        if kind == "cut":
            draw.line([(x+47, y+5), (x+38, y+36), (x+60, y+68), (x+43, y+102)], fill="#D76F55", width=7)
        if kind == "angle":
            draw.arc((x+22, y+33, x+78, y+85), 205, 330, fill="#D76F55", width=5)
    elif kind == "parts":
        for poly, shade in [([(0, 8), (40, 0), (48, 39), (8, 47)], color),
                            ([(54, 10), (90, 20), (91, 58), (59, 52)], "#80AEBF"),
                            ([(12, 58), (47, 50), (76, 79), (25, 99)], "#B3C9D9")]:
            draw.polygon([(x+a, y+b) for a, b in poly], fill=shade)
    elif kind == "tree":
        draw.line([(x+47, y+10), (x+47, y+43), (x+15, y+43), (x+15, y+85)], fill=color, width=4)
        draw.line([(x+47, y+43), (x+82, y+43), (x+82, y+85)], fill=color, width=4)
        for a, b in [(47, 10), (15, 85), (82, 85)]:
            draw.rounded_rectangle((x+a-12, y+b-10, x+a+12, y+b+10), 4, fill=color)
    elif kind in {"query", "agent", "feedback"}:
        draw.rounded_rectangle((x+2, y+12, x+96, y+86), 12, outline=color, width=4)
        if kind == "query":
            for row in [30, 47, 64]:
                draw.line((x+20, y+row, x+79, y+row), fill=color, width=4)
        elif kind == "agent":
            draw.ellipse((x+22, y+34, x+34, y+46), fill=color)
            draw.ellipse((x+64, y+34, x+76, y+46), fill=color)
            draw.line((x+33, y+65, x+66, y+65), fill=color, width=4)
        else:
            draw.arc((x+22, y+26, x+76, y+74), 20, 310, fill=color, width=5)
            draw.polygon([(x+76, y+43), (x+64, y+45), (x+73, y+56)], fill=color)
    else:
        if kind == "mirror":
            draw.polygon([(x+8, y+83), (x+37, y+12), (x+37, y+84)], outline=color, width=4)
            draw.polygon([(x+87, y+83), (x+57, y+12), (x+57, y+84)], outline=color, width=4)
            draw.line((x+47, y, x+47, y+100), fill=LINE, width=3)
        elif kind == "shared":
            draw.rounded_rectangle((x+6, y+10, x+75, y+81), 8, outline="#8CBABF", width=4)
            draw.rounded_rectangle((x+25, y+28, x+94, y+99), 8, outline=color, width=5)
            draw.line((x+39, y+51, x+78, y+86), fill=color, width=3)
        elif kind == "unshared":
            draw.rounded_rectangle((x, y+10, x+42, y+86), 7, outline=color, width=4)
            draw.rounded_rectangle((x+57, y+10, x+99, y+86), 7, outline=color, width=4)
        else:
            draw.rounded_rectangle((x+5, y+5, x+95, y+95), 8, outline=color, width=4)
            draw.polygon([(x+19, y+20), (x+75, y+18), (x+51, y+55), (x+24, y+79)], outline=color, width=3)


def paste_asset(canvas: Image.Image, path: Path, bounds: tuple[int, int, int, int]) -> None:
    x, y, width, height = bounds
    with Image.open(path) as image:
        picture = image.convert("RGB")
        picture.thumbnail((width, height), Image.Resampling.LANCZOS)
        canvas.paste(picture, (x+(width-picture.width)//2, y+(height-picture.height)//2))


def state_chip(draw: ImageDraw.ImageDraw, x: int, y: int, title: str, value: object) -> None:
    label = "通过" if value is True else "拒绝" if value is False else "未评估"
    background, foreground = ("#E0F1E9", "#246C53") if value is True else ("#FAE9E4", "#A44D39") if value is False else ("#E9EEF3", MUTED)
    draw.rounded_rectangle((x, y, x+540, y+60), 15, fill=background)
    text(draw, (x+25, y+13), title+"  "+label, 28, foreground, width=490)


def make_card(method: Method, row: dict[str, object], output: Path) -> list[Path]:
    canvas = Image.new("RGB", (W, H), BG)
    draw = ImageDraw.Draw(canvas)
    draw.rounded_rectangle((15, 15, W-15, H-15), 30, fill="white", outline=LINE, width=2)
    draw.rounded_rectangle((50, 44, 64, 105), 6, fill=method.color)
    text(draw, (88, 40), method.title, 56, NAVY, True, width=1250)
    draw.rounded_rectangle((1430, 46, 1740, 102), 20, fill=method.color)
    text(draw, (1453, 57), method.family, 27, "white", True, width=270)
    text(draw, (50, 130), method.headline, 34, INK)
    text(draw, (50, 193), "机制示意", 21, MUTED)
    for index, (step, kind) in enumerate(zip(method.steps, method.icons, strict=True)):
        x = 50 + index * 600
        draw.rounded_rectangle((x, 235, x+500, 375), 20, fill=BG)
        icon(draw, kind, x+22, 251, method.color)
        text(draw, (x+145, 284), step, 28, NAVY, True, width=335)
        if index < 2:
            arrow(draw, (x+520, 305), (x+573, 305), method.color)
    artifacts = row["delivery_artifacts"]
    assert isinstance(artifacts, dict)
    model_key = "color" if "color" in artifacts else "front"
    model = DATA / str(artifacts[model_key]).replace("\\", "/")
    uv = DATA / str(artifacts["uv"]).replace("\\", "/")
    for x, heading, asset in [(50, "实际BaseColor模型" if model_key == "color" else "实际棋盘格 · 无重烘焙输出", model),
                              (930, "共同排布UV" if row["pack_status"] == "OK" else "原生UV · 排布拒绝", uv)]:
        draw.rounded_rectangle((x, 405, x+820, 1115), 22, fill=BG)
        text(draw, (x+25, 427), heading, 28, NAVY, True, width=770)
        paste_asset(canvas, asset, (x+30, 478, 760, 600))
    tiles = str(row["tiles"]) if row["tiles"] is not None else "未核验"
    fraction = row["actual_shared_surface_fraction"]
    sharing = f"{fraction*100:.0f}%" if isinstance(fraction, (int, float)) else "未核验"
    for x, label, number in [(50, "UV连通岛", str(row["charts"])), (650, "独立图块", tiles),
                              (1250, "实际参与共享表面", sharing)]:
        text(draw, (x, 1150), label, 27, MUTED, width=500)
        text(draw, (x, 1197), number, 62, method.color, True, width=500)
    if row["pack_status"] != "OK":
        reason = f"排布拒绝 / {row['zero_uv_area_faces']}个零UV面积面 / 未进入最终质量验收"
    elif method.key == "reseam_shared":
        reason = "80岛仍各自连通 / 实际共用40图块 / 重复UV占位节省50%"
    elif row["quality_accepted"] is True:
        reason = "本轮UV＋BaseColor质量通过 / 人工、GPU与完整PBR另列"
    else:
        reason = "已产生真实贴图与模型 / 最终质量仍拒绝 / 保留诊断结果"
    text(draw, (50, 1285), reason, 26, INK, width=1700)
    for x, title, field in [(50, "颜色", "material_valid"), (630, "接缝", "seams_valid"), (1210, "渲染", "render_valid")]:
        state_chip(draw, x, 1340, title, row[field])
    text(draw, (50, 1420), method.setting, 25, MUTED, width=1700)
    text(draw, (50, 1477), f"[verified: measurements.json rows.{method.key}]  模型与UV为实际输出；上方图标为机制示意。", 17, MUTED, width=1700)
    canvas.save(output)
    return [model, uv]


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    source = DATA / "measurements.json"
    record = json.loads(source.read_text(encoding="utf-8"))
    rows = {row["method"]: cast(dict[str, object], row) for row in record["rows"]}
    assets: set[Path] = {source, ROOT / "baseline/sources.json"}
    cards: list[Path] = []
    for method in METHODS:
        path = OUT / f"{method.key}.png"
        assets.update(make_card(method, rows[method.key], path))
        cards.append(path)
    width, height = 3776, 1890
    overview = Image.new("RGB", (width, height), BG)
    draw = ImageDraw.Draw(overview)
    text(draw, (52, 26), "UV Baseline：看处理方式，也看真实结果", 64, NAVY, True, width=width-104)
    text(draw, (52, 111), "6项方法 ＋ 2份ReSeam对照 ｜ 同一11,338面镜像龙 ｜ 2048 BaseColor ｜ 原始Tripo另作跨几何参考", 32, MUTED, width=width-104)
    for index, path in enumerate(cards):
        with Image.open(path) as card:
            overview.paste(card.resize((900, 760), Image.Resampling.LANCZOS), (52+(index % 4)*924, 190+(index//4)*784))
    text(draw, (52, 1785), "读法：机制流程 → 实际模型 / UV → 岛与图块 → 颜色 / 接缝 / 渲染状态", 31, NAVY, True, width=width-104)
    text(draw, (52, 1835), "失败项保留原生输出；共享率与最终质量分别判断。基线未追加ReSeam共享后处理，0%不表示不能组合共享。", 26, MUTED, width=width-104)
    overview.save(OUT / "baseline-methods-overview.png")
    inputs = [{"path": p.relative_to(ROOT).as_posix(), "sha256": hashlib.sha256(p.read_bytes()).hexdigest()} for p in sorted(assets)]
    outputs: list[dict[str, object]] = []
    for path in [*cards, OUT / "baseline-methods-overview.png"]:
        with Image.open(path) as saved:
            size = saved.size
        outputs.append({"path": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "size": size})
    (OUT / "manifest.json").write_text(json.dumps({"input_assets": inputs, "outputs": outputs,
        "methods": [m.key for m in METHODS], "text_width_checks": "PASS", "visual_review": "PENDING",
        "new_geometry_or_bakes": False}, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    links = "\n\n".join(f"[{m.title}]({m.key}.png)" for m in METHODS)
    (OUT / "README.md").write_text("# UV Baseline可视化介绍\n\n![八条路径概览](baseline-methods-overview.png)\n\n"+links+
        "\n\n卡片中的模型、UV和指标均来自measurements.json绑定的实际资产。流程图标仅作机制说明；验收状态保持原结论。`[verified: manifest.json input_assets；measurements.json rows]`\n", encoding="utf-8")
    print(f"CREATED overview + {len(cards)} method cards in {OUT}")


if __name__ == "__main__":
    main()
