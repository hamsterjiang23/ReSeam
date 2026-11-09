"""Generated face/corner provenance survives coincident IDs and float32 storage."""

import time
from pathlib import Path

import numpy as np
import pytest

from reseam.agent_tools.geometry import check_pair
from reseam.agent_tools.mirror_identity import copy_mirror_identity
from reseam.agent_tools.state import Snapshot, file_hash, load_snapshot, read_object, save_snapshot
from reseam.budget_state import BudgetState
from reseam.mesh import make_mesh, state_from_corner_uv


def _fixture(tmp_path: Path, duplicate: bool = False) -> tuple[Snapshot, dict[str, object]]:
    offset = .00025
    first = np.array([[.123456789, 0, 0], [.123456789, 1, 0],
                      [.123456789, 1, 1], [.123456789, 0, 1]])
    second = first.copy()
    second[:, 0] = 2 * offset - second[:, 0]
    vertices = np.concatenate((first, second)).astype(np.float32).astype(np.float64)
    faces = np.array([[0, 1, 2], [0, 2, 3], [4, 6, 5], [4, 7, 6]], dtype=np.int64)
    if duplicate:
        vertices = np.concatenate((vertices, vertices))
        faces = np.concatenate((faces, faces + 8))
    mesh = make_mesh(vertices, faces)
    corners = .1 + vertices[faces, 1:] * .2
    corners[[2, 3], :, 0] += .5
    corners[[2, 3], :, 1] = .5 - corners[[2, 3], :, 1]
    pairs = [[0, 2], [1, 3], [4, 6], [5, 7]] if duplicate else [[0, 2], [1, 3]]
    path = tmp_path / 'correspondence.npz'
    np.savez(path, face_pairs=np.array(pairs, dtype=np.int64))
    state = BudgetState(state_from_corner_uv(mesh, corners, corner_ids=mesh.faces))
    metadata: dict[str, object] = {
        'geometry_variant': 'plane_clip_then_exact_mirror', 'source_correspondence': str(path),
        'source_manifest': [{'path': str(path), 'sha256': file_hash(path)}],
    }
    origin = save_snapshot(state, tmp_path / 'origin', metadata)
    current = save_snapshot(state, tmp_path / 'current', {})
    return current, {'snapshot': str(origin.root), 'state_hash': origin.state_hash}


def test_public_exact_uses_explicit_source_and_keeps_coordinate_only_behavior(tmp_path: Path) -> None:
    current, source = _fixture(tmp_path)
    request: dict[str, object] = {
        'snapshot': str(current.root), 'first_faces': [0, 1], 'second_faces': [2, 3],
        'mode': 'exact', 'plane_normal': [1, 0, 0], 'plane_offset': .00025,
        'profile': {'resolution': 64, 'gap_pixels': 2, 'border_pixels': 2},
    }
    old = check_pair(request, tmp_path / 'coordinate_only', time.monotonic() + 20)
    assert old['status'] == 'REJECTED' and old['reason'] == 'EXACT_TOPOLOGY_NOT_MATCHED'
    request['mirror_source'] = source
    result = check_pair(request, tmp_path / 'identity', time.monotonic() + 20)
    assert result['status'] == 'OK', result
    assert result['selected_method'] == 'source_identity_reflection_copy'
    proposal = read_object(Path(str(result['proposal'])))
    candidate = load_snapshot(Path(str(proposal['candidate_snapshot'])))
    assert len(candidate.state.groups) == 1
    surface = candidate.state.surface
    np.testing.assert_array_equal(surface.uv[surface.uv_faces[[0, 1]]],
                                  surface.uv[surface.uv_faces[[2, 3]]][:, [0, 2, 1]])
    np.testing.assert_array_equal(surface.mesh.vertices, current.state.surface.mesh.vertices)
    record = candidate.metadata['mirror_identity']
    assert isinstance(record, dict) and record['coordinate_welding'] is False


def test_coincident_unrelated_vertices_keep_distinct_uv_identity(tmp_path: Path) -> None:
    current, source = _fixture(tmp_path, duplicate=True)
    mapped, record = copy_mirror_identity(current, (0, 1, 4, 5), (2, 3, 6, 7),
                                          np.array([1., 0, 0]), .00025, source)
    assert record['vertex_pairs'] == 8
    assert set(mapped[1][:2].ravel()).isdisjoint(mapped[1][2:].ravel())


@pytest.mark.parametrize('mode', ['paired_domain', 'similarity', 'topology'])
def test_source_cannot_be_silently_ignored_by_other_modes(tmp_path: Path, mode: str) -> None:
    with pytest.raises(ValueError, match='MIRROR_SOURCE_REQUIRES'):
        check_pair({'mirror_source': {}, 'mode': mode}, tmp_path / 'invalid', time.monotonic() + 5)


def test_stale_source_and_changed_geometry_are_rejected(tmp_path: Path) -> None:
    current, source = _fixture(tmp_path)
    source['state_hash'] = 'stale'
    with pytest.raises(ValueError, match='STATE_HASH_MISMATCH'):
        copy_mirror_identity(current, (0, 1), (2, 3), np.array([1., 0, 0]), .00025, source)
    origin = load_snapshot(Path(str(source['snapshot'])))
    source['state_hash'] = origin.state_hash
    mesh = current.state.surface.mesh
    moved = mesh.vertices.copy()
    moved[:, 2] += .001
    changed = BudgetState(state_from_corner_uv(make_mesh(moved, mesh.faces), current.state.surface.uv[current.state.surface.uv_faces],
                                              corner_ids=current.state.surface.uv_faces))
    other = save_snapshot(changed, tmp_path / 'changed', {})
    with pytest.raises(ValueError, match='INDEXED_GEOMETRY_MISMATCH'):
        copy_mirror_identity(other, (0, 1), (2, 3), np.array([1., 0, 0]), .00025, source)


def test_wrong_plane_and_unpaired_selection_are_rejected(tmp_path: Path) -> None:
    current, source = _fixture(tmp_path)
    with pytest.raises(ValueError, match='REFLECTION_MISMATCH'):
        copy_mirror_identity(current, (0, 1), (2, 3), np.array([1., 0, 0]), .001, source)
    with pytest.raises(ValueError, match='SELECTION_NOT_PAIRED'):
        copy_mirror_identity(current, (0,), (3,), np.array([1., 0, 0]), .00025, source)


def test_correspondence_must_be_bound_and_unchanged(tmp_path: Path) -> None:
    current, source = _fixture(tmp_path)
    origin = load_snapshot(Path(str(source['snapshot'])))
    unbound = save_snapshot(origin.state, tmp_path / 'unbound', {
        'geometry_variant': 'plane_clip_then_exact_mirror',
        'source_correspondence': origin.metadata['source_correspondence'],
    })
    with pytest.raises(ValueError, match='NOT_HASH_BOUND'):
        copy_mirror_identity(current, (0, 1), (2, 3), np.array([1., 0, 0]), .00025,
                             {'snapshot': str(unbound.root), 'state_hash': unbound.state_hash})
    Path(str(origin.metadata['source_correspondence'])).write_bytes(b'changed')
    with pytest.raises(ValueError, match='SNAPSHOT_SOURCE_CHANGED'):
        copy_mirror_identity(current, (0, 1), (2, 3), np.array([1., 0, 0]), .00025, source)
