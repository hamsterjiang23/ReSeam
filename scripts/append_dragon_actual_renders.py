"""Compare the actual baked color of every available retained UV result."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import cast

from append_dragon_38_final import FinalPages
from build_baseline_input_comparisons import Card, baseline_cards, figure
from build_dragon_wholebody_pdf import APP, OUT, ROOT, TEAL, H, read, sha
from pypdf import PdfReader, PdfWriter
from reportlab.lib.utils import ImageReader

ARTIFACT = ROOT / 'artifacts/dragon_wholebody_refine/actual_render_addendum01'
SOURCE = OUT / '镜像龙制作流程图解_最终38份对照.pdf'
DESTINATION = OUT / '镜像龙制作流程图解_含各UV实际渲染.pdf'
Json = dict[str, object]


def verify_render(image: Path, texture: Path, expected_snapshot: str | None = None) -> Json:
    request_path, report_path = image.parent / 'request.json', image.parent / 'views.json'
    request, report = read(request_path), read(report_path)
    assert request['mode'] == 'basecolor' and request.get('target') is None
    assert Path(str(request['texture'])).resolve() == texture.resolve()
    snapshot = read(Path(str(request['snapshot'])))
    bake_path = texture.parent.parent / 'bake.json'
    baked = read(bake_path)
    assert snapshot['state_hash'] == report['state_hash'] == baked['state_hash']
    if expected_snapshot is not None:
        assert Path(str(request['snapshot'])).resolve() == Path(expected_snapshot).resolve()
    camera = next(item for item in cast(list[Json], request['cameras']) if item['name'] == 'oblique')
    assert camera['azimuth'] == 35 and camera['elevation'] == 15
    return {'snapshot': str(Path(str(request['snapshot'])).resolve()), 'state_hash': report['state_hash'],
        'render_request': request, 'render_report': str(report_path), 'render_request_sha256': sha(request_path),
        'render_report_sha256': sha(report_path), 'bake': str(bake_path), 'bake_sha256': sha(bake_path),
        'texture': str(texture), 'texture_sha256': sha(texture), 'render_image': str(image),
        'render_image_sha256': sha(image), 'camera': camera, 'scope': 'Actual per-snapshot UV sampling of its baked BaseColor'}


def make_cards() -> tuple[list[Card], Json]:
    data = ROOT / 'docs/assets/dragon-paper-baselines'
    for entry in cast(list[Json], read(data / 'manifest.json')['files']):
        path = data / str(entry['path']).replace('\\', '/')
        assert sha(path) == entry['sha256']
    original_dir = data / 'with-original-input/original'
    original = cast(Json, read(data / 'with-original-input/comparison-evidence.json')['original'])
    original_request = read(original_dir / 'color_request.json')
    assert original_request['mode'] == 'basecolor'
    original_card = Card('original_tripo', '原始 Tripo 输入 · 参考',
        {'color': original_dir / 'color.png', 'texture': original_dir / 'texture.jpg'},
        ['岛 121 / 图块 121', f'无声明共享 / 覆盖 {float(str(original["coverage"])) * 100:.2f}%',
         '原始 8192 / 跨几何参考'], True)
    originals = cast(list[Json], read(data / 'measurements.json')['rows'])
    methods = baseline_cards()
    provenance: list[Json] = [{'method': 'original_tripo', 'scope': 'Original UV and original 8192 texture, cross-geometry reference',
        'render_request': original_request, 'render_image': str(original_dir / 'color.png'),
        'render_image_sha256': sha(original_dir / 'color.png'), 'texture_sha256': sha(original_dir / 'texture.jpg')}]
    for row, card in zip(originals, methods, strict=True):
        artifacts = cast(dict[str, str], row['artifacts'])
        if 'color' in artifacts:
            proof = verify_render(Path(artifacts['color']), Path(artifacts['texture']), str(row['snapshot']))
            assert sha(card.files['color']) == proof['render_image_sha256']
            assert sha(card.files['texture']) == proof['texture_sha256']
            provenance.append({'method': card.method, 'available': True, **proof})
        else:
            assert row['pack_status'] == 'REJECTED'
            provenance.append({'method': card.method, 'available': False,
                'reason': row['pack_reason'], 'failures': row['failures'],
                'display': 'Packing failed; no baked color image was produced'})
    candidate = cast(Json, read(APP / 'selection.json')['candidate'])
    last = Card('reseam_further_optimized_38', 'ReSeam 进一步优化',
        {'color': ARTIFACT / 'final38_view/oblique.png',
         'texture': APP / 'candidate_bake/texture/BaseColor_mip0.png'},
        ['岛 80 / 图块 38', f'实际共享 100.0% / 覆盖 {float(str(candidate["coverage"])) * 100:.2f}%',
         '质量：拒绝'], refinement=True)
    provenance.append({'method': last.method, 'available': True,
                       **verify_render(last.files['color'], last.files['texture'], str(candidate['snapshot']))})
    return [original_card, *methods, last], {'methods': provenance,
        'available_render_count_including_original': 8, 'unavailable_methods': ['flatten_anything', 'xatlas'],
        'new_bakes': False, 'uv_modified': False, 'camera': {'azimuth': 35, 'elevation': 15},
        'scope': 'Saved actual BaseColor views and their corresponding atlas textures; original 8K reference and seven 2K outputs'}


class ActualPages(FinalPages):
    bookmark_prefix = 'actual-render'

    def __init__(self, output: Path, comparison: Path, cards: list[Card]) -> None:
        super().__init__(output, comparison)
        self.cards = cards
        for card in cards:
            if 'color' in card.files:
                self.paths['actual_' + card.method] = card.files['color']

    def start(self, title: str, subtitle: str, source: str) -> None:
        super().start(title, subtitle, source)
        self.rect(44, 18, 500, 20, '#F3F7F7', radius=0)
        self.text(44, 21, '制作档案  /  各UV实际颜色渲染', 10, TEAL, True)
        self.rect(1070, 680, 172, 30, '#F3F7F7', radius=0)
        self.text(1124, 689, f'实际渲染 {self.page:02d} / 03', 10, TEAL, True)
        self.checks[-1]['page'] = 42 + self.page

    def build(self) -> None:
        self.start('各UV的实际颜色渲染与对应贴图', '上排为各自UV贴回实际颜色图后的模型，下排为该方法实际生成的颜色图。',
                   '[verified: actual_render_addendum01/evidence.json；各方法clean_color_views/request.json、bake.json]')
        picture = ImageReader(str(self.paths['comparison_38']))
        iw, ih = picture.getSize()
        width = 1192.
        height = width * ih / iw
        self.c.drawImage(picture, 44, H - 148 - height, width, height)
        self.paragraph(63, 530, '每份已生成的颜色图，均核对到该方法自己的UV状态与烘焙记录；所有模型使用同一斜前方角度。[verified: evidence.json methods]',
                       1140, 16, max_height=55)
        self.paragraph(63, 598, 'FlattenAnything与xatlas在排布阶段失败，未生成颜色图，图中保留失败标记。[verified: 原基线measurements.json pack_status、pack_reason]',
                       1140, 15, max_height=50)
        available = [card for card in self.cards if 'color' in card.files]
        assert len(available) == 8
        for page in range(2):
            self.start('实际渲染放大对照 · ' + ('原始参考与三种基线' if page == 0 else 'MeshQuery与ReSeam各版本'),
                '直接展示各方法保存的实际颜色渲染。原始参考为8192贴图，其余输出为2048贴图。',
                '[verified: actual_render_addendum01/evidence.json methods；各自实际贴图与渲染请求]')
            for index, card in enumerate(available[page * 4:(page + 1) * 4]):
                self.panel('actual_' + card.method, card.title, 44 + index * 300, 145, 282, 409)
            self.paragraph(63, 578, '展示范围为实际基础颜色外观，数值质量状态沿用原记录。38份版本的角刺采用已记录的共用颜色目标。[verified: 各自bake.json及质量记录；round03目标记录]',
                           1140, 16, max_height=65)
        assert self.page == 3
        self.c.save()


def main() -> None:
    ARTIFACT.mkdir(parents=True, exist_ok=True)
    backup = ARTIFACT / 'input-42-pages.pdf'
    if not backup.exists():
        assert len(PdfReader(SOURCE).pages) == 42
        shutil.copyfile(SOURCE, backup)
    cards, evidence = make_cards()
    comparison = OUT / '各UV实际颜色渲染与贴图对照_含38份.png'
    figure(cards, comparison, 'color',
        refinement_note='最右为38图块后续整理版本，角刺有记录内的颜色统一。FlattenAnything与xatlas排布失败，未生成颜色图；其余列使用各自实际贴图。',
        missing_output_text='排布失败，未生成颜色图')
    evidence['comparison'] = str(comparison)
    evidence['comparison_sha256'] = sha(comparison)
    evidence['source_pdf_sha256'] = sha(backup)
    temporary = ROOT / 'tmp/pdfs/dragon-actual-render/addendum.pdf'
    temporary.parent.mkdir(parents=True, exist_ok=True)
    pages = ActualPages(temporary, comparison, cards)
    pages.build()
    evidence['pages'] = pages.checks
    evidence_path = ARTIFACT / 'evidence.json'
    evidence_path.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding='utf-8')
    writer = PdfWriter()
    writer.clone_document_from_reader(PdfReader(backup))
    writer.append(str(temporary), outline_item='各UV实际颜色渲染与贴图对照')
    writer.add_attachment('actual-render-evidence.json', evidence_path.read_bytes())
    writer.add_metadata({'/Title': '镜像龙制作流程图解 · 含各UV实际颜色渲染'})
    pending = DESTINATION.with_suffix('.updating.pdf')
    with pending.open('wb') as stream:
        writer.write(stream)
    result = PdfReader(pending)
    assert len(result.pages) == 45 and len(result.attachments) == 6
    for index, page in enumerate(PdfReader(backup).pages):
        first, second = page.get_contents(), result.pages[index].get_contents()
        assert first is not None and second is not None and first.get_data() == second.get_data()
    pending.replace(DESTINATION)
    print(json.dumps({'pdf': str(DESTINATION), 'pages': 45, 'image': str(comparison),
        'actual_renders': 8, 'unavailable_methods': evidence['unavailable_methods']}, ensure_ascii=False))


if __name__ == '__main__':
    main()
