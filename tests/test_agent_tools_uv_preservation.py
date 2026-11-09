"""Explicit static color binding, authored identities, and extra corner UV delivery."""

import time
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from PIL import Image

from reseam.agent_tools.assets import import_asset
from reseam.agent_tools.color_reuse import bake_color_reuse, freeze_target
from reseam.agent_tools.extra_uv import (
    append_extra_uv_glb,
    inherit_extra_uv,
    load_extra_uv,
    read_glb,
    write_extra_uv,
)
from reseam.agent_tools.material import export_asset
from reseam.agent_tools.state import file_hash, load_snapshot, read_object
from scripts.agent_fbx_uv import restore_loop_uv_ids


def _input(root: Path) -> tuple[Path, Path]:
    source = root / "panel.obj"
    source.write_text("v 0 0 0\nv 1 0 0\nv 0 1 0\nvt .1 .1\nvt .9 .1\nvt .1 .9\nf 1/1 2/2 3/3\n",
                      encoding="utf-8")
    texture = root / "color.png"
    Image.new("RGB", (16, 16), (205, 34, 72)).save(texture)
    return source, texture


def _folder(root: Path, name: str) -> Path:
    folder = root / name
    folder.mkdir()
    return folder


@pytest.mark.parametrize(("overrides", "error"), [
    ({"basecolor_texture": "relative.png", "uv_channel": 0}, "ABSOLUTE_PATH"),
    ({"basecolor_texture": "TEXTURE"}, "REQUIRES_UV_CHANNEL"),
    ({"basecolor_texture": "TEXTURE", "uv_channel": True}, "UV_CHANNEL_MUST"),
    ({"basecolor_texture": "TEXTURE", "uv_channel": -1}, "UV_CHANNEL_MUST"),
    ({"basecolor_texture": "TEXTURE", "uv_channel": ""}, "UV_CHANNEL_MUST"),
    ({"basecolor_texture": "TEXTURE", "uv_channel": 0, "reference": "ref.glb"}, "CONFLICTS_WITH_REFERENCE"),
])
def test_explicit_texture_input_validation(tmp_path: Path, overrides: dict[str, object], error: str) -> None:
    source, texture = _input(tmp_path)
    request = {"path": str(source), **{k: str(texture) if v == "TEXTURE" else v for k, v in overrides.items()}}
    with pytest.raises(ValueError, match=error):
        import_asset(request, _folder(tmp_path, "import"), time.monotonic() + 30)


def test_raw_uv_identity_keeps_control_points_and_conservatively_falls_back() -> None:
    polygons = [[0, 1, 2], [0, 2, 3], [4, 1, 2]]
    vertices = [v for p in polygons for v in p]
    uv = np.array([[0., 0.], [1., 0.], [0., 1.], [1., 1.]])
    indices = np.array([0, 1, 2, 0, 2, 3, 0, 1, 2])
    raw: dict[str, Any] = {"polygons": polygons, "layers": {"UV": {
        "mapping": "ByPolygonVertex", "reference": "IndexToDirect", "indices": indices, "uv": uv}}}
    identities, evidence = restore_loop_uv_ids(raw, "UV", polygons, vertices, uv[indices].tolist())
    assert evidence["exact"] is True
    assert identities[0] == identities[3]
    # Original vertices 0 and 4 may coincide spatially, but cannot share a UV node.
    assert identities[0] != identities[6]
    wrong_polygons = [[1, 0, 2], *polygons[1:]]
    fallback, evidence = restore_loop_uv_ids(raw, "UV", wrong_polygons, vertices, uv[indices].tolist())
    assert evidence["exact"] is False
    assert "ANCESTRY_MISMATCH" in str(evidence["reason"])
    assert fallback == list(range(len(vertices)))


def test_extra_uv_export_reimport_and_static_binding(tmp_path: Path) -> None:
    source, texture = _input(tmp_path)
    result = import_asset({"path": str(source), "basecolor_texture": str(texture), "uv_channel": 0},
                          _folder(tmp_path, "import"), time.monotonic() + 30)
    original = load_snapshot(Path(str(result["snapshot"])))
    binding = original.metadata["static_color_binding"]
    assert isinstance(binding, dict)
    assert binding["texture_sha256"] == file_hash(texture)
    assert binding["unity_material_reproduced"] is False
    layers = {"ArtistDetail": np.array([[[.02, .07], [.8, .12], [.19, .95]]]),
              "Mask": np.array([[[.1, .2], [.3, .4], [.5, .6]]])}
    metadata = dict(original.metadata)
    write_extra_uv(metadata, tmp_path / "extra", layers)
    reference = Path(str(metadata["reference_glb"]))
    # Write a distinct test GLB: never mutate the snapshot's frozen reference.
    glb = tmp_path / "two_extra.glb"
    glb.write_bytes(reference.read_bytes())
    report = append_extra_uv_glb(glb, metadata, 1)
    assert report["extra_uv_layers"] == 2
    document, _ = read_glb(glb)
    assert "TEXCOORD_2" in document["meshes"][0]["primitives"][0]["attributes"]
    imported = import_asset({"path": str(glb)}, _folder(tmp_path, "reimport"), time.monotonic() + 30)
    snapshot = load_snapshot(Path(str(imported["snapshot"])))
    actual = load_extra_uv(snapshot.metadata, 1)
    assert list(actual) == list(layers)
    for name, values in layers.items():
        np.testing.assert_allclose(actual[name], values, atol=5e-8, rtol=0)
    selected = import_asset({"path": str(glb), "basecolor_texture": str(texture), "uv_channel": 1},
                            _folder(tmp_path, "selected"), time.monotonic() + 30)
    selected_snapshot = load_snapshot(Path(str(selected["snapshot"])))
    surface = selected_snapshot.state.surface
    np.testing.assert_allclose(surface.uv[surface.uv_faces], layers["ArtistDetail"], atol=5e-8, rtol=0)
    assert set(load_extra_uv(selected_snapshot.metadata, 1)) == {"TEXCOORD_0", "Mask"}
    # The same extra corner contract also applies through BaseColor export.
    target = freeze_target({"snapshot": str(snapshot.root)}, _folder(tmp_path, "target"), time.monotonic() + 30)
    baked = bake_color_reuse({"snapshot": str(snapshot.root), "target": target["target"],
                             "resolution": 32, "padding_pixels": 1},
                            _folder(tmp_path, "bake"), time.monotonic() + 30)
    exported = export_asset({"snapshot": str(snapshot.root), "bake": baked["bake"], "mode": "diagnostic"},
                            _folder(tmp_path, "export"), time.monotonic() + 30)
    export_manifest = read_object(Path(str(exported["manifest"])))
    files = export_manifest["files"]
    assert isinstance(files, dict)
    assert files["asset.glb"] == file_hash(Path(str(exported["asset"])))
    again = import_asset({"path": exported["asset"]}, _folder(tmp_path, "export_reimport"), time.monotonic() + 30)
    final = load_snapshot(Path(str(again["snapshot"])))
    for name, values in load_extra_uv(final.metadata, 1).items():
        np.testing.assert_allclose(values, layers[name], atol=5e-8, rtol=0)


def test_extra_uv_variant_barycentric_inheritance_and_hash(tmp_path: Path) -> None:
    uv = np.array([[[.1, .2], [.9, .2], [.1, .8]]])
    metadata: dict[str, object] = {}
    write_extra_uv(metadata, tmp_path / "source", {"Second": uv})
    faces = np.array([0, 0], dtype=np.int64)
    bary = np.array([np.eye(3), [[1., 0, 0], [.5, .5, 0], [0, 0, 1.]]])
    inherited = inherit_extra_uv(metadata, tmp_path / "variant", faces, bary, 1)
    np.testing.assert_allclose(load_extra_uv(inherited, 2)["Second"], np.einsum("fck,fkj->fcj", bary, uv[faces]))
    assert load_extra_uv(metadata, 1)["Second"].shape == (1, 3, 2)
    extra = inherited["extra_uv"]
    assert isinstance(extra, dict)
    path = Path(str(extra["path"]))
    path.write_bytes(b"changed")
    with pytest.raises(ValueError, match="HASH_MISMATCH"):
        load_extra_uv(inherited, 2)
