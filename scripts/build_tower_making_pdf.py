"""Six-page illustrated making report from retained tower experiment artifacts."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from build_uv_workflow_pdf import Report as Drawing
from pypdf import PdfReader
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen.canvas import Canvas

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "artifacts/tower_uv_comparison/round02"
OUT = ROOT / "output/pdf/tower-making-process"
PDF = OUT / "防御塔制作过程图解.pdf"
W, H = 1280., 720.
NAVY, TEAL, MUTED, BG = "#15384A", "#087F83", "#607984", "#F3F7F7"


def read(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class Book(Drawing):
    def __init__(self) -> None:
        OUT.mkdir(parents=True, exist_ok=True)
        self.output = PDF
        self.paths: dict[str, Path] = {}
        self.images: dict[str, ImageReader] = {}
        self.page = 0
        self.checks: list[dict[str, object]] = []
        self.panel_counts: list[dict[str, object]] = []
        self.sources: dict[str, str] = {}
        self.rows = {row["method"]: row for row in read(BASE / "report/results.json")}
        for name, filename in [("YH", "msyh.ttc"), ("YHB", "msyhbd.ttc")]:
            pdfmetrics.registerFont(TTFont(name, "C:/Windows/Fonts/" + filename, subfontIndex=0))
        self.c = Canvas(str(PDF), pagesize=(W, H), pageCompression=1)
        self.c.setTitle("防御塔制作过程图解 - 从原始模型到24个共享图块")
        self.c.setAuthor("ReSeam")
        self.register("source", BASE / "source_original_preview/material-front_right-soft-1.png")
        self.register("source_front", BASE / "source_original_preview/material-front-soft-1.png")
        for method, row in self.rows.items():
            for key in ("checker", "uv"):
                if row.get(key):
                    self.register(method + "_" + key, Path(row[key]))
        self.register("final", BASE / "evaluation/mirror_negative_exhaustive_minimum_shared_2048/preview/material-front_right-soft-1.png")
        for region in ("bow", "base"):
            for subject in ("source", "final"):
                for channel, light in (("material", "soft"), ("normal_lit", "side")):
                    self.register(f"{subject}_{region}_{channel}", BASE / f"closeups/{subject}_{region}/{channel}-front_right-{light}-1.png")
        for relative in ["report/results.json", "protocol.json", "report/appearance_review.json",
                         "report/integrity.json", "comparison_scene_receipt.json",
                         "optimization/merge_plan.json", "optimization/aggressive_merge_plan.json",
                         "optimization/exhaustive_merge_plan.json", "mirror/positive_clean/mirror_variant.json",
                         "mirror/negative_clean/mirror_variant.json", "final_preflight/preflight.json",
                         "evaluation/reseam_shared_boundary_2048/bake/member_audit.json"]:
            path = BASE / relative
            self.sources[str(path)] = sha(path)
        for path in [BASE.parent / "round01/native_retry/optcuts/result.json",
                     BASE / "native_repaired/optcuts/result.json",
                     BASE / "evaluation/flatten_anything_2048/bake/result.json"]:
            self.sources[str(path)] = sha(path)

    def register(self, key: str, path: Path) -> None:
        if not path.is_file():
            raise FileNotFoundError(path)
        self.paths[key] = path
        self.sources[str(path)] = sha(path)

    def start(self, title: str, subtitle: str, source: str) -> None:
        if self.page:
            self.c.showPage()
        self.page += 1
        self.rect(0, 0, W, H, BG, radius=0)
        self.text(44, 22, "制作档案  /  防御塔  /  UV 整理与镜像复用", 10, TEAL, True)
        self.text(44, 49, title, 28, NAVY, True, width=1190)
        self.text(44, 92, subtitle, 13, MUTED, width=1190)
        self.line(44, 117, 1236, 117, "#D6E3E4")
        self.text(44, 669, source, 9, MUTED, width=1190)
        self.text(44, 692, "来源编号 S1-S5 见第6页。图像均取自已保存的模型、UV 与实际渲染。", 8.5, MUTED)
        self.text(1165, 689, f"{self.page:02d} / 06", 11, TEAL, True)
        self.c.bookmarkPage(f"page-{self.page}")
        self.c.addOutlineEntry(title, f"page-{self.page}", level=0)
        self.checks.append({"page": self.page, "title": title, "source": source})

    def panel(self, key: str, title: str, x: float, y: float, width: float, height: float) -> None:
        self.rect(x, y, width, height)
        self.text(x + 14, y + 13, title, 16, NAVY, True, width=width - 28)
        self.image(key, x + 10, y + 43, width - 20, height - 53)

    def strip(self, text: str, y: float = 608) -> None:
        self.rect(44, y, 1192, 43, "#DFEEEE")
        self.text(60, y + 13, text, 14, NAVY, width=1160)

    def cover(self) -> None:
        self.start("这座防御塔，是怎样整理成24份共用图案的？",
                   "沿用龙案例的观察、展开、共享、烘焙与看图流程，根据塔的结构继续调整。",
                   "[verified: S1 results / protocol; S3 negative mirror; S4 appearance_review]")
        self.panel("source", "输入模型 · 源贴图外观", 44, 140, 365, 360)
        self.panel("final", "成品 · 2K颜色与法线", 428, 140, 365, 360)
        self.panel("mirror_negative_exhaustive_minimum_shared_uv", "成品 · 24个共享图块", 812, 140, 424, 360)
        for x, value, label in [(44, "7,278 三角形", "统一实验输入"), (348, "7,650 三角形", "裁切镜像后的几何"),
                                 (652, "48 岛 / 24 图块", "左右两侧共用图案"), (956, "56.82%", "最终UV覆盖率")]:
            self.metric(x, 519, label, value, "[verified: S1]", 280)
        self.strip("制作顺序  输入检查 → 多方法展开 → 连续部件整理 → 半边镜像 → 继续合岛 → 重烘焙与看图", 624)

    def baselines(self) -> None:
        self.start("先跑各个方法，取得这座塔的真实对照",
                   "同一份7,278三角形输入，逐方法保存原生UV、排布、贴图和运行日志。",
                   "[verified: S1 rows; S5 native results / native_repaired; each evaluation folder]")
        methods = [("flatten_anything", "FlattenAnything", "4岛，UV重叠，烘焙失败"),
                   ("optcuts", "OptCuts", "原生退出，修复重试2400秒超时"),
                   ("xatlas", "xatlas", "363岛，完成颜色与法线烘焙"),
                   ("blender_smart", "Blender Smart UV", "836岛，完成颜色与法线烘焙"),
                   ("partuv", "PartUV", "120岛，完成颜色与法线烘焙"),
                   ("meshquery_project", "MeshQuery 项目实现", "108岛，采用Minimum Stretch修正")]
        for index, (method, title, note) in enumerate(methods):
            x, y = 44 + index % 3 * 405, 138 + index // 3 * 244
            self.rect(x, y, 382, 228)
            self.text(x + 14, y + 12, title, 18, NAVY, True)
            if method == "optcuts":
                self.paragraph(x + 18, y + 55, "程序运行记录<br/>原生运行退出后，增加已保存切缝再试，达到时间上限。<br/>[verified: S5]", 345, 16, max_height=117)
            else:
                self.image(method + "_checker", x + 8, y + 43, 164, 145)
                self.image(method + "_uv", x + 181, y + 43, 191, 145)
            self.text(x + 14, y + 197, note, 12, MUTED, width=354)
        self.strip("本次ReSeam从查询、接缝重建与Blender展开结果继续制作。[verified: S2 craft / optimization]", 616)

    def original(self) -> None:
        self.start("原形先接起连续部件，再尝试合岛与共享",
                   "统一副本移除3个零面积三角形，并拆分异常连接，保留其余面角位置与原UV。",
                   "[verified: S1 integrity.zero_area_repair; S2 seam_decision / merge_plan; S1 rows]")
        panels = [("source_uv_uv", "原始索引 · 3,998岛", "保留原UV作为起点"),
                  ("reseam_unshared_uv", "连续部件重展开 · 108岛", "用真实原边记录开口并重新展开"),
                  ("reseam_optimized_uv", "继续删缝合岛 · 82岛", "合并26组相邻区域，再用Minimum Stretch展开")]
        for index, (key, title, note) in enumerate(panels):
            x = 44 + index * 405
            self.panel(key, title, x, 139, 382, 350)
            self.paragraph(x + 12, 502, note + "<br/>[verified: S1 / S2]", 358, 14, max_height=67)
        self.text(58, 556, "3,998按原始UV索引邻接统计，后续沿真实原边重建连续UV。[verified: S1 / S2]", 11, MUTED, width=1160)
        self.rect(44, 580, 1192, 72, "#DFEEEE")
        self.text(60, 591, "原形部件共享也完成了", 17, TEAL, True)
        self.text(60, 619, "13组共享，108岛 / 95图块，实测共享表面约33.98%。采用最多4像素的显式边界延拓目标。[verified: S1 / S4]", 12.5, NAVY, width=1160)

    def symmetry(self) -> None:
        self.start("沿中线保留一侧，镜像出完整的塔",
                   "选用负X侧作为来源，保存裁切、三角化和逐面对应，再建立共享与无共享版本。",
                   "[verified: S3 negative_clean / positive_clean mirror_variant; S1 mirror_negative rows]")
        self.panel("source_front", "中线示意 · 保留负X侧", 44, 140, 330, 402)
        self.line(209, 198, 209, 517, TEAL, 1.5)
        self.tag(63, 500, "x = 0 裁切平面")
        self.panel("mirror_negative_unshared_uv", "镜像后无共享 · 124岛 / 124图块", 394, 140, 411, 402)
        self.panel("mirror_negative_shared_uv", "两侧共用 · 124岛 / 62图块", 825, 140, 411, 402)
        self.paragraph(58, 553, "源侧选择依据<br/>负侧未检出中线薄条，正侧检出14个候选。[verified: S3]", 308, 14, max_height=64)
        self.paragraph(410, 553, "两版均为7,650三角形，几何来自相同镜像来源。[verified: S1 / S3]", 380, 14, max_height=64)
        self.paragraph(841, 553, "每对表面共享一份图案，保存同几何无共享对照。[verified: S3]", 380, 14, max_height=64)
        self.strip("镜像会把来源侧的小装饰与纹理带到另一侧，中央装饰与宝石在近景中继续检查。[verified: S4]", 624)

    def refine(self) -> None:
        self.start("继续处理整块接缝，把共享图块从62份做到24份",
                   "在来源侧逐步删去相邻区域的共同边界，重展开后重新生成镜像版本，逐版烘焙与看图。",
                   "[verified: S2 merge_plan / aggressive_merge_plan / exhaustive_merge_plan; S1 mirror rows]")
        variants = [("mirror_negative_shared", "两侧共享"),
                    ("mirror_negative_optimized_minimum_shared", "局部合岛"),
                    ("mirror_negative_aggressive_minimum_shared", "扩大合岛"),
                    ("mirror_negative_exhaustive_minimum_shared", "本轮保留")]
        for index, (method, label) in enumerate(variants):
            row = self.rows[method]
            x = 44 + index * 304
            self.rect(x, 139, 280, 389)
            self.text(x + 14, 153, label, 17, NAVY, True)
            self.image(method + "_uv", x + 10, 187, 260, 260)
            self.text(x + 14, 461, f"{row['charts']} 岛 / {row['owners']} 图块", 20, TEAL, True)
            self.text(x + 14, 494, f"覆盖率 {row['coverage']:.2%}", 14, MUTED)
        self.rect(44, 545, 1192, 107)
        self.text(60, 560, "最终同几何对照", 17, TEAL, True)
        self.text(60, 591, "同为48个UV岛，解除共享后摆48份，共享版摆24份。两版均完成颜色与法线烘焙。[verified: S1]", 14, NAVY, width=1160)
        self.text(60, 622, "共享版覆盖56.82%，无共享版57.11%。两版2K解码贴图与完整mip均为67,108,860字节。[verified: S1]", 12, MUTED, width=1160)

    def finish(self) -> None:
        self.start("把颜色与法线烘回去，再看实物与近景",
                   "最终使用2048²颜色和法线，保留导入逐角法线，按新UV重算切线并处理镜像方向。",
                   "[verified: S1 protocol / final row; S4 member_audit / appearance_review / integrity]")
        labels = ["源贴图颜色", "24图块颜色", "源法线侧光", "24图块法线侧光"]
        for column, label in enumerate(labels):
            self.text(49 + column * 194, 145, label, 14, TEAL, True, width=190)
        for index, region in enumerate(("bow", "base")):
            for column, (subject, channel) in enumerate([("source", "material"), ("final", "material"),
                                                        ("source", "normal_lit"), ("final", "normal_lit")]):
                self.rect(44 + column * 194, 174 + index * 193, 185, 185)
                self.image(f"{subject}_{region}_{channel}", 44 + column * 194, 174 + index * 193, 185, 185)
        self.rect(839, 139, 397, 415)
        self.text(856, 155, "保留的交付与验收状态", 19, NAVY, True)
        self.paragraph(856, 195, "最终48岛 / 24图块，八个全身视角和弓臂、装饰、底座近景已由Agent查看并保留。[verified: S4]", 360, 16, max_height=110)
        self.paragraph(856, 304, "21份导出模型已重读核对，完整mip哈希与尺寸通过检查。[verified: S4 integrity]", 360, 15, max_height=86)
        self.paragraph(856, 392, "UV采样预检有失败项，数值结果保持诊断状态。人工美术与游戏Shader待验收。[verified: S4 preflight / protocol]", 360, 15, max_height=103)
        for index, (label, path) in enumerate([
            ("打开完整图文报告", BASE / "report/index.html"),
            ("打开Blender对照场景", BASE / "comparison.blend"),
            ("打开最终24图块模型", BASE / "evaluation/mirror_negative_exhaustive_minimum_shared_2048/export/asset.glb")]):
            y = 496 + index * 19
            self.text(856, y, label, 11, TEAL)
            self.c.linkURL(path.as_uri(), (856, H - y - 14, 1195, H - y + 2), relative=0)
        self.text(44, 572, "资料索引  artifacts/tower_uv_comparison/round02/", 11, TEAL, True)
        self.text(44, 592, "S1  report/results.json、protocol.json     S2  craft/、optimization/*merge_plan.json     S3  mirror/*/mirror_variant.json", 10, MUTED, width=1190)
        self.text(44, 612, "S4  report/appearance_review.json、integrity.json、final_preflight/     S5  ../round01/native_retry/、native_repaired/", 10, MUTED, width=1190)
        self.text(44, 637, "逐图来源、文件哈希与模型路径随PDF保存于evidence.json。预览粗糙度固定0.6、金属度固定0。[verified: S1 / S4]", 10, MUTED, width=1190)

    def save(self) -> None:
        self.c.save()
        document = PdfReader(PDF)
        assert len(document.pages) == 6
        text = "\n".join(page.extract_text() or "" for page in document.pages)
        (OUT / "pdf-text.txt").write_text(text, encoding="utf-8")
        (OUT / "evidence.json").write_text(json.dumps({
            "pdf": str(PDF), "pdf_sha256": sha(PDF), "pages": self.checks,
            "sources": self.sources, "scope": "presentation of retained results; report annotation only",
            "base": str(BASE), "final_model": str(BASE / "evaluation/mirror_negative_exhaustive_minimum_shared_2048/export/asset.glb")},
            ensure_ascii=False, indent=2), encoding="utf-8")
        print(PDF, len(document.pages), PDF.stat().st_size)


def main() -> None:
    report = Book()
    for page in [report.cover, report.baselines, report.original, report.symmetry, report.refine, report.finish]:
        page()
    report.save()


if __name__ == "__main__":
    main()
