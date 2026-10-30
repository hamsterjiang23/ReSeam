"""Public true-tile and surface-alias metrics, independent of packing owners."""

import time
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest
from shapely import Polygon

from reseam.agent_tools.geometry import inspect_uv
from reseam.agent_tools.state import (
    Snapshot,
    file_hash,
    load_snapshot,
    object_hash,
    read_object,
    save_snapshot,
)
from reseam.agent_tools.uv_metrics import _covered_area
from reseam.budget_state import BudgetState, fingerprint
from reseam.joint_types import ReuseGroup
from reseam.mesh import make_mesh, state_from_corner_uv


def _section(document: dict[str, object], key: str) -> dict[str, object]:
    value = document[key]
    assert isinstance(value, dict)
    return value


def _disconnected_pair() -> BudgetState:
    first = np.array([[-1., 0, 0], [-1, 1, 0], [-1, 0, 1],
                      [-1., 2, 0], [-1, 3, 0], [-1, 2, 1]])
    vertices = np.concatenate((first, first * [-1, 1, 1]))
    faces = np.array([[0, 1, 2], [3, 4, 5], [6, 8, 7], [9, 11, 10]], dtype=np.int64)
    mesh = make_mesh(vertices, faces)
    surface = state_from_corner_uv(mesh, .1 + .2 * vertices[faces, 1:], seam_ids=frozenset())
    return BudgetState(surface, (ReuseGroup("disconnected", (0, 1), (2, 3), (1., 0, 0), 0),))


def _inspect(snapshot: Snapshot, output: Path, **selection: object) -> tuple[dict[str, object], dict[str, object]]:
    result = inspect_uv({"snapshot": str(snapshot.root), "profile": {"resolution": 64,
                        "gap_pixels": 2, "border_pixels": 2}, **selection}, output, time.monotonic() + 20)
    return result, read_object(Path(str(result["metrics_report"])))


def test_one_packing_owner_can_have_two_actual_tiles(tmp_path: Path) -> None:
    snapshot = save_snapshot(_disconnected_pair(), tmp_path / "source", {})
    before = fingerprint(load_snapshot(snapshot.root).state)
    result, metrics = _inspect(snapshot, tmp_path / "inspect", selected_faces=[0])
    assert result["geometry_passed"] is True
    assert result["metrics_verified"] is True
    assert result["uv_connected_charts"] == 4
    assert result["packing_owners"] == 1
    assert result["actual_independent_tiles"] == 2
    assert _section(metrics, "global")["actual_shared_surface_area"] == pytest.approx(2)
    assert _section(metrics, "global")["actual_shared_surface_fraction"] == pytest.approx(1)
    assert metrics["state_hash"] == snapshot.state_hash
    assert metrics["resolution"] == 64
    assert metrics["source_snapshot_sha256"] == file_hash(snapshot.root / "snapshot.json")
    artifact = Path(str(result["report"]))
    assert metrics["inspection_report_sha256"] == file_hash(artifact)
    inspection = read_object(artifact)
    assert metrics["geometry_report_object_sha256"] == object_hash(inspection["geometry"])
    assert _section(inspection, "uv_metrics")["actual_independent_tiles"] == 2
    assert fingerprint(load_snapshot(snapshot.root).state) == before


def test_no_shared_groups_reports_zero_actual_alias_area(tmp_path: Path) -> None:
    state = _disconnected_pair()
    state.groups = ()
    ids = np.unique(state.surface.uv_faces[2:])
    state.surface.uv[ids, 1] += .4
    snapshot = save_snapshot(state, tmp_path / "source", {})
    result, metrics = _inspect(snapshot, tmp_path / "inspect")
    assert result["geometry_passed"] is True
    assert result["actual_independent_tiles"] == result["packing_owners"] == 4
    assert _section(metrics, "global")["actual_shared_surface_area"] == 0


@pytest.mark.parametrize("bad_mapping", [False, True])
def test_unauthorized_or_incompatible_overlap_has_no_verified_metrics(tmp_path: Path, bad_mapping: bool) -> None:
    state = _disconnected_pair()
    state.groups = (replace(state.groups[0], plane_offset=.2),) if bad_mapping else ()
    snapshot = save_snapshot(state, tmp_path / "source", {})
    result, metrics = _inspect(snapshot, tmp_path / "inspect")
    assert result["geometry_passed"] is False
    assert result["metrics_verified"] is False
    assert result["actual_independent_tiles"] is None
    assert _section(metrics, "global")["actual_shared_surface_area"] is None
    assert _section(metrics, "selection")["actual_shared_surface_fraction"] is None


def test_region_selection_never_restricts_global_tiles_or_validation(tmp_path: Path) -> None:
    metadata: dict[str, object] = {"regions": [{"region_id": "one_patch", "faces": [0, 2]}]}
    snapshot = save_snapshot(_disconnected_pair(), tmp_path / "source", metadata)
    result, selected = _inspect(snapshot, tmp_path / "selected", selected_faces=[0])
    assert result["actual_independent_tiles"] == 2
    assert _section(selected, "global")["surface_area"] == pytest.approx(2)
    assert _section(selected, "selection")["surface_area"] == pytest.approx(.5)
    assert _section(selected, "selection")["actual_shared_surface_area"] == pytest.approx(.5)
    _, region = _inspect(snapshot, tmp_path / "region", region_id="one_patch")
    assert region["actual_independent_tiles"] == 2
    assert _section(region, "selection")["surface_area"] == pytest.approx(1)
    with pytest.raises(ValueError, match="SELECT_FACES_OR_REGION_NOT_BOTH"):
        _inspect(snapshot, tmp_path / "ambiguous", selected_faces=[0], region_id="one_patch")


def test_per_face_coverage_uses_union_not_duplicate_cell_sum() -> None:
    face = Polygon([(0., 0), (1, 0), (0, 1)])
    quarter = Polygon([(0., 0), (.5, 0), (0, .5)])
    assert _covered_area(face, [quarter, quarter], 10) == pytest.approx(2.5)
    assert _covered_area(face, [face, face], 10) == pytest.approx(10)


def test_zero_area_contacts_do_not_merge_tiles(tmp_path: Path) -> None:
    state = _disconnected_pair()
    mesh = state.surface.mesh
    state.surface = state_from_corner_uv(mesh, .125 + .125 * mesh.vertices[mesh.faces, 1:],
                                        seam_ids=frozenset())
    second_ids = np.unique(state.surface.uv_faces[2:])
    state.surface.uv[second_ids, 0] += .125
    snapshot = save_snapshot(state, tmp_path / "source", {})
    result, metrics = _inspect(snapshot, tmp_path / "inspect")
    assert result["geometry_passed"] is True
    assert result["packing_owners"] == 1
    assert result["actual_independent_tiles"] == 4
    assert metrics["authorized_positive_overlap_cells"] == 0
    assert _section(metrics, "global")["actual_shared_surface_area"] == 0


def test_different_triangulations_cover_each_original_face_once(tmp_path: Path) -> None:
    vertices = np.array([[-1., 0, 0], [-1, 1, 0], [-1, 0, 1],
                         [1., 0, 0], [1, 1, 0], [1, 0, 1], [1, .5, .5]])
    faces = np.array([[0, 1, 2], [3, 6, 4], [3, 5, 6]], dtype=np.int64)
    mesh = make_mesh(vertices, faces)
    state = BudgetState(state_from_corner_uv(mesh, .1 + .2 * vertices[faces, 1:], seam_ids=frozenset()),
                        (ReuseGroup("split", (0,), (1, 2), (1., 0, 0), 0),))
    snapshot = save_snapshot(state, tmp_path / "source", {})
    result, metrics = _inspect(snapshot, tmp_path / "inspect")
    assert result["geometry_passed"] is True
    assert metrics["authorized_positive_overlap_cells"] == 2
    assert metrics["actual_independent_tiles"] == 1
    assert _section(metrics, "global")["actual_shared_surface_area"] == pytest.approx(1)
