"""Transport an actual same-surface PBR reference into an explicit mirror variant."""

import hashlib
import json
import struct
from dataclasses import replace
from pathlib import Path
from typing import cast

import numpy as np

from ..budget_material import interpolated_frames
from ..types import FloatArray, MeshState
from .material import _reference, _source_document
from .material_identity import face_corner_identity
from .state import Snapshot, check_deadline, file_hash
from .symmetry_variant import SymmetryVariant


def _image_hashes(document: dict[str, object], binary: bytes) -> list[str]:
    result: list[str] = []
    views = cast(list[dict[str, object]], document["bufferViews"])
    for image in cast(list[dict[str, object]], document.get("images", [])):
        if "bufferView" not in image or "uri" in image:
            raise ValueError("PBR_MIRROR_EMBEDDED_IMAGES_REQUIRED")
        view = views[int(cast(int, image["bufferView"]))]
        offset, size = int(cast(int, view.get("byteOffset", 0))), int(cast(int, view["byteLength"]))
        result.append(hashlib.sha256(binary[offset:offset + size]).hexdigest())
    return result


def _append_attribute(document: dict[str, object], binary: bytearray, values: FloatArray) -> int:
    binary.extend(b"\0" * ((-len(binary)) % 4))
    data = np.asarray(values, dtype="<f4")
    views = cast(list[dict[str, object]], document["bufferViews"])
    accessors = cast(list[dict[str, object]], document["accessors"])
    views.append({"buffer": 0, "byteOffset": len(binary), "byteLength": data.nbytes, "target": 34962})
    accessors.append({"bufferView": len(views) - 1, "componentType": 5126,
                      "count": len(data), "type": f"VEC{data.shape[1]}",
                      "min": data.min(axis=0).tolist(), "max": data.max(axis=0).tolist()})
    binary.extend(data.tobytes())
    return len(accessors) - 1


def _write_glb(path: Path, document: dict[str, object], binary: bytearray) -> None:
    document["buffers"] = [{"byteLength": len(binary)}]
    encoded = json.dumps(document, separators=(",", ":"), allow_nan=False).encode("utf-8")
    encoded += b" " * ((-len(encoded)) % 4)
    binary.extend(b"\0" * ((-len(binary)) % 4))
    path.write_bytes(b"glTF" + struct.pack("<II", 2, 28 + len(encoded) + len(binary))
                     + struct.pack("<I4s", len(encoded), b"JSON") + encoded
                     + struct.pack("<I4s", len(binary), b"BIN\0") + binary)


def mirror_pbr_reference(parent: Snapshot, surface: MeshState, variant: SymmetryVariant,
                         axis: int, output: Path, deadline: float) -> dict[str, object]:
    """Copy declarations/images and transport the source's declared shading frame.

    The repaired target normals may differ from source normals. This function
    preserves the source signal; the subsequent bake compensates in target TBN.
    Clipped corners use normalized barycentric frame interpolation; the resulting
    piecewise interpolated field is recorded, not claimed identical everywhere.
    """
    reference, source = _reference(parent, {"declared_tangents_only": True})
    source_sha = file_hash(source)
    document, original_binary = _source_document(source)
    if document.get("animations") or document.get("skins"):
        raise ValueError("PBR_MIRROR_STATIC_SOURCE_REQUIRED")
    materials = cast(list[dict[str, object]], document["materials"])
    pbr = cast(dict[str, object], materials[0]["pbrMetallicRoughness"])
    if not all(key in pbr for key in ("baseColorTexture", "metallicRoughnessTexture")) or "normalTexture" not in materials[0]:
        raise ValueError("PBR_MIRROR_BASECOLOR_NORMAL_METALLIC_ROUGHNESS_TEXTURES_REQUIRED")
    mesh = cast(list[dict[str, object]], document["meshes"])[0]
    primitive = cast(list[dict[str, object]], mesh["primitives"])[0]
    attributes = cast(dict[str, object], primitive["attributes"])
    if not all(key in attributes for key in ("NORMAL", "TANGENT")):
        raise ValueError("PBR_MIRROR_DECLARED_NORMAL_AND_TANGENT_REQUIRED")
    source_xyz = reference.geometry.vertices[reference.geometry.faces]
    parent_xyz = parent.state.surface.mesh.vertices[parent.state.surface.mesh.faces]
    identity = face_corner_identity(parent_xyz, source_xyz)
    source_count = len(reference.geometry.faces)
    _, source_valid = interpolated_frames(reference.normals, reference.tangents, reference.signs,
        np.repeat(np.arange(source_count, dtype=np.int64), 3), np.tile(np.eye(3), (source_count, 1)))
    source_invalid = ~source_valid.reshape(-1, 3)
    source_invalid |= ~np.isin(reference.signs, [-1., 1.])
    participating = np.any(np.abs(variant.source_barycentric) > 1e-12, axis=1)
    invalid_used = source_invalid[variant.source_face_ids] & participating
    if invalid_used.any():
        locations = [[int(variant.source_face_ids[face]), int(corner)]
                     for face, corner in np.argwhere(invalid_used)[:16]]
        raise ValueError("PBR_MIRROR_INVALID_PARTICIPATING_SOURCE_CORNERS:" + json.dumps(locations))
    ids = np.repeat(variant.source_face_ids, 3)
    bary = variant.source_barycentric.reshape(-1, 3)
    frames, valid = interpolated_frames(reference.normals, reference.tangents, reference.signs, ids, bary)
    if not valid.all():
        witnesses = [{"new_face": int(i // 3), "new_corner": int(i % 3), "source_face": int(ids[i]),
                      "source_barycentric": bary[i].tolist()} for i in np.flatnonzero(~valid)[:16]]
        raise ValueError("PBR_MIRROR_INVALID_INTERPOLATED_SOURCE_FRAME:" + json.dumps(witnesses))
    normals, tangents = frames[:, :, 2].copy(), frames[:, :, 0].copy()
    signs = np.sign(np.einsum("nk,nk->n", bary, reference.signs[ids]))
    mirrored = np.repeat(variant.reflected_faces, 3)
    normals[mirrored, axis] *= -1
    tangents[mirrored, axis] *= -1
    signs[mirrored] *= -1
    corner_uv = np.einsum("fck,fkj->fcj", variant.source_barycentric,
                          reference.geometry.corner_uv[variant.source_face_ids])
    gltf_uv = corner_uv.reshape(-1, 2).copy()
    gltf_uv[:, 1] = 1 - gltf_uv[:, 1]
    expected_positions = surface.mesh.vertices[surface.mesh.faces].reshape(-1, 3)
    image_hashes = _image_hashes(document, original_binary)
    binary = bytearray(original_binary)
    new_attributes = {name: _append_attribute(document, binary, values) for name, values in (
        ("POSITION", expected_positions), ("NORMAL", normals),
        ("TANGENT", np.column_stack((tangents, signs))), ("TEXCOORD_0", gltf_uv))}
    document["meshes"] = [{"primitives": [{"attributes": new_attributes, "material": 0, "mode": 4}]}]
    document["nodes"] = [{"mesh": 0, "name": "mirrored_same_surface_pbr"}]
    document["scenes"] = [{"nodes": [0]}]
    document["scene"] = 0
    extras = cast(dict[str, object], document.setdefault("extras", {}))
    reseam = extras.get("reseam")
    if isinstance(reseam, dict):
        reseam["tangent_contract"] = "explicit source frame transported by barycentric clipping and reflection"
    extras["mirror_pbr"] = {"source_sha256": source_sha,
        "frame_contract": "source declared N/T/w, barycentric clipping, reflected N/T and negated w",
        "source_face_corner_identity": identity, "full_pbr_appearance_accepted": False}
    check_deadline(deadline)
    _write_glb(output, document, binary)
    reloaded_document, reloaded_binary = _source_document(output)
    for key in ("materials", "textures", "samplers", "images"):
        if reloaded_document.get(key) != document.get(key):
            raise ValueError("PBR_MIRROR_MATERIAL_DECLARATION_ROUNDTRIP_FAILED:" + key)
    if _image_hashes(reloaded_document, reloaded_binary) != image_hashes:
        raise ValueError("PBR_MIRROR_IMAGE_BYTES_CHANGED")
    reread, _ = _reference(replace(parent, metadata={**parent.metadata, "reference_glb": str(output.resolve())}), {})
    comparisons = {
        "positions": (reread.geometry.vertices[reread.geometry.faces], expected_positions.reshape(-1, 3, 3)),
        "uv": (reread.geometry.corner_uv, corner_uv), "normal": (reread.normals, normals.reshape(-1, 3, 3)),
        "tangent": (reread.tangents, tangents.reshape(-1, 3, 3)), "sign": (reread.signs, signs.reshape(-1, 3)),
    }
    errors: dict[str, float] = {}
    for name, (actual, expected) in comparisons.items():
        if actual.shape != expected.shape or not np.allclose(actual, expected, rtol=0, atol=2e-6):
            raise ValueError("PBR_MIRROR_ATTRIBUTE_ROUNDTRIP_FAILED:" + name)
        errors[name] = float(np.max(np.abs(actual - expected), initial=0))
    if file_hash(source) != source_sha:
        raise ValueError("PBR_MIRROR_SOURCE_CHANGED_DURING_BUILD")
    parent_normals = parent.state.surface.corner_normals
    parent_normal_difference = None if parent_normals is None else float(
        np.max(np.linalg.norm(parent_normals - reference.normals, axis=-1), initial=0))
    return {"scope": "preserved same-surface BaseColor, normal, metallic/roughness and declared optional AO",
        "source": str(source.resolve()), "source_sha256": source_sha, "source_materials": materials,
        "source_image_sha256": image_hashes, "face_corner_identity": identity,
        "source_invalid_frame_corners": np.argwhere(source_invalid).tolist(),
        "participating_invalid_source_frame_corners": 0,
        "unused_invalid_frame_policy": "retained in original source audit; excluded from all new-corner barycentric support",
        "source_vs_parent_corner_normal_max_l2": parent_normal_difference,
        "frame_transport": "normalized source corner interpolation; reflect N/T and negate tangent sign",
        "clipped_field": "piecewise normalized interpolation; interior deviation requires bake/render review",
        "reference_reread_max_absolute_errors": errors, "material_declarations_and_image_bytes_preserved": True,
        "full_pbr_appearance_accepted": False}
