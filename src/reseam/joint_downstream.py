"""Prepacked V2 bake/render route: never repack or let shared faces race on the same target image."""

import shutil
import time
from pathlib import Path
from typing import Any

import numpy as np
import trimesh
from PIL import Image, ImageDraw
from trimesh.visual.texture import TextureVisuals

from .downstream import blender, image_metrics, scene_request
from .joint_export import verify_export_frames
from .joint_material import compose_material, linear_to_srgb, material_slots
from .joint_state import material_errors
from .joint_types import JointProfile, V2State
from .mesh import import_uv_arrays, sha256
from .pipeline import write_json
from .runtime import read_runtime, run_process
from .symmetry import srgb_to_linear


def joint_worker(root: Path, request: dict[str, Any], folder: Path, seconds: float) -> None:
    folder.mkdir(parents=True, exist_ok=False)
    write_json(folder / "request.json", request)
    runtime = read_runtime(root)
    run_process(
        [
            runtime["blender"],
            "-b",
            "--factory-startup",
            "--threads",
            "4",
            "--python-exit-code",
            "1",
            "--python",
            str(root / "scripts/joint_blender_worker.py"),
            "--",
            str(folder / "request.json"),
        ],
        folder,
        seconds,
    )


def bake_joint(
    root: Path, model: str, state: V2State, folder: Path, seconds: float, profile: JointProfile
) -> dict[str, Any]:
    start = time.monotonic()
    high = root / "assets/v1" / model / "high.glb"
    request = {
        **scene_request(state.surface, high),
        "mode": "bake_members",
        "resolution": profile.resolution,
        "face_slots": material_slots(state).tolist(),
        "folder": str(folder),
        "output": str(folder / "result.json"),
    }
    joint_worker(root, request, folder, max(0.1, seconds - 4))
    if time.monotonic() - start >= seconds:
        return {"valid": False, "reason": "MATERIAL_BUDGET_EXHAUSTED"}
    result = compose_material(state, folder, folder / "composed", profile)
    result["high_sha256"] = sha256(high)
    result["wall_seconds"] = time.monotonic() - start
    result["budget_overrun"] = result["wall_seconds"] > seconds
    if result["budget_overrun"]:
        result["valid"] = False
    write_json(folder / "composed/material.json", result)
    if result["valid"]:
        state.material_evidence = {g.group_id: str(folder / "composed") for g in state.groups}
    return result


def reference_views(root: Path, model: str, state: V2State, folder: Path, seconds: float) -> None:
    start = time.monotonic()
    folder.mkdir(parents=True, exist_ok=False)
    common = scene_request(state.surface, root / "assets/v1" / model / "high.glb")
    runtime = read_runtime(root)
    for label, normal in (("base", False), ("normal", True)):
        dest = folder / label
        blender(
            root,
            runtime,
            {
                **common,
                "mode": "render_high",
                "normal_preview": normal,
                "folder": str(dest),
                "output": str(dest / "views.json"),
            },
            dest,
            seconds - (time.monotonic() - start),
        )


def evaluate_joint(
    root: Path,
    model: str,
    state: V2State,
    folder: Path,
    reference: Path,
    seconds: float,
    profile: JointProfile,
) -> dict[str, Any]:
    started = time.monotonic()
    deadline = started + seconds
    folder.mkdir(parents=True, exist_ok=False)
    result: dict[str, Any] = {"status": "RUNNING", "repacked": False, "human_acceptance": "NOT_REVIEWED"}
    try:
        if state.groups and not material_errors(state):
            material = folder / "material"
            material.mkdir()
            source = Path(next(iter(state.material_evidence.values())))
            frames_path = source.parent / "result.json"
            for name in ("BaseColor.png", "Normal.png", "material.json"):
                shutil.copy2(source / name, material / name)
        else:
            data = bake_joint(
                root, model, state, folder / "bake", min(120, deadline - time.monotonic() - 10), profile
            )
            result["material"] = data
            if not data["valid"]:
                raise ValueError("MATERIAL_QUALITY_FAILED")
            material = folder / "bake/composed"
            frames_path = folder / "bake/result.json"
        base, normal = material / "BaseColor.png", material / "Normal.png"
        high = root / "assets/v1" / model / "high.glb"
        common = scene_request(state.surface, high)
        runtime = read_runtime(root)
        destination = folder / "export"
        joint_worker(
            root,
            {
                **common,
                "mode": "export_shared",
                "basecolor": str(base),
                "normal": str(normal),
                "asset": str(folder / "asset.glb"),
                "output": str(destination / "result.json"),
            },
            destination,
            deadline - time.monotonic() - 5,
        )
        loaded = trimesh.load(folder / "asset.glb", force="mesh", process=False)
        if not isinstance(loaded, trimesh.Trimesh) or not isinstance(loaded.visual, TextureVisuals):
            raise TypeError("GLB_EXPORT_MISSING_UV")
        recovered = import_uv_arrays(
            state.surface.mesh,
            np.asarray(loaded.vertices),
            np.asarray(loaded.faces),
            np.asarray(loaded.visual.uv),
            np.asarray(loaded.faces),
        )
        if not np.allclose(
            recovered.uv[recovered.uv_faces], state.surface.uv[state.surface.uv_faces], atol=1e-6, rtol=0
        ):
            raise ValueError("GLB_CORNER_UV_MISMATCH")
        result["export_frames"] = verify_export_frames(folder / "asset.glb", state, frames_path)
        for label, tex, norm, npreview in [("base", base, None, False), ("normal", base, normal, True)]:
            dest = folder / label
            blender(
                root,
                runtime,
                {
                    **common,
                    "mode": "render_low",
                    "texture": str(tex),
                    "normal": str(norm) if norm else None,
                    "normal_preview": npreview,
                    "folder": str(dest),
                    "output": str(dest / "views.json"),
                },
                dest,
                deadline - time.monotonic() - 5,
            )
        for size in (256, 128):
            texture = folder / f"mip_{size}.png"
            linear = srgb_to_linear(np.asarray(Image.open(base).convert("RGB"), dtype=float) / 255)
            resized = np.stack(
                [
                    np.asarray(
                        Image.fromarray(linear[:, :, c].astype(np.float32)).resize(
                            (size, size), Image.Resampling.BOX
                        )
                    )
                    for c in range(3)
                ],
                axis=2,
            )
            Image.fromarray(np.rint(np.clip(linear_to_srgb(resized), 0, 1) * 255).astype(np.uint8)).save(
                texture
            )
            dest = folder / f"mip_{size}"
            blender(
                root,
                runtime,
                {
                    **common,
                    "mode": "render_low",
                    "texture": str(texture),
                    "folder": str(dest),
                    "output": str(dest / "views.json"),
                },
                dest,
                deadline - time.monotonic() - 5,
            )
        mask = Image.new("RGB", (1024, 1024))
        draw = ImageDraw.Draw(mask)
        for edge in state.surface.seams:
            for fi, corner in state.surface.mesh.edge_faces[edge]:
                uv = state.surface.uv[state.surface.uv_faces[fi]]
                points = [
                    (float(uv[c, 0] * 1024), float((1 - uv[c, 1]) * 1024)) for c in (corner, (corner + 1) % 3)
                ]
                draw.line(points, fill="white", width=6)
        mask.save(folder / "seam_mask.png")
        dest = folder / "seam_mask"
        blender(
            root,
            runtime,
            {
                **common,
                "mode": "render_low",
                "texture": str(folder / "seam_mask.png"),
                "folder": str(dest),
                "output": str(dest / "views.json"),
            },
            dest,
            deadline - time.monotonic() - 3,
        )
        result["metrics"] = {
            label: [
                image_metrics(
                    reference / ("normal" if label == "normal" else "base") / f"view_{i:02d}.png",
                    folder / label / f"view_{i:02d}.png",
                )
                for i in range(6)
            ]
            for label in ("base", "normal", "mip_256", "mip_128")
        }
        result["status"] = "SUCCESS"
    except (TypeError, ValueError, RuntimeError, TimeoutError, OSError) as error:
        result.update(status="FAILED", reason=str(error))
    result["wall_seconds"] = time.monotonic() - started
    result["budget_overrun"] = result["wall_seconds"] > seconds
    if result["budget_overrun"]:
        result["status"] = "BUDGET_EXCEEDED"
    write_json(folder / "result.json", result)
    return result
