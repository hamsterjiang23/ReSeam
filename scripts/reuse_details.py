"""Add region-centred review crops and audit without rewriting frozen preview evidence."""

import argparse
import html
import json
import time
from pathlib import Path

import numpy as np
from PIL import Image

from reseam.mesh import load_state, sha256
from reseam.pipeline import write_json
from reseam.symmetry import validate_reuse


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--round", type=Path, required=True)
    parser.add_argument("--output-name", default="review")
    args = parser.parse_args()
    folder = args.round.resolve()
    start = time.monotonic()
    summary = json.loads((folder / "summary.json").read_text(encoding="utf-8"))
    if "wall_seconds" not in summary:
        raise ValueError("Preview not finalized")
    if Path(args.output_name).name != args.output_name:
        raise ValueError("Output name must be one directory name")
    output = folder / args.output_name
    output.mkdir(exist_ok=False)
    audit = {
        "models": [],
        "source_code_unchanged": summary["code_unchanged"],
        "blender_unchanged": summary["blender_unchanged"],
    }
    content = [
        """<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>对称复用 · 局部评审</title><style>body{margin:0;background:#eeeae0;color:#182c28;font:16px/1.6 'Microsoft YaHei',sans-serif}main{max-width:1300px;margin:auto;padding:28px}section{background:#fffdf8;padding:24px;margin:24px 0}h1,h2{font-weight:650}.grid{display:grid;grid-template-columns:repeat(4,1fr);gap:10px}img{width:100%;image-rendering:auto;background:#182222}figure{margin:0}figcaption{font-size:14px}a{color:#245f58}.status{color:#a24420;font-weight:bold}@media(max-width:800px){.grid{grid-template-columns:repeat(2,1fr)}}</style><main><h1>对称复用，先看差异</h1><p>下列是未通过默认小差异门限的诊断预览，不是推荐交付结果。原始纹理保持不变；这里只将橙色区域改为读取蓝色区域的像素。尚未重新排布、重烘焙、压缩纹理或验证法线。</p><p>两模型各有 4 块几何配对区域，0 块通过全部技术门限。每模型选违例种类最少的一块展示，其他候选仍保存在原始记录。</p><p><a href="../index.html">全部六视角及模型下载</a> · <a href="../summary.json">完整诊断</a></p>"""
    ]
    for result in summary["results"]:
        model = result["model"]
        selected = next(c for c in result["candidates"] if c["candidate_id"] == result["selected_preview"])
        source = load_state(Path(result["source"]))
        state = load_state(folder / model / "preview_state.json")
        checked = validate_reuse(source, state, selected["pairs"])
        assert checked["valid"], checked
        assert sha256(Path(result["source"])) == result["input_sha256"]
        assert sha256(Path(result["texture_source"])) == result["texture_sha256"]
        metrics = {
            "model": model,
            "selected": selected["candidate_id"],
            "donor_area_fraction": selected["donor_area_fraction"],
            "rejection_reasons": selected["rejection_reasons"],
            "geometry": checked,
            "maximum_face_color_mean": max(p["color_mean"] for p in selected["pairs"]),
            "maximum_face_color_p95": max(p["color_p95"] for p in selected["pairs"]),
            "maximum_anisotropy_ratio": max(p["anisotropy_ratio"] for p in selected["pairs"]),
            "view_mae": [v["mae"] for v in result["views"]],
            "artist_approval": "PENDING_HUMAN",
        }
        audit["models"].append(metrics)
        content.append(
            f'<section><h2>{model} · {selected["candidate_id"]}</h2><p class="status">DIAGNOSTIC_REJECTED · {html.escape(", ".join(selected["rejection_reasons"]))}</p><p>共享方占三维表面积 {selected["donor_area_fraction"]:.3%}；{len(selected["pairs"])} 对面。未重排时释放唯一 UV 面积 {checked["freed_unique_area"]:.3%}，不等于分辨率或存储收益。</p>'
        )
        count = 0
        metrics["donor_region_views"] = []
        for i in range(6):
            mask = np.asarray(Image.open(folder / model / "regions" / f"view_{i:02d}.png").convert("RGBA"))
            rgb = mask[..., :3].astype(float) / 255
            donor = (
                (rgb[..., 0] > rgb[..., 1] + 0.15) & (rgb[..., 0] > rgb[..., 2] + 0.2) & (mask[..., 3] > 240)
            )
            original = (
                np.asarray(
                    Image.open(folder / model / "original" / f"view_{i:02d}.png").convert("RGB"), dtype=float
                )
                / 255
            )
            reused = (
                np.asarray(
                    Image.open(folder / model / "reused" / f"view_{i:02d}.png").convert("RGB"), dtype=float
                )
                / 255
            )
            regional_mae = float(np.abs(original[donor] - reused[donor]).mean()) if donor.any() else None
            metrics["donor_region_views"].append({"view": i, "pixels": int(donor.sum()), "mae": regional_mae})
            colored = (rgb.max(axis=2) - rgb.min(axis=2) > 0.2) & (mask[..., 3] > 240)
            y, x = np.nonzero(colored)
            if not len(x):
                continue
            left, right = max(0, int(x.min()) - 24), min(mask.shape[1], int(x.max()) + 25)
            top, bottom = max(0, int(y.min()) - 24), min(mask.shape[0], int(y.max()) + 25)
            content.append(
                f'<h3>视角 {i + 1} 局部 · 共享区 MAE {regional_mae if regional_mae is not None else "不可见"} · 共享区 {int(donor.sum())} 像素</h3><div class="grid">'
            )
            for label, relative in (
                ("原图", f"original/view_{i:02d}.png"),
                ("共享后", f"reused/view_{i:02d}.png"),
                ("差异 ×8", f"difference_{i:02d}.png"),
                ("蓝色提供纹理／橙色共享", f"regions/view_{i:02d}.png"),
            ):
                src = folder / model / relative
                name = f"{model}_{i:02d}_{Path(relative).parent.name or 'difference'}_{src.stem}.png"
                Image.open(src).crop((left, top, right, bottom)).save(output / name)
                content.append(
                    f'<figure><img src="{name}" alt="{label}"><figcaption>{label}</figcaption></figure>'
                )
            content.append("</div>")
            count += 1
        metrics["visible_region_views"] = count
        content.append("</section>")
    (output / "index.html").write_text("".join(content) + "</main></html>", encoding="utf-8")
    audit["wall_seconds"] = time.monotonic() - start
    audit["script_sha256"] = sha256(Path(__file__))
    write_json(output / "audit.json", audit)
    print(json.dumps(audit, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
