"""Part assembly must preserve ancestry, placement and independent UV policy."""

import time
from pathlib import Path

import numpy as np
import pytest
import trimesh
from PIL import Image
from trimesh.visual.material import PBRMaterial
from trimesh.visual.texture import TextureVisuals

from reseam.agent_tools.color_reuse import _source
from reseam.agent_tools.parts import prepare_parts
from reseam.agent_tools.parts_material import ColorTile, atlas_tiles
from reseam.agent_tools.regions import define_region
from reseam.agent_tools.reparameterize import reparameterize
from reseam.agent_tools.state import file_hash, load_snapshot, read_object
from reseam.budget_texture import sample_rgba8
from reseam.types import FloatArray, IntArray


def scene_file(path: Path, textured: bool = True) -> Path:
    scene = trimesh.Scene()
    for name, dx, color in (("body", 0., (40, 130, 210, 255)), ("feature", 4., (230, 50, 40, 255))):
        mesh = trimesh.Trimesh(vertices=[[-1., 0, 0], [1., 0, 0], [1., 2, 0], [-1., 2, 0]],
                              faces=[[0, 1, 2], [0, 2, 3]], process=False)
        if textured:
            mesh.visual = TextureVisuals(uv=np.array([[0., 0], [1., 0], [1., 1], [0., 1]]),
                material=PBRMaterial(baseColorTexture=Image.new("RGBA", (8, 8), color)))
        transform = np.eye(4)
        transform[0, 3] = dx
        scene.add_geometry(mesh, node_name=name, geom_name=name, transform=transform)
    scene.export(path, file_type="glb")
    return path


def request_for(path: Path) -> dict[str, object]:
    return {"sources": [{"id": "dragon", "path": str(path)}], "placement": "source_world"}


def test_multi_object_source_atlas_and_negative_nonunit_transform(tmp_path: Path) -> None:
    path = scene_file(tmp_path / "source.glb")
    digest = file_hash(path)
    request = request_for(path)
    request["sources"] = [{"id": "dragon", "path": str(path),
                           "transform": [[-2., 0, 0, 0], [0, 3., 0, 0], [0, 0, 1., 0], [0, 0, 0, 1.]]}]
    result = prepare_parts(request, tmp_path / "result", time.monotonic()+30)
    snapshot = load_snapshot(Path(str(result["snapshot"])))
    reference, _, _ = _source(snapshot)
    assert result["part_count"] == 2
    assert file_hash(path) == digest
    np.testing.assert_array_equal(snapshot.state.surface.mesh.vertices.min(axis=0), [-10., 0, 0])
    assert len(snapshot.metadata["regions"]) == 2
    # Red remains on the translated feature, blue on the body after UV normalization.
    colors = sample_rgba8(reference.image, reference.corner_uv.mean(axis=1))
    assert colors[0, 2] > colors[0, 0]
    assert colors[2, 0] > colors[2, 2]
    with np.load(tmp_path / "result/part_lineage.npz") as lineage:
        np.testing.assert_array_equal(lineage["source_corner_barycentric"][0], np.eye(3)[[0, 2, 1]])


def test_local_half_mirror_leaves_other_part_and_builds_identical_control(tmp_path: Path) -> None:
    path = scene_file(tmp_path / "source.glb")
    request = request_for(path)
    request.update(allow_geometry_change=True, parts=[
        {"id": "body", "source": "dragon", "objects": ["body"], "geometry": {"mode": "mirror_half"},
         "uv_policy": "shared"},
        {"id": "feature", "source": "dragon", "objects": ["feature"], "protected_faces": [2]},
    ])
    result = prepare_parts(request, tmp_path / "result", time.monotonic()+30)
    snapshot = load_snapshot(Path(str(result["snapshot"])))
    control = load_snapshot(Path(str(result["unshared_snapshot"])))
    assert snapshot.state.groups
    assert not control.state.groups
    np.testing.assert_array_equal(snapshot.state.surface.mesh.faces, control.state.surface.mesh.faces)
    np.testing.assert_array_equal(snapshot.state.surface.mesh.vertices, control.state.surface.mesh.vertices)
    part = snapshot.metadata["parts"][1]
    faces = part["faces"]
    expected = np.array([[[3., 0, 0], [5., 0, 0], [5., 2, 0]], [[3., 0, 0], [5., 2, 0], [3., 2, 0]]])
    np.testing.assert_array_equal(snapshot.state.surface.mesh.vertices[snapshot.state.surface.mesh.faces[faces]], expected)
    assert snapshot.metadata["regions"][1]["protected_faces"] == [faces[0]]
    _source(snapshot)  # source has exactly the new face/corner geometry, including clipped triangles


def test_mirror_from_uses_master_texture_and_records_replaced_selection(tmp_path: Path) -> None:
    path = scene_file(tmp_path / "source.glb")
    request = request_for(path)
    request.update(allow_geometry_change=True, parts=[
        {"id": "master", "source": "dragon", "objects": ["body"]},
        {"id": "copy", "source": "dragon", "objects": ["feature"],
         "geometry": {"mode": "mirror_from", "master_part": "master", "axis": "x", "offset": 2.},
         "uv_policy": "shared"}])
    result = prepare_parts(request, tmp_path / "result", time.monotonic()+30)
    snapshot = load_snapshot(Path(str(result["snapshot"])))
    assert len(snapshot.state.groups) == 1
    assert snapshot.metadata["parts"][1]["replaced_input_faces"] == [2, 3]
    reference, _, _ = _source(snapshot)
    np.testing.assert_array_equal(reference.corner_uv[:2], reference.corner_uv[2:][:, [0, 2, 1]])


@pytest.mark.parametrize(("patch", "reason"), [
    ({"placement": None}, "PLACEMENT"),
    ({"parts": [{"id": "one", "source": "dragon", "objects": ["body"]}]}, "UNASSIGNED"),
    ({"parts": [{"id": "one", "source": "dragon", "objects": ["absent"]}]}, "UNKNOWN"),
    ({"parts": [{"id": "one", "source": "dragon", "objects": ["body", "feature"],
                 "geometry": {"mode": "mirror_half"}}]}, "EXPLICIT_GEOMETRY_CHANGE"),
])
def test_invalid_request_preserves_source_and_publishes_no_snapshot(
    tmp_path: Path, patch: dict[str, object], reason: str,
) -> None:
    path = scene_file(tmp_path / "source.glb")
    digest = file_hash(path)
    with pytest.raises(ValueError, match=reason):
        prepare_parts({**request_for(path), **patch}, tmp_path / "result", time.monotonic()+30)
    assert file_hash(path) == digest
    assert not (tmp_path / "result/snapshot").exists()


def test_multiple_files_and_explicit_geometry_only_missing_uv(tmp_path: Path) -> None:
    first = scene_file(tmp_path / "first.glb", textured=False)
    second = scene_file(tmp_path / "second.glb", textured=False)
    request = {"sources": [{"id": "first", "path": str(first)},
                           {"id": "second", "path": str(second),
                            "transform": [[1, 0, 0, 20], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]]}],
               "placement": "source_world", "material_scope": "geometry"}
    result = prepare_parts(request, tmp_path / "result", time.monotonic()+30)
    assert result["part_count"] == 4
    assert result["reference_glb"] is None
    assert read_object(Path(str(result["report"])))["scope"] == "geometry"
    with pytest.raises(ValueError, match="UV_MISSING"):
        prepare_parts(request_for(first), tmp_path / "rejected", time.monotonic()+30)


def test_multiple_sources_with_flattened_references(tmp_path: Path) -> None:
    source = scene_file(tmp_path / "source.glb")
    scene = trimesh.load_scene(source, process=False)
    # A standard single-geometry material reference with the same face order.
    meshes = []
    for node in sorted(scene.graph.nodes_geometry):
        transform, name = scene.graph[node]
        mesh = scene.geometry[name].copy()
        mesh.apply_transform(transform)
        meshes.append(mesh)
    reference = trimesh.util.concatenate(meshes)
    reference.visual = TextureVisuals(uv=np.tile([[0., 0], [1., 0], [1., 1], [0., 1]], (2, 1)),
        material=PBRMaterial(baseColorTexture=Image.new("RGBA", (8, 8), (20, 50, 180, 255))))
    ref = tmp_path / "reference.glb"
    reference.export(ref, file_type="glb")
    result = prepare_parts({"sources": [{"id": name, "path": str(source), "reference": str(ref)}
                                        for name in ("first", "second")], "placement": "source_world"},
                           tmp_path / "result", time.monotonic()+30)
    assert result["part_count"] == 4
    _source(load_snapshot(Path(str(result["snapshot"]))))


def test_identical_source_textures_share_one_atlas_tile() -> None:
    pixels = np.full((16, 16, 4), 255, dtype=np.uint8)
    corners = np.array([[[0., 0], [1., 0], [0., 1]]])
    tiles = [ColorTile(pixels.copy(), corners.copy(), True, {}) for _ in range(9)]
    atlas, uv, records = atlas_tiles(tiles, time.monotonic()+10)
    assert atlas.size == (18, 18)
    assert len(records) == 9
    np.testing.assert_array_equal(uv[0], uv[-1])


def test_projected_reference_tracks_retained_sources_after_half_mirror(tmp_path: Path) -> None:
    source = scene_file(tmp_path / "source.glb")
    # Obtain a single-geometry reference from the ordinary normalization path.
    plain = prepare_parts(request_for(source), tmp_path / "plain", time.monotonic()+30)
    request = request_for(source)
    request.update(material_scope="geometry", projected_reference=plain["reference_glb"],
                   allow_geometry_change=True, parts=[
        {"id": "body", "source": "dragon", "objects": ["body"],
         "geometry": {"mode": "mirror_half"}, "uv_policy": "shared"},
        {"id": "feature", "source": "dragon", "objects": ["feature"]}])
    result = prepare_parts(request, tmp_path / "result", time.monotonic()+30)
    snapshot = load_snapshot(Path(str(result["snapshot"])))
    metadata = snapshot.metadata["projected_source"]
    with np.load(metadata["lineage_npz"]) as lineage:
        positions = np.einsum("fci,fij->fcj", lineage["source_low_corner_barycentric"],
                              lineage["original_low_triangles"][lineage["source_low_faces"]])
    with np.load(tmp_path / "result/part_lineage.npz") as lineage:
        positions[lineage["reflected"], :, 0] *= -1
    np.testing.assert_allclose(positions, snapshot.state.surface.mesh.vertices[snapshot.state.surface.mesh.faces])


def test_segmentation_assigns_labels_without_replacing_source_geometry_or_uv(tmp_path: Path) -> None:
    path = scene_file(tmp_path / "source.glb")
    plain = prepare_parts(request_for(path), tmp_path / "plain", time.monotonic()+30)
    result = prepare_parts({"sources": [{"id": "original", "snapshot": plain["snapshot"]}],
                            "placement": "source_world", "segmentation_reference": str(path)},
                           tmp_path / "labeled", time.monotonic()+30)
    old = load_snapshot(Path(str(plain["snapshot"])))
    new = load_snapshot(Path(str(result["snapshot"])))
    np.testing.assert_array_equal(new.state.surface.mesh.vertices[new.state.surface.mesh.faces],
                                  old.state.surface.mesh.vertices[old.state.surface.mesh.faces])
    np.testing.assert_array_equal(new.state.surface.uv[new.state.surface.uv_faces],
                                  old.state.surface.uv[old.state.surface.uv_faces])
    report = read_object(tmp_path / "labeled/segmentation_transfer.json")
    assert report["maximum_distance"] < 1e-12
    assert report["boundary_disputed_faces"] == []
    assert result["part_count"] == 2


def test_snapshot_roundtrip_keeps_protected_source_faces(tmp_path: Path) -> None:
    source = scene_file(tmp_path / "source.glb")
    prepared = prepare_parts(request_for(source), tmp_path / "plain", time.monotonic()+30)
    protected = define_region({"snapshot": prepared["snapshot"], "name": "eye", "faces": [0],
                               "role": "protected"}, tmp_path / "protect", time.monotonic()+30)
    result = prepare_parts({"sources": [{"id": "original", "snapshot": protected["snapshot"]}],
                            "placement": "source_world"}, tmp_path / "again", time.monotonic()+30)
    snapshot = load_snapshot(Path(str(result["snapshot"])))
    assert result["part_count"] == 2
    assert snapshot.metadata["regions"][0]["protected_faces"] == [0]
    with pytest.raises(ValueError, match="PROTECTED_FACES"):
        prepare_parts({"sources": [{"id": "original", "snapshot": protected["snapshot"]}],
                       "placement": "source_world", "allow_geometry_change": True, "parts": [
                           {"id": "all", "source": "original", "faces": [0, 1, 2, 3],
                            "geometry": {"mode": "mirror_half"}}]}, tmp_path / "reject", time.monotonic()+30)


def test_regional_rebuild_can_defer_global_packing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = scene_file(tmp_path / "source.glb")
    prepared = prepare_parts(request_for(source), tmp_path / "plain", time.monotonic()+30)

    def forbidden_pack(*args: object, **kwargs: object) -> None:
        raise AssertionError("global packing must happen after all regional rebuilds")

    monkeypatch.setattr("reseam.agent_tools.geometry._packed", forbidden_pack)
    result = reparameterize({"snapshot": prepared["snapshot"], "faces": [0, 1], "backend": "harmonic",
                             "pack_after": False, "acceptance_policy": "visual"},
                            tmp_path / "rebuild", time.monotonic()+30)
    assert result["requires_pack"] is True
    assert result["geometry_passed"] is None
    old = load_snapshot(Path(str(prepared["snapshot"])))
    new = load_snapshot(Path(str(result["snapshot"])))
    np.testing.assert_array_equal(new.state.surface.mesh.vertices, old.state.surface.mesh.vertices)
    np.testing.assert_array_equal(new.state.surface.mesh.faces, old.state.surface.mesh.faces)
    assert new.metadata["requires_pack"] is True


def test_nonfinite_regional_backend_never_publishes_a_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = scene_file(tmp_path / "source.glb")
    prepared = prepare_parts(request_for(source), tmp_path / "plain", time.monotonic()+30)

    def broken_uv(*args: object, **kwargs: object) -> tuple[FloatArray, IntArray, list[int]]:
        return np.full((2, 3, 2), np.nan), np.arange(6).reshape(2, 3), []

    monkeypatch.setattr("reseam.agent_tools.reparameterize.harmonic_uv", broken_uv)
    with pytest.raises(ValueError, match="NONFINITE_OR_ZERO_AREA"):
        reparameterize({"snapshot": prepared["snapshot"], "faces": [0, 1], "backend": "harmonic",
                        "pack_after": False, "acceptance_policy": "visual"},
                       tmp_path / "failed", time.monotonic()+30)
    assert not (tmp_path / "failed/snapshot").exists()
