"""Queryable mesh identities, real ray attributes and non-mutating proposals."""

import json
import sqlite3
import time
from pathlib import Path

import numpy as np
import pytest
import trimesh

from reseam.agent_tools.mesh_query import index_mesh, load_index, query_mesh, query_selection
from reseam.agent_tools.region_proposals import propose_regions
from reseam.agent_tools.state import load_snapshot, save_snapshot
from reseam.budget_state import BudgetState
from reseam.mesh import make_mesh, state_from_corner_uv


def _cube(path: Path) -> Path:
    source = trimesh.creation.box()
    mesh = make_mesh(np.asarray(source.vertices), np.asarray(source.faces))
    corners = np.broadcast_to(np.array([[0., 0.], [1., 0.], [0., 1.]]), (12, 3, 2)).copy()
    surface = state_from_corner_uv(mesh, corners, corner_ids=np.arange(36).reshape(12, 3))
    metadata = {"regions": [{"name": "one_side", "faces": [0, 1]}]}
    return save_snapshot(BudgetState(surface), path, metadata).root


def test_index_queries_original_ids_and_real_thickness(tmp_path: Path) -> None:
    source = _cube(tmp_path / "source")
    snapshot = load_snapshot(source)
    result = index_mesh({"snapshot": str(source)}, tmp_path / "index", time.monotonic()+30)
    connection, manifest = load_index({"index": result["index"]}, snapshot)
    rows = connection.execute("SELECT * FROM edge ORDER BY id").fetchall()
    connection.close()
    assert len(rows) == len(snapshot.state.surface.mesh.edges)
    assert [(row["v0"], row["v1"]) for row in rows] == list(snapshot.state.surface.mesh.edges)
    assert all(row["ao"] == 0 for row in rows)  # A convex cube has no outward obstruction.
    assert all(row["thickness_raw"] is not None and row["thickness_raw"] > .1 for row in rows)
    assert manifest["ao_rays"] == 8
    selected = query_selection(snapshot, str(result["index"]),
                               "SELECT id AS edge_id FROM edge WHERE dihedral_raw > 1", time.monotonic()+10)
    assert selected["entity"] == "edge"
    assert len(selected["ids"]) == 12
    assert load_snapshot(source).state_hash == snapshot.state_hash


def test_query_complete_artifact_and_no_write_or_attach(tmp_path: Path) -> None:
    source = _cube(tmp_path / "source")
    result = index_mesh({"snapshot": str(source), "ao_rays": 0}, tmp_path / "index", time.monotonic()+10)
    request = {"snapshot": str(source), "index": result["index"], "sql": "SELECT * FROM edge"}
    query = query_mesh(request, tmp_path / "query", time.monotonic()+10)
    assert query["row_count"] == 18
    assert len(json.loads(Path(str(query["selection"])).read_text())["rows"]) == 18
    for sql in ("DELETE FROM edge", "ATTACH DATABASE ':memory:' AS hacked", "PRAGMA user_version=5"):
        with pytest.raises(ValueError, match="QUERY_REJECTED"):
            query_mesh({**request, "sql": sql}, tmp_path / "bad", time.monotonic()+10)
    with pytest.raises(TimeoutError):
        query_mesh({**request, "sql": "SELECT * FROM edge"}, tmp_path / "expired", time.monotonic()-1)


def test_index_rejects_changed_state_and_database(tmp_path: Path) -> None:
    source = _cube(tmp_path / "source")
    original = load_snapshot(source)
    result = index_mesh({"snapshot": str(source), "ao_rays": 0}, tmp_path / "index", time.monotonic()+10)
    child = save_snapshot(original.state, tmp_path / "child", {"changed": True}, original)
    with pytest.raises(ValueError, match="STALE_MESH_INDEX"):
        load_index({"index": result["index"]}, child)
    database = tmp_path / "index" / "mesh.db"
    with sqlite3.connect(database) as connection:
        connection.execute("UPDATE edge SET seam=0")
    with pytest.raises(ValueError, match="DATABASE_HASH"):
        load_index({"index": result["index"]}, original)


def test_multiscale_candidates_cross_old_uv_without_editing(tmp_path: Path) -> None:
    source = _cube(tmp_path / "source")
    before = load_snapshot(source)
    index = index_mesh({"snapshot": str(source), "ao_rays": 0}, tmp_path / "index", time.monotonic()+10)
    result = propose_regions({"snapshot": str(source), "index": index["index"]},
                             tmp_path / "regions", time.monotonic()+10)
    record = json.loads(Path(str(result["proposals"])).read_text())
    assert result["candidate_count"] == 7  # Whole cube and six planar sides.
    assert sorted(candidate["face_count"] for candidate in record["candidates"]) == [2]*6+[12]
    assert all(candidate["semantic_label"] is None for candidate in record["candidates"])
    assert not (tmp_path / "regions" / "snapshot").exists()
    assert load_snapshot(source).state_hash == before.state_hash


def test_partfield_missing_is_explicit(tmp_path: Path) -> None:
    source = _cube(tmp_path / "source")
    with pytest.raises(FileNotFoundError, match="PARTFIELD_WEIGHTS_MISSING"):
        propose_regions({"snapshot": str(source), "backend": "partfield", "weights": str(tmp_path / "missing.ckpt")},
                        tmp_path / "research", time.monotonic()+10)
    assert not (tmp_path / "research" / "proposals.json").exists()


def test_empty_or_invalid_sql_selection_not_silently_successful(tmp_path: Path) -> None:
    source = _cube(tmp_path / "source")
    snapshot = load_snapshot(source)
    index = index_mesh({"snapshot": str(source), "ao_rays": 0}, tmp_path / "index", time.monotonic()+10)
    for sql, error in (("SELECT id FROM edge", "REQUIRES_EDGE"),
                       ("SELECT -1 AS edge_id", "OUT_OF_RANGE"),
                       ("SELECT id AS edge_id FROM edge WHERE id < 0", "EMPTY")):
        with pytest.raises(ValueError, match=error):
            query_selection(snapshot, str(index["index"]), sql, time.monotonic()+10)



def test_running_sql_is_interrupted_not_only_prechecked(tmp_path: Path) -> None:
    source = _cube(tmp_path / "source")
    index = index_mesh({"snapshot": str(source), "ao_rays": 0}, tmp_path / "index", time.monotonic()+10)
    sql = "WITH RECURSIVE counter(x) AS (SELECT 1 UNION ALL SELECT x+1 FROM counter WHERE x<100000000) SELECT sum(x) FROM counter"
    started = time.monotonic()
    with pytest.raises(TimeoutError):
        query_mesh({"snapshot": str(source), "index": index["index"], "sql": sql},
                   tmp_path / "query", started+.1)
    assert time.monotonic()-started < 2


def test_genus_candidate_is_verified_on_torus_not_inferred_from_shape(tmp_path: Path) -> None:
    source = trimesh.creation.torus(major_radius=1, minor_radius=.3, major_sections=12, minor_sections=8)
    mesh = make_mesh(np.asarray(source.vertices), np.asarray(source.faces))
    corners = np.broadcast_to(np.array([[0., 0.], [1., 0.], [0., 1.]]), (len(mesh.faces), 3, 2)).copy()
    snapshot = save_snapshot(BudgetState(state_from_corner_uv(mesh, corners,
                             corner_ids=np.arange(len(mesh.faces)*3).reshape(-1, 3))), tmp_path / "source", {})
    result = index_mesh({"snapshot": str(snapshot.root), "ao_rays": 0}, tmp_path / "index", time.monotonic()+30)
    connection, _ = load_index({"index": result["index"]}, snapshot)
    rows = connection.execute("SELECT type,score,edge_ids FROM feature WHERE type LIKE 'genus_%'").fetchall()
    connection.close()
    assert len(rows) == 1
    assert rows[0]["score"] == 1
    assert rows[0]["type"] == "genus_neck"
    assert len(json.loads(rows[0]["edge_ids"])) >= 3
