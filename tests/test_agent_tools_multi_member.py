"""Four physical panels can share one atlas tile with explicit member maps."""

import time
from dataclasses import replace
from pathlib import Path
from typing import cast

import numpy as np
import pytest

from reseam.agent_tools.color_reuse import bake_color_reuse, freeze_target, validate_color_reuse
from reseam.agent_tools.geometry import apply_reuse, check_pair, geometry_report, inspect_uv
from reseam.agent_tools.state import file_hash, load_snapshot, read_object, save_snapshot
from reseam.budget_material import atlas_from_arrays, export_material_glb
from reseam.budget_state import BudgetState, exclusive_faces, owners_and_members
from reseam.joint_types import ReuseGroup
from reseam.mesh import make_mesh, state_from_corner_uv
from reseam.reuse_transform import similarity_transform


def _panels(tmp_path: Path) -> dict[str, object]:
    first = np.array([[1., 0, 0], [1., 1, 0], [1., 1, 1], [1., 0, 1]])
    points = [first, first * [-1, 1, 1], first + [0, 2, 0], first * [-1, 1, 1] + [0, 2, 0]]
    vertices = np.concatenate(points)
    faces = np.array([[0, 1, 2], [0, 2, 3], [4, 6, 5], [4, 7, 6],
                      [8, 9, 10], [8, 10, 11], [12, 14, 13], [12, 15, 14]])
    mesh = make_mesh(vertices, faces)
    corners = .1 + .2 * vertices[faces, 1:]
    corners[4:, :, 0] += .05
    corners[4:, :, 1] += .4
    surface = state_from_corner_uv(mesh, corners, corner_ids=faces)
    groups = (ReuseGroup('a', (0, 1), (2, 3), (1., 0, 0), 0),
              ReuseGroup('b', (4, 5), (6, 7), (1., 0, 0), 0))
    state = BudgetState(surface, groups)
    provenance = tmp_path / 'pairs.npz'
    np.savez(provenance, face_pairs=np.array([[0, 2], [1, 3], [4, 6], [5, 7]]))
    origin = save_snapshot(state, tmp_path / 'origin', {
        'geometry_variant': 'plane_clip_then_exact_mirror', 'source_correspondence': str(provenance),
        'source_manifest': [{'path': str(provenance), 'sha256': file_hash(provenance)}]})
    color = np.full((32, 32, 4), [.4, .6, .8, 1.])
    atlas = atlas_from_arrays(color, np.full((32, 32, 3), [0., 0, 1]),
                              np.full((32, 32, 3), [1., 1., 0]))
    path = tmp_path / 'source.glb'
    export_material_glb(surface, atlas, path)
    source = save_snapshot(state, tmp_path / 'source', {'reference_glb': str(path)})
    return {'snapshot': str(source.root), 'combine_mirror_groups': ['a', 'b'],
            'mode': 'similarity', 'first_to_second': [[1, 0, 0, 0], [0, 1, 0, 2], [0, 0, 1, 0]],
            'mirror_source': {'snapshot': str(origin.root), 'state_hash': origin.state_hash},
            'profile': {'resolution': 64, 'gap_pixels': 2, 'border_pixels': 2,
                        'quality_constraints': False}}


def test_public_four_member_transaction_roundtrip_and_metrics(tmp_path: Path) -> None:
    request = _panels(tmp_path)
    before = load_snapshot(Path(str(request['snapshot'])))
    checked = check_pair(request, tmp_path / 'checked', time.monotonic() + 30)
    assert checked['status'] == 'OK', checked
    result = apply_reuse({'snapshot': request['snapshot'], 'proposal': checked['proposal']},
                         tmp_path / 'apply', time.monotonic() + 30)
    assert result['status'] == 'OK', result
    after = load_snapshot(Path(str(result['snapshot'])))
    assert len(after.state.groups) == 1
    assert len(after.state.groups[0].member_maps) == 3
    assert len(np.unique(owners_and_members(after.state)[0])) == 1
    np.testing.assert_array_equal(before.state.surface.mesh.vertices, after.state.surface.mesh.vertices)
    inspected = inspect_uv({'snapshot': str(after.root), 'profile': request['profile']},
                           tmp_path / 'inspect', time.monotonic() + 30)
    metrics = read_object(Path(str(inspected['metrics_report'])))
    assert metrics['metrics_verified'] is True
    assert metrics['actual_independent_tiles'] == 1
    assert metrics['uv_connected_charts'] == 4
    assert cast(dict[str, object], metrics['global'])['actual_shared_surface_fraction'] == pytest.approx(1)
    assert len(load_snapshot(before.root).state.groups) == 2


def test_bad_member_partition_transform_orientation_and_slave_overlap_fail(tmp_path: Path) -> None:
    request = _panels(tmp_path)
    result = check_pair(request, tmp_path / 'check', time.monotonic() + 30)
    proposal = read_object(Path(str(result['proposal'])))
    snapshot = load_snapshot(Path(str(proposal['candidate_snapshot'])))
    group = snapshot.state.groups[0]
    member = group.member_maps[1]
    for wrong in [replace(member, faces=group.member_maps[0].faces),
                  replace(member, orientation=-member.orientation),
                  replace(member, first_to_member=similarity_transform(
                      [[1, 0, 0, 0], [0, 1, 0, 20], [0, 0, 1, 0]]))]:
        state = snapshot.state.copy()
        state.groups = (replace(group, member_maps=(group.member_maps[0], wrong, group.member_maps[2])),)
        assert geometry_report(state, cast(dict[str, object], request['profile']))['hard_valid'] is False
    with pytest.raises(ValueError, match='MULTI_MEMBER_RELEASE_REQUIRES_UNSHARE'):
        exclusive_faces(snapshot.state, (4,))
    # Each slave's root error is allowed, but their composed relative error is not.
    changed = []
    for index, member in enumerate(group.member_maps):
        matrix = np.array(member.first_to_member)
        matrix[1, 3] += .02 if index == 1 else -.02 if index == 2 else 0
        changed.append(replace(member, first_to_member=similarity_transform(matrix)))
    state = snapshot.state.copy()
    state.groups = (replace(group, member_maps=tuple(changed)),)
    report = geometry_report(state, cast(dict[str, object], request['profile']))
    errors = cast(list[str], report['hard_errors'])
    assert any(error.startswith('OVERLAP_CORRESPONDENCE:4:6') for error in errors)


def test_invalid_request_preserves_parent(tmp_path: Path) -> None:
    request = _panels(tmp_path)
    request['combine_mirror_groups'] = ['a', 'a']
    with pytest.raises(ValueError, match='TWO_DISTINCT_MIRROR_GROUPS_REQUIRED'):
        check_pair(request, tmp_path / 'invalid', time.monotonic() + 30)
    assert len(load_snapshot(Path(str(request['snapshot']))).state.groups) == 2


@pytest.mark.parametrize('layout', ['intrinsic', 'preserve_patches'])
def test_unrelated_face_release_preserves_four_member_group(tmp_path: Path, layout: str) -> None:
    request = _panels(tmp_path)
    checked = check_pair(request, tmp_path / 'check', time.monotonic() + 30)
    proposal = read_object(Path(str(checked['proposal'])))
    source = load_snapshot(Path(str(proposal['candidate_snapshot'])))
    surface = source.state.surface
    vertices = np.concatenate((surface.mesh.vertices, [[3., 0, 0], [3., 1, 0], [3., 0, 1]]))
    faces = np.concatenate((surface.mesh.faces, [[16, 17, 18]]))
    corners = np.concatenate((surface.uv[surface.uv_faces], [[[.8, .1], [.9, .1], [.8, .2]]]))
    identities = np.concatenate((surface.uv_faces, [np.arange(len(surface.uv), len(surface.uv) + 3)]))
    state = BudgetState(state_from_corner_uv(make_mesh(vertices, faces), corners, corner_ids=identities),
                        source.state.groups)
    with_extra = save_snapshot(state, tmp_path / 'with_extra', {})
    released = apply_reuse({'snapshot': str(with_extra.root), 'profile': request['profile'],
        'action': 'exclusive', 'exclusive_faces': [8], 'exclusive_layout': layout},
        tmp_path / 'release', time.monotonic() + 30)
    assert released['status'] == 'OK', released
    result = load_snapshot(Path(str(released['snapshot'])))
    assert result.state.groups == source.state.groups
    assert len(result.state.groups[0].member_maps) == 3


def test_legacy_groups_keep_two_member_defaults() -> None:
    group = ReuseGroup('old', (0,), (1,), (1., 0, 0), 0)
    assert group.member_maps == ()


def test_target_selection_preserves_protected_other_groups_and_four_member_bake(tmp_path: Path) -> None:
    request = _panels(tmp_path)
    target_dir = tmp_path / 'selected_target'
    target_dir.mkdir()
    frozen = freeze_target({'snapshot': request['snapshot'], 'policy': 'symmetrize',
        'geometry_profile': request['profile'], 'symmetrize_group_ids': ['a'],
        'protected_faces': [4, 5, 6, 7]}, target_dir, time.monotonic() + 30)
    target = read_object(Path(str(frozen['target'])))
    assert [group['group_id'] for group in cast(list[dict[str, object]], target['groups'])] == ['a']
    checked = check_pair(request, tmp_path / 'check', time.monotonic() + 30)
    proposal = read_object(Path(str(checked['proposal'])))
    snapshot = str(proposal['candidate_snapshot'])
    directory = tmp_path / 'four_target'
    directory.mkdir()
    frozen = freeze_target({'snapshot': snapshot, 'policy': 'symmetrize',
                           'geometry_profile': request['profile']}, directory, time.monotonic() + 30)
    bake_dir = tmp_path / 'bake'
    bake_dir.mkdir()
    baked = bake_color_reuse({'snapshot': snapshot, 'target': frozen['target'], 'resolution': 64,
        'padding_pixels': 2}, bake_dir, time.monotonic() + 30)
    validation_dir = tmp_path / 'validation'
    validation_dir.mkdir()
    validation = validate_color_reuse({'snapshot': snapshot, 'bake': baked['bake'],
        'render_size': 16, 'mip_levels': [0, 1, 2]}, validation_dir, time.monotonic() + 30)
    assert validation['quality_accepted'] is True, validation
