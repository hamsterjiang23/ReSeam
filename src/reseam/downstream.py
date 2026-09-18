"""Fixed packing, real Cycles baking and separate evaluation of texture optimization."""

import json
import time
from pathlib import Path
from typing import Any

import numpy as np
import trimesh
from PIL import Image, ImageDraw
from scipy.ndimage import binary_erosion, distance_transform_edt
from shapely import Polygon, union_all
from trimesh.visual.texture import TextureVisuals

from .mesh import chart_labels, import_uv_arrays, load_mesh, load_state, save_state, sha256
from .pipeline import draw_uv, export_glb, write_json
from .runtime import read_runtime, run_process
from .types import MeshState

TEST_VIEWS = [(30, 10), (120, 10), (210, 10), (300, 10), (45, 65), (225, -65)]


def blender(
    root: Path, runtime: dict[str, Any], request: dict[str, Any], folder: Path, seconds: float
) -> None:
    folder.mkdir(parents=True, exist_ok=False)
    path = folder / "request.json"
    write_json(path, request)
    run_process(
        [
            runtime["blender"],
            "--background",
            "--factory-startup",
            "--threads",
            "4",
            "--python",
            str(root / "scripts/blender_worker.py"),
            "--",
            str(path),
        ],
        folder,
        seconds,
    )


def packing_metrics(state: MeshState, resolution: int = 1024) -> dict[str, Any]:
    labels = chart_labels(state)
    shapes = [
        union_all([Polygon(state.uv[state.uv_faces[fi]]) for fi in np.flatnonzero(labels == label)])
        for label in np.unique(labels)
    ]
    min_gap = float("inf")
    overlap = 0.0
    for index, first in enumerate(shapes):
        for second in shapes[index + 1 :]:
            min_gap = min(min_gap, float(first.distance(second)) * resolution)
            overlap += float(first.intersection(second).area)
    in_bounds = bool(np.all(state.uv >= -1e-6) and np.all(state.uv <= 1 + 1e-6))
    return {
        "min_gap_pixels": min_gap if np.isfinite(min_gap) else None,
        "overlap_area": overlap,
        "in_bounds": in_bounds,
        "coverage": sum(float(shape.area) for shape in shapes),
        "valid": in_bounds and overlap < 1e-10 and (min_gap >= 16 - 0.05),
    }


def pack(
    root: Path, state: MeshState, runtime: dict[str, Any], folder: Path, seconds: float
) -> tuple[MeshState, dict[str, Any]]:
    request = {
        "mode": "pack",
        "vertices": state.mesh.vertices.tolist(),
        "faces": state.mesh.faces.tolist(),
        "corner_uv": state.uv[state.uv_faces].tolist(),
        "margin": 9 / 1024,
        "output": str(folder / "uv.json"),
    }
    blender(root, runtime, request, folder, seconds)
    result = json.loads((folder / "uv.json").read_text())
    packed = state.copy()
    corners = np.asarray(result["corner_uv"], dtype=float)
    for fi, face in enumerate(packed.uv_faces):
        packed.uv[face] = corners[fi]
    if not np.allclose(packed.uv[packed.uv_faces], corners, atol=1e-6, rtol=0):
        raise ValueError("Packing broke UV connectivity")
    quality = packing_metrics(packed)
    save_state(folder / "state.json", packed)
    draw_uv(packed, folder / "uv_layout.png")
    export_glb(packed, folder / "packed.glb")
    return packed, quality


def image_metrics(reference: Path, rendered: Path, mask_path: Path | None = None) -> dict[str, float | int]:
    a = np.asarray(Image.open(reference).convert("RGBA"), dtype=float) / 255
    b = np.asarray(Image.open(rendered).convert("RGBA"), dtype=float) / 255
    if a.shape != b.shape:
        raise ValueError("Render dimensions differ")
    foreground = (a[..., 3] > 0.98) & (b[..., 3] > 0.98)
    mask = np.asarray(binary_erosion(foreground, iterations=3), dtype=bool)
    if mask_path is not None:
        mask_image = np.asarray(Image.open(mask_path).convert("RGBA"), dtype=float) / 255
        mask &= (mask_image[..., 0] > 0.5) & (mask_image[..., 3] > 0.98)
    if not mask.any():
        raise ValueError("No shared rendered foreground")
    difference = a[..., :3][mask] - b[..., :3][mask]
    union = (a[..., 3] > 0.5) | (b[..., 3] > 0.5)
    intersection = (a[..., 3] > 0.5) & (b[..., 3] > 0.5)
    mse = float(np.mean(difference**2))
    return {
        "mae": float(np.mean(np.abs(difference))),
        "psnr": float(-10 * np.log10(max(mse, 1e-12))),
        "silhouette_iou": float(intersection.sum() / max(1, union.sum())),
        "pixels": int(mask.sum()),
    }


def scene_request(state: MeshState, high: Path) -> dict[str, Any]:
    mesh = trimesh.load(high, force="mesh", process=False)
    if not isinstance(mesh, trimesh.Trimesh):
        raise TypeError("Expected a triangle high reference")
    center = mesh.bounds.mean(axis=0)
    diagonal = float(np.linalg.norm(mesh.extents))
    return {
        "vertices": state.mesh.vertices.tolist(),
        "faces": state.mesh.faces.tolist(),
        "corner_uv": state.uv[state.uv_faces].tolist(),
        "high": str(high),
        "camera_center_blender": [float(center[0]), float(-center[2]), float(center[1])],
        "camera_distance": diagonal * 1.8,
        "ortho_scale": diagonal * 1.05,
        "views": TEST_VIEWS,
        "resolution": 512,
        "cage_extrusion": diagonal * 0.03,
    }


def prepare_annotation(root: Path, model: str, output: Path) -> Path:
    mesh = load_mesh(root / "assets/v1" / model / "low.obj")
    from .constraints import suggestions

    suggestion = suggestions(mesh)
    suggestion["mesh_sha256"] = sha256(root / "assets/v1" / model / "low.obj")
    request = {
        "mode": "annotate",
        "vertices": mesh.vertices.tolist(),
        "faces": mesh.faces.tolist(),
        "edges": mesh.edges,
        "annotation": suggestion,
        "output": str(output / "annotation.blend"),
        "addon": str(root / "scripts/reseam_annotation_addon.py"),
    }
    blender(root, read_runtime(root), request, output, 60)
    write_json(output / "suggestions.json", suggestion)
    return output / "annotation.blend"


def evaluate(
    root: Path, run_folder: Path, seconds: float = 600, differentiable: bool = True
) -> dict[str, Any]:
    start = time.monotonic()
    runtime = read_runtime(root)
    run = json.loads((run_folder / "result.json").read_text())
    state = load_state(run_folder / "state.json")
    folder = run_folder / "downstream"
    folder.mkdir(exist_ok=False)
    result: dict[str, Any] = {"status": "RUNNING", "input_uv_unchanged": None}

    def remaining() -> float:
        value = seconds - (time.monotonic() - start)
        if value <= 0:
            raise TimeoutError("DOWNSTREAM_BUDGET_EXHAUSTED")
        return value

    try:
        packed, result["packing"] = pack(root, state, runtime, folder / "pack", remaining())
        if not result["packing"]["valid"]:
            raise ValueError("PACKING_GAP_OR_OVERLAP_INVALID")
        high = root / "assets/v1" / run["model"] / "high.glb"
        common = scene_request(packed, high)
        bake_folder = folder / "blender"
        blender(
            root,
            runtime,
            {
                **common,
                "mode": "bake",
                "resolution": 1024,
                "folder": str(bake_folder),
                "output": str(bake_folder / "bake.json"),
            },
            bake_folder,
            remaining(),
        )
        base = bake_folder / "BaseColor.png"
        normal = bake_folder / "Normal.png"
        bake_result = json.loads((bake_folder / "bake.json").read_text())
        result["projection_probe"] = bake_result["projection_probe"]
        # Exporting from Blender may split vertices; reimport and restore original triangle identity.
        exported = trimesh.load(bake_folder / "asset.glb", force="mesh", process=False)
        if not isinstance(exported, trimesh.Trimesh):
            raise TypeError("Blender export is not a mesh")
        if not isinstance(exported.visual, TextureVisuals):
            raise TypeError("Blender export has no UV")
        restored = import_uv_arrays(
            packed.mesh,
            np.asarray(exported.vertices),
            np.asarray(exported.faces),
            np.asarray(exported.visual.uv),
            np.asarray(exported.faces),
        )
        if restored.seams != packed.seams or not np.allclose(
            restored.uv[restored.uv_faces], packed.uv[packed.uv_faces], atol=1e-6, rtol=0
        ):
            raise ValueError("Blender bake/export changed UV topology or coordinates")
        export_glb(packed, folder / "packed_basecolor.glb", base)
        result["input_uv_unchanged"] = True
        for route in ("high", "blender"):
            destination = folder / f"renders_{route}"
            blender(
                root,
                runtime,
                {
                    **common,
                    "mode": "render_high" if route == "high" else "render_low",
                    "texture": str(base),
                    "folder": str(destination),
                    "output": str(destination / "views.json"),
                },
                destination,
                remaining(),
            )
        result["blender_views"] = [
            image_metrics(
                folder / "renders_high" / f"view_{i:02d}.png",
                folder / "renders_blender" / f"view_{i:02d}.png",
            )
            for i in range(6)
        ]
        result["blender_mae"] = float(np.mean([item["mae"] for item in result["blender_views"]]))
        # Project UV-space diagnostic masks with the same low mesh and cameras (occlusion-aware).
        seam_mask = Image.new("RGB", (1024, 1024), (0, 0, 0))
        seam_draw = ImageDraw.Draw(seam_mask)
        for ei in packed.seams:
            for fi, corner in packed.mesh.edge_faces[ei]:
                face_uv = packed.uv[packed.uv_faces[fi]]
                points = [
                    (float(face_uv[c, 0] * 1023), float((1 - face_uv[c, 1]) * 1023))
                    for c in (corner, (corner + 1) % 3)
                ]
                seam_draw.line(points, fill="white", width=5)
        projection_mask = Image.new("RGB", (1024, 1024), (0, 0, 0))
        projection_draw = ImageDraw.Draw(projection_mask)
        for fi, fraction in enumerate(bake_result["projection_probe"]["face_hit_fraction"]):
            if fraction == 1:
                points = [
                    (float(uv[0] * 1023), float((1 - uv[1]) * 1023)) for uv in packed.uv[packed.uv_faces[fi]]
                ]
                projection_draw.polygon(points, fill="white")
        for label, mask_image in (("seam", seam_mask), ("projection", projection_mask)):
            mask_texture = folder / f"{label}_mask.png"
            mask_image.save(mask_texture)
            destination = folder / f"mask_{label}"
            blender(
                root,
                runtime,
                {
                    **common,
                    "mode": "render_low",
                    "texture": str(mask_texture),
                    "folder": str(destination),
                    "output": str(destination / "views.json"),
                },
                destination,
                remaining(),
            )
            diagnostic = []
            for i in range(6):
                try:
                    diagnostic.append(
                        image_metrics(
                            folder / "renders_high" / f"view_{i:02d}.png",
                            folder / "renders_blender" / f"view_{i:02d}.png",
                            destination / f"view_{i:02d}.png",
                        )
                    )
                except ValueError:
                    diagnostic.append({"status": "NO_DIAGNOSTIC_PIXELS"})
            result[f"blender_{label}_views"] = diagnostic
        for route in ("high", "blender"):
            destination = folder / f"normal_{route}"
            blender(
                root,
                runtime,
                {
                    **common,
                    "views": TEST_VIEWS[:2],
                    "normal_preview": True,
                    "mode": "render_high" if route == "high" else "render_low",
                    "texture": str(base),
                    "normal": str(normal),
                    "folder": str(destination),
                    "output": str(destination / "views.json"),
                },
                destination,
                remaining(),
            )
        result["normal_views"] = [
            image_metrics(
                folder / "normal_high" / f"view_{i:02d}.png", folder / "normal_blender" / f"view_{i:02d}.png"
            )
            for i in range(2)
        ]
        result["mips"] = []
        for size in (256, 128):
            mip = folder / f"basecolor_mip_{size}.png"
            Image.open(base).resize((size, size), Image.Resampling.LANCZOS).save(mip)
            destination = folder / f"mip_{size}"
            blender(
                root,
                runtime,
                {
                    **common,
                    "views": TEST_VIEWS[:2],
                    "mode": "render_low",
                    "texture": str(mip),
                    "folder": str(destination),
                    "output": str(destination / "views.json"),
                },
                destination,
                remaining(),
            )
            result["mips"].append(
                {
                    "size": size,
                    "views": [
                        image_metrics(
                            folder / "renders_high" / f"view_{i:02d}.png", destination / f"view_{i:02d}.png"
                        )
                        for i in range(2)
                    ],
                }
            )
        write_json(folder / "result.json", result)
        if differentiable:
            diff_folder = folder / "differentiable"
            diff_folder.mkdir()
            chart_mask = Image.new("I", (1024, 1024), 0)
            draw = ImageDraw.Draw(chart_mask)
            labels = chart_labels(packed)
            for fi, face in enumerate(packed.uv_faces):
                points = [(float(uv[0] * 1023), float((1 - uv[1]) * 1023)) for uv in packed.uv[face]]
                draw.polygon(points, fill=int(labels[fi]) + 1)
            mask_path = diff_folder / "chart_mask.npy"
            np.save(mask_path, np.asarray(chart_mask, dtype=np.int32))
            padding_result = distance_transform_edt(np.asarray(chart_mask) == 0, return_indices=True)
            if not isinstance(padding_result, tuple):
                raise TypeError("Distance transform did not return padding indices")
            distances, indices = padding_result
            padding_path = diff_folder / "padding_map.npz"
            np.savez_compressed(
                padding_path,
                expanded=(np.asarray(chart_mask) == 0) & (distances <= 8),
                indices=indices.astype(np.int32),
            )
            command = [
                runtime["differentiable_python"],
                str(root / "scripts/differentiable_worker.py"),
                "--high",
                str(high),
                "--low",
                str(folder / "packed_basecolor.glb"),
                "--initial",
                str(base),
                "--output",
                str(diff_folder),
                "--renderer-root",
                runtime["differentiable_renderer_root"],
                "--seed",
                str(run["seed"]),
                "--iterations",
                "128",
                "--chart-mask",
                str(mask_path),
                "--padding-map",
                str(padding_path),
            ]
            run_process(command, diff_folder, remaining())
            result["differentiable"] = json.loads((diff_folder / "metrics.json").read_text())
            destination = folder / "renders_differentiable"
            blender(
                root,
                runtime,
                {
                    **common,
                    "mode": "render_low",
                    "texture": str(diff_folder / "BaseColor.png"),
                    "folder": str(destination),
                    "output": str(destination / "views.json"),
                },
                destination,
                remaining(),
            )
            result["differentiable_views"] = [
                image_metrics(
                    folder / "renders_high" / f"view_{i:02d}.png", destination / f"view_{i:02d}.png"
                )
                for i in range(6)
            ]
            result["differentiable_mae"] = float(
                np.mean([item["mae"] for item in result["differentiable_views"]])
            )
            result["differentiable_seam_views"] = []
            for i in range(6):
                try:
                    result["differentiable_seam_views"].append(
                        image_metrics(
                            folder / "renders_high" / f"view_{i:02d}.png",
                            destination / f"view_{i:02d}.png",
                            folder / "mask_seam" / f"view_{i:02d}.png",
                        )
                    )
                except ValueError:
                    result["differentiable_seam_views"].append({"status": "NO_DIAGNOSTIC_PIXELS"})
            result["differentiable_mips"] = []
            for size in (256, 128):
                mip = folder / f"optimized_mip_{size}.png"
                Image.open(diff_folder / "BaseColor.png").resize((size, size), Image.Resampling.LANCZOS).save(
                    mip
                )
                destination = folder / f"optimized_mip_{size}"
                blender(
                    root,
                    runtime,
                    {
                        **common,
                        "views": TEST_VIEWS[:2],
                        "mode": "render_low",
                        "texture": str(mip),
                        "folder": str(destination),
                        "output": str(destination / "views.json"),
                    },
                    destination,
                    remaining(),
                )
                result["differentiable_mips"].append(
                    {
                        "size": size,
                        "views": [
                            image_metrics(
                                folder / "renders_high" / f"view_{i:02d}.png",
                                destination / f"view_{i:02d}.png",
                            )
                            for i in range(2)
                        ],
                    }
                )
        result["status"] = "SUCCESS"
    except (ValueError, TypeError, RuntimeError, TimeoutError, FileNotFoundError) as error:
        result.update(status="FAILED", reason=str(error))
    result["seconds"] = time.monotonic() - start
    result["geometric_correspondence_note"] = (
        "Cage and silhouette errors are not attributed exclusively to UV"
    )
    write_json(folder / "result.json", result)
    return result
