"""Discoverable operations; import optional execution modules only when invoked."""

from collections.abc import Callable
from importlib import import_module
from pathlib import Path
from typing import cast

Handler = Callable[[dict[str, object], Path, float], dict[str, object]]

TOOLS: dict[str, tuple[str, str, str, dict[str, object]]] = {
    "asset.import": ("assets", "import_asset", "Freeze geometry, original UV and material sources.",
                     {"path": "required absolute GLB/OBJ/FBX", "reference": "optional opaque PBR GLB",
                      "initialize_uv": "xatlas (explicitly required only if UV missing)",
                      "repair_degenerate_faces": "false; explicit intrinsic whole-face isolation, requires pack",
                      "blender": "optional executable"}),
    "uv.inspect": ("geometry", "inspect_uv", "Inspect charts, geometry safety and independent texture footprint.", {}),
    "view.render": ("assets", "render_views", "Numbered UV, textured/checker model views and selection evidence.",
                    {"size": "640; 64..2048", "mode": "groups|charts|regions|basecolor|checker|errors",
                     "selected_faces": "optional whole-face IDs for closeup", "region_id": "optional defined region",
                     "texture": "optional actual atlas path, sampled with current UV", "error_faces": "optional IDs",
                     "pick_pixels": "optional view name to [[x,y],...] returns stable face IDs and barycentric coordinates"}),
    "region.define": ("regions", "define_region", "Persist an Agent-defined region without changing geometry.",
                      {"name": "required semantic name", "faces": "whole-face IDs, or chart_ids",
                       "protected_faces": "optional whole-face IDs", "role": "reuse_candidate",
                       "rationale": "Agent decision reason", "evidence": "source views or inspection evidence"}),
    "uv.reparameterize": ("reparameterize", "reparameterize", "Rebuild regional UV with an explicit geometry backend.",
                          {"faces": "required whole-face IDs", "backend": "harmonic|optcuts",
                           "note": "immutable child; original triangles and geometry retained; rebake required"}),
    "material.target": ("color_reuse", "freeze_target", "Freeze source-derived color target independently of an atlas.",
                        {"protected_faces": "optional whole-face IDs", "master_members": "group ID to first|second",
                         "policy": "source_original|symmetrize",
                         "original_topology": "optional authoritative imported NPZ with original face/corner identity, for pre-split seam checks"}),
    "reuse.find_candidates": ("geometry", "find_candidates", "Find geometric mirror-region proposals.",
                              {"max_candidates": "32", "max_regions": "512", "center_fraction": "0.05",
                               "minimum_area_ratio": "0.6", "axes": "fixed X/Y/Z through bounds center"}),
    "reuse.check_pair": ("geometry", "check_pair", "Check exact or approximate surface sharing before application.",
                         {"first_faces": "face ID list, or first_chart", "second_faces": "face ID list, or second_chart",
                          "plane_normal": "[1,0,0]", "plane_offset": "0", "mode": "auto|exact|approximate|topology|common_domain|normal_corner",
                          "exclusive_faces": "optional whole-face IDs", "refine_iterations": "0", "solve_seconds": "30"}),
    "reuse.apply": ("geometry", "apply_reuse", "Apply a checked proposal or release sharing in a new snapshot.",
                    {"proposal": "checked proposal.json path", "action": "share|exclusive|unshare",
                     "exclusive_faces": "whole-face IDs", "group_ids": "for unshare"}),
    "uv.pack": ("geometry", "pack_uv", "Pack groups together and recheck exact overlap permissions.", {"density_normalize": True}),
    "material.bake": ("material", "bake_material_tool", "Bake source material into actual channels and full mips.",
                      {"backend": "cpu|blender; default cpu", "resolution": "256; integer 8..2048",
                       "mode": "pbr (default) | basecolor_diagnostic | basecolor_reuse (scoped acceptance)",
                       "target": "optional frozen target.json for basecolor_reuse; resolution supports up to 8192 in this scope",
                       "padding_pixels": "4; integer 0 <= padding < resolution/2",
                       "max_projection_fraction": "source-scale normalized projection bound",
                       "mikk_library": "optional official Mikk bridge library", "blender": "optional executable"}),
    "quality.validate": ("material", "validate_quality", "Independent geometry, channel, mip and render validation.",
                         {"bake": "bake.json path", "thresholds": "optional explicit material thresholds",
                          "mip_levels": "[0,1]; output stores full chain; validation checks requested LODs",
                          "render_size": "48", "max_projection_fraction": "0.02", "important_faces": "optional face ID list",
                          "geometry_profile": "same explicit geometry limits as uv.inspect profile"}),
    "asset.export": ("material", "export_asset", "Export bound validated artifacts or explicitly diagnostic results.",
                     {"bake": "bake.json path", "validation": "validation.json path", "mode": "accepted|diagnostic",
                      "baseline_bake": "optional baseline bake.json", "baseline_validation": "optional baseline validation.json"}),
}


def describe(name: str) -> dict[str, object]:
    if name not in TOOLS:
        raise ValueError("UNKNOWN_TOOL:" + name)
    module, function, description, parameters = TOOLS[name]
    common: dict[str, object] = {"timeout_seconds": "300; positive wall-clock deadline"}
    if name != "asset.import":
        common.update({"snapshot": "required snapshot.json path or snapshot directory",
                       "state_hash": "optional expected immutable snapshot hash"})
    if module in {"geometry", "reparameterize"}:
        common["profile"] = {"resolution": 256, "gap_pixels": 4, "border_pixels": 4,
                             "correspondence_fraction": 0.005, "normal_angle_degrees": 45,
                             "maximum_anisotropy": 2, "mean_shape_limit": 0.025, "tail_shape_limit": 0.1,
                             "maximum_density_ratio": 2, "quality_constraints": True, "balance_density": False,
                             "note": "geometry/quality thresholds are explicit and must match across comparisons"}
    return {"name": name, "description": description, "entrypoint": f"{module}.{function}",
            "request": {**common, **parameters}, "outputs": "result.json, process.log and named artifacts",
            "contract": "New output directory only. Execution status is separate from quality acceptance."}


def handler(name: str) -> Handler:
    if name not in TOOLS:
        raise ValueError("UNKNOWN_TOOL:" + name)
    module, function, _, _ = TOOLS[name]
    return cast(Handler, getattr(import_module(f"reseam.agent_tools.{module}"), function))
