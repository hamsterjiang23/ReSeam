"""Seam refinement removes redundant bands without erasing named boundaries."""

import time
from pathlib import Path

import numpy as np

from reseam.agent_tools.seam_plans import _nonmanifold_vertices, cut_topology, submit_plan
from reseam.agent_tools.state import Snapshot, read_object, save_snapshot
from reseam.budget_state import BudgetState, fingerprint
from reseam.mesh import make_mesh, state_from_corner_uv


def _strip(tmp_path: Path, labels: bool = False) -> tuple[Snapshot, list[int]]:
    vertices = np.array([[x, y, 0.] for x in [0., 1., 1.05, 2.05] for y in [0., 1.]])
    faces = np.array([[i, i+2, i+3] if j == 0 else [i, i+3, i+1]
                      for i in [0, 2, 4] for j in [0, 1]], dtype=np.int64)
    mesh = make_mesh(vertices, faces)
    state = state_from_corner_uv(mesh, vertices[faces, :2], corner_ids=faces)
    regions: list[dict[str, object]] = []
    if labels:
        regions = [{"name": str(i), "faces": [2*i, 2*i+1]} for i in range(3)]
    snapshot = save_snapshot(BudgetState(state), tmp_path / "source", {"regions": regions})
    return snapshot, [mesh.edges.index((2, 3)), mesh.edges.index((4, 5))]


def test_parallel_thin_band_merges_complete_interface(tmp_path: Path) -> None:
    source, edges = _strip(tmp_path)
    before = fingerprint(source.state)
    initial = cut_topology(source, list(range(6)), edges, time.monotonic()+10)
    assert initial["chart_count"] == 3
    result = submit_plan({"snapshot": str(source.root), "edge_ids": edges}, tmp_path / "plan",
                         time.monotonic()+10)
    plan = read_object(Path(str(result["plan"])))
    assert result["status"] == "OK"
    assert len(result["edge_ids"]) == 1
    assert plan["topology"]["chart_count"] == 2
    assert plan["refinements"][0]["kind"] == "thin_parallel_band"
    assert fingerprint(source.state) == before


def test_semantic_interfaces_are_not_merged(tmp_path: Path) -> None:
    source, edges = _strip(tmp_path, labels=True)
    result = submit_plan({"snapshot": str(source.root), "edge_ids": edges}, tmp_path / "plan",
                         time.monotonic()+10)
    assert result["edge_ids"] == sorted(edges)
    plan = read_object(Path(str(result["plan"])))
    assert plan["refinements"] == []


def test_vertex_link_detects_bowtie_without_nonmanifold_edges() -> None:
    vertices = np.array([[0., 0, 0], [1, 0, 0], [0, 1, 0], [-1, 0, 0], [0, -1, 0]])
    mesh = make_mesh(vertices, np.array([[0, 1, 2], [0, 3, 4]], dtype=np.int64))
    assert all(len(entries) <= 2 for entries in mesh.edge_faces)
    assert _nonmanifold_vertices(mesh) == [0]


def test_disk_certificate_records_vertex_links(tmp_path: Path) -> None:
    source, edges = _strip(tmp_path)
    report = cut_topology(source, list(range(6)), edges, time.monotonic()+10)
    assert report["all_disks"] is True
    assert all(chart["manifold_vertices"] for chart in report["charts"])


def test_zigzag_refinement_keeps_semantic_boundary(tmp_path: Path) -> None:
    vertices = np.array([[0., 0, 0], [1, 1, 0], [2, 0, 0], [1, 2, 0], [1, -1, 0]])
    faces = np.array([[0, 2, 1], [0, 1, 3], [1, 2, 3], [2, 0, 4]], dtype=np.int64)
    mesh = make_mesh(vertices, faces)
    state = BudgetState(state_from_corner_uv(mesh, vertices[faces, :2], corner_ids=faces))
    edges = [mesh.edges.index((0, 1)), mesh.edges.index((1, 2))]
    for named in (False, True):
        regions = [{"name": "upper", "faces": [1, 2]}] if named else []
        source = save_snapshot(state, tmp_path / f"source_{named}", {"regions": regions})
        result = submit_plan({"snapshot": str(source.root), "edge_ids": edges},
                             tmp_path / f"plan_{named}", time.monotonic()+10)
        assert result["status"] == "OK"
        assert len(result["edge_ids"]) == (2 if named else 1)
