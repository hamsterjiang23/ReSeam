"""Conservative mirror correspondences and explicitly whitelisted UV reuse previews.

This is an edge/face correspondence prototype, not the paper's optimal transport solver.
Normal maps and production approval deliberately stay outside the Base Color gate.
"""

from dataclasses import asdict, dataclass
from typing import Any

import numpy as np
from scipy.spatial import KDTree
from shapely import Polygon, STRtree, union_all

from .mesh import DisjointSet, state_from_corner_uv
from .types import FloatArray, MeshState
from .validation import jacobians, validate


@dataclass(frozen=True)
class ReuseProfile:
    geometry_tolerance: float = 0.005  # fraction of sqrt(total 3D surface area)
    normal_angle_degrees: float = 15.0
    color_mean_limit: float = 0.025  # linear RGB; provisional preview gate
    color_p95_limit: float = 0.08
    anisotropy_ratio_limit: float = 1.10
    density_ratio_tolerance: float = 0.10
    minimum_patch_faces: int = 3

    def __post_init__(self) -> None:
        values = (
            self.geometry_tolerance,
            self.normal_angle_degrees,
            self.color_mean_limit,
            self.color_p95_limit,
            self.anisotropy_ratio_limit,
            self.density_ratio_tolerance,
        )
        if not np.isfinite(values).all() or min(values) <= 0 or self.minimum_patch_faces < 1:
            raise ValueError("Invalid reuse preview profile")


def srgb_to_linear(image: FloatArray) -> FloatArray:
    return np.where(image <= 0.04045, image / 12.92, ((image + 0.055) / 1.055) ** 2.4)


def sample_texture(image: FloatArray, uv: FloatArray) -> FloatArray:
    """Bilinear clamp sampler, UV bottom-left and image top-left, pixel-centre convention."""
    height, width = image.shape[:2]
    x = np.clip(uv[..., 0] * width - 0.5, 0, width - 1)
    y = np.clip((1 - uv[..., 1]) * height - 0.5, 0, height - 1)
    ix, iy = np.floor(x).astype(int), np.floor(y).astype(int)
    jx, jy = np.minimum(ix + 1, width - 1), np.minimum(iy + 1, height - 1)
    dx, dy = (x - ix)[..., None], (y - iy)[..., None]
    return (1 - dy) * ((1 - dx) * image[iy, ix] + dx * image[iy, jx]) + dy * (
        (1 - dx) * image[jy, ix] + dx * image[jy, jx]
    )


def barycentric_samples() -> FloatArray:
    # 28 strictly interior, symmetric sample locations; not an image-error guarantee.
    return np.asarray([(i + 1, j + 1, 7 - i - j) for i in range(7) for j in range(7 - i)], dtype=float) / 9


def mirror_mapping(state: MeshState, normal: FloatArray) -> dict[str, Any]:
    vertices = state.mesh.vertices
    normal = normal / np.linalg.norm(normal)
    tree = KDTree(vertices)
    offset = float(np.median(vertices @ normal))
    for _ in range(12):
        reflected = vertices - 2 * (vertices @ normal - offset)[:, None] * normal
        _, index = tree.query(reflected)
        offset = float(np.median(((vertices + vertices[index]) * 0.5) @ normal))
    reflected = vertices - 2 * (vertices @ normal - offset)[:, None] * normal
    distances, index = tree.query(reflected)
    index = np.asarray(index, dtype=np.int64)
    lookup = {tuple(sorted(map(int, face))): i for i, face in enumerate(state.mesh.faces)}
    mapped_faces = np.asarray(
        [lookup.get(tuple(sorted(map(int, index[face]))), -1) for face in state.mesh.faces], dtype=np.int64
    )
    mutual = index[index] == np.arange(len(vertices))
    return {
        "normal": normal,
        "offset": offset,
        "vertex_map": index,
        "face_map": mapped_faces,
        "distances": distances / state.mesh.scale,
        "mutual": mutual,
    }


def propose_reuse(
    state: MeshState, texture: FloatArray, profile: ReuseProfile, projected_faces: np.ndarray | None = None
) -> dict[str, Any]:
    if not validate(state, tau=None)["valid"]:
        raise ValueError("Source UV invalid")
    mesh = state.mesh
    if texture.ndim != 3 or texture.shape[2] not in (3, 4) or not np.isfinite(texture).all():
        raise ValueError("Expected finite RGB or RGBA texture")
    if projected_faces is not None and projected_faces.shape != (len(mesh.faces),):
        raise ValueError("Projection mask face count differs")
    tri = mesh.vertices[mesh.faces]
    normals = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
    normals /= np.linalg.norm(normals, axis=1)[:, None]
    center = np.sum(tri.mean(axis=1) * mesh.areas[:, None], axis=0) / mesh.areas.sum()
    covariance = (mesh.vertices - center).T @ (mesh.vertices - center)
    _, axes = np.linalg.eigh(covariance)
    proposals = [mirror_mapping(state, n) for n in np.concatenate((np.eye(3), axes.T))]
    corners = state.uv[state.uv_faces]
    bary = barycentric_samples()
    linear = srgb_to_linear(texture[..., :3])
    old_color = sample_texture(linear, np.einsum("sc,fcd->fsd", bary, corners))
    singular = np.linalg.svd(jacobians(state), compute_uv=False)
    old_ratio = singular[:, 0] / singular[:, 1]
    candidates: list[dict[str, Any]] = []
    planes: list[dict[str, Any]] = []
    dedup: set[tuple[tuple[int, int], ...]] = set()
    for plane_id, plane in enumerate(proposals):
        normal, offset = plane["normal"], plane["offset"]
        mapping, vmap = plane["face_map"], plane["vertex_map"]
        max_distance = plane["distances"][mesh.faces].max(axis=1)
        side = tri.mean(axis=1) @ normal - offset
        eligible = (mapping >= 0) & (mapping != np.arange(len(mapping)))
        eligible &= np.all(plane["mutual"][mesh.faces], axis=1)
        eligible &= max_distance <= profile.geometry_tolerance
        eligible &= side > mesh.scale * 1e-8
        donor_faces = np.flatnonzero(eligible)
        records: dict[int, dict[str, Any]] = {}
        for donor in donor_faces:
            owner = int(mapping[donor])
            if mapping[owner] != donor or side[owner] >= -mesh.scale * 1e-8:
                continue
            reflected_normal = normals[donor] - 2 * float(normals[donor] @ normal) * normal
            alignment = float(np.clip(reflected_normal @ normals[owner], -1, 1))
            if alignment < np.cos(np.radians(profile.normal_angle_degrees)):
                continue
            perm = np.asarray(
                [int(np.flatnonzero(mesh.faces[owner] == vmap[v])[0]) for v in mesh.faces[donor]]
            )
            transferred = corners[owner, perm]
            new_color = sample_texture(linear, bary @ transferred)
            color_error = np.mean(np.abs(new_color - old_color[donor]), axis=1)
            confident = projected_faces is None or bool(projected_faces[donor] and projected_faces[owner])
            if texture.shape[2] == 4:
                confident = confident and bool(
                    np.all(sample_texture(texture[..., 3:4], bary @ corners[donor]) >= 0.99)
                    and np.all(sample_texture(texture[..., 3:4], bary @ transferred) >= 0.99)
                )
            # Evaluate the donor triangle directly to avoid modifying neighboring UV copies.
            donor_geometry = tri[donor] / mesh.scale
            e1, e2 = donor_geometry[1] - donor_geometry[0], donor_geometry[2] - donor_geometry[0]
            length = np.linalg.norm(e1)
            x = np.asarray(
                [[length, np.dot(e1, e2) / length], [0, np.linalg.norm(np.cross(e1, e2)) / length]]
            )
            y = np.stack((transferred[1] - transferred[0], transferred[2] - transferred[0]), axis=1)
            new_singular = np.linalg.svd(y @ np.linalg.inv(x), compute_uv=False)
            stretch_ratio = float((new_singular[0] / new_singular[1]) / old_ratio[donor])
            density_ratio = float(np.sqrt(np.prod(new_singular) / np.prod(singular[donor])))
            passed = confident and (
                float(color_error.mean()) <= profile.color_mean_limit
                and float(np.quantile(color_error, 0.95)) <= profile.color_p95_limit
                and stretch_ratio <= profile.anisotropy_ratio_limit
                and abs(density_ratio - 1) <= profile.density_ratio_tolerance
            )
            records[int(donor)] = {
                "donor": int(donor),
                "owner": owner,
                "permutation": perm.tolist(),
                "geometry_error": float(max_distance[donor]),
                "color_mean": float(color_error.mean()),
                "color_p95": float(np.quantile(color_error, 0.95)),
                "anisotropy_ratio": stretch_ratio,
                "density_ratio": density_ratio,
                "passes_preview_gate": passed,
                "sample_confident": confident,
            }
        planes.append(
            {
                "plane_id": plane_id,
                "normal": normal.tolist(),
                "offset": offset,
                "vertex_error_median": float(np.median(plane["distances"])),
                "paired_donor_faces": len(records),
                "gate_pass_faces": sum(r["passes_preview_gate"] for r in records.values()),
                "pair_diagnostics": list(records.values()),
            }
        )
        dsu = DisjointSet(len(mesh.faces))
        for entries in mesh.edge_faces:
            if len(entries) == 2:
                a, b = entries[0][0], entries[1][0]
                if a in records and b in records:
                    dsu.union(a, b)
        groups: dict[int, list[int]] = {}
        for fi in records:
            groups.setdefault(dsu.find(fi), []).append(fi)
        for faces in groups.values():
            if len(faces) < profile.minimum_patch_faces:
                continue
            pairs = [records[f] for f in faces]
            key = tuple(sorted((p["donor"], p["owner"]) for p in pairs))
            if key in dedup:
                continue
            dedup.add(key)
            failures = []
            if not all(p["sample_confident"] for p in pairs):
                failures.append("PROJECTION_OR_ALPHA_UNCERTAIN")
            if max(p["color_mean"] for p in pairs) > profile.color_mean_limit:
                failures.append("BASE_COLOR_MEAN")
            if max(p["color_p95"] for p in pairs) > profile.color_p95_limit:
                failures.append("BASE_COLOR_P95")
            if max(p["anisotropy_ratio"] for p in pairs) > profile.anisotropy_ratio_limit:
                failures.append("STRETCH_INCREASE")
            if max(abs(p["density_ratio"] - 1) for p in pairs) > profile.density_ratio_tolerance:
                failures.append("DENSITY_ALLOCATION_CHANGE")
            candidates.append(
                {
                    "candidate_id": f"mirror_{len(candidates):03d}",
                    "plane_id": plane_id,
                    "pairs": pairs,
                    "donor_area_fraction": float(mesh.areas[faces].sum() / mesh.areas.sum()),
                    "status": "DIAGNOSTIC_REJECTED" if failures else "PENDING_HUMAN",
                    "rejection_reasons": failures,
                    "material_scope": "BASE_COLOR_ONLY",
                }
            )
    candidates.sort(key=lambda p: -p["donor_area_fraction"])
    return {
        "profile": asdict(profile),
        "planes": planes,
        "candidates": candidates,
        "scope": "vertex/triangle mirror matches, no OT, no nonmatching triangulation registration, no texture carving",
        "status": "CANDIDATES_FOR_REVIEW"
        if any(c["status"] == "PENDING_HUMAN" for c in candidates)
        else "NO_CANDIDATE_PASSED_PREVIEW_GATE",
    }


def apply_reuse(source: MeshState, pairs: list[dict[str, Any]]) -> MeshState:
    """Copy only approved correspondence coordinates, retaining explicit original vertex identity."""
    corners = source.uv[source.uv_faces].copy()
    reference_ids = source.uv_faces.copy()
    donors = [int(p["donor"]) for p in pairs]
    owners = [int(p["owner"]) for p in pairs]
    if len(set(donors)) != len(donors) or len(set(owners)) != len(owners) or set(donors) & set(owners):
        raise ValueError("Reuse mappings must be disjoint one-to-one pairs")
    for pair in pairs:
        donor, owner = int(pair["donor"]), int(pair["owner"])
        if min(donor, owner) < 0 or max(donor, owner) >= len(source.mesh.faces):
            raise ValueError("Face correspondence out of range")
        perm = np.asarray(pair["permutation"], dtype=int)
        if sorted(perm.tolist()) != [0, 1, 2] or donor == owner:
            raise ValueError("Invalid corner correspondence")
        corners[donor] = source.uv[source.uv_faces[owner, perm]]
        reference_ids[donor] = source.uv_faces[owner, perm]
    identities: dict[tuple[int, int], int] = {}
    ids = np.empty_like(reference_ids)
    for fi, face in enumerate(source.mesh.faces):
        for ci, vertex in enumerate(face):
            key = (int(vertex), int(reference_ids[fi, ci]))
            ids[fi, ci] = identities.setdefault(key, len(identities))
    result = state_from_corner_uv(source.mesh, corners, corner_ids=ids)
    if not np.allclose(result.uv[result.uv_faces], corners, atol=1e-12, rtol=0):
        raise ValueError("Reuse corner connectivity is inconsistent")
    return result


def validate_reuse(source: MeshState, output: MeshState, pairs: list[dict[str, Any]]) -> dict[str, Any]:
    if not np.array_equal(source.mesh.vertices, output.mesh.vertices) or not np.array_equal(
        source.mesh.faces, output.mesh.faces
    ):
        return {"valid": False, "errors": ["GEOMETRY_CHANGED"]}
    expected = apply_reuse(source, pairs)
    if (
        not np.array_equal(output.uv_faces, expected.uv_faces)
        or output.seams != expected.seams
        or not np.allclose(output.uv, expected.uv, atol=1e-10, rtol=0)
    ):
        return {"valid": False, "errors": ["UNAUTHORIZED_UV_CHANGE"]}
    polygons = [Polygon(output.uv[face]) for face in output.uv_faces]
    permitted = {frozenset((int(p["donor"]), int(p["owner"]))) for p in pairs}
    errors = []
    intersections = STRtree(polygons).query(polygons, predicate="intersects")
    for a, b in zip(*intersections, strict=True):
        if a >= b:
            continue
        area = float(polygons[a].intersection(polygons[b]).area)
        if area > 1e-12 and frozenset((int(a), int(b))) not in permitted:
            errors.append(f"UNAUTHORIZED_OVERLAP:{a}:{b}")
    unique = float(union_all(polygons).area)
    total = sum(float(p.area) for p in polygons)
    source_area = sum(float(Polygon(source.uv[face]).area) for face in source.uv_faces)
    return {
        "valid": not errors,
        "errors": errors,
        "unique_occupancy": unique,
        "summed_uv_area": total,
        "reuse_factor": total / unique,
        "freed_unique_area": source_area - unique,
        "source_unique_occupancy": source_area,
        "flipped_faces": int(np.sum(np.linalg.det(jacobians(output)) < 0)),
        "added_seams": len(output.seams - source.seams),
        "removed_seams": len(source.seams - output.seams),
        "note": "Negative Jacobians are whitelisted mirror counterparts only, not general flip permission.",
    }
