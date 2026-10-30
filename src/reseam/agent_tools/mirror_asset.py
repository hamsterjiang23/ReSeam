"""Explicit new-geometry mirror variant; fixed-asset UV operations remain immutable."""

import json
import struct
from pathlib import Path
from typing import cast

import numpy as np
import trimesh
from PIL import Image
from trimesh.visual.material import PBRMaterial
from trimesh.visual.texture import TextureVisuals

from ..budget_state import BudgetState
from ..joint_types import ReuseGroup
from ..mesh import chart_labels, make_mesh
from ..types import FloatArray, MeshState
from ..validation import jacobians
from .geometry import _packed, _profile
from .state import check_deadline, file_hash, require_snapshot, save_snapshot, snapshot_result, write_json
from .views import _texture


def _reference_glb(
    surface: MeshState, corner_uv: FloatArray, image: Image.Image, factor: FloatArray,
    repeat: bool, path: Path,
) -> None:
    """Export exact face order and preserve the source BaseColor factor/sampler."""
    mesh = trimesh.Trimesh(vertices=surface.mesh.vertices[surface.mesh.faces].reshape(-1, 3),
                          faces=np.arange(len(surface.mesh.faces)*3).reshape(-1, 3), process=False)
    mesh.visual = TextureVisuals(uv=corner_uv.reshape(-1, 2),
        material=PBRMaterial(baseColorTexture=image, metallicFactor=0., roughnessFactor=1.))
    raw = cast(bytes, mesh.export(file_type="glb"))
    old_length = struct.unpack_from("<I", raw, 12)[0]
    document = json.loads(raw[20:20+old_length])
    document["materials"][0]["pbrMetallicRoughness"]["baseColorFactor"] = factor.tolist()
    document["samplers"] = [{"wrapS": 10497 if repeat else 33071, "wrapT": 10497 if repeat else 33071}]
    for texture in document["textures"]:
        texture["sampler"] = 0
    document["asset"]["extras"] = {"scope": "Mirrored variant BaseColor only; original PBR not reproduced"}
    encoded = json.dumps(document, separators=(",", ":")).encode("utf-8")
    encoded += b" "*((-len(encoded)) % 4)
    tail = raw[20+old_length:]
    path.write_bytes(b"glTF"+struct.pack("<II", 2, 20+len(encoded)+len(tail))
                     +struct.pack("<I4s", len(encoded), b"JSON")+encoded+tail)


def mirror_asset(request: dict[str, object], output: Path, deadline: float) -> dict[str, object]:
    """Clip and mirror into a new asset, then derive matched packed UV controls."""
    if request.get("allow_geometry_change") is not True:
        raise ValueError("MIRROR_VARIANT_REQUIRES_EXPLICIT_GEOMETRY_CHANGE")
    from .symmetry_variant import build_symmetry_variant

    parent = require_snapshot(request)
    axis = request.get("axis", "x")
    if axis not in ("x", "y", "z"):
        raise ValueError("MIRROR_AXIS_MUST_BE_X_Y_Z")
    offset = request.get("offset", 0.)
    positive = request.get("keep_positive", True)
    if (isinstance(offset, bool) or not isinstance(offset, (int, float))
            or not np.isfinite(offset) or not isinstance(positive, bool)):
        raise ValueError("MIRROR_FINITE_OFFSET_AND_BOOLEAN_SIDE_REQUIRED")
    pixels, source_uv, factor, repeat, source_record = _texture({}, parent)
    variant = build_symmetry_variant(parent.state.surface, plane_axis=("x", "y", "z").index(str(axis)),
                                    plane_offset=float(offset), keep_positive=positive, deadline=deadline)
    surface = variant.surface.copy()
    positions = surface.mesh.vertices.astype(np.float32).astype(float)
    quantization_error = float(np.abs(positions-surface.mesh.vertices).max())
    surface.mesh = make_mesh(positions, surface.mesh.faces)
    reference_uv = np.einsum("fck,fkj->fcj", variant.source_barycentric, source_uv[variant.source_face_ids])
    reference = output / "mirrored_source.glb"
    check_deadline(deadline)
    _reference_glb(surface, reference_uv, Image.fromarray(pixels), factor, repeat, reference)
    check_deadline(deadline)
    provenance = output / "source_correspondence.npz"
    np.savez_compressed(provenance, source_face_ids=variant.source_face_ids,
                        source_barycentric=variant.source_barycentric, face_pairs=variant.face_pairs)
    labels = chart_labels(surface)
    signs = np.sign(np.linalg.det(jacobians(surface))).astype(int)
    normal = [0., 0., 0.]
    normal[("x", "y", "z").index(str(axis))] = 1.
    groups: list[ReuseGroup] = []
    for label in np.unique(labels[variant.face_pairs[:, 0]]):
        pairs = variant.face_pairs[labels[variant.face_pairs[:, 0]] == label]
        first, second = pairs[:, 0], pairs[:, 1]
        first_signs = np.unique(signs[first])
        if len(first_signs) != 1 or first_signs[0] == 0 or not np.all(signs[second] == -first_signs[0]):
            raise ValueError("MIRROR_SOURCE_CHART_HAS_MIXED_OR_DEGENERATE_UV_ORIENTATION")
        groups.append(ReuseGroup(f"mirror:{int(label)}", tuple(map(int, first)), tuple(map(int, second)),
                                (normal[0], normal[1], normal[2]), float(offset),
                                int(first_signs[0]), -int(first_signs[0])))
    state = BudgetState(surface, groups=tuple(groups))
    metadata: dict[str, object] = {"reference_glb": str(reference.resolve()), "source_uv_present": True,
        "source_manifest": [{"path": str(reference.resolve()), "sha256": file_hash(reference)},
                            {"path": str(provenance.resolve()), "sha256": file_hash(provenance)}],
        "derived_from": {"asset_id": parent.asset_id, "state_hash": parent.state_hash,
                         "snapshot": str(parent.root / "snapshot.json")},
        "geometry_variant": "plane_clip_then_exact_mirror", "basecolor_only": True,
        "source_material_scope": source_record, "requires_rebake": True,
        "source_correspondence": str(provenance.resolve())}
    # No parent= argument: this is intentionally a new geometry/asset identity.
    root = save_snapshot(state, output / "variant_root", metadata)
    profile = _profile(request)
    shared = _packed(state, profile, density_normalize=False, deadline=deadline)
    unshared = _packed(BudgetState(surface.copy()), profile, density_normalize=False, deadline=deadline)
    check_deadline(deadline)
    shared_snapshot = save_snapshot(shared, output / "shared", metadata, root)
    unshared_snapshot = save_snapshot(unshared, output / "unshared", metadata, root)
    report = {**variant.report, "parent_asset_id": parent.asset_id, "variant_asset_id": root.asset_id,
        "parent_state_hash": parent.state_hash, "float32_position_max_abs_error": quantization_error,
        "source_uv_interpolation": "source triangle barycentric; reflected members use retained-side source",
        "shared_groups": len(groups), "geometry_changed": True, "original_asset_modified": False,
        "geometry_validated": False, "quality_accepted": None, "full_pbr_passed": False,
        "shared_snapshot": snapshot_result(shared_snapshot), "unshared_snapshot": snapshot_result(unshared_snapshot),
        "variant_root": snapshot_result(root), "source_reference_glb": str(reference.resolve()),
        "source_correspondence": str(provenance.resolve()), "profile": profile,
        "scope": "explicit upstream geometry variant, not fixed-geometry UV improvement"}
    write_json(output / "mirror_variant.json", report)
    return {**snapshot_result(shared_snapshot), "shared_snapshot": str(shared_snapshot.root / "snapshot.json"),
            "unshared_snapshot": str(unshared_snapshot.root / "snapshot.json"),
            "variant_root": str(root.root / "snapshot.json"), "reference_glb": str(reference.resolve()),
            "geometry_changed": True, "requires_rebake": True, "final_quality_passed": None,
            "report": str((output / "mirror_variant.json").resolve())}
