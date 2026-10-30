"""Real Blender operators and source-polygon identities on a bounded fixture."""

import time
from pathlib import Path

import numpy as np
import pytest

from reseam.agent_tools.assets import import_asset
from reseam.agent_tools.reparameterize import reparameterize
from reseam.agent_tools.state import load_snapshot, read_object, save_snapshot
from reseam.budget_state import BudgetState, fingerprint

BLENDER = Path("E:/Blender 4.5.11/blender.exe")


@pytest.mark.skipif(not BLENDER.is_file(), reason="Native Blender runtime not installed")
@pytest.mark.parametrize("backend,method", [("blender_lscm", "CONFORMAL"),
    ("blender_abf", "ANGLE_BASED"), ("blender_minimum_stretch", "MINIMUM_STRETCH")])
def test_native_blender_cut_cylinder_preserves_original_faces(
    tmp_path: Path, backend: str, method: str,
) -> None:
    from test_agent_tools_regional_cut import cylinder

    source = save_snapshot(BudgetState(cylinder()), tmp_path / "source", {})
    before = fingerprint(source.state)
    result = reparameterize({"snapshot": str(source.root), "faces": list(range(8)), "backend": backend,
        "blender": str(BLENDER), "seam_edges": [[0, 4]],
        "profile": {"resolution": 128, "gap_pixels": 2, "border_pixels": 2, "quality_constraints": False}},
        tmp_path / "result", time.monotonic() + 30)
    assert result["status"] == "OK", result
    mapped = load_snapshot(Path(str(result["snapshot"])))
    assert np.array_equal(mapped.state.surface.mesh.vertices, source.state.surface.mesh.vertices)
    assert np.array_equal(mapped.state.surface.mesh.faces, source.state.surface.mesh.faces)
    assert np.isfinite(mapped.state.surface.uv).all()
    seam = source.state.surface.mesh.edges.index((0, 4))
    assert seam in mapped.state.surface.seams
    assert fingerprint(load_snapshot(source.root).state) == before
    report = read_object(tmp_path / "result/blender/backend.json")
    assert report["requested_method"] == method
    assert report["status"] == "OK"


def test_missing_blender_does_not_change_parent(tmp_path: Path) -> None:
    from test_agent_tools_regional_cut import cylinder

    source = save_snapshot(BudgetState(cylinder()), tmp_path / "source", {})
    before = fingerprint(source.state)
    with pytest.raises(FileNotFoundError, match="BLENDER_MISSING"):
        reparameterize({"snapshot": str(source.root), "faces": list(range(8)), "backend": "blender_lscm",
                       "blender": str(tmp_path / "missing.exe"), "seam_edges": [[0, 4]]},
                      tmp_path / "result", time.monotonic() + 30)
    assert fingerprint(load_snapshot(source.root).state) == before
    assert not (tmp_path / "result/snapshot").exists()


@pytest.mark.skipif(not BLENDER.is_file(), reason="Native Blender runtime not installed")
def test_quad_obj_keeps_polygon_to_triangle_identity(tmp_path: Path) -> None:
    path = tmp_path / "quad.obj"
    path.write_text("v 0 0 0\nv 1 0 0\nv 1 1 0\nv 0 1 0\n"
                    "vt 0 0\nvt 1 0\nvt 1 1\nvt 0 1\nf 1/1 2/2 3/3 4/4\n", encoding="utf-8")
    output = tmp_path / "import"
    output.mkdir()
    result = import_asset({"path": str(path), "blender": str(BLENDER)}, output, time.monotonic() + 30)
    snapshot = load_snapshot(Path(str(result["snapshot"])))
    topology = snapshot.metadata["polygon_topology"]
    assert isinstance(topology, dict)
    assert topology["polygons"] == [[0, 1, 2, 3]]
    assert topology["triangle_to_polygon"] == [0, 0]
    assert result["faces"] == 2
    assert result["source_uv_preserved"] is True
    assert set(snapshot.state.surface.mesh.faces.flat) == {0, 1, 2, 3}


def test_triangular_obj_records_triangles_without_invented_quads(tmp_path: Path) -> None:
    path = tmp_path / "tri.obj"
    path.write_text("v 0 0 0\nv 1 0 0\nv 1 1 0\nv 0 1 0\n"
        "vt 0 0\nvt 1 0\nvt 1 1\nvt 0 1\nf 1/1 2/2 3/3\nf 1/1 3/3 4/4\n", encoding="utf-8")
    output = tmp_path / "import"
    output.mkdir()
    result = import_asset({"path": str(path)}, output, time.monotonic() + 30)
    snapshot = load_snapshot(Path(str(result["snapshot"])))
    topology = snapshot.metadata["polygon_topology"]
    assert isinstance(topology, dict)
    assert topology["polygons"] == [[0, 1, 2], [0, 2, 3]]
    assert topology["triangle_to_polygon"] == [0, 1]
