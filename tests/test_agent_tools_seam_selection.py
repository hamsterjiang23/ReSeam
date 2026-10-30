"""Selections operate on real mesh connectivity, not copied expected answers."""

import time
from pathlib import Path
from typing import cast

import numpy as np
import pytest

from reseam.agent_tools.seam_selection import select_edges, select_seam
from reseam.agent_tools.state import Snapshot, save_snapshot
from reseam.budget_state import BudgetState
from reseam.mesh import make_mesh, state_from_corner_uv


def grid(tmp_path: Path, size: int = 5, *, fold: bool = False) -> Snapshot:
    vertices = np.asarray([[float(x), float(y), abs(x-2)*.8 if fold else 0.]
                           for y in range(size) for x in range(size)])
    polygons = [[y*size+x, y*size+x+1, (y+1)*size+x+1, (y+1)*size+x]
                for y in range(size-1) for x in range(size-1)]
    faces = np.asarray([[p[0], p[1], p[2]] if j == 0 else [p[0], p[2], p[3]]
                        for p in polygons for j in range(2)], dtype=np.int64)
    mesh = make_mesh(vertices, faces)
    surface = state_from_corner_uv(mesh, vertices[faces, :2], corner_ids=faces)
    return save_snapshot(BudgetState(surface), tmp_path / "grid", {
        "polygon_topology": {"polygons": polygons, "triangle_to_polygon": [p for p in range(len(polygons))
                                                                          for _ in range(2)]}})


def run(snapshot: Snapshot, operator: str, arguments: dict[str, object]) -> dict[str, object]:
    return select_edges(snapshot, operator, arguments, time.monotonic()+10)


def test_region_boundary_differs_from_mesh_boundary(tmp_path: Path) -> None:
    snapshot = grid(tmp_path)
    region = run(snapshot, "region_boundary", {"face_ids": [10, 11]})
    boundary = run(snapshot, "boundary_loop", {"face_ids": [10, 11]})
    assert region["edge_count"] == 4
    assert boundary["edge_ids"] == []
    assert cast(list[dict[str, object]], cast(dict[str, object], region["diagnostics"])["chains"])[0]["closed"]


def test_quad_loop_crosses_valence_four_and_stops_at_boundary(tmp_path: Path) -> None:
    snapshot = grid(tmp_path)
    mesh = snapshot.state.surface.mesh
    seed = mesh.edges.index((7, 12))
    selected = run(snapshot, "topological_loop", {"edge_ids": [seed]})
    assert set(map(tuple, cast(list[list[int]], selected["edges"]))) == {(2, 7), (7, 12), (12, 17), (17, 22)}
    loops = run(snapshot, "find_loop", {"edge_ids": [seed, mesh.edges.index((11, 12))]})
    assert len(cast(list[object], loops["candidates"])) == 2


def test_quad_provenance_never_inferred_from_triangles(tmp_path: Path) -> None:
    snapshot = grid(tmp_path)
    snapshot.metadata.pop("polygon_topology")
    with pytest.raises(TypeError, match="QUAD_PROVENANCE_REQUIRED"):
        run(snapshot, "topological_loop", {"edge_ids": [0]})


def test_triangle_diagonal_is_not_an_original_quad_edge(tmp_path: Path) -> None:
    snapshot = grid(tmp_path)
    with pytest.raises(ValueError, match="TRIANGULATION_DIAGONAL"):
        run(snapshot, "topological_loop", {"edge_ids": [snapshot.state.surface.mesh.edges.index((0, 6))]})


def test_invalid_polygon_mapping_rejects(tmp_path: Path) -> None:
    snapshot = grid(tmp_path)
    provenance = cast(dict[str, object], snapshot.metadata["polygon_topology"])
    provenance["triangle_to_polygon"] = [0] * len(snapshot.state.surface.mesh.faces)
    with pytest.raises(ValueError, match="POLYGON_TRIANGLE_MAPPING_INVALID"):
        run(snapshot, "topological_loop", {"edge_ids": [0]})


def test_crease_and_semantic_boundary_are_actual_shared_edges(tmp_path: Path) -> None:
    snapshot = grid(tmp_path, fold=True)
    mesh = snapshot.state.surface.mesh
    creases = run(snapshot, "crease_select", {"angle_degrees": 30})
    expected = {mesh.edges.index((v, v+5)) for v in (2, 7, 12, 17)}
    assert set(cast(list[int], creases["edge_ids"])) == expected
    first = [f for f, triangle in enumerate(mesh.faces) if mesh.vertices[triangle, 0].mean() < 2]
    second = sorted(set(range(len(mesh.faces))) - set(first))
    snapshot.metadata["regions"] = [{"name": "left", "faces": first}, {"name": "right", "faces": second}]
    interface = run(snapshot, "semantic_interface", {"first_label": "left", "second_label": "right"})
    assert set(cast(list[int], interface["edge_ids"])) == expected


def test_thin_two_sided_pillow_selects_one_closed_rim(tmp_path: Path) -> None:
    vertices = np.asarray([[1., 0, 0], [0, 1., 0], [-1., 0, 0], [0, -1., 0], [0, 0, .05], [0, 0, -.05]])
    faces = np.asarray([[i, (i+1) % 4, 4] for i in range(4)] +
                       [[(i+1) % 4, i, 5] for i in range(4)], dtype=np.int64)
    mesh = make_mesh(vertices, faces)
    state = state_from_corner_uv(mesh, vertices[faces, :2], corner_ids=faces)
    snapshot = save_snapshot(BudgetState(state), tmp_path / "pillow", {})
    selection = run(snapshot, "thin_loop", {})
    assert set(map(tuple, cast(list[list[int]], selection["edges"]))) == {(0, 1), (1, 2), (2, 3), (0, 3)}
    assert cast(dict[str, object], selection["diagnostics"])["area_balance"] == pytest.approx(1)


def test_open_single_sheet_has_no_fake_two_sided_rim(tmp_path: Path) -> None:
    snapshot = grid(tmp_path)
    with pytest.raises(ValueError, match="NO_SINGLE_CLOSED_TWO_SIDED_SPLIT"):
        run(snapshot, "thin_loop", {})


def test_curvature_path_and_shortest_path_have_distinct_costs_and_routes(tmp_path: Path) -> None:
    snapshot = grid(tmp_path, fold=True)
    mesh = snapshot.state.surface.mesh
    # Endpoints offset across the ridge: ordinary path cuts across the slope;
    # strong anisotropy travels via the folded ridge.
    waypoints = [mesh.edges.index((1, 2)), mesh.edges.index((22, 23))]
    shortest = run(snapshot, "geodesic", {"waypoint_edges": waypoints})
    ridge = run(snapshot, "seamcut_seam", {"waypoint_edges": waypoints, "beta": 30})
    assert shortest["edge_ids"] != ridge["edge_ids"]
    ridge_edges = {mesh.edges.index((v, v+5)) for v in (2, 7, 12, 17)}
    assert len(ridge_edges & set(cast(list[int], ridge["edge_ids"]))) > len(
        ridge_edges & set(cast(list[int], shortest["edge_ids"])))
    assert cast(dict[str, object], ridge["diagnostics"])["lookback"] == 3
    for result in (shortest, ridge):
        chains = cast(list[dict[str, object]], cast(dict[str, object], result["diagnostics"])["chains"])
        assert len(chains) == 1
        assert not chains[0]["branch_vertices"]


def test_extremities_find_surface_distance_caps(tmp_path: Path) -> None:
    snapshot = grid(tmp_path, size=7)
    result = run(snapshot, "find_extremities", {"cap_fraction": .6})
    candidates = cast(list[dict[str, object]], result["candidates"])
    assert candidates
    for candidate in candidates:
        cap = set(cast(list[int], candidate["face_ids"]))
        for e in cast(list[int], candidate["edge_ids"]):
            assert sum(f in cap for f, _ in snapshot.state.surface.mesh.edge_faces[e]) == 1


def test_disconnected_coincident_sheets_are_not_welded(tmp_path: Path) -> None:
    points = np.asarray([[0., 0, 0], [1, 0, 0], [0, 1, 0]]*2)
    faces = np.arange(6).reshape(2, 3)
    mesh = make_mesh(points, faces)
    state = state_from_corner_uv(mesh, points[faces, :2], corner_ids=faces)
    snapshot = save_snapshot(BudgetState(state), tmp_path / "coincident", {})
    with pytest.raises(ValueError, match="NO_CONNECTED_EDGE_PATH"):
        run(snapshot, "geodesic", {"waypoint_edges": [mesh.edges.index((0, 1)), mesh.edges.index((3, 4))]})


def test_timeout_stale_request_and_parent_immutability(tmp_path: Path) -> None:
    snapshot = grid(tmp_path)
    before = (snapshot.root / "snapshot.json").read_bytes()
    request: dict[str, object] = {"snapshot": str(snapshot.root), "operator": "boundary_loop"}
    with pytest.raises(TimeoutError):
        select_seam(request, tmp_path / "expired", time.monotonic()-1)
    assert not (tmp_path / "expired").exists()
    with pytest.raises(ValueError, match="STALE_SNAPSHOT"):
        select_seam({**request, "state_hash": "wrong"}, tmp_path / "stale", time.monotonic()+10)
    selected = select_seam(request, tmp_path / "selection", time.monotonic()+10)
    assert Path(str(selected["selection"])).is_file()
    assert before == (snapshot.root / "snapshot.json").read_bytes()


def test_sql_selection_preserves_entity_and_snapshot_binding(tmp_path: Path) -> None:
    from reseam.agent_tools.mesh_query import index_mesh

    snapshot = grid(tmp_path, fold=True)
    indexed = index_mesh({"snapshot": str(snapshot.root), "ao_rays": 0}, tmp_path / "index", time.monotonic()+10)
    selected = run(snapshot, "crease_select", {"index": indexed["index"],
        "edge_query": "SELECT id AS edge_id FROM edge WHERE boundary = 0", "angle_degrees": 30})
    direct = run(snapshot, "crease_select", {"angle_degrees": 30})
    assert selected["edge_ids"] == direct["edge_ids"]
    with pytest.raises(ValueError, match="ENTITY_MISMATCH"):
        run(snapshot, "crease_select", {"index": indexed["index"],
            "edge_query": "SELECT id AS face_id FROM face", "angle_degrees": 30})
