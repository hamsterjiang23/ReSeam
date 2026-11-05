"""Build the ten-page ReSeam case presentation from actual retained evidence.

Run with the Codex bundled Python (reportlab, pypdf, Pillow, numpy required).
Source inputs are copied beside the PDF so the report can be regenerated.
"""

from __future__ import annotations

import argparse
import colorsys
import hashlib
import json
import shutil
from pathlib import Path

import numpy as np
from PIL import Image
from pypdf import PdfReader
from reportlab.lib.colors import HexColor
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen.canvas import Canvas
from reportlab.platypus import Paragraph

ROOT = Path(__file__).resolve().parents[1]
W, H = 1280.0, 720.0
NAVY, INK, MUTED = '#143653', '#233B4E', '#61778A'
BG, LINE, TEAL, ORANGE = '#F3F7FA', '#DCE6EE', '#087F8C', '#B36A13'

SOURCES = {
    'original_model': 'artifacts/meshquery_v1/uv_comparison01/original/oblique.png',
    'original_front': 'artifacts/meshquery_v1/uv_comparison01/original/front.png',
    'original_side': 'artifacts/meshquery_v1/uv_comparison01/original/left.png',
    'original_uv': 'artifacts/meshquery_v1/uv_comparison01/original/uv.png',
    'original_texture': 'model/dragon/tripo_p2/blue dragon 3d model/blue_dragon_3d_model.fbm/blue_dragon_3d_model_basecolor.jpg',
    'final_model': 'artifacts/meshquery_v1/midplane_cleanup01/seed29/views/oblique.png',
    'final_uv': 'artifacts/meshquery_v1/midplane_cleanup01/seed29/views/uv.png',
    'final_texture': 'docs/assets/dragon-midplane-cleanup/texture/BaseColor_mip0.png',
    'groups': 'tmp/pdfs/reseam-workflow/groups/front.png',
    'sole_cut': 'tmp/pdfs/reseam-workflow/sole_cut_lines/sole.png',
    'thin_crop': 'tmp/pdfs/reseam-workflow/thin-lines.png',
    'unshared_uv': 'artifacts/meshquery_v1/whole_half_formal_unshared01/unshared_blender_concave/views/uv.png',
    'shared_uv': 'artifacts/meshquery_v1/whole_half_formal_shared01/shared_blender_concave/views/uv.png',
    'foot_baseline': 'docs/assets/dragon-uv-final/baseline-foot.png',
    'foot_uncut': 'docs/assets/dragon-uv-final/uncut-foot.png',
    'foot_repaired': 'docs/assets/dragon-uv-final/repaired-foot.png',
    'packing_before': 'artifacts/meshquery_v1/compact_search01/baseline/views_clean/uv.png',
    'packing_after': 'artifacts/meshquery_v1/compact_search01/gap8_seed0/views_clean/uv.png',
    'packing_texture': 'docs/assets/dragon-packing-search/gap8_seed0/texture/BaseColor_mip0.png',
    'small_texture': 'docs/assets/dragon-packing-search/gap8_seed0_1024/texture/BaseColor_mip0.png',
    'original_metrics': 'docs/assets/dragon-uv-final/measurements.json',
    'merge_summary': 'docs/assets/dragon-uv-final/experiment-summary.json',
    'mirror_report': 'docs/results/dragon-whole-half-reuse.md',
    'packing_summary': 'docs/assets/dragon-packing-search/summary.json',
    'final_summary': 'docs/assets/dragon-midplane-cleanup/summary.json',
    'final_validation': 'docs/assets/dragon-midplane-cleanup/validation.json',
    'regions': 'artifacts/meshquery_v1/island_merge01/candidate_regions.json',
    'old_arrays': 'artifacts/meshquery_v1/whole_half_formal_shared01/shared_blender_concave/pack/snapshot/arrays.npz',
    'head_arrays': 'artifacts/meshquery_v1/island_merge01/component36_blender_abf_apply/snapshot/arrays.npz',
    'foot_arrays': 'artifacts/meshquery_v1/island_merge01/sole_reparameterize/snapshot/arrays.npz',
}


class Report:
    def __init__(self, output: Path) -> None:
        self.output = output
        self.assets = output.parent / 'source-assets'
        self.assets.mkdir(parents=True, exist_ok=True)
        self.paths: dict[str, Path] = {}
        self.manifest: list[dict[str, object]] = []
        for key, relative in SOURCES.items():
            source = ROOT / relative
            destination = self.assets / (key + source.suffix)
            if source.exists():
                shutil.copyfile(source, destination)
            elif not destination.exists():
                raise FileNotFoundError(f'Missing report input: {key}: {relative}')
            self.paths[key] = destination
            self.manifest.append({'key': key, 'source': relative, 'file': str(destination.relative_to(output.parent)),
                                  'sha256': hashlib.sha256(destination.read_bytes()).hexdigest()})
        for name, filename in [('YH', 'msyh.ttc'), ('YHB', 'msyhbd.ttc')]:
            pdfmetrics.registerFont(TTFont(name, str(Path('C:/Windows/Fonts') / filename), subfontIndex=0))
        self.c = Canvas(str(output), pagesize=(W, H), pageCompression=1)
        self.c.setTitle('ReSeam - 从AI生成资产到可复用UV：十页全流程')
        self.c.setAuthor('ReSeam 项目')
        self.page = 0
        self.checks: list[dict[str, object]] = []
        self.panel_counts: list[dict[str, object]] = []
        self.images: dict[str, ImageReader] = {}

    def read(self, key: str) -> object:
        return json.loads(self.paths[key].read_text(encoding='utf-8'))

    def rect(self, x: float, top: float, width: float, height: float, fill: str = '#FFFFFF',
             stroke: str | None = None, radius: float = 12) -> None:
        self.c.setFillColor(HexColor(fill))
        self.c.setStrokeColor(HexColor(stroke or fill))
        self.c.roundRect(x, H-top-height, width, height, radius, stroke=int(stroke is not None), fill=1)

    def text(self, x: float, top: float, value: str, size: float = 15, color: str = INK,
             bold: bool = False, width: float | None = None) -> None:
        font = 'YHB' if bold else 'YH'
        actual = pdfmetrics.stringWidth(value, font, size)
        if width is not None and actual > width + .1:
            raise ValueError(f'Text overflow on page {self.page}: {value}: {actual}>{width}')
        self.c.setFont(font, size)
        self.c.setFillColor(HexColor(color))
        self.c.drawString(x, H-top-size*.88, value)

    def paragraph(self, x: float, top: float, value: str, width: float, size: float = 15,
                  color: str = INK, max_height: float = 90, bold: bool = False) -> float:
        style = ParagraphStyle('body', fontName='YHB' if bold else 'YH', fontSize=size, leading=size*1.48,
                               textColor=HexColor(color), wordWrap='CJK', spaceAfter=0)
        p = Paragraph(value, style)
        _, height = p.wrap(width, max_height + 1000)
        if height > max_height + .1:
            raise ValueError(f'Paragraph overflow on page {self.page}: {value}: {height}>{max_height}')
        p.drawOn(self.c, x, H-top-height)
        return height

    def image(self, key: str, x: float, top: float, width: float, height: float) -> None:
        if key not in self.images:
            with Image.open(self.paths[key]) as im:
                picture = im.convert('RGB')
                picture.thumbnail((1900, 1900), Image.Resampling.LANCZOS)
                self.images[key] = ImageReader(picture.copy())
        im = self.images[key]
        iw, ih = im.getSize()
        factor = min(width / iw, height / ih)
        tw, th = iw*factor, ih*factor
        self.c.drawImage(im, x+(width-tw)/2, H-top-th-(height-th)/2, tw, th)

    def line(self, x1: float, top1: float, x2: float, top2: float, color: str = LINE,
             thickness: float = 1) -> None:
        self.c.setStrokeColor(HexColor(color))
        self.c.setLineWidth(thickness)
        self.c.line(x1, H-top1, x2, H-top2)

    def tag(self, x: float, top: float, value: str, color: str = TEAL) -> None:
        width = pdfmetrics.stringWidth(value, 'YHB', 11) + 22
        self.rect(x, top, width, 25, fill='#E3F2F2' if color == TEAL else '#FFF1DC', radius=6)
        self.text(x+11, top+6, value, 11, color, True)

    def start(self, title: str, subtitle: str, source: str) -> None:
        if self.page:
            self.c.showPage()
        self.page += 1
        self.rect(0, 0, W, H, BG, radius=0)
        self.text(44, 24, 'ReSeam  /  AI 生成资产 UV 重构', 10, MUTED, True)
        self.text(44, 54, title, 28, NAVY, True, width=1192)
        self.text(44, 94, subtitle, 13, MUTED, width=1192)
        self.line(44, 667, 1236, 667)
        self.text(44, 680, source, 7.2, MUTED, width=1120)
        self.text(1180, 679, f'{self.page:02d} / 10', 10, MUTED)
        self.checks.append({'page': self.page, 'title': title, 'source': source})

    def metric(self, x: float, top: float, label: str, value: str, note: str, width: float = 264) -> None:
        self.rect(x, top, width, 95)
        self.text(x+17, top+12, label, 12, MUTED)
        self.text(x+17, top+34, value, 25, TEAL, True, width=width-30)
        self.text(x+17, top+70, note, 10, MUTED, width=width-30)

    def bullet(self, x: float, top: float, title: str, body: str, width: float) -> None:
        self.rect(x, top+5, 5, 18, TEAL, radius=2)
        self.text(x+17, top, title, 17, NAVY, True, width=width-17)
        self.paragraph(x+17, top+31, body, width-17, 14, max_height=90)

    def uv_panel(self, array_key: str, faces: list[int], x: float, top: float,
                 width: float, height: float, expected_charts: int) -> None:
        with np.load(self.paths[array_key], allow_pickle=False) as data:
            uv = data['uv']
            uv_faces = data['uv_faces'][faces]
        parent = list(range(len(uv_faces)))

        def find(i: int) -> int:
            while parent[i] != i:
                parent[i] = parent[parent[i]]
                i = parent[i]
            return i

        edges: dict[tuple[int, int], int] = {}
        for face_id, triangle in enumerate(uv_faces):
            for first, second in zip(triangle, np.roll(triangle, 1), strict=True):
                a, b = sorted((int(first), int(second)))
                key = (a, b)
                if key in edges:
                    parent[find(face_id)] = find(edges[key])
                else:
                    edges[key] = face_id
        labels = [find(i) for i in range(len(uv_faces))]
        unique = sorted(set(labels))
        if len(unique) != expected_charts:
            raise ValueError(f'UV panel chart mismatch: {array_key}: {len(unique)} != {expected_charts}')
        colors = {label: colorsys.hsv_to_rgb(i*.61803398875 % 1, .42, .91) for i, label in enumerate(unique)}
        triangles = uv[uv_faces]
        minimum = triangles.min(axis=(0, 1))
        extent = triangles.max(axis=(0, 1))-minimum
        scale = min((width-18)/max(extent[0], 1e-12), (height-18)/max(extent[1], 1e-12))
        base = np.array([x+(width-extent[0]*scale)/2, H-top-height+(height-extent[1]*scale)/2])
        for triangle, label in zip(triangles, labels, strict=True):
            points = (triangle-minimum)*scale+base
            path = self.c.beginPath()
            path.moveTo(*points[0])
            path.lineTo(*points[1])
            path.lineTo(*points[2])
            path.close()
            self.c.setFillColorRGB(*colors[label])
            self.c.setStrokeColor(HexColor('#647C89'))
            self.c.setLineWidth(.24)
            self.c.drawPath(path, stroke=1, fill=1)
        self.panel_counts.append({'asset': array_key, 'faces': len(faces), 'charts': len(unique)})

    def cover(self) -> None:
        self.start('从 AI 生成资产，到可复用 UV 的龙模型',
                   '原始输入与最新输出  |  真实模型、真实 UV、真实 BaseColor',
                   '[verified: docs/assets/dragon-uv-final/measurements.json; dragon-midplane-cleanup/summary.json, validation.json]')
        for x, title, model, uv, note in [(44, '输入 · 原始 Tripo', 'original_model', 'original_uv', '10,832 面  /  原生 UV'),
                                           (652, '输出 · 中线修复版', 'final_model', 'final_uv', '11,338 面  /  新镜像变体')]:
            self.rect(x, 133, 584, 350)
            self.text(x+18, 148, title, 18, NAVY, True)
            self.image(model, x+8, 179, 360, 286)
            self.image(uv, x+373, 190, 190, 190)
            self.text(x+375, 397, '实际 UV 布局', 12, MUTED)
            self.text(x+375, 424, note, 10.5, MUTED)
        self.metric(44, 499, '连通 UV 岛', '121 → 80', '跨版本减少 41 岛', 284)
        self.metric(348, 499, '独立排布图块', '121 → 40', '左右共享后减少重复占位', 284)
        self.metric(652, 499, 'BaseColor 尺寸', '8192 → 2048', '尺寸变化不是同质量节省证明', 284)
        self.metric(956, 499, '图集覆盖率', '72.55% → 65.08%', '如实保留覆盖率下降', 280)
        self.tag(44, 616, '当前状态：外观预览', ORANGE)
        self.text(226, 620, '包含授权镜像改模；颜色/接缝通过，严格渲染仍拒绝。', 13, MUTED)

    def problem(self) -> None:
        self.start('输入问题：碎片多，也有可复用的区域',
                   '目标是改善 UV 的组织与复用，再用真实烘焙判断是否可用。',
                   '[verified: docs/assets/dragon-uv-final/measurements.json; skills/reseam-uv/SKILL.md]')
        self.rect(44, 137, 440, 495)
        self.text(63, 151, '原始 UV · 121 个连通岛', 17, NAVY, True)
        self.image('original_uv', 64, 187, 399, 399)
        self.text(64, 607, '每个彩色图块表示一片原始 UV 区域。', 12, MUTED)
        self.bullet(516, 148, '制作目标 [opinion]', '保留可用原 UV；优先完整部件复用。\n明显颜色边界与外观损伤需要反馈修正。', 700)
        self.bullet(516, 264, '两个指标，分别统计', '连通岛描述面片在 UV 中如何相连；独立图块描述堆叠后要在图集中安排多少份位置。', 700)
        for i, x in enumerate([535, 710]):
            self.rect(x, 407, 130, 130, '#E8EFF5', stroke=LINE)
            p = self.c.beginPath()
            p.moveTo(x+20, H-505)
            p.lineTo(x+70, H-431)
            p.lineTo(x+111, H-505)
            p.close()
            self.c.setFillColor(HexColor('#74C6C9' if i == 0 else '#A3C4F3'))
            self.c.drawPath(p, stroke=0, fill=1)
        self.text(527, 552, '两片表面，各自是一岛', 13, MUTED)
        self.text(876, 448, '共享同一 UV 位置', 15, TEAL, True)
        self.text(876, 480, '2 个连通岛', 19, NAVY, True)
        self.text(876, 512, '1 个独立图块', 19, TEAL, True)
        self.paragraph(516, 596, '示意只解释计数。实际复用仍需对应、真实交叠及材质证据。', 696, 13, max_height=45)

    def workflow(self) -> None:
        self.start('完整工作流：视觉决策与几何工具协同',
                   'Training-Free：当前 Agent 负责观察和操作决策，确定性工具执行网格与 UV 修改。',
                   '[verified: skills/reseam-uv/SKILL.md; references/symmetry-reuse-and-merge.md]')
        nodes = [('01  导入与冻结', '模型、UV、真实材质\n来源哈希与父快照'), ('02  多视图观察', '原色、线框、编号\n保护区与错误定位'),
                 ('03  区域与路线', 'Agent 定义整块区域\n固定几何 / 授权镜像'), ('04  对应与展开', '切口、锚点、参数化\n失败证据与整块修正'),
                 ('05  共享与合岛', '先堆叠，再删多余缝\n两类收益分开统计'), ('06  排布与烘焙', '共享组只排主图块\n冻结目标、生成真实贴图'),
                 ('07  外观与数值反馈', '检查特征、接缝与 mip\n接受、修正或回退'), ('08  导出与沉淀', '模型、贴图、对照报告\nSkill 与可追溯证据')]
        for index, (title, body) in enumerate(nodes):
            row, col = divmod(index, 4)
            x, y = 44+col*304, 153+row*202
            self.rect(x, y, 280, 167)
            self.text(x+17, y+18, title, 17, NAVY, True)
            self.paragraph(x+17, y+57, body.replace('\n', '<br/>'), 246, 15, max_height=80)
            if col < 3:
                self.text(x+285, y+72, '→', 17, TEAL, True)
        self.rect(44, 582, 1192, 59, '#E1F0F0')
        self.text(62, 600, '反馈原则', 15, TEAL, True)
        self.text(160, 600, '修改一个主要机制，生成新快照；重新烘焙后再判断，失败回到父状态。', 15, NAVY)

    def observe(self) -> None:
        self.start('观察与分区：先定位，再决定怎样复用',
                   '多视图、真实颜色与原面身份共同支持决策；遮挡区域必须补充观察。',
                   '[verified: view.render request/results; skills/reseam-uv/SKILL.md; final group view is a presentation aid]')
        for x, key, title in [(44, 'original_front', '原始正面'), (342, 'original_side', '原始侧面'),
                              (640, 'groups', '共享组着色定位参考')]:
            self.rect(x, 141, 276, 323)
            self.text(x+16, 155, title, 16, NAVY, True)
            self.image(key, x+8, 186, 260, 264)
        self.rect(940, 141, 296, 323, '#E4EFF5')
        self.text(958, 162, '观察重点 [opinion]', 16, NAVY, True)
        for top, title, body in [(207, '翼膜与翼骨', '确认正反面、根部和边界'), (281, '四肢与爪子', '保护白爪等明显颜色边界'),
                                 (355, '头脸与角刺', '保留眼嘴、轮廓与重复部件')]:
            self.text(958, top, title, 16, TEAL, True)
            self.text(958, top+28, body, 12.5, MUTED)
        self.text(44, 479, '着色图来自最终共享组，用于说明定位方式，不冒充初始语义分割真值。', 11.5, MUTED)
        self.rect(44, 518, 578, 119)
        self.rect(644, 518, 592, 119)
        self.text(62, 534, '路线 A：保留现有几何', 17, NAVY, True)
        self.paragraph(62, 570, '检查精确或近似曲面对应；匹配失败先改锚点、区域或切口。', 538, 14, max_height=55)
        self.text(662, 534, '路线 B：当前资产已授权镜像', 17, TEAL, True)
        self.paragraph(662, 570, '另建父几何，保留一侧并镜像。原资产和同几何无共享对照保留。', 550, 14, max_height=55)

    def mirror(self) -> None:
        self.start('整半镜像共享：先减少重复占位',
                   '同一新父几何的比较：岛数保持 134，独立图块从 134 份变为 67 份。',
                   '[verified: docs/results/dragon-whole-half-reuse.md; whole_half01/create/mirror_variant.json]')
        for x, key, title in [(44, 'unshared_uv', '同几何 · 无共享'), (454, 'shared_uv', '同几何 · 左右共享')]:
            self.rect(x, 141, 386, 429)
            self.text(x+17, 158, title, 17, NAVY, True)
            self.image(key, x+15, 198, 356, 356)
        self.metric(866, 141, '连通岛数', '134 → 134', '堆叠没有建立网格邻接', 370)
        self.metric(866, 256, '独立图块', '134 → 67', '每组只排一个主图块', 370)
        self.metric(866, 371, '实际参与共享的表面', '100%', '不是贴图节省 100%', 370)
        self.paragraph(866, 493, '来源面角与重心坐标保留，镜像面角顺序为 [0,2,1]。中线不按坐标自动焊接。', 352, 14, max_height=95)
        self.rect(44, 590, 1192, 50, '#E1F0F0')
        self.text(63, 606, '已经发生复用，也可能完全没有减少连通岛。下一步才处理同一表面内部的多余接缝。', 15, TEAL, True)

    def merge(self) -> None:
        self.start('真实合岛：把自然表面的碎片重新展开',
                   '临时解除选定组共享，重展开主侧，再检查对应并精确复制到镜像侧。',
                   '[verified: island_merge01/merge_results.json; candidate_regions.json; sole_reparameterize/reparameterization.json]')
        regions = self.read('regions')
        assert isinstance(regions, list)
        head = next(r['faces'] for r in regions if r['component'] == 36)
        foot = next(r['faces'] for r in regions if r['component'] == 18)
        panels = [('头冠：每侧 16 岛 → 1 岛', head, 'head_arrays', 16, 148),
                  ('脚部：每侧 7 岛 → 1 岛', foot, 'foot_arrays', 7, 387)]
        for title, faces, after, count, top in panels:
            self.text(44, top, title, 18, NAVY, True)
            for x in [44, 354]:
                self.rect(x, top+34, 288, 173)
            self.uv_panel('old_arrays', faces, 49, top+39, 278, 163, count)
            self.uv_panel(after, faces, 359, top+39, 278, 163, 1)
            self.text(652, top+99, '→', 23, TEAL, True)
        self.rect(724, 150, 512, 472, '#E4EFF5')
        self.text(746, 174, '整龙连通岛变化', 16, MUTED)
        self.text(746, 211, '134 → 104 → 92', 31, TEAL, True)
        self.bullet(747, 280, '失败保留', '两处 LSCM 因方向问题被拒绝，随后显式尝试 ABF++。', 458)
        self.bullet(747, 397, '整块操作', '保留未选区域；以原面集合处理自然部件，避免逐面挖出独占小岛。', 458)
        self.paragraph(747, 534, '区域图各自适应取景，图形大小不作密度对比。数据为实际连通岛计数。', 458, 13, max_height=60)

    def feedback(self) -> None:
        self.start('外观反馈：白爪模糊后，重新设计脚底切口',
                   '一次合成一岛并不代表外观合格；源颜色、冻结目标与实际贴图需要分别观察。',
                   '[verified: docs/assets/dragon-uv-final foot images/experiment-summary.json; island_merge01/sole_reparameterize_request.json]')
        for x, key, title, tag in [(44, 'foot_baseline', '旧接缝对照', '参考'),
                                   (449, 'foot_uncut', '直接合岛', '外观拒绝'),
                                   (854, 'foot_repaired', '补脚底切口', '单脚仍为 1 岛')]:
            self.rect(x, 140, 382, 285)
            self.text(x+16, 154, title, 17, NAVY, True)
            self.image(key, x+8, 189, 366, 218)
            self.tag(x+16, 393, tag, ORANGE if '拒绝' in tag else TEAL)
        self.rect(44, 447, 340, 197)
        self.image('sole_cut', 48, 451, 332, 186)
        self.text(55, 625, '红线：当前视角可见的实际切口', 9.5, ORANGE)
        self.text(406, 456, '实际切口：52 条原网格边', 20, NAVY, True)
        self.paragraph(406, 496, '后踝经过脚底连接脚趾，并添加分支开口；ABF++ 重展开后复制到另一侧，再重新烘焙。', 799, 15, max_height=70)
        self.tag(406, 578, '切口增加，不必然增加连通岛')
        self.text(406, 620, '修正后外观预览被接受，严格渲染探针仍拒绝。', 13, MUTED)

    def packing(self) -> None:
        self.start('紧凑排布与缩图：先看实测收益',
                   '固定 92 岛 / 46 图块，3 种间距 × 3 个种子；保持形状与共享关系。',
                   '[verified: docs/assets/dragon-packing-search/summary.json search/cases; independent_audit.json]')
        for x, key, title in [(44, 'packing_before', '当前对照 · 57.26%'), (365, 'packing_after', '最好覆盖 · 59.93%')]:
            self.rect(x, 140, 299, 337)
            self.text(x+13, 155, title, 16, NAVY, True)
            self.image(key, x+10, 193, 279, 275)
        self.rect(688, 140, 548, 337)
        self.text(706, 158, '9 次搜索 · 实际覆盖率', 17, NAVY, True)
        packing = self.read('packing_summary')
        assert isinstance(packing, dict)
        search = packing['search']
        assert isinstance(search, dict)
        rows = search['cases']
        assert isinstance(rows, list)
        for index, row in enumerate(rows):
            assert isinstance(row, dict)
            top = 197+index*27
            value = row['unique_coverage']*100
            self.text(707, top+2, f"{row['gap']}px / {row['seed']}", 10.5, MUTED)
            self.rect(802, top+3, value*5.4, 13, TEAL if value > 59 else '#A1BACB', radius=3)
            self.text(1155, top, f'{value:.2f}%', 11, INK)
        self.rect(44, 498, 1192, 147)
        self.image('packing_texture', 58, 509, 126, 124)
        self.image('small_texture', 195, 509, 126, 124)
        self.text(342, 515, '2048 与 1024 · 实际贴图缩略图', 17, NAVY, True)
        self.paragraph(342, 549, '1024 完整 mip 载荷约减少 75%，但颜色、接缝与渲染均拒绝。当前排布提升只相当于约 4.45% 的面积余量，不能宣称同质量降到 1024。', 866, 14, max_height=80)

    def midplane(self) -> None:
        self.start('中线薄条修复：从细线岛追溯到裁切几何',
                   '本例调整裁切平面，继承已有好 UV；沿新平面生成独立镜像变体，再排布与烘焙。',
                   '[verified: docs/assets/dragon-midplane-cleanup/independent_audit.json, mirror_variant.json, summary.json]')
        for x, key, title in [(44, 'packing_after', '修复前 · 92 岛 / 46 图块'), (457, 'final_uv', '修复后 · 80 岛 / 40 图块')]:
            self.rect(x, 141, 391, 428)
            self.text(x+14, 156, title, 16, NAVY, True)
            self.image(key, x+12, 192, 367, 365)
        self.metric(870, 141, '已定位中线细条岛', '12 → 0', '细条不再留在模型和 UV 中', 366)
        self.metric(870, 252, '实际图集覆盖率', '59.93% → 65.08%', '包含裁切几何修复的收益', 366)
        self.bullet(870, 371, '根因与修正', '中线窄带约到 X=0.00024417；本例使用 X=0.00025 重新裁切。', 366)
        self.text(870, 491, '用户指出的旧版细线局部', 12, MUTED, True)
        self.image('thin_crop', 870, 520, 166, 115)
        self.paragraph(1050, 524, '[opinion] 几何候选只提示复核；真实薄翼不能自动删除。',
                       178, 12, max_height=105)
        self.rect(44, 590, 804, 52, '#E1F0F0')
        self.text(62, 607, '先辨别三维薄条与 UV 压扁；偏移值和旧面 ID 都不能直接套给其他资产。', 13, TEAL, True)

    def delivery(self) -> None:
        self.start('最终交付：成果、边界与可复用经验',
                   '已交付模型、真实贴图、完整 mip、UV / 渲染对照、来源映射、报告与 Skill。',
                   '[verified: dragon-midplane-cleanup/manifest.json, validation.json; skills/reseam-uv/SKILL.md]')
        self.rect(44, 140, 350, 385)
        self.image('final_model', 50, 146, 338, 368)
        self.rect(414, 140, 252, 385)
        self.text(431, 155, '真实 2048 BaseColor', 15, NAVY, True)
        self.image('final_texture', 427, 194, 226, 226)
        self.paragraph(431, 440, '完整 mip 已写出；本轮验收范围为 LOD 0 / 1 / 2。', 216, 13, max_height=68)
        self.rect(688, 140, 548, 385)
        self.text(707, 158, '各阶段记录', 17, NAVY, True)
        headers = [('阶段', 708), ('岛 / 图块', 945), ('覆盖率', 1106)]
        for text, x in headers:
            self.text(x, 201, text, 12, MUTED, True)
        rows = [('原始输入', '121 / 121', '72.55%'), ('同几何镜像共享', '134 / 67', '57.95%'),
                ('合岛与脚底修正', '92 / 46', '57.26%'), ('固定形状重排', '92 / 46', '59.93%'),
                ('中线薄条修复', '80 / 40', '65.08%')]
        for index, (label, counts, coverage) in enumerate(rows):
            top = 237+index*40
            self.text(708, top, label, 13, INK, index == 4)
            self.text(945, top, counts, 13, TEAL, index == 4)
            self.text(1106, top, coverage, 13, TEAL, index == 4)
            self.line(707, top+29, 1216, top+29)
        self.text(708, 464, '不同阶段几何与密度规则不同，不能直接作单机制归因。', 10.2, MUTED)
        self.text(708, 493, '早期镜像共享值取轮廓排布路径。', 10.2, MUTED)
        self.tag(44, 545, '颜色 / 接缝：通过')
        self.tag(260, 545, '严格渲染：拒绝', ORANGE)
        self.tag(451, 545, '人工 / GPU / PBR：未完成', ORANGE)
        self.text(44, 591, '可复用入口：$reseam-uv', 18, NAVY, True)
        self.paragraph(44, 624, '[opinion] 下一步：改善难排图块形状或扩大重复区域共享，继续用真实外观判断缩图。', 1180, 12, max_height=25)

    def finish(self) -> None:
        assert self.page == 10
        original = self.read('original_metrics')
        final = self.read('final_summary')
        validation = self.read('final_validation')
        assert isinstance(original, list) and isinstance(final, dict) and isinstance(validation, dict)
        assert original[0]['charts'] == 121 and original[0]['independent_tiles'] == 121
        assert original[0]['faces'] == 10832 and final['audit']['new_faces'] == 11338
        assert final['audit']['new_charts'] == 80 and final['audit']['shared_groups'] == 40
        assert round(final['coverage']*100, 2) == 65.08
        assert validation['material']['valid'] and validation['seams']['valid']
        assert not validation['render']['valid'] and not validation['quality_accepted']
        with Image.open(self.paths['original_texture']) as im:
            assert im.size == (8192, 8192)
        with Image.open(self.paths['final_texture']) as im:
            assert im.size == (2048, 2048)
        self.c.save()
        reader = PdfReader(self.output)
        assert len(reader.pages) == 10
        extracted = [page.extract_text() or '' for page in reader.pages]
        for index, text in enumerate(extracted):
            assert len(text) > 180, (index+1, 'Text extraction incomplete')
            assert '\ufffd' not in text
        assert all(value in extracted[0] for value in ['121', '80', '40', '8192', '2048', '72.55%', '65.08%'])
        assert '严格渲染' in extracted[9] and '拒绝' in extracted[9]
        evidence = {'page_count': 10, 'page_size_points': [W, H], 'pages': self.checks,
                    'uv_panel_checks': self.panel_counts, 'assets': self.manifest,
                    'text_extraction': 'PASS; Chinese and first-page metrics checked',
                    'pdf_sha256': hashlib.sha256(self.output.read_bytes()).hexdigest()}
        (self.output.parent / 'workflow-pdf-evidence.json').write_text(
            json.dumps(evidence, ensure_ascii=False, indent=2), encoding='utf-8')
        (self.output.parent / 'workflow-pdf-text.txt').write_text(
            '\n\n'.join(f'PAGE {i+1}\n{text}' for i, text in enumerate(extracted)), encoding='utf-8')
        print(f'CREATED {self.output} pages=10 bytes={self.output.stat().st_size}')


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, default=ROOT/'output/pdf/reseam-uv-workflow-10-pages.pdf')
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    report = Report(args.output)
    for method in [report.cover, report.problem, report.workflow, report.observe, report.mirror,
                   report.merge, report.feedback, report.packing, report.midplane, report.delivery]:
        method()
    report.finish()


if __name__ == '__main__':
    main()
