"""Show the final 38-tile result and extend the user's baseline figure at the end."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import cast

from build_baseline_input_comparisons import Card, baseline_cards, figure
from build_dragon_wholebody_pdf import APP, BG, MUTED, NAVY, OUT, ROOT, TEAL, Book, H, W, read, sha
from pypdf import PdfReader, PdfWriter
from reportlab.lib.utils import ImageReader

ARTIFACT = ROOT / 'artifacts/dragon_wholebody_refine/final_pdf_addendum01'
SOURCE_PDF = OUT / '镜像龙制作流程图解_全身整理新版.pdf'
PDF = OUT / '镜像龙制作流程图解_最终38份对照.pdf'
Json = dict[str, object]


def build_comparison() -> tuple[Path, Json]:
    original_dir = ROOT / 'docs/assets/dragon-paper-baselines/with-original-input'
    source = read(original_dir / 'comparison-evidence.json')
    original = cast(Json, source['original'])
    original_card = Card('original_tripo', '原始 Tripo 输入 · 参考',
        {'front': original_dir / 'original/front.png', 'uv': original_dir / 'original/uv.png'},
        ['岛 121 / 图块 121', f'无声明共享 / 覆盖 {float(str(original["coverage"])) * 100:.2f}%',
         '原始 8192 / 跨几何参考'], True)
    metrics = read(APP / 'candidate_inspect/uv_metrics.json')
    candidate = cast(Json, read(APP / 'selection.json')['candidate'])
    views = read(ARTIFACT / 'checker/views.json')
    assert views['state_hash'] == candidate['state_hash']
    assert metrics['metrics_verified'] is True and metrics['actual_independent_tiles'] == 38
    assert metrics['uv_connected_charts'] == 80
    shared = cast(Json, metrics['global'])['actual_shared_surface_fraction']
    last = Card('reseam_further_optimized_38', 'ReSeam 进一步优化',
        {'front': ARTIFACT / 'checker/front.png', 'uv': ARTIFACT / 'checker/uv.png'},
        ['岛 80 / 图块 38', f'实际共享 {float(str(shared)) * 100:.1f}% / 覆盖 {float(str(candidate["coverage"])) * 100:.2f}%',
         '质量：拒绝'], refinement=True)
    cards = [original_card, *baseline_cards(), last]
    assert len(cards) == 10
    path = OUT / '最终对照_含38份进一步优化.png'
    figure(cards, path, 'uv', refinement_note='最右“进一步优化”指独立图块40→38；覆盖63.05%，严格质量仍拒绝。中间八条为历史冻结基线，最右为后续整理结果。')
    sources = {str(file): sha(file) for card in cards for file in card.files.values()}
    sources[str(ROOT / 'docs/assets/dragon-paper-baselines/measurements.json')] = sha(ROOT / 'docs/assets/dragon-paper-baselines/measurements.json')
    return path, {'columns': [card.method for card in cards], 'last_column_title': last.title,
        'candidate_state_hash': candidate['state_hash'], 'charts': 80, 'tiles': 38,
        'coverage': candidate['coverage'], 'actual_shared_surface_fraction': shared,
        'checker_camera': {'azimuth': 0, 'elevation': 0},
        'new_bakes': False, 'uv_modified': False, 'source_hashes': sources,
        'figure': str(path), 'figure_sha256': sha(path),
        'scope': 'Historical eight baseline columns followed by a later refinement result, with numerical rejection retained'}


class FinalPages(Book):
    bookmark_prefix = 'final-38'

    def __init__(self, output: Path, comparison: Path) -> None:
        super().__init__(output)
        self.paths['comparison_38'] = comparison

    def start(self, title: str, subtitle: str, source: str) -> None:
        if self.page:
            self.c.showPage()
        self.page += 1
        self.rect(0, 0, W, H, BG, radius=0)
        self.text(44, 21, '制作档案  /  最终38份图案', 10, TEAL, True)
        self.text(44, 49, title, 27, NAVY, True, width=1190)
        self.text(44, 91, subtitle, 13, MUTED, width=1190)
        self.line(44, 117, 1236, 117, '#D6E3E4')
        self.text(44, 668, source, 8.5, MUTED, width=1190)
        self.text(44, 691, '最终结果的模型、真实展开图和各方法对照。', 8.5, MUTED)
        self.text(1132, 689, f'最终展示 {self.page:02d} / 02', 10, TEAL, True)
        key = f'{self.bookmark_prefix}-{self.page}'
        self.c.bookmarkPage(key)
        self.c.addOutlineEntry(title, key, level=0, closed=False)
        self.checks.append({'page': 40 + self.page, 'title': title, 'source': source})

    def build(self) -> None:
        self.start('进一步优化 · 最终38份图案', '把最终模型、完整展开图和真实颜色图放在一起查看。',
                   '[verified: round03/appearance/candidate_inspect/uv_metrics.json；candidate_bake；最终快照]')
        self.panel('candidate_oblique', '最终模型 · 真实颜色', 44, 142, 370, 385)
        self.text(449, 154, '最终完整展开图 · 38份', 17, NAVY, True)
        self.uv(Path(str(self.candidate['snapshot'])), 439, 191, 390, 336)
        self.panel('texture', '实际2048颜色图', 854, 142, 382, 385)
        self.paragraph(62, 544, '80片表面 / 38份独立图案 / 画布利用率63.05% / 实际共享表面100%。[verified: 最终UV检查]',
                       1150, 17, max_height=35)
        self.next_step('最后一页将38份版本放进全部方法对照图，标为“进一步优化”。')
        self.start('全部方法对照 · 最右为38份进一步优化', '原始输入、八条历史基线和本轮最终38份结果同图展示；放大可查看各列。',
                   '[verified: final_pdf_addendum01/comparison-evidence.json；原基线measurements.json；最终UV检查]')
        width = 1192.
        picture = ImageReader(str(self.paths['comparison_38']))
        iw, ih = picture.getSize()
        height = width * ih / iw
        self.c.drawImage(picture, 44, H - 148 - height, width, height)
        self.paragraph(63, 531, '绿色边框的最后一列为“ReSeam 进一步优化”，对应80片表面、38份图案。模型与展开图来自同一个最终状态。[verified: 最终checker视图及状态哈希]',
                       1140, 16, max_height=55)
        self.paragraph(63, 597, '“进一步优化”记录图案数量40→38。覆盖率为63.05%，严格质量检查仍拒绝。[verified: 最终UV检查、candidate_validate/result.json]',
                       1140, 15, max_height=46)
        assert self.page == 2
        self.c.save()


def main() -> None:
    ARTIFACT.mkdir(parents=True, exist_ok=True)
    base = ARTIFACT / 'original-40-pages.pdf'
    if not base.exists():
        assert len(PdfReader(SOURCE_PDF).pages) == 40
        shutil.copyfile(SOURCE_PDF, base)
    assert len(PdfReader(base).pages) == 40
    comparison, evidence = build_comparison()
    evidence['base_pdf'] = str(base)
    evidence['base_pdf_sha256'] = sha(base)
    evidence['attachment_from_user'] = 'C:/Users/admin/AppData/Local/Temp/codex-clipboard-679451d9-9479-4a98-867d-792b22385fee.png'
    evidence['attachment_sha256'] = sha(Path(str(evidence['attachment_from_user'])))
    temporary = ROOT / 'tmp/pdfs/dragon-final-38/addendum.pdf'
    temporary.parent.mkdir(parents=True, exist_ok=True)
    pages = FinalPages(temporary, comparison)
    pages.build()
    evidence['pages'] = pages.checks
    evidence_path = ARTIFACT / 'comparison-evidence.json'
    evidence_path.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding='utf-8')
    writer = PdfWriter()
    writer.clone_document_from_reader(PdfReader(base))
    writer.append(str(temporary), outline_item='最终38份展示与全部方法对照')
    writer.add_attachment('final-38-display.json', evidence_path.read_bytes())
    writer.add_metadata({'/Title': '镜像龙制作流程图解 · 含最终38份与进一步优化对照'})
    pending = PDF.with_suffix('.updating.pdf')
    with pending.open('wb') as stream:
        writer.write(stream)
    result = PdfReader(pending)
    assert len(result.pages) == 42 and len(result.attachments) == 5
    assert '38份' in result.pages[40].extract_text() and '进一步优化' in result.pages[41].extract_text()
    for index, old in enumerate(PdfReader(base).pages):
        before, after = old.get_contents(), result.pages[index].get_contents()
        assert before is not None and after is not None
        assert before.get_data() == after.get_data()
    pending.replace(PDF)
    (OUT / 'pdf-text.txt').write_text('\n\n'.join(page.extract_text() for page in result.pages), encoding='utf-8')
    print(json.dumps({'pdf': str(PDF), 'pages': 42, 'comparison': str(comparison),
                      'state_hash': evidence['candidate_state_hash'], 'sha256': sha(PDF)}, ensure_ascii=False))


if __name__ == '__main__':
    main()
