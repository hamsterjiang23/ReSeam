"""Bounded similarity hypotheses for repeated regions; full UV cells decide safety."""

import time
from itertools import permutations, product

import numpy as np
from scipy.spatial import KDTree

from ..joint_types import SimilarityTransform
from ..reuse_transform import similarity_transform
from ..types import FloatArray, MeshState


def _samples(surface: MeshState, faces: tuple[int, ...]) -> tuple[FloatArray, FloatArray, FloatArray]:
    triangles = surface.mesh.vertices[surface.mesh.faces[list(faces)]]
    normals = np.cross(triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0])
    areas = np.linalg.norm(normals, axis=1)
    normals /= areas[:, None]
    bary = np.asarray([[1., 0, 0], [0, 1., 0], [0, 0, 1.], [1/3, 1/3, 1/3]])
    points = np.einsum("sc,fcd->fsd", bary, triangles).reshape(-1, 3)
    weights = np.repeat(areas, 4)
    sampled_normals = np.repeat(normals, 4, axis=0)
    if len(points) > 1024:
        selected = np.linspace(0, len(points)-1, 1024, dtype=np.int64)
        points, sampled_normals, weights = points[selected], sampled_normals[selected], weights[selected]
    return points, sampled_normals, weights / weights.sum()


def _fit(source: FloatArray, target: FloatArray, weights: FloatArray, parity: int) -> FloatArray:
    source_center, target_center = weights @ source, weights @ target
    x, y = source-source_center, target-target_center
    u, singular, vt = np.linalg.svd((y * weights[:, None]).T @ x)
    signs = np.ones(3)
    signs[-1] = parity * np.linalg.det(u @ vt)
    q = (u * signs) @ vt
    scale = float(singular @ signs / np.sum(weights[:, None] * x**2))
    if scale <= 1e-12:
        raise ValueError("SINGULAR_SIMILARITY_FIT")
    linear = scale*q
    return np.column_stack((linear, target_center-linear @ source_center))


def fit_similarity_candidates(
    surface: MeshState, first: tuple[int, ...], second: tuple[int, ...],
    normal: FloatArray, offset: float, deadline: float,
) -> tuple[list[SimilarityTransform], dict[str, object]]:
    """PCA signed-axis hypotheses then parity-preserving point ICP, no welding.

    Sampled nearest-neighbor error only orders hypotheses. Boundary, normals,
    original identity, per-face expression and full cells are checked downstream.
    Symmetric/planar domains can have multiple valid hypotheses; they are logged.
    """
    a, an, aw = _samples(surface, first)
    b, bn, bw = _samples(surface, second)
    ca, cb = aw @ a, bw @ b
    xa, xb = a-ca, b-cb
    ea, va = np.linalg.eigh((xa*aw[:, None]).T @ xa)
    eb, vb = np.linalg.eigh((xb*bw[:, None]).T @ xb)
    variance_a, variance_b = float(np.sum(aw[:, None]*xa**2)), float(np.sum(bw[:, None]*xb**2))
    if min(variance_a, variance_b) <= 1e-24:
        raise ValueError("REGION_VARIANCE_TOO_SMALL_FOR_SIMILARITY")
    scale = float(np.sqrt(variance_b/variance_a))
    diagonal = float(np.linalg.norm(np.ptp(surface.mesh.vertices, axis=0)))
    tree_a, tree_b = KDTree(a), KDTree(b)

    def score(matrix: FloatArray) -> tuple[float, float, float]:
        linear, t = matrix[:, :3], matrix[:, 3]
        s = float(np.sqrt(np.sum(linear**2)/3))
        q = linear/s
        distance, ids = tree_b.query(a @ linear.T+t)
        back, back_ids = tree_a.query((b-t) @ np.linalg.inv(linear).T)
        distance_score = float((aw @ distance + bw @ back)/2/diagonal)
        agreement = float((aw @ np.clip(np.sum((an @ q.T)*bn[ids], axis=1), -1, 1)
                           + bw @ np.clip(np.sum((bn @ q)*an[back_ids], axis=1), -1, 1))/2)
        return distance_score + .005*(1-agreement), distance_score, agreement

    matrices: list[FloatArray] = []
    for order in permutations(range(3)):
        for signs in product((-1., 1.), repeat=3):
            q = (vb[:, order]*np.asarray(signs)) @ va.T
            linear = scale*q
            matrices.append(np.column_stack((linear, cb-linear @ ca)))
    # Keep the established reflection hypothesis and translated rigid identity.
    reflected = np.eye(3)-2*np.outer(normal, normal)
    matrices.extend((np.column_stack((reflected, 2*offset*normal)),
                     np.column_stack((scale*np.eye(3), cb-scale*ca))))
    ranked = sorted(((score(matrix), matrix) for matrix in matrices), key=lambda item: item[0][0])
    refined: list[tuple[tuple[float, float, float], FloatArray, int]] = []
    for initial_score, seed in ranked[:10]:
        if time.monotonic() >= deadline:
            raise TimeoutError("SIMILARITY_ALIGNMENT_TIMEOUT")
        matrix, old_score, steps = seed, initial_score, 0
        parity = 1 if np.linalg.det(matrix[:, :3]) > 0 else -1
        for steps in range(1, 17):
            if time.monotonic() >= deadline:
                raise TimeoutError("SIMILARITY_ALIGNMENT_TIMEOUT")
            _, indices = tree_b.query(a @ matrix[:, :3].T+matrix[:, 3])
            proposal = _fit(a, b[indices], aw, parity)
            proposed_score = score(proposal)
            if proposed_score[0] >= old_score[0]-1e-14:
                break
            matrix, old_score = proposal, proposed_score
        refined.append((old_score, matrix, steps))
    # Initial hypotheses are retained: discrete ICP samples can worsen an exact
    # surface relation when the two triangulations have different vertices.
    refined.extend((value, matrix, 0) for value, matrix in ranked[:4])
    refined.sort(key=lambda row: row[0][0])
    selected: list[SimilarityTransform] = []
    evidence: list[dict[str, object]] = []
    for value, matrix, steps in refined:
        if any(np.allclose(matrix, old, atol=1e-8, rtol=0) for old in selected):
            continue
        selected.append(similarity_transform(matrix))
        evidence.append({"first_to_second": matrix.tolist(), "ranking_score": value[0],
                         "sampled_distance_fraction": value[1], "normal_agreement": value[2],
                         "determinant_parity": 1 if np.linalg.det(matrix[:, :3]) > 0 else -1,
                         "icp_iterations": steps})
        if len(selected) == 6:
            break
    return selected, {"method": "area-weighted PCA signed axes + parity-preserving similarity ICP",
                      "normalization": "original all-vertex bounding-box diagonal",
                      "normalization_scale": diagonal, "first_pca_eigenvalues": ea.tolist(),
                      "second_pca_eigenvalues": eb.tolist(), "initial_hypothesis_count": len(matrices),
                      "ranked_hypotheses": evidence, "unique_alignment_proven": False,
                      "selection_scope": "sampled heuristic only; final correspondence and UV cells remain mandatory"}
