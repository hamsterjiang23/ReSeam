"""Combine two authored mirror pairs using checked cross-part correspondence."""

from dataclasses import replace
from pathlib import Path

import numpy as np

from ..joint_types import ReuseGroup, ReuseMember
from ..mesh import state_from_corner_uv
from ..reuse_transform import similarity_transform, transform_parity
from .mirror_identity import copy_mirror_identity
from .state import load_snapshot, object_hash, read_object, require_snapshot, save_snapshot, write_json


def combine_mirror_groups(request: dict[str, object], output: Path, deadline: float) -> dict[str, object]:
    """Keep all four surface identities and validate every pairwise UV intersection."""
    from .geometry import (
        _edited_metadata,
        _packed,
        _profile,
        _rejected,
        _unshare,
        check_pair,
        geometry_report,
    )

    ids = request.get('combine_mirror_groups')
    if (not isinstance(ids, list) or len(ids) != 2
            or any(not isinstance(value, str) for value in ids) or len(set(ids)) != 2):
        raise ValueError('TWO_DISTINCT_MIRROR_GROUPS_REQUIRED')
    if request.get('mode') != 'similarity' or request.get('mirror_source') is None:
        raise ValueError('COMBINING_MIRROR_GROUPS_REQUIRES_SIMILARITY_AND_MIRROR_SOURCE')
    if request.get('exclusive_faces') or request.get('refine_iterations'):
        raise ValueError('COMBINING_MIRROR_GROUPS_REQUIRES_COMPLETE_MEMBERS')
    snapshot = require_snapshot(request)
    selected = {group.group_id: group for group in snapshot.state.groups if group.group_id in ids}
    if len(selected) != 2:
        raise ValueError('UNKNOWN_REUSE_GROUP')
    first, second = (selected[value] for value in ids)
    if (any(group.member_maps or group.first_to_second is not None for group in [first, second])
            or first.plane_normal != second.plane_normal or first.plane_offset != second.plane_offset):
        raise ValueError('COMBINING_REQUIRES_TWO_SIMPLE_GROUPS_ON_SAME_MIRROR_PLANE')
    profile = _profile(request)
    working = save_snapshot(_unshare(snapshot.state, ids), output / 'working_parent',
                            _edited_metadata(snapshot, 'reuse.check_pair:combine_prepare', profile), snapshot)
    pair_request = {key: value for key, value in request.items()
                    if key not in ('combine_mirror_groups', 'mirror_source', 'state_hash')}
    pair_request.update(snapshot=str(working.root), first_faces=list(first.first_faces),
                        second_faces=list(second.first_faces), plane_normal=list(first.plane_normal),
                        plane_offset=first.plane_offset)
    checked = check_pair(pair_request, output / 'cross_part_check', deadline)
    if checked.get('status') != 'OK':
        return _rejected(output, {'reason': 'CROSS_PART_CHECK_REJECTED', 'cross_part_result': checked})
    proposal = read_object(Path(str(checked['proposal'])))
    candidate_snapshot = load_snapshot(Path(str(proposal['candidate_snapshot'])))
    candidate = candidate_snapshot.state.copy()
    new_id = str(proposal['semantic_group_id'])
    cross_group = next(group for group in candidate.groups if group.group_id == new_id)
    transform = similarity_transform(proposal['first_to_second'])
    normal = np.asarray(first.plane_normal)
    reflection = np.eye(4)
    reflection[:3, :3] -= 2 * np.outer(normal, normal)
    reflection[:3, 3] = 2 * first.plane_offset * normal
    direct = np.eye(4)
    direct[:3] = transform
    mirror_records: list[dict[str, object]] = []
    for group in [first, second]:
        current = replace(candidate_snapshot, state=candidate)
        mapped, record = copy_mirror_identity(current, group.first_faces, group.second_faces,
                                              normal, first.plane_offset, request['mirror_source'])
        corners = candidate.surface.uv[candidate.surface.uv_faces].copy()
        identities = candidate.surface.uv_faces.copy()
        corners[list(group.second_faces)], identities[list(group.second_faces)] = mapped
        candidate.surface = state_from_corner_uv(candidate.surface.mesh, corners, corner_ids=identities)
        mirror_records.append(record)
    sign = cross_group.first_orientation
    members = (ReuseMember(first.second_faces, -sign, similarity_transform(reflection[:3])),
               ReuseMember(second.first_faces, sign * transform_parity(transform), transform),
               ReuseMember(second.second_faces, -sign * transform_parity(transform),
                           similarity_transform((reflection @ direct)[:3])))
    combined = ReuseGroup(new_id, first.first_faces,
                          first.second_faces + second.first_faces + second.second_faces,
                          first.plane_normal, first.plane_offset, sign, -sign, None, members)
    candidate.groups = tuple(combined if group.group_id == new_id else group for group in candidate.groups)
    candidate.invalidate()
    candidate = _packed(candidate, profile, deadline=deadline)
    report = geometry_report(candidate, profile)
    active = report.get('active_reuse_group_ids', [])
    if report.get('hard_valid') is not True or not isinstance(active, list) or new_id not in active:
        return _rejected(output, {'reason': 'FOUR_MEMBER_FULL_STATE_REJECTED', 'geometry': report,
                                  'cross_part_result': checked, 'mirror_identity': mirror_records})
    evidence = {'source_group_ids': ids, 'physical_members': 4, 'cross_part_result': checked,
                'mirror_identity': mirror_records, 'geometry_changed': False,
                'scope': 'Every strict-positive cross-member intersection checked forward and inverse'}
    metadata = _edited_metadata(snapshot, 'reuse.check_pair:combine_mirror_groups', profile)
    metadata['multi_member_reuse'] = evidence
    saved = save_snapshot(candidate, output / 'checked_snapshot', metadata, snapshot)
    final_proposal = {**proposal, 'base_state_hash': snapshot.state_hash,
                      'candidate_state_hash': saved.state_hash,
                      'candidate_snapshot': str(saved.root / 'snapshot.json'),
                      'first_faces': list(combined.first_faces), 'second_faces': list(combined.second_faces),
                      'geometry': report, 'selected_method': 'checked_cross_part_with_exact_mirror_members',
                      'diagnostics': evidence}
    final_proposal.pop('proposal_hash', None)
    final_proposal['proposal_hash'] = object_hash(final_proposal)
    path = output / 'proposal.json'
    write_json(path, final_proposal)
    return {'status': 'OK', 'execution_success': True, 'proposal': str(path.resolve()),
            'report': str(path.resolve()), 'geometry_passed': report['valid'],
            'hard_geometry_passed': True, 'final_quality_passed': None,
            'selected_method': final_proposal['selected_method'], 'shared_group_id': new_id,
            'shared_group_ids': [new_id], 'physical_members': 4}
