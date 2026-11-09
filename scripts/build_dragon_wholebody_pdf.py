"""Append an evidence-bound, plain-language whole-body continuation to the dragon book."""

from __future__ import annotations

import colorsys
import hashlib
import json
from pathlib import Path
from typing import cast

import numpy as np
from build_dragon_making_pdf import TILE_NAMES
from build_dragon_making_pdf import Report as HistoricalDrawing
from numpy.typing import NDArray
from pypdf import PdfReader, PdfWriter
from reportlab.lib.colors import HexColor
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen.canvas import Canvas

ROOT = Path(__file__).resolve().parents[1]
R2 = ROOT / 'artifacts/dragon_wholebody_refine/round02'
R3 = ROOT / 'artifacts/dragon_wholebody_refine/round03'
APP = R3 / 'appearance'
OUT = ROOT / 'output/pdf/dragon-wholebody-refine'
OLD = ROOT / 'output/pdf/dragon-making-process/镜像龙制作流程图解.pdf'
W, H = 1280., 720.
NAVY, TEAL, MUTED, BG = '#15384A', '#087F83', '#607984', '#F3F7F7'
Json = dict[str, object]


def read(path: Path) -> Json:
    return cast(Json, json.loads(path.read_text(encoding='utf-8')))


def rows(path: Path) -> list[Json]:
    return cast(list[Json], json.loads(path.read_text(encoding='utf-8')))


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def percentage(value: object) -> str:
    return f'{float(str(value)) * 100:.2f}%'


class Book(HistoricalDrawing):
    def __init__(self, output: Path) -> None:
        self.output = output
        self.paths: dict[str, Path] = {}
        self.images = {}
        self.page = 0
        self.checks: list[Json] = []
        self.panel_counts: list[Json] = []
        self.selection = read(APP / 'selection.json')
        self.candidate = cast(Json, self.selection['candidate'])
        self.baseline = cast(Json, self.selection['baseline'])
        self.summaries = rows(APP / 'summary.json')
        self.regions = rows(R2 / 'regions.json')
        for weight, name in [('YH', 'msyh.ttc'), ('YHB', 'msyhbd.ttc')]:
            pdfmetrics.registerFont(TTFont(weight, 'C:/Windows/Fonts/' + name, subfontIndex=0))
        self.c = Canvas(str(output), pagesize=(W, H), pageCompression=1)
        self.c.setTitle('镜像龙全身整理续篇')
        self.c.setAuthor('ReSeam')
        for name in ['candidate', 'baseline']:
            for camera in ['front', 'oblique', 'back', 'underside']:
                self.paths[f'{name}_{camera}'] = APP / f'{name}_actual_whole/{camera}.png'
            self.paths[f'{name}_spikes'] = APP / f'{name}_actual_spikes/back.png'
        for kind, suffix in [('source', 'original'), ('target', 'target'), ('difference', 'difference')]:
            self.paths['preflight_' + kind] = APP / 'target_preflight_spikes' / f'back_{suffix}.png'
        self.paths['texture'] = APP / 'candidate_bake/texture/BaseColor_mip0.png'

    def start(self, title: str, subtitle: str, source: str) -> None:
        if self.page:
            self.c.showPage()
        self.page += 1
        self.rect(0, 0, W, H, BG, radius=0)
        self.text(44, 21, '制作档案  /  镜像龙  /  全身继续整理', 10, TEAL, True)
        self.text(44, 49, title, 27, NAVY, True, width=1190)
        self.text(44, 91, subtitle, 13, MUTED, width=1190)
        self.line(44, 117, 1236, 117, '#D6E3E4')
        self.text(44, 668, source, 8.5, MUTED, width=1190)
        self.text(44, 691, 'R2 / R3 原始文件索引见本章末页。部位名称为讲解用名称，判断与返回分别标记。', 8.5, MUTED)
        self.text(1155, 689, f'{self.page:02d} / 12', 11, TEAL, True)
        key = f'continuation-{self.page}'
        self.c.bookmarkPage(key)
        self.c.addOutlineEntry(title, key, level=0, closed=False)
        self.checks.append({'page': self.page, 'title': title, 'source': source})

    def uv(self, path: Path, x: float, top: float, width: float, height: float,
           faces: list[int] | None = None, color: str | None = None) -> None:
        with np.load(path.with_name('arrays.npz'), allow_pickle=False) as data:
            uv, indices = data['uv'], data['uv_faces']
        groups = cast(list[Json], read(path)['groups'])
        domains = [cast(list[int], group['first_faces']) + cast(list[int], group['second_faces'])
                   for group in groups] if faces is None else [faces]
        all_faces = [face for domain in domains for face in domain]
        points = uv[indices[all_faces]]
        lower = np.zeros(2) if faces is None else points.min(axis=(0, 1))
        extent = np.ones(2) if faces is None else points.max(axis=(0, 1)) - lower
        scale = min((width - 16) / max(float(extent[0]), 1e-12),
                    (height - 16) / max(float(extent[1]), 1e-12))
        origin = np.array([x + (width - extent[0] * scale) / 2,
                           H - top - height + (height - extent[1] * scale) / 2])
        self.rect(x, top, width, height, '#FFFFFF', '#D6E3E4', 3)
        annotations: list[tuple[int, NDArray[np.float64]]] = []
        for index, domain in enumerate(domains):
            channels = colorsys.hsv_to_rgb((index * .6180339) % 1, .45, .8)
            fill = color or '#' + ''.join(f'{round(channel * 255):02x}' for channel in channels)
            self.c.setFillColor(HexColor(fill))
            self.c.setStrokeColor(HexColor('#536A70'))
            self.c.setLineWidth(.15)
            for triangle in (uv[indices[domain]] - lower) * scale + origin:
                shape = self.c.beginPath()
                shape.moveTo(*triangle[0])
                shape.lineTo(*triangle[1])
                shape.lineTo(*triangle[2])
                shape.close()
                self.c.drawPath(shape, fill=1, stroke=1)
            if faces is None:
                triangles = uv[indices[domain]]
                a, b = triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0]
                area = np.abs(a[:, 0] * b[:, 1] - a[:, 1] * b[:, 0])
                center = (triangles[int(np.argmax(area))].mean(axis=0) - lower) * scale + origin
                annotations.append((index + 1, center))
        placed: list[NDArray[np.float64]] = []
        for number, anchor in annotations:
            choices = [anchor]
            for radius in [14, 26, 40, 55]:
                choices.extend(anchor + radius * np.array([np.cos(angle), np.sin(angle)])
                               for angle in np.linspace(0, 2 * np.pi, 24, endpoint=False))
            valid = [point for point in choices if x + 8 <= point[0] <= x + width - 8
                     and H - top - height + 8 <= point[1] <= H - top - 8
                     and all(np.linalg.norm(point - old) > 16 for old in placed)]
            if not valid:
                raise ValueError(f'UV label cannot be separated: {number}')
            point = valid[0]
            placed.append(point)
            self.c.setStrokeColor(HexColor(MUTED))
            self.c.setLineWidth(.5)
            self.c.line(float(anchor[0]), float(anchor[1]), float(point[0]), float(point[1]))
            self.c.setFillColor(HexColor('#FFFFFF'))
            self.c.circle(float(point[0]), float(point[1]), 7, fill=1, stroke=0)
            self.c.setFillColor(HexColor(NAVY))
            self.c.setFont('YHB', 8)
            self.c.drawCentredString(float(point[0]), float(point[1]) - 2.7, str(number))

    def summary(self) -> None:
        count = int(str(self.candidate['groups']))
        self.start('接着40份图案的版本，继续检查全身', '本章按本轮实际顺序记录。前一章的40份版本为历史起点。',
                   '[verified: R2 shape_trials；R3 appearance/selection、candidate_inspect]')
        self.panel('candidate_oblique', '本轮重新上色后的真实模型', 44, 141, 405, 407)
        self.uv(Path(str(self.candidate['snapshot'])), 472, 141, 405, 407)
        self.text(899, 160, f'40份 → {count}份', 28, TEAL, True, width=320)
        self.paragraph(899, 222, '实际表面仍为80片，部分角刺从“两块共用”扩展为“四块共用”。[verified: R3 candidate_inspect]',
                       315, 18, max_height=130)
        self.paragraph(899, 371, f'本轮摆放占画布{percentage(self.candidate["coverage"])}。紧凑程度与图块数量分别核对。[verified: R3 selection.candidate]',
                       315, 18, max_height=130)
        self.tool_note('章节目录 · 全身检查02-04；角刺共享05-06；颜色与实物07-09；全部来源10-11；证据与检查12。')
        self.next_step('先查看整龙，挑完整的大部位尝试；眼嘴、白爪和已有修整单独保护。[opinion]')

    def body_trials(self) -> None:
        self.start('继续整理 · 先逐部位试，留下每次反馈', '先试一处，再向腿、尾巴、前肢和翅膀扩大。', '[verified: R2 shape_trials[1..16]；region_decisions]')
        trials = rows(R2 / 'shape_trials.json')[1:]
        self.text(60, 138, '完整部位', 15, TEAL, True)
        self.text(350, 138, '重新铺平，再同条件摆放', 15, TEAL, True)
        self.text(688, 138, '全身仍有80片 / 40份', 15, TEAL, True)
        for index, row in enumerate(trials):
            y = 171 + index * 23
            tile = int(str(row['tile']))
            title = TILE_NAMES[tile - 1] + (' · 加开口' if row['label'] == 'chest_bridge' else '')
            self.text(60, y, title, 13, NAVY)
            self.text(350, y, percentage(row['coverage']), 13, NAVY)
            self.text(688, y, '已保存局部输出与全身检查', 12, MUTED)
        self.paragraph(923, 151, '看到了什么<br/>其余大块已经各是一片，但形状仍可尝试重做。[verified: R2 regions]', 292, 16, max_height=130)
        self.paragraph(923, 295, '为什么这样做<br/>试着减少铺开的扭曲，观察是否更好摆；结果不预先保证。[opinion]', 292, 16, max_height=140)
        self.paragraph(923, 452, '工具交回什么<br/>16次局部试验，片数均保持80。保留失败与较差结果。[verified: R2 shape_trials]', 292, 16, max_height=120)
        self.tool_note('本页为几何候选记录，大腿另有实际贴图对照；其它未采用候选未上色。[verified: R2 shape_trials、appearance]', 548)
        self.tool_note('工具 · reuse.apply → uv.reparameterize → reuse.check_pair（来源面角复制镜像侧）→ uv.pack。', 571)
        self.next_step('单次摆得更满，还要换几个摆放起点，再与此前最好版本对照。[opinion]')

    def shapes(self) -> None:
        self.start('继续整理 · 重新铺平，形状会怎样变', '下面直接读取同一部位在真实父子快照中的平面坐标。各小图分别放大。', '[verified: R2 regions；对应 unwrap / apply / pack 快照]')
        original = Path(str(read(R2 / 'protocol.json')['parent']))
        for index, (tile, label) in enumerate([(1, 'chest_minimum_stretch'), (16, 'thigh'), (12, 'tail'), (21, 'wing_outer')]):
            x = 44 + index * 300
            region = self.regions[tile - 1]
            trial = next(row for row in rows(R2 / 'shape_trials.json') if row['label'] == label)
            self.text(x + 10, 140, TILE_NAMES[tile - 1], 18, NAVY, True)
            self.uv(original, x, 180, 280, 160, cast(list[int], region['first_faces']), '#9DB2B9')
            self.text(x + 9, 351, '↓ 重新铺平', 14, TEAL)
            self.uv(Path(str(trial['snapshot'])), x, 384, 280, 160, cast(list[int], region['first_faces']), '#71B7B2')
        self.tool_note('上排为本轮起点，下排为试验输出。它们是候选记录，是否采用由整龙比较决定。[verified: R2 shape_trials；opinion: 采用规则]')
        self.next_step('把大腿、尾巴和外侧翅膀组合，再检查整张图有没有改善。[opinion]')

    def packing(self) -> None:
        self.start('继续整理 · 数量少，与摆得满，分开看', '同一尺寸、相同留边，每种方案都试0、17、29三个摆放起点。', '[verified: R2 packing_repeats；historical_density_comparison；R3 measurements]')
        rows3 = rows(R3 / 'measurements.json')
        labels = list(dict.fromkeys(str(row['label']).rsplit('_seed', 1)[0] for row in rows3))
        self.text(63, 142, '方案', 15, TEAL, True)
        for x, name in [(560, '起点0'), (770, '起点17'), (980, '起点29')]:
            self.text(x, 142, name, 15, TEAL, True)
        names = ['原40份对照', '上背与中背角刺共用', '再加下背与尾根角刺', '再加尾根与尾端角刺']
        for index, label in enumerate(labels):
            y = 189 + index * 62
            self.rect(44, y - 9, 1192, 51)
            self.text(63, y, names[index], 16, NAVY)
            for column, seed in enumerate([0, 17, 29]):
                row = next(row for row in rows3 if row['label'] == label + f'_seed{seed}')
                value = percentage(row['coverage']) if row['status'] == 'OK' else str(row['status'])
                self.text(560 + 210 * column, y, value, 18, TEAL, True)
        self.paragraph(63, 458, '大腿单独整理在三次摆放中两次较好、一次较差；大腿加尾巴为62.25%，再加外侧翅膀为62.97%。[verified: R2 packing_repeats]',
                       1140, 17, max_height=65)
        self.paragraph(63, 530, '历史还保留65.08%的40份版本。因此，本轮数量变化和摆放变化各自报告，选择规则和所有候选同时保留。[verified: R2 historical_density_comparison；opinion: 报告取舍]',
                       1140, 17, max_height=70)
        self.next_step('大块重铺没有稳定获益，转向不同位置的重复角刺。[opinion]')

    def sharing(self, second: bool) -> None:
        candidate_state = read(Path(str(self.candidate['snapshot'])))
        groups = [group for group in cast(list[Json], candidate_state['groups']) if group.get('member_maps')]
        original_faces = [set(cast(list[int], region['first_faces']) + cast(list[int], region['second_faces']))
                          for region in self.regions]
        group = groups[int(second)]
        all_faces = set(cast(list[int], group['first_faces']) + cast(list[int], group['second_faces']))
        tiles = [index + 1 for index, faces in enumerate(original_faces) if faces & all_faces]
        names = '与'.join(TILE_NAMES[tile - 1] for tile in tiles)
        self.start('继续整理 · ' + ('把第二组角刺也放到一起' if second else '让两组左右角刺，共用同一份图案'),
                   names + '各自的左右半边，共四块表面。', '[verified: R3 candidates；对应 combine 检查与最终 member_maps]')
        original = Path(str(read(R3 / 'protocol.json')['parent']))
        for index, tile in enumerate(tiles):
            x = 44 + index * 405
            self.text(x + 15, 145, f'{tile} · {TILE_NAMES[tile - 1]}', 17, NAVY, True)
            self.uv(original, x, 181, 375, 260, cast(list[int], self.regions[tile - 1]['first_faces']))
            self.text(x + 15, 455, '左右两块原本共用这一份', 14, MUTED)
        self.text(851, 251, '→', 37, TEAL, True)
        self.uv(Path(str(self.candidate['snapshot'])), 914, 181, 320, 260, sorted(all_faces), '#71B7B2')
        self.text(930, 455, '四块表面共用一份', 17, TEAL, True)
        self.paragraph(63, 505, '先检查不同部位能否对应，再按原始面角记录恢复各自镜像伙伴。所有成员之间的重叠逐一检查，表面身份继续保留。[verified: R3 combine 检查 diagnostics、geometry]',
                       1160, 17, max_height=65)
        self.tool_note('工具 · reuse.check_pair combine_mirror_groups → reuse.apply；右图叠画四块真实轮廓，各成员保留独立UV身份。', 578)
        self.next_step('两组共用关系确定后，先检查改色目标，再生成真实颜色图。[opinion]' if second else
                       '第一组形成39份版本，再比较两种第二组候选，继续减少一份。[verified: R3 candidates]')

    def target(self) -> None:
        self.start('继续整理 · 先看颜色会怎样变化，再上色', '仅对本轮新增共用的角刺统一图案，其它部位沿用来源颜色。', '[verified: R3 appearance/common_target/{target,appearance_change}.json；target_review.json]')
        for x, kind, title in [(44, 'source', '原来的颜色'), (449, 'target', '冻结后的目标'), (854, 'difference', '两者差异的位置')]:
            self.panel('preflight_' + kind, title, x, 145, 382, 352)
        self.paragraph(63, 517, '眼嘴、头部和白爪所在表面作为保护区。目标先于候选贴图冻结，Agent查看后才开始本轮两份烘焙。[verified: R3 common_target/target.protected_faces；target_review]',
                       1140, 17, max_height=65)
        self.tool_note('工具 · material.target → view.render；差异图的黑色表示无变化，红色越亮表示差异越大。', 581)
        self.next_step('目标外观可以接受后，把这些颜色放到新位置，并检查实际模型。[opinion]')

    def actual(self) -> None:
        self.start('继续整理 · 对比实际贴图后的龙', '左右使用同一冻结目标、相同角度和相同2048尺寸。', '[verified: R3 baseline_actual_whole、candidate_actual_whole；appearance_review]')
        for index, camera in enumerate(['oblique', 'back']):
            x = 44 + index * 608
            self.panel('baseline_' + camera, '40份对照 · ' + ('斜前方' if camera == 'oblique' else '背面'), x, 141, 290, 340)
            self.panel('candidate_' + camera, '本轮共用后', x + 300, 141, 290, 340)
        self.paragraph(63, 502, '同目标对照用于检查颜色重建。上一页的“原色到目标”另行说明统一图案带来的外观改变。[verified: R3 两类视图与共同target_hash]',
                       1140, 17, max_height=65)
        self.tool_note('工具 · material.bake → view.render texture=真实BaseColor；图为本轮实际输出，未以目标渲染冒充贴图结果。', 578)
        self.next_step('再看正面、脚底与角刺近景，保留数值验收的完整结论。[opinion]')

    def checks_page(self) -> None:
        self.start('继续整理 · 外观判断和数值检查分别保留', '采用本轮外观优先设置，分别记录几何、颜色、接缝和严格渲染结果。', '[verified: R3 candidate_actual_whole、candidate_actual_spikes、summary、export_integrity]')
        self.panel('candidate_front', '实际正面', 44, 143, 383, 307)
        self.panel('candidate_underside', '实际脚底方向', 449, 143, 383, 307)
        self.panel('candidate_spikes', '实际角刺近景', 854, 143, 382, 307)
        candidate = next(row for row in self.summaries if row['name'] == 'candidate')
        validation = cast(Json, candidate['validation'])
        self.text(63, 473, 'Agent预览判断见本轮留档；人工、完整材质和GPU状态单列。[verified: R3 appearance_review]', 17, NAVY)
        status = '；'.join(name + ('通过' if validation[key] else '未通过') for name, key in
                           [('几何', 'geometry_valid'), ('颜色', 'material_valid'), ('接缝', 'seams_valid'), ('严格渲染', 'render_valid')])
        self.paragraph(63, 511, status.replace('；', ' / ') + '。[verified: R3 candidate_validate/result]', 1140, 18, max_height=65)
        self.tool_note('验收LOD 0/1/2。全部12级缩小贴图已写出，写出范围与验收范围分别记录。导出后独立重读GLB。', 577)
        self.next_step('把本轮保留的各份图案，逐一对应回原来的40份位置。[opinion]')

    def origins(self, second: bool) -> None:
        data = rows(R3 / 'final_owner_origins.json')
        half = (len(data) + 1) // 2
        selected = data[half:] if second else data[:half]
        self.start('最终组成 · ' + ('其余图案逐一查来源' if second else '每份图案从哪些部位组成'),
                   '编号为本轮最终排布编号。原40份的三维定位仍在前一章逐块图解中。', '[verified: R3 final_owner_origins；原40组固定面集合]')
        self.text(60, 140, '最终编号', 14, TEAL, True)
        self.text(182, 140, '原40份编号与部位', 14, TEAL, True)
        self.text(833, 140, '实际表面数 / 来源说明', 14, TEAL, True)
        for index, row in enumerate(selected):
            top = 173 + index * 21
            self.text(63, top, str(row['final_owner_number']), 13, NAVY, True)
            tiles = cast(list[int], row['source_tiles'])
            label = ' + '.join(f'{tile} {TILE_NAMES[tile - 1]}' for tile in tiles)
            self.text(182, top, label, 12.5, NAVY, width=625)
            description = '新增四块共用' if row['physical_charts'] == 4 else '沿用原左右共用'
            self.text(833, top, f'{row["physical_charts"]}块 / {description}', 12.5, MUTED)
        self.tool_note('整龙80片逐面覆盖无遗漏。大块试验经比较后保留原方案，新增保留改动集中在重复角刺。[verified: R3 final_owner_origins、summary]', 578)
        self.next_step('查看全部操作输入、返回和最终模型检查。' if second else '继续查看剩余图案与来源。')

    def evidence(self) -> None:
        self.start('证据附录 · 原始调用、失败和交付范围', '每次调用保留请求、结果、父子快照和耗时。PDF内附机器可读JSON。', '[verified: R2/R3 calls；closure；review；export_integrity；PDF verification]')
        entries = [
            ('R2 · 大块与角刺探索', 'artifacts/dragon_wholebody_refine/round02/', 'shape_trials.json / packing_repeats.json / cross_part/summary.json'),
            ('R3 · 四块共用与排布', 'artifacts/dragon_wholebody_refine/round03/', 'protocol.json / candidates.json / measurements.json / final_owner_origins.json'),
            ('R3 · 颜色与真实模型', 'round03/appearance/', 'target_review.json / appearance_review.json / summary.json / export_integrity.json'),
            ('检查与源码', 'round02/frozen_code/ 和 round03/frozen_code/', '204项相关回归通过，源码冻结后运行，独立复审CLEAN。'),
        ]
        for index, (title, path, detail) in enumerate(entries):
            y = 143 + index * 94
            self.rect(44, y, 1192, 82)
            self.text(62, y + 10, title, 17, TEAL, True)
            self.text(62, y + 36, path, 12, NAVY)
            self.text(62, y + 59, detail, 11, MUTED, width=1146)
        self.paragraph(63, 535, '数量更少、摆放更紧凑、贴图字节更小分别报告。同为2048 RGBA8并带12级缩小图时，本轮未获得运行时贴图字节节省。[verified: R3 两份export.runtime_texture_bytes]',
                       1140, 16, max_height=65)
        self.next_step('历史28页制作记录与本轮12页续篇同册保存，可沿书签回查每一步。')

    def build(self) -> None:
        self.summary()
        self.body_trials()
        self.shapes()
        self.packing()
        self.sharing(False)
        self.sharing(True)
        self.target()
        self.actual()
        self.checks_page()
        self.origins(False)
        self.origins(True)
        self.evidence()
        assert self.page == 12
        self.c.save()


def collect_calls() -> list[Json]:
    result: list[Json] = []
    for path in [R2.parent / 'round01/calls.json', R2 / 'calls.json', R2 / 'appearance/calls.json',
                 R2 / 'cross_part/calls.json', R3 / 'calls.json', APP / 'calls.json']:
        for call in rows(path):
            request, response = Path(str(call['request'])), Path(str(call['result']))
            result.append({**call, 'request_content': read(request), 'result_content': read(response),
                           'request_sha256': sha(request), 'result_sha256': sha(response)})
    return result


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    continuation = ROOT / 'tmp/pdfs/dragon-wholebody-authoring/continuation.pdf'
    continuation.parent.mkdir(parents=True, exist_ok=True)
    book = Book(continuation)
    book.build()
    calls = collect_calls()
    calls_path = OUT / 'tool-calls.json'
    calls_path.write_text(json.dumps(calls, ensure_ascii=False, indent=2), encoding='utf-8')
    evidence = {'historical_pdf': str(OLD), 'historical_sha256': sha(OLD), 'pages': book.checks,
                'selection': book.selection, 'calls': len(calls),
                'sources': {str(path): sha(path) for path in [*book.paths.values(),
                    R2 / 'shape_trials.json', R2 / 'packing_repeats.json', R3 / 'measurements.json',
                    APP / 'summary.json', APP / 'appearance_review.json', APP / 'export_integrity.json',
                    R3 / 'final_owner_origins.json']}}
    (OUT / 'evidence.json').write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding='utf-8')
    writer = PdfWriter()
    writer.append(str(OLD), outline_item='原始制作记录 · 121片到80片与40份图案')
    writer.append(str(continuation), outline_item='全身继续整理 · 从40份继续减少')
    writer.add_metadata({'/Title': '镜像龙制作流程图解 · 全身整理新版', '/Author': 'ReSeam'})
    writer.add_attachment('wholebody-tool-calls.json', calls_path.read_bytes())
    writer.add_attachment('wholebody-evidence.json', (OUT / 'evidence.json').read_bytes())
    writer.add_attachment('historical-tool-calls.json', (OLD.parent / 'tool-calls.json').read_bytes())
    writer.add_attachment('historical-evidence.json', (OLD.parent / 'evidence.json').read_bytes())
    destination = OUT / '镜像龙制作流程图解_全身整理新版.pdf'
    with destination.open('wb') as stream:
        writer.write(stream)
    reader = PdfReader(destination)
    assert len(reader.pages) == 40
    for page in reader.pages:
        assert page.extract_text().strip()
    (OUT / 'pdf-text.txt').write_text('\n\n'.join(page.extract_text() for page in reader.pages), encoding='utf-8')
    print(json.dumps({'pdf': str(destination), 'pages': len(reader.pages), 'calls': len(calls),
                      'bytes': destination.stat().st_size}, ensure_ascii=False))


if __name__ == '__main__':
    main()
