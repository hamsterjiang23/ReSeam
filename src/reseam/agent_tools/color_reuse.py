"""Source-bound Base Color reuse, with explicitly scoped CPU acceptance.

Targets store original source UV and a frozen shared parameterization. They never
read a candidate atlas. The reference filter is a finite quadrature of that
source signal over the candidate texel footprint, followed by the same bilinear
kernel as the runtime sampler. This is not PBR or GPU acceptance.
"""

import hashlib
import shutil
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import cast

import numpy as np
import shapely
import trimesh
from PIL import Image
from shapely import STRtree
from trimesh.visual.material import PBRMaterial
from trimesh.visual.texture import TextureVisuals

from ..budget_render import _rasterize
from ..budget_texture import (
    ReferenceSurface,
    Uint8Array,
    _stratified_barycentric,
    decode_rgba8,
    encode_rgba8,
    load_reference,
    mip_dimensions,
    sample_rgba8,
)
from ..mesh import chart_labels
from ..types import FloatArray, IntArray, MeshState
from .geometry import _DEFAULT_PROFILE, geometry_report
from .state import (
    Snapshot,
    check_deadline,
    file_hash,
    object_hash,
    read_object,
    require_snapshot,
    write_json,
)

SCOPE = "basecolor_reuse"
_DEFAULT_LIMITS = {"color_mean": .025, "color_p95": .08, "color_max": .20,
                   "render_mean": .025, "render_p95": .08, "render_max": .20,
                   "seam_mean": .025, "seam_max": .20}

_LAYOUT_KEYS = {"resolution", "gap_pixels", "border_pixels", "balance_density"}


def _geometry_contract(profile: dict[str, object]) -> dict[str, object]:
    """Bind correspondence and quality policy; layout is stage-specific."""
    return {key: profile[key] for key in (*_DEFAULT_PROFILE, "preserve_source_uv")
            if key not in _LAYOUT_KEYS and key in profile}


def _geometry_profile(request: dict[str, object], bound: dict[str, object] | None = None,
                      resolution: int | None = None) -> dict[str, object]:
    raw = request.get("geometry_profile", {})
    if not isinstance(raw, dict) or set(raw) - set(_DEFAULT_PROFILE) - {"balance_density", "preserve_source_uv"}:
        raise ValueError("INVALID_BASECOLOR_GEOMETRY_PROFILE")
    profile = {**_DEFAULT_PROFILE, **(bound or {}), **raw}
    for key, value in profile.items():
        if key == "preserve_source_uv":
            from .source_uv import source_uv_policy
            profile[key] = source_uv_policy(value)
        elif key in ("quality_constraints", "balance_density"):
            if not isinstance(value, bool):
                raise ValueError(f"BOOLEAN_REQUIRED:{key}")
        elif key == "resolution":
            if type(value) is not int or value < 1:
                raise ValueError("INVALID_GEOMETRY_RESOLUTION")
        elif type(value) not in (int, float) or not np.isfinite(value) or value < 0:
            raise ValueError(f"NONNEGATIVE_FINITE_GEOMETRY_VALUE_REQUIRED:{key}")
        else:
            profile[key] = float(value)
    if cast(float, profile["normal_angle_degrees"]) > 180:
        raise ValueError("INVALID_GEOMETRY_NORMAL_ANGLE")
    if bound is not None and _geometry_contract(profile) != _geometry_contract(bound):
        raise ValueError("BASECOLOR_GEOMETRY_PROFILE_MISMATCH:freeze a new target for another policy")
    if resolution is not None:
        if "resolution" in raw and raw["resolution"] != resolution:
            raise ValueError("BASECOLOR_GEOMETRY_RESOLUTION_MISMATCH")
        profile["resolution"] = resolution
    return profile


def _record_geometry_profile(record: dict[str, object]) -> dict[str, object]:
    profile = _geometry_profile(record)
    if ("geometry_profile" in record
            and record.get("geometry_profile_hash") != object_hash(profile)):
        raise ValueError("BASECOLOR_GEOMETRY_PROFILE_CHANGED")
    return profile


def _geometry_metadata(profile: dict[str, object]) -> dict[str, object]:
    strict = _geometry_contract(profile) == _geometry_contract(_DEFAULT_PROFILE)
    return {"geometry_profile": profile, "geometry_profile_hash": object_hash(profile),
            "geometry_policy": "strict_default" if strict else "explicit_custom",
            "strict_geometry_profile_applied": strict, "artist_reviewed": False}


def _prerequisite_profile(profile: dict[str, object], request: dict[str, object]) -> dict[str, object]:
    # Historically target/bake checked safety, leaving shape acceptance to
    # quality.validate. An explicitly supplied policy is checked as requested.
    explicit = "geometry_profile" in request
    return {**profile, "gap_pixels": 0., "border_pixels": 0.,
            "quality_constraints": profile["quality_constraints"] if explicit else False}


def _int(request: dict[str, object], key: str, default: int, low: int, high: int) -> int:
    value = request.get(key, default)
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f"INVALID_{key.upper()}:expected integer {low}..{high}")
    return value


def _geometry_hash(snapshot: Snapshot) -> str:
    mesh = snapshot.state.surface.mesh
    return hashlib.sha256(mesh.vertices.tobytes() + mesh.faces.tobytes()).hexdigest()


def _source(snapshot: Snapshot) -> tuple[ReferenceSurface, Path, dict[str, object]]:
    from .material import _source_document, _wraps

    value = snapshot.metadata.get("reference_glb")
    if not isinstance(value, str):
        raise TypeError("REFERENCE_GLB_REQUIRED")
    path = Path(value)
    document, _ = _source_document(path)
    meshes = document.get("meshes", [])
    if isinstance(meshes, list):
        for mesh in meshes:
            if not isinstance(mesh, dict):
                continue
            for primitive in mesh.get("primitives", []):
                if isinstance(primitive, dict) and "COLOR_0" in primitive.get("attributes", {}):
                    raise ValueError("UNSUPPORTED_BASECOLOR_VERTEX_COLOR_0")
    materials = document.get("materials", [])
    if not isinstance(materials, list) or len(materials) != 1 or not isinstance(materials[0], dict):
        raise ValueError("SINGLE_BASECOLOR_MATERIAL_REQUIRED")
    material = cast(dict[str, object], materials[0])
    pbr = material.get("pbrMetallicRoughness", {})
    if not isinstance(pbr, dict):
        raise TypeError("PBR_BASECOLOR_DEFINITION_REQUIRED")
    definition = pbr.get("baseColorTexture")
    if (not isinstance(definition, dict) or definition.get("texCoord", 0) != 0
            or definition.get("extensions") or material.get("alphaMode", "OPAQUE") != "OPAQUE"):
        raise ValueError("UNSUPPORTED_BASECOLOR_DEFINITION")
    reference = load_reference(path)
    reference.repeat = _wraps(document, (definition, None, None, None))[0]
    reference.base_factor = np.asarray(pbr.get("baseColorFactor", [1., 1., 1., 1.]), dtype=float)
    if (reference.base_factor.shape != (4,) or not np.isfinite(reference.base_factor).all()
            or np.any(reference.base_factor < 0) or np.any(reference.base_factor > 1)):
        raise ValueError("INVALID_BASECOLOR_FACTOR")
    triangles = snapshot.state.surface.mesh.vertices[snapshot.state.surface.mesh.faces]
    # Same-face ancestry is required here. A different high mesh needs an explicit
    # separate source projection map, not an unrecorded nearest-point fallback.
    if triangles.shape != reference.triangles.shape or not np.allclose(
        triangles, reference.triangles, rtol=0, atol=max(1e-12, reference.diagonal * 1e-10),
    ):
        raise ValueError("BASECOLOR_REUSE_REQUIRES_ORIGINAL_FACE_CORNER_SOURCE_IDENTITY")
    return reference, path, material


@dataclass
class _Domain:
    faces: IntArray
    triangles: FloatArray
    source_uv: FloatArray
    tree: STRtree


def _project_uv_barycentric(triangles: FloatArray, points: FloatArray) -> FloatArray:
    """Closest UV triangle barycentrics without a world-scale absolute tolerance.

    UV determinants shrink to fourth order in generic 3D projection predicates;
    a fixed tolerance can therefore classify a valid tiny interior as an edge.
    Use the 2D determinant for interior membership and exact segment clamping for
    the outside case. Geometry validation separately rejects degenerate faces.
    """
    first, second = triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0]
    delta = points - triangles[:, 0]
    determinant = first[:, 0] * second[:, 1] - first[:, 1] * second[:, 0]
    if np.any(determinant == 0):
        raise ValueError("DEGENERATE_UV_PROJECTION_TRIANGLE")
    b = (delta[:, 0] * second[:, 1] - delta[:, 1] * second[:, 0]) / determinant
    c = (first[:, 0] * delta[:, 1] - first[:, 1] * delta[:, 0]) / determinant
    result = np.column_stack((1 - b - c, b, c))
    outside = np.flatnonzero(np.any(result < 0, axis=1))
    if len(outside):
        candidates: list[FloatArray] = []
        barycentric: list[FloatArray] = []
        for start, end in ((0, 1), (1, 2), (2, 0)):
            edge = triangles[outside, end] - triangles[outside, start]
            offset = points[outside] - triangles[outside, start]
            denominator = np.sum(edge * edge, axis=1)
            t = np.clip(np.sum(offset * edge, axis=1) / denominator, 0, 1)
            candidates.append(triangles[outside, start] + t[:, None] * edge)
            weights = np.zeros((len(outside), 3)); weights[:, start] = 1 - t; weights[:, end] = t
            barycentric.append(weights)
        distances = np.sum((np.stack(candidates, axis=1) - points[outside, None]) ** 2, axis=2)
        nearest = np.argmin(distances, axis=1)
        result[outside] = np.stack(barycentric, axis=1)[np.arange(len(outside)), nearest]
    return result


class ColorTarget:
    """Frozen surface signal, addressable by original face and barycentric point."""

    def __init__(self, snapshot: Snapshot, path: Path) -> None:
        self.path = path.resolve()
        self.record = read_object(self.path)
        record = dict(self.record)
        digest = record.pop("target_hash", None)
        if digest != object_hash(record) or record.get("scope") != SCOPE:
            raise ValueError("TARGET_MANIFEST_HASH_MISMATCH")
        _record_geometry_profile(self.record)
        if record.get("asset_id") != snapshot.asset_id or record.get("geometry_hash") != _geometry_hash(snapshot):
            raise ValueError("TARGET_ASSET_OR_GEOMETRY_MISMATCH")
        self.reference, source, _ = _source(snapshot)
        if record.get("source_sha256") != file_hash(source):
            raise ValueError("TARGET_SOURCE_CHANGED")
        data_path = self.path.parent / "target.npz"
        if file_hash(data_path) != record.get("arrays_sha256"):
            raise ValueError("TARGET_ARRAY_HASH_MISMATCH")
        with np.load(data_path, allow_pickle=False) as data:
            self.source_uv = np.asarray(data["source_uv"], dtype=float)
            self.domain_uv = np.asarray(data["domain_uv"], dtype=float)
            self.edge_pairs = np.asarray(data["original_edge_pairs"], dtype=np.int64) if "original_edge_pairs" in data else np.empty((0, 8), dtype=np.int64)
        topology = record.get("original_topology")
        if isinstance(topology, dict) and file_hash(self.path.parent / "original_topology.npz") != topology.get("sha256"):
            raise ValueError("TARGET_ORIGINAL_TOPOLOGY_HASH_MISMATCH")
        self.slaves: dict[int, _Domain] = {}
        groups = cast(list[dict[str, object]], record.get("groups", []))
        for group in groups:
            ids = np.asarray(group["master_faces"], dtype=np.int64)
            uv = self.domain_uv[ids]
            triangles = np.concatenate((uv, np.zeros((*uv.shape[:2], 1))), axis=2)
            domain = _Domain(ids, triangles, self.source_uv[ids], STRtree([shapely.Polygon(t) for t in uv]))
            for face in cast(list[int], group["slave_faces"]):
                self.slaves[face] = domain

    def original(self, face: int, bary: FloatArray) -> FloatArray:
        return self._source_color(bary @ self.source_uv[face])

    def _source_color(self, uv: FloatArray) -> FloatArray:
        color = sample_rgba8(self.reference.image, uv,
                             repeat=self.reference.repeat) * self.reference.base_factor
        # glTF OPAQUE ignores texture/factor alpha. Atlas alpha separately records
        # sampled support and must not mistake authored alpha for missing coverage.
        color[:, 3] = 1.
        return color

    def sample(self, face: int, bary: FloatArray) -> FloatArray:
        domain = self.slaves.get(face)
        if domain is None:
            return self.original(face, bary)
        uv = bary @ self.domain_uv[face]
        points = shapely.points(uv)
        pairs = domain.tree.query(points, predicate="intersects")
        ids = np.full(len(uv), -1, dtype=np.int64)
        if pairs.size:
            # Stable face priority on a shared edge, independent of tree ordering.
            order = np.lexsort((domain.faces[pairs[1]], pairs[0]))
            ordered = pairs[:, order]
            unique = np.r_[True, np.diff(ordered[0]) != 0]
            ids[ordered[0, unique]] = ordered[1, unique]
        missing = ids < 0
        if missing.any():
            # Texture footprint extension uses the nearest point on the same
            # master domain. Center samples were authorized by geometry gates.
            ids[missing] = np.asarray(domain.tree.nearest(points[missing]), dtype=np.int64)
        coordinates = _project_uv_barycentric(domain.triangles[ids, :, :2], uv)
        source_uv = np.einsum("nc,ncd->nd", coordinates, domain.source_uv[ids])
        return self._source_color(source_uv)


class AtlasSignal:
    """Evaluate a source-only target through current chart geometry.

Filtering footprints cross ordinary triangle edges but never jump across chart
seams. Outside a chart, nearest-boundary extension matches the padding contract.
No image produced by the candidate bake is read here.
"""

    def __init__(self, target: ColorTarget, surface: MeshState, *, cache_filter: bool = False) -> None:
        self.target = target
        self.uv = surface.uv[surface.uv_faces]
        self.labels = chart_labels(surface)
        self.domains: dict[int, _Domain] = {}
        self.filter_cache: dict[tuple[int, int, int, int], FloatArray] | None = {} if cache_filter else None

    def sample(self, face: int, bary: FloatArray) -> FloatArray:
        inside = np.all(bary >= -1e-10, axis=1)
        result = np.empty((len(bary), 4))
        if inside.any():
            result[inside] = self.target.sample(face, bary[inside])
        outside = ~inside
        if not outside.any():
            return result
        label = int(self.labels[face])
        if label not in self.domains:
            faces = np.flatnonzero(self.labels == label)
            triangles = np.concatenate((self.uv[faces], np.zeros((len(faces), 3, 1))), axis=2)
            self.domains[label] = _Domain(faces, triangles, self.target.source_uv[faces],
                STRtree([shapely.Polygon(t) for t in self.uv[faces]]))
        domain = self.domains[label]
        uv = bary[outside] @ self.uv[face]
        points = shapely.points(uv)
        selected = np.asarray(domain.tree.nearest(points), dtype=np.int64)
        coordinates = _project_uv_barycentric(domain.triangles[selected, :, :2], uv)
        values = np.empty((len(uv), 4))
        for index in np.unique(selected):
            keep = selected == index
            values[keep] = self.target.sample(int(domain.faces[index]), coordinates[keep])
        result[outside] = values
        return result


def _freeze_topology(request: dict[str, object], reference: ReferenceSurface,
                     output: Path) -> tuple[IntArray, dict[str, object] | None]:
    value = request.get("original_topology")
    if value is None:
        return np.empty((0, 8), dtype=np.int64), None
    if not isinstance(value, str):
        raise TypeError("ORIGINAL_TOPOLOGY_NPZ_PATH_REQUIRED")
    source = Path(value)
    digest = file_hash(source)
    with np.load(source, allow_pickle=False) as data:
        vertices = np.asarray(data["vertices"], dtype=float)
        faces = np.asarray(data["faces"])
        uv = np.asarray(data["corner_uv"], dtype=float)
        corners = np.asarray(data["corner_ids"])
    if (vertices.ndim != 2 or vertices.shape[1] != 3 or not np.isfinite(vertices).all()
            or faces.shape != reference.triangles.shape[:2] or faces.dtype.kind not in "iu"
            or np.any(faces < 0) or np.any(faces >= len(vertices))
            or corners.shape != faces.shape or corners.dtype.kind not in "iu"
            or np.any(corners < 0)
            or uv.shape != reference.corner_uv.shape or not np.isfinite(uv).all()):
        raise ValueError("INVALID_ORIGINAL_TOPOLOGY_ARRAYS")
    order = np.argsort(corners.ravel(), kind="stable")
    repeated = np.diff(corners.ravel()[order]) == 0
    if (np.any(np.diff(faces.ravel()[order])[repeated] != 0)
            or np.any(np.abs(np.diff(uv.reshape(-1, 2)[order], axis=0)[repeated]) > 1e-12)):
        raise ValueError("INCONSISTENT_ORIGINAL_TOPOLOGY_CORNER_ID")
    position_error = float(np.abs(vertices[faces] - reference.triangles).max())
    uv_error = float(np.abs(uv - reference.corner_uv).max())
    if position_error > max(1e-12, reference.diagonal * 1e-6) or uv_error > 1e-6:
        raise ValueError("ORIGINAL_TOPOLOGY_FACE_CORNER_WITNESS_MISMATCH")
    edges: dict[tuple[int, int], list[tuple[int, int, int]]] = {}
    for face, triangle in enumerate(faces):
        for first, second in ((0, 1), (1, 2), (2, 0)):
            a, b = int(triangle[first]), int(triangle[second])
            if a == b:
                raise ValueError("DEGENERATE_ORIGINAL_TOPOLOGY_EDGE")
            key = (min(a, b), max(a, b))
            edges.setdefault(key, []).append((face, first, second) if a < b else (face, second, first))
    pairs = [(*edge, *members[i], *members[j]) for edge, members in sorted(edges.items())
             for i in range(len(members)) for j in range(i + 1, len(members))]
    shutil.copy2(source, output / "original_topology.npz")
    if file_hash(output / "original_topology.npz") != digest or file_hash(source) != digest:
        raise ValueError("ORIGINAL_TOPOLOGY_CHANGED_DURING_FREEZE")
    return np.asarray(pairs, dtype=np.int64).reshape(-1, 8), {
        "source": str(source.resolve()), "sha256": digest, "pair_count": len(pairs),
        "corner_ids_sha256": hashlib.sha256(corners.tobytes()).hexdigest(),
        "true_boundary_edges": sum(len(v) == 1 for v in edges.values()),
        "nonmanifold_edges": sum(len(v) > 2 for v in edges.values()),
        "face_corner_position_max_error": position_error, "face_corner_uv_max_error": uv_error,
        "identity_contract": "authoritative face/corner incidence; no coordinate welding",
    }


def freeze_target(request: dict[str, object], output: Path, deadline: float) -> dict[str, object]:
    snapshot = require_snapshot(request)
    profile = _geometry_profile(request)
    reference, source, material = _source(snapshot)
    policy = request.get("policy", "symmetrize" if snapshot.state.groups else "source_original")
    if policy not in ("symmetrize", "source_original"):
        raise ValueError("INVALID_TARGET_POLICY")
    protected = request.get("protected_faces", [])
    nfaces = len(snapshot.state.surface.mesh.faces)
    if (not isinstance(protected, list) or any(type(f) is not int or not 0 <= f < nfaces for f in protected)
            or len(set(protected)) != len(protected)):
        raise ValueError("INVALID_PROTECTED_FACES")
    protected_set = set(protected)
    regions = snapshot.metadata.get("regions", [])
    if not isinstance(regions, list):
        raise TypeError("INVALID_REGION_METADATA")
    for region in regions:
        if not isinstance(region, dict):
            raise TypeError("INVALID_REGION_METADATA")
        protected_set.update(cast(list[int], region.get("protected_faces", [])))
        if region.get("role") == "protected":
            protected_set.update(cast(list[int], region.get("faces", [])))
    if any(type(face) is not int or not 0 <= face < nfaces for face in protected_set):
        raise ValueError("INVALID_REGION_PROTECTED_FACES")
    protected = sorted(protected_set)
    masters = request.get("master_members", {})
    if not isinstance(masters, dict) or any(v not in ("first", "second") for v in masters.values()):
        raise ValueError("INVALID_MASTER_MEMBERS")
    if set(masters) - {g.group_id for g in snapshot.state.groups}:
        raise ValueError("UNKNOWN_MASTER_GROUP")
    groups: list[dict[str, object]] = []
    if policy == "symmetrize":
        safety_profile = _prerequisite_profile(profile, request)
        safety = geometry_report(snapshot.state, safety_profile)
        write_json(output / "geometry.json", {"profile": safety_profile, "geometry": safety})
        if safety.get("valid") is not True:
            raise ValueError("SYMMETRIC_TARGET_REQUIRES_LEGAL_SHARED_STATE")
        for group in snapshot.state.groups:
            if set(protected).intersection(group.first_faces + group.second_faces):
                raise ValueError("PROTECTED_FACE_IN_SHARED_TARGET:release its sharing first")
            side = masters.get(group.group_id, "first")
            first, second = (group.first_faces, group.second_faces) if side == "first" else (
                group.second_faces, group.first_faces)
            groups.append({"group_id": group.group_id, "master_member": side,
                           "master_faces": list(first), "slave_faces": list(second)})
    check_deadline(deadline)
    edge_pairs, original_topology = _freeze_topology(request, reference, output)
    np.savez_compressed(output / "target.npz", source_uv=reference.corner_uv,
                        domain_uv=snapshot.state.surface.uv[snapshot.state.surface.uv_faces],
                        original_edge_pairs=edge_pairs)
    record: dict[str, object] = {
        "schema_version": 1, "scope": SCOPE, "asset_id": snapshot.asset_id,
        "geometry_hash": _geometry_hash(snapshot), "source": str(source.resolve()),
        "source_sha256": file_hash(source), "frozen_from_state_hash": snapshot.state_hash,
        "arrays_sha256": file_hash(output / "target.npz"), "policy": policy,
        "groups": groups, "protected_faces": protected,
        "source_material_extensions_not_validated": material.get("extensions", {}),
        "source_double_sided": bool(material.get("doubleSided", False)),
        "original_topology": original_topology,
        "signal": "original source texture sampled through frozen source face/corner and shared UV domain",
        "master_default": "first member; caller must record any semantic source-side decision",
        **_geometry_metadata(profile),
    }
    record["target_hash"] = object_hash(record)
    write_json(output / "target.json", record)
    target = ColorTarget(snapshot, output / "target.json")
    bary = _stratified_barycentric(16)
    errors = np.zeros(nfaces)
    maximum = np.zeros(nfaces)
    for face in sorted(target.slaves):
        check_deadline(deadline)
        error = np.linalg.norm(target.sample(face, bary)[:, :3] - target.original(face, bary)[:, :3], axis=1)
        errors[face], maximum[face] = float(error.mean()), float(error.max())
    areas = snapshot.state.surface.mesh.areas
    change = {"area_weighted_mean": float(errors @ areas / areas.sum()),
              "maximum": float(maximum.max()), "modified_faces": sorted(target.slaves),
              "face_mean": errors.tolist(), "face_max": maximum.tolist(), "samples_per_face": 16,
              "protected_faces_changed": 0, "role": "intentional appearance edit, not reconstruction gain"}
    write_json(output / "appearance_change.json", change)
    return {"status": "OK", "execution_success": True, "target": str((output / "target.json").resolve()),
            "target_hash": record["target_hash"], "policy": policy,
            "appearance_change": str((output / "appearance_change.json").resolve()),
            "modified_faces": len(target.slaves), "full_pbr_passed": False,
            **_geometry_metadata(profile)}


def _target(snapshot: Snapshot, request: dict[str, object], output: Path, deadline: float) -> ColorTarget:
    path = request.get("target")
    if path is None:
        folder = output / "source_target"
        folder.mkdir()
        target_request: dict[str, object] = {"snapshot": str(snapshot.root), "policy": "source_original"}
        if "geometry_profile" in request:
            target_request["geometry_profile"] = request["geometry_profile"]
        frozen = freeze_target(target_request, folder, deadline)
        path = frozen["target"]
    if not isinstance(path, str):
        raise TypeError("TARGET_PATH_REQUIRED")
    return ColorTarget(snapshot, Path(path))


def _bary(triangle: FloatArray, points: FloatArray) -> FloatArray:
    matrix = np.column_stack((triangle[1] - triangle[0], triangle[2] - triangle[0]))
    tail = (points - triangle[0]) @ np.linalg.inv(matrix).T
    return np.column_stack((1 - tail.sum(axis=1), tail))


def _pixels(triangle: FloatArray, resolution: int, support_pixels: float = 0.) -> Iterator[tuple[IntArray, IntArray, FloatArray]]:
    low = np.maximum(0, np.floor(triangle.min(axis=0) * resolution - .5 - support_pixels).astype(np.int64))
    high = np.minimum(resolution - 1, np.ceil(triangle.max(axis=0) * resolution - .5 + support_pixels).astype(np.int64))
    width = int(high[0] - low[0] + 1)
    if width <= 0:
        return
    step = max(1, 32768 // width)
    for row in range(int(low[1]), int(high[1]) + 1, step):
        xx, vv = np.meshgrid(np.arange(low[0], high[0] + 1), np.arange(row, min(row + step, high[1] + 1)))
        x, y = xx.ravel(), resolution - 1 - vv.ravel()
        points = np.column_stack(((x + .5) / resolution, 1 - (y + .5) / resolution))
        bary = _bary(triangle, points)
        keep = (np.asarray(shapely.distance(shapely.points(points), shapely.Polygon(triangle))) <= support_pixels / resolution
                if support_pixels else np.all(bary >= -1e-10, axis=1))
        yield x[keep], y[keep], bary[keep]


def _box(target: ColorTarget | AtlasSignal, face: int, triangle: FloatArray, centers: FloatArray,
         resolution: int, mip: int) -> FloatArray:
    # 2x2 at mip0, 4x4 at mip1, 8x8 at mip2: exact positions of the
    # original 2x2 source quadrature under recursive 2x2 linear BOX filtering.
    side = 2 * (2 ** mip)
    offsets = (np.arange(side) + .5) / side - .5
    # Small faces repeatedly query identical neighboring mip texel centers.
    # Deduplicate the source quadrature, preserving its exact weights.
    if len(centers) < 1024:
        centers, inverse = np.unique(centers, axis=0, return_inverse=True)
    else:
        inverse = None
    cache = target.filter_cache if isinstance(target, AtlasSignal) else None
    means = np.empty((len(centers), 4))
    keys: list[tuple[int, int, int, int]] = []
    if cache is not None and isinstance(target, AtlasSignal):
        pixel = np.rint(centers * (resolution // 2 ** mip) - .5).astype(np.int64)
        keys = [(int(target.labels[face]), mip, int(x), int(y)) for x, y in pixel]
        missing = np.asarray([i for i, key in enumerate(keys) if key not in cache], dtype=np.int64)
        for index, key in enumerate(keys):
            if key in cache:
                means[index] = cache[key]
    else:
        missing = np.arange(len(centers), dtype=np.int64)
    xx, yy = np.meshgrid(offsets, offsets)
    delta = np.column_stack((xx.ravel(), yy.ravel())) * (2 ** mip / resolution)
    if len(missing):
        points = (centers[missing, None, :] + delta[None, :, :]).reshape(-1, 2)
        values = target.sample(face, _bary(triangle, points))
        means[missing] = values.reshape(len(missing), side * side, 4).mean(axis=1)
        if cache is not None:
            for index in missing:
                if len(cache) >= 250000:
                    break
                cache[keys[index]] = means[index].copy()
    return means if inverse is None else means[inverse]


def _filtered(target: ColorTarget | AtlasSignal, face: int, triangle: FloatArray, uv: FloatArray,
              resolution: int, mip: int) -> FloatArray:
    size = resolution // (2 ** mip)
    pixels = uv * size - .5
    lower = np.floor(pixels).astype(np.int64)
    fraction = pixels - lower
    result = np.zeros((len(uv), 4))
    for dx, dy in ((0, 0), (1, 0), (0, 1), (1, 1)):
        centers = (np.clip(lower + [dx, dy], 0, size - 1) + .5) / size
        weight = ((fraction[:, 0] if dx else 1 - fraction[:, 0])
                  * (fraction[:, 1] if dy else 1 - fraction[:, 1]))
        result += weight[:, None] * _box(target, face, triangle, centers, resolution, mip)
    return result


def _mips(base: Uint8Array, folder: Path, deadline: float) -> list[Path]:
    paths: list[Path] = []
    previous = base
    for level, (width, height) in enumerate(mip_dimensions(base.shape[1], base.shape[0])):
        check_deadline(deadline)
        if level:
            current = np.empty((height, width, 4), dtype=np.uint8)
            for row in range(0, height, 64):
                check_deadline(deadline)
                stop = min(row + 64, height)
                block = decode_rgba8(previous[2 * row:2 * stop, :2 * width])
                mean = block.reshape(stop - row, 2, width, 2, 4).mean(axis=(1, 3))
                current[row:stop] = encode_rgba8(mean)
            previous = current
        path = folder / f"BaseColor_mip{level}.png"
        Image.fromarray(previous).save(path)
        paths.append(path)
    return paths


def bake_color_reuse(request: dict[str, object], output: Path, deadline: float) -> dict[str, object]:
    snapshot = require_snapshot(request)
    resolution = _int(request, "resolution", 512, 8, 8192)
    if resolution & (resolution - 1):
        raise ValueError("BASECOLOR_REUSE_REQUIRES_POWER_OF_TWO_RESOLUTION")
    padding = _int(request, "padding_pixels", 4, 0, resolution // 2 - 1)
    target = _target(snapshot, request, output, deadline)
    profile = _geometry_profile(request, _record_geometry_profile(target.record), resolution)
    safety_profile = _prerequisite_profile(profile, request)
    safety = geometry_report(snapshot.state, safety_profile)
    write_json(output / "geometry.json", {"profile": safety_profile, "geometry": safety})
    if safety.get("valid") is not True:
        return {"status": "REJECTED", "execution_success": True, "reason": "GEOMETRY_UNSAFE", "geometry": safety}
    base = np.zeros((resolution, resolution, 4), dtype=np.uint8)
    owner = np.full((resolution, resolution), -1, dtype=np.int32)
    surface = snapshot.state.surface
    signal = AtlasSignal(target, surface)
    triangles = surface.uv[surface.uv_faces]
    subtexel_faces: list[int] = []
    subtexel_conflicts: list[int] = []
    for face, triangle in enumerate(triangles):
        check_deadline(deadline)
        if face % 256 == 0:
            write_json(output / "progress.json", {"stage": "bake", "completed_faces": face,
                                                  "total_faces": len(triangles)})
        has_center = False
        for x, y, bary in _pixels(triangle, resolution):
            has_center = has_center or bool(len(x))
            keep = owner[y, x] < 0
            x, y, bary = x[keep], y[keep], bary[keep]
            if not len(x):
                continue
            colors = _box(signal, face, triangle, bary @ triangle, resolution, 0)
            base[y, x] = encode_rgba8(colors)
            owner[y, x] = face
        if not has_center:
            center = triangle.mean(axis=0)
            x = int(np.clip(np.floor(center[0] * resolution), 0, resolution - 1))
            y = int(np.clip(np.floor((1 - center[1]) * resolution), 0, resolution - 1))
            subtexel_faces.append(face)
            if owner[y, x] >= 0:
                subtexel_conflicts.append(face)
    geometric_coverage = owner >= 0
    np.save(output / "geometric_coverage.npy", geometric_coverage, allow_pickle=False)
    geometric_pixels = int(geometric_coverage.sum())
    del geometric_coverage
    support_radius = max(float(padding), .75)
    for face, triangle in enumerate(triangles):
        check_deadline(deadline)
        if face % 256 == 0:
            write_json(output / "progress.json", {"stage": "source_sampled_support", "completed_faces": face,
                                                  "total_faces": len(triangles)})
        for x, y, bary in _pixels(triangle, resolution, support_radius):
            keep = owner[y, x] < 0
            x, y, bary = x[keep], y[keep], bary[keep]
            if not len(x):
                continue
            base[y, x] = encode_rgba8(_box(signal, face, triangle, bary @ triangle, resolution, 0))
            owner[y, x] = face
    coverage = owner >= 0
    del owner
    folder = output / "texture"
    folder.mkdir()
    paths = _mips(base, folder, deadline)
    np.save(output / "coverage.npy", coverage, allow_pickle=False)
    manifest: dict[str, object] = {
        "schema_version": 1, "scope": SCOPE, "state_hash": snapshot.state_hash,
        "asset_id": snapshot.asset_id, "geometry_hash": _geometry_hash(snapshot),
        "snapshot": str((snapshot.root / "snapshot.json").resolve()),
        "target": str(target.path), "target_sha256": file_hash(target.path),
        "target_hash": target.record["target_hash"], "source_sha256": target.record["source_sha256"],
        "resolution": resolution, "padding_pixels": padding,
        "payload_bytes": sum(w * h * 4 for w, h in mip_dimensions(resolution, resolution)),
        "package_bytes": sum(p.stat().st_size for p in paths), "covered_pixels": int(coverage.sum()),
        "geometric_center_pixels": geometric_pixels, "support_radius_pixels": support_radius,
        "codec": "RGBA8_SRGB_RGB_LINEAR_ALPHA", "source_quadrature": "2x2 uniform per base texel",
        "footprint_boundary": "traverse current chart faces; nearest same-chart boundary extension",
        "mip_filter": "recursive linear 2x2 BOX with per-level RGBA8 quantization",
        "overlap_write_policy": "stable original face priority; shared-member agreement independently validated",
        "subtexel_faces": subtexel_faces, "subtexel_occupied_seed_faces": subtexel_conflicts,
        "coverage_definition": "source-sampled triangle buffer support; geometric_coverage.npy separately stores center rasterization; neither is continuous geometric occupancy",
        "files": {str(p.relative_to(output)): file_hash(p) for p in [*paths, output / "coverage.npy", output / "geometric_coverage.npy"]},
        "full_pbr_passed": False, "gpu_verified": False, "artist_reviewed": False,
        **_geometry_metadata(profile),
    }
    check_deadline(deadline)
    ColorTarget(require_snapshot(request), target.path)
    write_json(output / "bake.json", manifest)
    return {"status": "OK", "execution_success": True, "scope": SCOPE,
            "bake": str((output / "bake.json").resolve()), "target": str(target.path),
            "payload_bytes": manifest["payload_bytes"], "quality_accepted": False, "full_pbr_passed": False,
            **_geometry_metadata(profile)}


def _load(snapshot: Snapshot, path: Path) -> tuple[dict[str, object], ColorTarget, list[Uint8Array]]:
    record = read_object(path)
    if (record.get("scope") != SCOPE or record.get("state_hash") != snapshot.state_hash
            or record.get("asset_id") != snapshot.asset_id or record.get("geometry_hash") != _geometry_hash(snapshot)):
        raise ValueError("STALE_BASECOLOR_BAKE")
    target_path = Path(str(record["target"]))
    if file_hash(target_path) != record.get("target_sha256"):
        raise ValueError("BAKE_TARGET_CHANGED")
    target = ColorTarget(snapshot, target_path)
    _geometry_profile({"geometry_profile": _record_geometry_profile(record)},
                      _record_geometry_profile(target.record))
    files = record.get("files")
    if not isinstance(files, dict) or not files:
        raise ValueError("BAKE_FILES_REQUIRED")
    for name, digest in files.items():
        item = (path.parent / str(name)).resolve()
        if not item.is_relative_to(path.parent.resolve()) or file_hash(item) != digest:
            raise ValueError("BAKE_FILE_CHANGED")
    size = cast(int, record["resolution"])
    levels = [np.asarray(Image.open(path.parent / "texture" / f"BaseColor_mip{i}.png").convert("RGBA"))
              for i in range(len(mip_dimensions(size, size)))]
    return record, target, levels


def _metrics(errors: FloatArray, mean: float, p95: float, maximum: float) -> dict[str, object]:
    if not len(errors):
        return {"valid": False, "mean": None, "p95": None, "max": None, "samples": 0}
    values = float(errors.mean()), float(np.quantile(errors, .95)), float(errors.max())
    return {"valid": bool(values[0] <= mean and values[1] <= p95 and values[2] <= maximum),
            "mean": values[0], "p95": values[1], "max": values[2], "samples": len(errors)}


def validate_color_reuse(request: dict[str, object], output: Path, deadline: float) -> dict[str, object]:
    snapshot = require_snapshot(request)
    path = Path(str(request.get("bake", "")))
    record, target, levels = _load(snapshot, path)
    size = cast(int, record["resolution"])
    mips = request.get("mip_levels", [0, 1, 2])
    if (not isinstance(mips, list) or not mips or len(set(mips)) != len(mips)
            or any(type(m) is not int or not 0 <= m <= 2 or m >= len(levels) for m in mips)):
        raise ValueError("BASECOLOR_REUSE_VALIDATED_MIPS_MUST_BE_SUBSET_0_1_2")
    limits = dict(_DEFAULT_LIMITS)
    raw_limits = request.get("thresholds", {})
    if not isinstance(raw_limits, dict) or set(raw_limits) - set(limits):
        raise ValueError("INVALID_BASECOLOR_THRESHOLDS")
    for key, value in raw_limits.items():
        if type(value) not in (float, int) or not np.isfinite(value) or value <= 0:
            raise ValueError("POSITIVE_FINITE_COLOR_THRESHOLD_REQUIRED")
        limits[str(key)] = float(value)
    profile = _geometry_profile(request, _record_geometry_profile(record), size)
    geometry = geometry_report(snapshot.state, profile)
    surface = snapshot.state.surface
    signal = AtlasSignal(target, surface, cache_filter=True)
    triangles = surface.uv[surface.uv_faces]
    count = _int(request, "samples_per_face", 16, 4, 64)
    bary = _stratified_barycentric(count)
    records: list[dict[str, object]] = []
    failures: set[int] = set()
    color_failures: set[int] = set()
    coverage_failures: set[int] = set()
    errors_all: list[FloatArray] = []
    area_mean = 0.
    for face, triangle in enumerate(triangles):
        check_deadline(deadline)
        if face % 256 == 0:
            write_json(output / "progress.json", {"stage": "face_quality", "completed_faces": face,
                                                  "total_faces": len(triangles), "failed_faces": len(failures)})
        uv = bary @ triangle
        for level in mips:
            expected = _filtered(signal, face, triangle, uv, size, level)
            actual = sample_rgba8(levels[level], uv)
            error = np.linalg.norm(expected[:, :3] - actual[:, :3], axis=1)
            metrics = _metrics(error, limits["color_mean"], limits["color_p95"], limits["color_max"])
            color_valid = metrics["valid"] is True
            coverage_valid = bool(np.all(actual[:, 3] >= .99))
            metrics.update({"color_valid": color_valid, "coverage_valid": coverage_valid,
                            "alpha_min": float(actual[:, 3].min()), "valid": color_valid and coverage_valid})
            records.append({"face": face, "mip": level, **metrics})
            if not color_valid:
                color_failures.add(face)
            if not coverage_valid:
                coverage_failures.add(face)
            if not metrics["valid"]:
                failures.add(face)
            errors_all.append(error)
            area_mean += float(error.mean() * surface.mesh.areas[face]) / len(mips)
    material = {**_metrics(np.concatenate(errors_all), limits["color_mean"], limits["color_p95"], limits["color_max"]),
                "valid": not failures, "failed_faces": sorted(failures), "faces": records,
                "color_failed_faces": sorted(color_failures), "coverage_failed_faces": sorted(coverage_failures),
                "area_weighted_mean": area_mean / float(surface.mesh.areas.sum())}
    render_size = _int(request, "render_size", 96, 16, 256)
    write_json(output / "progress.json", {"stage": "render_quality", "completed_faces": len(triangles)})
    rendering = _render(target, surface, levels, mips, render_size, limits, output / "render", deadline)
    seams = _seams(signal, surface, levels, mips, limits, deadline)
    write_json(output / "progress.json", {"stage": "verification", "completed_faces": len(triangles)})
    check_deadline(deadline)
    _load(require_snapshot(request), path)
    valid = all(item.get("valid") is True for item in (geometry, material, rendering, seams))
    protocol: dict[str, object] = {
        "thresholds": limits, "geometry": {k: v for k, v in profile.items() if k != "resolution"},
        "mip_levels": mips, "samples_per_face": count, "render_size": render_size,
        "reference_filter": "source-only chart-aware piecewise-affine footprint: (2*2^LOD)^2 BOX quadrature then bilinear",
        "reference_boundary": "traverse current chart faces; nearest same-chart boundary extension",
        "render_reference": "fixed screen 2x2 source-signal supersampling, independent of atlas resolution and LOD",
        "scope": "finite surface/color/seam/unlit-CPU probes; not full PBR, GPU, or continuous bounds",
    }
    evidence: dict[str, object] = {
        "schema_version": 1, "scope": SCOPE, "valid": valid, "quality_accepted": valid,
        "state_hash": snapshot.state_hash, "asset_id": snapshot.asset_id,
        "bake_sha256": file_hash(path), "target_hash": record["target_hash"], "source_sha256": record["source_sha256"],
        "geometry": geometry, "material": material, "render": rendering, "seams": seams,
        "profile": protocol, "profile_hash": object_hash(protocol), "payload_bytes": record["payload_bytes"],
        "full_pbr_passed": False, "gpu_verified": False, "artist_reviewed": False,
        **_geometry_metadata(profile),
    }
    write_json(output / "validation.json", evidence)
    return {"status": "OK" if valid else "REJECTED", "execution_success": True, "scope": SCOPE,
            "quality_accepted": valid, "full_pbr_passed": False, "geometry_valid": geometry.get("valid"),
            "material_valid": material["valid"], "render_valid": rendering["valid"], "seams_valid": seams["valid"],
            "validation": str((output / "validation.json").resolve()), "payload_bytes": record["payload_bytes"],
            **_geometry_metadata(profile)}


def _render(target: ColorTarget, surface: MeshState, levels: list[Uint8Array], mips: list[int], size: int,
            limits: dict[str, float], output: Path, deadline: float) -> dict[str, object]:
    output.mkdir()
    records: list[dict[str, object]] = []
    for azimuth in (17., 107., 197., 287.):
        screen = size * 2
        face_image, bary_image, _, _ = _rasterize(surface.mesh, screen, azimuth, 23., deadline)
        mask = face_image >= 0
        visible = np.unique(face_image[mask])
        target_high = np.zeros((screen, screen, 4))
        for face in visible:
            check_deadline(deadline)
            pixels = face_image == face
            target_high[pixels] = target.sample(int(face), bary_image[pixels])
        target_image = target_high.reshape(size, 2, size, 2, 4).mean(axis=(1, 3))
        screen_mask = mask.reshape(size, 2, size, 2).any(axis=(1, 3))
        for mip in mips:
            actual_high = np.zeros((screen, screen, 4))
            for face in visible:
                check_deadline(deadline)
                pixels = face_image == face
                triangle = surface.uv[surface.uv_faces[face]]
                uv = bary_image[pixels] @ triangle
                actual_high[pixels] = sample_rgba8(levels[mip], uv)
            actual_image = actual_high.reshape(size, 2, size, 2, 4).mean(axis=(1, 3))
            error = np.linalg.norm(actual_image[screen_mask, :3] - target_image[screen_mask, :3], axis=1)
            metrics = _metrics(error, limits["render_mean"], limits["render_p95"], limits["render_max"])
            label = f"azimuth{int(azimuth)}_mip{mip}"
            Image.fromarray(encode_rgba8(actual_image)).save(output / f"{label}_actual.png")
            Image.fromarray(encode_rgba8(target_image)).save(output / f"{label}_target.png")
            heat = np.zeros((size, size, 4)); heat[screen_mask, 0] = np.minimum(1, error / limits["render_max"])
            heat[screen_mask, 3] = 1
            Image.fromarray(encode_rgba8(heat)).save(output / f"{label}_error.png")
            records.append({"view": label, **metrics})
    return {"valid": all(r["valid"] for r in records), "views": records,
            "contract": "fixed original-mesh z-buffer; 2x2 screen supersampling; unlit BaseColor only",
            "reference_independent_of_atlas_resolution_and_mip": True}


def _seams(target: AtlasSignal, surface: MeshState, levels: list[Uint8Array], mips: list[int],
           limits: dict[str, float], deadline: float) -> dict[str, object]:
    errors: list[float] = []
    side_errors: list[float] = []
    records: list[dict[str, object]] = []
    uv = surface.uv[surface.uv_faces]
    pairs: dict[tuple[int, int, int, int, int, int], dict[str, object]] = {}
    continuous_pairs = 0
    for edge_a, edge_b, first, a0, a1, second, b0, b1 in target.target.edge_pairs:
        first, second = int(first), int(second)
        first_corners, second_corners = [int(a0), int(a1)], [int(b0), int(b1)]
        if (np.array_equal(uv[first, first_corners], uv[second, second_corners])
                and np.array_equal(target.target.source_uv[first, first_corners], target.target.source_uv[second, second_corners])
                and first not in target.target.slaves and second not in target.target.slaves):
            continuous_pairs += 1
            continue
        key = (first, first_corners[0], first_corners[1], second, second_corners[0], second_corners[1])
        pairs[key] = {"original_edge": [int(edge_a), int(edge_b)], "origin": "authoritative_original_topology"}
    for edge_id in sorted(surface.seams):
        check_deadline(deadline)
        entries = surface.mesh.edge_faces[edge_id]
        edge = surface.mesh.edges[edge_id]
        for first_index in range(len(entries)):
            for second_index in range(first_index + 1, len(entries)):
                first, second = entries[first_index][0], entries[second_index][0]
                a0, a1 = [int(np.flatnonzero(surface.mesh.faces[first] == v)[0]) for v in edge]
                b0, b1 = [int(np.flatnonzero(surface.mesh.faces[second] == v)[0]) for v in edge]
                direct = (first, a0, a1, second, b0, b1)
                reverse = (second, b0, b1, first, a0, a1)
                flipped = (first, a1, a0, second, b1, b0)
                flipped_reverse = (second, b1, b0, first, a1, a0)
                if not any(key in pairs for key in (direct, reverse, flipped, flipped_reverse)):
                    pairs[direct] = {"edge": edge_id, "origin": "current_mesh_seam"}
    for key, identity in pairs.items():
        check_deadline(deadline)
        first, a0, a1, second, b0, b1 = key
        for mip in mips:
            residuals: list[FloatArray] = []
            side_valid = True
            for face, corners in ((first, [a0, a1]), (second, [b0, b1])):
                bary = np.full((3, 3), 1e-5)
                bary[:, corners[0]] += np.asarray([.25, .5, .75]) * (1 - 3e-5)
                bary[:, corners[1]] += np.asarray([.75, .5, .25]) * (1 - 3e-5)
                triangle = surface.uv[surface.uv_faces[face]]
                uv = bary @ triangle
                residuals.append(sample_rgba8(levels[mip], uv)[:, :3]
                                 - _filtered(target, face, triangle, uv, len(levels[0]), mip)[:, :3])
                absolute_error = np.linalg.norm(residuals[-1], axis=1)
                side_errors.extend(absolute_error.tolist())
                side_valid = side_valid and bool(absolute_error.mean() <= limits["seam_mean"]
                                                 and absolute_error.max() <= limits["seam_max"])
            error = np.linalg.norm(residuals[0] - residuals[1], axis=1)
            errors.extend(error.tolist())
            records.append({**identity, "faces": [first, second], "mip": mip,
                            "mean": float(error.mean()), "max": float(error.max()), "side_valid": side_valid,
                            "side_max": float(max(np.linalg.norm(r, axis=1).max() for r in residuals))})
    value = np.asarray(errors)
    absolute = np.asarray(side_errors)
    jump_valid = not len(value) or bool(value.mean() <= limits["seam_mean"] and value.max() <= limits["seam_max"])
    ancestry = target.target.record.get("original_topology")
    authoritative = isinstance(ancestry, dict)
    return {"valid": jump_valid and all(record["side_valid"] for record in records), "jump_valid": jump_valid,
            "authoritative_original_topology_supplied": authoritative,
            "scope": "authoritative_source_and_current_edges" if authoritative else "canonical_input_edges_only",
            "original_topology_sha256": ancestry.get("sha256") if isinstance(ancestry, dict) else None,
            "original_corner_ids_sha256": ancestry.get("corner_ids_sha256") if isinstance(ancestry, dict) else None,
            "mean": float(value.mean()) if len(value) else 0., "max": float(value.max()) if len(value) else 0.,
            "absolute_side_mean": float(absolute.mean()) if len(absolute) else 0.,
            "absolute_side_max": float(absolute.max()) if len(absolute) else 0.,
            "samples": len(value), "edges": records,
            "original_incidence_pairs": len(target.target.edge_pairs), "evaluated_seam_pairs": len(pairs),
            "continuous_identical_uv_pairs": continuous_pairs,
            "contract": "authoritative original-edge plus current-seam residual jump and each-side absolute reconstruction gates; exact UV/source-continuous unmodified edges use shared-sample identity"}


def export_color_reuse(request: dict[str, object], output: Path, deadline: float) -> dict[str, object]:
    snapshot = require_snapshot(request)
    path = Path(str(request.get("bake", "")))
    record, target, levels = _load(snapshot, path)
    mode = request.get("mode", "accepted")
    if mode not in ("accepted", "diagnostic"):
        raise ValueError("INVALID_EXPORT_MODE")
    validation_path = request.get("validation")
    evidence = read_object(Path(validation_path)) if isinstance(validation_path, str) else {}
    if evidence and (evidence.get("scope") != SCOPE or evidence.get("state_hash") != snapshot.state_hash
                     or evidence.get("bake_sha256") != file_hash(path)
                     or evidence.get("target_hash") != record["target_hash"]
                     or evidence.get("profile_hash") != object_hash(evidence.get("profile"))):
        raise ValueError("STALE_BASECOLOR_VALIDATION")
    if evidence:
        validation_profile = cast(dict[str, object], evidence.get("profile", {}))
        _geometry_profile({"geometry_profile": validation_profile.get("geometry", {})},
                          _record_geometry_profile(record))
    valid = evidence.get("valid") is True and all(isinstance(evidence.get(k), dict)
        and cast(dict[str, object], evidence[k]).get("valid") is True for k in ("geometry", "material", "render", "seams"))
    if mode == "accepted" and not valid:
        return {"status": "REJECTED", "execution_success": True, "reason": "SCOPED_ACCEPTED_EXPORT_REQUIRES_VALIDATION"}
    surface = snapshot.state.surface
    mesh = trimesh.Trimesh(vertices=surface.mesh.vertices[surface.mesh.faces].reshape(-1, 3),
                          faces=np.arange(len(surface.mesh.faces) * 3).reshape(-1, 3), process=False)
    mesh.visual = TextureVisuals(uv=surface.uv[surface.uv_faces].reshape(-1, 2),
                                material=PBRMaterial(baseColorTexture=Image.fromarray(levels[0]),
                                                     metallicFactor=0., roughnessFactor=1.,
                                                     doubleSided=bool(target.record.get("source_double_sided", False))))
    mesh.metadata["validation_scope"] = "BaseColor only; source specular and other PBR channels not reproduced"
    mesh.export(output / "asset.glb", file_type="glb")
    shutil.copytree(path.parent / "texture", output / "texture")
    for name in cast(dict[str, str], record["files"]):
        relative = (path.parent / name).resolve().relative_to(path.parent.resolve())
        if relative.parts[0] != "texture":
            destination = output / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path.parent / relative, destination)
    shutil.copytree(snapshot.root, output / "snapshot")
    (output / "target").mkdir()
    shutil.copy2(target.path, output / "target/target.json")
    shutil.copy2(target.path.parent / "target.npz", output / "target/target.npz")
    if (target.path.parent / "original_topology.npz").is_file():
        shutil.copy2(target.path.parent / "original_topology.npz", output / "target/original_topology.npz")
    write_json(output / "bake.json", record)
    write_json(output / "validation.json", evidence)
    check_deadline(deadline)
    result: dict[str, object] = {"scope": SCOPE, "mode": mode, "quality_accepted": valid,
        "full_pbr_passed": False, "gpu_verified": False, "artist_reviewed": False,
        "state_hash": snapshot.state_hash, "target_hash": record["target_hash"],
        "payload_bytes": record["payload_bytes"], "package_bytes": record["package_bytes"],
        "codec": record.get("codec", "RGBA8_SRGB_RGB_LINEAR_ALPHA"),
        "runtime_texture_bytes": record.get("runtime_texture_bytes", record["payload_bytes"]),
        "runtime_package_bytes": record.get("runtime_package_bytes", record["package_bytes"]),
        "decoded_preview_bytes": record.get("decoded_preview_bytes", sum(level.nbytes for level in levels)),
        "decoded_preview_package_bytes": record.get("decoded_preview_package_bytes", record["package_bytes"]),
        "runtime_artifacts": record.get("runtime_artifacts", []),
        "delivered_artifact_bytes_scope": "artifact tree at export-handler completion; excludes late runner transport receipt/log bytes",
        "validated_mips": cast(dict[str, object], evidence.get("profile", {})).get("mip_levels", []),
        "preview_material": "BaseColor diagnostic presentation; roughness=1 metallic=0; not source PBR",
        "files": {str(p.relative_to(output)): file_hash(p) for p in sorted(output.rglob("*")) if p.is_file()},
        **_geometry_metadata(_record_geometry_profile(record))}
    write_json(output / "export.json", result)
    (output / "report.md").write_text(
        f"# Base Color UV 复用导出\n\n- 验收范围：`{SCOPE}`；通过：`{valid}`；导出模式：`{mode}`。\n"
        f"- 已检查 LOD：`{result['validated_mips']}`；完整 mip 字节：`{record['payload_bytes']}`。\n"
        f"- 编码：`{result['codec']}`；运行时纹理字节：`{result['runtime_texture_bytes']}`；解码预览像素字节：`{result['decoded_preview_bytes']}`。\n"
        f"- 几何配置：`{result['geometry_policy']}`；使用严格默认配置：`{result['strict_geometry_profile_applied']}`。\n"
        "- 完整 PBR、GPU、艺术验收均未完成；预览材质不复现源 Specular。\n\n"
        "![实际颜色贴图](texture/BaseColor_mip0.png)\n", encoding="utf-8")
    # Include the exported preview GLB, debug snapshots, reports, and this
    # manifest in delivery size. This is deliberately not codec payload size.
    for _ in range(4):
        delivered = sum(p.stat().st_size for p in output.rglob("*") if p.is_file())
        if result.get("delivered_artifact_bytes") == delivered:
            break
        result["delivered_artifact_bytes"] = delivered
        write_json(output / "export.json", result)
    return {"status": "OK", "execution_success": True, "scope": SCOPE, "quality_accepted": valid,
            "full_pbr_passed": False, "asset": str((output / "asset.glb").resolve()),
            "report": str((output / "report.md").resolve()), "manifest": str((output / "export.json").resolve()),
            "codec": result["codec"], "runtime_texture_bytes": result["runtime_texture_bytes"],
            "decoded_preview_bytes": result["decoded_preview_bytes"],
            "delivered_artifact_bytes": result["delivered_artifact_bytes"],
            **_geometry_metadata(_record_geometry_profile(record))}
