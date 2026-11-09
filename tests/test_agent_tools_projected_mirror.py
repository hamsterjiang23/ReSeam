"""Mirror variants must preserve projected-source and auxiliary-UV ancestry."""

import json
import time
from pathlib import Path

import numpy as np

from reseam.agent_tools.color_reuse import ColorTarget, freeze_target
from reseam.agent_tools.extra_uv import load_extra_uv, write_extra_uv
from reseam.agent_tools.mirror_asset import mirror_asset
from reseam.agent_tools.projected_source import projected_source_metadata
from reseam.agent_tools.state import Snapshot, file_hash, load_snapshot, save_snapshot
from reseam.agent_tools.views import render_views
from reseam.budget_material import atlas_from_arrays, export_material_glb
from reseam.budget_state import BudgetState
from reseam.mesh import make_mesh, state_from_corner_uv


def _source(folder: Path) -> Snapshot:
    vertices = np.array([[-1., -1., 0], [1., -1., 0], [1., 1., 0], [-1., 1., 0], [0., 0., 0]])
    faces = np.array([[0, 1, 4], [1, 2, 4], [2, 3, 4], [3, 0, 4]])
    uv = .15 + .3 * (vertices[:, :2] + 1)
    high = state_from_corner_uv(make_mesh(vertices, faces), uv[faces], seam_ids=frozenset())
    color = np.full((16, 16, 4), [.5, .1, .05, 1.])
    atlas = atlas_from_arrays(color, np.full((16, 16, 3), [0., 0, 1]), np.full((16, 16, 3), [1., 1, 0]))
    reference = folder / "high.glb"
    export_material_glb(high, atlas, reference)
    low_vertices = vertices[:4].copy()
    low_vertices[:, 2] = .001
    low_faces = np.array([[0, 1, 2], [0, 2, 3]])
    low = state_from_corner_uv(make_mesh(low_vertices, low_faces), uv[:4][low_faces], seam_ids=frozenset())
    metadata: dict[str, object] = {"reference_glb": str(reference)}
    write_extra_uv(metadata, folder / "extra", {"other": uv[:4][low_faces] * .5})
    return save_snapshot(BudgetState(low), folder / "source", metadata)


def test_projected_mirror_uses_original_high_and_composes_extra_uv(tmp_path: Path) -> None:
    parent = _source(tmp_path)
    projection = projected_source_metadata(parent, tmp_path / "initial_lineage.npz", .1)
    parent = save_snapshot(parent.state, tmp_path / "projected_parent",
                           {**parent.metadata, "projected_source": projection}, parent)
    before = file_hash(Path(str(parent.metadata["reference_glb"])))
    output = tmp_path / "mirror"
    output.mkdir()
    deadline = time.monotonic() + 60
    result = mirror_asset({"snapshot": str(parent.root), "allow_geometry_change": True,
                           "source_mode": "projected_high", "profile": {"resolution": 64,
                           "quality_constraints": False, "gap_pixels": 2, "border_pixels": 2}}, output, deadline)
    mirrored = load_snapshot(Path(str(result["snapshot"])))
    assert mirrored.metadata["reference_glb"] == parent.metadata["reference_glb"]
    inherited = mirrored.metadata["projected_source"]
    assert isinstance(inherited, dict)
    assert inherited["projection_max_fraction"] == .1
    assert file_hash(Path(str(parent.metadata["reference_glb"]))) == before
    with np.load(output / "source_correspondence.npz", allow_pickle=False) as ancestry:
        old = load_extra_uv(parent.metadata, 2)["other"]
        expected = np.einsum("fck,fkj->fcj", ancestry["source_barycentric"], old[ancestry["source_face_ids"]])
    actual = load_extra_uv(mirrored.metadata, len(mirrored.state.surface.mesh.faces))["other"]
    np.testing.assert_array_equal(actual, expected)
    target_dir = tmp_path / "target"
    target_dir.mkdir()
    frozen = freeze_target({"snapshot": str(mirrored.root), "policy": "source_original",
                            "geometry_profile": {"quality_constraints": False}}, target_dir, deadline)
    target = ColorTarget(mirrored, Path(str(frozen["target"])))
    assert target.projected_source is not None
    source = target.projected_source
    bary = np.array([[.2, .3, .5]])
    first = 0
    sampled = source.sample(first, bary)
    expected_points = np.einsum("nc,ncd->nd", sampled.source_low_barycentric,
                                source.original_low_triangles[sampled.source_low_faces])
    np.testing.assert_allclose(sampled.source_points, expected_points)
    assert sampled.valid.all()
    views = tmp_path / "views"
    views.mkdir()
    rendered = render_views({"snapshot": str(mirrored.root), "mode": "basecolor", "size": 64,
                             "target": frozen["target"], "seams": False}, views, deadline)
    assert Path(str(rendered["report"])).exists()
    assert (views / "source_projection/source_map.json").is_file()
    raw_views = tmp_path / "raw_views"
    raw_views.mkdir()
    render_views({"snapshot": str(mirrored.root), "mode": "basecolor", "size": 64,
                  "seams": False}, raw_views, deadline)
    source_report = json.loads((raw_views / "source_projection/source_map.json").read_text(encoding="utf-8"))
    assert source_report["max_distance"] == source.max_distance
