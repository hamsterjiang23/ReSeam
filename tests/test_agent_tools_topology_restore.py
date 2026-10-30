"""Source-index restoration must preserve geometry and authored UV identities."""

import json
import time
from pathlib import Path
from typing import cast

import numpy as np
import pytest
import trimesh
from PIL import Image

from reseam.agent_tools.assets import import_asset
from reseam.agent_tools.state import Snapshot, file_hash, load_snapshot, object_hash
from reseam.agent_tools.topology_restore import restore_import_topology
from reseam.mesh import chart_labels, make_mesh, state_from_corner_uv
from reseam.types import FloatArray, IntArray
from reseam.validation import topology_errors


def _deadline() -> float:
    return time.monotonic() + 30


def _fixture(
    root: Path, vertices: FloatArray | None = None, faces: IntArray | None = None,
) -> tuple[Snapshot, Path, Path]:
    root.mkdir(parents=True, exist_ok=True)
    vertices = (np.array([[0., 0, 0], [1., 0, 0], [1., 1, 0], [0., 1, 0], [.5, .5, 0]])
                if vertices is None else vertices)
    faces = (np.array([[0, 1, 4], [1, 2, 4], [2, 3, 4], [3, 0, 4]], dtype=np.int64)
             if faces is None else faces)
    corner_uv = np.tile(np.array([[.05, .05], [.15, .05], [.1, .15]]), (len(faces), 1, 1))
    corner_uv[:, :, 0] += np.arange(len(faces))[:, None] * .12
    mesh = trimesh.Trimesh(vertices=vertices[faces].reshape(-1, 3),
                           faces=np.arange(3 * len(faces)).reshape(-1, 3), process=False)
    material = trimesh.visual.material.PBRMaterial(baseColorTexture=Image.new("RGBA", (4, 4), "white"))
    mesh.visual = trimesh.visual.texture.TextureVisuals(uv=corner_uv.reshape(-1, 2), material=material)
    source = root / "canonical.glb"
    source.write_bytes(trimesh.Scene(mesh).export(file_type="glb"))
    output = root / "parent"
    output.mkdir()
    result = import_asset({"path": str(source)}, output, _deadline())
    parent = load_snapshot(Path(str(result["snapshot"])))
    ancestry = root / "original.npz"
    np.savez(ancestry, vertices=vertices, faces=faces, corner_uv=corner_uv,
             corner_ids=np.arange(len(faces) * 3).reshape(-1, 3))
    return parent, ancestry, source


def _restore(root: Path, parent: Snapshot, ancestry: Path, source: Path,
             regions: list[list[int]]) -> Snapshot:
    root.mkdir()
    result = import_asset({"path": str(source), "source_parent_snapshot": str(parent.root),
                           "original_topology": str(ancestry), "restore_regions": regions}, root, _deadline())
    return load_snapshot(Path(str(result["snapshot"])))


def test_attribute_split_patch_restores_connectivity_without_changing_uv_or_positions(tmp_path: Path) -> None:
    parent, ancestry, source = _fixture(tmp_path)
    source_hashes = file_hash(source), file_hash(ancestry), file_hash(parent.root / "arrays.npz")
    result = _restore(tmp_path / "variant", parent, ancestry, source, [[0, 1, 2, 3]])
    old, new = parent.state.surface, result.state.surface
    assert len(old.mesh.vertices) == 12
    assert len(new.mesh.vertices) == 5
    assert sum(len(entries) == 2 for entries in new.mesh.edge_faces) == 4
    assert new.mesh.vertices[new.mesh.faces].tobytes() == old.mesh.vertices[old.mesh.faces].tobytes()
    assert np.array_equal(new.uv, old.uv) and np.array_equal(new.uv_faces, old.uv_faces)
    assert np.array_equal(chart_labels(new), chart_labels(old))
    assert len(new.seams) == 4
    assert not topology_errors(new)
    record = json.loads((result.root / "snapshot.json").read_text())
    assert record["parent_hash"] is None
    assert result.asset_id != parent.asset_id
    restoration = cast(dict[str, object], result.metadata["topology_restoration"])
    assert restoration["source_parent_state_hash"] == parent.state_hash
    assert restoration["face_corner_geometry_unchanged"] is True
    assert restoration["uv_values_and_copy_ids_unchanged"] is True
    assert file_hash(Path(str(restoration["original_topology"]))) == file_hash(ancestry)
    assert source_hashes == (file_hash(source), file_hash(ancestry), file_hash(parent.root / "arrays.npz"))


def test_regions_get_separate_boundaries_and_distinct_variant_ids(tmp_path: Path) -> None:
    parent, ancestry, source = _fixture(tmp_path)
    whole = _restore(tmp_path / "whole", parent, ancestry, source, [[0, 1, 2, 3]])
    split = _restore(tmp_path / "split", parent, ancestry, source, [[0, 1], [2, 3]])
    assert whole.asset_id != split.asset_id
    assert len(split.state.surface.mesh.vertices) == 8
    assert not set(split.state.surface.mesh.faces[:2].ravel()).intersection(
        split.state.surface.mesh.faces[2:].ravel())
    old_sources = cast(list[dict[str, object]], parent.metadata["source_manifest"])
    assert parent.asset_id == object_hash({"geometry": old_sources[0]["sha256"],
                                           "reference": old_sources[-1]["sha256"]})


def test_coincident_distinct_original_vertex_ids_remain_distinct(tmp_path: Path) -> None:
    vertices = np.array([[0., 0, 0], [1., 0, 0], [0., 1, 0], [0., 0, 0], [1., 0, 0], [0., 1, 0]])
    faces = np.array([[0, 1, 2], [3, 4, 5]], dtype=np.int64)
    parent, ancestry, source = _fixture(tmp_path, vertices, faces)
    restored = _restore(tmp_path / "variant", parent, ancestry, source, [[0], [1]])
    mesh = restored.state.surface.mesh
    assert len(mesh.vertices) == 6
    assert np.array_equal(mesh.vertices[:3], mesh.vertices[3:])
    assert not set(mesh.faces[0]).intersection(mesh.faces[1])


@pytest.mark.parametrize("regions", [[[0, 0]], [[0, 1], [1, 2]], [[0], [0]], [[]], [[True]], [[99]]])
def test_repeated_overlapping_or_invalid_regions_reject(tmp_path: Path, regions: list[list[int]]) -> None:
    parent, ancestry, source = _fixture(tmp_path)
    with pytest.raises(ValueError, match="RESTORE_REGION"):
        _restore(tmp_path / "variant", parent, ancestry, source, regions)
    assert not (tmp_path / "variant/snapshot").exists()


@pytest.mark.parametrize("kind", ["position", "uv", "ordering", "noninteger", "corner_identity"])
def test_invalid_authoritative_ancestry_rejects(tmp_path: Path, kind: str) -> None:
    parent, ancestry, source = _fixture(tmp_path)
    with np.load(ancestry) as data:
        arrays = {key: data[key].copy() for key in data.files}
    if kind == "position":
        arrays["vertices"][0, 0] += .01
    elif kind == "uv":
        arrays["corner_uv"][0, 0, 0] += .01
    elif kind == "ordering":
        arrays["faces"] = arrays["faces"][[1, 0, 2, 3]]
    elif kind == "noninteger":
        arrays["faces"] = arrays["faces"].astype(float)
    else:
        arrays["corner_ids"][0, 1] = arrays["corner_ids"][0, 0]
    np.savez(ancestry, **arrays)
    with pytest.raises(ValueError, match="RESTORE_ORIGINAL"):
        _restore(tmp_path / "variant", parent, ancestry, source, [[0, 1, 2, 3]])


def test_nonmanifold_source_region_rejects_even_when_canonical_is_split(tmp_path: Path) -> None:
    vertices = np.array([[0., 0, 0], [1., 0, 0], [0., 1, 0], [0., -1, 0], [0., 0, 1]])
    faces = np.array([[0, 1, 2], [1, 0, 3], [0, 1, 4]], dtype=np.int64)
    parent, ancestry, source = _fixture(tmp_path, vertices, faces)
    with pytest.raises(ValueError, match="RESTORE_NONMANIFOLD_REGION_EDGE"):
        _restore(tmp_path / "variant", parent, ancestry, source, [[0, 1, 2]])


def test_bowtie_vertex_and_inconsistent_orientation_reject(tmp_path: Path) -> None:
    vertices = np.array([[0., 0, 0], [1., 0, 0], [0., 1, 0], [-1., 0, 0], [0., -1, 0]])
    faces = np.array([[0, 1, 2], [0, 3, 4]], dtype=np.int64)
    parent, ancestry, source = _fixture(tmp_path / "bowtie", vertices, faces)
    with pytest.raises(ValueError, match="RESTORE_NONMANIFOLD_REGION_VERTEX"):
        _restore(tmp_path / "bowtie/variant", parent, ancestry, source, [[0, 1]])
    faces = np.array([[0, 1, 2], [0, 1, 4]], dtype=np.int64)
    parent, ancestry, source = _fixture(tmp_path / "orientation", vertices, faces)
    with pytest.raises(ValueError, match="RESTORE_INCONSISTENT_REGION_ORIENTATION"):
        _restore(tmp_path / "orientation/variant", parent, ancestry, source, [[0, 1]])


def test_parent_uv_edit_cannot_be_claimed_as_original_canonical_import(tmp_path: Path) -> None:
    from reseam.agent_tools.state import save_snapshot

    parent, ancestry, source = _fixture(tmp_path)
    changed = parent.state.copy()
    changed.surface.uv += .01
    edited = save_snapshot(changed, tmp_path / "edited", parent.metadata, parent=parent)
    with pytest.raises(ValueError, match="RESTORE_SOURCE_PARENT_GEOMETRY_OR_UV_IDENTITY_MISMATCH"):
        _restore(tmp_path / "variant", edited, ancestry, source, [[0, 1, 2, 3]])


def test_canonical_material_reference_filename_is_already_frozen(tmp_path: Path) -> None:
    parent, ancestry, source = _fixture(tmp_path)
    named = tmp_path / "material_reference.glb"
    named.write_bytes(source.read_bytes())
    restored = _restore(tmp_path / "variant", parent, ancestry, named, [[0, 1, 2, 3]])
    assert len(restored.state.surface.mesh.vertices) == 5
    assert file_hash(Path(str(restored.metadata["reference_glb"]))) == file_hash(named)


def test_canonical_coordinate_spread_is_exact_not_float_bridge_tolerance(tmp_path: Path) -> None:
    parent, ancestry, _ = _fixture(tmp_path)
    old = parent.state.surface
    vertices = old.mesh.vertices.copy()
    vertices[3, 0] += 1e-9
    changed = state_from_corner_uv(make_mesh(vertices, old.mesh.faces), old.uv[old.uv_faces],
                                   corner_ids=old.uv_faces)
    from reseam.agent_tools.topology_restore import _source_faces

    with pytest.raises(ValueError, match="RESTORE_CANONICAL_COPIES_NOT_EXACT"):
        _source_faces(ancestry, changed)


def test_boundary_crossing_existing_uv_copy_rejects(tmp_path: Path) -> None:
    from reseam.agent_tools.state import save_snapshot
    from reseam.budget_state import BudgetState

    vertices = np.array([[0., 0, 0], [1., 0, 0], [1., 1, 0], [0., 1, 0]])
    faces = np.array([[0, 1, 2], [0, 2, 3]], dtype=np.int64)
    surface = state_from_corner_uv(make_mesh(vertices, faces), vertices[faces, :2], corner_ids=faces)
    parent = save_snapshot(BudgetState(surface), tmp_path / "parent", {})
    original = tmp_path / "original.npz"
    np.savez(original, vertices=vertices, faces=faces, corner_uv=vertices[faces, :2], corner_ids=faces)
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    with pytest.raises(ValueError, match="RESTORE_BOUNDARY_CROSSES_EXISTING_UV_COPY"):
        restore_import_topology(surface, {"source_parent_snapshot": str(parent.root),
                                         "original_topology": str(original), "restore_regions": [[0]]},
                                inputs, _deadline())
