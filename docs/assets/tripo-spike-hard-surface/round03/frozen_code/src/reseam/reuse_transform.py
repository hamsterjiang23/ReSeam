"""Explicit similarity maps authorize a coordinate comparison, never geometry edits."""

import numpy as np

from .joint_types import SimilarityTransform
from .surface_match import reflect
from .types import FloatArray


def similarity_transform(value: object) -> SimilarityTransform:
    """Accept only finite sQ+t, s>0 and Q orthogonal; nonuniform affine maps fail."""
    matrix = np.asarray(value, dtype=float)
    if matrix.shape != (3, 4) or not np.isfinite(matrix).all():
        raise ValueError("FINITE_3_BY_4_SIMILARITY_REQUIRED")
    linear = matrix[:, :3]
    scale = float(np.sqrt(np.sum(linear**2) / 3))
    if scale <= 1e-12:
        raise ValueError("SINGULAR_SIMILARITY_TRANSFORM")
    orthogonal = linear / scale
    if not np.allclose(orthogonal.T @ orthogonal, np.eye(3), atol=1e-8, rtol=0):
        raise ValueError("NONUNIFORM_AFFINE_TRANSFORM_FORBIDDEN")
    a, b, c = matrix.tolist()
    return (tuple(a), tuple(b), tuple(c))  # type: ignore[return-value]


def transform_parity(transform: SimilarityTransform | None) -> int:
    return -1 if transform is None or np.linalg.det(np.asarray(transform)[:, :3]) < 0 else 1


def mapped_points(points: FloatArray, normal: FloatArray, offset: float,
                  transform: SimilarityTransform | None = None, *, inverse: bool = False) -> FloatArray:
    if transform is None:
        return reflect(points, normal, offset)
    matrix = np.asarray(transform)
    linear, translation = matrix[:, :3], matrix[:, 3]
    if inverse:
        return (points - translation) @ np.linalg.inv(linear).T
    return points @ linear.T + translation


def mapped_normals(normals: FloatArray, normal: FloatArray,
                   transform: SimilarityTransform | None = None, *, inverse: bool = False) -> FloatArray:
    if transform is None:
        return reflect(normals, normal, 0)
    linear = np.asarray(transform)[:, :3]
    scale = float(np.sqrt(np.sum(linear**2) / 3))
    orthogonal = linear / scale
    return normals @ (orthogonal if inverse else orthogonal.T)
