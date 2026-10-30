"""Plans bind geometry and record actual cuts rather than inferred labels."""

import time
from pathlib import Path

import numpy as np
import pytest
from test_agent_tools_regional_cut import cylinder

from reseam.agent_tools.seam_plans import (
    _mirror,
    apply_seams,
    cut_topology,
    evaluate_program,
    manage_plan,
    preview_seams,
    submit_plan,
)
from reseam.agent_tools.state import load_snapshot, save_snapshot
from reseam.agent_tools.views import render_views
from reseam.budget_state import BudgetState, fingerprint
from reseam.mesh import make_mesh
from reseam.types import MeshState


@pytest.mark.parametrize('plane', [True, {'normal': [1, 0, 0], 'offset': 0, 'tolerance_fraction': .001}])
def test_mirror_program_returns_original_edge_ids_without_welding(tmp_path: Path, plane: object) -> None:
    vertices = np.array([[1., 0, 0], [3, 0, 0], [1, 1, 0], [-1, 0, 0], [-3, 0, 0], [-1, 1, 0]])
    faces = np.array([[0, 1, 2], [3, 5, 4]], dtype=np.int64)
    surface = MeshState(make_mesh(vertices, faces), frozenset(), vertices[:, :2].copy(), faces)
    source = save_snapshot(BudgetState(surface), tmp_path/'source', {})
    before = fingerprint(source.state)
    edges, traces = evaluate_program(source, [{'name': 'pair', 'operator': 'region_boundary',
                                               'arguments': {'face_ids': [0]}, 'mirror': plane}], time.monotonic()+30)
    assert edges == list(range(6))
    assert traces[0]['result']['mirrored_edge_ids'] == [surface.mesh.edges.index(e) for e in [(3, 4), (3, 5), (4, 5)]]
    assert fingerprint(load_snapshot(source.root).state) == before
    with pytest.raises(ValueError, match='OUTSIDE_TOLERANCE'):
        _mirror(source, [surface.mesh.edges.index((0, 1))], {'offset': 100})


def test_mirror_rejects_matching_midpoint_with_wrong_endpoints(tmp_path: Path) -> None:
    vertices = np.array([[1., 0, 0], [3, 0, 0], [1, 1, 0], [-2, -1, 0], [-2, 1, 0], [-3, 1, 0]])
    faces = np.array([[0, 1, 2], [3, 4, 5]], dtype=np.int64)
    surface = MeshState(make_mesh(vertices, faces), frozenset(), vertices[:, :2].copy(), faces)
    source = save_snapshot(BudgetState(surface), tmp_path/'source', {})
    with pytest.raises(ValueError, match='ENDPOINT_MISMATCH'):
        _mirror(source, [surface.mesh.edges.index((0, 1))], {'tolerance_fraction': .001})


def test_mirror_rejects_many_original_edges_mapping_to_one(tmp_path: Path) -> None:
    first = np.array([[1., 0, 0], [3, 0, 0], [1, 1, 0]])
    vertices = np.vstack((first, first, first * [-1, 1, 1]))
    faces = np.array([[0, 1, 2], [3, 4, 5], [6, 8, 7]], dtype=np.int64)
    surface = MeshState(make_mesh(vertices, faces), frozenset(), vertices[:, :2].copy(), faces)
    source = save_snapshot(BudgetState(surface), tmp_path/'source', {})
    with pytest.raises(ValueError, match='NOT_INJECTIVE'):
        _mirror(source, [surface.mesh.edges.index(e) for e in [(0, 1), (3, 4)]], {})


def test_cylinder_plan_rebuilds_eight_fragments_to_one_disk(tmp_path: Path) -> None:
    source = save_snapshot(BudgetState(cylinder()), tmp_path/'source', {})
    before = fingerprint(source.state)
    edge = source.state.surface.mesh.edges.index((0, 4))
    assert not cut_topology(source, list(range(8)), [], time.monotonic()+30)['all_disks']
    plan = submit_plan({'snapshot': str(source.root), 'edge_ids': [edge]}, tmp_path/'plan', time.monotonic()+30)
    assert plan['status'] == 'OK'
    folder = tmp_path/'applied'
    folder.mkdir()
    result = apply_seams({'snapshot': str(source.root), 'plan': plan['plan'], 'backend': 'harmonic',
                         'profile': {'resolution': 128, 'quality_constraints': False}}, folder, time.monotonic()+30)
    assert result['status'] == 'OK', result
    import numpy as np

    from reseam.mesh import chart_labels
    child = load_snapshot(Path(str(result['snapshot'])))
    assert len(np.unique(chart_labels(child.state.surface))) == 1
    assert fingerprint(load_snapshot(source.root).state) == before


def test_plan_stale_parent_and_protection(tmp_path: Path) -> None:
    source = save_snapshot(BudgetState(cylinder()), tmp_path/'source', {})
    plan = submit_plan({'snapshot': str(source.root), 'edge_ids': [source.state.surface.mesh.edges.index((0, 4))]},
                       tmp_path/'plan', time.monotonic()+30)
    other = save_snapshot(source.state, tmp_path/'other', {'different': True}, source)
    with pytest.raises(ValueError, match='STALE_SEAM_PLAN'):
        manage_plan({'snapshot': str(other.root), 'plan': plan['plan']}, tmp_path/'get', time.monotonic()+30)
    with pytest.raises(ValueError, match='PROTECTED'):
        submit_plan({'snapshot': str(source.root), 'protected_faces': [0]}, tmp_path/'protected', time.monotonic()+30)


def test_dsl_dependency_cycle_and_unknown_reference(tmp_path: Path) -> None:
    source = save_snapshot(BudgetState(cylinder()), tmp_path/'source', {})
    for ref in ('a', 'missing'):
        with pytest.raises(ValueError, match='CYCLIC_OR_UNKNOWN'):
            evaluate_program(source, [{'name': 'a', 'operator': 'crease_select',
                                       'arguments': {'edge_ids': {'ref': ref}}}], time.monotonic()+30)


def test_dsl_order_and_actual_selected_edge_preview(tmp_path: Path) -> None:
    source = save_snapshot(BudgetState(cylinder()), tmp_path/'source', {})
    program = [{'name': 'filtered', 'operator': 'crease_select',
                'arguments': {'edge_ids': {'ref': 'base'}, 'angle_degrees': 30}},
               {'name': 'base', 'operator': 'crease_select', 'emit': False,
                'arguments': {'angle_degrees': 30}}]
    edges, traces = evaluate_program(source, program, time.monotonic()+30)
    assert edges
    assert traces[0]['name'] == 'base'
    preview = preview_seams({'snapshot': str(source.root), 'edge_ids': edges, 'size': 64},
                           tmp_path/'preview', time.monotonic()+30)
    assert preview['edge_ids'] == edges
    assert Path(str(preview['report'])).is_file()


@pytest.mark.parametrize('mode', ['wireframe', 'normals', 'distortion'])
def test_geometric_views_and_pick_edges(tmp_path: Path, mode: str) -> None:
    source = save_snapshot(BudgetState(cylinder()), tmp_path/'source', {})
    folder = tmp_path/mode
    folder.mkdir()
    result = render_views({'snapshot': str(source.root), 'mode': mode, 'size': 64,
                           'pick_pixels': {'front': [[32, 32]]}}, folder, time.monotonic()+30)
    assert Path(str(result['report'])).is_file()
    assert 'nearby_edge_ids' in result['picked_faces']['front'][0]


def test_expired_plan_never_publishes(tmp_path: Path) -> None:
    source = save_snapshot(BudgetState(cylinder()), tmp_path/'source', {})
    with pytest.raises(TimeoutError):
        submit_plan({'snapshot': str(source.root), 'edge_ids': []}, tmp_path/'plan', time.monotonic()-1)
    assert not (tmp_path/'plan'/'plan.json').exists()


def test_dsl_rejects_edge_reference_as_faces_and_string_booleans(tmp_path: Path) -> None:
    source = save_snapshot(BudgetState(cylinder()), tmp_path/'source', {})
    base = {'name': 'base', 'operator': 'crease_select', 'arguments': {'angle_degrees': 30}}
    with pytest.raises(TypeError, match='TYPE_MISMATCH'):
        evaluate_program(source, [base, {'name': 'bad', 'operator': 'region_boundary',
                                        'arguments': {'face_ids': {'ref': 'base'}}}], time.monotonic()+30)
    for field in ('emit', 'mirror'):
        with pytest.raises(TypeError, match='MUST_BE_BOOLEAN'):
            evaluate_program(source, [{**base, field: 'false'}], time.monotonic()+30)
