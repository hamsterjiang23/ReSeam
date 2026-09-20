"""Fixed-geometry texture optimization with held-out validation and honest test metrics."""

import argparse
import importlib.util
import json
from pathlib import Path
from typing import Any


def main() -> None:
    parser = argparse.ArgumentParser()
    for name in ("high", "low", "initial", "output", "renderer-root", "chart-mask", "padding-map"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--iterations", type=int, default=128)
    parser.add_argument("--seed", type=int, default=17)
    args = parser.parse_args()
    import numpy as np
    import torch
    import trimesh
    from PIL import Image

    torch.manual_seed(args.seed)
    np.random.seed(args.seed)
    torch.set_num_threads(4)
    device = torch.device("cuda")
    spec = importlib.util.spec_from_file_location(
        "reseam_external_renderer", args.renderer_root / "unlit_mesh_render.py"
    )
    if spec is None or spec.loader is None:
        raise ValueError("Missing renderer module")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    high = trimesh.load(args.high, force="mesh", process=False)
    low = trimesh.load(args.low, force="mesh", process=False)

    def tensors(mesh: Any) -> tuple[Any, Any, Any]:
        return (
            torch.as_tensor(np.asarray(mesh.vertices).copy(), dtype=torch.float32, device=device),
            torch.as_tensor(np.asarray(mesh.faces).copy(), dtype=torch.int32, device=device),
            torch.as_tensor(np.asarray(mesh.visual.uv).copy(), dtype=torch.float32, device=device),
        )

    high_v, high_f, high_uv = tensors(high)
    low_v, low_f, low_uv = tensors(low)
    source_image = high.visual.material.baseColorTexture.convert("RGB")
    source = torch.from_numpy(np.asarray(source_image, dtype=np.float32) / 255).unsqueeze(0).to(device)
    initial = (
        torch.from_numpy(np.asarray(Image.open(args.initial).convert("RGB"), dtype=np.float32) / 255)
        .unsqueeze(0)
        .to(device)
    )
    center = high.bounds.mean(axis=0).tolist()
    distance = float(np.linalg.norm(high.extents) * 1.4)
    train_views = [(0, 15), (90, 15), (180, 15), (270, 15), (0, 75), (0, -75)]
    validation_views = [(45, 25), (225, -25)]
    test_views = [(30, 10), (120, 10), (210, 10), (300, 10), (45, 65), (225, -65)]
    views = train_views + validation_views + test_views

    def render(texture: Any, view: tuple[int, int], reference: bool = False) -> Any:
        vertices, faces, uv = (high_v, high_f, high_uv) if reference else (low_v, low_f, low_uv)
        return module.render_unlit_mesh(
            vertices,
            faces,
            uv,
            faces,
            texture,
            camera_dist=distance,
            camera_elev=view[1],
            camera_azim=view[0],
            camera_at=center,
            image_size=512,
            transparent=True,
            cull_backfaces=True,
            device=device,
            renderer="nvdiffrast",
        )

    with torch.no_grad():
        targets = [render(source, view, True).detach() for view in views]
        baseline_renders = [render(initial, view).detach() for view in views]
    masks = [
        (target[..., 3:] > 0.99) & (baseline[..., 3:] > 0.99)
        for target, baseline in zip(targets, baseline_renders, strict=True)
    ]
    if any(int(mask.sum()) == 0 for mask in masks):
        raise ValueError("Empty supervision or evaluation foreground")
    labels = torch.as_tensor(np.load(args.chart_mask), device=device)
    mx = ((labels[:, 1:] == labels[:, :-1]) & (labels[:, 1:] > 0))[None, ..., None]
    my = ((labels[1:] == labels[:-1]) & (labels[1:] > 0))[None, ..., None]

    def error(rendered: Any, index: int, squared: bool = False) -> Any:
        residual = rendered[..., :3] - targets[index][..., :3]
        value = residual.square() if squared else residual.abs()
        return (value * masks[index]).sum() / (3 * masks[index].sum())

    def validation(texture: Any) -> float:
        with torch.no_grad():
            return float(torch.stack([error(render(texture, views[i]), i) for i in (6, 7)]).mean().cpu())

    initial_validation = validation(initial)
    best_validation = initial_validation
    best = initial.detach().clone()
    best_step = 0
    logits = torch.logit(initial.clamp(1e-4, 1 - 1e-4)).detach().requires_grad_(True)
    optimizer = torch.optim.Adam([logits], lr=0.01)
    history = []
    for step in range(1, args.iterations + 1):
        optimizer.zero_grad(set_to_none=True)
        texture = torch.sigmoid(logits)
        rgb = torch.stack([error(render(texture, views[i]), i, True) for i in range(6)]).mean()
        anchor = (texture - initial).square().mean()
        dx = (texture[:, :, 1:] - texture[:, :, :-1]).abs()
        dy = (texture[:, 1:] - texture[:, :-1]).abs()
        tv = (dx * mx).sum() / (3 * mx.sum().clamp_min(1)) + (dy * my).sum() / (3 * my.sum().clamp_min(1))
        loss = rgb + 0.05 * anchor + 0.0005 * tv
        if not torch.isfinite(loss):
            raise ValueError("Nonfinite optimization loss")
        loss.backward()
        if logits.grad is None or not torch.isfinite(logits.grad).all():
            raise ValueError("Missing or nonfinite gradients")
        gradient = float(logits.grad.norm().detach().cpu())
        optimizer.step()
        entry = {
            "step": step,
            "loss": float(loss.detach().cpu()),
            "rgb_mse": float(rgb.detach().cpu()),
            "gradient_norm": gradient,
        }
        if step % 8 == 0 or step == args.iterations:
            candidate = torch.sigmoid(logits).detach()
            measured = validation(candidate)
            entry["validation_mae"] = measured
            if measured < best_validation:
                best, best_validation, best_step = candidate.clone(), measured, step
            print(json.dumps(entry), flush=True)
        history.append(entry)
    # Regenerate padding from nearest chart texels; never average unrelated chart colors.
    padding = np.load(args.padding_map)
    indices, expanded = padding["indices"], padding["expanded"]
    padded = best[0].detach().cpu().numpy().copy()
    padded[expanded] = padded[indices[0][expanded], indices[1][expanded]]
    exported = np.round(np.clip(padded, 0, 1) * 255).astype(np.uint8)
    export_tensor = torch.from_numpy(exported.astype(np.float32) / 255).unsqueeze(0).to(device)
    export_validation = validation(export_tensor)
    padding_fallback = export_validation > initial_validation
    if padding_fallback:
        export_tensor = initial
        exported = np.round(initial[0].detach().cpu().numpy() * 255).astype(np.uint8)
        export_validation = initial_validation
        best_step = 0
    best = export_tensor
    with torch.no_grad():
        test_initial = [float(error(baseline_renders[i], i).cpu()) for i in range(8, len(views))]
        test_final = [float(error(render(best, views[i]), i).cpu()) for i in range(8, len(views))]
    Image.fromarray(exported).save(args.output / "BaseColor.png")
    result = {
        "iterations": args.iterations,
        "seed": args.seed,
        "best_step": best_step,
        "initial_validation_mae": initial_validation,
        "best_validation_mae": best_validation,
        "exported_validation_mae": export_validation,
        "padding_pixels": 8,
        "padding_fallback_to_initial": padding_fallback,
        "test_initial_mae": test_initial,
        "test_final_mae": test_final,
        "train_views": train_views,
        "validation_views": validation_views,
        "test_views": test_views,
        "history": history,
        "learning_rate": 0.01,
        "initial_anchor_weight": 0.05,
        "within_chart_tv_weight": 0.0005,
        "geometry_optimized": False,
        "uv_optimized": False,
        "renderer": "nvdiffrast",
        "color_convention": "sRGB texture samples; masked unlit RGB; independent Blender evaluation follows",
    }
    (args.output / "metrics.json").write_text(json.dumps(result, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
