"""Illustrate the retained mirrored-dragon workflow for non-specialist readers.

This report only reads historical mesh states. New diagrams and highlights are
presentation aids, never replacement solving, baking, or acceptance evidence.
"""

from __future__ import annotations

import argparse
import colorsys
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import cast
from xml.sax.saxutils import escape

import numpy as np
from build_uv_workflow_pdf import Report as Drawing
from numpy.typing import NDArray
from PIL import Image, ImageDraw, ImageFilter, ImageFont
from pypdf import PdfReader, PdfWriter
from reportlab.lib.colors import HexColor
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen.canvas import Canvas

ROOT = Path(__file__).resolve().parents[1]
W, H = 1280.0, 720.0
NAVY, INK, MUTED = '#15384A', '#233D4B', '#607984'
TEAL, ORANGE, BG = '#087F83', '#A86428', '#F3F7F7'
TILE_NAMES = [
    '胸腹与身体', '头脸主体', '额头前侧小片', '下颌', '头顶前排角', '头顶后排角',
    '背颈长刺', '颈背小连接片', '上背尖刺', '中背尖刺', '下背尖刺', '尾巴主体',
    '尾根尖刺', '尾端尖刺', '脚与爪子', '大腿', '前肢与肩臂', '身体侧面小片',
    '肩背细小连接片', '靠身体的翼膜', '翅膀外缘大片', '翅膀中段膜片', '翅膀下缘膜片',
    '翅膀支撑长片', '翼尖下缘窄片', '鼻尖小片', '嘴角小片', '内侧脸颊小片',
    '外侧脸颊小片', '眼周片', '头侧连接片', '下颌连接片', '头冠与长角',
    '翼根上侧短片', '翼根前侧短片', '头侧外伸尖角', '肩翼连接小片', '胸前小尖片',
    '翅膀中段窄片', '翼根内侧窄片',
]
Json = dict[str, object]
FloatArray = NDArray[np.float64]
IntArray = NDArray[np.int64]


def read_json(path: Path) -> Json:
    value = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(value, dict):
        raise TypeError(f'Expected an object: {path}')
    return cast(Json, value)


def hash_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def chart_labels(uv_faces: IntArray) -> IntArray:
    """Count UV adjacency through actual shared UV-index edges."""
    parent = np.arange(len(uv_faces), dtype=np.int64)

    def root(index: int) -> int:
        while int(parent[index]) != index:
            parent[index] = parent[int(parent[index])]
            index = int(parent[index])
        return index

    owners: dict[tuple[int, int], int] = {}
    for face_id, corners in enumerate(uv_faces):
        for start, end in zip(corners, np.roll(corners, 1), strict=True):
            edge = (min(int(start), int(end)), max(int(start), int(end)))
            if edge in owners:
                parent[root(face_id)] = root(owners[edge])
            else:
                owners[edge] = face_id
    return np.array([root(i) for i in range(len(uv_faces))], dtype=np.int64)


@dataclass
class Tile:
    number: int
    group_id: str
    first: IntArray
    second: IntArray
    parent_faces: IntArray
    original_faces: IntArray
    color: tuple[int, int, int]
    origin: str
    name: str
    view: str = ''
    visible_pixels: int = 0


class Evidence:
    def __init__(self, output: Path) -> None:
        self.output = output
        self.assets = output.parent / 'source-assets'
        self.assets.mkdir(parents=True, exist_ok=True)
        self.paths: dict[str, Path] = {}
        self.sources: dict[str, str] = {}
        self.checks: dict[str, object] = {}
        self.measurements = read_json(ROOT / 'docs/assets/dragon-paper-baselines/measurements.json')
        rows = cast(list[Json], self.measurements['rows'])
        self.final_row = next(row for row in rows if row['method'] == 'reseam_shared')
        self.final_state_path = Path(str(self.final_row['snapshot']))
        self.state = read_json(self.final_state_path)
        self.summary = read_json(ROOT / 'docs/assets/dragon-midplane-cleanup/summary.json')
        with np.load(self.final_state_path.with_name('arrays.npz'), allow_pickle=False) as data:
            self.vertices = data['vertices'].copy()
            self.faces = data['faces'].copy()
            self.uv = data['uv'].copy()
            self.uv_faces = data['uv_faces'].copy()
        with np.load(ROOT / 'artifacts/meshquery_v1/midplane_cleanup01/create/source_correspondence.npz',
                     allow_pickle=False) as data:
            self.parent_faces = data['source_face_ids'].copy()
            parent_barycentric = data['source_barycentric'].copy()
        with np.load(ROOT / 'artifacts/meshquery_v1/whole_half01/create/source_correspondence.npz',
                     allow_pickle=False) as data:
            self.original_faces = data['source_face_ids'].copy()
        parent_arrays = ROOT / 'artifacts/meshquery_v1/compact_search01/gap8_seed0/pack/snapshot/arrays.npz'
        with np.load(parent_arrays, allow_pickle=False) as data:
            parent_vertices, parent_mesh = data['vertices'], data['faces']
            old_positions = parent_vertices[parent_mesh[self.parent_faces]]
            inherited = np.einsum('fij,fjk->fik', parent_barycentric, old_positions)
        # The source correspondence uses the retained half for both mirrored sides.
        pair_count = len(self.faces) // 2
        error = float(np.max(np.abs(inherited[:pair_count] - self.vertices[self.faces[:pair_count]])))
        if error > 1e-6:
            raise ValueError(f'Historical face-source reconstruction failed: {error}')
        self.checks['retained_half_source_position_error'] = error
        base = ROOT / 'artifacts/meshquery_v1/midplane_cleanup01/seed29/pack/snapshot/arrays.npz'
        with np.load(base, allow_pickle=False) as data:
            assert np.array_equal(self.faces, data['faces'])
            assert np.array_equal(self.vertices, data['vertices'])
        self.checks['screenshot_and_making_geometry_identical'] = True
        region_list = json.loads((ROOT / 'artifacts/meshquery_v1/island_merge01/candidate_regions.json').read_text(encoding='utf-8'))
        regions = {int(row['component']): set(row['faces']) for row in region_list}
        old_arrays = ROOT / 'artifacts/meshquery_v1/whole_half_formal_shared01/shared_blender_concave/pack/snapshot/arrays.npz'
        with np.load(old_arrays, allow_pickle=False) as data:
            old_labels = chart_labels(data['uv_faces'])
            source_uv = data['uv'][data['uv_faces'][self.parent_faces]]
            inherited_uv = np.einsum('fij,fjk->fik', parent_barycentric, source_uv)
        groups = cast(list[Json], self.state['groups'])
        labels = chart_labels(self.uv_faces)
        self.tiles: list[Tile] = []
        retained_shape_errors: list[float] = []
        for index, group in enumerate(groups):
            first = np.asarray(group['first_faces'], dtype=np.int64)
            second = np.asarray(group['second_faces'], dtype=np.int64)
            parent = np.unique(self.parent_faces[first])
            head_count = len(set(parent.tolist()) & regions[36])
            foot_count = len(set(parent.tolist()) & regions[18])
            old_count = len(np.unique(old_labels[parent]))
            if head_count:
                assert head_count == len(parent) and old_count == 16
                origin = '头冠原来16块，重新铺成1块'
            elif foot_count:
                assert foot_count == len(parent) and old_count == 7
                origin = '脚部原来7块，补切口后成1块'
            else:
                assert old_count == 1, (index + 1, old_count)
                origin = '沿用原有展开形状，再重新摆放'
                before = inherited_uv[first].reshape(-1, 2)
                after = self.uv[self.uv_faces[first]].reshape(-1, 2)
                before -= before.mean(axis=0)
                after -= after.mean(axis=0)
                left, values, right = np.linalg.svd(before.T @ after)
                scale = float(values.sum() / (before * before).sum())
                shape_error = float(np.max(np.abs(before @ (left @ right) * scale - after)))
                assert shape_error < 1e-10, (index + 1, shape_error)
                retained_shape_errors.append(shape_error)
            hue = (index * 0.61803398875 + 0.04) % 1
            channels = colorsys.hsv_to_rgb(hue, 0.52, 0.86)
            rgb = (int(channels[0] * 255), int(channels[1] * 255), int(channels[2] * 255))
            tile = Tile(index + 1, str(group['group_id']), first, second, parent,
                        np.unique(self.original_faces[parent]), rgb, origin, TILE_NAMES[index])
            assert len(np.unique(labels[first])) == 1
            assert len(np.unique(labels[second])) == 1
            np.testing.assert_allclose(self.uv[self.uv_faces[first]],
                                       self.uv[self.uv_faces[second]][:, [0, 2, 1]], atol=1e-10, rtol=0)
            self.tiles.append(tile)
        all_faces = np.concatenate([np.concatenate((t.first, t.second)) for t in self.tiles])
        assert len(self.tiles) == 40 and len(np.unique(labels)) == 80
        assert len(all_faces) == len(self.faces) == len(np.unique(all_faces)) == 11338
        self.checks.update({'groups': 40, 'charts': 80, 'all_faces_accounted_for': 11338,
                            'each_group_is_two_actual_charts': True, 'mirror_uv_max_error': 0.0,
                            'retained_38_shape_max_error_after_similarity': max(retained_shape_errors),
                            'head_tile': next(t.number for t in self.tiles if t.name == '头冠与长角'),
                            'foot_tile': next(t.number for t in self.tiles if t.name == '脚与爪子')})
        self.register_sources()
        self.rasters = self.load_rasters()

    def register(self, key: str, source: str | Path) -> None:
        path = Path(source)
        if not path.is_absolute():
            path = ROOT / path
        if not path.is_file():
            raise FileNotFoundError(path)
        self.paths[key] = path
        self.sources[str(path)] = hash_file(path)

    def register_sources(self) -> None:
        paths = {
            'original_model': 'artifacts/meshquery_v1/uv_comparison01/original/oblique.png',
            'original_front': 'artifacts/meshquery_v1/uv_comparison01/original/front.png',
            'original_uv': 'artifacts/meshquery_v1/uv_comparison01/original/uv.png',
            'original_texture': 'model/dragon/tripo_p2/blue dragon 3d model/blue_dragon_3d_model.fbm/blue_dragon_3d_model_basecolor.jpg',
            'mirror_model': 'artifacts/meshquery_v1/whole_half01/compact_views/front.png',
            'unshared_uv': 'artifacts/meshquery_v1/whole_half_formal_unshared01/unshared_blender_concave/views/uv.png',
            'shared_uv': 'artifacts/meshquery_v1/whole_half_formal_shared01/shared_blender_concave/views/uv.png',
            'head_model': 'artifacts/meshquery_v1/island_merge01/view_component36/front.png',
            'foot_model': 'artifacts/meshquery_v1/island_merge01/view_component18/front.png',
            'foot_baseline': 'docs/assets/dragon-uv-final/baseline-foot.png',
            'foot_uncut': 'docs/assets/dragon-uv-final/uncut-foot.png',
            'foot_repaired': 'docs/assets/dragon-uv-final/repaired-foot.png',
            'sole_cut': 'tmp/pdfs/reseam-workflow/sole_cut_lines/sole.png',
            'thin_crop': 'tmp/pdfs/reseam-workflow/thin-lines.png',
            'packing_before': 'artifacts/meshquery_v1/compact_search01/baseline/views_clean/uv.png',
            'packing_after': 'artifacts/meshquery_v1/compact_search01/gap8_seed0/views_clean/uv.png',
            'making_uv': 'artifacts/meshquery_v1/midplane_cleanup01/seed29/views/uv.png',
            'making_model': 'artifacts/meshquery_v1/midplane_cleanup01/seed29/views/oblique.png',
            'making_texture': 'docs/assets/dragon-midplane-cleanup/texture/BaseColor_mip0.png',
            'old_arrays': 'artifacts/meshquery_v1/whole_half_formal_shared01/shared_blender_concave/pack/snapshot/arrays.npz',
            'head_arrays': 'artifacts/meshquery_v1/island_merge01/component36_blender_abf_apply/snapshot/arrays.npz',
            'foot_arrays': 'artifacts/meshquery_v1/island_merge01/sole_reparameterize/snapshot/arrays.npz',
            'foot_uncut_arrays': 'artifacts/meshquery_v1/island_merge01/component18_blender_abf/snapshot/arrays.npz',
            'measurements': 'docs/assets/dragon-paper-baselines/measurements.json',
            'merge_results': 'artifacts/meshquery_v1/island_merge01/merge_results.json',
            'final_summary': 'docs/assets/dragon-midplane-cleanup/summary.json',
            'candidate_regions': 'artifacts/meshquery_v1/island_merge01/candidate_regions.json',
            'making_retrospective': 'docs/results/dragon-final-workflow-retrospective.md',
            'mirror_report': 'docs/results/dragon-whole-half-reuse.md',
            'packing_summary': 'docs/assets/dragon-packing-search/summary.json',
            'merge_summary': 'docs/assets/dragon-uv-final/experiment-summary.json',
            'sole_plan': 'artifacts/meshquery_v1/island_merge01/sole_cut_plan.json',
            'sole_request': 'artifacts/meshquery_v1/island_merge01/sole_reparameterize_request.json',
        }
        for key, path in paths.items():
            self.register(key, path)
        artifacts = cast(dict[str, str], self.final_row['artifacts'])
        for key in ['uv', 'color', 'front', 'texture']:
            self.register('final_' + key, artifacts[key])
        for key, path in [
            ('final_snapshot', self.final_state_path),
            ('final_arrays', self.final_state_path.with_name('arrays.npz')),
            ('midplane_source', ROOT / 'artifacts/meshquery_v1/midplane_cleanup01/create/source_correspondence.npz'),
            ('mirror_source', ROOT / 'artifacts/meshquery_v1/whole_half01/create/source_correspondence.npz'),
        ]:
            self.register(key, path)

    def load_rasters(self) -> dict[str, IntArray]:
        directories = [
            ROOT / 'artifacts/dragon_paper_baselines/round02/evaluation/reseam_shared_2048/uv_views',
            ROOT / 'artifacts/meshquery_v1/midplane_cleanup01/seed29/views',
        ]
        result: dict[str, IntArray] = {}
        for directory in directories:
            views = read_json(directory / 'views.json')
            self.sources[str(directory / 'views.json')] = hash_file(directory / 'views.json')
            for name, raw_path in cast(dict[str, str], views['face_rasters']).items():
                if name in result:
                    continue
                path = Path(raw_path)
                with np.load(path, allow_pickle=False) as data:
                    result[name] = data['face_ids'].astype(np.int64)
                self.sources[str(path)] = hash_file(path)
        return result

    def shaded(self, raster: IntArray, tile: Tile | None = None) -> Image.Image:
        valid = raster >= 0
        image = np.full((*raster.shape, 3), 247, dtype=np.uint8)
        triangles = self.vertices[self.faces]
        normal = np.cross(triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0])
        normal /= np.maximum(np.linalg.norm(normal, axis=1, keepdims=True), 1e-15)
        light = np.abs(normal @ np.array([0.32, 0.62, 0.716]))
        shade = 0.68 + 0.28 * light
        base = np.array([190, 202, 204], dtype=np.float64)
        image[valid] = (base * shade[raster[valid], None]).astype(np.uint8)
        if tile is None:
            palette = np.zeros((len(self.faces), 3), dtype=np.float64)
            for member in self.tiles:
                palette[np.concatenate((member.first, member.second))] = member.color
            image[valid] = (palette[raster[valid]] * shade[raster[valid], None]).astype(np.uint8)
        else:
            selected = np.isin(raster, np.concatenate((tile.first, tile.second)))
            image[selected] = (np.asarray(tile.color) * shade[raster[selected], None]).astype(np.uint8)
        return Image.fromarray(image)

    def prepare_figures(self) -> None:
        font = ImageFont.truetype('C:/Windows/Fonts/msyhbd.ttc', 22)
        for name in ['front', 'back', 'left', 'oblique']:
            image = self.shaded(self.rasters[name])
            image.save(self.assets / f'groups-{name}.png')
            self.paths['groups_' + name] = self.assets / f'groups-{name}.png'
        for tile in self.tiles:
            selected_ids = np.concatenate((tile.first, tile.second))
            counts = {name: int(np.isin(raster, selected_ids).sum()) for name, raster in self.rasters.items()}
            tile.view = max(counts, key=lambda key: counts[key])
            tile.visible_pixels = counts[tile.view]
            if tile.visible_pixels == 0:
                raise ValueError(f'Tile {tile.number} has no observed pixels')
            raster = self.rasters[tile.view]
            image = self.shaded(raster, tile)
            mask = np.isin(raster, selected_ids)
            ys, xs = np.nonzero(mask)
            x0, y0, x1, y1 = int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())
            padding = max(14, round(max(x1 - x0, y1 - y0) * 0.18))
            crop = image.crop((max(0, x0 - padding), max(0, y0 - padding),
                               min(image.width, x1 + padding + 1), min(image.height, y1 + padding + 1)))
            crop.save(self.assets / f'tile-{tile.number:02d}-detail.png')
            draw = ImageDraw.Draw(image)
            draw.rectangle((x0 - 7, y0 - 7, x1 + 7, y1 + 7), outline=tile.color, width=5)
            draw.text((max(5, x0 - 9), max(5, y0 - 34)), str(tile.number), font=font, fill='#233D4B',
                      stroke_width=2, stroke_fill='white')
            image.save(self.assets / f'tile-{tile.number:02d}-model.png')
            self.paths[f'tile_{tile.number}_model'] = self.assets / f'tile-{tile.number:02d}-model.png'
            self.paths[f'tile_{tile.number}_detail'] = self.assets / f'tile-{tile.number:02d}-detail.png'
        self.make_group_contact_sheet()
        self.make_mirror_diagram()
        self.make_historical_locations()
        self.emphasize_recorded_cut()

    def make_historical_locations(self) -> None:
        """Highlight the selected old faces on a saved, pre-merge model view."""
        directory = ROOT / 'artifacts/meshquery_v1/whole_half_formal_shared01/shared_blender_concave/views'
        views = read_json(directory / 'views.json')
        image_path = Path(cast(dict[str, str], views['images'])['front'])
        raster_path = Path(cast(dict[str, str], views['face_rasters'])['front'])
        with np.load(raster_path, allow_pickle=False) as data:
            raster = data['face_ids']
        with np.load(self.paths['mirror_source'], allow_pickle=False) as data:
            pairs = data['face_pairs']
        regions = json.loads(self.paths['candidate_regions'].read_text(encoding='utf-8'))
        for component, key, rgb in [(36, 'head_location', (175, 81, 158)), (18, 'foot_location', (67, 139, 163))]:
            region = np.asarray(next(row['faces'] for row in regions if row['component'] == component))
            both = pairs[np.any(np.isin(pairs, region), axis=1)].reshape(-1)
            assert len(both) == 2 * len(region)
            image = Image.open(image_path).convert('RGB')
            array = np.asarray(image).copy()
            valid = raster >= 0
            gray = np.asarray(image.convert('L'))
            array[valid] = np.stack((gray[valid], gray[valid], gray[valid]), axis=1)
            mask = np.isin(raster, both)
            array[mask] = rgb
            Image.fromarray(array).save(self.assets / f'{key}.png')
            self.paths[key] = self.assets / f'{key}.png'
        self.sources[str(image_path)] = hash_file(image_path)
        self.sources[str(raster_path)] = hash_file(raster_path)

    def emphasize_recorded_cut(self) -> None:
        image = Image.open(self.paths['sole_cut']).convert('RGB')
        array = np.asarray(image).copy()
        red, green, blue = [array[:, :, channel].astype(float) for channel in range(3)]
        mask = (red > green * 1.4) & (red > blue * 1.3) & (red > 30)
        assert int(mask.sum()) == 2474
        expanded = np.asarray(Image.fromarray(mask.astype(np.uint8) * 255).filter(ImageFilter.MaxFilter(3))) > 0
        array[expanded] = [232, 66, 45]
        destination = self.assets / 'sole-cut-emphasized.png'
        Image.fromarray(array).save(destination)
        self.paths['sole_cut'] = destination
        self.checks['recorded_cut_highlight'] = 'Only existing dark-red seam pixels brightened and expanded by one pixel'

    def make_group_contact_sheet(self) -> None:
        sheet = Image.new('RGB', (1600, 1800), 'white')
        draw = ImageDraw.Draw(sheet)
        font = ImageFont.truetype('C:/Windows/Fonts/msyh.ttc', 21)
        for index, tile in enumerate(self.tiles):
            x, y = (index % 5) * 320, (index // 5) * 225
            image = Image.open(self.paths[f'tile_{tile.number}_model']).convert('RGB')
            image.thumbnail((200, 180))
            sheet.paste(image, (x, y + 30))
            detail = Image.open(self.paths[f'tile_{tile.number}_detail']).convert('RGB')
            detail.thumbnail((118, 160))
            sheet.paste(detail, (x + 200, y + 50))
            draw.text((x + 7, y + 4), f'{tile.number:02d} {tile.name}', font=font, fill='black')
        sheet.save(self.assets / 'tile-contact-sheet.png')

    def make_mirror_diagram(self) -> None:
        raster = self.rasters['front']
        valid = raster >= 0
        centers = self.vertices[self.faces].mean(axis=1)
        retained = valid & (centers[np.maximum(raster, 0), 0] > 0.00025)
        image = self.shaded(raster, self.tiles[0])
        array = np.asarray(image).copy()
        array[valid] = [210, 222, 223]
        array[retained] = [74, 160, 165]
        result = Image.fromarray(array)
        result.save(self.assets / 'mirror-half.png')
        self.paths['mirror_half'] = self.assets / 'mirror-half.png'

    def write_receipt(self, page_checks: list[Json], pdf: Path) -> None:
        tiles = [{
            'number': t.number, 'group_id': t.group_id, 'name': t.name, 'origin': t.origin,
            'first_faces': t.first.tolist(), 'second_faces': t.second.tolist(),
            'parent_faces': t.parent_faces.tolist(), 'original_tripo_faces': t.original_faces.tolist(),
            'view': t.view, 'visible_pixels': t.visible_pixels,
        } for t in self.tiles]
        receipt = {'pdf': str(pdf), 'pdf_sha256': hash_file(pdf), 'pages': page_checks,
                   'checks': self.checks, 'tiles': tiles, 'sources': self.sources,
                   'illustration_scope': 'Report-only highlights from retained per-pixel face IDs; newly drawn UV panels from retained coordinates',
                   'decision_scope': 'High-level reasons reconstructed from saved requests, outcomes and retrospective; no private reasoning transcript',
                   'final_snapshot': str(self.final_state_path), 'final_state_hash': self.state['state_hash']}
        (self.output.parent / 'evidence.json').write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding='utf-8')


class Report(Drawing):
    def __init__(self, evidence: Evidence) -> None:
        self.evidence = evidence
        self.output = evidence.output
        self.paths = evidence.paths
        self.images = {}
        self.page = 0
        self.checks: list[Json] = []
        self.panel_counts: list[dict[str, object]] = []
        for name, filename in [('YH', 'msyh.ttc'), ('YHB', 'msyhbd.ttc')]:
            pdfmetrics.registerFont(TTFont(name, 'C:/Windows/Fonts/' + filename, subfontIndex=0))
        self.c = Canvas(str(self.output), pagesize=(W, H), pageCompression=1)
        self.c.setTitle('镜像龙制作流程图解 - 从原模型到40份共用图案')
        self.c.setAuthor('ReSeam')

    def start(self, title: str, subtitle: str, source: str) -> None:
        if self.page:
            self.c.showPage()
        self.page += 1
        self.c.setFillColor(HexColor(BG))
        self.c.rect(0, 0, W, H, fill=1, stroke=0)
        self.text(44, 21, '制作档案  /  镜像龙', 10, TEAL, True)
        self.text(44, 49, title, 27, NAVY, True, width=1190)
        self.text(44, 89, subtitle, 13, MUTED, width=1190)
        self.line(44, 117, 1236, 117, '#D6E3E4')
        self.text(44, 668, source, 8.3, MUTED, width=1190)
        self.text(44, 690, '图中编号仅用于本册查找。制作决定按保存记录整理；新画的定位图会另行注明。', 8.5, MUTED)
        self.text(1167, 688, f'{self.page:02d} / 28', 11, TEAL, True)
        self.c.bookmarkPage(f'page-{self.page}')
        self.c.addOutlineEntry(title, f'page-{self.page}', level=0, closed=False)
        self.checks.append({'page': self.page, 'title': title, 'source': source})

    def image(self, key: str, x: float, top: float, width: float, height: float) -> None:
        cache_key = f'{key}-{round(width)}-{round(height)}'
        if cache_key not in self.images:
            with Image.open(self.paths[key]) as source:
                picture = source.convert('RGB')
                picture.thumbnail((min(1450, round(width * 2.5)), min(1450, round(height * 2.5))), Image.Resampling.LANCZOS)
                self.images[cache_key] = ImageReader(picture.copy())
        picture = self.images[cache_key]
        image_width, image_height = picture.getSize()
        scale = min(width / image_width, height / image_height)
        draw_width, draw_height = image_width * scale, image_height * scale
        self.c.drawImage(picture, x + (width - draw_width) / 2,
                         H - top - draw_height - (height - draw_height) / 2, draw_width, draw_height)

    def source(self, code: str, locator: str) -> str:
        return f'[verified: {code} {locator}]  原始文件位置见末页资料索引。'

    def panel(self, key: str, title: str, x: float, top: float, width: float, height: float) -> None:
        self.rect(x, top, width, height, '#FFFFFF')
        self.text(x + 17, top + 14, title, 16, NAVY, True, width=width - 34)
        self.image(key, x + 12, top + 46, width - 24, height - 58)

    def next_step(self, text: str) -> None:
        self.rect(44, 600, 1192, 47, '#DFEEEE')
        self.text(61, 614, '接下来 →', 15, TEAL, True)
        self.text(154, 614, text, 15, NAVY, width=1058)

    def decision(self, seen: str, why: str, tool: str, returned: str, x: float = 854,
                 top: float = 138, width: float = 382) -> None:
        rows = [('看到了什么', seen), ('为什么这样做', why), ('让工具做什么', tool), ('工具交回什么', returned)]
        cursor = top
        for heading, body in rows:
            self.text(x, cursor, heading, 15, TEAL, True)
            height = self.paragraph(x, cursor + 25, escape(body), width, 16, max_height=85)
            cursor += max(92, height + 43)
        self.checks[-1]['decision'] = {'seen': seen, 'why': why, 'tool': tool, 'returned': returned}

    def tool_note(self, text: str, top: float = 571) -> None:
        self.text(44, top, text, 10, MUTED, width=1190)

    def cover(self) -> None:
        self.start('这只镜像龙，是怎样一步步做出来的？', '从原来的121片，到最后40份左右共用的图案。跟着每次观察、操作和反馈走一遍。',
                   self.source('A / G', 'original；rows.reseam_shared'))
        self.panel('original_model', '开始 · 原来的龙', 44, 140, 570, 330)
        self.panel('final_color', '最后 · 你截图里的共享版本', 638, 140, 598, 330)
        for x, value, label in [(44, '121片', '原来的展开图'), (348, '80片', '最后龙身的展开块'),
                                (652, '40份', '最后需要摆放的图案'), (956, '左右共用', '每份图案服务两侧')]:
            self.rect(x, 489, 280, 87, '#FFFFFF')
            self.text(x + 17, 502, value, 27, TEAL, True)
            self.text(x + 17, 544, label, 13, MUTED)
        self.next_step('先用一页解释“展开图”，然后从原始模型开始。')

    def vocabulary(self) -> None:
        self.start('先懂一个小概念，就能看完整个过程', 'UV，就是把模型表面铺到平面上的位置图。可以把它想成给玩偶做衣服时用的纸样。',
                   self.source('B / H', '共享计数；40组面集合'))
        self.panel('final_color', '立体的龙', 44, 143, 355, 337)
        self.panel('final_uv', '铺到平面上的一块块“纸样”', 421, 143, 394, 337)
        self.text(824, 270, '→', 24, TEAL, True)
        self.panel('final_texture', '把颜色放回这些位置', 865, 143, 371, 337)
        self.rect(44, 500, 1192, 74, '#FFFFFF')
        self.text(64, 517, '两只脚可以各有一张“纸样”，再共用同一份图案。', 20, NAVY, True)
        self.text(64, 548, '所以“龙身有几片”和“平面上摆几份”要分开数。后面一直使用这两个说法。', 15, MUTED)
        self.next_step('按发生顺序，看每一步为什么会接到下一步。')

    def contents(self) -> None:
        self.start('阅读路线 · 一边看变化，一边看为什么这样改', '每一步都回答五件事。看到了什么 → 为什么改 → 工具做什么 → 交回什么 → 下一步。',
                   '[opinion] 本册的阅读组织；事实来源在各页页脚和末页。')
        rows = [
            ('04—06', '从原龙开始', '看输入 → 保留半边 → 镜像并共用'),
            ('07—09', '先整理头冠', '找到碎片 → 尝试并检查 → 整理成一块'),
            ('10—13', '再整理脚部', '合成一块 → 发现白爪问题 → 补切口 → 再看结果'),
            ('14—17', '摆放、上色、处理细条', '调整摆放 → 重做颜色图 → 查细条 → 修正后重做'),
            ('18—19', '走到截图里的版本', '统一摆放 → 看最后模型和真实贴图'),
            ('20—25', '查清40块的来源', '整图对应 → 每页8块 → 全部40块逐一查看'),
            ('26—28', '总结和原始记录', '回看变化 → 查调用记录 → 查资料和结论'),
        ]
        for index, (pages, title, detail) in enumerate(rows):
            y = 140 + index * 61
            self.rect(44, y, 1192, 52, '#FFFFFF')
            self.text(64, y + 13, pages, 17, TEAL, True)
            self.text(187, y + 13, title, 18, NAVY, True)
            self.text(470, y + 16, detail, 14, MUTED)
        self.tool_note('“为什么这样做”为根据记录整理的操作理由。确有记录的观察、返回结果和事后解释分别标记。', 581)
        self.next_step('Step 1 · 打开原来的模型，先看已有的形状和颜色。')

    def original(self) -> None:
        self.start('Step 1 · 先看原来的龙，保留原始输入', '先确认手里有什么，再决定从哪里下手。', self.source('A', 'original；原始逐角UV核对'))
        self.panel('original_model', '原来的模型和颜色', 44, 142, 388, 393)
        self.panel('original_uv', '原来的121片展开图', 450, 142, 380, 393)
        self.decision('原来的龙已经有颜色和展开图，共121片。', '先留下原件，后面才能对照哪些地方改变了。',
                      '打开模型，查看颜色和展开图。', '保存原始状态。原图为8192×8192，模型有10,832个三角面。')
        self.tool_note('工具 · view.render / uv.inspect；本页为保存的原始视图。')
        self.next_step('用户允许另外做一只镜像龙，于是转到“保留半边”的路线。')

    def mirror(self) -> None:
        self.start('Step 2 · 保留半边，再像照镜子一样做出另一半', '这一步另建一只左右一致的新龙。原来的龙继续保留。', self.source('B', 'create/request.json；mirror_variant.json'))
        self.panel('original_front', '原来的龙', 44, 142, 370, 386)
        self.panel('mirror_half', '示意 · 深色为保留侧，浅色为镜像侧', 438, 142, 392, 386)
        self.decision('原来左右两边各自有形状和颜色细节。', '在用户允许的镜像版本里，先做好半边，就有明确的另一半对应。',
                      '沿中间裁切，保留一侧，再镜像生成另一侧。', '得到新的11,450面模型，并保存每个新小面的来源。')
        self.tool_note('工具 · asset.mirror_variant；右图根据历史模型新画，帮助理解保留侧和镜像侧。')
        self.next_step('左右已有一一对应关系，可以让两边用同一份平面图案。')

    def share(self) -> None:
        self.start('Step 3 · 左右两边，共用同一份图案', '两边的表面仍各自存在，平面上的重复图案可以叠放。', self.source('B', '同几何共享 / 无共享对照'))
        self.panel('unshared_uv', '两边分开摆 · 134份', 44, 142, 384, 392)
        self.panel('shared_uv', '左右叠在一起 · 67份', 448, 142, 382, 392)
        self.decision('新模型左右的形状和对应关系一致。', '同一份图案可以同时服务两边，先减少重复占位。',
                      '把主侧的展开位置复制到镜像侧，再按组摆放。', '龙身仍有134片，平面上只需摆67份。')
        self.tool_note('工具 · asset.mirror_variant / uv.pack；图为同一镜像几何的真实共享与无共享结果。')
        self.next_step('重复占位减少了，但头冠和脚部仍被切得很碎。')

    def locate(self) -> None:
        self.start('Step 4 · 先找最值得整理的两处', '重点看头冠和脚部，它们原本连着，却在展开图里分成了多块。', self.source('C', 'candidate_regions；区域视图'))
        self.panel('head_location', '头冠 · 每侧原有16块', 44, 143, 386, 391)
        self.panel('foot_location', '脚部 · 每侧原有7块', 450, 143, 380, 391)
        self.decision('主半边有67块展开图，其中头冠16块、脚部7块。', '先把这两处完整部位整理好，可以减少明显的碎片。',
                      '按实际相连的面找区域，并查看局部。', '圈定头冠363面、脚部1198面；其它44处已经各是一块。')
        self.tool_note('工具 · 原边连通统计 / view.render；高亮由修改前的历史面编号图新画。[verified: C 区域面集合]')
        self.next_step('从头冠开始。先把选中的左右共享临时拆开，做好主侧。')

    def head_attempt(self) -> None:
        self.start('Step 5 · 头冠先试一次，检查反馈后再换方法', '工具能算出坐标，还要读它的检查结果。', self.source('C', 'regions[0].attempts'))
        self.region_panel(36, 'old_arrays', 16, 44, 147, 420, 350, '修改前 · 16块')
        self.rect(488, 147, 342, 350, '#FFFFFF')
        self.text(510, 170, '第一次尝试', 20, NAVY, True)
        self.text(510, 218, '检查未通过', 28, ORANGE, True)
        self.paragraph(510, 269, '保存失败结果，再换一种铺平方式继续尝试。', 292, 20, max_height=110)
        self.text(510, 426, '原始返回  REJECTED', 13, MUTED)
        self.decision('头冠碎成16块，想把相连部分铺成整块。', '先试已有方法，检查未通过就保留结果，再尝试另一种。',
                      '第一次铺平后检查；随后切换到另一种铺平工具。', '第一次被拒绝；第二次返回可用的新状态。')
        self.tool_note('工具 · uv.reparameterize；先 blender_lscm，后 blender_abf。输入、返回见调用索引。')
        self.next_step('检查第二次输出，把整理好的头冠复制到另一侧。')

    def head_result(self) -> None:
        self.start('Step 5 · 头冠从一堆碎片，变成一整块', '这里真正改变了相连表面的展开方式。', self.source('C', 'regions[0]；before_charts / after_charts'))
        self.region_panel(36, 'old_arrays', 16, 44, 143, 371, 385, '原来 · 主侧16块')
        self.region_panel(36, 'head_arrays', 1, 459, 143, 371, 385, '整理后 · 主侧1块')
        self.text(426, 310, '→', 25, TEAL, True)
        self.decision('第二种方法把选中的头冠铺成了1块。', '主侧做好后，另一侧可以复制相同的位置。',
                      '检查左右对应，复制新位置，恢复这组共享。', '整龙由134片变成104片；头冠左右各少15片。')
        self.tool_note('工具 · uv.reparameterize → reuse.check_pair → reuse.apply；局部图按真实坐标新画，各自放大，大小不作比较。')
        self.next_step('头冠整理完成，再处理脚部的7块碎片。')

    def foot_merge(self) -> None:
        self.start('Step 6 · 用相同思路，先把脚部整理成一块', '头冠结果保留，脚部另做尝试，再复制到对侧。', self.source('C', 'regions[1].attempts'))
        self.region_panel(18, 'old_arrays', 7, 44, 143, 371, 385, '原来 · 主侧7块')
        self.region_panel(18, 'foot_uncut_arrays', 1, 459, 143, 371, 385, '第一次整理后 · 主侧1块')
        self.text(426, 310, '→', 25, TEAL, True)
        self.decision('脚部仍有7块，包含明显的白爪颜色边界。', '先尝试整块铺平，再看颜色贴回去后是否自然。',
                      '先后尝试两种铺平方式；通过后复制到另一侧。', '第一种被拒绝；第二种生成1块。整龙104片变92片。')
        self.tool_note('工具 · uv.reparameterize / reuse.check_pair / reuse.apply；局部各自放大，下一页展示首次合并后的真实外观。')
        self.next_step('把颜色放回新位置后，看见了白爪边界变模糊的问题。')

    def foot_problem(self) -> None:
        self.start('Step 6 · 数量变少了，白爪却变模糊了', '把颜色贴回模型后，继续看实际外观。', self.source('D', 'baseline-foot / uncut-foot；merged_islands'))
        self.panel('foot_baseline', '修改前 · 白爪边界', 44, 143, 384, 386)
        self.panel('foot_uncut', '直接合成一块后', 448, 143, 382, 386)
        self.decision('整龙已经降到92片，但脚爪局部的颜色边界不好看。', '片数少只是一步，还要保住能认出来的白爪。',
                      '生成真实颜色图，在同一个角度对比脚部。', '颜色与接缝检查通过，渲染检查拒绝；该外观另行保留。')
        self.tool_note('工具 · material.target / material.bake / quality.validate / view.render；外观问题为历史Agent判断。[opinion]')
        self.next_step('保留头冠成果，回到脚部修改前的状态，重新安排脚底切口。')

    def sole(self) -> None:
        self.start('Step 7 · 从脚底开口，让这块“纸样”铺得更顺', '像展开立体纸壳时找开口一样，先在合适的位置安排切线。', self.source('D', 'sole_cut_plan；sole_reparameterize_request'))
        self.panel('sole_cut', '红线 · 实际选出的脚底切口', 44, 143, 786, 389)
        self.decision('整块脚部需要更合适的开口，白爪附近才能更好地铺开。', '从后踝经过脚底连到脚趾，再加通向其它脚趾的分支。',
                      '沿模型选路径，把选出的52条边交给铺平工具。', '得到带明确脚底切口的新展开图，脚部仍是一整块。')
        self.tool_note('工具 · seam.select（三次选路）→ uv.reparameterize；历史状态补拍图中的原有切线已加亮。')
        self.next_step('重新复制左右位置、摆放、上色，再对照白爪有没有改善。')

    def sole_result(self) -> None:
        self.start('Step 7 · 再看一遍，白爪边界改善到什么程度', '三张图使用相同的脚部观察角度。', self.source('D', '三份脚部图；cases[0..2]'))
        for x, key, title in [(44, 'foot_baseline', '原来的脚'), (449, 'foot_uncut', '直接合并后'),
                               (854, 'foot_repaired', '补脚底切口后')]:
            self.panel(key, title, x, 143, 382, 292)
        self.rect(44, 452, 1192, 126, '#FFFFFF')
        self.text(63, 469, '看图后的决定', 16, TEAL, True)
        self.paragraph(63, 500, '保留补切口的版本作为外观预览。龙身仍为92片，平面上摆46份。[opinion]', 1118, 19, max_height=35)
        self.text(63, 544, '工具反馈 · 颜色和接缝通过；严格渲染检查仍未通过。[verified: D cases[2]]', 15, ORANGE)
        self.tool_note('工具 · reuse.check_pair → reuse.apply → uv.pack → material.bake → quality.validate；判断根据记录整理。', 584)
        self.next_step('脚部组织确定后，尝试把这46份图案摆得更紧凑。')

    def packing(self) -> None:
        self.start('Step 8 · 形状整理好了，再试着摆得更紧凑', '这一步保持每块的内部形状，改变摆放位置、方向和空隙。', self.source('E', 'search.cases；cases[0 / 2]'))
        self.panel('packing_before', '原摆放 · 占画布57.26%', 44, 142, 384, 392)
        self.panel('packing_after', '搜索后 · 占画布59.93%', 448, 142, 382, 392)
        self.decision('46份图案之间还有空隙，摆放可以再试。', '保持已经做好的形状和左右共享，比较几种摆法。',
                      '尝试3种空隙大小，每种试3个摆放起点，共9次。', '最好结果为59.93%；仍是92片、46份图案。')
        self.tool_note('工具 · uv.pack backend=blender_concave；所有失败和候选保留在历史结果中。')
        self.next_step('位置改过后，需要为这些新位置重新生成颜色图。')

    def coloring(self) -> None:
        self.start('Step 9 · 把原来的颜色，重新放到新的位置', '先固定要保留的颜色，再生成新图，最后贴回龙身查看。', self.source('D / E', 'target / bake；实际贴图'))
        self.panel('original_texture', '最初的颜色图', 44, 143, 373, 322)
        self.panel('making_texture', '整理后的真实颜色图', 444, 143, 373, 322)
        self.panel('making_model', '颜色贴回模型后的样子', 844, 143, 392, 322)
        self.rect(44, 482, 1192, 96, '#FFFFFF')
        self.text(62, 497, '为什么重新生成', 16, TEAL, True)
        self.text(62, 528, '各块的位置已经变了。工具按保存的来源重新取色，再检查接缝和模型外观。[verified: D target / bake]', 16, NAVY)
        self.text(62, 555, '镜像侧使用保留侧的颜色，图示展示这条来源关系。[verified: B 镜像来源说明]', 13, MUTED)
        self.tool_note('工具 · material.target → material.bake → quality.validate；本页把多轮重复的上色步骤集中解释。', 584)
        self.next_step('继续检查展开图，发现中间附近有一些细线一样的小块。')

    def thin_problem(self) -> None:
        self.start('Step 10 · 那些细线，究竟从哪里来的？', '先放大定位，再回到立体模型找来源。', self.source('F', 'old_thin_charts；中线来源检查'))
        self.panel('packing_after', '细线出现在这一版展开图里', 44, 143, 424, 392)
        self.panel('thin_crop', '局部放大 · 细窄的小块', 488, 143, 342, 392)
        self.decision('图上有12块很细的条带，缩小时几乎只看见线。', '先查它们对应的真实表面，才能判断该改展开还是改裁切位置。',
                      '把图上编号对应回模型，检查中间裁切留下的窄条。', '定位到6对靠近中线的窄条，来源与位置都有记录。')
        self.tool_note('工具与记录 · view.render 的编号对应 / source_correspondence / 中线预检；放大图为已保存图片。')
        self.next_step('在已允许的新镜像版本里，小幅调整中间的裁切位置。')

    def thin_fix(self) -> None:
        self.start('Step 10 · 修正中间的裁切位置，保留已经整理好的大块', '沿新位置重新生成镜像版本，再重新摆放和上色。', self.source('F', 'variant；packing_search；audit'))
        self.panel('packing_after', '修正前 · 92片 / 46份', 44, 142, 384, 392)
        self.panel('making_uv', '修正后 · 80片 / 40份', 448, 142, 382, 392)
        self.decision('窄条来自中线裁切，已整理好的头冠和脚部可以继承。', '稍微挪动裁切位置，同时保留主侧已有的展开形状。',
                      '比较裁切位置，生成新镜像模型，再试3种摆法。', '12块细条消失，剩80片、40份；选定摆法占画布65.08%。')
        self.tool_note('工具 · asset.mirror_variant → uv.pack → material.target / bake / validate；这一步同时改变了新版本几何。')
        self.next_step('已有完整80片版本，再进入你截图所用的统一对照摆放。')

    def screenshot(self) -> None:
        self.start('Step 11 · 走到你截图里的共享版本', '同一只80片镜像龙，再按该轮统一设置摆放，便于与其它方法一起展示。', self.source('G', 'rows.reseam_shared；input'))
        self.panel('making_uv', '制作阶段 · 占画布65.08%', 44, 142, 384, 392)
        self.panel('final_uv', '截图版本 · 占画布62.93%', 448, 142, 382, 392)
        self.decision('制作完成的龙已经有40份共用图案。', '这轮对照统一摆放设置，直接沿用前面完成的展开形状。',
                      '读取完成状态，按对照设置重新摆放、上色和检查。', '仍为80片、40份，位置有变化，占画布62.93%。')
        self.tool_note('工具 · uv.pack → material.target → material.bake → quality.validate；两列是不同摆放轮次。')
        self.next_step('先看完整成品，再把40份图案逐一对应到龙身。')

    def final_result(self) -> None:
        self.start('Step 11 · 最后的模型、展开图和真实颜色图', '这就是截图里“ReSeam共享”一列所对应的结果。', self.source('G', 'reseam_shared.delivery_artifacts；质量字段'))
        self.panel('final_color', '颜色贴回龙身', 44, 143, 465, 382)
        self.panel('final_uv', '最后摆好的40份图案', 529, 143, 339, 382)
        self.panel('final_texture', '实际生成的颜色图', 888, 143, 348, 382)
        self.text(45, 548, '80片表面展开块  /  40份共用图案  /  2048×2048颜色图', 20, TEAL, True)
        self.tool_note('检查结果 · 颜色、接缝通过；严格渲染检查未通过。[verified: G material_valid / seams_valid / render_valid]', 579)
        self.next_step('Step 12 · 这40份图案各来自哪里？用相同编号和颜色逐块对上。')

    def uv_diagram(self, x: float, top: float, size: float, tile: Tile | None = None,
                   numbers: bool = True) -> None:
        self.rect(x, top, size, size, '#FFFFFF', stroke='#D6E3E4', radius=3)
        selected = [tile] if tile is not None else self.evidence.tiles
        annotations: list[tuple[int, FloatArray, float]] = []
        for member in selected:
            coordinates = self.evidence.uv[self.evidence.uv_faces[member.first]]
            if tile is None:
                lower, extent = np.zeros(2), np.ones(2)
            else:
                lower = coordinates.min(axis=(0, 1))
                extent = coordinates.max(axis=(0, 1)) - lower
            scale = (size - 12) / max(float(extent.max()), 1e-12)
            base = np.array([x + (size - extent[0] * scale) / 2,
                             H - top - size + (size - extent[1] * scale) / 2])
            mapped = (coordinates - lower) * scale + base
            color = ''.join(f'{channel:02x}' for channel in member.color)
            color = '#' + color
            self.c.setFillColor(HexColor(color))
            self.c.setStrokeColor(HexColor('#526F77'))
            self.c.setLineWidth(0.18 if tile is None else 0.24)
            for triangle in mapped:
                path = self.c.beginPath()
                path.moveTo(*triangle[0])
                path.lineTo(*triangle[1])
                path.lineTo(*triangle[2])
                path.close()
                self.c.drawPath(path, fill=1, stroke=1)
            if numbers:
                first = coordinates[:, 1] - coordinates[:, 0]
                second = coordinates[:, 2] - coordinates[:, 0]
                signed = first[:, 0] * second[:, 1] - first[:, 1] * second[:, 0]
                center = mapped[int(np.argmax(np.abs(signed)))].mean(axis=0)
                annotations.append((member.number, center, float(np.abs(signed).sum())))
        placed: list[FloatArray] = []
        for number, anchor, _ in sorted(annotations, key=lambda item: item[2]):
            center = anchor.copy()
            candidates = [anchor]
            for radius in [12, 22, 34, 48]:
                for angle in np.linspace(0, np.pi * 2, 16, endpoint=False):
                    candidates.append(anchor + np.array([np.cos(angle), np.sin(angle)]) * radius)
            for candidate in candidates:
                within = (x + 9 <= candidate[0] <= x + size - 9
                          and H - top - size + 9 <= candidate[1] <= H - top - 9)
                if within and all(np.linalg.norm(candidate - previous) > 17.5 for previous in placed):
                    center = candidate
                    break
            if any(np.linalg.norm(center - previous) <= 17.5 for previous in placed):
                raise ValueError(f'Overview label overlap: {number}')
            placed.append(center)
            if np.linalg.norm(center - anchor) > 2:
                self.c.setStrokeColor(HexColor(MUTED))
                self.c.setLineWidth(0.6)
                self.c.line(float(anchor[0]), float(anchor[1]), float(center[0]), float(center[1]))
            self.c.setFillColor(HexColor('#FFFFFF'))
            self.c.circle(float(center[0]), float(center[1]), 8.0, stroke=0, fill=1)
            self.c.setFillColor(HexColor(NAVY))
            self.c.setFont('YHB', 9)
            self.c.drawCentredString(float(center[0]), float(center[1]) - 3, str(number))

    def overview(self) -> None:
        self.start('Step 12 · 同色同号，就是同一份图案', '右边每一块都能在左边的龙身上找到。接下来五页把40块全部展开讲。', self.source('H', '最终groups / source_face_ids；新画定位图'))
        self.panel('groups_front', '正面 · 同一颜色服务左右两边', 44, 143, 390, 386)
        self.panel('groups_back', '背面 · 看被正面挡住的部位', 450, 143, 390, 386)
        self.uv_diagram(858, 163, 378)
        self.text(863, 143, '最终40块 · 编号1—40', 16, NAVY, True)
        self.text(44, 550, '每块详情按“整龙位置 → 局部放大 → 展开形状 → 来历”从左到右读。', 18, NAVY)
        self.tool_note('颜色和编号为本册新画。形状、面集合、左右配对和来源均读取最终保存状态。', 579)
        self.next_step('先看1—8号。颜色只帮助查找，不代表龙本来的材质颜色。')

    def tiles_page(self, start: int) -> None:
        stop = start + 7
        self.start(f'Step 12 · 第{start}—{stop}块，来自龙的哪里？',
                   '每格从左到右读。整龙位置 → 局部放大 → 这块的展开图 → 制作来历。',
                   self.source('H / C', f'最终groups[{start-1}..{stop-1}]；两层来源面对应'))
        view_names = {'front': '正面', 'back': '背面', 'left': '侧面', 'right': '侧面',
                      'oblique': '斜面', 'underside': '底面'}
        for index, tile in enumerate(self.evidence.tiles[start - 1:stop]):
            column, row = index % 2, index // 2
            x, y = 44 + column * 606, 137 + row * 123
            self.rect(x, y, 586, 114, '#FFFFFF')
            color = '#' + ''.join(f'{channel:02x}' for channel in tile.color)
            self.rect(x + 8, y + 9, 37, 26, color, radius=5)
            self.text(x + 15, y + 14, f'{tile.number:02d}', 14, '#FFFFFF', True)
            self.image(f'tile_{tile.number}_model', x + 4, y + 36, 106, 72)
            self.image(f'tile_{tile.number}_detail', x + 116, y + 18, 112, 88)
            self.uv_diagram(x + 240, y + 16, 89, tile, numbers=False)
            self.text(x + 344, y + 12, tile.name, 15, NAVY, True, width=225)
            self.paragraph(x + 344, y + 39, tile.origin, 222, 13.3, max_height=42)
            self.text(x + 344, y + 83, '左右各1片，共用这一份', 12, TEAL)
            self.text(x + 49, y + 15, view_names[tile.view], 10, MUTED)
        self.tool_note('部位名按图取白话称呼。[opinion] 局部各自放大；小框表示实际可见位置。[verified: H 面编号图]', 638)

    def summary_page(self) -> None:
        self.start('回头看 · 最后40块是这样组成的', '两处重新铺平，其余沿用已有的展开形状；左右一一共用，再重新摆放。', self.source('C / F / G / H', '区域来源核对；数量变化'))
        head = next(tile for tile in self.evidence.tiles if tile.name == '头冠与长角')
        foot = next(tile for tile in self.evidence.tiles if tile.name == '脚与爪子')
        self.rect(44, 143, 780, 357, '#FFFFFF')
        self.text(65, 164, '40份图案的构成', 20, NAVY, True)
        self.text(68, 217, '38份', 42, TEAL, True)
        self.paragraph(234, 222, '沿用原有展开形状，保留主侧，再镜像共用。', 548, 21, max_height=70)
        self.text(68, 316, '+ 1份', 33, TEAL, True)
        self.text(234, 323, f'头冠16块整理成1块 · 本册{head.number}号', 21, NAVY)
        self.text(68, 405, '+ 1份', 33, TEAL, True)
        self.text(234, 412, f'脚部7块整理成1块 · 本册{foot.number}号', 21, NAVY)
        self.panel('final_uv', '最后再摆成一张图', 847, 143, 389, 357)
        self.rect(44, 516, 1192, 63, '#DFEEEE')
        self.text(63, 535, '121 → 新镜像134 → 头冠整理104 → 脚部整理92 → 中线修正80片 / 40份', 21, TEAL, True)
        self.tool_note('“沿用”指来源片的展开形状经裁切继承和整体摆放；全流程包含镜像改模。[verified: H 来源核对；F variant]', 585)
        self.next_step('如果想查工具到底收到什么、返回什么，翻到后两页。')

    def call_index(self) -> None:
        self.start('附录 · 关键工具输入和返回，可以这样查', '正文里的白话操作，与真实工具记录一一对应。完整已采集调用清单嵌入PDF附件。', self.source('I', 'request / result / provenance；tool-calls.json'))
        rows = [
            ('做出镜像龙', 'asset.mirror_variant', '保留侧、裁切位置、来源状态', '新模型、左右配对、每个新面的来源', 'B create'),
            ('把头冠铺成一块', 'uv.reparameterize', '头冠363面、所选方法', '第一次拒绝；第二次成功', 'C component36_*'),
            ('检查并复制另一侧', 'reuse.check_pair / apply', '成对部位、新的主侧展开图', '复制方法、共享提案、新状态', 'C *_check / *_apply'),
            ('把脚部铺成一块', 'uv.reparameterize', '脚部1198面、所选方法', '第一次拒绝；第二次成功', 'C component18_*'),
            ('选择脚底切线', 'seam.select', '脚踝到脚趾的路径端点', '三条路径，合并为52条边', 'D sole_cut_plan'),
            ('补切口后再展开', 'uv.reparameterize', '脚部面集合、52条切线', '新的1块脚部展开图', 'D sole_reparameterize'),
            ('把各块摆进图里', 'uv.pack', '共享组、空隙、摆放起点', '新坐标、占用面积、需重做颜色', 'E / F / G pack'),
            ('重新生成颜色图', 'material.target / bake', '固定颜色来源、新展开位置', '真实颜色图、缩小层级图', 'D / F / G target / bake'),
            ('检查和交付', 'quality.validate / asset.export', '模型、颜色图、检查设置', '各项通过或拒绝、实际GLB', 'F / G validate / export'),
        ]
        widths = [163, 237, 268, 294, 230]
        x_positions = [44, 207, 444, 712, 1006]
        for x, heading in zip(x_positions, ['正文说法', '真实工具名', '交给工具的内容', '工具交回的内容', '记录位置'], strict=True):
            self.text(x + 7, 141, heading, 12.5, TEAL, True)
        for index, row in enumerate(rows):
            y = 169 + index * 44
            self.rect(44, y, 1192, 40, '#FFFFFF', radius=4)
            for column, value in enumerate(row):
                self.paragraph(x_positions[column] + 7, y + 7, escape(value), widths[column] - 14,
                               11.1 if column == 1 else 12, max_height=31)
        self.paragraph(44, 584, '工具的长返回值存入PDF附件 tool-calls.json。每项保留请求、结果和来源路径，打开附件可查原始内容。',
                       1180, 14, max_height=53)

    def sources_page(self) -> None:
        self.start('资料索引与完成程度', '每页页脚的字母，都能在这里找到原始记录。PDF附带完整调用清单和40块来源表。',
                   self.source('F / G / I', '质量结果；来源清单'))
        rows = [
            ('A', '原始龙与原图', 'docs/assets/dragon-uv-final/measurements.json；uv_comparison01/original'),
            ('B', '镜像与左右共享', 'artifacts/meshquery_v1/whole_half01/create；dragon-whole-half-reuse.md'),
            ('C', '头冠、脚部尝试和局部结果', 'artifacts/meshquery_v1/island_merge01/merge_results.json；candidate_regions.json'),
            ('D', '脚底切口、实际颜色和前后图', 'artifacts/meshquery_v1/island_merge01/sole_*；docs/assets/dragon-uv-final'),
            ('E', '九次摆放比较', 'docs/assets/dragon-packing-search/summary.json'),
            ('F', '中线修正与80片制作状态', 'docs/assets/dragon-midplane-cleanup/summary.json；midplane_cleanup01'),
            ('G', '截图所用共享版本', 'docs/assets/dragon-paper-baselines/measurements.json → reseam_shared'),
            ('H', '40块逐面来源', '最终snapshot.groups；midplane / whole_half 两层source_correspondence.npz'),
            ('I', '本册完整核对与工具调用', 'PDF附件 evidence.json / tool-calls.json；PDF旁保留同名文件'),
        ]
        for index, (letter, title, path) in enumerate(rows):
            y = 138 + index * 39
            self.text(48, y + 5, letter, 16, TEAL, True)
            self.text(81, y + 6, title, 13, NAVY, True)
            self.text(330, y + 7, path, 10.5, MUTED, width=905)
            self.line(44, y + 32, 1236, y + 32, '#DBE5E6')
        self.rect(44, 510, 1192, 132, '#FFF0DF')
        self.text(64, 526, '目前做到哪里', 18, ORANGE, True)
        self.paragraph(64, 559, '已完成真实模型、展开图和颜色图。颜色与接缝检查通过，严格渲染检查仍未通过。完整材质、GPU和人工验收尚未完成。[verified: F / G]',
                       1137, 16, max_height=58)
        self.text(64, 619, '新增高亮、编号和箭头是本册讲解图。操作理由按历史记录整理。[opinion]', 12.5, MUTED)

    def region_panel(self, component: int, arrays: str, count: int, x: float, top: float,
                     width: float, height: float, title: str) -> None:
        regions = json.loads(self.paths['candidate_regions'].read_text(encoding='utf-8'))
        faces = next(row['faces'] for row in regions if row['component'] == component)
        self.rect(x, top, width, height, '#FFFFFF')
        self.text(x + 17, top + 16, title, 17, NAVY, True, width=width - 34)
        self.uv_panel(arrays, faces, x + 10, top + 60, width - 20, height - 80, count)

    def finish(self, calls: list[Json]) -> None:
        assert self.page == 28
        call_bytes = json.dumps(calls, ensure_ascii=False, indent=2).encode('utf-8')
        self.c.showOutline()
        self.c.save()
        # Embed readable machine evidence without relying on file-system links.
        writer = PdfWriter(clone_from=str(self.output))
        writer.add_attachment('tool-calls.json', call_bytes)
        embedded = {'checks': self.evidence.checks, 'sources': self.evidence.sources,
                    'tiles': [{'number': t.number, 'group_id': t.group_id, 'name': t.name,
                               'origin': t.origin, 'first_faces': t.first.tolist(),
                               'second_faces': t.second.tolist(), 'parent_faces': t.parent_faces.tolist(),
                               'original_tripo_faces': t.original_faces.tolist(), 'view': t.view,
                               'visible_pixels': t.visible_pixels} for t in self.evidence.tiles]}
        writer.add_attachment('evidence.json', json.dumps(embedded, ensure_ascii=False, indent=2).encode('utf-8'))
        intermediate = self.output.with_suffix('.writing.pdf')
        writer.write(intermediate)
        intermediate.replace(self.output)
        reader = PdfReader(self.output)
        assert len(reader.pages) == 28
        texts = [page.extract_text() or '' for page in reader.pages]
        for index, text in enumerate(texts):
            assert len(text) > 120, (index + 1, 'missing text')
            assert '\ufffd' not in text, (index + 1, 'replacement glyph')
            assert f'{index+1:02d} / 28' in text
        assert all(f'{number:02d}' in ''.join(texts[20:25]) for number in range(1, 41))
        assert '62.93%' in texts[17] and '65.08%' in texts[17]
        assert '严格渲染检查仍未通过' in texts[27]
        assert set(reader.attachments) == {'tool-calls.json', 'evidence.json'}
        assert len(reader.outline) == 28
        self.evidence.checks.update({'pdf_pages': 28, 'chinese_text_extraction': 'PASS',
                                    'outline_entries': 28, 'attachments': list(reader.attachments),
                                    'tool_call_count': len(calls), 'uv_panel_checks': self.panel_counts})
        self.evidence.write_receipt(self.checks, self.output)
        (self.output.parent / 'tool-calls.json').write_bytes(call_bytes)
        (self.output.parent / 'pdf-text.txt').write_text('\n\n'.join(f'PAGE {i+1}\n{t}' for i, t in enumerate(texts)), encoding='utf-8')
        print(json.dumps({'pdf': str(self.output), 'pages': 28, 'bytes': self.output.stat().st_size,
                          'checks': self.evidence.checks}, ensure_ascii=False))


def collect_calls(evidence: Evidence) -> list[Json]:
    """Collect actual retained calls only in the documented workflow directories."""
    roots = [
        'artifacts/meshquery_v1/whole_half01',
        'artifacts/meshquery_v1/whole_half_formal_shared01',
        'artifacts/meshquery_v1/whole_half_formal_unshared01',
        'artifacts/meshquery_v1/island_merge01',
        'artifacts/meshquery_v1/compact_search01',
        'artifacts/meshquery_v1/midplane_cleanup01',
        'artifacts/dragon_paper_baselines/round02/evaluation/reseam_shared_2048',
    ]
    calls: list[Json] = []
    for relative in roots:
        for path in sorted((ROOT / relative).rglob('result.json')):
            result = read_json(path)
            request = path.with_name('request.json')
            if not request.is_file():
                continue
            provenance_path = path.with_name('provenance.json')
            provenance = read_json(provenance_path) if provenance_path.is_file() else {}
            calls.append({'directory': str(path.parent.relative_to(ROOT)), 'request': read_json(request),
                          'result': result, 'provenance': provenance,
                          'order_note': 'Grouped by recorded workflow directory; directory order is not a wall-clock timeline'})
            evidence.sources[str(path)] = hash_file(path)
            evidence.sources[str(request)] = hash_file(request)
            if provenance_path.is_file():
                evidence.sources[str(provenance_path)] = hash_file(provenance_path)
    return calls


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, default=ROOT / 'output/pdf/dragon-making-process/镜像龙制作流程图解.pdf')
    parser.add_argument('--prepare-only', action='store_true')
    args = parser.parse_args()
    evidence = Evidence(args.output)
    evidence.prepare_figures()
    if args.prepare_only:
        print(json.dumps(evidence.checks, ensure_ascii=False))
        return
    report = Report(evidence)
    for page in [report.cover, report.vocabulary, report.contents, report.original, report.mirror,
                 report.share, report.locate, report.head_attempt, report.head_result, report.foot_merge,
                 report.foot_problem, report.sole, report.sole_result, report.packing, report.coloring,
                 report.thin_problem, report.thin_fix, report.screenshot, report.final_result, report.overview]:
        page()
    for start in [1, 9, 17, 25, 33]:
        report.tiles_page(start)
    report.summary_page()
    report.call_index()
    report.sources_page()
    report.finish(collect_calls(evidence))


if __name__ == '__main__':
    main()
