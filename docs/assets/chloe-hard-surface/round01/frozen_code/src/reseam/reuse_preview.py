"""Read-only source inspection and isolated Base Color symmetry preview delivery."""

import html
import json
import shutil
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageDraw

from .downstream import blender, image_metrics, scene_request
from .mesh import load_state, save_state, sha256
from .packing_benchmark import draw_atlas
from .pipeline import export_glb, write_json
from .runtime import read_runtime
from .symmetry import ReuseProfile, apply_reuse, propose_reuse, validate_reuse


def render_comparison(
    root: Path, model: str, folder: Path, source_folder: Path, seconds: float
) -> dict[str, Any]:
    start = time.monotonic()
    folder.mkdir(parents=True, exist_ok=False)
    source_path = source_folder / "pack/state.json"
    texture_path = source_folder / "blender/BaseColor.png"
    state = load_state(source_path)
    texture = np.asarray(Image.open(texture_path).convert("RGBA"), dtype=float) / 255
    bake_info = json.loads((source_folder / "blender/bake.json").read_text(encoding="utf-8"))
    projected = np.asarray(bake_info["projection_probe"]["face_hit_fraction"]) == 1
    result = propose_reuse(state, texture, ReuseProfile(), projected)
    result.update(
        {
            "model": model,
            "source": str(source_path),
            "texture_source": str(texture_path),
            "input_sha256": sha256(source_path),
            "texture_sha256": sha256(texture_path),
            "artist_approval": "PENDING_HUMAN",
            "normal_map_status": "NOT_VALIDATED_NOT_EXPORTED",
            "packing_status": "NOT_RUN_PENDING_REUSE_REVIEW",
            "source_detail": "Existing V1 bake; no new high-resolution detail",
        }
    )
    write_json(folder / "result.json", result)
    if not result["candidates"]:
        result["preview_status"] = "NO_MATCHED_PATCH"
    else:
        # Prefer passing candidates. Otherwise show the fewest violated gates, then largest area.
        chosen = min(
            result["candidates"], key=lambda c: (len(c["rejection_reasons"]), -c["donor_area_fraction"])
        )
        result["selected_preview"] = chosen["candidate_id"]
        result["selection_rule"] = (
            "fewest violated preview gates, then largest donor area; no camera feedback"
        )
        output = apply_reuse(state, chosen["pairs"])
        result["reuse_geometry"] = validate_reuse(state, output, chosen["pairs"])
        if not result["reuse_geometry"]["valid"]:
            raise ValueError(f"Unauthorized overlap: {result['reuse_geometry']}")
        save_state(folder / "preview_state.json", output)
        shutil.copy2(texture_path, folder / "BaseColor.png")
        export_glb(output, folder / "reuse_basecolor_preview.glb", texture_path)
        draw_atlas(state, folder / "uv_before.png")
        draw_atlas(output, folder / "uv_reused.png")
        mask = Image.new("RGB", (1024, 1024), "#444444")
        painter = ImageDraw.Draw(mask)
        for pair in chosen["pairs"]:
            for face_id, color in ((pair["owner"], "#20b5e1"), (pair["donor"], "#ff812a")):
                painter.polygon(
                    [(float(x * 1024), float((1 - y) * 1024)) for x, y in state.uv[state.uv_faces[face_id]]],
                    fill=color,
                )
        mask.save(folder / "region_mask.png")
        runtime = read_runtime(root)
        high = root / "assets/v1" / model / "high.glb"
        try:
            for label, render_state, tex in (
                ("original", state, texture_path),
                ("reused", output, texture_path),
                ("regions", state, folder / "region_mask.png"),
            ):
                destination = folder / label
                blender(
                    root,
                    runtime,
                    {
                        **scene_request(render_state, high),
                        "mode": "render_low",
                        "texture": str(tex),
                        "folder": str(destination),
                        "output": str(destination / "views.json"),
                    },
                    destination,
                    seconds - (time.monotonic() - start),
                )
            result["views"] = []
            for i in range(6):
                first = folder / "original" / f"view_{i:02d}.png"
                second = folder / "reused" / f"view_{i:02d}.png"
                metrics = image_metrics(first, second)
                a = np.asarray(Image.open(first).convert("RGBA"), dtype=float) / 255
                b = np.asarray(Image.open(second).convert("RGBA"), dtype=float) / 255
                difference = np.clip(np.abs(a[:, :, :3] - b[:, :, :3]) * 8, 0, 1)
                Image.fromarray(np.asarray(difference * 255, dtype=np.uint8)).save(
                    folder / f"difference_{i:02d}.png"
                )
                result["views"].append(metrics)
            result["preview_status"] = "RENDERED_FOR_REVIEW"
            result["comparison"] = (
                "Original low-mesh Base Color vs copied mirror Base Color; held camera list; sRGB image MAE distinct from linear-RGB candidate gate"
            )
        except (ValueError, RuntimeError, TimeoutError, OSError) as error:
            result["preview_status"] = "RENDER_FAILED"
            result["reason"] = str(error)
    result["inputs_unchanged"] = (
        sha256(source_path) == result["input_sha256"] and sha256(texture_path) == result["texture_sha256"]
    )
    result["wall_seconds"] = time.monotonic() - start
    result["budget_overrun"] = result["wall_seconds"] > seconds
    write_json(folder / "result.json", result)
    return result


def report(folder: Path, results: list[dict[str, Any]]) -> None:
    body = [
        """<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>ReSeam 对称复用预览</title><style>
    body{background:#eeeae0;color:#172923;font:16px/1.7 'Microsoft YaHei',sans-serif;margin:0}main{max-width:1400px;margin:auto;padding:28px}section{background:#fffdf7;padding:24px;margin:24px 0;border-top:3px solid #326e67}.views{display:grid;grid-template-columns:repeat(4,1fr);gap:12px}img{width:100%;background:#182222}figure{margin:0}figcaption{font-size:14px}a{color:#236c62}.rejected{color:#9d3e16;font-weight:bold}@media(max-width:800px){.views{grid-template-columns:repeat(2,1fr)}}</style><main><h1>对称 ≠ 可以直接共用纹理</h1><p>这轮预览固定现有纹理，仅将配对区域映射到同一组像素；未重新 pack，因此唯一覆盖率会降低，纹素密度不会因“叠起来”自动提高。橙色为共享方，蓝色为提供像素的一方。</p><p>候选门限用于技术筛选，不是你的交付标准。被拒绝候选仍展示差异，但没有自动通过 artist 验收；不包含切线法线复用、OT 配准或 Texture Carving。</p>"""
    ]
    for result in results:
        model = result["model"]
        candidates = result["candidates"]
        body.append(
            f"<section><h2>{html.escape(model)}</h2><p>几何区域候选 {len(candidates)} 个；通过小差异筛选 {sum(c['status'] == 'PENDING_HUMAN' for c in candidates)} 个。</p>"
        )
        chosen = next((c for c in candidates if c["candidate_id"] == result.get("selected_preview")), None)
        if chosen:
            body.append(
                f'<p class="rejected">{chosen["status"]} · {len(chosen["pairs"])} 对面 · 共享方占三维表面积 {chosen["donor_area_fraction"]:.2%} · {html.escape(", ".join(chosen["rejection_reasons"]))}</p>'
            )
        if result.get("preview_status") == "RENDERED_FOR_REVIEW":
            for i, metric in enumerate(result["views"]):
                body.append(f'<h3>视角 {i + 1} · 整体前景 MAE {metric["mae"]:.6f}</h3><div class="views">')
                for label, path in (
                    ("原图", f"original/view_{i:02d}.png"),
                    ("共享后", f"reused/view_{i:02d}.png"),
                    ("差异 ×8", f"difference_{i:02d}.png"),
                    ("对应区域", f"regions/view_{i:02d}.png"),
                ):
                    body.append(
                        f'<figure><img loading="lazy" src="{model}/{path}" alt="{label}"><figcaption>{label}</figcaption></figure>'
                    )
                body.append("</div>")
        body.append(
            f'<p><a href="{model}/result.json">全部候选、门限和拒绝原因</a> · <a href="{model}/reuse_basecolor_preview.glb">下载 Base Color 诊断模型</a></p></section>'
        )
    (folder / "index.html").write_text("".join(body) + "</main></html>", encoding="utf-8")


def run_preview(root: Path, config_path: Path) -> None:
    started = time.monotonic()
    config = json.loads(config_path.read_text(encoding="utf-8"))
    folder = root / "artifacts/v2" / config["round_id"]
    folder.mkdir(parents=True, exist_ok=False)
    files = [
        *sorted((root / "src/reseam").glob("*.py")),
        root / "scripts/blender_worker.py",
        root / "uv.lock",
    ]
    code = {str(p.relative_to(root)): sha256(p) for p in files}
    for path in files:
        dest = folder / "source_snapshot" / path.relative_to(root)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, dest)
    runtime = read_runtime(root)
    summary: dict[str, Any] = {
        "config": config,
        "profile": asdict(ReuseProfile()),
        "code_sha256": code,
        "config_sha256": sha256(config_path),
        "blender_sha256": sha256(Path(runtime["blender"])),
        "results": [],
    }
    write_json(folder / "summary.json", summary)
    for model in config["models"]:
        source = root / "artifacts/v1/v1_round03" / model / "original/optcuts/seed_17/downstream"
        for relative, expected in config["inputs"][model].items():
            if sha256(source / relative) != expected:
                raise ValueError(f"Input changed: {model}/{relative}")
        result = render_comparison(root, model, folder / model, source, config["seconds_per_model"])
        summary["results"].append(result)
        write_json(folder / "summary.json", summary)
        print(
            json.dumps(
                {
                    "model": model,
                    "status": result["status"],
                    "preview": result["preview_status"],
                    "seconds": result["wall_seconds"],
                }
            ),
            flush=True,
        )
    report(folder, summary["results"])
    summary["code_unchanged"] = all(sha256(root / p) == h for p, h in code.items())
    summary["blender_unchanged"] = sha256(Path(runtime["blender"])) == summary["blender_sha256"]
    summary["wall_seconds"] = time.monotonic() - started
    write_json(folder / "summary.json", summary)
