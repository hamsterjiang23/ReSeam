"""Compose retained dragon-part results; render views without changing UV or baking."""

from __future__ import annotations

import argparse
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import cast

import matplotlib

matplotlib.use("Agg")

from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure
from matplotlib.font_manager import FontProperties
from matplotlib.patches import FancyBboxPatch
from matplotlib.text import Text
from PIL import Image

from reseam.agent_tools.runner import run_tool
from reseam.agent_tools.state import file_hash, read_object, write_json

ROOT = Path(__file__).resolve().parents[1]
FONT = "C:/Windows/Fonts/msyh.ttc"
BOLD = "C:/Windows/Fonts/msyhbd.ttc"
CAMERA = {"name": "oblique", "azimuth": 35, "elevation": 15}


@dataclass
class Card:
    key: str
    title: str
    subtitle: str
    snapshot: Path
    texture: Path | None
    metrics: dict[str, object]
    lines: list[str]
    status: str
    tone: str = "neutral"


def rows_by_name(path: Path) -> dict[str, dict[str, object]]:
    rows = cast(list[dict[str, object]], read_object(path)["rows"])
    return {str(row["name"]): row for row in rows}


def make_cards() -> tuple[list[Card], list[Path]]:
    final_path = ROOT / "artifacts/dragon_parts/final/summary.json"
    old_path = ROOT / "artifacts/dragon_parts/round03/summary.json"
    reference_path = ROOT / "docs/assets/dragon-paper-baselines/with-original-input/comparison-evidence.json"
    final, old = rows_by_name(final_path), rows_by_name(old_path)
    summary = read_object(final_path)
    original = cast(dict[str, object], read_object(reference_path)["original"])
    segmented = Path("E:/Downloads/blue_dragon_3d_mode_partedl.fbx.glb")
    raw = segmented.read_bytes()
    chunk_size, kind = struct.unpack_from("<I4s", raw, 12)
    if kind != b"JSON":
        raise ValueError("GLB JSON chunk required")
    import json

    source = cast(dict[str, object], json.loads(raw[20:20 + chunk_size]))
    meshes = cast(list[dict[str, object]], source["meshes"])
    accessors = cast(list[dict[str, object]], source["accessors"])
    input_faces = 0
    for mesh in meshes:
        for primitive in cast(list[dict[str, object]], mesh["primitives"]):
            attributes = cast(dict[str, object], primitive["attributes"])
            assert "TEXCOORD_0" not in attributes
            accessor = accessors[int(str(primitive["indices"]))]
            input_faces += int(str(accessor["count"])) // 3
    assert len(meshes) == 9 and input_faces == 10832 and not source.get("images")
    cards = [Card(
        "original", "原始 Tripo · 外观参考", "原始几何 / 原 UV / 原色", Path(str(original["snapshot"])),
        Path(str(original["texture"])), original,
        [f"{original['faces']:,} 面 / 岛 {original['charts']} / 图块 {original['independent_tiles']}",
         f"覆盖 {float(str(original['coverage'])):.2%} / 原始交叠保留",
         "8192² 原始 BaseColor · JPG", "跨几何参考，不计新增复用"],
        "参考输入 · 未做本轮重烘焙验收", "reference"), Card(
        "segmented", "Tripo 分割输入", "9 parts · 分区颜色示意", ROOT / "artifacts/dragon_parts/round01/prepared_fans/snapshot/snapshot.json",
        None, {"parts": len(meshes), "faces": input_faces, "uv_present": False, "texture_present": False},
        [f"{input_faces:,} 面 / {len(meshes)} 个部件", "输入未包含 UV / 材质贴图",
         "分区颜色用于展示部件身份", "仅提供部件范围与组装位置"], "分割参考 · 无 BaseColor 可对比")]
    middle = old["mirror_shared2048"]
    cards.append(Card(
        "old_shared", "旧 UV 共享 · 中间版", "沿用旧岛，未完成分 part 重建", Path(str(middle["snapshot"])),
        ROOT / "artifacts/dragon_parts/round03/mirror_shared2048/bake/texture/BaseColor_mip0.png", middle,
        [f"{middle['faces']:,} 面 / 岛 {middle['uv_islands']}",
         f"排布代表 {middle['packing_owners']} / 共享组 {middle['shared_groups']}",
         f"覆盖 {float(str(middle['coverage'])):.2%} / 2048²",
         "RGBA8 全 mip 21.33 MiB"], "数值拒绝 · 保留历史中间结果", "rejected"))
    for key, title, subtitle, tone in [
        ("original2048", "分 part 重建 · 无共享", "清理微小碎片 / 部件合岛", "neutral"),
        ("mirror_unshared2048", "局部镜像 · 无共享", "镜像对照 / 每块独占", "neutral"),
        ("mirror_shared2048", "最终方案 · 镜像共享", "同镜像几何 / 7 组复用", "final"),
    ]:
        row = final[key]
        coverage = float(str(row["coverage"]))
        shared = float(str(row["shared_member_surface_fraction"]))
        payload = int(str(row["payload_bytes"])) / 1048576
        assert row["numeric_accepted"] is True and row["validated_mips"] == [0, 1, 2]
        cards.append(Card(
            key, title, subtitle, Path(str(row["snapshot"])),
            ROOT / f"artifacts/dragon_parts/final/{key}/bake/texture/BaseColor_mip0.png", row,
            [f"{row['faces']:,} 面 / 岛 {row['uv_islands']} / 图块 {row['independent_tiles']}",
             f"共享组 {row['shared_groups']} / 共享成员表面 {shared:.1%}",
             f"覆盖 {coverage:.2%} / 2048²",
             f"RGBA8 全 mip {payload:.2f} MiB"], "UV＋BaseColor 通过 · LOD 0 / 1 / 2", tone))
    small = cast(dict[str, object], summary["shrunk1024"])
    shared_row = final["mirror_shared2048"]
    assert small["same_uv_as_2048"] is True and small["numeric_accepted"] is None
    cards.append(Card(
        "shared1024", "1K 缩图 · 诊断版", "保持最终 UV / 另存缩图", Path(str(shared_row["snapshot"])),
        ROOT / "artifacts/dragon_parts/final/shrink1024/texture/BaseColor_mip0.png", small,
        [f"{shared_row['faces']:,} 面 / 岛 {shared_row['uv_islands']} / 图块 {shared_row['independent_tiles']}",
         f"共享组 {shared_row['shared_groups']} / 覆盖 {float(str(shared_row['coverage'])):.2%}",
         "1024² / padding 4 px / 同一 UV",
         f"RGBA8 全 mip {int(str(small['payload_bytes'])) / 1048576:.2f} MiB"],
        "诊断预览 · 尚未完成数值验收", "diagnostic"))
    assert summary["same_mirror_geometry_exact"] is True
    return cards, [final_path, old_path, reference_path, segmented]


def render_card(card: Card, output: Path) -> dict[str, object]:
    folder = output / "renders" / card.key
    request = {
        "snapshot": str(card.snapshot.resolve()), "size": 900, "seams": False, "annotate": False,
        "mode": "regions" if card.texture is None else "basecolor", "uv_labels": "owners",
        "cameras": [CAMERA], "timeout_seconds": 120,
    }
    if card.texture is not None:
        request["texture"] = str(card.texture.resolve())
    request_path = output / f"{card.key}_request.json"
    if folder.exists():
        assert read_object(folder / "request.json") == request, "Existing render has a different request"
    else:
        write_json(request_path, request)
        result = run_tool("view.render", request_path, folder)
        if result.get("status") != "OK":
            raise RuntimeError(f"Render failed: {card.key}: {result.get('reason')}")
    result = read_object(folder / "result.json")
    views = read_object(folder / "views.json")
    assert result.get("status") == "OK" and views["cameras"] == [CAMERA]
    assert views["mode"] == request["mode"]
    with Image.open(folder / "oblique.png") as im:
        assert im.size == (900, 900)
    return {"key": card.key, "title": card.title, "snapshot": str(card.snapshot),
            "snapshot_sha256": file_hash(card.snapshot), "state_hash": views["state_hash"],
            "texture": str(card.texture) if card.texture else None,
            "texture_sha256": file_hash(card.texture) if card.texture else None,
            "render": str(folder / "oblique.png"), "render_sha256": file_hash(folder / "oblique.png"),
            "uv": str(folder / "uv.png") if card.texture else None,
            "camera": CAMERA, "metrics": card.metrics, "lines": card.lines, "status": card.status}


def compose(cards: list[Card], output: Path, include_uv: bool) -> dict[str, object]:
    panel, gap, margin, dpi = 480, 14, 24, 125
    width = 2 * margin + len(cards) * panel + (len(cards) - 1) * gap
    height = 1950 if include_uv else 1480
    fig = Figure(figsize=(width / dpi, height / dpi), dpi=dpi, facecolor="#f5f7fa")
    canvas = FigureCanvasAgg(fig)
    text_boxes: list[tuple[Text, float]] = []

    def text(x: float, y: float, content: str, size: float, color: str = "#23435d",
             bold: bool = False, available: float | None = None) -> None:
        artist = fig.text(x / width, 1 - y / height, content, va="top", color=color,
                          fontproperties=FontProperties(fname=BOLD if bold else FONT, size=size))
        text_boxes.append((artist, available if available is not None else width - x - margin))

    text(30, 18, "恐龙分部件 UV 整理：同视角外观 / 真实 BaseColor 图集" + (" / UV 布局" if include_uv else ""), 25, bold=True)
    text(30, 80, "部件划分 → 微小碎片清理 → 部件合岛与重展开 → 局部镜像 → UV 共享 → 统一图集；使用已保存的真实结果，未追加求解。", 14)
    text(30, 123, "原始与中间版含不同几何；镜像无共享 / 最终共享使用相同几何。分割输入用分区颜色显示，缺失贴图与 UV 留空。", 13, "#63778a")
    row_count = 3 if include_uv else 2
    metrics_top = 250 + row_count * 470 + 10
    card_bottom = metrics_top + 148
    tones = {
        "neutral": ("#ffffff", "#d1dce6", "#23435d"),
        "reference": ("#fffaf0", "#c99540", "#956321"),
        "rejected": ("#fffafb", "#d5b1b5", "#b04e58"),
        "final": ("#eaf8f5", "#078886", "#087f7d"),
        "diagnostic": ("#fffdf6", "#c6af72", "#987326"),
    }
    for index, card in enumerate(cards):
        x = margin + index * (panel + gap)
        fill, border, accent = tones[card.tone]
        fig.add_artist(FancyBboxPatch(
            (x / width, 1 - card_bottom / height), panel / width, (card_bottom - 180) / height,
            boxstyle="round,pad=0,rounding_size=0.006", transform=fig.transFigure,
            facecolor=fill, edgecolor=border, linewidth=1.4, zorder=-1))
        text(x + 14, 196, card.title, 15.5, accent, bold=True, available=panel - 28)
        text(x + 14, 232, card.subtitle, 11, "#63778a", available=panel - 28)
        paths: list[tuple[Path | None, str]] = [
            (output / "renders" / card.key / "oblique.png", "同视角模型"),
            (card.texture, "原文件未包含贴图\n此处不生成替代贴图"),
        ]
        if include_uv:
            paths.append((output / "renders" / card.key / "uv.png" if card.texture else None,
                          "原文件未包含 UV\n不将导入初始化 UV 当作输入"))
        for row, (path, placeholder) in enumerate(paths):
            top = 275 + row * 470
            axes = fig.add_axes(((x + 10) / width, 1 - (top + 435) / height, 460 / width, 435 / height))
            axes.set_facecolor("#eef2f6")
            axes.set_xticks([])
            axes.set_yticks([])
            for spine in axes.spines.values():
                spine.set_visible(False)
            text(x + 14, top - 20, ["部件分区示意" if card.texture is None else "BaseColor 外观", "实际 BaseColor 贴图", "UV 布局 · 按排布代表标号"][row], 9.5, "#63778a", available=panel - 28)
            if path is None:
                axes.text(0.5, 0.5, placeholder, ha="center", va="center", color="#9c656a",
                          fontproperties=FontProperties(fname=FONT, size=13), linespacing=1.7)
            else:
                with Image.open(path) as picture:
                    image = picture.convert("RGBA")
                    if row == 1:
                        backdrop = Image.new("RGBA", image.size, (0, 0, 0, 255))
                        image = Image.alpha_composite(backdrop, image)
                    image.thumbnail((1000, 1000), Image.Resampling.LANCZOS)
                    axes.imshow(image.convert("RGB"), interpolation="lanczos")
        for line_index, line in enumerate(card.lines):
            text(x + 14, metrics_top + line_index * 28, line, 11.6, available=panel - 28)
        text(x + 14, metrics_top + 116, card.status, 10.8, accent, bold=True, available=panel - 28)
    text(30, card_bottom + 32, "岛 = 连通 UV 岛；图块 = 独立排布块。旧中间版仅统计排布代表，存在拒绝项，不与最终合格图块混用。", 12, "#63778a")
    text(30, card_bottom + 70, "共享成员表面约 25.0%，不等于内存节省；两份 2K 镜像对照的纹理字节相同。验收限 UV＋BaseColor，完整 PBR / GPU / 人工验收未完成。", 12, "#087f7d")
    canvas.draw()
    renderer = canvas.get_renderer()
    for artist, available in text_boxes:
        bounds = artist.get_window_extent(renderer)
        assert bounds.width <= available + 1, f"Text overflow: {artist.get_text()}"
        assert bounds.x0 >= 0 and bounds.y0 >= 0 and bounds.x1 <= width and bounds.y1 <= height
    stem = "dragon-parts-preview-with-uv" if include_uv else "dragon-parts-preview"
    fig.savefig(output / f"{stem}.png", dpi=dpi, facecolor=fig.get_facecolor())
    fig.savefig(output / f"{stem}.pdf", facecolor=fig.get_facecolor())
    with Image.open(output / f"{stem}.png") as image:
        assert image.size == (width, height)
    return {"png": f"{stem}.png", "pdf": f"{stem}.pdf", "dimensions": [width, height],
            "checked_text_boxes": len(text_boxes), "sha256": file_hash(output / f"{stem}.png")}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "output/dragon-parts-preview-20261125")
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    cards, sources = make_cards()
    source_hashes = {str(path): file_hash(path) for path in sources}
    records = []
    for card in cards:
        records.append(render_card(card, output))
        print(f"Rendered {card.key}", flush=True)
    figures = [compose(cards, output, False), compose(cards, output, True)]
    assert source_hashes == {str(path): file_hash(path) for path in sources}
    write_json(output / "preview-evidence.json", {
        "camera": CAMERA, "source_sha256": source_hashes, "cards": records, "figures": figures,
        "checks": {"same_camera_all_columns": True, "render_dimensions": [900, 900],
                   "all_text_within_bounds": True, "source_files_unchanged": True,
                   "new_unwrap_pack_bake_performed": False},
    })
    print({"output": str(output), "figures": figures}, flush=True)


if __name__ == "__main__":
    main()
