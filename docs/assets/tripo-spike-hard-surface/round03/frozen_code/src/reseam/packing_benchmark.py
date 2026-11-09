"""Conditional fixed-UV comparison. V1 states are read-only and every run starts afresh."""

import html
import importlib.metadata
import importlib.util
import json
import platform
import shutil
import subprocess
import sys
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageDraw

from .fixed_uv import FixedAtlas, PackingProfile, certify, raster_mask, search_scale
from .mesh import load_state, save_state, sha256, write_obj
from .pipeline import export_glb, write_json
from .runtime import read_runtime, run_process
from .types import MeshState


def draw_atlas(state: MeshState, path: Path, resolution: int = 1024) -> None:
    """Atlas coordinates stay in [0,1]; do not auto-fit the drawing bounds."""
    canvas = Image.new("RGB", (resolution, resolution), "#101820")
    draw = ImageDraw.Draw(canvas)
    for face in state.uv_faces:
        points = [(float(x * resolution), float((1 - y) * resolution)) for x, y in state.uv[face]]
        draw.polygon(points, fill="#487d78", outline="#a7c4bc")
    canvas.save(path)


def backend_pose(
    root: Path,
    atlas: FixedAtlas,
    backend: str,
    seed: int,
    trial: int,
    profile: PackingProfile,
    folder: Path,
    seconds: float,
) -> tuple[np.ndarray, dict[str, Any]]:
    folder.mkdir(parents=True, exist_ok=False)
    rng = np.random.default_rng(np.random.SeedSequence([seed, trial]))
    angles = np.zeros(len(atlas.groups)) if trial == 0 else rng.uniform(-np.pi, np.pi, len(atlas.groups))
    # Keep the original atlas extent as the numerical scale; identities remain explicit.
    centers = atlas.centers.copy()
    posed = atlas.reconstruct(angles, centers, atlas.scale)
    request: dict[str, Any] = {
        "mode": "pack_fixed",
        "backend": backend,
        "vertices": posed.mesh.vertices.tolist(),
        "faces": posed.mesh.faces.tolist(),
        "uv": posed.uv.tolist(),
        "uv_faces": posed.uv_faces.tolist(),
        "corner_uv": posed.uv[posed.uv_faces].tolist(),
        "resolution": profile.resolution,
        "padding": int(np.ceil(profile.gap_pixels + 2)),
        "border": profile.border_pixels + 0.05,
        "margin": (profile.gap_pixels / 2 + 1) / profile.resolution,
        "output": str(folder / "response.json"),
    }
    write_json(folder / "request.json", request)
    worker = str(root / "scripts/fixed_pack_worker.py")
    if backend == "blender":
        worker = str(root / "scripts/blender_worker.py")
        argv = [
            read_runtime(root)["blender"],
            "-b",
            "--factory-startup",
            "--threads",
            "4",
            "--python",
            worker,
            "--",
            str(folder / "request.json"),
        ]
    elif backend == "xatlas":
        argv = [sys.executable, worker, str(folder / "request.json")]
    else:
        raise ValueError(f"Unknown backend {backend}")
    run_process(argv, folder, seconds)
    response = json.loads((folder / "response.json").read_text(encoding="utf-8"))
    corners = np.asarray(response.pop("corner_uv"), dtype=float)
    if corners.shape != posed.uv_faces.shape + (2,) or not np.isfinite(corners).all():
        raise ValueError("BACKEND_CORNER_SHAPE")
    uv = np.empty_like(posed.uv)
    uv[posed.uv_faces.reshape(-1)] = corners.reshape(-1, 2)
    if not np.allclose(uv[posed.uv_faces], corners, atol=1e-6, rtol=0):
        raise ValueError("BACKEND_BROKE_UV_IDENTITIES")
    return uv, response


def run_case(
    root: Path,
    source: Path,
    output: Path,
    backend: str,
    seed: int,
    profile: PackingProfile,
    seconds: float = 25,
    trials: int = 4,
) -> dict[str, Any]:
    started = time.monotonic()
    deadline = started + seconds
    output.mkdir(parents=True, exist_ok=False)
    source_hash = sha256(source)
    result: dict[str, Any] = {
        "source": str(source),
        "input_sha256": source_hash,
        "backend": backend,
        "seed": seed,
        "profile": asdict(profile),
        "trials": [],
        "status": "RUNNING",
    }
    best: MeshState | None = None
    try:
        atlas = FixedAtlas.from_state(load_state(source))
        initial = certify(atlas, atlas.source, profile)
        result["initial"] = initial
        if not initial["valid"]:
            raise ValueError(f"SOURCE_SAMPLING_INVALID:{initial['errors']}")
        best, quality = atlas.source.copy(), initial
        save_state(output / "state.json", best)  # feasible checkpoint before any backend invocation
        angles = np.zeros(len(atlas.groups))
        scale = search_scale(atlas, angles, atlas.centers, profile, atlas.scale, deadline - 5)
        if scale is not None:
            candidate = atlas.reconstruct(angles, atlas.centers, scale)
            checked = certify(atlas, candidate, profile)
            if checked["valid"] and checked["occupancy"] > quality["occupancy"]:
                best, quality = candidate, checked
                save_state(output / "state.json", best)
        result["scale_only"] = quality.copy()
        for trial in range(trials):
            if time.monotonic() >= deadline - 5:
                result["stop_reason"] = "BUDGET_EXHAUSTED"
                break
            event: dict[str, Any] = {"trial": trial}
            try:
                uv, metadata = backend_pose(
                    root,
                    atlas,
                    backend,
                    seed,
                    trial,
                    profile,
                    output / f"trial_{trial:02}",
                    min(45, deadline - time.monotonic() - 5),
                )
                angles, centers, residuals = atlas.fit_poses(uv)
                event.update({"backend": metadata, "raw_pose_fit_max_error": max(residuals)})
                scale = search_scale(atlas, angles, centers, profile, atlas.scale, deadline - 5)
                if scale is None:
                    event["status"] = "NO_FEASIBLE_SCALE_FOUND"
                else:
                    candidate = atlas.reconstruct(angles, centers, scale)
                    checked = certify(atlas, candidate, profile)
                    event["quality"] = checked
                    if checked["valid"] and checked["occupancy"] > quality["occupancy"] + 1e-8:
                        best, quality = candidate, checked
                        save_state(output / "state.json", best)
                        event["status"] = "ACCEPTED"
                    else:
                        event["status"] = "REJECTED"
            except (ValueError, RuntimeError, TimeoutError) as error:
                event.update({"status": "FAILED", "reason": str(error)})
            event.update(
                {"elapsed_seconds": time.monotonic() - started, "best_occupancy": quality["occupancy"]}
            )
            result["trials"].append(event)
            write_json(output / "result.json", result)
        result.setdefault("stop_reason", "TRIAL_LIMIT")
        result["quality"] = certify(atlas, best, profile)
        if not result["quality"]["valid"]:
            raise ValueError("FINAL_CERTIFICATION_FAILED")
        mask = raster_mask(best, profile.resolution)
        result["quality"]["pixel_center_occupancy"] = float(mask.mean())
        Image.fromarray(mask.astype(np.uint8) * 255).save(output / "coverage_mask.png")
        draw_atlas(atlas.source, output / "before.png")
        draw_atlas(best, output / "after.png")
        write_obj(output / "packed.obj", best)
        export_glb(best, output / "packed.glb")
        angles, centers, _ = atlas.fit_poses(best.uv)
        write_json(
            output / "poses.json",
            {
                "canonical_uv": atlas.canonical.tolist(),
                "angles": angles.tolist(),
                "centers": centers.tolist(),
                "scale": result["quality"]["scale"],
            },
        )
        result["status"] = (
            "SUCCESS" if any(t["status"] == "ACCEPTED" for t in result["trials"]) else "FALLBACK"
        )
    except (ValueError, RuntimeError, TimeoutError, OSError) as error:
        result.update({"status": "FAILED", "reason": f"{type(error).__name__}:{error}"})
    result["input_unchanged"] = sha256(source) == source_hash
    result["wall_seconds"] = time.monotonic() - started
    result["budget_overrun"] = result["wall_seconds"] > seconds
    write_json(output / "result.json", result)
    return result


def report(output: Path, rows: list[dict[str, Any]]) -> None:
    cards = []
    for row in rows:
        quality, initial = row.get("quality", {}), row.get("initial", {})
        folder = row["folder"]
        name = html.escape(f"{row['model']} / {row['uv_source']} / {row['backend']} / seed {row['seed']}")
        if quality.get("valid"):
            info = (
                f"{initial['occupancy']:.2%} → {quality['occupancy']:.2%} · "
                f"线性纹素密度 ×{quality['density_gain']:.3f} · {quality['islands']} 岛 · "
                f"最小间距 {quality['min_gap_pixels']:.2f}px · 边界 {quality['min_border_pixels']:.2f}px"
            )
            images = f'<img loading="lazy" src="{folder}/before.png" alt="输入 UV"><img loading="lazy" src="{folder}/after.png" alt="输出 UV">'
            links = f'<a href="{folder}/packed.glb">下载无贴图 GLB</a> · <a href="{folder}/result.json">检查与逐次记录</a>'
        else:
            info, images, links = html.escape(str(row.get("reason", row["status"]))), "", ""
        cards.append(
            f'<article data-model="{html.escape(row["model"])}"><h2>{name}</h2><p>{info}</p><p>{row["status"]} · {row.get("wall_seconds", 0):.2f}s</p><div class="images">{images}</div><p>{links}</p></article>'
        )
    content = """<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>ReSeam · 固定岛排布对照</title><style>
    body{margin:0;background:#eeeae0;color:#172923;font:16px/1.6 system-ui}main{max-width:1120px;margin:auto;padding:32px}h1{font-size:32px}h2{font-size:19px}article{background:#fffdf7;padding:22px;margin:24px 0;border-top:3px solid #36736a}a{color:#185b52}.images{display:flex;gap:16px}.images img{width:calc(50% - 8px);height:auto}header{border-bottom:1px solid #9aaba2;padding-bottom:20px}</style><main><header><h1>固定 UV 岛，能排紧多少？</h1><p>左：冻结的 V1 输入；右：保留最好合法布局。1024²，岛间距 ≥16px，图集边界 ≥8px。只旋转、平移与共同缩放。</p><p>利用率按 UV 三角形并集计算。保持接缝、岛形与相对密度；未重新烘焙，不代表渲染质量或 artist 验收完成。FALLBACK 表示后端未获得通过验收的提升。多起点结果是有限搜索，不是最优上界。</p><p><a href="summary.json">完整矩阵与版本记录</a></p></header>"""
    controls = """<label>模型 <select id="model"><option value="all">全部</option><option value="energy_blade">能量刀</option><option value="dragon">幻想龙</option></select></label><script>document.addEventListener('change',e=>{if(e.target.id==='model')document.querySelectorAll('article').forEach(x=>x.hidden=e.target.value!=='all'&&x.dataset.model!==e.target.value)})</script>"""
    (output / "index.html").write_text(
        content + controls + "".join(cards) + "</main></html>", encoding="utf-8"
    )


def run_packing_benchmark(root: Path, config_path: Path) -> None:
    start = time.monotonic()
    config = json.loads(config_path.read_text(encoding="utf-8"))
    output = root / "artifacts/v2" / config["round_id"]
    output.mkdir(parents=True, exist_ok=False)
    profile = PackingProfile(**config["profile"])
    files = [
        *sorted((root / "src/reseam").glob("*.py")),
        root / "scripts/fixed_pack_worker.py",
        root / "scripts/blender_worker.py",
        root / "uv.lock",
    ]
    sources = {str(p.relative_to(root)): sha256(p) for p in files}
    for path in files:
        target = output / "source_snapshot" / path.relative_to(root)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
    shutil.copy2(config_path, output / "config.json")
    input_hashes = {case["path"]: sha256(root / case["path"]) for case in config["sources"]}
    for case in config["sources"]:
        if case.get("sha256") != input_hashes[case["path"]]:
            raise ValueError(f"LOCKED_INPUT_HASH_MISMATCH:{case['path']}")
    spec = importlib.util.find_spec("xatlas")
    if spec is None or spec.origin is None:
        raise RuntimeError("xatlas module unavailable")
    backend_paths = {"blender": Path(read_runtime(root)["blender"]), "xatlas": Path(spec.origin)}
    backend_hashes = {
        name: {"path": str(path), "sha256": sha256(path)} for name, path in backend_paths.items()
    }
    summary: dict[str, Any] = {
        "config": config,
        "config_sha256": sha256(config_path),
        "code_sha256": sources,
        "backend_files": backend_hashes,
        "input_sha256": input_hashes,
        "git_head": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip(),
        "python": sys.version,
        "platform": platform.platform(),
        "packages": {p: importlib.metadata.version(p) for p in ("numpy", "shapely", "xatlas", "trimesh")},
        "rows": [],
        "scope": "fixed_uv_packing_only; no bake, no seam search, no controlled overlap",
    }
    for case in config["sources"]:
        for backend in config["backends"]:
            for seed in config["seeds"]:
                folder = f"{case['model']}_{case['method']}_{backend}_{seed}"
                identity = {
                    "model": case["model"],
                    "uv_source": case["method"],
                    "backend": backend,
                    "seed": seed,
                    "folder": folder,
                }
                remaining = config["total_seconds"] - (time.monotonic() - start)
                if remaining < config["run_seconds"] + 10:
                    row = {**identity, "status": "NOT_RUN_BUDGET"}
                else:
                    row = {
                        **run_case(
                            root,
                            root / case["path"],
                            output / folder,
                            backend,
                            seed,
                            profile,
                            config["run_seconds"],
                            config["trials"],
                        ),
                        **identity,
                    }
                summary["rows"].append(row)
                write_json(output / "summary.json", summary)
                print(
                    json.dumps(
                        {
                            **identity,
                            "status": row["status"],
                            "occupancy": row.get("quality", {}).get("occupancy"),
                        }
                    ),
                    flush=True,
                )
    summary["code_unchanged"] = all(sha256(root / p) == digest for p, digest in sources.items())
    summary["inputs_unchanged"] = all(sha256(root / p) == digest for p, digest in input_hashes.items())
    summary["backends_unchanged"] = all(
        sha256(Path(item["path"])) == item["sha256"] for item in backend_hashes.values()
    )
    summary["matrix_complete"] = all(r["status"] != "NOT_RUN_BUDGET" for r in summary["rows"])
    report(output, summary["rows"])
    summary["wall_seconds"] = time.monotonic() - start
    summary["total_budget_overrun"] = summary["wall_seconds"] > config["total_seconds"]
    write_json(output / "summary.json", summary)
