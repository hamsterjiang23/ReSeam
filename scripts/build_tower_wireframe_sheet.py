"""Compose a one-row wireframe sheet from the public view.render outputs."""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
RUN = ROOT / "artifacts/tower-wireframe-views/round01"
MODEL = ROOT / "artifacts/tower_uv_comparison/round02/evaluation/mirror_negative_exhaustive_minimum_shared_2048"
OUT = ROOT / "docs/assets/tower-wireframe/round01"
VIEWS = (("front", "正面"), ("back", "背面"), ("left", "左侧"), ("right", "右侧"),
         ("front_right", "右前"), ("front_left", "左前"), ("back_right", "右后"), ("back_left", "左后"))


def read(path: Path) -> Any:
    """Read externally produced experiment JSON."""
    return json.loads(path.read_text(encoding="utf-8"))


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype("C:/Windows/Fonts/" + ("msyhbd.ttc" if bold else "msyh.ttc"), size)


def main() -> None:
    report = read(RUN / "render/views.json")
    result = read(RUN / "render/result.json")
    request = read(RUN / "request.json")
    exported = read(MODEL / "export/export.json")
    snapshot_path = Path(request["snapshot"])
    snapshot = read(snapshot_path)
    assert result["execution_success"] is True
    assert report["mode"] == "wireframe"
    assert report["state_hash"] == exported["state_hash"] == snapshot["state_hash"]
    assert len(report["uv_label_mapping"]) == 48
    assert len(snapshot["groups"]) == 24
    assert exported["mesh"]["faces"] == 7650
    model = MODEL / "export/asset.glb"
    checks = read(ROOT / "artifacts/tower_uv_comparison/round02/report/integrity.json")["model_checks"]
    matching = [check for check in checks if Path(check["model"]).resolve() == model.resolve()]
    assert len(matching) == 1 and matching[0]["blender_geometry_uv_match"]
    assert matching[0]["sha256"] == digest(model)

    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "views").mkdir(exist_ok=True)
    width, height = 7232, 1120
    sheet = Image.new("RGB", (width, height), "#EDF3F7")
    draw = ImageDraw.Draw(sheet)
    draw.text((40, 28), "镜像共享防御塔 · 多视角线框", font=font(43, True), fill="#17344C")
    draw.text((40, 92), "最终共享版本  /  7,650 三角形  /  48 UV岛 · 24共享图块  /  1×8 正交视角",
              font=font(24), fill="#587286")
    records: list[dict[str, object]] = []
    cameras = {camera["name"]: camera for camera in request["cameras"]}
    for index, (name, label) in enumerate(VIEWS):
        path = Path(report["images"][name])
        destination = OUT / "views" / (name + ".png")
        shutil.copyfile(path, destination)
        with Image.open(path) as source:
            assert source.size == (1024, 1024)
            rgb = source.convert("RGB")
            pixels = np.asarray(rgb)
            foreground = np.any(pixels != pixels[0, 0], axis=2)
            y, x = np.nonzero(foreground)
            assert len(x) > 10000, name
            bounds = [int(x.min()), int(y.min()), int(x.max()), int(y.max())]
            assert bounds[0] > 0 and bounds[1] > 0 and bounds[2] < 1023 and bounds[3] < 1023, name
            wire = np.all(pixels == np.array([75, 85, 100]), axis=2)
            assert int(wire.sum()) > 1000, name
            tile_x = 40 + index * 896
            draw.rounded_rectangle((tile_x, 145, tile_x + 880, 1050), 12,
                                   fill="white", outline="#C9D7E2", width=2)
            draw.text((tile_x + 28, 165), label, font=font(31, True), fill="#17344C")
            sheet.paste(rgb.resize((820, 820), Image.Resampling.LANCZOS), (tile_x + 30, 215))
            records.append({"name": name, "label": label, "camera": cameras[name],
                            "path": destination.relative_to(OUT).as_posix(), "sha256": digest(destination),
                            "source": str(path), "size": list(source.size),
                            "foreground_bounds": bounds, "wire_pixels": int(wire.sum())})
    draw.text((40, 1074), "灰面叠加可见三角网格  ·  来源为最终导出模型对应快照  [verified: render-report.json / manifest.json source_export]",
              font=font(23), fill="#587286")
    output = OUT / "镜像共享防御塔_多视角线框_1x8.png"
    sheet.save(output, optimize=True)
    for source, destination_name in [(RUN / "request.json", "render-request.json"),
                                      (RUN / "render/views.json", "render-report.json"),
                                      (RUN / "render/provenance.json", "render-provenance.json")]:
        shutil.copyfile(source, OUT / destination_name)
    sources = {path.relative_to(ROOT).as_posix(): digest(path) for path in [
        model, snapshot_path, MODEL / "export/export.json", RUN / "request.json",
        RUN / "render/views.json", Path(__file__), ROOT / "src/reseam/agent_tools/views.py"]}
    manifest = {"layout": "1x8", "image": output.name, "image_size": [width, height],
                "image_sha256": digest(output), "model": str(model), "model_sha256": digest(model),
                "source_export": str(MODEL / "export/export.json"), "snapshot": str(snapshot_path),
                "state_hash": report["state_hash"], "asset_id": report["asset_id"],
                "triangles": 7650, "uv_islands": 48, "shared_tiles": 24,
                "renderer": "ReSeam view.render; CPU orthographic depth-tested visible-edge wireframe",
                "camera_contract": "explicit azimuth/elevation values in render-request.json; same eight named cameras as retained material preview",
                "views": records, "source_sha256": sources,
                "quality_evidence": False}
    (OUT / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"image": str(output), "size": [width, height], "views": len(records),
                      "state_hash": report["state_hash"], "all_foreground_bounds_inside_frame": True}, ensure_ascii=False))


if __name__ == "__main__":
    main()
