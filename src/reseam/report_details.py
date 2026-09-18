"""Offline, explicitly diagnostic views of frozen results."""

import html
import json
import shutil
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image
from scipy.ndimage import uniform_filter

from .constraints import load_constraints, suggestions
from .diagnostics import artist_metrics, package_optimized
from .mesh import load_state, sha256
from .pipeline import write_json
from .runtime import read_runtime


def enrich(run: Path) -> dict[str, Any]:
    path = run / "state.json"
    if not path.exists():
        return {"status": "NO_UV_OUTPUT"}
    state = load_state(path)
    regions = None
    snapshot = run / "constraints.json"
    if snapshot.exists():
        constraints = load_constraints(snapshot, state.mesh)
        if constraints.confirmed:
            regions = json.loads(snapshot.read_text(encoding="utf-8")).get("protected_regions", [])
    data = artist_metrics(state, regions)
    data["state_sha256"] = sha256(path)
    write_json(run / "artist_diagnostics.json", data)
    packaging = package_optimized(run)
    return {"diagnostics": data, "optimized_asset": packaging}


def detail_crops(root: Path, records: list[dict[str, Any]]) -> None:
    """Select a shared crop per model from pooled seam-error maps, not per-method favorable views."""
    for model in sorted({record["model"] for record in records}):
        available = [root / r["path"] for r in records if r["model"] == model and r["seed"] == 17]
        best_score = -1.0
        selected: tuple[int, int, int] | None = None
        for view in range(6):
            pooled = np.zeros((512, 512))
            mask_union = np.zeros((512, 512), dtype=bool)
            for run in available:
                base = run / "downstream"
                paths = [
                    base / directory / f"view_{view:02d}.png"
                    for directory in ("renders_high", "renders_blender", "mask_seam")
                ]
                if not all(path.exists() for path in paths):
                    continue
                ref, baked, mask = [
                    np.asarray(Image.open(path).convert("RGBA"), dtype=float) / 255 for path in paths
                ]
                if ref.shape != (512, 512, 4):
                    continue
                visible = (mask[..., 0] > 0.5) & (ref[..., 3] > 0.98) & (baked[..., 3] > 0.98)
                pooled = np.maximum(pooled, np.abs(ref[..., :3] - baked[..., :3]).mean(axis=2) * visible)
                mask_union |= visible
            count = uniform_filter(mask_union.astype(float), size=64)
            local_error = uniform_filter(pooled, size=64) / np.maximum(count, 1e-9)
            local_error[count < 16 / 4096] = 0
            index = np.unravel_index(int(local_error.argmax()), local_error.shape)
            if float(local_error[index]) > best_score and mask_union.any():
                best_score = float(local_error[index])
                selected = (view, int(index[1]), int(index[0]))
        if selected is None:
            continue
        view, x, y = selected
        left, top = min(max(0, x - 64), 384), min(max(0, y - 64), 384)
        box = (left, top, left + 128, top + 128)
        for run in available:
            output = run / "details"
            output.mkdir(exist_ok=True)
            for label in ("high", "blender", "differentiable"):
                image = run / "downstream" / f"renders_{label}" / f"view_{view:02d}.png"
                if image.exists():
                    Image.open(image).crop(box).save(output / f"{label}.png")
            write_json(
                output / "selection.json",
                {
                    "model": model,
                    "view": view,
                    "crop": box,
                    "selection": "Pooled available-method seam error; common crop for visual diagnosis only, not a new independent test",
                },
            )


def anytime_svg(run: Path) -> str:
    path = run / "result.json"
    if not path.exists():
        return ""
    result = json.loads(path.read_text(encoding="utf-8"))
    if "first_feasible_seconds" not in result:
        return '<p class="note">旧轮未记录首次可行解时间，不作补估。</p>'
    first = result["first_feasible_seconds"]
    initial = result.get("initial_quality") or {}
    if first is None or "objective" not in initial:
        return '<p class="note">未获得已验证可行解。</p>'
    recorded = result.get("anytime_history")
    if not recorded:
        return '<p class="note">未记录当时的目标值，不根据最终结果补造早期曲线。</p>'
    points = [(float(item["seconds"]), float(item["objective"])) for item in recorded]
    log = run / "search/candidates.jsonl"
    if log.exists() and result.get("search_started_at_seconds") is not None:
        offset = float(result["search_started_at_seconds"])
        for line in log.read_text(encoding="utf-8").splitlines():
            event = json.loads(line)
            points.append((offset + float(event["cumulative_seconds"]), float(event["best_objective"])))
    budget = float(result["seconds"])
    points = [(t, value) for t, value in points if t <= budget]
    if not points:
        return '<p class="note">预算内没有已验证可行解。</p>'
    low, high = min(v for _, v in points), max(v for _, v in points)
    delta = max(high - low, max(abs(high) * 0.02, 0.001))
    polyline = " ".join(
        f"{24 + t / budget * 290:.2f},{72 - (value - low) / delta * 50:.2f}" for t, value in points
    )
    return (
        f'<svg viewBox="0 0 340 110" role="img" aria-label="本配置最好可行目标随时间变化">'
        f'<path d="M24 16V80H316" fill="none" stroke="#79897d"/><polyline points="{polyline}" fill="none" stroke="#ad542e" stroke-width="2"/>'
        f'<text x="24" y="99" font-size="11">首次可行 {first:.2f}s</text><text x="235" y="99" font-size="11">预算 {budget:g}s</text>'
        f'<text x="24" y="12" font-size="11">本配置 C：{html.escape(f"{high:.4f} → {low:.4f}")}</text></svg>'
    )


def atlas_overlay(root: Path, run: Path, code: str) -> str:
    packed = run / "downstream/pack/state.json"
    texture = run / "downstream/blender/BaseColor.png"
    if not packed.exists() or not texture.exists():
        return ""
    state = load_state(packed)
    paths = []
    for face in state.uv_faces:
        points = " ".join(f"{state.uv[v, 0] * 1024:.4f},{(1 - state.uv[v, 1]) * 1024:.4f}" for v in face)
        paths.append(
            f'<polygon points="{points}" fill="none" stroke="#b9ded4" stroke-opacity=".5" stroke-width=".6"/>'
        )
    for edge, entries in enumerate(state.mesh.edge_faces):
        if edge not in state.seams and len(entries) != 1:
            continue
        for face, corner in entries:
            a, b = state.uv[state.uv_faces[face, [corner, (corner + 1) % 3]]]
            paths.append(
                f'<path d="M{a[0] * 1024:.4f},{(1 - a[1]) * 1024:.4f}L{b[0] * 1024:.4f},{(1 - b[1]) * 1024:.4f}" '
                'fill="none" stroke="#e5ff52" stroke-width="1.4"/>'
            )
    output = run / "details/atlas_uv.svg"
    output.parent.mkdir(exist_ok=True)
    output.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1024 1024">' + "".join(paths) + "</svg>",
        encoding="utf-8",
    )
    texture_url = html.escape(texture.relative_to(root).as_posix(), quote=True)
    outline_url = html.escape(output.relative_to(root).as_posix(), quote=True)
    return (
        "<details><summary>实际图集：颜色贴图与 UV 轮廓</summary>"
        f'<div class="atlas"><input id="atlas_{code}" type="checkbox" checked><label for="atlas_{code}">显示 UV 轮廓</label>'
        f'<div class="atlas-images"><img loading="lazy" src="{texture_url}" alt="烘焙颜色图集">'
        f'<img class="atlas-outline" loading="lazy" src="{outline_url}" alt="同坐标 UV 轮廓"></div></div>'
        '<p class="note">直接使用 0–1 UV 坐标对齐图集，未重新缩放岛形。彩色扩边不计入覆盖率。</p></details>'
    )


def editing_scene(repository: Path, run: Path, output: Path) -> Path | None:
    """Anonymous editable mesh with original identities and packed texture files."""
    from .downstream import blender

    packed_path = run / "downstream/pack/state.json"
    if not packed_path.exists():
        return None
    scene_folder = output / "edit_scene"
    destination = scene_folder / "editable.blend"
    identity_path = scene_folder / "identity.json"
    identity = {"state_sha256": sha256(packed_path)}
    for file in ("BaseColor.png", "Normal.png"):
        texture = run / "downstream/blender" / file
        if texture.exists():
            identity[file] = sha256(texture)
    if destination.exists():
        if not identity_path.exists():
            raise ValueError("Existing editing scene lacks source identity; create a new review package")
        recorded = json.loads(identity_path.read_text(encoding="utf-8"))
        if recorded != {**identity, "scene_sha256": sha256(destination)}:
            raise ValueError("Editing scene source or content changed; create a new review package")
        return destination
    state = load_state(packed_path)
    request = {
        "mode": "annotate",
        "vertices": state.mesh.vertices.tolist(),
        "faces": state.mesh.faces.tolist(),
        "corner_uv": state.uv[state.uv_faces].tolist(),
        "edges": state.mesh.edges,
        "seam_edges": [state.mesh.edges[e] for e in state.seams],
        "annotation": suggestions(state.mesh),
        "editing_template": True,
        "output": str(destination),
        "addon": str(repository / "scripts/reseam_annotation_addon.py"),
    }
    for file, key in (("BaseColor.png", "base_texture"), ("Normal.png", "normal_texture")):
        source = run / "downstream/blender" / file
        if source.exists():
            target = output / file
            shutil.copy2(source, target)
            request[key] = str(target)
    blender(repository, read_runtime(repository), request, scene_folder, 60)
    write_json(identity_path, {**identity, "scene_sha256": sha256(destination)})
    return destination
