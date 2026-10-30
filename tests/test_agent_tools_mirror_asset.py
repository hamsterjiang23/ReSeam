"""New geometry is explicit and paired UVs preserve the retained source signal."""

import json
import time
from pathlib import Path

import numpy as np
import pytest
import trimesh
from PIL import Image
from trimesh.visual.material import PBRMaterial
from trimesh.visual.texture import TextureVisuals

from reseam.agent_tools.assets import import_asset
from reseam.agent_tools.geometry import geometry_report
from reseam.agent_tools.mirror_asset import mirror_asset
from reseam.agent_tools.state import file_hash, load_snapshot
from reseam.budget_texture import load_reference


def test_mirror_tool_derives_new_asset_and_identical_geometry_uv_controls(tmp_path: Path) -> None:
    mesh = trimesh.Trimesh(vertices=[[-1., -1, 0], [1., -1, 0], [1., 1, 0], [-1., 1, 0]],
                          faces=[[0, 1, 2], [0, 2, 3]], process=False)
    mesh.visual = TextureVisuals(uv=np.array([[0., 0], [1., 0], [1., 1], [0., 1]]),
        material=PBRMaterial(baseColorTexture=Image.new("RGBA", (8, 8), (40, 120, 210, 255))))
    source = tmp_path / "source.glb"
    mesh.export(source, file_type="glb")
    imported = tmp_path / "import"
    imported.mkdir()
    receipt = import_asset({"path": str(source)}, imported, time.monotonic()+20)
    parent = load_snapshot(Path(str(receipt["snapshot"])))
    parent_hash = file_hash(parent.root / "arrays.npz")
    output = tmp_path / "mirror"
    output.mkdir()
    profile: dict[str, object] = {"resolution": 128, "gap_pixels": 4, "border_pixels": 4,
                                  "quality_constraints": False}
    result = mirror_asset({"snapshot": str(parent.root), "allow_geometry_change": True, "profile": profile},
                          output, time.monotonic()+20)
    shared = load_snapshot(Path(str(result["shared_snapshot"])))
    unshared = load_snapshot(Path(str(result["unshared_snapshot"])))
    assert shared.asset_id == unshared.asset_id != parent.asset_id
    assert file_hash(parent.root / "arrays.npz") == parent_hash
    assert len(shared.state.groups) == 1
    assert unshared.state.groups == ()
    np.testing.assert_array_equal(shared.state.surface.mesh.vertices, unshared.state.surface.mesh.vertices)
    np.testing.assert_array_equal(shared.state.surface.mesh.faces, unshared.state.surface.mesh.faces)
    assert geometry_report(shared.state, profile)["hard_valid"] is True
    assert geometry_report(unshared.state, profile)["hard_valid"] is True
    reference = load_reference(Path(str(result["reference_glb"])))
    with np.load(output / "source_correspondence.npz") as data:
        pairs = data["face_pairs"]
        np.testing.assert_array_equal(reference.corner_uv[pairs[:, 0]],
                                      reference.corner_uv[pairs[:, 1]][:, [0, 2, 1]])
    assert reference.corner_uv[:, :, 0].min() >= .5
    record = json.loads((output / "mirror_variant.json").read_text())
    assert record["original_asset_modified"] is False
    assert record["quality_accepted"] is None


def test_geometry_change_must_be_explicit_before_reading_source(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="EXPLICIT_GEOMETRY_CHANGE"):
        mirror_asset({}, tmp_path, time.monotonic()+10)
