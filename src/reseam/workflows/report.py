"""Compose retained production records without changing their quality identity."""

from __future__ import annotations

import html
import textwrap
from dataclasses import dataclass, field
from pathlib import Path
from typing import cast

import numpy as np
from matplotlib.figure import Figure
from matplotlib.text import Text
from PIL import Image

from ..agent_tools.runner import run_tool
from ..agent_tools.state import file_hash, load_snapshot, object_hash, read_object, write_json

DEFAULT_CAMERAS: dict[str, dict[str, object]] = {
    "basecolor": {"name": "oblique", "azimuth": 35, "elevation": 15},
    "checker": {"name": "front", "azimuth": 0, "elevation": 0},
}


@dataclass
class Card:
    key: str
    title: str
    status: str
    notes: str
    metrics: dict[str, object]
    images: dict[str, Path] = field(default_factory=dict)
    snapshot: Path | None = None
    texture: Path | None = None
    geometry_hash: str | None = None
    state_hash: str | None = None
    target_hash: str | None = None
    protocol: dict[str, object] | None = None
    numeric_accepted: bool | None = None
    missing: list[str] = field(default_factory=list)


def _path(value: object) -> Path:
    if not isinstance(value, str) or not value:
        raise ValueError("REPORT_ABSOLUTE_PATH_REQUIRED")
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise ValueError("REPORT_ABSOLUTE_PATH_REQUIRED:" + value)
    return path.resolve()


def _object(value: object, reason: str) -> dict[str, object]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise ValueError(reason)
    return cast(dict[str, object], value)


def _tracked(path: Path, sources: dict[str, str], missing: list[str]) -> bool:
    if not path.is_file():
        missing.append(str(path))
        return False
    digest = file_hash(path)
    if str(path) in sources and sources[str(path)] != digest:
        raise ValueError("REPORT_SOURCE_CHANGED:" + str(path))
    sources[str(path)] = digest
    return True


def _record(case: dict[str, object], sources: dict[str, str], missing: list[str]) -> dict[str, object]:
    if case.get("evidence") is None:
        return {}
    path = _path(case["evidence"])
    if not _tracked(path, sources, missing):
        return {}
    record: object = read_object(path)
    selectors = case.get("record_path", [])
    if not isinstance(selectors, list):
        raise TypeError("REPORT_RECORD_PATH_LIST_REQUIRED")
    for selector in selectors:
        if isinstance(selector, str):
            record = _object(record, "REPORT_RECORD_PATH_OBJECT_REQUIRED")[selector]
        elif isinstance(selector, int) and not isinstance(selector, bool) and isinstance(record, list):
            sequence = cast(list[object], record)
            record = sequence[selector]
        else:
            raise TypeError("REPORT_RECORD_PATH_INVALID")
    return _object(record, "REPORT_EVIDENCE_RECORD_OBJECT_REQUIRED")


def _card(case: dict[str, object], sources: dict[str, str]) -> Card:
    key = case.get("id")
    if not isinstance(key, str) or not key or any(char not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-" for char in key):
        raise ValueError("REPORT_SAFE_UNIQUE_CASE_ID_REQUIRED")
    card = Card(key, str(case.get("title", key)), str(case.get("status", "历史记录，未重新验收")),
                str(case.get("notes", "")), {})
    record = _record(case, sources, card.missing)
    card.metrics = dict(_object(record.get("metrics", record), "REPORT_METRICS_OBJECT_REQUIRED"))
    images = _object(case.get("images", {}), "REPORT_IMAGES_OBJECT_REQUIRED")
    for mode, value in images.items():
        if mode not in {"basecolor", "checker", "uv", "texture"}:
            raise ValueError("REPORT_UNKNOWN_IMAGE_MODE:" + mode)
        path = _path(value)
        if _tracked(path, sources, card.missing):
            card.images[mode] = path
    if case.get("snapshot") is not None:
        path = _path(case["snapshot"])
        manifest = path / "snapshot.json" if path.is_dir() else path
        if _tracked(manifest, sources, card.missing):
            _tracked(manifest.parent / "arrays.npz", sources, card.missing)
            snapshot = load_snapshot(manifest)
            card.snapshot, card.state_hash = manifest, snapshot.state_hash
            snap = read_object(manifest)
            card.geometry_hash = str(snap["geometry_hash"])
            card.metrics["faces"] = len(snapshot.state.surface.mesh.faces)
    bake: dict[str, object] | None = None
    if case.get("bake") is not None:
        bake_path = _path(case["bake"])
        if _tracked(bake_path, sources, card.missing):
            bake = read_object(bake_path)
            if card.state_hash is None:
                raise ValueError("REPORT_BAKE_REQUIRES_BOUND_SNAPSHOT:" + key)
            if bake.get("state_hash") != card.state_hash:
                raise ValueError("REPORT_BAKE_STATE_MISMATCH:" + key)
            files = _object(bake.get("files"), "REPORT_BAKE_FILE_MANIFEST_REQUIRED")
            if "texture/BaseColor_mip0.png" not in {relative.replace("\\", "/") for relative in files}:
                raise ValueError("REPORT_BAKE_BASECOLOR_NOT_BOUND:" + key)
            for relative, expected in files.items():
                normalized = relative.replace("\\", "/")
                frozen_file = (bake_path.parent / normalized).resolve()
                if (Path(normalized).is_absolute() or ":" in normalized or not frozen_file.is_relative_to(bake_path.parent)
                        or not frozen_file.is_file() or file_hash(frozen_file) != expected):
                    raise ValueError("REPORT_BAKE_FILE_CHANGED:" + key + ":" + relative)
                _tracked(frozen_file, sources, card.missing)
            target_hash = bake.get("target_hash")
            card.target_hash = str(target_hash) if target_hash is not None else None
            for name in ("resolution", "payload_bytes", "padding_pixels"):
                if name in bake:
                    card.metrics[name] = bake[name]
            path = bake_path.parent / "texture/BaseColor_mip0.png"
            if _tracked(path, sources, card.missing):
                card.texture = path
    if case.get("texture") is not None:
        path = _path(case["texture"])
        if _tracked(path, sources, card.missing):
            if bake is not None and card.texture is not None and file_hash(path) != file_hash(card.texture):
                raise ValueError("REPORT_TEXTURE_BAKE_MISMATCH:" + key)
            card.texture = path
    if card.texture is not None:
        if bake is not None and "texture" in card.images and file_hash(card.images["texture"]) != file_hash(card.texture):
            raise ValueError("REPORT_DISPLAY_TEXTURE_BAKE_MISMATCH:" + key)
        card.images["texture"] = card.texture
    if case.get("validation") is not None:
        path = _path(case["validation"])
        if _tracked(path, sources, card.missing):
            validation = read_object(path)
            if bake is None or validation.get("state_hash") != card.state_hash:
                raise ValueError("REPORT_VALIDATION_BINDING_REQUIRED:" + key)
            if validation.get("bake_sha256") != file_hash(_path(case["bake"])):
                raise ValueError("REPORT_VALIDATION_BAKE_MISMATCH:" + key)
            if validation.get("target_hash") != bake.get("target_hash"):
                raise ValueError("REPORT_VALIDATION_TARGET_MISMATCH:" + key)
            card.protocol = _object(validation.get("profile", {}), "REPORT_VALIDATION_PROFILE_REQUIRED")
            card.numeric_accepted = validation.get("quality_accepted") is True
    return card


def _render(card: Card, output: Path, request: dict[str, object], sources: dict[str, str]) -> None:
    if card.snapshot is None:
        return
    cameras = _object(request.get("cameras", DEFAULT_CAMERAS), "REPORT_CAMERAS_OBJECT_REQUIRED")
    for mode in ("basecolor", "checker"):
        if mode in card.images or (mode == "basecolor" and card.texture is None):
            continue
        camera = _object(cameras.get(mode, DEFAULT_CAMERAS[mode]), "REPORT_CAMERA_OBJECT_REQUIRED")
        name = str(camera.get("name", mode))
        if not name.isidentifier():
            raise ValueError("REPORT_SAFE_CAMERA_NAME_REQUIRED")
        arguments: dict[str, object] = {"snapshot": str(card.snapshot), "state_hash": card.state_hash,
            "mode": mode, "size": int(str(request.get("size", 512))), "seams": False,
            "cameras": [{**camera, "name": name}], "timeout_seconds": request.get("timeout_seconds", 300)}
        if mode == "basecolor":
            arguments["texture"] = str(card.texture)
        path = output / f"{card.key}_{mode}.request.json"
        write_json(path, arguments)
        result = run_tool("view.render", path, output / f"{card.key}_{mode}")
        if result.get("status") == "OK":
            images = _object(result.get("images"), "REPORT_RENDER_IMAGES_REQUIRED")
            image = _path(images.get(name))
            if _tracked(image, sources, card.missing):
                card.images[mode] = image
            if "uv" in images:
                uv_image = _path(images["uv"])
                if _tracked(uv_image, sources, card.missing):
                    card.images["uv"] = uv_image
        else:
            card.missing.append(f"{mode}: {result.get('status')} {result.get('reason', '')}")
    if "uv" not in card.images:
        path = output / f"{card.key}_uv.request.json"
        write_json(path, {"snapshot": str(card.snapshot), "state_hash": card.state_hash,
            "mode": "charts", "size": int(str(request.get("size", 512))),
            "cameras": [DEFAULT_CAMERAS["checker"]],
            "timeout_seconds": request.get("timeout_seconds", 300)})
        result = run_tool("view.render", path, output / f"{card.key}_uv")
        if result.get("status") == "OK":
            images = _object(result.get("images"), "REPORT_RENDER_IMAGES_REQUIRED")
            image = _path(images.get("uv"))
            if _tracked(image, sources, card.missing):
                card.images["uv"] = image
        else:
            card.missing.append(f"uv: {result.get('status')} {result.get('reason', '')}")


def _font(request: dict[str, object]) -> str | None:
    if request.get("font") is not None:
        path = _path(request["font"])
        if not path.is_file():
            raise ValueError("REPORT_FONT_MISSING")
        return str(path)
    for path in (Path("C:/Windows/Fonts/msyh.ttc"),
                 Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc")):
        if path.is_file():
            return str(path)
    return None


def _metric_lines(card: Card) -> list[str]:
    metrics = card.metrics
    islands = metrics.get("islands", metrics.get("uv_islands", metrics.get("charts", "未核验")))
    tiles = metrics.get("tiles", metrics.get("independent_tiles", "未核验"))
    islands = "未核验" if islands is None else islands
    tiles = "未核验" if tiles is None else tiles
    lines = [f"面 {metrics.get('faces', '未核验')} / 岛 {islands} / 图块 {tiles}"]
    coverage = metrics.get("coverage")
    if isinstance(coverage, (int, float)) and np.isfinite(coverage):
        lines.append(f"覆盖 {coverage:.2%}")
    if "resolution" in metrics:
        payload = metrics.get("payload_bytes")
        lines.append(f"分辨率 {metrics['resolution']} / 字节 {payload if payload is not None else '未核验'}")
    numeric = "未重新验收" if card.numeric_accepted is None else ("通过声明协议" if card.numeric_accepted else "拒绝")
    geometry = card.geometry_hash[:10] if card.geometry_hash else "未核验"
    protocol = object_hash(card.protocol)[:10] if card.protocol is not None else "未核验"
    lines += [f"几何 {geometry} / 协议 {protocol}", f"数值：{numeric}", card.status]
    if card.notes:
        lines.append(card.notes)
    if card.missing:
        lines.append(f"缺失或未生成 {len(card.missing)} 项，见证据记录")
    return lines


def _figure(cards: list[Card], stem: str, mode: str, output: Path, font: str | None) -> Figure:
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.font_manager import FontProperties

    columns = len(cards)
    width, height, dpi = 430 * columns + 40, 1180, 100
    figure = Figure(figsize=(width / dpi, height / dpi), dpi=dpi, facecolor="#f4f7fa")
    canvas = FigureCanvasAgg(figure)
    properties = FontProperties(fname=font, size=11) if font else FontProperties(size=11)
    title = "历史制作记录" if stem.startswith("history") else "方法与输入对照"
    renderer = canvas.get_renderer()
    heading = title + ("：BaseColor / 贴图" if mode == "basecolor" else "：棋盘格 / UV")
    heading_properties = properties.copy()
    title_size = 20
    heading_properties.set_size(title_size)
    while renderer.get_text_width_height_descent(heading, heading_properties, False)[0] > width - 40:
        title_size -= 1
        if title_size < 8:
            raise ValueError("REPORT_HEADER_TOO_WIDE")
        heading_properties.set_size(title_size)
    heading_artist = figure.text(20 / width, .977, heading, fontproperties=heading_properties, color="#19364c")
    subtitle = "按实际几何与验收协议分组；历史记录与本轮结果分别保留，缺图不补造。"
    subtitle_lines: list[str] = []
    current_line = ""
    for character in subtitle:
        candidate = current_line + character
        if current_line and renderer.get_text_width_height_descent(candidate, properties, False)[0] > width - 40:
            subtitle_lines.append(current_line)
            current_line = character
        else:
            current_line = candidate
    subtitle_lines.append(current_line)
    subtitle_artist = figure.text(20 / width, .948, "\n".join(subtitle_lines),
                                 fontproperties=properties, color="#556575")
    extra_header = 20 * (len(subtitle_lines) - 1)
    text_boxes: list[tuple[Text, float]] = [(heading_artist, width - 40), (subtitle_artist, width - 40)]
    for column, card in enumerate(cards):
        x = 20 + 430 * column
        title_lines = textwrap.wrap(card.title, width=25, break_long_words=True)
        if len(title_lines) > 2:
            raise ValueError("REPORT_CARD_TITLE_TOO_LONG:" + card.key)
        title_artist = figure.text((x + 8) / width, .921 - extra_header / height, "\n".join(title_lines),
                                  fontproperties=properties, fontsize=11, color="#19364c")
        text_boxes.append((title_artist, 405))
        for row, image_mode in enumerate((mode, "texture" if mode == "basecolor" else "uv")):
            axes = figure.add_axes(((x + 5) / width, (height - 105 - extra_header - (row + 1) * 365) / height,
                                    400 / width, 350 / height))
            axes.set_axis_off()
            path = card.images.get(image_mode)
            if path is None:
                axes.text(.5, .5, "缺图 / 未运行\n" + image_mode, ha="center", va="center",
                          color="#a04c4c", fontproperties=properties)
            else:
                with Image.open(path) as picture:
                    rgba = picture.convert("RGBA")
                    background = Image.new("RGBA", rgba.size, "black" if image_mode == "texture" else "white")
                    background.alpha_composite(rgba)
                    axes.imshow(background.convert("RGB"))
        lines = _metric_lines(card)
        wrapped = [line for value in lines for line in textwrap.wrap(value, width=26, break_long_words=True)]
        if len(wrapped) > 15:
            raise ValueError("REPORT_CARD_TEXT_TOO_LONG:" + card.key)
        for index, line in enumerate(wrapped):
            artist = figure.text((x + 8) / width, (height - 850 - extra_header - index * 19) / height, line,
                                 fontproperties=properties, fontsize=10, color="#203c52")
            text_boxes.append((artist, 405))
    canvas.draw()
    renderer = canvas.get_renderer()
    for artist, available in text_boxes:
        bounds = artist.get_window_extent(renderer)
        if bounds.width > available or bounds.x0 < 0 or bounds.x1 > width or bounds.y0 < 0 or bounds.y1 > height:
            raise ValueError("REPORT_TEXT_OVERFLOW")
    figure.savefig(output / f"{stem}.png", dpi=dpi, facecolor=figure.get_facecolor())
    figure.savefig(output / f"{stem}.pdf", facecolor=figure.get_facecolor())
    return figure


def report(request: dict[str, object], output: Path) -> dict[str, object]:
    """Create four comparison sheets and an evidence-backed offline viewer."""
    from matplotlib.backends.backend_pdf import PdfPages

    entries = request.get("cases")
    if not isinstance(entries, list) or not entries or len(entries) > 64:
        raise ValueError("REPORT_CASES_REQUIRE_1_TO_64_RECORDS")
    sources: dict[str, str] = {}
    cards: dict[str, Card] = {}
    for entry in entries:
        card = _card(_object(entry, "REPORT_CASE_OBJECT_REQUIRED"), sources)
        if card.key in cards:
            raise ValueError("REPORT_DUPLICATE_CASE_ID:" + card.key)
        cards[card.key] = card
    if request.get("render_missing", False) is True:
        for card in cards.values():
            _render(card, output, request, sources)
    font = _font(request)
    figures: list[str] = []
    records: dict[str, object] = {}
    comparison_groups: dict[str, list[str]] = {}
    image_root = output / "images"
    image_root.mkdir()
    for card in cards.values():
        images: dict[str, str] = {}
        for name, path in card.images.items():
            destination = image_root / f"{card.key}-{name}.png"
            with Image.open(path) as picture:
                picture.convert("RGBA").save(destination)
            images[name] = destination.relative_to(output).as_posix()
        records[card.key] = {"title": card.title, "status": card.status, "notes": card.notes,
            "metrics": card.metrics, "images": images, "geometry_hash": card.geometry_hash,
            "state_hash": card.state_hash, "target_hash": card.target_hash, "protocol": card.protocol,
            "numeric_accepted": card.numeric_accepted, "missing": card.missing}
        if card.geometry_hash is not None and card.protocol is not None:
            grouping = object_hash({"geometry": card.geometry_hash, "protocol": card.protocol,
                                    "resolution": card.metrics.get("resolution"),
                                    "padding_pixels": card.metrics.get("padding_pixels")})
            comparison_groups.setdefault(grouping, []).append(card.key)
    with PdfPages(output / "comparison-book.pdf") as book:
        for group, prefix in (("overview", "all-methods"), ("history", "history")):
            order = request.get(group, list(cards))
            if not isinstance(order, list) or not order or len(set(map(str, order))) != len(order):
                raise ValueError("REPORT_NONEMPTY_UNIQUE_ORDER_REQUIRED:" + group)
            if any(not isinstance(key, str) or key not in cards for key in order):
                raise ValueError("REPORT_UNKNOWN_CASE_IN_ORDER:" + group)
            selected = [cards[str(key)] for key in order]
            for mode, suffix in (("basecolor", "basecolor"), ("checker", "checker-uv")):
                stem = f"{prefix}-{suffix}"
                figure = _figure(selected, stem, mode, output, font)
                book.savefig(figure)
                figure.clear()
                figures.append(stem + ".png")
    if any(not Path(path).is_file() or file_hash(Path(path)) != digest for path, digest in sources.items()):
        raise ValueError("REPORT_SOURCE_CHANGED_DURING_RENDER")
    evidence = {"execution_scope": "Retained evidence and optional views only; no unwrap/pack/bake/quality revalidation",
        "cases": records, "comparison_groups": comparison_groups,
        "comparison_group_scope": "Geometry and declared numeric protocol only; no automatic target equality or ranking",
        "camera_alignment": "Supplied images retain their declared provenance; no inferred camera equality",
        "overview_order": request.get("overview", list(cards)),
        "history_order": request.get("history", list(cards)), "source_sha256": sources,
        "figures": {name: file_hash(output / name) for name in figures},
        "checks": {"sources_unchanged": True, "text_within_bounds": True}, "visual_review": "pending"}
    write_json(output / "evidence.json", evidence)
    panels = "".join(f'<h2>{html.escape(name)}</h2><a href="{name}"><img src="{name}" '
                     'style="width:100%;height:auto"></a>' for name in figures)
    rows = "".join(f'<tr><td>{html.escape(card.title)}</td><td>{html.escape(card.status)}</td>'
                   f'<td>{html.escape(" | ".join(_metric_lines(card)))}</td></tr>' for card in cards.values())
    page = '<!doctype html><meta charset="utf-8"><title>ReSeam 制作对照</title>'
    page += '<style>body{font:16px sans-serif;margin:24px;background:#f4f7fa}td{padding:8px;vertical-align:top}</style>'
    page += '<h1>ReSeam 制作与方法对照</h1><p>原始状态、几何、协议与失败分别保留；未重新验收。</p>'
    page += panels + '<table>' + rows + '</table><p><a href="evidence.json">证据 JSON</a> · '
    page += '<a href="comparison-book.pdf">对照 PDF</a></p>'
    (output / "index.html").write_text(page, encoding="utf-8")
    return {"status": "OK", "execution_success": True, "report": str(output / "index.html"),
        "evidence": str(output / "evidence.json"), "figures": figures, "case_count": len(cards),
        "sources_unchanged": True, "quality_revalidated": False, "visual_review": "pending"}
