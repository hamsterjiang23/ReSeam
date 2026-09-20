"""Import provided artist references and write independent per-object UV measurements."""

import argparse
import html
import json
import shutil
import time
from pathlib import Path

import numpy as np

from reseam.ground_truth import uv_reference_metrics
from reseam.mesh import sha256
from reseam.pipeline import write_json
from reseam.runtime import read_runtime, run_process


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--manifest", type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    started = time.monotonic()
    before = {path.name: sha256(path) for path in args.input.glob("*.FBX")}
    references = {}
    if args.manifest:
        manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
        for reference in manifest["references"]:
            if before.get(reference["file"]) != reference["sha256"]:
                raise ValueError("Artist FBX manifest hash mismatch")
            texture = args.input / reference["texture_reference"]
            if sha256(texture) != reference["texture_sha256"]:
                raise ValueError("Artist texture reference hash mismatch")
            references[(reference["file"], reference["object"], reference["uv_channel_for_overlay"])] = (
                reference
            )
        write_json(output / "reference_manifest.json", manifest)
    runtime = read_runtime(root)
    run_process(
        [
            runtime["blender"],
            "--background",
            "--factory-startup",
            "--threads",
            "1",
            "--python",
            str(root / "scripts/inspect_gt_blender.py"),
            "--",
            str(args.input.resolve()),
            str(output / "scene.json"),
        ],
        output,
        60,
    )
    data = json.loads((output / "scene.json").read_text(encoding="utf-8"))
    measurements, cards = [], []
    for file_record in data["records"]:
        for index, mesh in enumerate(file_record["meshes"]):
            vertices = np.asarray(mesh["vertices"], dtype=float)
            faces = np.asarray(mesh["triangles"], dtype=np.int64)
            materials = np.asarray(mesh["triangle_materials"], dtype=np.int64)
            for layer_index, layer in enumerate(mesh["uv_layers"]):
                corners = np.asarray(layer["corner_uv"], dtype=float)
                metrics = uv_reference_metrics(vertices, faces, corners)
                per_material = {
                    str(int(material)): uv_reference_metrics(
                        vertices, faces[materials == material], corners[materials == material]
                    )
                    for material in np.unique(materials)
                }
                row = {
                    "file": file_record["file"],
                    "mesh": mesh["name"],
                    "layer": layer["name"],
                    "vertices": len(vertices),
                    "polygons": len(mesh["polygons"]),
                    "metrics": metrics,
                    "per_material": per_material,
                    "materials": mesh["materials"],
                    "original_uv_topology": mesh.get("uv_topology", {}).get(layer["name"]),
                }
                measurements.append(row)
                filename = f"{Path(file_record['file']).stem}_{index}_{layer_index}.svg"
                polygons = []
                outlines = []
                for triangle in corners:
                    points = " ".join(f"{u * 1024:.4f},{(1 - v) * 1024:.4f}" for u, v in triangle)
                    polygons.append(
                        f'<polygon points="{points}" fill="#458a7540" stroke="#94c9b4" stroke-width=".6"/>'
                    )
                    outlines.append(
                        f'<polygon points="{points}" fill="none" stroke="#edff56" stroke-width=".8"/>'
                    )
                (output / filename).write_text(
                    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1024 1024"><rect width="1024" height="1024" fill="#132324"/>'
                    + "".join(polygons)
                    + "</svg>",
                    encoding="utf-8",
                )
                reference = references.get((file_record["file"], mesh["name"], layer["name"]))
                overlay = ""
                if reference:
                    texture_name = reference["id"] + ".png"
                    shutil.copy2(args.input / reference["texture_reference"], output / texture_name)
                    outline_name = filename.removesuffix(".svg") + ".outline.svg"
                    (output / outline_name).write_text(
                        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1024 1024">'
                        + "".join(outlines)
                        + "</svg>",
                        encoding="utf-8",
                    )
                    row["texture_overlay_reference"] = reference["id"]
                    overlay = f'<details><summary>用户贴图与此 UV 通道的关联展示</summary><div class="overlay"><img src="{texture_name}" alt="用户提供的贴图参考"><img class="outline" src="{outline_name}" alt="同坐标 UV 轮廓"></div><p>用于核对关联；FBX 未提供完整可读的原始材质绑定。</p></details>'
                topology = row["original_uv_topology"] or {}
                topology_text = f"按 FBX UVIndex 连接：{topology.get('chart_count', '—')} 个 UV 岛、{topology.get('seam_edge_count', '—')} 条原始网格切缝边；原曲面含 {topology.get('edge_connected_surface_components', '—')} 个边连通分量。"
                cards.append(
                    f"<article><h2>{html.escape(mesh['name'])} / {html.escape(layer['name'])}</h2>"
                    f"<p>{len(vertices)} 顶点，{len(faces)} 三角形；独占图集并集覆盖 {metrics['unique_atlas_coverage']:.2%}；重复覆盖面积 {metrics['redundant_uv_area']:.4f}；UV 退化三角形 {metrics['degenerate_uv_triangles']}。</p>"
                    f'<p>{topology_text}</p><img src="{filename}" alt="原始 UV 三角形">{overlay}<p>{html.escape(file_record["file"])}</p></article>'
                )
    after = {path.name: sha256(path) for path in args.input.glob("*.FBX")}
    if before != after:
        raise ValueError("Artist source FBX changed during read-only inspection")
    write_json(
        output / "metrics.json",
        {
            "records": measurements,
            "input_sha256": before,
            "input_unchanged": True,
            "seconds": time.monotonic() - started,
            "scope": "External artist references; original objects/materials/UV channels stay separate. Not replacements for fixed V1 inputs.",
            "source_sha256": {
                str(path.relative_to(root)): sha256(path)
                for path in (
                    Path(__file__),
                    root / "scripts/inspect_gt_blender.py",
                    root / "src/reseam/ground_truth.py",
                )
            },
        },
    )
    page = (
        '<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>Artist 真值 UV 核对</title><style>body{background:#eeeae0;color:#172d2a;font:16px sans-serif;margin:4vw}main{display:grid;grid-template-columns:repeat(auto-fit,minmax(360px,1fr));gap:24px}img{width:100%}.overlay{position:relative}.outline{position:absolute;inset:0}p{line-height:1.7}h2{font-size:20px}</style><h1>Artist 真值 UV 核对</h1><p>按原始对象和 UV 通道分别测量。覆盖率使用 0–1 图集内的面积并集；重复或镜像覆盖不重复计入利用率。辅助网格和第二 UV 不自动视为主颜色图集。几何／拓扑不同于固定低模，不能并入同预算方法排名。</p><a href="metrics.json">完整指标</a><main>'
        + "".join(cards)
        + "</main></html>"
    )
    (output / "index.html").write_text(page, encoding="utf-8")
    print(
        json.dumps(
            {
                "objects_and_uv_layers": len(measurements),
                "input_unchanged": True,
                "seconds": time.monotonic() - started,
            }
        )
    )


if __name__ == "__main__":
    main()
