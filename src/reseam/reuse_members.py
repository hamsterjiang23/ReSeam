"""Explicit root-to-member maps for more than two shared surfaces."""

import numpy as np

from .joint_types import ReuseGroup, SimilarityTransform
from .reuse_transform import similarity_transform, transform_parity
from .types import IntArray


def member_transforms(group: ReuseGroup, labels: IntArray) -> dict[tuple[int, int], SimilarityTransform]:
    """Validate a complete chart partition and compose every member-to-member map."""
    if (len(group.member_maps) < 2 or group.first_to_second is not None
            or group.second_orientation != group.member_maps[0].orientation):
        raise ValueError('INVALID_MULTI_MEMBER_GROUP')
    domains = (group.first_faces, *(member.faces for member in group.member_maps))
    flattened = tuple(face for domain in domains[1:] for face in domain)
    if len(set(flattened)) != len(flattened) or set(flattened) != set(group.second_faces):
        raise ValueError('INVALID_MULTI_MEMBER_PARTITION')
    for domain in domains:
        if (not domain or min(domain) < 0 or max(domain) >= len(labels)
                or len(set(domain)) != len(domain) or len(np.unique(labels[list(domain)])) != 1
                or set(np.flatnonzero(labels == labels[domain[0]])) != set(domain)):
            raise ValueError('MULTI_MEMBER_REQUIRES_COMPLETE_SINGLE_CHARTS')
    matrices = [np.eye(4)]
    for member in group.member_maps:
        transform = similarity_transform(member.first_to_member)
        if member.orientation != group.first_orientation * transform_parity(transform):
            raise ValueError('MULTI_MEMBER_ORIENTATION_MISMATCH')
        matrix = np.eye(4)
        matrix[:3] = transform
        matrices.append(matrix)
    return {(first, second): similarity_transform((target @ np.linalg.inv(source))[:3])
            for first, source in enumerate(matrices) for second, target in enumerate(matrices)
            if first != second}
