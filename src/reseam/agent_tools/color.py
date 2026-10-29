"""Explicit BaseColor-only diagnostic for materials outside the full PBR contract."""

from pathlib import Path

import numpy as np
import trimesh
from PIL import Image
from trimesh.visual.material import PBRMaterial
from trimesh.visual.texture import TextureVisuals

from ..budget_state import owners_and_members
from ..budget_texture import bake_atlas, evaluate_atlas, load_reference
from .geometry import geometry_report
from .material import _source_document, _wraps
from .state import check_deadline, file_hash, require_snapshot, write_json


def bake_color_diagnostic(request: dict[str, object], output: Path, deadline: float) -> dict[str, object]:
    snapshot = require_snapshot(request)
    source = snapshot.metadata.get("reference_glb")
    if not isinstance(source, str):
        raise TypeError("REFERENCE_GLB_REQUIRED")
    document, _ = _source_document(Path(source))
    definitions = document.get("materials", [])
    if not isinstance(definitions, list) or len(definitions) != 1 or not isinstance(definitions[0], dict):
        raise ValueError("SINGLE_BASECOLOR_MATERIAL_REQUIRED")
    material = definitions[0]
    pbr = material.get("pbrMetallicRoughness", {})
    if not isinstance(pbr, dict):
        raise TypeError("BASECOLOR_DEFINITION_REQUIRED")
    definition = pbr.get("baseColorTexture")
    if (not isinstance(definition, dict) or definition.get("texCoord", 0) != 0
            or definition.get("extensions") or material.get("alphaMode", "OPAQUE") != "OPAQUE"):
        raise ValueError("UNSUPPORTED_BASECOLOR_DEFINITION")
    repeat = _wraps(document, (definition, None, None, None))[0]
    resolution = request.get("resolution", 256)
    padding = request.get("padding_pixels", 4)
    if type(resolution) is not int or not 8 <= resolution <= 2048 or type(padding) is not int or not 0 <= padding < resolution / 2:
        raise ValueError("INVALID_BAKE_SETTINGS")
    mips = request.get("mip_levels", [0, 1])
    if not isinstance(mips, list) or not mips or any(type(mip) is not int for mip in mips):
        raise ValueError("INVALID_MIP_LEVELS")
    safety = geometry_report(snapshot.state, {"resolution": resolution, "gap_pixels": 0,
                                             "border_pixels": 0, "quality_constraints": False})
    if safety.get("hard_valid") is not True:
        return {"status": "REJECTED", "execution_success": True, "reason": "GEOMETRY_UNSAFE", "geometry": safety}
    check_deadline(deadline)
    reference = load_reference(Path(source))
    reference.repeat = repeat
    factor = np.asarray(pbr.get("baseColorFactor", [1., 1., 1., 1.]), dtype=float)
    if factor.shape != (4,) or not np.isfinite(factor).all() or np.any(factor < 0) or np.any(factor > 1):
        raise ValueError("INVALID_BASECOLOR_FACTOR")
    reference.base_factor = factor
    _, members = owners_and_members(snapshot.state)
    atlas = bake_atlas(snapshot.state.surface, reference, resolution, resolution, output / "texture",
                       member_ids=members, padding_pixels=padding, deadline=deadline)
    quality = evaluate_atlas(snapshot.state.surface, reference, atlas, mip_levels=tuple(mips), deadline=deadline)
    surface = snapshot.state.surface
    uv = surface.uv[surface.uv_faces].reshape(-1, 2)
    vertices = surface.mesh.vertices[surface.mesh.faces].reshape(-1, 3)
    faces = np.arange(len(vertices), dtype=np.int64).reshape(-1, 3)
    preview = trimesh.Trimesh(vertices=vertices, faces=faces, process=False)
    preview.visual = TextureVisuals(uv=uv, material=PBRMaterial(baseColorTexture=Image.fromarray(atlas.levels[0]),
                                                              metallicFactor=0., roughnessFactor=1.))
    preview.metadata["scope"] = "BaseColor-only diagnostic preview; source specular and other channels NOT reproduced"
    preview.export(output / "color_only_preview.glb", file_type="glb")
    result: dict[str, object] = {"status": "BASE_COLOR_DIAGNOSTIC", "quality_accepted": False,
        "state_hash": snapshot.state_hash, "asset_id": snapshot.asset_id,
        "source": source, "source_sha256": file_hash(Path(source)), "geometry": safety,
        "color_quality": quality, "metadata": atlas.metadata,
        "ignored_for_color_only": {"material_extensions": material.get("extensions", {}),
                                   "channels": ["normal", "roughness", "metallic", "occlusion", "specular", "render"]},
        "preview_material": "BaseColor only; metallic=0, roughness=1 solely for diagnostic viewing",
        "files": {str(p.relative_to(output)): file_hash(p) for p in sorted((output / "texture").iterdir())},
        "preview": str(output / "color_only_preview.glb")}
    require_snapshot(request)
    write_json(output / "color_diagnostic.json", result)
    return {"status": "OK", "execution_success": True, "quality_accepted": False,
            "diagnostic_only": True, "base_color_valid": quality["base_color_valid"],
            "report": str(output / "color_diagnostic.json"), "payload_bytes": atlas.metadata["payload_bytes"],
            "preview": str(output / "color_only_preview.glb")}
