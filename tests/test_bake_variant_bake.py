"""Real isolated Blender smoke and rollback checks for the missing worker."""

import json
import struct
import time
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from reseam.bake_variant_bake import DEFAULT_BLENDER, BakeVariantRequest, bake_variant
from reseam.budget_material import atlas_from_arrays, export_material_glb, frame_arrays
from reseam.mesh import make_mesh
from reseam.types import MeshState


def source_fixture(root: Path) -> tuple[MeshState, Path]:
    vertices = np.asarray([[0, 0, 0], [1, 0, 0], [0, 1, 0]], dtype=float)
    faces = np.asarray([[0, 1, 2]], dtype=np.int64)
    uv = np.asarray([[0.15, 0.15], [0.85, 0.15], [0.15, 0.85]])
    state = MeshState(make_mesh(vertices, faces), frozenset(), uv, faces.copy())
    atlas = atlas_from_arrays(
        np.full((8, 8, 4), [0.2, 0.4, 0.1, 1]),
        np.full((8, 8, 3), [0.25, -0.3, 0.92]),
        np.full((8, 8, 3), [0.3, 0.6, 0.2]),
    )
    source = root / "source.glb"
    export_material_glb(state, atlas, source)
    return state, source


@pytest.mark.skipif(not DEFAULT_BLENDER.is_file(), reason="Real Blender dependency unavailable")
@pytest.mark.parametrize("mirror", [False, True])
def test_real_blender_three_channels_and_mirrored_tbn(tmp_path: Path, mirror: bool) -> None:
    state, source = source_fixture(tmp_path)
    if mirror:
        state.uv[:, 0] = 1 - state.uv[:, 0]
    request = BakeVariantRequest(
        state.mesh.vertices, state.mesh.faces, state.uv[state.uv_faces], source, 32, 0.02, 4
    )
    result = bake_variant(request, tmp_path / "bake", deadline=time.monotonic() + 120)
    assert result["status"] == "BAKED_THREE_CHANNELS"
    with Image.open(tmp_path / "bake/ORM.png") as image:
        orm = np.asarray(image)
    with Image.open(tmp_path / "bake/BaseColor.png") as image:
        color = np.asarray(image)
    with Image.open(tmp_path / "bake/Normal.png") as image:
        normal = np.asarray(image, dtype=float) / 255 * 2 - 1
    x, y = (21 if mirror else 10), 21
    assert orm[y, x, :3] == pytest.approx([77, 153, 51], abs=3)
    assert color[y, x, :3] == pytest.approx([124, 170, 89], abs=3)
    n, t, sign = frame_arrays(state)
    bitangent = np.cross(n[0, 0], t[0, 0]) * sign[0, 0]
    world = t[0, 0] * normal[y, x, 0] + bitangent * normal[y, x, 1] + n[0, 0] * normal[y, x, 2]
    expected = np.asarray([0.25, 0.3, 0.92])
    expected /= np.linalg.norm(expected)
    assert world == pytest.approx(expected, abs=0.025)


def test_expired_bake_never_starts_or_changes_inputs(tmp_path: Path) -> None:
    state, source = source_fixture(tmp_path)
    original = source.read_bytes()
    request = BakeVariantRequest(state.mesh.vertices, state.mesh.faces, state.uv[state.uv_faces], source, 32)
    with pytest.raises(TimeoutError):
        bake_variant(request, tmp_path / "expired", deadline=time.monotonic() - 1)
    assert not (tmp_path / "expired").exists()
    assert source.read_bytes() == original


def test_overlapping_sheets_cannot_overwrite_each_other(tmp_path: Path) -> None:
    state, source = source_fixture(tmp_path)
    request = BakeVariantRequest(
        state.mesh.vertices,
        np.vstack((state.mesh.faces, state.mesh.faces)),
        np.repeat(state.uv[state.uv_faces], 2, axis=0),
        source,
        32,
    )
    with pytest.raises(ValueError, match="SHARED_VARIANT_UV_UNSUPPORTED"):
        bake_variant(request, tmp_path / "overlap", deadline=time.monotonic() + 10)
    assert not (tmp_path / "overlap").exists()


@pytest.mark.skipif(not DEFAULT_BLENDER.is_file(), reason="Real Blender dependency unavailable")
def test_real_blender_uses_original_material_factors_and_occlusion_strength(tmp_path: Path) -> None:
    state, source = source_fixture(tmp_path)
    data = source.read_bytes()
    old_size = struct.unpack_from("<I", data, 12)[0]
    document = json.loads(data[20 : 20 + old_size])
    material = document["materials"][0]
    material["occlusionTexture"]["strength"] = 0.5
    material["pbrMetallicRoughness"].update(metallicFactor=0.7, roughnessFactor=0.4)
    encoded = json.dumps(document).encode()
    encoded += b" " * (-len(encoded) % 4)
    tail = data[20 + old_size :]
    source.write_bytes(
        struct.pack("<4sII", b"glTF", 2, 20 + len(encoded) + len(tail))
        + struct.pack("<I4s", len(encoded), b"JSON")
        + encoded
        + tail
    )
    request = BakeVariantRequest(
        state.mesh.vertices, state.mesh.faces, state.uv[state.uv_faces], source, 32, 0.02, 4
    )
    bake_variant(request, tmp_path / "bake", deadline=time.monotonic() + 120)
    with Image.open(tmp_path / "bake/ORM.png") as image:
        actual = np.asarray(image)[21, 10, :3]
    assert actual == pytest.approx([0.65 * 255, 0.24 * 255, 0.14 * 255], abs=3)
