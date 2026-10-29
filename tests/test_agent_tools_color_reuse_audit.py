"""Counterexamples for source identity and original-edge seam coverage."""

import json
import struct
import time
from pathlib import Path

import numpy as np
import pytest

from reseam.agent_tools.color_reuse import AtlasSignal, ColorTarget, _seams, freeze_target
from reseam.agent_tools.state import Snapshot, save_snapshot
from reseam.budget_material import atlas_from_arrays, export_material_glb
from reseam.budget_state import BudgetState
from reseam.budget_texture import encode_rgba8
from reseam.mesh import make_mesh, state_from_corner_uv


def _deadline() -> float:
    return time.monotonic() + 30


def _split_source(
    root: Path, *, nonmanifold: bool = False, continuous_uv: bool = False,
) -> tuple[Snapshot, Path]:
    vertices = np.array([[0., 0, 0], [1., 0, 0], [1., 1, 0], [0., 1, 0], [.5, 0, 1]])
    faces = np.array([[0, 1, 2], [0, 2, 3]], dtype=np.int64)
    if nonmanifold:
        faces = np.array([[0, 1, 2], [1, 0, 3], [0, 1, 4]], dtype=np.int64)
    corner_uv = np.tile(np.array([[.05, .1], [.25, .1], [.2, .3]]), (len(faces), 1, 1))
    corner_uv[:, :, 0] += np.arange(len(faces))[:, None] * .3
    corner_ids = np.arange(len(faces) * 3).reshape(-1, 3)
    if continuous_uv:
        corner_uv = (.1 + .5 * vertices[:, :2])[faces]
        # Triangulating one polygon reuses the original polygon's loop IDs.
        corner_ids = faces.copy()
    surface = state_from_corner_uv(
        make_mesh(vertices[faces].reshape(-1, 3), np.arange(len(faces) * 3).reshape(-1, 3)),
        corner_uv, seam_ids=frozenset(),
    )
    color = np.full((32, 32, 4), [.7, .1, .1, 1.])
    atlas = atlas_from_arrays(color, np.full((32, 32, 3), [0., 0, 1]),
                              np.full((32, 32, 3), [1., 1., 0]))
    source = root / "source.glb"
    export_material_glb(surface, atlas, source)
    snapshot = save_snapshot(BudgetState(surface), root / "snapshot", {"reference_glb": str(source)})
    ancestry = root / "original.npz"
    np.savez(ancestry, vertices=vertices, faces=faces, corner_uv=corner_uv, corner_ids=corner_ids)
    return snapshot, ancestry


def _freeze(root: Path, snapshot: Snapshot, ancestry: Path | None = None) -> ColorTarget:
    folder = root / "target"
    folder.mkdir()
    request: dict[str, object] = {"snapshot": str(snapshot.root), "policy": "source_original"}
    if ancestry is not None:
        request["original_topology"] = str(ancestry)
    result = freeze_target(request, folder, _deadline())
    return ColorTarget(snapshot, Path(str(result["target"])))


def _add_green_vertex_color(path: Path) -> None:
    raw = path.read_bytes()
    json_length = struct.unpack_from("<I", raw, 12)[0]
    document = json.loads(raw[20:20 + json_length])
    binary_start = 20 + json_length
    binary_length = struct.unpack_from("<I", raw, binary_start)[0]
    binary = raw[binary_start + 8:binary_start + 8 + binary_length]
    primitive = document["meshes"][0]["primitives"][0]
    count = document["accessors"][primitive["attributes"]["POSITION"]]["count"]
    colors = np.tile(np.array([0., 1., 0., 1.], dtype="<f4"), (count, 1)).tobytes()
    view_index, accessor_index = len(document["bufferViews"]), len(document["accessors"])
    document["bufferViews"].append({"buffer": 0, "byteOffset": len(binary), "byteLength": len(colors)})
    document["accessors"].append({"bufferView": view_index, "componentType": 5126,
                                  "count": count, "type": "VEC4"})
    primitive["attributes"]["COLOR_0"] = accessor_index
    binary += colors
    document["buffers"][0]["byteLength"] = len(binary)
    encoded = json.dumps(document, separators=(",", ":")).encode()
    encoded += b" " * (-len(encoded) % 4)
    path.write_bytes(struct.pack("<III", 0x46546C67, 2, 28 + len(encoded) + len(binary))
                     + struct.pack("<II", len(encoded), 0x4E4F534A) + encoded
                     + struct.pack("<II", len(binary), 0x004E4942) + binary)


def test_nontrivial_source_vertex_color_cannot_be_silently_ignored(tmp_path: Path) -> None:
    snapshot, _ = _split_source(tmp_path)
    _add_green_vertex_color(tmp_path / "source.glb")
    with pytest.raises(ValueError, match="COLOR|color"):
        _freeze(tmp_path, snapshot)


def test_original_edge_is_checked_after_canonical_attribute_split(tmp_path: Path) -> None:
    snapshot, ancestry = _split_source(tmp_path)
    assert not snapshot.state.surface.seams
    assert all(len(faces) == 1 for faces in snapshot.state.surface.mesh.edge_faces)
    target = _freeze(tmp_path, snapshot, ancestry)
    assert target.edge_pairs.shape == (1, 8)
    assert target.edge_pairs[0].tolist() == [0, 2, 0, 0, 2, 1, 0, 1]


def test_original_split_seam_detects_one_sided_color_error(tmp_path: Path) -> None:
    snapshot, ancestry = _split_source(tmp_path)
    target = _freeze(tmp_path, snapshot, ancestry)
    color = np.full((256, 256, 4), [.7, .1, .1, 1.])
    color[:, 77:, :3] = [.1, .1, .7]
    report = _seams(AtlasSignal(target, snapshot.state.surface), snapshot.state.surface,
                    [encode_rgba8(color)], [0], {"seam_mean": .025, "seam_max": .2}, _deadline())
    assert report["evaluated_seam_pairs"] == 1
    assert report["samples"] == 3
    assert report["valid"] is False


def test_equal_wrong_seam_colors_cannot_cancel_in_residual_jump(tmp_path: Path) -> None:
    snapshot, ancestry = _split_source(tmp_path)
    target = _freeze(tmp_path, snapshot, ancestry)
    surface = snapshot.state.surface
    size = 256
    color = np.full((size, size, 4), [.7, .1, .1, 1.])
    yy, xx = np.indices((size, size))
    pixels = np.stack(((xx + .5) / size, 1 - (yy + .5) / size), axis=2)
    for face, corners in ((0, [0, 2]), (1, [0, 1])):
        segment = surface.uv[surface.uv_faces[face]][corners]
        edge = segment[1] - segment[0]
        along = np.clip(((pixels - segment[0]) @ edge) / (edge @ edge), 0, 1)
        closest = segment[0] + along[..., None] * edge
        narrow_band = np.linalg.norm(pixels - closest, axis=2) < 2 / size
        color[narrow_band, :3] = 0.
    report = _seams(AtlasSignal(target, surface), surface, [encode_rgba8(color)], [0],
                    {"seam_mean": .025, "seam_max": .2}, _deadline())
    assert report["evaluated_seam_pairs"] == 1
    assert report["valid"] is False


def test_original_coincident_but_distinct_vertex_ids_are_not_welded(tmp_path: Path) -> None:
    snapshot, ancestry = _split_source(tmp_path)
    surface = snapshot.state.surface
    np.savez(ancestry, vertices=surface.mesh.vertices, faces=surface.mesh.faces,
             corner_uv=surface.uv[surface.uv_faces], corner_ids=np.arange(6).reshape(2, 3))
    target = _freeze(tmp_path, snapshot, ancestry)
    assert target.edge_pairs.shape == (0, 8)


def test_triangulated_polygon_may_reuse_consistent_original_loop_ids(tmp_path: Path) -> None:
    snapshot, ancestry = _split_source(tmp_path, continuous_uv=True)
    target = _freeze(tmp_path, snapshot, ancestry)
    assert target.edge_pairs.shape == (1, 8)


def test_ancestry_uv_witness_uses_original_source_not_candidate_atlas(tmp_path: Path) -> None:
    original, ancestry = _split_source(tmp_path)
    state = original.state.copy()
    state.surface.uv[:] = state.surface.uv * .5 + .2
    candidate = save_snapshot(state, tmp_path / "candidate", original.metadata, original)
    target = _freeze(tmp_path, candidate, ancestry)
    assert target.edge_pairs.shape == (1, 8)


def test_nonmanifold_edge_keeps_every_incidence_pair_with_endpoint_identity(tmp_path: Path) -> None:
    snapshot, ancestry = _split_source(tmp_path, nonmanifold=True)
    target = _freeze(tmp_path, snapshot, ancestry)
    assert target.edge_pairs.shape == (3, 8)
    assert {tuple(row[[2, 5]]) for row in target.edge_pairs} == {(0, 1), (0, 2), (1, 2)}
    with np.load(ancestry) as data:
        faces = data["faces"]
    for a, b, first, a0, b0, second, a1, b1 in target.edge_pairs:
        assert [a, b] == [0, 1]
        assert faces[first, [a0, b0]].tolist() == [a, b]
        assert faces[second, [a1, b1]].tolist() == [a, b]


@pytest.mark.parametrize("change", ["position", "uv", "face_order", "invalid_index"])
def test_incompatible_original_topology_is_rejected(tmp_path: Path, change: str) -> None:
    snapshot, ancestry = _split_source(tmp_path)
    with np.load(ancestry) as data:
        arrays = {key: data[key].copy() for key in data.files}
    if change == "position":
        arrays["vertices"][0, 0] += .1
    elif change == "uv":
        arrays["corner_uv"][0, 0, 0] += .1
    elif change == "face_order":
        arrays["faces"] = arrays["faces"][::-1]
    else:
        arrays["faces"][0, 0] = len(arrays["vertices"])
    np.savez(ancestry, **arrays)
    with pytest.raises(ValueError):
        _freeze(tmp_path, snapshot, ancestry)
