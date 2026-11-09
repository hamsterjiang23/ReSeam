"""Hard-normal provenance, diagonal exclusion and explicit mirror source mapping."""

from pathlib import Path
from typing import cast

import numpy as np
import pytest
from PIL import Image

from reseam.agent_tools.hard_edges import add_surface_cues, classify_edges, patch_plan, source_fields
from reseam.agent_tools.mesh_query import index_mesh
from reseam.agent_tools.regional_cut import cut_region
from reseam.agent_tools.state import Snapshot, file_hash, save_snapshot
from reseam.budget_state import BudgetState
from reseam.mesh import make_mesh, state_from_corner_uv


def panel(root: Path, *, crease: bool = False, enriched: bool = True, diagonal: bool = False) -> Snapshot:
    vertices = np.asarray([[0., 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 1. if crease else 0.]])
    faces = np.asarray([[0, 1, 2], [0, 2, 3]])
    uv = np.asarray([[[0., 0], [1, 0], [1, 1]], [[0., 0], [1, 1], [0, 1]]])
    surface = state_from_corner_uv(make_mesh(vertices, faces), uv, corner_ids=faces)
    metadata: dict[str, object] = {"polygon_topology": {"triangle_to_polygon": [0, 0] if diagonal else [0, 1]}}
    if enriched:
        normals = np.tile([0., 0, 1], (2, 3, 1))
        if crease:
            normals[1] = [0., 1, 0]
        metadata["hard_surface_source"] = {"corner_normals": normals.tolist(),
            "objects": [{"face_start": 0, "face_count": 2, "has_custom_normals": True}],
            "sharp_vertex_edges": []}
    return save_snapshot(BudgetState(surface), root, metadata)


def test_smooth_and_hard_normal_edges_are_distinct_and_read_only(tmp_path: Path) -> None:
    smooth = panel(tmp_path / "smooth")
    assert classify_edges(smooth, 45, .1)["edge_ids"] == []
    hard = panel(tmp_path / "hard", crease=True)
    before = file_hash(hard.root / "snapshot.json")
    rows = cast(list[dict[str, object]], classify_edges(hard, 120, .1)["edges"])
    row = next(row for row in rows if row["vertices"] == [0, 2])
    assert row["reasons"] == ["imported_custom_normal_discontinuity"]
    assert file_hash(hard.root / "snapshot.json") == before


def test_polygon_diagonal_is_not_a_hard_edge_even_with_imported_split_normals(tmp_path: Path) -> None:
    source = panel(tmp_path / "source", crease=True, diagonal=True)
    report = classify_edges(source, 20, .1)
    assert report["edge_ids"] == []
    assert len(cast(list[int], report["excluded_triangulation_diagonals"])) == 1


def test_legacy_snapshot_uses_explicitly_labeled_geometry_proxy(tmp_path: Path) -> None:
    source = panel(tmp_path / "source", crease=True, enriched=False)
    rows = cast(list[dict[str, object]], classify_edges(source, 20, .1)["edges"])
    row = next(row for row in rows if row["vertices"] == [0, 2])
    assert row["reasons"] == ["geometric_dihedral_proxy"]
    assert row["source_normal_angle_degrees"] is None


def test_source_enrichment_requires_exact_indexed_geometry(tmp_path: Path) -> None:
    old = panel(tmp_path / "old", enriched=False)
    source = panel(tmp_path / "new")
    normals, _, _, evidence = source_fields(old, source)
    assert normals is not None
    assert evidence["source_state_hash"] == source.state_hash
    # Different geometry must never inherit those normals by position proximity.
    wrong = panel(tmp_path / "wrong", crease=True)
    with pytest.raises(ValueError, match="GEOMETRY_MISMATCH"):
        source_fields(old, wrong)


def test_mirror_lineage_interpolates_normals_without_guessing_identity(tmp_path: Path) -> None:
    source = panel(tmp_path / "source", crease=True)
    vertices = np.concatenate((source.state.surface.mesh.vertices,
                               source.state.surface.mesh.vertices + [3., 0, 0]))
    faces = np.concatenate((source.state.surface.mesh.faces, source.state.surface.mesh.faces + 4))
    surface = state_from_corner_uv(make_mesh(vertices, faces), vertices[faces, :2], corner_ids=faces)
    lineage = tmp_path / "source_correspondence.npz"
    np.savez_compressed(lineage, source_face_ids=[0, 1, 0, 1], source_barycentric=np.tile(np.eye(3), (4, 1, 1)),
                        face_pairs=[[0, 2], [1, 3]])
    mirrored = save_snapshot(BudgetState(surface), tmp_path / "mirror", {
        "source_correspondence": str(lineage),
        "source_manifest": [{"path": str(lineage), "sha256": file_hash(lineage)}],
        "derived_from": {"snapshot": str(source.root), "state_hash": source.state_hash}})
    normals, polygons, sides, evidence = source_fields(mirrored)
    assert normals is not None and polygons is not None
    np.testing.assert_array_equal(normals[0], normals[2])
    np.testing.assert_array_equal(polygons, [0, 1, 0, 1])
    np.testing.assert_array_equal(sides, [0, 0, 1, 1])
    assert len(cast(list[object], evidence["lineage"])) == 1
    # The source manifest is rechecked before evidence can be used.
    lineage.write_bytes(b"changed")
    with pytest.raises(ValueError, match="HASH"):
        source_fields(mirrored)


def test_cut_changes_uv_identity_without_changing_original_faces(tmp_path: Path) -> None:
    source = panel(tmp_path / "source", crease=True)
    surface = source.state.surface
    cut = cut_region(surface, (0, 1), [[0, 2]])
    np.testing.assert_array_equal(cut.original_vertex_ids[cut.surface.mesh.faces], surface.mesh.faces)
    np.testing.assert_array_equal(cut.surface.mesh.vertices[cut.surface.mesh.faces],
                                  surface.mesh.vertices[surface.mesh.faces])
    assert surface.uv_faces[0, 0] == surface.uv_faces[1, 0]
    assert cut.surface.uv_faces[0, 0] != cut.surface.uv_faces[1, 0]


@pytest.mark.parametrize("angle", [0., -1., 181., float("nan")])
def test_invalid_angles_reject(tmp_path: Path, angle: float) -> None:
    with pytest.raises(ValueError, match="ANGLE"):
        classify_edges(panel(tmp_path / "source"), angle, .1)


def test_patch_fans_keep_polygon_diagonals_and_bound_patch_size(tmp_path: Path) -> None:
    source = panel(tmp_path / "source", diagonal=True)
    report = classify_edges(source, 45, .1)
    plan = patch_plan(source, report, 8)
    assert plan["edge_ids"] == []
    assert plan["face_patches"] == [[0, 1]]
    folded = panel(tmp_path / "folded", crease=True)
    plan = patch_plan(folded, classify_edges(folded, 45, .1), 1)
    assert len(cast(list[list[int]], plan["face_patches"])) == 2


def test_geometric_ao_cue_is_bound_to_state_and_not_called_texture(tmp_path: Path) -> None:
    import time

    source = panel(tmp_path / "source")
    folder = tmp_path / "index"
    folder.mkdir()
    result = index_mesh({"snapshot": str(source.root), "ao_rays": 4}, folder, time.monotonic() + 20)
    report = classify_edges(source, 45, .1)
    add_surface_cues(source, report, {"mesh_index": result["index"]}, tmp_path, time.monotonic() + 20)
    channels = cast(dict[str, object], report["surface_cue_sources"])
    assert isinstance(channels["geometry_ao"], dict)
    assert channels["ao"] == "not_bound"
    assert all("geometry_ao_occlusion" in row for row in cast(list[dict[str, object]], report["edges"]))
    other = panel(tmp_path / "other", crease=True)
    with pytest.raises(ValueError, match="STATE_OR_HASH"):
        add_surface_cues(other, classify_edges(other, 45, .1), {"mesh_index": result["index"]},
                         tmp_path, time.monotonic() + 20)


def test_explicit_maps_are_linear_cues_with_frozen_source_uv(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import time

    from reseam.agent_tools import views

    source = panel(tmp_path / "source")
    uv = source.state.surface.uv[source.state.surface.uv_faces].copy()
    uv[0, :, 0] = .2
    uv[1, :, 0] = .8
    monkeypatch.setattr(views, "_texture", lambda *_: (None, uv, None, False, {}))
    pixels = np.zeros((16, 16, 3), dtype=np.uint8)
    pixels[:, 8:] = 255
    height = tmp_path / "height.png"
    Image.fromarray(pixels).save(height)
    normal = tmp_path / "normal.png"
    Image.new("RGB", (16, 16), (128, 128, 255)).save(normal)
    report = classify_edges(source, 45, .1)
    output = tmp_path / "cues"
    output.mkdir()
    add_surface_cues(source, report, {"maps": {"height": {"path": str(height), "channel": 0}}},
                     output, time.monotonic() + 20)
    row = next(row for row in cast(list[dict[str, object]], report["edges"]) if row["vertices"] == [0, 2])
    assert row["height_map_difference"] == 1.
    assert row["suggested_cut"] is False  # signal cue does not authorize a cut
    assert file_hash(output / "cue_inputs/height.png") == file_hash(height)


def test_16_bit_height_map_preserves_midrange_gradients(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import time

    from reseam.agent_tools import views

    source = panel(tmp_path / "source")
    uv = source.state.surface.uv[source.state.surface.uv_faces].copy()
    uv[0, :, 0] = .2
    uv[1, :, 0] = .8
    monkeypatch.setattr(views, "_texture", lambda *_: (None, uv, None, False, {}))
    pixels = np.full((16, 16), 256, dtype=np.uint16)
    pixels[:, 8:] = 32768
    height = tmp_path / "height16.png"
    Image.fromarray(pixels).save(height)
    report = classify_edges(source, 45, .1)
    output = tmp_path / "cues"
    output.mkdir()
    add_surface_cues(source, report, {"maps": {"height": {"path": str(height)}}}, output,
                     time.monotonic() + 20)
    row = next(row for row in cast(list[dict[str, object]], report["edges"]) if row["vertices"] == [0, 2])
    assert float(str(row["height_map_difference"])) == pytest.approx((32768 - 256) / 65535)


def test_flat_normal_map_world_comparison_respects_source_uv(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import time

    from reseam.agent_tools import views

    source = panel(tmp_path / "source")
    uv = source.state.surface.uv[source.state.surface.uv_faces].copy() * .3 + .1
    uv[1, :, 0] += .5
    monkeypatch.setattr(views, "_texture", lambda *_: (None, uv, None, False, {}))
    normal = tmp_path / "normal.png"
    Image.new("RGB", (16, 16), (128, 128, 255)).save(normal)
    report = classify_edges(source, 45, .1)
    output = tmp_path / "cues"
    output.mkdir()
    add_surface_cues(source, report, {"maps": {"normal": {"path": str(normal), "convention": "opengl"}}},
                     output, time.monotonic() + 20)
    row = next(row for row in cast(list[dict[str, object]], report["edges"]) if row["vertices"] == [0, 2])
    assert float(str(row["normal_map_world_angle_degrees"])) < .001
