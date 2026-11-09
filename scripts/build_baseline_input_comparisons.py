"""Add the original Tripo reference to retained baseline figures without solving again."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "docs/assets/dragon-paper-baselines"
ORIGINAL_METRICS = ROOT / "docs/assets/dragon-uv-final/measurements.json"
FONT = Path("C:/Windows/Fonts/msyh.ttc")
ORDER = ["flatten_anything", "optcuts", "xatlas", "blender_smart", "partuv",
         "meshquery_project", "reseam_shared", "reseam_unshared"]


@dataclass
class Card:
    method: str
    title: str
    files: dict[str, Path]
    lines: list[str]
    original: bool = False
    refinement: bool = False


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def font(size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(FONT), size)


def checked_text(draw: ImageDraw.ImageDraw, xy: tuple[int, int], text: str,
                 size: int, fill: str, available_width: int) -> None:
    face = font(size)
    assert draw.textlength(text, font=face) <= available_width, f"Text overflow: {text}"
    draw.text(xy, text, fill=fill, font=face)


def figure(cards: list[Card], output: Path, kind: str, refinement_note: str | None = None,
           missing_output_text: str = "无合格重烘焙输出") -> None:
    panel, gap, margin = 480, 16, 24
    height = 1460 if refinement_note else 1400
    width = margin * 2 + len(cards) * panel + (len(cards) - 1) * gap
    canvas = Image.new("RGB", (width, height), "#f5f7fa")
    draw = ImageDraw.Draw(canvas)
    title = "原始输入与全部基线：UV / 棋盘格 / 复用对照" if kind == "uv" else "原始输入与全部基线：同镜头真实 BaseColor / 贴图对照"
    checked_text(draw, (30, 18), title, 46, "#15344e", width - 60)
    subtitle = ("左一为原始Tripo：10,832面 / 8192贴图；中间八条为冻结基线；最右为38图块进一步优化，同一11,338面镜像龙 / 2048 BaseColor。"
                if refinement_note else
                "左一为原始Tripo：10,832面 / 8192贴图 / 跨几何参考；右侧八条路径：同一11,338面镜像龙 / 2048 BaseColor。")
    checked_text(draw, (30, 82), subtitle,
                 27, "#52677b", width - 60)
    checked_text(draw, (30, 126), "输入列保留原始UV与原色，不重烘焙、不计入本轮质量排名；MeshQuery为项目实现，数值通过与外观预览分开。",
                 25, "#52677b", width - 60)
    for index, card in enumerate(cards):
        x, y = margin + index * (panel + gap), 184
        draw.rounded_rectangle((x, y, x + panel, 1304), radius=14,
                               fill="#fff9ec" if card.original else "#eaf8f4" if card.refinement else "white",
                               outline="#c38b35" if card.original else "#087f83" if card.refinement else "#d5dfe8",
                               width=3 if card.original or card.refinement else 2)
        checked_text(draw, (x + 12, y + 14), card.title, 28,
                     "#8a581b" if card.original else "#15344e", panel - 24)
        keys = ("front", "uv") if kind == "uv" else ("color", "texture")
        for index_image, key in enumerate(keys):
            top = y + 67 + index_image * 460
            source = card.files.get(key)
            if source is not None:
                with Image.open(source) as original:
                    picture = original.convert("RGB")
                    picture.thumbnail((panel - 20, 450), Image.Resampling.LANCZOS)
                    canvas.paste(picture, (x + (panel - picture.width) // 2,
                                           top + (450 - picture.height) // 2))
            else:
                draw.rectangle((x + 10, top, x + panel - 10, top + 450), fill="#eef1f5")
                checked_text(draw, (x + 25, top + 180), missing_output_text, 28, "#a84848", panel - 50)
        for line_index, line in enumerate(card.lines):
            checked_text(draw, (x + 14, 1176 + line_index * 34), line, 24, "#36526a", panel - 28)
    checked_text(draw, (30, 1344), "原始交叠不计新增复用；共享率不等于质量通过或同质量内存节省。基线未追加ReSeam共享后处理，0%不代表不能组合共享。",
                 25, "#52677b", width - 60)
    if refinement_note:
        checked_text(draw, (30, 1394), refinement_note, 25, "#087f83", width - 60)
    canvas.save(output)


def baseline_cards() -> list[Card]:
    record = json.loads((DATA / "measurements.json").read_text(encoding="utf-8"))
    assert record["input"]["faces"] == 11338 and record["input"]["resolution"] == 2048
    rows = cast(list[dict[str, object]], record["rows"])
    assert [str(row["method"]) for row in rows] == ORDER
    cards: list[Card] = []
    for row in rows:
        artifacts = row["delivery_artifacts"]
        assert isinstance(artifacts, dict)
        files = {str(key): DATA / str(value).replace("\\", "/") for key, value in artifacts.items()}
        shared, coverage = row["actual_shared_surface_fraction"], row["coverage"]
        sharing = f"{shared * 100:.1f}%" if isinstance(shared, (int, float)) else "未核验"
        occupancy = f"{coverage * 100:.2f}%" if isinstance(coverage, (int, float)) else "—"
        quality = "通过" if row["quality_accepted"] is True else "拒绝" if row["quality_accepted"] is False else "未通过流程"
        tiles = row["tiles"] if row["tiles"] is not None else "未核验"
        cards.append(Card(str(row["method"]), str(row["name"]), files,
                          [f"岛 {row['charts']} / 图块 {tiles}",
                           f"实际共享 {sharing} / 覆盖 {occupancy}", f"质量：{quality}"]))
    return cards


def build(render_dir: Path, output: Path) -> None:
    output.mkdir(parents=True, exist_ok=True)
    original_dir = output / "original"
    original_dir.mkdir(exist_ok=True)
    frozen = json.loads((DATA / "manifest.json").read_text(encoding="utf-8"))
    for entry in frozen["files"]:
        assert sha256(DATA / entry["path"].replace("\\", "/")) == entry["sha256"]
    metric_rows = json.loads(ORIGINAL_METRICS.read_text(encoding="utf-8"))
    original = next(row for row in metric_rows if row["name"] == "original")
    assert original["faces"] == 10832 and original["charts"] == 121 and original["independent_tiles"] == 121
    render_sources = {"front": render_dir / "checker/front.png", "uv": render_dir / "checker/uv.png",
                      "color": render_dir / "color/oblique.png", "texture": Path(original["texture"])}
    files: dict[str, Path] = {}
    provenance: list[dict[str, object]] = []
    for key, source in render_sources.items():
        target = original_dir / (key + source.suffix)
        if source.exists():
            shutil.copy2(source, target)
        assert target.exists(), f"Missing original reference image: {source}"
        files[key] = target
        provenance.append({"kind": key, "source": str(source.relative_to(ROOT)),
                           "file": target.relative_to(output).as_posix(), "sha256": sha256(target)})
    with Image.open(files["texture"]) as texture:
        texture_size = texture.size
    assert texture_size == (8192, 8192)
    for name in ["checker", "color"]:
        for filename, source in [(f"{name}_request.json", render_dir / f"requests/{name}.json"),
                                 (f"{name}_result.json", render_dir / f"{name}/result.json")]:
            target = original_dir / filename
            if source.exists():
                shutil.copy2(source, target)
            assert target.exists()
        result = json.loads((original_dir / f"{name}_result.json").read_text(encoding="utf-8"))
        assert result["status"] == "OK" and result["state_hash"] == original["state_hash"]
    original_card = Card("original_tripo", "原始 Tripo 输入 · 参考", files,
                         ["岛 121 / 图块 121", f"无声明共享 / 覆盖 {original['coverage'] * 100:.2f}%",
                          "原始 8192 / 跨几何参考"], True)
    cards = [original_card, *baseline_cards()]
    assert len(cards) == 9
    for kind, name in [("uv", "uv-and-checker-comparison.png"), ("color", "basecolor-comparison.png")]:
        figure(cards, output / name, kind)
    save_json(output / "comparison-evidence.json", {
        "columns": [card.method for card in cards], "original": original, "texture_size": texture_size,
        "original_quality_scope": "reference only; original geometry and 8K texture; not part of fixed-geometry ranking",
        "checker_camera": {"azimuth": 0, "elevation": 0},
        "basecolor_camera": {"azimuth": 35, "elevation": 15},
        "baseline_measurements_sha256": sha256(DATA / "measurements.json"),
        "original_measurements_sha256": sha256(ORIGINAL_METRICS),
        "new_reference_images": provenance, "baseline_frozen_files_unchanged": len(frozen["files"]),
        "algorithms_rerun": False, "new_bakes": False,
        "layout_text_width_assertions": "PASS", "visual_review": "PENDING"})
    save_json(output / "manifest.json", {"files": [{"path": path.relative_to(output).as_posix(),
                "bytes": path.stat().st_size, "sha256": sha256(path)}
                for path in sorted(output.rglob("*")) if path.is_file() and path.name != "manifest.json"]})
    print(f"Created two 9-column figures: {output}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--render-dir", type=Path,
                        default=ROOT / "artifacts/dragon_paper_baselines/with_original_input01")
    parser.add_argument("--output", type=Path, default=DATA / "with-original-input")
    args = parser.parse_args()
    build(args.render_dir, args.output)


if __name__ == "__main__":
    main()
