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
from reseam.agent_tools.color_reuse import ColorTarget, freeze_target
from reseam.agent_tools.geometry import apply_reuse, check_pair
from reseam.agent_tools.state import Snapshot, load_snapshot, object_hash, save_snapshot
from reseam.budget_state import BudgetState
from reseam.budget_texture import encode_rgba8
from reseam.joint_types import ReuseGroup
from reseam.mesh import make_mesh, state_from_corner_uv


def test_custom_top_camera_reveals_thin_surface_and_picks_original_edges(tmp_path: Path) -> None:
    mesh = make_mesh(np.array([[-1., 0, -1], [1., 0, -1], [0., 0, 1]]),
                     np.array([[0, 1, 2]]))
    corners = np.array([[[.1, .1], [.9, .1], [.5, .9]]])
    parent = save_snapshot(BudgetState(state_from_corner_uv(mesh, corners, corner_ids=np.array([[0, 1, 2]]))),
                           tmp_path / "parent", {})
    default = tmp_path / "default"
    default.mkdir()
    render_views({"snapshot": str(parent.root), "size": 64}, default, time.monotonic()+10)
    baseline = json.loads((default / "views.json").read_text())
    assert not any(baseline["visible_pixels"].values())
    output = tmp_path / "top"
    output.mkdir()
    cameras = [{"name": "top", "azimuth": 0, "elevation": 90},
               {"name": "underside", "azimuth": 35, "elevation": -60}]
    render_views({"snapshot": str(parent.root), "size": 64, "cameras": cameras,
                  "mode": "wireframe", "seam_edge_ids": [0, 1, 2],
                  "pick_pixels": {"top": [[32, 32]]}},
                 output, time.monotonic()+10)
    record = json.loads((output / "views.json").read_text())
    assert record["cameras"] == cameras
    assert all(count > 500 for count in record["visible_pixels"].values())
    picked = record["picked_faces"]["top"][0]
    assert picked["face_id"] == 0
    assert set(picked["nearby_edge_ids"]) == {0, 1, 2}
    assert sum(picked["barycentric"]) == pytest.approx(1)
    pixels = np.asarray(Image.open(output / "top.png"))
    assert np.any(np.all(pixels == [90, 30, 45], axis=-1))
    assert np.any(np.all(pixels == [190, 198, 210], axis=-1))


@pytest.mark.parametrize("cameras", [[], [{"name": "../escape", "azimuth": 0, "elevation": 0}],
    [{"name": "uv", "azimuth": 0, "elevation": 0}],
    [{"name": "top", "azimuth": True, "elevation": 0}],
    [{"name": "top", "azimuth": 0, "elevation": 91}],
    [{"name": "top", "azimuth": float("nan"), "elevation": 0}],
    [{"name": "top", "azimuth": 0, "elevation": 90}]*2,
    [{"name": "top", "azimuth": 0, "elevation": 90},
     {"name": "top_original", "azimuth": 0, "elevation": 90}]])
def test_invalid_custom_cameras_rejected(tmp_path: Path, cameras: object) -> None:
    parent = _overlap(tmp_path / "parent")
    output = tmp_path / "views"
    output.mkdir()
    with pytest.raises((ValueError, TypeError), match="CAMERA"):
        render_views({"snapshot": str(parent), "size": 64, "cameras": cameras},
                     output, time.monotonic()+10)
    assert not list(output.iterdir())


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


def test_owner_uv_diagram_labels_both_stacked_members_once(tmp_path: Path) -> None:
    parent = load_snapshot(_overlap(tmp_path / "parent"))
    state = parent.state.copy()
    state.groups = (ReuseGroup("stack", (0,), (1,), (1., 0., 0.), 0.),)
    shared = save_snapshot(state, tmp_path / "shared", parent.metadata, parent)
    output = tmp_path / "views"
    output.mkdir()
    render_views({"snapshot": str(shared.root), "size": 64, "uv_labels": "owners"},
                 output, time.monotonic()+10)
    record = json.loads((output / "views.json").read_text())
    assert record["uv_label_mode"] == "owners"
    assert record["uv_label_mapping"] == {"1": {"owner_id": 2, "faces": [0, 1]}}


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


@pytest.mark.parametrize("mode", ["groups", "regions"])
def test_bilateral_visible_components_repeat_traceable_label(tmp_path: Path, mode: str) -> None:
    vertices = np.array([[-2., -1, 0], [-1., -1, 0], [-1.5, 1, 0],
                         [1., -1, 0], [2., -1, 0], [1.5, 1, 0]])
    mesh = make_mesh(vertices, np.array([[0, 1, 2], [3, 4, 5]]))
    corners = np.array([[[.1, .1], [.9, .1], [.5, .9]]] * 2)
    group = ReuseGroup("bilateral_wings", (0,), (1,), (1., 0, 0), 0.)
    state = BudgetState(state_from_corner_uv(mesh, corners, corner_ids=np.arange(6).reshape(2, 3)),
                        groups=(group,) if mode == "groups" else ())
    parent = save_snapshot(state, tmp_path / "parent", {
        "regions": [{"region_id": "wing_region", "name": "wings", "faces": [0, 1]}],
    })
    output = tmp_path / "views"
    output.mkdir()
    render_views({"snapshot": str(parent.root), "size": 128, "mode": mode, "seams": False},
                  output, time.monotonic() + 10)
    record = json.loads((output / "views.json").read_text(encoding="utf-8"))
    label = 2 if mode == "groups" else 1
    annotations = [item for item in record["annotation_instances"]["front"] if item["label"] == label]
    assert len(annotations) == 2
    assert {tuple(item["visible_faces"]) for item in annotations} == {(0,), (1,)}
    assert min(item["pixel"][0] for item in annotations) < 64 < max(item["pixel"][0] for item in annotations)
    mapping = record["label_mapping"][str(label)]
    assert mapping["faces"] == [0, 1]
    assert mapping["chart_ids"] == [0, 1]
    if mode == "groups":
        assert mapping["shared_group_id"] == "bilateral_wings"
        assert mapping["member_faces"] == {"first": [0], "second": [1]}
    else:
        assert mapping["region_id"] == "wing_region"
    assert set(record["uv_label_mapping"]) == {"0", "1"}


def _frozen_fixture(root: Path) -> tuple[Snapshot, Path]:
    vertices = np.array([[-1., 0, 0], [-1, 1, 0], [-1, 1, 1], [-1, 0, 1],
                         [1., 0, 0], [1, 1, 0], [1, 1, 1], [1, 0, 1],
                         [-.5, 2, 0], [.5, 2, 0], [0, 3, 0]])
    faces = np.array([[0, 1, 2], [0, 2, 3], [4, 6, 5], [4, 7, 6], [8, 9, 10]], dtype=np.int64)
    corners = .1 + .25 * vertices[faces, 1:]
    corners[2:4, :, 0] += .5
    corners[2:4, :, 1] = .45 - corners[2:4, :, 1]
    corners[4] = [[.4, .7], [.6, .7], [.5, .9]]
    color = np.full((32, 32, 4), [.7, .1, .1, 1.])
    color[:, 16:] = [.1, .1, .7, 1.]
    color[:12] = [.1, .7, .1, 1.]
    mesh = trimesh.Trimesh(vertices=vertices[faces].reshape(-1, 3),
                           faces=np.arange(15).reshape(-1, 3), process=False)
    mesh.visual = TextureVisuals(uv=corners.reshape(-1, 2), material=PBRMaterial(
        baseColorTexture=Image.fromarray(encode_rgba8(color)), baseColorFactor=[128, 192, 240, 255]))
    source = root / "source.glb"
    mesh.export(source, file_type="glb")
    surface = state_from_corner_uv(make_mesh(vertices, faces), corners, seam_ids=frozenset())
    original = save_snapshot(BudgetState(surface), root / "original", {"reference_glb": str(source)})
    for name in ("check", "apply", "target"):
        (root / name).mkdir()
    checked = check_pair({"snapshot": str(original.root), "first_faces": [0, 1], "second_faces": [2, 3],
                          "profile": {"resolution": 128, "gap_pixels": 8, "border_pixels": 8}},
                         root / "check", time.monotonic() + 30)
    assert checked["status"] == "OK", checked
    applied = apply_reuse({"snapshot": str(original.root), "proposal": checked["proposal"]},
                          root / "apply", time.monotonic() + 30)
    shared = load_snapshot(Path(str(applied["snapshot"])))
    frozen = freeze_target({"snapshot": str(shared.root), "protected_faces": [4]},
                           root / "target", time.monotonic() + 30)
    return shared, Path(str(frozen["target"]))


def test_frozen_target_view_preserves_protected_signal_and_uses_exact_source_factors(tmp_path: Path) -> None:
    snapshot, target_path = _frozen_fixture(tmp_path)
    output = tmp_path / "views"
    output.mkdir()
    result = render_views({"snapshot": str(snapshot.root), "mode": "basecolor", "target": str(target_path),
                           "size": 64, "seams": False, "annotate": False}, output, time.monotonic() + 30)
    record = json.loads((output / "views.json").read_text(encoding="utf-8"))
    change = record["appearance_change"]
    errors = {item["face"]: item["max"] for item in change["per_face"]}
    assert errors[2] > .5 and errors[3] > .5 and errors[4] == 0
    assert change["protected_faces_selected"] == [4] and change["protected_faces_changed"] == []
    assert record["frozen_target"]["candidate_bake_read"] is False
    assert result["quality_evidence"] is False
    with np.load(output / "left_faces.npz") as data:
        y, x = np.argwhere(data["face_ids"] == 2)[0]
        bary = data["barycentric"][y, x].astype(float)[None, :]
    target = ColorTarget(snapshot, target_path)
    for suffix, expected in (("original", target.original(2, bary)), ("target", target.sample(2, bary))):
        with Image.open(output / f"left_{suffix}.png") as image:
            np.testing.assert_allclose(np.asarray(image, dtype=np.uint8)[int(y), int(x)],
                                       encode_rgba8(expected)[0, :3], atol=1)
    assert (output / "left.png").read_bytes() == (output / "left_target.png").read_bytes()
    assert (output / "front_original.png").read_bytes() == (output / "front_target.png").read_bytes()


def test_frozen_target_images_and_picking_ignore_repacked_current_uv(tmp_path: Path) -> None:
    snapshot, target_path = _frozen_fixture(tmp_path)
    changed = snapshot.state.copy()
    changed.surface.uv[:] = changed.surface.uv * .4 + .3
    repacked = save_snapshot(changed, tmp_path / "repacked", snapshot.metadata, snapshot)
    for name, state in (("before", snapshot), ("after", repacked)):
        output = tmp_path / name
        output.mkdir()
        render_views({"snapshot": str(state.root), "mode": "basecolor", "target": str(target_path),
                      "size": 64, "selected_faces": [2, 3], "seams": False, "annotate": False},
                     output, time.monotonic() + 30)
    for view in ("front", "back", "left", "right"):
        for suffix in ("original", "target", "difference"):
            assert (tmp_path / "before" / f"{view}_{suffix}.png").read_bytes() == (
                tmp_path / "after" / f"{view}_{suffix}.png").read_bytes()
        with np.load(tmp_path / "before" / f"{view}_faces.npz") as old, np.load(
            tmp_path / "after" / f"{view}_faces.npz") as new:
            assert np.array_equal(old["face_ids"], new["face_ids"])


def test_frozen_target_with_wrong_source_hash_is_rejected(tmp_path: Path) -> None:
    snapshot, target_path = _frozen_fixture(tmp_path)
    record = json.loads(target_path.read_text(encoding="utf-8"))
    record.pop("target_hash")
    record["source_sha256"] = "0" * 64
    record["target_hash"] = object_hash(record)
    target_path.write_text(json.dumps(record), encoding="utf-8")
    output = tmp_path / "views"
    output.mkdir()
    with pytest.raises(ValueError, match="TARGET_SOURCE_CHANGED"):
        render_views({"snapshot": str(snapshot.root), "mode": "basecolor", "target": str(target_path)},
                      output, time.monotonic() + 30)


def test_frozen_target_from_other_geometry_is_rejected(tmp_path: Path) -> None:
    _, target_path = _frozen_fixture(tmp_path)
    incompatible = _overlap(tmp_path / "other")
    output = tmp_path / "views"
    output.mkdir()
    with pytest.raises(ValueError, match="TARGET_ASSET_OR_GEOMETRY_MISMATCH"):
        render_views({"snapshot": str(incompatible), "mode": "basecolor", "target": str(target_path)},
                      output, time.monotonic() + 30)


@pytest.mark.parametrize("extra,reason", [({"mode": "groups"}, "TARGET_VIEW_REQUIRES_BASECOLOR"),
    ({"texture": "unused.png"}, "TARGET_AND_EXPLICIT_TEXTURE"), ({}, "missing-target")])
def test_target_request_never_falls_back_silently(tmp_path: Path, extra: dict[str, object], reason: str) -> None:
    snapshot = _overlap(tmp_path / "parent")
    output = tmp_path / "views"
    output.mkdir()
    error = FileNotFoundError if reason == "missing-target" else ValueError
    with pytest.raises(error, match=reason):
        render_views({"snapshot": str(snapshot), "mode": "basecolor", "target": str(tmp_path / "missing-target.json"),
                      **extra}, output, time.monotonic() + 10)


@pytest.mark.parametrize("fields", [{"selected_faces": [7]}, {"region_id": "missing"},
                                    {"pick_pixels": {"front": [[64, 0]]}}, {"size": True}])
def test_invalid_view_selection_is_rejected(tmp_path: Path, fields: dict[str, object]) -> None:
    parent = _overlap(tmp_path / "parent")
    output = tmp_path / "views"
    output.mkdir()
    with pytest.raises(ValueError):
        render_views({"snapshot": str(parent), "size": 64, **fields}, output, time.monotonic() + 10)
