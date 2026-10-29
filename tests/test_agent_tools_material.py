"""Artifact-bound material workflow: actual textures, independent gates, rollback."""

import json
import struct
import time
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from reseam.agent_tools.material import bake_material_tool, export_asset, validate_quality
from reseam.agent_tools.state import Snapshot, file_hash, save_snapshot
from reseam.bake_variant_bake import DEFAULT_BLENDER
from reseam.budget_material import atlas_from_arrays, export_material_glb
from reseam.budget_state import BudgetState
from reseam.budget_texture import decode_rgba8
from reseam.joint_types import ReuseGroup
from reseam.mesh import make_mesh
from reseam.types import MeshState


def _asset(root: Path, conflict: bool = False) -> Snapshot:
    if conflict:
        vertices = np.asarray(
            [[-2, 0, 0], [-1, 0, 0], [-2, 1, 0], [2, 0, 0], [2, 1, 0], [1, 0, 0]], dtype=float
        )
        faces = np.asarray([[0, 1, 2], [3, 4, 5]], dtype=np.int64)
        uv = np.asarray([[0.1, 0.2], [0.4, 0.2], [0.1, 0.8], [0.9, 0.2], [0.9, 0.8], [0.6, 0.2]])
    else:
        vertices = np.asarray([[0, 0, 0], [1, 0, 0], [0, 1, 0]], dtype=float)
        faces = np.asarray([[0, 1, 2]], dtype=np.int64)
        uv = np.asarray([[0.15, 0.15], [0.85, 0.15], [0.15, 0.85]])
    surface = MeshState(make_mesh(vertices, faces), frozenset(), uv, faces.copy())
    color = np.full((32, 32, 4), [0.2, 0.4, 0.1, 1])
    if conflict:
        color[:, :16] = [0.8, 0.1, 0.1, 1]
        color[:, 16:] = [0.1, 0.1, 0.8, 1]
    atlas = atlas_from_arrays(
        color, np.full((32, 32, 3), [0.0, 0.0, 1.0]), np.full((32, 32, 3), [0.3, 0.6, 0.2])
    )
    reference = root / "reference.glb"
    export_material_glb(surface, atlas, reference)
    state = BudgetState(surface)
    if conflict:
        surface.uv[:3] = [[0.15, 0.15], [0.85, 0.15], [0.15, 0.85]]
        surface.uv[3:] = surface.uv[[0, 2, 1]]
        state.groups = (ReuseGroup("mirror", (0,), (1,), (1.0, 0.0, 0.0), 0.0, 1, -1),)
    return save_snapshot(
        state,
        root / "snapshot",
        {
            "reference_glb": str(reference),
            "normal_policy": "explicit_flat_target_with_original_source_shading",
        },
    )


def _output(root: Path, name: str) -> Path:
    output = root / name
    output.mkdir()
    return output


def test_actual_cpu_bake_independent_quality_and_accepted_export(tmp_path: Path) -> None:
    snapshot = _asset(tmp_path)
    request: dict[str, object] = {"snapshot": str(snapshot.root), "resolution": 32}
    bake = bake_material_tool(request, _output(tmp_path, "bake"), time.monotonic() + 60)
    assert bake["quality_accepted"] is False
    assert bake["payload_bytes"] == 3 * 4 * sum(size**2 for size in (32, 16, 8, 4, 2, 1))
    request["bake"] = bake["bake"]
    request["render_size"] = 16
    result = validate_quality(request, _output(tmp_path, "quality"), time.monotonic() + 60)
    assert result["quality_accepted"] is True
    evidence = json.loads((tmp_path / "quality/validation.json").read_text())
    assert evidence["material"]["raster_checked"]
    assert evidence["render"]["views"] == 4
    request["validation"] = result["validation"]
    exported = export_asset(request, _output(tmp_path, "export"), time.monotonic() + 60)
    assert exported["quality_accepted"] is True
    assert (tmp_path / "export/asset.glb").stat().st_size > 100
    assert len(list((tmp_path / "export/texture").glob("*_mip*.png"))) == 18


def test_shared_material_conflict_is_averaged_then_rejected(tmp_path: Path) -> None:
    snapshot = _asset(tmp_path, conflict=True)
    request: dict[str, object] = {"snapshot": str(snapshot.root), "resolution": 32}
    bake = bake_material_tool(request, _output(tmp_path, "bake"), time.monotonic() + 60)
    assert bake["status"] == "OK", bake
    with Image.open(tmp_path / "bake/texture/BaseColor_mip0.png") as image:
        values = decode_rgba8(np.asarray(image.convert("RGBA"), dtype=np.uint8))
    assert values[21, 10, :3] == pytest.approx([0.45, 0.1, 0.45], abs=0.006)
    request.update(bake=bake["bake"], render_size=8)
    result = validate_quality(request, _output(tmp_path, "quality"), time.monotonic() + 60)
    assert result["quality_accepted"] is False
    assert result["material_valid"] is False


def test_unvalidated_export_refused_and_diagnostic_explicit(tmp_path: Path) -> None:
    snapshot = _asset(tmp_path)
    request: dict[str, object] = {"snapshot": str(snapshot.root), "resolution": 32}
    bake = bake_material_tool(request, _output(tmp_path, "bake"), time.monotonic() + 60)
    request["bake"] = bake["bake"]
    result = export_asset(request, _output(tmp_path, "rejected"), time.monotonic() + 60)
    assert result["status"] == "REJECTED"
    assert not (tmp_path / "rejected/asset.glb").exists()
    request["mode"] = "diagnostic"
    diagnostic = export_asset(request, _output(tmp_path, "diagnostic"), time.monotonic() + 60)
    assert diagnostic["quality_accepted"] is False


def test_tampered_texture_and_stale_snapshot_cannot_validate(tmp_path: Path) -> None:
    snapshot = _asset(tmp_path)
    request: dict[str, object] = {"snapshot": str(snapshot.root), "resolution": 32}
    bake = bake_material_tool(request, _output(tmp_path, "bake"), time.monotonic() + 60)
    request["bake"] = bake["bake"]
    changed = snapshot.state.copy()
    changed.surface.uv *= 0.9
    derived = save_snapshot(changed, tmp_path / "changed", snapshot.metadata, snapshot)
    with pytest.raises(ValueError, match="STALE_BAKE"):
        validate_quality(
            {**request, "snapshot": str(derived.root)}, _output(tmp_path, "stale"), time.monotonic() + 60
        )
    path = tmp_path / "bake/texture/Normal_mip0.png"
    path.write_bytes(path.read_bytes() + b"altered")
    with pytest.raises(ValueError, match="BAKE_ARTIFACT_HASH_MISMATCH"):
        validate_quality(request, _output(tmp_path, "tampered"), time.monotonic() + 60)


def test_timeout_and_missing_reference_preserve_snapshot(tmp_path: Path) -> None:
    snapshot = _asset(tmp_path)
    before = file_hash(snapshot.root / "snapshot.json")
    with pytest.raises(TimeoutError):
        bake_material_tool(
            {"snapshot": str(snapshot.root)}, _output(tmp_path, "expired"), time.monotonic() - 1
        )
    assert before == file_hash(snapshot.root / "snapshot.json")
    incomplete = save_snapshot(snapshot.state, tmp_path / "missing-reference", {})
    with pytest.raises(ValueError, match="MATERIAL_REFERENCE_REQUIRED"):
        bake_material_tool(
            {"snapshot": str(incomplete.root), "resolution": 32},
            _output(tmp_path, "missing"),
            time.monotonic() + 30,
        )


def test_absent_maps_use_declared_gltf_constants(tmp_path: Path) -> None:
    snapshot = _asset(tmp_path)
    source = tmp_path / "reference.glb"
    data = source.read_bytes()
    old_size = struct.unpack_from("<I", data, 12)[0]
    document = json.loads(data[20 : 20 + old_size])
    document["materials"][0] = {
        "name": "Constant",
        "pbrMetallicRoughness": {
            "baseColorFactor": [0.25, 0.5, 0.75, 1],
            "metallicFactor": 0.25,
            "roughnessFactor": 0.4,
        },
    }
    encoded = json.dumps(document).encode()
    encoded += b" " * (-len(encoded) % 4)
    tail = data[20 + old_size :]
    source.write_bytes(
        struct.pack("<4sII", b"glTF", 2, 20 + len(encoded) + len(tail))
        + struct.pack("<I4s", len(encoded), b"JSON")
        + encoded
        + tail
    )
    result = bake_material_tool(
        {"snapshot": str(snapshot.root), "resolution": 32},
        _output(tmp_path, "constants"),
        time.monotonic() + 60,
    )
    assert result["status"] == "OK"
    with Image.open(tmp_path / "constants/texture/BaseColor_mip0.png") as image:
        color = decode_rgba8(np.asarray(image, dtype=np.uint8))[21, 10]
    with Image.open(tmp_path / "constants/texture/ORM_mip0.png") as image:
        orm = np.asarray(image)[21, 10, :3]
    assert color == pytest.approx([0.25, 0.5, 0.75, 1], abs=0.005)
    assert orm == pytest.approx([255, 0.4 * 255, 0.25 * 255], abs=1)


@pytest.mark.skipif(not DEFAULT_BLENDER.is_file(), reason="Real Blender dependency unavailable")
def test_shared_blender_members_composed_before_quality(tmp_path: Path) -> None:
    snapshot = _asset(tmp_path, conflict=True)
    result = bake_material_tool(
        {"snapshot": str(snapshot.root), "resolution": 32, "backend": "blender"},
        _output(tmp_path, "blender"),
        time.monotonic() + 90,
    )
    assert result["status"] == "OK"
    with Image.open(tmp_path / "blender/texture/BaseColor_mip0.png") as image:
        color = decode_rgba8(np.asarray(image, dtype=np.uint8))[21, 10, :3]
    assert color == pytest.approx([0.45, 0.1, 0.45], abs=0.01)
    assert (tmp_path / "blender/blender_pass_0/record.json").is_file()
    assert (tmp_path / "blender/blender_pass_1/record.json").is_file()
