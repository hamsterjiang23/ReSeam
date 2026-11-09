"""Compose per-member bake images and validate every member in its exported tangent frame."""

import hashlib
import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image
from scipy.ndimage import distance_transform_edt

from .fixed_uv import face_areas
from .joint_types import JointProfile, V2State
from .joint_validation import uv_barycentric, validate_joint_geometry
from .mesh import sha256
from .pipeline import write_json
from .symmetry import sample_texture, srgb_to_linear


def state_fingerprint(state: V2State) -> str:
    digest = hashlib.sha256()
    for array in (
        state.surface.mesh.vertices,
        state.surface.mesh.faces,
        state.surface.uv,
        state.surface.uv_faces,
    ):
        digest.update(array.tobytes())
    digest.update(json.dumps([asdict(g) for g in state.groups], sort_keys=True).encode())
    return digest.hexdigest()


def material_slots(state: V2State) -> np.ndarray:
    slots = np.zeros(len(state.surface.mesh.faces), dtype=np.int64)
    for i, g in enumerate(state.groups):
        slots[list(g.first_faces)] = 2 * i + 1
        slots[list(g.second_faces)] = 2 * i + 2
    return slots


def face_pixels(triangle: np.ndarray, size: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    pixels = triangle * size
    low = np.maximum(0, np.ceil(pixels.min(axis=0) - 0.5).astype(int))
    high = np.minimum(size - 1, np.floor(pixels.max(axis=0) - 0.5).astype(int))
    if np.any(low > high):
        return np.empty(0, dtype=int), np.empty(0, dtype=int), np.empty((0, 3))
    x, y = np.meshgrid(np.arange(low[0], high[0] + 1), np.arange(low[1], high[1] + 1))
    uv = np.column_stack(((x.ravel() + 0.5) / size, (y.ravel() + 0.5) / size))
    bary = uv_barycentric(triangle, uv)
    inside = np.all(bary >= -1e-10, axis=1)
    return x.ravel()[inside], size - 1 - y.ravel()[inside], bary[inside]


def tbn(frames: list[dict[str, Any]], bary: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    normals = bary @ np.asarray([f["normal"] for f in frames])
    tangents = bary @ np.asarray([f["tangent"] for f in frames])
    signs = bary @ np.asarray([f["sign"] for f in frames])
    nl = np.linalg.norm(normals, axis=1)
    normals /= np.maximum(nl[:, None], 1e-15)
    tangents -= np.sum(tangents * normals, axis=1)[:, None] * normals
    tl = np.linalg.norm(tangents, axis=1)
    tangents /= np.maximum(tl[:, None], 1e-15)
    bits = np.cross(normals, tangents) * np.sign(signs)[:, None]
    valid = (nl > 1e-8) & (tl > 1e-8) & (np.abs(signs) > 0.99)
    return np.stack((tangents, bits, normals), axis=2), valid


def linear_to_srgb(image: np.ndarray) -> np.ndarray:
    return np.where(image <= 0.0031308, 12.92 * image, 1.055 * np.maximum(image, 0) ** (1 / 2.4) - 0.055)


def compose_material(
    state: V2State, bake_folder: Path, output: Path, profile: JointProfile
) -> dict[str, Any]:
    output.mkdir(parents=True, exist_ok=False)
    geometry = validate_joint_geometry(state, profile)
    if not geometry["valid"]:
        raise ValueError("Cannot bake unauthorized UV layout")
    info = json.loads((bake_folder / "result.json").read_text())
    surface = state.surface
    size = profile.resolution
    slots = material_slots(state)
    colors = []
    normals = []
    for slot in range(int(slots.max()) + 1):
        colors.append(
            np.asarray(
                Image.open(bake_folder / f"BaseColor_{slot:03d}.png").convert("RGBA"), dtype=np.float32
            )
            / 255
        )
        normals.append(
            np.asarray(Image.open(bake_folder / f"Normal_{slot:03d}.png").convert("RGBA"), dtype=np.float32)
            / 255
        )
        if colors[-1].shape != (size, size, 4) or normals[-1].shape != (size, size, 4):
            raise ValueError("Bake resolution mismatch")
    sums = np.zeros((size, size, 3))
    linear_colors = [srgb_to_linear(color[:, :, :3]) for color in colors]
    normal_sums = np.zeros_like(sums)
    weights = np.zeros((size, size))
    seen = [np.zeros((size, size), dtype=bool) for _ in colors]
    area = np.abs(face_areas(surface))
    for fi, face in enumerate(surface.uv_faces):
        x, y, _ = face_pixels(surface.uv[face], size)
        slot = int(slots[fi])
        keep = ~seen[slot][y, x]
        x, y = x[keep], y[keep]
        seen[slot][y, x] = True
        weight = float(surface.mesh.areas[fi] / area[fi])
        sums[y, x] += linear_colors[slot][y, x] * weight
        n = normals[slot][y, x, :3] * 2 - 1
        n /= np.maximum(np.linalg.norm(n, axis=1)[:, None], 1e-15)
        normal_sums[y, x] += n * weight
        weights[y, x] += weight
    coverage = weights > 0
    composed = sums / np.maximum(weights[..., None], 1e-15)
    normal_sums /= np.maximum(np.linalg.norm(normal_sums, axis=2)[..., None], 1e-15)
    # Sampling the delivered, quantized PNG is part of the error check, not an unquantized surrogate.
    base = np.rint(np.clip(linear_to_srgb(composed), 0, 1) * 255).astype(np.uint8)
    normal = np.rint(np.clip(normal_sums * 0.5 + 0.5, 0, 1) * 255).astype(np.uint8)
    padding_result = distance_transform_edt(~coverage, return_indices=True)
    if not isinstance(padding_result, tuple):
        raise TypeError("Padding distance transform omitted indices")
    distances, indices = padding_result
    padding = (~coverage) & (distances <= 8)
    for image in (base, normal):
        image[padding] = image[indices[0][padding], indices[1][padding]]
    Image.fromarray(base).save(output / "BaseColor.png")
    Image.fromarray(normal).save(output / "Normal.png")
    base_float = base.astype(float) / 255
    base_linear = srgb_to_linear(base_float)
    normal_float = normal.astype(float) / 255
    records = []
    failures = []
    projection_failures = []
    supplemental = np.asarray(
        [
            (
                1 - np.sqrt((i + 0.5) / 8),
                np.sqrt((i + 0.5) / 8) * (1 - (j + 0.5) / 8),
                np.sqrt((i + 0.5) / 8) * (j + 0.5) / 8,
            )
            for i in range(8)
            for j in range(8)
        ]
    )
    for fi, face in enumerate(surface.uv_faces):
        x, y, bary = face_pixels(surface.uv[face], size)
        if len(x):
            uv = np.column_stack(((x + 0.5) / size, 1 - (y + 0.5) / size))
        else:
            bary = supplemental
            uv = bary @ surface.uv[face]
        slot = int(slots[fi])
        ref_color = sample_texture(colors[slot], uv)
        ref_normal = sample_texture(normals[slot], uv)
        actual_color = sample_texture(base_linear, uv)
        expected_color = sample_texture(linear_colors[slot], uv)
        actual_normal = sample_texture(normal_float, uv) * 2 - 1
        expected_normal = ref_normal[:, :3] * 2 - 1
        matrix, frame_valid = tbn(info["corner_frames"][fi], bary)
        actual = np.einsum("nij,nj->ni", matrix, actual_normal)
        expected = np.einsum("nij,nj->ni", matrix, expected_normal)
        al, el = np.linalg.norm(actual, axis=1), np.linalg.norm(expected, axis=1)
        angle = np.degrees(
            np.arccos(np.clip(np.sum(actual * expected, axis=1) / np.maximum(al * el, 1e-15), -1, 1))
        )
        delta = np.abs(actual_color - expected_color)
        valid_source = bool(
            np.all(ref_color[:, 3] > 0.99)
            & np.all(ref_normal[:, 3] > 0.99)
            & np.all(frame_valid)
            & np.all(al > 1e-8)
            & np.all(el > 1e-8)
        )
        metrics = {
            "face": fi,
            "samples": len(uv),
            "valid_source": valid_source,
            "color_mean": float(delta.mean()),
            "color_p95": float(np.quantile(delta, 0.95)),
            "color_max": float(delta.max()),
            "normal_mean_degrees": float(angle.mean()),
            "normal_p95_degrees": float(np.quantile(angle, 0.95)),
            "normal_max_degrees": float(angle.max()),
            "shared_member": bool(slots[fi] != 0),
        }
        passed = (
            valid_source
            and metrics["color_mean"] <= 0.025
            and metrics["color_p95"] <= 0.08
            and metrics["color_max"] <= 0.20
            and metrics["normal_mean_degrees"] <= 5
            and metrics["normal_p95_degrees"] <= 15
            and metrics["normal_max_degrees"] <= 30
        )
        metrics["passed"] = passed
        records.append(metrics)
        if not valid_source:
            projection_failures.append(fi)
        if slots[fi] != 0 and not passed:
            failures.append(fi)
    result = {
        "valid": not failures,
        "failed_faces": failures,
        "projection_or_frame_invalid_faces": projection_failures,
        "faces": records,
        "state_fingerprint": state_fingerprint(state),
        "base_sha256": sha256(output / "BaseColor.png"),
        "normal_sha256": sha256(output / "Normal.png"),
        "bake_manifest_sha256": sha256(bake_folder / "result.json"),
        "definition": "every member decoded with its exported TBN; shared texture composed before error measurement",
    }
    write_json(output / "material.json", result)
    return result
