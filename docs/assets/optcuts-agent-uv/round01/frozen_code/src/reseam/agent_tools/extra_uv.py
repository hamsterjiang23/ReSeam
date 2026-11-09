"""Immutable auxiliary face-corner UVs and explicit GLB attribute preservation."""

import copy
import io
import json
import struct
from pathlib import Path
from typing import Any, cast

import numpy as np

from ..types import FloatArray, IntArray
from .state import file_hash


def write_extra_uv(metadata: dict[str, object], folder: Path, layers: dict[str, FloatArray]) -> None:
    """Freeze non-active layers in source order; names are not array keys."""
    if not layers:
        metadata.pop("extra_uv", None)
        return
    path = folder.resolve() / "extra_uv.npz"
    folder.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise FileExistsError(path)
    arrays: dict[str, FloatArray] = {}
    records: list[dict[str, object]] = []
    face_count: int | None = None
    for index, (name, corners) in enumerate(layers.items()):
        values = np.asarray(corners, dtype=np.float64)
        if values.ndim != 3 or values.shape[1:] != (3, 2) or not np.isfinite(values).all():
            raise ValueError("INVALID_EXTRA_UV_ARRAY")
        if face_count is not None and len(values) != face_count:
            raise ValueError("EXTRA_UV_FACE_COUNT_MISMATCH")
        face_count = len(values)
        key = f"layer_{index}"
        arrays[key] = values
        records.append({"name": name, "array": key, "texcoord": index + 1})
    np.savez_compressed(path, **arrays)  # pyright: ignore[reportArgumentType] -- array keys exclude allow_pickle
    metadata["extra_uv"] = {"path": str(path), "sha256": file_hash(path), "layers": records,
                            "face_count": face_count, "mapping": "snapshot face/corner order; UV bottom-left"}
    manifest = list(cast(list[dict[str, object]], metadata.get("source_manifest", [])))
    manifest.append({"path": str(path), "sha256": file_hash(path)})
    metadata["source_manifest"] = manifest


def load_extra_uv(metadata: dict[str, object], face_count: int) -> dict[str, FloatArray]:
    value = metadata.get("extra_uv")
    if value is None:
        return {}
    record = cast(dict[str, Any], value)
    path = Path(record["path"])
    if file_hash(path) != record["sha256"]:
        raise ValueError("EXTRA_UV_HASH_MISMATCH")
    if record["face_count"] != face_count:
        raise ValueError("EXTRA_UV_FACE_COUNT_MISMATCH")
    layers: dict[str, FloatArray] = {}
    with np.load(path, allow_pickle=False) as data:
        for layer in record["layers"]:
            corners = np.asarray(data[layer["array"]], dtype=float)
            if corners.shape != (face_count, 3, 2) or not np.isfinite(corners).all():
                raise ValueError("INVALID_EXTRA_UV_ARRAY")
            layers[layer["name"]] = corners.copy()
    return layers


def inherit_extra_uv(
    metadata: dict[str, object], folder: Path, source_faces: IntArray,
    corner_barycentric: FloatArray, source_face_count: int,
) -> dict[str, object]:
    """Interpolate from explicit source face/corner ancestry for a new variant."""
    result = copy.deepcopy(metadata)
    layers = load_extra_uv(metadata, source_face_count)
    if not layers:
        return result
    faces = np.asarray(source_faces)
    bary = np.asarray(corner_barycentric, dtype=float)
    if (faces.ndim != 1 or faces.dtype.kind not in "iu" or np.any(faces < 0)
            or np.any(faces >= source_face_count) or bary.shape != (len(faces), 3, 3)
            or not np.isfinite(bary).all() or np.any(bary < -1e-8)
            or not np.allclose(bary.sum(axis=-1), 1., atol=1e-8)):
        raise ValueError("INVALID_EXTRA_UV_ANCESTRY")
    write_extra_uv(result, folder, {name: np.einsum("fck,fkj->fcj", bary, uv[faces])
                                   for name, uv in layers.items()})
    return result


def read_glb(path: Path) -> tuple[dict[str, Any], bytes]:
    raw = path.read_bytes()
    if len(raw) < 28 or raw[:4] != b"glTF" or struct.unpack_from("<I", raw, 4)[0] != 2:
        raise ValueError("INVALID_GLB_HEADER")
    size, kind = struct.unpack_from("<II", raw, 12)
    if kind != 0x4E4F534A:
        raise ValueError("INVALID_GLB_JSON_CHUNK")
    document = json.loads(raw[20:20 + size])
    offset = 20 + size
    binary_size, binary_kind = struct.unpack_from("<II", raw, offset)
    if binary_kind != 0x004E4942:
        raise ValueError("INVALID_GLB_BINARY_CHUNK")
    return document, raw[offset + 8:offset + 8 + binary_size]


def encode_glb(document: dict[str, Any], binary: bytes) -> bytes:
    encoded = json.dumps(document, separators=(",", ":"), allow_nan=False).encode("utf-8")
    encoded += b" " * (-len(encoded) % 4)
    binary += b"\0" * (-len(binary) % 4)
    return (struct.pack("<III", 0x46546C67, 2, 28 + len(encoded) + len(binary))
            + struct.pack("<II", len(encoded), 0x4E4F534A) + encoded
            + struct.pack("<II", len(binary), 0x004E4942) + binary)


def glb_with_uv_attributes(path: Path, uv_channel: str | int | None = None) -> io.BytesIO:
    """Expose extra core UV attributes through trimesh's custom-attribute path.

    The installed glTF loader retains only TEXCOORD_0 and underscore attributes.
    Renaming a second copy preserves exact accessor/vertex ancestry through it.
    """
    document, binary = read_glb(path)
    selected = f"TEXCOORD_{uv_channel}" if isinstance(uv_channel, int) else uv_channel or "TEXCOORD_0"
    for mesh in document.get("meshes", []):
        for primitive in mesh.get("primitives", []):
            attributes = primitive["attributes"]
            original = {key: value for key, value in attributes.items() if key.startswith("TEXCOORD_")}
            if uv_channel is not None and selected not in original:
                raise ValueError("UV_CHANNEL_NOT_FOUND:" + selected)
            for key, value in original.items():
                if key != selected:
                    attributes["_RESEAM_" + key] = value
            if selected in original:
                attributes["TEXCOORD_0"] = original[selected]
                attributes["_RESEAM_PRIMARY_UV"] = original[selected]
    return io.BytesIO(encode_glb(document, binary))


def append_extra_uv_glb(path: Path, metadata: dict[str, object], face_count: int) -> dict[str, object]:
    """Append auxiliary UVs to the exporters' explicit-per-corner GLB mesh."""
    layers = load_extra_uv(metadata, face_count)
    if not layers:
        return {"extra_uv_layers": 0}
    document, binary = read_glb(path)
    primitives = [p for mesh in document["meshes"] for p in mesh["primitives"]]
    if len(primitives) != 1:
        raise ValueError("EXTRA_UV_EXPORT_REQUIRES_ONE_CORNER_PRIMITIVE")
    primitive = primitives[0]
    accessors = document["accessors"]
    if accessors[primitive["attributes"]["POSITION"]]["count"] != face_count * 3:
        raise ValueError("EXTRA_UV_EXPORT_CORNER_COUNT_MISMATCH")
    if "indices" in primitive:
        accessor = accessors[primitive["indices"]]
        view = document["bufferViews"][accessor["bufferView"]]
        dtype = {5121: "u1", 5123: "<u2", 5125: "<u4"}.get(accessor["componentType"])
        if dtype is None or view.get("byteStride") or accessor.get("sparse"):
            raise ValueError("EXTRA_UV_EXPORT_UNSUPPORTED_INDICES")
        indices = np.frombuffer(binary, dtype=dtype, count=accessor["count"],
                                offset=view.get("byteOffset", 0) + accessor.get("byteOffset", 0))
        if not np.array_equal(indices, np.arange(face_count * 3)):
            raise ValueError("EXTRA_UV_EXPORT_CORNER_IDENTITY_MISMATCH")
    buffer = bytearray(binary)
    names: dict[str, str] = {}
    for index, (name, uv) in enumerate(layers.items(), 1):
        coords = uv.reshape(-1, 2).copy()
        coords[:, 1] = 1. - coords[:, 1]
        raw = np.asarray(coords, dtype="<f4").tobytes()
        buffer.extend(b"\0" * (-len(buffer) % 4))
        document["bufferViews"].append({"buffer": 0, "byteOffset": len(buffer), "byteLength": len(raw),
                                       "target": 34962})
        buffer.extend(raw)
        accessors.append({"bufferView": len(document["bufferViews"]) - 1, "componentType": 5126,
                          "type": "VEC2", "count": face_count * 3})
        attribute = f"TEXCOORD_{index}"
        primitive["attributes"][attribute] = len(accessors) - 1
        names[attribute] = name
    document["buffers"][0]["byteLength"] = len(buffer)
    document.setdefault("extras", {})["reseam_extra_uv"] = names
    path.write_bytes(encode_glb(document, bytes(buffer)))
    return {"extra_uv_layers": len(layers), "extra_uv_mapping": names,
            "extra_uv_export": "source face/corner ancestry; float32 GLB attributes"}
