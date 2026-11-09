"""Build the three reference-style baseline introduction figures."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import cast

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "docs/assets/dragon-paper-baselines"
OUT = DATA / "overview-figures"
NAVY, INK, MUTED = "#16354D", "#293C4C", "#627A8E"
BLUE, GREEN, PURPLE, ORANGE, RED = "#3478CB", "#57994A", "#8965AF", "#CE8739", "#D26969"
PAPER_FONT = Path("C:/Windows/Fonts/timesi.ttf")


@dataclass(frozen=True)
class Method:
    key: str
    year: str
    name: str
    title: str
    publication: str
    url: str
    use: str
    category: str
    evidence: str
    native: str


METHODS = [
    Method("optcuts", "2018", "OptCuts",
           "OptCuts: Joint Optimization of Surface Cuts and Parameterization",
           "TOG / SIGGRAPH Asia 2018 · P1", "https://www.cs.ubc.ca/labs/imager/tr/2018/OptCuts/",
           "调用固定官方源码对应的编译后端；项目负责输入输出与原面角对应。实际二进制哈希已记录。",
           "公开源码＋调用适配", "baseline/runner.py optcuts分支；sources.json OptCuts", "切缝与参数化联合优化"),
    Method("flatten_anything", "2024", "FlattenAnything / FAM",
           "Flatten Anything: Unsupervised Neural Surface Parameterization",
           "NeurIPS 2024 · P2", "https://arxiv.org/abs/2405.14633",
           "调用官方模型与train_fam训练循环；在当前资产训练后推理原顶点UV。项目仅适配IO与扩展装载。",
           "公开源码＋调用适配", "adapters/flatten_anything.py main；sources.json FlattenAnything", "当前资产训练神经映射"),
    Method("partuv", "2025", "PartUV", "PartUV: Part-Based UV Unwrapping of 3D Meshes",
           "按arXiv首次公开年份；2026年修订 · P3", "https://arxiv.org/abs/2511.16659",
           "调用官方PartUV 0.1.2与预训练PartField，执行预处理和pipeline_numpy；项目适配坐标与面角导入。",
           "官方包＋预训练权重", "adapters/partuv_worker.py main；measurements.json partuv", "部件层次＋递归几何展开"),
    Method("meshquery_project", "2026", "MeshQuery 项目实现",
           "MeshQuery: Agentic Seam Planning for UV Parametrization",
           "arXiv首次公开2026-10-01 · P4", "https://arxiv.org/abs/2610.02507",
           "归档检索未找到可核实的官方代码；用ReSeam网格查询、接缝计划和反馈工具实现论文公开机制。",
           "论文机制的项目实现", "baseline/MeshQuery/README.md；sources.json meshquery", "Agent规划切缝＋展开反馈"),
    Method("xatlas", "工具", "xatlas", "Mesh parameterization / UV unwrapping library",
           "官方仓库说明；不填论文发表年份 · P5", "https://github.com/jpcy/xatlas",
           "调用xatlas Python绑定的parametrize，使用默认chart选项；核验顶点映射和原三角面角身份。",
           "公开库＋Python绑定", "baseline/runner.py xatlas分支；sources.json xatlas-python", "自动分图、参数化与排布"),
    Method("blender_smart", "工具", "Blender Smart UV", "Smart UV Project — Blender Manual",
           "本轮运行Blender 4.5.11；工具而非独立论文 · P6",
           "https://docs.blender.org/manual/en/4.5/modeling/meshes/editing/uv.html#smart-uv-project",
           "直接调用Blender 4.5.11原生Smart UV Project，Angle Limit为66°；适配层负责隔离运行与UV导出。",
           "DCC原生工具＋适配", "baseline/runner.py blender_smart分支；measurements.json native_settings", "按面夹角分组与投影"),
    Method("reseam_shared", "本项目", "ReSeam 共享", "整半镜像共享、真实合岛与紧凑排布工作流",
           "项目方法；本轮使用既有完成布局，不对应独立外部论文 · D1", "",
           "复用已完成的镜像龙布局，以精确面角对应堆叠左右UV，再共同排布和真实重烘焙。历史制作成本另列。",
           "本项目流程／既有布局", "measurements.json reseam_shared；dragon-baselines-overview.md", "精确堆叠＋合岛＋共同排布"),
    Method("reseam_unshared", "本项目", "ReSeam 解除共享", "同一完成布局的解除共享对照",
           "项目消融对照；不作为新的独立展开算法 · D2", "",
           "从相同完成几何与来源目标解除共享，分别排布与重烘焙；用于观察堆叠带来的独立图块变化。",
           "本项目对照流程", "measurements.json reseam_unshared；dragon-baselines-overview.md", "相同布局解除共享对照"),
]


class Canvas:
    def __init__(self, width: int, height: int) -> None:
        self.image = Image.new("RGB", (width, height), "white")
        self.draw = ImageDraw.Draw(self.image)
        self.boxes = 0

    def box(self, rect: tuple[int, int, int, int], color: str = "#F5F8FC",
            outline: str | None = None, radius: int = 15) -> None:
        self.draw.rounded_rectangle(rect, radius, fill=color, outline=outline, width=2)

    def write(self, value: str, x: int, y: int, width: int, size: int = 34,
              color: str = INK, bold: bool = False, height: int = 1000,
              paper: bool = False) -> int:
        font = ImageFont.truetype(str(PAPER_FONT) if paper else
                                 "C:/Windows/Fonts/msyhbd.ttc" if bold else "C:/Windows/Fonts/msyh.ttc", size)
        tokens = re.findall(r"[A-Za-z0-9_./:+—-]+|[^\S\n]+|\n|[^\x00-\x7F]|.", value)
        lines: list[str] = []
        line = ""
        for token in tokens:
            if token == "\n":
                lines.append(line.rstrip())
                line = ""
            elif self.draw.textlength(line + token, font=font) > width:
                assert line, f"Token too long: {token}"
                lines.append(line.rstrip())
                line = token.lstrip()
            else:
                line += token if line else token.lstrip()
        if line:
            lines.append(line.rstrip())
        line_height = round(size * 1.43)
        assert len(lines) * line_height <= height, (value, len(lines), height)
        assert 0 <= x and x + width <= self.image.width and 0 <= y
        assert y + len(lines) * line_height <= self.image.height
        for i, content in enumerate(lines):
            assert self.draw.textlength(content, font=font) <= width
            self.draw.text((x, y + i * line_height), content, font=font, fill=color)
        self.boxes += 1
        return y + len(lines) * line_height

    def line(self, points: list[tuple[int, int]], color: str, width: int = 3) -> None:
        self.draw.line(points, fill=color, width=width, joint="curve")

    def save(self, name: str) -> Path:
        path = OUT / name
        self.image.save(path)
        return path


def heading(c: Canvas, title: str, subtitle: str) -> None:
    c.write(title, 75, 38, c.image.width-150, 66, NAVY, True, height=100)
    c.write(subtitle, 78, 143, c.image.width-156, 30, MUTED, height=90)


def figure_papers() -> tuple[Path, int]:
    c = Canvas(3000, 1480)
    heading(c, "UV Baseline：方法与论文 / 工具来源", "按论文首次公开或发表年份列出；工具与本项目对照单独标识")
    c.box((65, 236, 2935, 310), "#EAF1FC")
    for x, label, width in [(90, "年份 / 类型", 240), (380, "方法", 600), (1080, "论文 / 官方说明", 1800)]:
        c.write(label, x, 250, width, 33, BLUE, True, height=55)
    for i, method in enumerate(METHODS):
        y = 320 + i * 122
        if i % 2 == 0:
            c.box((65, y, 2935, y+119), "#F8FAFD", radius=4)
        c.write(method.year, 90, y+22, 240, 37, BLUE, True, height=70)
        c.write(method.name, 380, y+22, 650, 36, NAVY, True, height=75)
        c.write(method.title, 1080, y+8, 1800, 37, INK, height=57, paper=i < 6)
        c.write(method.publication, 1080, y+70, 1800, 25, MUTED, height=45)
    c.line([(65, 1310), (2935, 1310)], "#D9E3EC", 2)
    c.write("[verified: P1–P4 官方论文标题与提交历史；P5 官方README；P6 本轮工具记录；D1/D2 measurements.json]",
            80, 1335, 2840, 25, MUTED, height=45)
    c.write("来源链接与固定代码版本见同目录 sources.json；不将工具版本或项目实验年份当成论文发表年份。",
            80, 1390, 2840, 26, MUTED, height=45)
    return c.save("01-methods-and-papers.png"), c.boxes


def figure_implementation() -> tuple[Path, int]:
    c = Canvas(3000, 2130)
    heading(c, "UV Baseline：实际怎么调用，哪些是项目实现", "区分官方源码、官方包、DCC原生工具与论文机制实现；来源分类不代表质量排名")
    c.box((65, 236, 2935, 316), "#EAF1FC")
    for x, value, width in [(90, "方法", 500), (650, "本轮使用方式", 1530), (2270, "实现分类", 590)]:
        c.write(value, x, 255, width, 34, BLUE, True, height=60)
    for i, method in enumerate(METHODS):
        y = 330 + i * 204
        shade = "#F8FAFD" if i % 2 == 0 else "white"
        c.box((65, y, 2935, y+200), shade, radius=3)
        c.write(method.name, 90, y+23, 500, 37, NAVY, True, height=120)
        c.write(method.use, 650, y+14, 1500, 34, INK, height=153)
        category_color = ORANGE if method.key == "meshquery_project" else "#078B91" if method.year == "本项目" else BLUE
        c.box((2240, y+20, 2905, y+155), "#F1F6FA", category_color)
        c.write(method.category, 2270, y+40, 600, 34, category_color, True, height=110)
        c.write(f"[verified: {method.evidence}]", 650, y+162, 1500, 21, MUTED, height=35)
    c.line([(65, 1988), (2935, 1988)], "#D9E3EC", 2)
    c.write("Research Track：冻结原生算法输出与失败    →    Asset Track：共同排布、重烘焙、验收与导出",
            85, 2010, 2830, 31, NAVY, True, height=50)
    c.write("官方源码版本与运行包版本分别记录；MeshQuery项目实现不等于作者源码复现；已有ReSeam布局不用于等预算速度排名。",
            85, 2071, 2830, 25, MUTED, height=45)
    return c.save("02-implementation-and-provenance.png"), c.boxes


def branch(c: Canvas, top: int, height: int, name: str, note: str, color: str) -> None:
    center = top + height // 2
    c.line([(420, center), (465, center), (465, center), (500, center)], color, 4)
    c.box((500, center-69, 900, center+69), "#F7F9FC", color, 20)
    c.write(name, 525, center-47, 350, 35, color, True, height=56)
    c.write(note, 525, center+12, 350, 24, MUTED, height=50)
    c.line([(900, center), (930, center), (930, center), (960, center)], color, 4)
    c.box((960, top, 3340, top+height), "white", color, 18)


def figure_map(rows: dict[str, dict[str, object]]) -> tuple[Path, int]:
    c = Canvas(3420, 3740)
    heading(c, "UV重构与复用：方法、指标、实施与结论导图", "同一完成镜像龙 / 11,338三角面 / 2048 BaseColor；原始Tripo仅作跨几何参考")
    c.box((55, 1510, 365, 1990), NAVY, radius=30)
    c.write("UV\nBaseline\n研究与资产\n制作导图", 86, 1588, 250, 45, "white", True, height=330)
    c.line([(365, 1750), (420, 1750)], "#91A6BA", 4)
    centers = [425, 985, 1550, 2415, 3300]
    c.line([(420, centers[0]), (420, centers[-1])], "#91A6BA", 4)

    branch(c, 245, 360, "方法与来源", "论文 / 工具 / 项目对照", BLUE)
    categories = [
        ("几何与DCC", "OptCuts：切缝＋参数化联合优化\nxatlas：自动分图与排布\nSmart UV：按面夹角分片投影"),
        ("学习先验", "FAM：当前资产训练神经映射\nPartUV：预训练PartField\n＋部件层次递归展开"),
        ("Agent与共享", "MeshQuery项目实现：切缝计划\nReSeam共享：精确堆叠与合岛\nReSeam解除共享：同布局对照"),
    ]
    for i, (label, body) in enumerate(categories):
        x = 990+i*777
        c.box((x, 270, x+750, 535), "#F1F6FE")
        c.write(label, x+24, 288, 700, 33, BLUE, True, height=55)
        c.write(body, x+24, 355, 700, 29, height=150)
    c.write("[verified: P1–P6；baseline/sources.json；runner.py各方法分支]", 990, 554, 2300, 23, MUTED, height=40)

    branch(c, 650, 670, "评估指标", "分项阅读，不合成万能分数", GREEN)
    metric_groups = [
        ("UV组织与复用", "连通岛 / 独立图块 / 共享组分开计数；报告有效共享表面、重复占位节省与复用倍数。"),
        ("接缝与图块形状", "UV与3D切缝长度、接缝AO、Non-disk；紧凑度、凸性、边界锯齿与角度正交度。"),
        ("展开畸变与纹素", "面积畸变D_area、保角K、纹素分配离散度、密度；零面积及方向问题保留跳过数量。"),
        ("外观、载荷与成本", "颜色均值/P95/最大误差、最差视图、接缝与mip；纹理字节、包体、失败和耗时分别记录。"),
    ]
    for i, (label, body) in enumerate(metric_groups):
        y = 675+i*139
        c.box((990, y, 1510, y+118), "#F0F7EC")
        c.write(label, 1015, y+35, 470, 32, GREEN, True, height=60)
        c.box((1530, y, 3310, y+118), "#F8FBF5")
        c.write(body, 1560, y+16, 1720, 30, height=95)
    c.write("[verified: with-original-input/all-metrics.json；paper-metrics-protocol.json]", 990, 1250, 2300, 23, MUTED, height=42)

    branch(c, 1360, 380, "结构与结果检查", "合法交叠与错误交叠分开", PURPLE)
    checks = [
        ("原面角身份", "对应回原始面角；保留来源映射，不能按坐标接近自动焊接。"),
        ("UV结构", "检查零面积、混合方向、非圆盘岛、UV越界与边界闭环。"),
        ("实际复用", "经对应许可的堆叠才计共享；同色标记、候选相似或非法重叠不能充数。"),
    ]
    for i, (label, body) in enumerate(checks):
        y = 1380+i*95
        c.box((990, y, 1480, y+80), "#F5F0FA")
        c.write(label, 1015, y+15, 440, 32, PURPLE, True, height=52)
        c.write(body, 1510, y+15, 1760, 30, height=65)
    c.write("[verified: baseline/runner.py 面角检查；measurements.json 结构与有效复用字段]", 990, 1680, 2300, 23, MUTED, height=40)

    branch(c, 1780, 1270, "逐方法双轨流程", "原生结果与制作结果分离", ORANGE)
    for i, method in enumerate(METHODS):
        y = 1805+i*146
        c.box((990, y, 1580, y+131), "#FCF4E9")
        c.write(method.name, 1015, y+17, 540, 31, NAVY, True, height=100)
        c.box((1600, y, 3305, y+60), "#FFF9F1")
        c.write("Research Track", 1620, y+10, 350, 28, ORANGE, True, height=45)
        c.write(method.native, 1990, y+10, 1280, 29, height=47)
        c.box((1600, y+70, 3305, y+131), "#F3F6FA")
        c.write("Asset Track", 1620, y+80, 350, 28, BLUE, True, height=45)
        r = rows[method.key]
        count = f"{r['charts']}岛"
        if r['tiles'] is not None:
            count += f" / {r['tiles']}图块"
        status = "排布拒绝；无重烘焙输出" if r['pack_status'] != 'OK' else "UV＋颜色流程通过" if r['quality_accepted'] else "已重烘焙；质量仍拒绝"
        c.write(f"{count}；{status}", 1990, y+80, 1280, 29, height=47)
    c.write("[verified: measurements.json各方法行；方法来源与实现方式见前两张图]", 990, 2990, 2300, 23, MUTED, height=40)

    branch(c, 3090, 440, "本例结论与边界", "单龙实测，不外推泛化", RED)
    conclusion = [
        ("碎片与图块", "OptCuts与MQ项目均为80岛；\nReSeam共享为80岛 / 40图块；\n121→80包含镜像几何改造。"),
        ("确有实际共享", "共享表面100%；重复占位节省50%；\n实际复用倍数2×。\n其余基线未追加共享后处理。"),
        ("质量仍独立判断", "OptCuts与MQ项目本轮质量通过；\nReSeam共享颜色/接缝通过，\n严格渲染拒绝；非同质量缩图证明。"),
    ]
    for i, (label, body) in enumerate(conclusion):
        x = 990+i*777
        c.box((x, 3115, x+750, 3445), "#FFF5F4")
        c.write(label, x+24, 3135, 700, 34, RED, True, height=55)
        c.write(body, x+24, 3210, 700, 29, height=200)
    c.write("[verified: measurements.json；with-original-input/comparison-evidence.json original]", 990, 3470, 2300, 23, MUTED, height=40)
    c.line([(65, 3580), (3340, 3580)], "#D9E3EC", 2)
    c.write("共同Asset Track：统一排布 → 独立成员采样 → 真实BaseColor与mip → 颜色 / 接缝 / 渲染 → 接受或诊断导出",
            80, 3605, 3260, 30, NAVY, True, height=55)
    c.write("[verified: protocol.json；measurements.json delivery_artifacts]  原始输入跨几何单列；GPU、完整PBR与人工认可不自动升级。",
            80, 3670, 3260, 24, MUTED, height=45)
    return c.save("03-baseline-workflow-mindmap.png"), c.boxes


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    record = json.loads((DATA / "measurements.json").read_text(encoding="utf-8"))
    rows = {r["method"]: cast(dict[str, object], r) for r in record["rows"]}
    outputs = [figure_papers(), figure_implementation(), figure_map(rows)]
    sources = {
        "methods": [asdict(m) for m in METHODS],
        "public_source_check_date": "2026-11-05",
        "public_source_scope": "P1–P5 official web pages opened; P6 uses retained local version and adapter evidence",
        "upstream_versions": [
            {key: repository[key] for key in ("name", "origin", "commit", "license_status")}
            for repository in json.loads((ROOT / "baseline/sources.json").read_text(encoding="utf-8"))["repositories"]
        ],
        "native_settings": {key: row.get("native_settings") for key, row in rows.items()},
        "source_files": [],
        "outputs": [],
        "text_bounds": "PASS: every text block width, height and canvas bounds asserted",
        "visual_review": "PENDING",
        "new_solver_or_bakes": False,
    }
    paths = [DATA / "measurements.json", DATA / "protocol.json", ROOT / "baseline/sources.json",
             ROOT / "baseline/runner.py", ROOT / "baseline/adapters/flatten_anything.py",
             ROOT / "baseline/adapters/partuv_worker.py", ROOT / "baseline/MeshQuery/README.md",
             DATA / "with-original-input/paper-metrics-protocol.json",
             DATA / "with-original-input/comparison-evidence.json", Path(__file__)]
    sources["source_files"] = [{"path": p.relative_to(ROOT).as_posix(),
                                "sha256": hashlib.sha256(p.read_bytes()).hexdigest()} for p in paths]
    sources["outputs"] = [{"path": p.name, "text_blocks_checked": count,
                           "sha256": hashlib.sha256(p.read_bytes()).hexdigest()} for p, count in outputs]
    (OUT / "sources.json").write_text(json.dumps(sources, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    print(f"CREATED {len(outputs)} reference-style figures; {sum(n for _, n in outputs)} text blocks checked")


if __name__ == "__main__":
    main()
