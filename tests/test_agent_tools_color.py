"""BaseColor diagnostic never implies unsupported full-material acceptance."""

import time
from pathlib import Path

import numpy as np
import pytest

from reseam.agent_tools.material import bake_material_tool, export_asset, validate_quality
from reseam.agent_tools.state import read_object, save_snapshot
from reseam.budget_material import atlas_from_arrays, export_material_glb
from reseam.budget_state import BudgetState
from reseam.mesh import make_mesh
from reseam.types import MeshState


def test_color_only_outputs_real_channel_without_full_acceptance(tmp_path: Path) -> None:
    vertices = np.array([[0., 0, 0], [1, 0, 0], [0, 1, 0]])
    faces = np.array([[0, 1, 2]], dtype=np.int64)
    uv = np.array([[.15, .15], [.85, .15], [.15, .85]])
    surface = MeshState(make_mesh(vertices, faces), frozenset(), uv, faces)
    atlas = atlas_from_arrays(np.full((8, 8, 4), [.2, .4, .1, 1]),
                              np.full((8, 8, 3), [0., 0., 1.]), np.full((8, 8, 3), [1., .6, .2]))
    source = tmp_path / "source.glb"
    export_material_glb(surface, atlas, source)
    snapshot = save_snapshot(BudgetState(surface), tmp_path / "snapshot", {"reference_glb": str(source)})
    output = tmp_path / "diagnostic"
    output.mkdir()
    result = bake_material_tool({"snapshot": str(snapshot.root), "mode": "basecolor_diagnostic", "resolution": 32},
                                output, time.monotonic() + 30)
    assert result["status"] == "OK" and result["diagnostic_only"]
    assert result["quality_accepted"] is False
    assert result["base_color_valid"] is True
    assert (output / "texture/BaseColor_mip0.png").exists()
    assert not (output / "texture/Normal_mip0.png").exists()
    assert (output / "color_only_preview.glb").stat().st_size > 0
    report = read_object(output / "color_diagnostic.json")
    assert report["ignored_for_color_only"]
    assert report["geometry_profile"]["resolution"] == 32
    rejected_output = tmp_path / "strict_border"
    rejected_output.mkdir()
    rejected = bake_material_tool({"snapshot": str(snapshot.root), "mode": "basecolor_diagnostic", "resolution": 32,
                                   "geometry_profile": {"border_pixels": 16}}, rejected_output, time.monotonic() + 30)
    assert rejected["status"] == "REJECTED" and rejected["reason"] == "GEOMETRY_UNSAFE"
    assert not (rejected_output / "color_only_preview.glb").exists()
    for handler in (validate_quality, export_asset):
        with pytest.raises(ValueError, match="DIAGNOSTIC_REPORT_IS_NOT_BAKE_MANIFEST"):
            handler({"snapshot": str(snapshot.root), "bake": str(output / "color_diagnostic.json")},
                    tmp_path, time.monotonic() + 30)
