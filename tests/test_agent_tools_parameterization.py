"""Identity-preserving regional maps and explicit method changes."""

import time
from pathlib import Path

import numpy as np
import pytest
from shapely import GeometryCollection, LineString, Polygon

from reseam.agent_tools.correspondence import common_harmonic_pair, topology_copy
from reseam.agent_tools.geometry import check_pair
from reseam.agent_tools.reparameterize import harmonic_uv, reparameterize
from reseam.agent_tools.state import load_snapshot, save_snapshot
from reseam.budget_state import BudgetState, _positive_cell_vertices, fingerprint
from reseam.mesh import make_mesh, state_from_corner_uv
from reseam.validation import jacobians


def _square() -> BudgetState:
    vertices = np.asarray([[-1.,0,0],[-1,1,0],[-1,1,1],[-1,0,1],
                           [1.,0,0],[1,1,0],[1,1,1],[1,0,1]])
    faces = np.asarray([[0,1,2],[0,2,3],[4,7,5],[5,7,6]],dtype=np.int64)
    corners = vertices[faces,1:]*.2+.1
    corners[2:,:,0] += .5
    corners[2:,:,1] *= -1
    return BudgetState(state_from_corner_uv(make_mesh(vertices,faces),corners,seam_ids=frozenset()))


def test_common_harmonic_different_diagonals_preserves_faces() -> None:
    original = _square().surface
    mapped = common_harmonic_pair(original,(0,1),(2,3),np.asarray([1.,0,0]),0)
    assert np.array_equal(mapped.mesh.faces,original.mesh.faces)
    assert np.array_equal(mapped.mesh.vertices,original.mesh.vertices)
    signs = np.sign(np.linalg.det(jacobians(mapped)))
    assert np.all(signs[:2]==1) and np.all(signs[2:]==-1)


def test_explicit_common_domain_can_repair_nonaffine_old_uv(tmp_path: Path) -> None:
    state = _square()
    state.surface.uv[state.surface.uv_faces[0,2]] += [.1,.01]
    snapshot = save_snapshot(state,tmp_path/'source',{})
    result = check_pair({'snapshot':str(snapshot.root),'first_faces':[0,1],'second_faces':[2,3],
        'mode':'common_domain','profile':{'resolution':128,'gap_pixels':2,'border_pixels':2}},
        tmp_path/'check',time.monotonic()+30)
    assert result['status']=='OK',result
    assert result['selected_method']=='common_harmonic_domain'
    assert fingerprint(load_snapshot(snapshot.root).state)==fingerprint(state)


def test_harmonic_rejects_multiple_boundaries() -> None:
    with pytest.raises(ValueError,match='MULTIPLE_BOUNDARIES_OR_COMPONENTS'):
        harmonic_uv(_square().surface,(0,1,2,3))


def test_regional_harmonic_keeps_original_geometry(tmp_path: Path) -> None:
    source=save_snapshot(_square(),tmp_path/'source',{})
    result=reparameterize({'snapshot':str(source.root),'faces':[0,1],'backend':'harmonic',
        'profile':{'resolution':128,'gap_pixels':2,'border_pixels':2}},tmp_path/'mapped',time.monotonic()+30)
    assert result['status']=='OK',result
    mapped=load_snapshot(Path(str(result['snapshot'])))
    assert np.array_equal(mapped.state.surface.mesh.vertices,source.state.surface.mesh.vertices)
    assert np.array_equal(mapped.state.surface.mesh.faces,source.state.surface.mesh.faces)


def test_topology_match_rejects_different_diagonals_without_false_negative_claim() -> None:
    mapped=topology_copy(_square().surface,(0,1),(2,3),np.asarray([1.,0,0]),0,.005,time.monotonic()+2)
    assert mapped is None


def test_exclusive_region_is_removed_before_orientation_initialization(tmp_path: Path) -> None:
    state=_square()
    snapshot=save_snapshot(state,tmp_path/'source',{})
    result=check_pair({'snapshot':str(snapshot.root),'first_faces':[0,1],'second_faces':[2,3],
        'exclusive_faces':[0,1]},tmp_path/'check',time.monotonic()+30)
    assert result['reason']=='EXCEPTIONS_REMOVE_COMPLETE_MEMBER'
    assert fingerprint(load_snapshot(snapshot.root).state)==fingerprint(state)


def test_mixed_dimensional_cell_keeps_every_positive_polygon() -> None:
    first = Polygon([(0,0),(1,0),(0,1)])
    tiny = Polygon([(2,0),(2+1e-12,0),(2,1e-12)])
    collection = GeometryCollection([first,LineString([(0,0),(0,-1)]),tiny])
    vertices = _positive_cell_vertices(collection)
    assert len(vertices)==6
    assert np.any(vertices[:,0]>2)
