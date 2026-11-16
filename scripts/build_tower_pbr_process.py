"""Organize retained tower PBR channels and draw a single-row method overview."""

from __future__ import annotations

import csv
import hashlib
import json
import shutil
import zipfile
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "artifacts/tower_uv_comparison/round02"
OUT = ROOT / "docs/assets/tower-pbr-process/round01"
FINAL = "mirror_negative_exhaustive_minimum_shared"
CONTROL = "mirror_negative_exhaustive_minimum_unshared"
CHANNELS = ("BaseColor", "Normal", "Roughness", "ORM")
COLORS = {"BaseColor": "#148476", "Normal": "#7762C7", "Roughness": "#738596", "ORM": "#BD762D"}
BG, INK, MUTED, TEAL = "#EDF3F7", "#17344C", "#5B7185", "#087E85"
COL, GAP, MARGIN, HEIGHT = 480, 16, 40, 1690


def read(path: Path) -> Any:
    """Read external experiment JSON while preserving its original schema."""
    return json.loads(path.read_text(encoding="utf-8"))


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    name = "msyhbd.ttc" if bold else "msyh.ttc"
    return ImageFont.truetype("C:/Windows/Fonts/" + name, size)


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def organize(rows: list[dict[str, Any]]) -> tuple[list[dict[str, object]], dict[str, str]]:
    records: list[dict[str, object]] = []
    sources: dict[str, str] = {}
    original = BASE.parent / "round01/inputs/source/fantasy_cross_statue_3d_model.fbm"
    for channel, filename in (("BaseColor", "fantasy_cross_statue_3d_model_basecolor.jpg"),
                              ("Normal", "fantasy_cross_statue_3d_model_normal.png")):
        source = original / filename
        target = OUT / "maps/source" / (channel + source.suffix)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        sources[source.relative_to(ROOT).as_posix()] = sha(source)
        with Image.open(source) as image:
            records.append({"method": "source", "channel": channel, "mip": 0,
                            "path": target.relative_to(OUT).as_posix(), "size": list(image.size),
                            "sha256": sha(target), "source": str(source), "operation": "byte-identical copy"})
    for row in rows:
        if not row.get("model"):
            continue
        method = str(row["method"])
        folder = Path(row["folder"]) / "bake"
        levels = range(12) if method in (FINAL, CONTROL) else range(1)
        coverage = np.load(folder / "coverage.npy").astype(bool)
        with Image.open(folder / "texture/ORM_mip0.png") as image:
            actual = np.asarray(image.convert("RGB"))[coverage]
        assert np.all(actual == np.array([255, 153, 0])), method
        for mip in levels:
            destination = OUT / "maps" / method
            if mip:
                destination /= "mips"
            destination.mkdir(parents=True, exist_ok=True)
            suffix = f"_mip{mip}" if mip else ""
            for channel in ("BaseColor", "Normal", "ORM"):
                source = folder / f"texture/{channel}_mip{mip}.png"
                target = destination / f"{channel}{suffix}.png"
                shutil.copyfile(source, target)
                sources[source.relative_to(ROOT).as_posix()] = sha(source)
                with Image.open(source) as image:
                    records.append({"method": method, "channel": channel, "mip": mip,
                                    "path": target.relative_to(OUT).as_posix(), "size": list(image.size),
                                    "sha256": sha(target), "source": str(source), "operation": "byte-identical copy"})
            source = folder / f"texture/ORM_mip{mip}.png"
            with Image.open(source) as image:
                rgb = image.convert("RGB")
                for channel, index in (("AO", 0), ("Roughness", 1), ("Metallic", 2)):
                    target = destination / f"{channel}{suffix}.png"
                    split = rgb.getchannel(index)
                    split.save(target)
                    records.append({"method": method, "channel": channel, "mip": mip,
                                    "path": target.relative_to(OUT).as_posix(), "size": list(split.size),
                                    "sha256": sha(target), "source": str(source),
                                    "operation": f"exact 8-bit channel {index} extraction from ORM"})
        Image.fromarray(coverage.astype(np.uint8) * 255).save(OUT / "maps" / method / "surface_coverage.png")
    return records, sources


def build_bundle() -> dict[str, object]:
    path = OUT / "最终共享版_PBR模型与贴图.zip"
    model = BASE / f"evaluation/{FINAL}_2048/export/asset.glb"
    textures = OUT / "maps" / FINAL
    instruction = (
        "# 防御塔最终共享版\n\n"
        "asset.glb 为原实验导出模型，内含材质。textures/ 保存独立贴图。"
        "BaseColor 使用 sRGB，其余通道按线性数据解释，Normal 采用实验声明的 OpenGL 约定。\n\n"
        "顶层贴图为 2048² mip0，mips/ 保存 mip1 至 mip11。"
        "ORM 的 R/G/B 分别为 AO/Roughness/Metallic。"
        "有效表面参数为 AO=1、Roughness=0.6、Metallic=0。"
        "三个灰度通道从烘焙 ORM 逐像素拆分。\n\n"
        "模型为 7650 三角形、48 连通岛、24 独立图块。"
        "原记录数值状态为 DIAGNOSTIC，Agent 看图结论为 VISUAL_KEEP，"
        "人工和游戏 Shader 状态为 NOT_RUN。\n\n"
        "[verified: round02/report/results.json; report/appearance_review.json; "
        "bake/texture/material_metadata.json; organized channel pixel checks]\n"
    )
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=4) as archive:
        archive.write(model, "asset.glb")
        for texture in sorted(textures.rglob("*.png")):
            archive.write(texture, "textures/" + texture.relative_to(textures).as_posix())
        archive.writestr("README.md", instruction)
    return {"path": path.name, "bytes": path.stat().st_size, "sha256": sha(path),
            "model_source": model.relative_to(ROOT).as_posix(), "model_sha256": sha(model)}


class Overview:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows
        self.width = MARGIN * 2 + (len(rows) + 1) * COL + len(rows) * GAP
        self.image = Image.new("RGB", (self.width, HEIGHT), BG)
        self.draw = ImageDraw.Draw(self.image)
        self.text_count = 0
        self.image_sources: dict[str, str] = {}

    def text(self, xy: tuple[int, int], value: str, size: int = 25,
             color: str = INK, bold: bool = False, width: int | None = None) -> None:
        face = font(size, bold)
        bounds = self.draw.textbbox(xy, value, font=face)
        if width is not None and bounds[2] > xy[0] + width:
            raise ValueError("TEXT_OVERFLOW: " + value)
        if bounds[2] > self.width - 20 or bounds[3] > HEIGHT - 10:
            raise ValueError("CANVAS_OVERFLOW: " + value)
        self.draw.text(xy, value, fill=color, font=face)
        self.text_count += 1

    def picture(self, path: Path, box: tuple[int, int, int, int]) -> None:
        x, y, width, height = box
        with Image.open(path) as source:
            image = source.convert("RGBA")
            image.thumbnail((width, height), Image.Resampling.LANCZOS)
            backing = Image.new("RGBA", image.size, "#E9EEF2")
            backing.alpha_composite(image)
            self.image.paste(backing.convert("RGB"), (x + (width - image.width) // 2,
                                                       y + (height - image.height) // 2))
        self.image_sources[path.relative_to(ROOT).as_posix()] = sha(path)

    def card(self, index: int, title: str, second: str, selected: bool = False) -> int:
        x = MARGIN + index * (COL + GAP)
        self.draw.rounded_rectangle((x, 215, x + COL, 1570), 14, fill="white",
                                    outline=TEAL if selected else "#CAD7E2", width=4 if selected else 2)
        self.text((x + 20, 233), title, 29, TEAL if selected else INK, True, COL - 40)
        self.text((x + 20, 279), second, 23, MUTED, width=COL - 40)
        return x

    def tile(self, x: int, row_index: int, channel: str, path: Path | None = None,
             constant: int | None = None) -> None:
        channel_index = CHANNELS.index(channel)
        px = x + 20 + (channel_index % 2) * 228
        py = 780 + row_index * 270
        self.draw.rounded_rectangle((px, py, px + 212, py + 212), 6, fill="#F5F7FA", outline="#D8E2EA")
        if path is not None:
            self.picture(path, (px + 2, py + 2, 208, 208))
        elif constant is not None:
            self.draw.rectangle((px + 2, py + 2, px + 210, py + 210), fill=(constant,) * 3)
            self.text((px + 12, py + 12), "材质常量", 21,
                      "#FFFFFF" if constant < 180 else INK, width=188)
        else:
            self.text((px + 25, py + 73), "参数合成示意", 21, MUTED, width=180)
            self.draw.rectangle((px + 25, py + 120, px + 185, py + 155), fill=(255, 153, 0))
        self.text((px, py + 220), channel, 25, COLORS[channel], True, 218)

    def source(self) -> None:
        x = self.card(0, "原始材质输入", "原模型与原贴图")
        self.picture(BASE / "source_original_preview/material-front_right-soft-1.png", (x + 20, 330, 440, 400))
        for channel in CHANNELS:
            if channel in ("BaseColor", "Normal"):
                extension = ".jpg" if channel == "BaseColor" else ".png"
                self.tile(x, CHANNELS.index(channel) // 2, channel, OUT / f"maps/source/{channel}{extension}")
            else:
                constants = {"Roughness": 153}
                self.tile(x, CHANNELS.index(channel) // 2, channel, constant=constants.get(channel))
        for offset, value in enumerate(["BaseColor 8192² / Normal 2048²", "Roughness 0.6（常量）", "ORM通道含义见图底", "[verified: S1 / S2]"]):
            self.text((x + 20, 1345 + offset * 46), value, 23, MUTED, width=COL - 40)

    def method(self, index: int, row: dict[str, Any]) -> None:
        names = {
            "source_uv": ("原 UV · 统一2K", "保留原布局后重烘焙"),
            "flatten_anything": ("FlattenAnything", "原生UV / 烘焙停止"),
            "optcuts": ("OptCuts", "原生失败 / 重试超时"),
            "xatlas": ("xatlas", "自动展开与重烘焙"),
            "blender_smart": ("Blender Smart UV", "自动展开与重烘焙"),
            "partuv": ("PartUV", "研究后端实际输出"),
            "meshquery_project": ("MeshQuery 项目实现", "接缝规划与重烘焙"),
            "reseam_unshared": ("ReSeam 原形无共享", "108岛 / 108图块"),
            "reseam_shared": ("ReSeam 原形共享", "13组 / 主边界延拓4px"),
            "reseam_optimized": ("ReSeam 原形整理", "合并相邻区域后重烘焙"),
            "mirror_negative_unshared": ("镜像塔 · 初始无共享", "124岛 / 124图块"),
            "mirror_negative_shared": ("镜像塔 · 初次共享", "124岛 / 62图块"),
            "mirror_negative_optimized_minimum_shared": ("镜像塔 · 合岛共享", "102岛 / 51图块"),
            "mirror_negative_optimized_minimum_unshared": ("合岛 · 无共享对照", "102岛 / 102图块"),
            "mirror_negative_aggressive_minimum_shared": ("镜像塔 · 大块共享", "50岛 / 25图块"),
            "mirror_negative_aggressive_minimum_unshared": ("大块 · 无共享对照", "50岛 / 50图块"),
            FINAL: ("镜像塔 · 最终共享", "48岛 / 24图块"),
            CONTROL: ("最终 · 无共享对照", "48岛 / 48图块"),
            "reseam_shared_boundary_control": ("原形共享 · 同目标对照", "4px目标 / 108图块无共享"),
            "reseam_shared_repaired": ("原形共享 · 回退候选", "整组退回 / 97图块"),
        }
        method = str(row["method"])
        title, subtitle = names[method]
        x = self.card(index, title, subtitle, method == FINAL)
        if not row.get("model"):
            uv = row.get("uv")
            if uv:
                self.picture(Path(uv), (x + 20, 330, 440, 400))
            messages = (["UV存在未经许可的重叠", "停在PBR烘焙阶段", "保留原生UV与失败日志"] if uv else
                        ["原生执行失败", "显式切缝修复后重试", "达到2400秒运行上限"])
            for i, value in enumerate(messages):
                self.text((x + 25, 900 + i * 66), value, 27, "#B85A4B", width=430)
            self.text((x + 25, 1180), "贴图产物状态见原始记录", 25, MUTED, width=430)
            self.text((x + 20, 1490), "[verified: S3 / S6]", 23, MUTED, width=440)
            return
        preview = Path(row["folder"]) / "preview/material-front_right-soft-1.png"
        self.picture(preview, (x + 20, 330, 440, 400))
        for i, channel in enumerate(CHANNELS):
            self.tile(x, i // 2, channel, OUT / "maps" / method / f"{channel}.png")
        values = [f"三角形 {row['faces']}  /  图块 {row['owners']}",
                  f"有效UV覆盖 {100 * row['coverage']:.2f}%",
                  "2K / 12级MIP / DIAGNOSTIC", "[verified: S3 / S4]"]
        for i, value in enumerate(values):
            self.text((x + 20, 1345 + i * 46), value, 23, MUTED, width=440)

    def build(self) -> Path:
        self.text((MARGIN, 24), "防御塔 PBR 贴图处理过程与全方法对照", 51, INK, True)
        self.text((MARGIN, 102), "原颜色 / 法线 → 镜像来源映射 → 新UV逐面采样与法线方向转换 → 2K图集 / MIP → 材质渲染检查   [verified: S1–S5]", 31, TEAL)
        self.text((MARGIN, 157), "每列一个版本，依次查看真实材质、BaseColor、Normal、Roughness、ORM。原始输入的灰度块表示材质常量。", 27, MUTED)
        self.source()
        for i, row in enumerate(self.rows, 1):
            self.method(i, row)
        self.text((MARGIN, 1592), "法线处理  读取原凹凸方向 → 镜像时反射法线/切线并修正方向符号 → 按新UV切线方向重新编码 → 各级MIP重新归一化。  [verified: S2 / S4 / S5]", 29, TEAL)
        self.text((MARGIN, 1635), "ORM有效UV内  R=AO 1 / G=Roughness 0.6 / B=Metallic 0。最终共享版已作Agent看图保留，人工与游戏Shader状态为待验收。来源S1–S6见README与manifest.json。  [verified: S1 / S3 / S4 / S6]", 26, MUTED)
        path = OUT / "防御塔PBR全方法对照_1x21.png"
        self.image.save(path, optimize=True)
        return path


def main() -> None:
    rows = read(BASE / "report/results.json")
    # Keep the selected final variant at the right edge of the single method row.
    rows = [row for row in rows if row["method"] != FINAL] + [row for row in rows if row["method"] == FINAL]
    OUT.mkdir(parents=True, exist_ok=True)
    maps, sources = organize(rows)
    overview = Overview(rows)
    image = overview.build()
    bundle = build_bundle()
    source_files = {
        "S1": BASE / "clean_import/import_request.json",
        "S2": BASE / "mirror/negative_exhaustive_minimum_clean/mirror_variant.json",
        "S3": BASE / "report/results.json",
        "S4": BASE / f"evaluation/{FINAL}_2048/bake/texture/material_metadata.json",
        "S5": ROOT / "src/reseam/agent_tools/pbr_reuse.py",
        "S6": BASE / "report/appearance_review.json",
    }
    for source in source_files.values():
        sources[source.relative_to(ROOT).as_posix()] = sha(source)
    sources[Path(__file__).relative_to(ROOT).as_posix()] = sha(Path(__file__))
    for relative in ["src/reseam/agent_tools/mirror_pbr_reference.py", "scripts/agent_pbr_binding.py"]:
        sources[relative] = sha(ROOT / relative)
    sources[str(bundle["model_source"])] = str(bundle["model_sha256"])
    sources.update(overview.image_sources)
    with (OUT / "channels.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=["method", "channel", "mip", "path", "size", "sha256", "source", "operation"])
        writer.writeheader()
        writer.writerows(maps)
    write_json(OUT / "manifest.json", {
        "layout": "1x21", "columns": ["source"] + [row["method"] for row in rows],
        "display_channels": list(CHANNELS),
        "image": {"path": image.name, "size": list(overview.image.size), "sha256": sha(image)},
        "bundle": bundle,
        "source_ids": {key: path.relative_to(ROOT).as_posix() for key, path in source_files.items()},
        "source_sha256": sources, "maps": maps,
        "material_settings": {"roughness": .6, "metallic": 0, "AO_default": 1,
                              "normal_convention": "OpenGL, explicit experiment assumption"},
        "mip_scope": "all successful versions include mip0; final shared and same-geometry unshared also include mip1-11",
        "constant_channel_check": "all 18 successful mip0 coverage masks have ORM RGB=[255,153,0]",
        "map_scope": "roughness, metallic and AO extracted from baked ORM; values outside covered/padded UV retain atlas defaults",
        "normal_transport": "source normal to world; source reflection with T/N/w; frozen member transport; new target tangent encoding; normal mip renormalization",
    })
    print(json.dumps({"image": str(image), "size": overview.image.size, "organized_maps": len(maps),
                      "successful_versions": sum(bool(row.get('model')) for row in rows),
                      "retained_failures": sum(not bool(row.get('model')) for row in rows),
                      "text_elements_checked": overview.text_count}, ensure_ascii=False))


if __name__ == "__main__":
    main()
