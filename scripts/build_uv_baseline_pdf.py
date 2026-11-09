"""Create a ten-page workflow and baseline report from retained, measured assets."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from pathlib import Path
from typing import cast

import build_uv_workflow_pdf as workflow
from pypdf import PdfReader
from pypdf.generic import DictionaryObject

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / 'docs/assets/dragon-paper-baselines'
METHODS = ['flatten_anything', 'optcuts', 'xatlas', 'blender_smart', 'partuv',
           'meshquery_project', 'reseam_shared', 'reseam_unshared']
NAMES = {'flatten_anything': 'FlattenAnything', 'optcuts': 'OptCuts', 'xatlas': 'xatlas',
         'blender_smart': 'Blender Smart UV', 'partuv': 'PartUV',
         'meshquery_project': 'MeshQuery 项目实现', 'reseam_shared': 'ReSeam 共享',
         'reseam_unshared': 'ReSeam 解除共享'}


def percentage(value: object) -> str:
    return f'{value*100:.2f}%' if isinstance(value, (int, float)) else '未核验'


def flag(value: object) -> str:
    return '通过' if value is True else '拒绝' if value is False else '未评估'


class BaselineReport(workflow.Report):
    def __init__(self, output: Path) -> None:
        fallback = ROOT / 'output/pdf/source-assets'
        if fallback.exists():
            shutil.copytree(fallback, output.parent / 'source-assets', dirs_exist_ok=True)
        super().__init__(output)
        record = json.loads((DATA / 'measurements.json').read_text(encoding='utf-8'))
        self.rows = {str(row['method']): cast(dict[str, object], row) for row in record['rows']}
        assert set(self.rows) == set(METHODS) and record['input']['faces'] == 11338
        assert self.rows['reseam_shared']['actual_shared_surface_fraction'] == 1.0
        assert abs(float(str(self.rows['reseam_shared']['valid_duplicate_uv_area_saved_fraction']))-.5) < 1e-12
        assert self.rows['optcuts']['quality_accepted'] is True
        assert self.rows['meshquery_project']['quality_accepted'] is True
        assert self.rows['reseam_shared']['render_valid'] is False
        self.c.setTitle('ReSeam - UV重构全流程与论文基线对照：十页更新版')
        destination = output.parent / 'baseline-assets'
        destination.mkdir(exist_ok=True)
        for name in ['measurements.json', 'verification.json', 'cost-summary.json']:
            self.add_asset('baseline_'+Path(name).stem, DATA / name, destination / name)
        for method in METHODS:
            artifacts = self.rows[method]['delivery_artifacts']
            assert isinstance(artifacts, dict)
            for kind in ['uv', 'front', 'color', 'texture']:
                if kind not in artifacts:
                    continue
                source = DATA / str(artifacts[kind]).replace('\\', '/')
                self.add_asset(method+'_'+kind, source, destination / f'{method}_{kind}{source.suffix}')

    def add_asset(self, key: str, source: Path, destination: Path) -> None:
        shutil.copyfile(source, destination)
        self.paths[key] = destination
        self.manifest.append({'key': key, 'source': str(source.relative_to(ROOT)).replace('\\', '/'),
                              'file': str(destination.relative_to(self.output.parent)).replace('\\', '/'),
                              'sha256': hashlib.sha256(destination.read_bytes()).hexdigest()})

    def reuse_and_merge(self) -> None:
        self.start('先共享，再合岛：两种收益分别统计',
                   '镜像UV堆叠减少重复占位；重新设计切缝与展开，才会减少同一表面上的连通岛。',
                   '[verified: dragon-whole-half-reuse.md; dragon-uv-island-merge.md; source-assets/regions.json]')
        self.rect(44, 140, 368, 470)
        self.text(62, 158, '整半镜像 · 实际共享', 19, workflow.NAVY, True)
        self.image('unshared_uv', 58, 208, 165, 165)
        self.image('shared_uv', 231, 208, 165, 165)
        self.text(72, 386, '无共享', 13, workflow.MUTED)
        self.text(247, 386, '左右堆叠', 13, workflow.TEAL)
        self.text(64, 428, '134 岛保持不变', 23, workflow.NAVY, True)
        self.text(64, 465, '独立图块 134 → 67', 23, workflow.TEAL, True)
        self.paragraph(64, 516, '在已授权的新变体中复制半侧几何和UV；原模型保留。两片表面仍各自是一岛。', 326, 14, max_height=70)
        regions = self.read('regions')
        assert isinstance(regions, list)
        for x, component, after, old_count, title in [
                (432, 36, 'head_arrays', 16, '头冠：每侧 16 → 1 岛'),
                (844, 18, 'foot_arrays', 7, '脚部：每侧 7 → 1 岛')]:
            faces = next(row['faces'] for row in regions if row['component'] == component)
            self.rect(x, 140, 392, 470)
            self.text(x+18, 158, title, 19, workflow.NAVY, True)
            self.uv_panel('old_arrays', faces, x+12, 207, 177, 190, old_count)
            self.uv_panel(after, faces, x+204, 207, 177, 190, 1)
            self.text(x+40, 409, '旧接缝', 13, workflow.MUTED)
            self.text(x+238, 409, '重展开', 13, workflow.TEAL)
            if component == 36:
                self.text(x+18, 450, 'LSCM 拒绝 → ABF++', 19, workflow.TEAL, True)
                self.paragraph(x+18, 490, '删除选区内的多余接缝，展开主侧，再精确复制到镜像侧。整龙134 → 104岛。', 350, 14, max_height=88)
            else:
                self.text(x+18, 450, '补脚底切口，修正白爪', 19, workflow.TEAL, True)
                self.paragraph(x+18, 490, '直接合岛曾使白爪模糊；补52条原边组成的脚底开口后，单脚仍为1岛。整龙降至92岛。', 350, 14, max_height=88)
        self.text(44, 632, '这是历史固定镜像几何上的操作；区域面ID与切口数量不作为其它资产的通用参数。', 13, workflow.MUTED)

    def packing_and_cleanup(self) -> None:
        self.start('排布与中线修复：保留真实改进，也保留限制',
                   '先寻找完整图块之间的空隙，再追溯异常细条的三维来源；不靠隐藏线条修饰结果。',
                   '[verified: dragon-packing-search/summary.json; dragon-midplane-cleanup/summary.json, independent_audit.json]')
        self.rect(44, 140, 584, 352)
        self.text(63, 158, '轮廓排布：固定 92 岛 / 46 图块', 19, workflow.NAVY, True)
        self.image('packing_before', 63, 201, 260, 235)
        self.image('packing_after', 342, 201, 260, 235)
        self.text(84, 451, '57.26%', 22, workflow.MUTED, True)
        self.text(365, 451, '59.93%', 22, workflow.TEAL, True)
        self.rect(652, 140, 584, 352)
        self.text(671, 158, '中线修复：消除 12 个已定位薄条岛', 19, workflow.NAVY, True)
        self.image('thin_crop', 674, 228, 252, 155)
        self.image('final_uv', 956, 193, 252, 252)
        self.text(680, 451, '92 岛 / 46 图块', 17, workflow.MUTED)
        self.text(966, 451, '80 岛 / 40 图块', 17, workflow.TEAL, True)
        self.rect(44, 513, 1192, 128, '#E4EFF5')
        self.bullet(64, 526, '排布实测', '9次间距/起点搜索；1024缩图未通过同质量检查。', 360)
        self.bullet(456, 526, '几何来源', '调整授权镜像变体的中线；继承已有好UV，重烘焙。', 360)
        self.bullet(848, 526, '最终预览', '覆盖率65.08%；颜色/接缝过，严格渲染仍拒绝。', 360)

    def baseline_setup(self) -> None:
        self.start('基线实验：同一几何、同一冻结外观参考',
                   '六项方法实际运行，另设ReSeam共享/解除共享对照；不混用历史2000面龙的指标。',
                   '[verified: docs/assets/dragon-paper-baselines/protocol.json, measurements.json; baseline/sources.json]')
        self.metric(44, 139, '固定输入', '11,338 面', '最新已完成镜像龙；所有方法几何一致', 384)
        self.metric(448, 139, '共同输出', '2048 BaseColor', '8px间距/边界/烘焙留边；LOD 0/1/2', 384)
        self.metric(852, 139, '比较口径', '6 方法 + 2 对照', '保留失败；共享与质量分别记录', 384)
        settings = [('FlattenAnything', '官方模型/训练循环；10,000点、10,000迭代；seed17'),
                    ('OptCuts', '固定官方源码及二进制哈希；λ=0.999、畸变上限4.1；seed0'),
                    ('xatlas', '官方Python绑定0.0.11；默认chart options'),
                    ('Blender Smart UV', '原生Smart UV；角度66°、原生island margin=0.03'),
                    ('PartUV', '官方0.1.2与PartField权重；阈值1.25；seed42'),
                    ('MeshQuery 项目实现', '查询/预览/计划/反馈；4个环带补测地切口；Minimum Stretch')]
        for index, (method, setting) in enumerate(settings):
            top = 260+index*45
            self.rect(44, top, 1192, 40, '#FFFFFF' if index % 2 == 0 else '#EAF0F5', radius=4)
            self.text(60, top+11, method, 14, workflow.NAVY, True, width=250)
            self.text(320, top+11, setting, 13.5, workflow.INK, width=899)
        self.paragraph(44, 549, '范围差异：本机Blender4.5.11，论文为5.1.2；共同排布8px留边区别于论文0.001间距。MeshQuery未找到官方代码，采用项目实现及几何分区，未使用作者的PartField语义预处理。', 1192, 13, max_height=52)
        self.paragraph(44, 610, '首页65.08%为历史精调布局；本轮共同排布seed0的ReSeam覆盖率为62.93%。ReSeam沿用既有制作布局，历史成本另列，不作等预算从零排名。', 1192, 12.5, workflow.MUTED, max_height=38)

    def comparison_cards(self, methods: list[str], second: bool) -> None:
        self.start('UV 与棋盘格对照 · '+('后四条路径' if second else '前四条路径'),
                   '相同正视镜头；完成项采用共同排布，拒绝项显示原生UV并明确标记。红线为切缝叠加。',
                   '[verified: docs/assets/dragon-paper-baselines/measurements.json; individual UV/checker PNGs]')
        notes = {'flatten_anything': '110个零面积面；方向检查拒绝。', 'xatlas': '112个零UV面积面；排布拒绝。',
                 'optcuts': '颜色、接缝和渲染均通过。', 'blender_smart': '颜色/接缝拒绝；渲染通过。',
                 'partuv': '颜色/接缝拒绝；渲染通过。', 'meshquery_project': '项目实现；本轮三项均通过。',
                 'reseam_shared': '颜色/接缝通过；渲染拒绝。', 'reseam_unshared': '接缝通过；颜色/渲染拒绝。'}
        for index, method in enumerate(methods):
            row = self.rows[method]
            x = 44+index*304
            self.rect(x, 138, 280, 507)
            self.text(x+12, 151, NAMES[method], 15, workflow.NAVY, True, width=256)
            self.image(method+'_front', x+10, 185, 260, 184)
            self.image(method+'_uv', x+10, 375, 260, 184)
            tiles = row['tiles'] if row['tiles'] is not None else '未核验'
            self.text(x+12, 568, f"岛 {row['charts']} / 图块 {tiles}", 13, workflow.TEAL, True, width=256)
            self.text(x+12, 590, '共享 '+percentage(row['actual_shared_surface_fraction']), 12.5, workflow.INK)
            self.paragraph(x+12, 613, notes[method], 256, 11.8, workflow.MUTED, max_height=28)

    def actual_color(self) -> None:
        self.start('真实外观与贴图：三条代表路径的同镜头对照',
                   '模型图关闭切缝叠加；下方为各自产生的真实BaseColor。完整六份模型/贴图均在交付目录。',
                   '[verified: docs/assets/dragon-paper-baselines/{optcuts,meshquery_project,reseam_shared}/color.png, texture.png]')
        for index, method in enumerate(['optcuts', 'meshquery_project', 'reseam_shared']):
            row = self.rows[method]
            x = 44+index*404
            self.rect(x, 139, 384, 507)
            self.text(x+18, 155, NAMES[method], 20, workflow.NAVY, True)
            self.text(x+18, 185, f"共享{percentage(row['actual_shared_surface_fraction'])} / {row['tiles']}图块", 13, workflow.TEAL)
            self.image(method+'_color', x+10, 207, 364, 207)
            self.image(method+'_texture', x+80, 424, 224, 198)
            self.text(x+18, 628, 'RGBA8完整mip：22,369,620字节', 11, workflow.MUTED)

    def reuse_table(self) -> None:
        self.start('UV 复用率：共享表面、节省占位与复用倍数',
                   '基线使用原生流程，未追加ReSeam共享后处理；0%不代表不能与共享组合。共享与质量分别验收。',
                   '[verified: docs/assets/dragon-paper-baselines/measurements.json rows and reuse_metric_scope]')
        xs = [44, 279, 379, 489, 669, 849, 999]
        widths = [235, 100, 110, 180, 180, 150, 237]
        headers = ['方法', '连通岛', '独立图块', '共享表面', '重复占位节省', '复用倍数', '覆盖率']
        self.rect(44, 140, 1192, 40, '#DDEBF0', radius=5)
        for x, title in zip(xs, headers, strict=True):
            self.text(x+12, 153, title, 13, workflow.NAVY, True)
        for index, method in enumerate(METHODS):
            row = self.rows[method]
            top = 184+index*43
            self.rect(44, top, 1192, 39, '#DDF1F0' if method == 'reseam_shared' else '#FFFFFF', radius=3)
            factor = row['valid_uv_reuse_factor']
            values = [NAMES[method], str(row['charts']), str(row['tiles']) if row['tiles'] is not None else '未核验',
                      percentage(row['actual_shared_surface_fraction']),
                      percentage(row['valid_duplicate_uv_area_saved_fraction']),
                      f'{factor:.2f}×' if isinstance(factor, (int, float)) else '未核验', percentage(row['coverage'])]
            for x, width, value in zip(xs, widths, values, strict=True):
                self.text(x+12, top+12, value, 13, workflow.TEAL if method == 'reseam_shared' else workflow.INK,
                          bold=method == 'reseam_shared', width=width-20)
        self.metric(44, 551, '全部表面参与左右共享', '100%', '两侧均计入参与共享的表面积', 384)
        self.metric(448, 551, '消除成对重复占位', '50%', '是重复UV面积；本轮未做同质量缩图', 384)
        self.metric(852, 551, '同一图块服务两侧', '2.00×', '80岛仍存在，实际只排40图块', 384)

    def verdict(self) -> None:
        self.start('最终结论：共享成立，质量状态逐项保留',
                   '四项基线产出2048模型/贴图，另有两份ReSeam对照；失败、拒绝与未知成本均保留。',
                   '[verified: dragon-paper-baselines/measurements.json, verification.json, cost-summary.json; baseline/sources.json]')
        self.rect(44, 140, 750, 410)
        columns = [(60, '方法'), (310, '颜色'), (410, '接缝'), (510, '渲染'), (610, '综合状态')]
        for x, title in columns:
            self.text(x, 157, title, 14, workflow.NAVY, True)
        for index, method in enumerate(METHODS):
            row = self.rows[method]
            top = 198+index*40
            values = [NAMES[method], flag(row['material_valid']), flag(row['seams_valid']),
                      flag(row['render_valid']), '通过' if row['quality_accepted'] is True else
                      '质量拒绝' if row['quality_accepted'] is False else '流程拒绝']
            for (x, _), value in zip(columns, values, strict=True):
                self.text(x, top, value, 13, workflow.TEAL if value == '通过' else workflow.INK, width=238 if x == 60 else 150)
        self.rect(818, 140, 418, 410, '#E4EFF5')
        self.text(838, 158, '代码与复现', 20, workflow.NAVY, True)
        self.paragraph(838, 200, '官方源码按固定提交保存在baseline/，适配器与实验请求可追溯。MeshQuery一栏是项目实现，不能写成作者原版实验。', 378, 14, max_height=115)
        self.paragraph(838, 330, 'FlattenAnything约18分钟完成10,000次迭代；原生UV方向检查拒绝。xatlas默认输出112个零面积面，保留拒绝，不逐面拆岛补洞。', 378, 14, max_height=120)
        self.paragraph(838, 461, '6份GLB独立回读、完整mip及图像核验通过；LLM token/费用未知。', 378, 13, max_height=65)
        self.rect(44, 570, 1192, 77, '#FFF1DC')
        self.paragraph(63, 585, 'ReSeam共享版颜色/接缝通过，严格渲染拒绝。共享率不替代最终质量；本轮仍为UV＋BaseColor范围，人工艺术认可、完整PBR和GPU材质验收未完成。', 1154, 14, max_height=52)

    def finish(self) -> None:
        super().finish()
        reader = PdfReader(self.output)
        text = [page.extract_text() or '' for page in reader.pages]
        assert all(name in '\n'.join(text[5:7]) for name in ['FlattenAnything', 'OptCuts', 'xatlas', 'PartUV', 'MeshQuery'])
        assert all(number in text[8] for number in ['643', '931', '1115', '100.00%', '50.00%', '2.00'])
        assert '项目实现' in text[9] and '严格渲染拒绝' in text[9]
        assert self.rows['blender_smart']['render_valid'] is True
        assert self.rows['partuv']['render_valid'] is True
        fonts: set[str] = set()
        for page in reader.pages:
            resources = page['/Resources'].get_object()
            assert isinstance(resources, DictionaryObject)
            page_fonts = resources['/Font'].get_object()
            assert isinstance(page_fonts, DictionaryObject)
            for reference in page_fonts.values():
                font = reference.get_object()
                assert isinstance(font, DictionaryObject)
                descriptor = font.get('/FontDescriptor')
                if descriptor:
                    descriptor = descriptor.get_object()
                    assert isinstance(descriptor, DictionaryObject)
                    assert '/FontFile2' in descriptor
                    fonts.add(str(font.get('/BaseFont')))
        assert len(fonts) >= 2
        for entry in self.manifest:
            path = self.output.parent / str(entry['file'])
            assert hashlib.sha256(path.read_bytes()).hexdigest() == entry['sha256']
        verification = {'pages': 10, 'page_size_points': [workflow.W, workflow.H],
                        'fonts_embedded': sorted(fonts), 'assets_hashed': len(self.manifest),
                        'method_rows': 8, 'text_extraction': 'PASS', 'baseline_numbers_checked': True,
                        'smart_uv_and_partuv_render_status_preserved': True,
                        'uv_panel_checks': self.panel_counts,
                        'pdf_sha256': hashlib.sha256(self.output.read_bytes()).hexdigest(),
                        'visual_review': 'PENDING'}
        (self.output.parent / 'verification.json').write_text(json.dumps(verification, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path,
                        default=ROOT / 'output/pdf/with-baselines/reseam-uv-workflow-baselines-10-pages.pdf')
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    report = BaselineReport(args.output)
    for page in [report.cover, report.workflow, report.reuse_and_merge, report.packing_and_cleanup,
                 report.baseline_setup]:
        page()
    report.comparison_cards(METHODS[:4], False)
    report.comparison_cards(METHODS[4:], True)
    for page in [report.actual_color, report.reuse_table, report.verdict]:
        page()
    report.finish()


if __name__ == '__main__':
    main()
