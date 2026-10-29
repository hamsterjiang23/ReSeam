"""Raster tests verify visibility, original-face picking, and actual color samples."""

import json
import time
from pathlib import Path

import numpy as np
import pytest
import trimesh
from PIL import Image
from trimesh.visual.material import PBRMaterial
from trimesh.visual.texture import TextureVisuals

from reseam.agent_tools.assets import import_asset, render_views
from reseam.agent_tools.state import save_snapshot
from reseam.budget_state import BudgetState
from reseam.mesh import make_mesh, state_from_corner_uv


def _overlap(path: Path) -> Path:
    vertices = np.array([[-1., -1, 0], [1, -1, 0], [0, 1, 0],
                         [-1, -1, .1], [1, -1, .1], [0, 1, .1]])
    mesh = make_mesh(vertices, np.array([[0, 1, 2], [3, 4, 5]]))
    corners = np.array([[[.1, .1], [.9, .1], [.5, .9]]] * 2)
    return save_snapshot(BudgetState(state_from_corner_uv(mesh, corners,
                         corner_ids=np.arange(6).reshape(2, 3))), path, {}).root


def test_zbuffer_and_pick_report_front_and_back_face(tmp_path: Path) -> None:
    parent = _overlap(tmp_path / "parent")
    output = tmp_path / "views"
    output.mkdir()
    result = render_views({"snapshot": str(parent), "size": 64, "seams": False,
                           "pick_pixels": {"front": [[32, 32]], "back": [[32, 32]]}},
                          output, time.monotonic() + 10)
    record = json.loads((output / "views.json").read_text(encoding="utf-8"))
    assert record["picked_faces"]["front"][0]["face_id"] == 1
    assert record["picked_faces"]["back"][0]["face_id"] == 0
    with np.load(output / "front_faces.npz") as data:
        assert int(data["face_ids"][32, 32]) == 1
        np.testing.assert_allclose(data["barycentric"][32, 32].sum(), 1)
    assert result["quality_evidence"] is False
    assert (output / "right.png").is_file()


def test_explicit_texture_uses_current_uv_and_original_face_ids(tmp_path: Path) -> None:
    parent = _overlap(tmp_path / "parent")
    texture = tmp_path / "red.png"
    Image.new("RGB", (4, 4), (255, 0, 0)).save(texture)
    output = tmp_path / "views"
    output.mkdir()
    render_views({"snapshot": str(parent), "size": 64, "mode": "basecolor", "texture": str(texture),
                  "selected_faces": [1], "seams": False}, output, time.monotonic() + 10)
    with Image.open(output / "front.png") as image:
        assert image.getpixel((32, 32)) == (255, 0, 0)
    with np.load(output / "front_faces.npz") as data:
        assert set(np.unique(data["face_ids"])) == {-1, 1}
    record = json.loads((output / "views.json").read_text(encoding="utf-8"))
    assert record["source"]["uv"] == "current_snapshot"
    assert record["selected_faces_only"] is True


def test_error_heatmap_is_face_bound(tmp_path: Path) -> None:
    parent = _overlap(tmp_path / "parent")
    output = tmp_path / "views"
    output.mkdir()
    render_views({"snapshot": str(parent), "size": 64, "mode": "errors", "error_faces": [1],
                  "seams": False}, output, time.monotonic() + 10)
    with Image.open(output / "front.png") as image:
        assert image.getpixel((32, 32)) == (235, 40, 45)
    with Image.open(output / "back.png") as image:
        assert image.getpixel((32, 32)) == (160, 178, 190)


def test_source_basecolor_preserves_face_corner_sampling(tmp_path: Path) -> None:
    source = tmp_path / "source.glb"
    mesh = trimesh.Trimesh(vertices=[[-1., -1, 0], [1, -1, 0], [0, 1, 0]],
                           faces=[[0, 1, 2]], process=False)
    mesh.visual = TextureVisuals(uv=np.array([[.1, .1], [.9, .1], [.5, .9]]),
                                material=PBRMaterial(baseColorTexture=Image.new("RGB", (4, 4), (12, 120, 245))))
    mesh.export(source, file_type="glb")
    imported = tmp_path / "import"
    imported.mkdir()
    result = import_asset({"path": str(source)}, imported, time.monotonic() + 10)
    output = tmp_path / "views"
    output.mkdir()
    render_views({"snapshot": result["snapshot"], "size": 64, "mode": "basecolor", "seams": False},
                 output, time.monotonic() + 10)
    with Image.open(output / "front.png") as image:
        assert image.getpixel((32, 32)) == (12, 120, 245)
    record = json.loads((output / "views.json").read_text(encoding="utf-8"))
    assert record["source"]["uv"] == "source_face_corner_uv"
    assert record["source"]["sha256"]


@pytest.mark.parametrize("fields", [{"selected_faces": [7]}, {"region_id": "missing"},
                                    {"pick_pixels": {"front": [[64, 0]]}}, {"size": True}])
def test_invalid_view_selection_is_rejected(tmp_path: Path, fields: dict[str, object]) -> None:
    parent = _overlap(tmp_path / "parent")
    output = tmp_path / "views"
    output.mkdir()
    with pytest.raises(ValueError):
        render_views({"snapshot": str(parent), "size": 64, **fields}, output, time.monotonic() + 10)
