"""Anonymous evidence export and Chinese reports from every scheduled matrix row."""

import html
import json
from pathlib import Path
from statistics import median
from typing import Any

from PIL import Image, ImageDraw

from .joint_review import ReviewSample, aggregate_reviews, export_packet
from .joint_search import TRACKS
from .joint_types import V2State
from .mesh import chart_labels
from .pipeline import write_json


def draw_atlas(state: V2State, path: Path) -> None:
    image = Image.new("RGB", (1024, 1024), "#101820")
    draw = ImageDraw.Draw(image)
    uv = state.surface.uv * [1024, -1024] + [0, 1024]
    labels = chart_labels(state.surface)
    palette = ("#233c39", "#29384a", "#413a2a", "#393244")
    for fi, face in enumerate(state.surface.uv_faces):
        points = [tuple(map(float, x)) for x in uv[face]]
        draw.polygon(points, fill=palette[int(labels[fi]) % len(palette)])
        draw.line(points + points[:1], fill="#b7e4d8", width=1)
    image.save(path)


def contact_sheet(paths: list[Path], target: Path, crop: bool = False) -> None:
    sheet = Image.new("RGB", (768, 552), "#e9e7df")
    draw = ImageDraw.Draw(sheet)
    for i, path in enumerate(paths):
        with Image.open(path) as source:
            image = source.convert("RGB")
            if crop:
                w, h = image.size
                image = image.crop((w // 4, h // 4, w * 3 // 4, h * 3 // 4))
            image = image.resize((256, 256), Image.Resampling.LANCZOS)
        x, y = (i % 3) * 256, (i // 3) * 276
        sheet.paste(image, (x, y + 20))
        draw.text((x + 8, y + 3), f"view_{i:02d}" + (" center crop" if crop else ""), fill="black")
    sheet.save(target)


def export_round_packets(folder: Path) -> list[Path]:
    rows = json.loads((folder / "matrix.json").read_text())
    packets = []
    for model in ("energy_blade", "dragon"):
        for seed in (17, 29, 43):
            key = f"{model}_seed_{seed}"
            prepared = folder / "review_prepared" / key
            prepared.mkdir(parents=True, exist_ok=False)
            samples = []
            for track in TRACKS:
                row = next(r for r in rows if (r["model"], r["seed"], r["track"]) == (model, seed, track))
                source = folder / row["path"]
                evidence = []
                if (source / "uv.png").exists():
                    evidence.append(("uv", source / "uv.png"))
                for kind, subpath, crop in (
                    ("base", "base", False),
                    ("seam_crop", "base", True),
                    ("normal", "normal", False),
                    ("mip", "mip_128", False),
                ):
                    paths = [source / "downstream" / subpath / f"view_{i:02d}.png" for i in range(6)]
                    if all(p.exists() for p in paths):
                        target = prepared / f"{track}_{kind}.png"
                        contact_sheet(paths, target, crop)
                        evidence.append((kind, target))
                valid = row["status"] == "SUCCESS" and row["downstream_status"] == "SUCCESS"
                samples.append(ReviewSample(track, valid, tuple(evidence)))
            references = []
            for kind, subpath, crop in (
                ("base", "base", False),
                ("seam_crop", "base", True),
                ("normal", "normal", False),
            ):
                paths = [folder / "reference" / model / subpath / f"view_{i:02d}.png" for i in range(6)]
                if all(p.exists() for p in paths):
                    target = prepared / f"reference_{kind}.png"
                    contact_sheet(paths, target, crop)
                    references.append((kind, target))
            packets.append(
                export_packet(
                    folder / "blind" / key, folder / "private" / f"{key}.json", samples, references, seed
                )
            )
    return packets


def generate_joint_report(folder: Path) -> None:
    rows = json.loads((folder / "matrix.json").read_text())
    summaries: list[dict[str, Any]] = []
    for model in ("energy_blade", "dragon"):
        for track in TRACKS:
            group = [r for r in rows if r["model"] == model and r["track"] == track]
            gains = [r["quality"]["gain"] for r in group if r["status"] == "SUCCESS"]
            summaries.append(
                {
                    "model": model,
                    "track": track,
                    "success": len(gains),
                    "scheduled": len(group),
                    "gain_median": median(gains) if gains else None,
                    "gain_range": [min(gains), max(gains)] if gains else None,
                    "accepted_reuse_groups": sum(r.get("reuse_groups", 0) for r in group),
                }
            )
    reviews = {}
    for packet in sorted((folder / "blind").glob("*/manifest.json")):
        imported = folder / "reviews" / packet.parent.name
        raw = [json.loads(p.read_text()) for p in sorted(imported.glob("*.json"))]
        reviews[packet.parent.name] = aggregate_reviews(packet, raw)
    write_json(folder / "review_summary.json", reviews)
    write_json(
        folder / "summary.json",
        {
            "tracks": summaries,
            "scheduled": len(rows),
            "completed_searches": sum(r["status"] == "SUCCESS" for r in rows),
            "completed_downstream": sum(r["downstream_status"] == "SUCCESS" for r in rows),
            "human_acceptance": "NOT_REVIEWED",
        },
    )
    lines = ["# V2 联合实验结论", "", "本报告保留全部预定运行；技术、代理及人工状态分别记录。", ""]
    cards = []
    for row in rows:
        quality = row.get("quality") or {}
        title = f"{row['model']} · {row['track']} · seed {row['seed']}"
        status = f"搜索 {row['status']} / 下游 {row['downstream_status']} / 初值 {row.get('paired_initialization', '未核对')}"
        gain = quality.get("gain")
        metrics = (
            f"G={gain:.6f}；唯一覆盖={quality['unique_coverage']:.2%}；复用组={row.get('reuse_groups', 0)}"
            if gain
            else "无合法质量记录"
        )
        lines.append(f"- {title}：{status}；{metrics}。[运行记录]({row['path']}/result.json)")
        images = "".join(
            f'<a href="{row["path"]}/{p}"><img src="{row["path"]}/{p}" alt="{label}"></a>'
            for label, p in (
                ("UV", "uv.png"),
                ("Base", "downstream/base/view_00.png"),
                ("Normal", "downstream/normal/view_00.png"),
            )
            if (folder / row["path"] / p).exists()
        )
        cards.append(
            f'<article><h2>{html.escape(title)}</h2><p>{html.escape(status)}</p><p>{html.escape(metrics)}</p>{images}<p><a href="{row["path"]}/result.json">候选与门禁原始记录</a></p></article>'
        )
    lines.extend(
        [
            "",
            "## 限制",
            "",
            "两模型是开发与回归集，不能推断独立资产泛化。没有接受复用组的运行不能证明纹理复用带来收益。",
            "代理评分见 review_summary.json；未评分不补零，不等于真实 artist 已验收。人工状态 NOT_REVIEWED。",
        ]
    )
    (folder / "conclusions.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (folder / "index.html").write_text(
        '<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>ReSeam V2 联合实验</title><style>body{background:#eeeae0;color:#172321;font:16px system-ui;margin:32px;max-width:1440px}main{display:grid;grid-template-columns:repeat(auto-fit,minmax(520px,1fr));gap:24px}article{background:#fffdf7;padding:20px}h2{font-size:20px}img{width:30%;margin-right:2%}a{color:#23605b}</style><h1>ReSeam V2 联合实验</h1><p>固定 1024² · 四轨道 · 两模型 · 三种子。保留失败，人工验收独立。</p><p><a href="conclusions.md">结论记录</a> · <a href="matrix.json">完整矩阵</a> · <a href="review_summary.json">代理评分</a></p><main>'
        + "".join(cards)
        + "</main></html>",
        encoding="utf-8",
    )
