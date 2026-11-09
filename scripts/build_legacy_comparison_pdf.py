"""Present the six retained three-asset comparison figures in one PDF."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from PIL import Image
from pypdf import PdfReader
from reportlab.lib.colors import HexColor
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen.canvas import Canvas

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "docs/assets/legacy-assets-reseam/round02/figures"
OUTPUT = ROOT / "output/pdf/legacy-assets-comparison"
FONT = Path("C:/Windows/Fonts/msyh.ttc")
ASSETS = (("chloe", "Chloe 红刺"), ("energy_blade", "历史能量刀"), ("dragon", "历史幻想龙"))
KINDS = (("uv", "UV 与指标"), ("models", "模型渲染对照"))
WIDTH, MARGIN, FOOTER = 1440.0, 20.0, 40.0


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build(output: Path) -> None:
    sources = [(SOURCE / f"{asset}_{kind}.png", name, label)
               for asset, name in ASSETS for kind, label in KINDS]
    for source, _, _ in sources:
        if not source.is_file():
            raise FileNotFoundError(source)
    hashes = {str(source.relative_to(ROOT)): digest(source) for source, _, _ in sources}
    output.mkdir(parents=True, exist_ok=False)
    pdf_path = output / "reseam-three-assets-uv-model-comparison.pdf"
    pdfmetrics.registerFont(TTFont("ReSeamYaHei", str(FONT)))
    canvas = Canvas(str(pdf_path), pageCompression=1, invariant=1)
    canvas.setTitle("ReSeam 三模型 UV 与模型渲染对照")
    canvas.setAuthor("ReSeam")
    canvas.setSubject("Chloe 红刺、历史能量刀、历史幻想龙的六张真实对照图")
    records: list[dict[str, object]] = []
    for number, (source, asset_name, kind_name) in enumerate(sources, start=1):
        with Image.open(source) as image:
            source_size = image.size
            image_width = WIDTH - 2 * MARGIN
            image_height = image_width * image.height / image.width
        height = image_height + MARGIN + FOOTER
        canvas.setPageSize((WIDTH, height))
        canvas.setFillColor(HexColor("#f4f6f9"))
        canvas.rect(0, 0, WIDTH, height, fill=1, stroke=0)
        canvas.drawImage(ImageReader(str(source)), MARGIN, FOOTER,
                         width=image_width, height=image_height)
        canvas.setFillColor(HexColor("#52677c"))
        canvas.setFont("ReSeamYaHei", 11)
        canvas.drawString(MARGIN, 17, f"{asset_name} | {kind_name} | 保留原始实验状态")
        canvas.drawRightString(WIDTH - MARGIN, 17, f"{number} / {len(sources)}")
        key = f"page_{number}"
        canvas.bookmarkPage(key)
        canvas.addOutlineEntry(f"{asset_name} - {kind_name}", key, level=0)
        records.append({"page": number, "source": source.relative_to(ROOT).as_posix(),
                        "source_sha256": hashes[str(source.relative_to(ROOT))],
                        "source_size_pixels": list(source_size), "page_size_points": [WIDTH, height],
                        "image_box_points": [MARGIN, FOOTER, image_width, image_height],
                        "title": f"{asset_name} | {kind_name}"})
        canvas.showPage()
    canvas.save()
    reader = PdfReader(pdf_path)
    assert len(reader.pages) == len(sources) == 6
    for page, (source, _, _), record in zip(reader.pages, sources, records, strict=True):
        assert len(page.images) == 1
        embedded = page.images[0].image
        assert embedded is not None
        with Image.open(source) as original:
            assert embedded.convert("RGB").tobytes() == original.convert("RGB").tobytes()
        assert digest(source) == record["source_sha256"]
        assert "6" in (page.extract_text() or "")
    evidence = {"pdf": pdf_path.name, "pdf_sha256": digest(pdf_path), "pages": records,
                "page_count": len(reader.pages), "original_images_unchanged": True,
                "embedded_pixels_exact": True, "preserved_aspect_ratio": True,
                "scope": "Presentation only: six existing images; no UV, model, bake or metric changes."}
    (output / "evidence.json").write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"pdf": str(pdf_path), "pages": len(reader.pages),
                      "bytes": pdf_path.stat().st_size, "embedded_pixels_exact": True}, ensure_ascii=False))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()
    build(args.output.resolve())


if __name__ == "__main__":
    main()
