"""Isolated Blender baking for independent, non-overlapping topology variants.

This adapter measures emitted image bytes and four geometric probes per face.
It does not certify exact Cycles ray coverage, appearance quality, mip safety,
GPU behavior, or artist acceptance.
"""

import hashlib
import json
import math
import os
import struct
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image
from shapely import Polygon, STRtree

from .types import FloatArray, IntArray

DEFAULT_BLENDER = Path("E:/Blender 4.5.11/blender.exe")


@dataclass(frozen=True)
class BakeVariantRequest:
    vertices: FloatArray
    faces: IntArray
    corner_uv: FloatArray
    high_path: Path
    resolution: int = 256
    cage_extrusion: float = .01
    margin_pixels: int = 4


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def _write(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def _mapping(value: object, label: str) -> dict[str, object]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise ValueError("INVALID_MAPPING:" + label)
    return value


def read_source_materials(path: Path) -> dict[str, object]:
    """Read the embedded GLB JSON; external resources and extended PBR fail closed."""
    with path.open("rb") as stream:
        header = stream.read(12)
        if len(header) != 12:
            raise ValueError("INVALID_SOURCE_GLB_HEADER")
        magic, version, size = struct.unpack("<4sII", header)
        if magic != b"glTF" or version != 2 or size != path.stat().st_size:
            raise ValueError("INVALID_SOURCE_GLB_HEADER")
        chunk = stream.read(8)
        if len(chunk) != 8:
            raise ValueError("MISSING_SOURCE_GLB_JSON")
        count, kind = struct.unpack("<I4s", chunk)
        if kind != b"JSON" or count > 64 * 1024 * 1024 or count + 20 > size:
            raise ValueError("INVALID_SOURCE_GLB_JSON")
        document = _mapping(json.loads(stream.read(count).decode("utf-8")), "source")
    for name in ("buffers", "images"):
        resources = document.get(name, [])
        if not isinstance(resources, list):
            raise TypeError("INVALID_SOURCE_RESOURCES")
        if any("uri" in _mapping(resource, name) for resource in resources):
            raise ValueError("EXTERNAL_SOURCE_RESOURCES_UNSUPPORTED")
    materials = document.get("materials")
    if not isinstance(materials, list) or not materials:
        raise ValueError("SOURCE_MATERIALS_REQUIRED")
    declarations: list[dict[str, object]] = []
    for index, value in enumerate(materials):
        material = _mapping(value, "material")
        alpha = material.get("alphaMode", "OPAQUE")
        if alpha != "OPAQUE":
            raise ValueError("UNSUPPORTED_SOURCE_ALPHA_MODE:" + str(alpha))
        if material.get("extensions"):
            raise ValueError("EXTENDED_SOURCE_PBR_UNSUPPORTED")
        pbr = _mapping(material.get("pbrMetallicRoughness", {}), "pbrMetallicRoughness")
        name = material.get("name", f"Material_{index}")
        if not isinstance(name, str) or not name:
            raise ValueError("INVALID_SOURCE_MATERIAL_NAME")
        declarations.append({"index": index, "name": name, "alpha_mode": alpha,
            "alpha_mode_declared": "alphaMode" in material,
            "base_color_texture": "baseColorTexture" in pbr,
            "normal_texture": "normalTexture" in material,
            "metallic_roughness_texture": "metallicRoughnessTexture" in pbr,
            "occlusion_texture": "occlusionTexture" in material,
            "source_definition": material})
    if len({item["name"] for item in declarations}) != len(declarations):
        raise ValueError("AMBIGUOUS_SOURCE_MATERIAL_NAMES")
    return {"materials": declarations, "alpha_contract": "OPAQUE_ONLY",
            "omitted_alpha_mode": "glTF2 OPAQUE default", "source": str(path.resolve())}


def _validate_request(request: BakeVariantRequest) -> None:
    vertices, faces, uv = request.vertices, request.faces, request.corner_uv
    if (vertices.ndim != 2 or vertices.shape[1:] != (3,) or len(vertices) < 3
            or faces.ndim != 2 or faces.shape[1:] != (3,) or not len(faces)
            or not np.issubdtype(faces.dtype, np.integer)
            or uv.shape != (len(faces), 3, 2)
            or not np.isfinite(vertices).all() or not np.isfinite(uv).all()
            or faces.min() < 0 or faces.max() >= len(vertices)):
        raise ValueError("INVALID_VARIANT_GEOMETRY_OR_UV")
    triangles = vertices[faces]
    if np.any(np.linalg.norm(np.cross(triangles[:, 1] - triangles[:, 0],
                                     triangles[:, 2] - triangles[:, 0]), axis=1) <= 0):
        raise ValueError("DEGENERATE_VARIANT_GEOMETRY")
    if np.any(uv < 0) or np.any(uv > 1):
        raise ValueError("VARIANT_UV_OUTSIDE_ATLAS")
    polygons = [Polygon(corners) for corners in uv]
    if any(not polygon.is_valid or polygon.area <= 0 for polygon in polygons):
        raise ValueError("DEGENERATE_VARIANT_UV")
    tree = STRtree(polygons)
    for index, polygon in enumerate(polygons):
        if any(index < int(other) and polygon.intersection(polygons[int(other)]).area > 0
               for other in tree.query(polygon)):
            raise ValueError("SHARED_VARIANT_UV_UNSUPPORTED")
    if (type(request.resolution) is not int or not 4 <= request.resolution <= 8192
            or type(request.margin_pixels) is not int or request.margin_pixels < 0
            or request.margin_pixels * 2 >= request.resolution
            or not math.isfinite(request.cage_extrusion) or request.cage_extrusion <= 0):
        raise ValueError("INVALID_VARIANT_BAKE_SETTINGS")


def _inputs_hashes(request: BakeVariantRequest, worker: Path, serialized: Path) -> dict[str, str]:
    arrays = hashlib.sha256()
    for array in (request.vertices, request.faces, request.corner_uv):
        arrays.update(str((array.shape, array.dtype.str)).encode("ascii"))
        arrays.update(array.tobytes())
    return {"high_glb": _sha256(request.high_path), "worker": _sha256(worker),
            "request": _sha256(serialized), "geometry_arrays": arrays.hexdigest()}


def _channel_files(report: dict[str, object], output: Path, resolution: int) -> dict[str, object]:
    channels = _mapping(report.get("channels"), "channels")
    if set(channels) != {"BaseColor", "Normal", "ORM"}:
        raise ValueError("INCOMPLETE_THREE_CHANNEL_BAKE")
    measured: dict[str, object] = {}
    for name, value in channels.items():
        if not isinstance(value, str):
            raise TypeError("INVALID_BAKE_CHANNEL_PATH")
        path = Path(value).resolve()
        if path.parent != output.resolve() or path.name != name + ".png":
            raise ValueError("BAKE_CHANNEL_ESCAPES_OUTPUT")
        with Image.open(path) as image:
            if image.size != (resolution, resolution) or image.mode != "RGBA" or image.format != "PNG":
                raise ValueError("INVALID_BAKE_IMAGE_LAYOUT:" + name)
            pixels = np.asarray(image)
            if pixels.dtype != np.uint8 or pixels.shape != (resolution, resolution, 4):
                raise ValueError("INVALID_BAKE_IMAGE_LAYOUT:" + name)
            measured[name] = {"path": str(path), "sha256": _sha256(path), "bytes": path.stat().st_size,
                "minimum_rgba8": pixels.min(axis=(0, 1)).tolist(),
                "maximum_rgba8": pixels.max(axis=(0, 1)).tolist(),
                "mean_rgba8": pixels.mean(axis=(0, 1)).tolist(),
                "alpha_nonzero_pixels": int(np.count_nonzero(pixels[..., 3])),
                "alpha_scope": "image alpha only; not a source projection coverage certificate"}
    return measured


def _validate_outputs(report: dict[str, object], request: BakeVariantRequest, output: Path) -> dict[str, object]:
    if report.get("status") != "BAKED_THREE_CHANNELS":
        raise ValueError("WORKER_DID_NOT_COMPLETE:" + str(report.get("error", report.get("status"))))
    materials = report.get("source_materials")
    if not isinstance(materials, list) or not materials:
        raise ValueError("MISSING_SOURCE_MATERIAL_NODE_EVIDENCE")
    for value in materials:
        material = _mapping(value, "source_material")
        if material.get("occlusion_texture") is True and material.get("occlusion_socket_linked") is not True:
            raise ValueError("UNSUPPORTED_SOURCE_AO")
    measured = _channel_files(report, output, request.resolution)
    mesh_path = output / "mesh_frames.json"
    mesh = _mapping(json.loads(mesh_path.read_text(encoding="utf-8")), "mesh_frames")
    vertices = np.asarray(mesh.get("vertices"), dtype=float)
    faces = np.asarray(mesh.get("faces"), dtype=np.int64)
    uv = np.asarray(mesh.get("corner_uv"), dtype=float)
    normals = np.asarray(mesh.get("corner_normals_gltf"), dtype=float)
    tangents = np.asarray(mesh.get("corner_tangents_gltf"), dtype=float)
    scale = max(float(np.linalg.norm(np.ptp(request.vertices, axis=0))), 1e-12)
    if (vertices.shape != request.vertices.shape or not np.allclose(vertices, request.vertices, rtol=1e-6,
            atol=scale * 1e-7) or not np.array_equal(faces, request.faces)
            or uv.shape != request.corner_uv.shape or not np.allclose(uv, request.corner_uv, atol=1e-7)
            or normals.shape != (len(faces), 3, 3) or tangents.shape != (len(faces), 3, 4)
            or not np.isfinite(normals).all() or not np.isfinite(tangents).all()
            or not np.allclose(np.linalg.norm(normals, axis=2), 1, atol=1e-3)
            or not np.allclose(np.linalg.norm(tangents[..., :3], axis=2), 1, atol=1e-3)
            or not np.allclose(np.sum(normals * tangents[..., :3], axis=2), 0, atol=1e-3)
            or not np.allclose(np.abs(tangents[..., 3]), 1, atol=1e-6)):
        raise ValueError("VARIANT_GEOMETRY_UV_OR_FRAME_MISMATCH")
    for name in ("asset.glb", "preview.blend"):
        if not (output / name).is_file() or (output / name).stat().st_size == 0:
            raise ValueError("MISSING_VARIANT_PREVIEW:" + name)
    return measured


def bake_variant(
    request: BakeVariantRequest, output: Path, *, deadline: float,
    blender: Path = DEFAULT_BLENDER, worker: Path | None = None,
) -> dict[str, object]:
    """Run one CPU child, kill and reap on timeout, and preserve all diagnostics."""
    _validate_request(request)
    if not math.isfinite(deadline) or time.monotonic() >= deadline:
        raise TimeoutError("VARIANT_BAKE_DEADLINE_EXHAUSTED")
    worker = worker or Path(__file__).resolve().parents[2] / "scripts/bake_variant_worker.py"
    if not blender.is_file() or not worker.is_file():
        raise FileNotFoundError("BLENDER_OR_VARIANT_WORKER_MISSING")
    source = read_source_materials(request.high_path)
    output.mkdir(parents=True, exist_ok=False)
    serialized = output / "request.json"
    payload = {"vertices": request.vertices.tolist(), "faces": request.faces.tolist(),
        "corner_uv": request.corner_uv.tolist(), "high": str(request.high_path.resolve()),
        "resolution": request.resolution, "cage_extrusion": request.cage_extrusion,
        "margin_pixels": request.margin_pixels, "source": source, "folder": str(output.resolve()),
        "output": str((output / "worker_result.json").resolve())}
    _write(serialized, payload)
    before = _inputs_hashes(request, worker, serialized)
    _write(output / "input_hashes_before.json", before)
    command = [str(blender), "--background", "--factory-startup", "--threads", "1",
        "--python-exit-code", "1", "--python", str(worker.resolve()), "--", str(serialized.resolve())]
    _write(output / "command.json", command)
    environment = os.environ.copy()
    environment.update({"OMP_NUM_THREADS": "1", "OPENBLAS_NUM_THREADS": "1", "MKL_NUM_THREADS": "1",
        "CUDA_VISIBLE_DEVICES": "", "HIP_VISIBLE_DEVICES": "", "PYTHONUNBUFFERED": "1"})
    started = time.monotonic()
    failure: str | None = None
    try:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("VARIANT_BAKE_PREFLIGHT_EXHAUSTED_DEADLINE")
        with (output / "worker.log").open("w", encoding="utf-8") as log:
            process = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT, env=environment)
            try:
                returncode = process.wait(timeout=remaining)
            except subprocess.TimeoutExpired as error:
                process.kill()
                process.wait()
                raise TimeoutError("VARIANT_BAKE_CHILD_KILLED_AND_REAPED") from error
        if returncode:
            raise RuntimeError(f"VARIANT_BAKE_PROCESS_FAILED:{returncode}")
        if time.monotonic() > deadline:
            raise TimeoutError("VARIANT_BAKE_PROCESS_COMPLETED_AFTER_DEADLINE")
        report = _mapping(json.loads((output / "worker_result.json").read_text(encoding="utf-8")), "worker")
        measurements = _validate_outputs(report, request, output)
        if time.monotonic() > deadline:
            raise TimeoutError("VARIANT_BAKE_OUTPUT_VALIDATION_EXCEEDED_DEADLINE")
        result: dict[str, object] = {"status": "BAKED_THREE_CHANNELS", "worker": report,
            "channels": measurements, "source": source, "input_hashes": before,
            "wall_seconds": time.monotonic() - started,
            "claims": {"numeric_images_verified": True, "exact_bake_coverage_verified": False,
                "appearance_quality_verified": False, "mip_safety_verified": False,
                "gpu_verified": False, "artist_reviewed": False}}
        _write(output / "record.json", result)
        return result
    except (OSError, ValueError, RuntimeError, TimeoutError) as error:
        failure = str(error)
        _write(output / "failure.json", {"status": "BAKE_FAILED", "reason": failure})
        raise
    finally:
        after = _inputs_hashes(request, worker, serialized)
        _write(output / "input_hashes_after.json", after)
        if after != before:
            _write(output / "failure.json", {"status": "INPUT_MUTATED", "prior_failure": failure,
                                             "before": before, "after": after})
            raise ValueError("VARIANT_BAKE_INPUT_MUTATED")
