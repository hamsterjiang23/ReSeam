"""Validate actual GLB tangent attributes against frames used for member baking."""

import json
import struct
from pathlib import Path
from typing import Any

import numpy as np
from scipy.spatial import KDTree

from .joint_types import V2State


def verify_export_frames(asset: Path, state: V2State, frames_path: Path) -> dict[str, Any]:
    raw = asset.read_bytes()
    if raw[:4] != b"glTF" or struct.unpack_from("<I", raw, 4)[0] != 2:
        raise ValueError("Unsupported GLB")
    json_size = struct.unpack_from("<I", raw, 12)[0]
    document = json.loads(raw[20 : 20 + json_size])
    binary_start = 20 + json_size + 8
    binary = raw[binary_start:]

    def accessor(index: int) -> np.ndarray:
        item = document["accessors"][index]
        view = document["bufferViews"][item["bufferView"]]
        dtypes = {5126: "<f4", 5125: "<u4", 5123: "<u2", 5121: "u1"}
        sizes = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4}
        dtype = np.dtype(dtypes[item["componentType"]])
        width = sizes[item["type"]]
        offset = view.get("byteOffset", 0) + item.get("byteOffset", 0)
        stride = view.get("byteStride", dtype.itemsize * width)
        return np.ndarray(
            (item["count"], width),
            dtype=dtype,
            buffer=binary,
            offset=offset,
            strides=(stride, dtype.itemsize),
        ).copy()

    frames = json.loads(frames_path.read_text())["corner_frames"]
    tree = KDTree(state.surface.mesh.vertices)
    lookup = {tuple(sorted(map(int, face))): i for i, face in enumerate(state.surface.mesh.faces)}
    visited = set()
    tangent_error = normal_error = 0.0
    signs = []
    for mesh in document["meshes"]:
        for primitive in mesh["primitives"]:
            attrs = primitive["attributes"]
            if not {"POSITION", "NORMAL", "TANGENT", "TEXCOORD_0"} <= set(attrs):
                raise ValueError("GLB_MISSING_TANGENT_ATTRIBUTES")
            positions, normals, tangents = (accessor(attrs[k]) for k in ("POSITION", "NORMAL", "TANGENT"))
            distance, ids = tree.query(positions)
            ids = np.asarray(ids, dtype=np.int64)
            if np.max(distance) > state.surface.mesh.scale * 1e-6:
                raise ValueError("GLB_FRAME_GEOMETRY_CHANGED")
            triangles = accessor(primitive["indices"]).reshape(-1, 3)
            for face in triangles:
                mapped = ids[face]
                fi = lookup.get(tuple(sorted(map(int, mapped))))
                if fi is None or fi in visited:
                    raise ValueError("GLB_FRAME_FACE_COVERAGE")
                visited.add(fi)
                for corner, original in enumerate(state.surface.mesh.faces[fi]):
                    vertex = int(face[np.flatnonzero(mapped == original)[0]])
                    expected = frames[fi][corner]
                    tangent_error = max(
                        tangent_error, float(np.linalg.norm(tangents[vertex, :3] - expected["tangent"]))
                    )
                    normal_error = max(
                        normal_error, float(np.linalg.norm(normals[vertex] - expected["normal"]))
                    )
                    if abs(float(tangents[vertex, 3]) - expected["sign"]) > 1e-6:
                        raise ValueError("GLB_TANGENT_HANDEDNESS_CHANGED")
                    signs.append(float(tangents[vertex, 3]))
    if len(visited) != len(state.surface.mesh.faces) or max(tangent_error, normal_error) > 1e-4:
        raise ValueError("GLB_TANGENT_FRAME_CHANGED")
    return {
        "valid": True,
        "faces": len(visited),
        "max_tangent_error": tangent_error,
        "max_normal_error": normal_error,
        "negative_handedness_corners": sum(s < 0 for s in signs),
    }
