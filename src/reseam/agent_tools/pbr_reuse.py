"""Frozen full-PBR surface targets and explicit member-safe atlas composition.

Projection is a bounded nearest-surface query, not a cage/ray bake. The source
normal map is evaluated in the high mesh frame before transport to low frames.
No numerical or visual acceptance is implied by producing textures.
"""

import hashlib
from dataclasses import dataclass, replace
from pathlib import Path
from typing import cast

import numpy as np
import shapely
from scipy.ndimage import distance_transform_edt
from shapely import STRtree

from ..budget_material import (
    MaterialAtlas,
    MaterialSamples,
    _sample_material_with_frames,
    atlas_from_arrays,
    frame_arrays,
    interpolated_frames,
)
from ..budget_texture import BoolArray, _face_pixels, _stratified_barycentric
from ..mesh import chart_labels
from ..types import FloatArray, IntArray, MeshState
from .artist import color_locked_faces, member_faces, visual_policy
from .color_reuse import (
    _geometry_hash,
    _geometry_profile,
    _prerequisite_profile,
    _project_uv_barycentric,
)
from .geometry import geometry_report
from .material_identity import face_corner_identity
from .pbr_projection import FiniteRayProjection
from .state import (
    Snapshot,
    check_deadline,
    file_hash,
    load_snapshot,
    object_hash,
    read_object,
    require_snapshot,
    write_json,
)

SCOPE = "pbr_reuse"
SIDE_CONTRACT = "declared double-sided mapped world normal follows low geometric side; single-sided opposite rejected"


def frame_hash(frames: tuple[FloatArray, FloatArray, FloatArray]) -> str:
    return hashlib.sha256(b"".join(array.tobytes() for array in frames)).hexdigest()


def _number(request: dict[str, object], key: str, default: float) -> float:
    value = request.get(key, default)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not np.isfinite(value) or value < 0:
        raise ValueError("NONNEGATIVE_FINITE_NUMBER_REQUIRED:" + key)
    return float(value)


def _integer(request: dict[str, object], key: str, default: int, low: int, high: int) -> int:
    value = request.get(key, default)
    if type(value) is not int or not low <= value <= high:
        raise ValueError("INTEGER_OUT_OF_RANGE:" + key)
    return value


@dataclass
class Domain:
    faces: IntArray
    uv: FloatArray
    tree: STRtree


class PBRTarget:
    """Source-only target, addressable with original low face/corner identity."""

    def __init__(self, snapshot: Snapshot, path: Path, deadline: float) -> None:
        from .material import _reference

        self.path = path.resolve()
        self.record = read_object(path)
        record = dict(self.record)
        digest = record.pop("target_hash", None)
        if digest != object_hash(record) or record.get("scope") != SCOPE:
            raise ValueError("PBR_TARGET_HASH_MISMATCH")
        if record.get("source_side_contract") != SIDE_CONTRACT:
            raise ValueError("PBR_SOURCE_SIDE_CONTRACT_CHANGED:freeze a new target")
        if record.get("asset_id") != snapshot.asset_id or record.get("geometry_hash") != _geometry_hash(snapshot):
            raise ValueError("PBR_TARGET_GEOMETRY_MISMATCH")
        self.frozen = load_snapshot(Path(str(record["snapshot"])))
        if self.frozen.state_hash != record["state_hash"]:
            raise ValueError("PBR_FROZEN_SNAPSHOT_CHANGED")
        if file_hash(Path(str(record["source"]))) != record["source_sha256"]:
            raise ValueError("PBR_TARGET_SOURCE_CHANGED")
        source_snapshot = replace(self.frozen, metadata={**self.frozen.metadata, "reference_glb": record["source"]})
        self.reference, source = _reference(source_snapshot, {})
        if file_hash(source) != record["source_sha256"]:
            raise ValueError("PBR_TARGET_SOURCE_CHANGED")
        self.deadline = deadline
        self.surface = self.frozen.state.surface
        self.frames = frame_arrays(self.surface)
        if record.get("frozen_frame_hash") != frame_hash(self.frames):
            raise ValueError("PBR_FROZEN_FRAME_CONTRACT_CHANGED:freeze a new target")
        if not np.array_equal(self.frames[0], frame_arrays(snapshot.state.surface)[0]):
            raise ValueError("PBR_TARGET_LOW_NORMALS_CHANGED")
        self.distance = float(cast(float, record["max_projection_fraction"])) * float(
            np.linalg.norm(np.ptp(self.surface.mesh.vertices, axis=0)))
        self.source_double_sided = self.reference.metadata.get("source_double_sided") is True
        high_xyz = self.reference.geometry.vertices[self.reference.geometry.faces]
        low_xyz = self.surface.mesh.vertices[self.surface.mesh.faces]
        self.high_geometric_normals = np.cross(high_xyz[:, 1] - high_xyz[:, 0], high_xyz[:, 2] - high_xyz[:, 0])
        self.low_geometric_normals = np.cross(low_xyz[:, 1] - low_xyz[:, 0], low_xyz[:, 2] - low_xyz[:, 0])
        self.side_flips = 0
        self.single_sided_opposite = 0
        self.queried_samples = 0
        self.flipped_faces: dict[int, int] = {}
        self.projection: FiniteRayProjection | None = None
        self.projection_method = str(record.get("projection_method", "nearest_surface"))
        if self.projection_method == "finite_ray":
            self.projection = FiniteRayProjection(self.surface, self.reference.geometry.vertices,
                self.reference.geometry.faces, float(cast(float, record["front_fraction"])),
                float(cast(float, record["back_fraction"])), source_double_sided=self.source_double_sided)
        elif self.projection_method == "same_surface_face_corner":
            if face_corner_identity(low_xyz, high_xyz) != record.get("same_surface_identity"):
                raise ValueError("PBR_SAME_SURFACE_FROZEN_IDENTITY_CHANGED")
        elif self.projection_method != "nearest_surface":
            raise ValueError("UNKNOWN_PBR_PROJECTION_METHOD")
        self.slaves: dict[int, Domain] = {}
        self.face_groups: dict[int, str] = {}
        self.domains: dict[str, Domain] = {}
        protected = set(cast(list[int], record.get("protected_faces", [])))
        if record["policy"] == "source_original":
            for group in snapshot.state.groups:
                if protected.intersection(face for member in member_faces(group) for face in member):
                    raise ValueError("PROTECTED_PBR_COMPOSITION_CONFLICT:" + group.group_id)
        for group in cast(list[dict[str, object]], record["groups"]):
            faces = np.asarray(group["master_faces"], dtype=np.int64)
            uv = self.surface.uv[self.surface.uv_faces[faces]]
            domain = Domain(faces, uv, STRtree([shapely.Polygon(triangle) for triangle in uv]))
            self.domains[str(group["id"])] = domain
            for face in cast(list[int], group["all_faces"]):
                self.face_groups[face] = str(group["id"])
            if record["policy"] == "master":
                for face in cast(list[int], group["slave_faces"]):
                    self.slaves[face] = domain
        if set(color_locked_faces(snapshot)).intersection(self.slaves):
            raise ValueError("PROTECTED_PBR_TARGET_CONFLICT")

    def original(self, faces: IntArray, bary: FloatArray) -> MaterialSamples:
        check_deadline(self.deadline)
        if self.projection_method == "same_surface_face_corner":
            samples = self.reference.sample_at(faces, bary, np.zeros(len(faces)), np.ones(len(faces), dtype=bool))
        elif self.projection is not None:
            ids, coordinates, distance, valid = self.projection.query(faces, bary)
            samples = self.reference.sample_at(ids, coordinates, distance, valid)
        else:
            points = np.einsum("ni,nij->nj", bary, self.surface.mesh.vertices[self.surface.mesh.faces[faces]])
            samples = self.reference.sample(points, self.distance, deadline=self.deadline)
        hit_faces = np.clip(samples.target_faces, 0, len(self.high_geometric_normals) - 1)
        opposite = np.sum(self.high_geometric_normals[hit_faces] * self.low_geometric_normals[faces], axis=1) < 0
        opposite &= samples.valid
        normal = samples.normal_world.copy()
        valid = samples.valid.copy()
        self.queried_samples += len(faces)
        if self.source_double_sided:
            normal[opposite] *= -1
            self.side_flips += int(opposite.sum())
            for face, count in zip(*np.unique(faces[opposite], return_counts=True), strict=True):
                self.flipped_faces[int(face)] = self.flipped_faces.get(int(face), 0) + int(count)
        else:
            valid &= ~opposite
            self.single_sided_opposite += int(opposite.sum())
        return MaterialSamples(samples.base_color, normal, samples.orm, valid, samples.distances,
                               samples.target_faces, samples.barycentric)

    def sample(self, face: int, bary: FloatArray) -> MaterialSamples:
        domain = self.slaves.get(face)
        faces = np.full(len(bary), face, dtype=np.int64)
        if domain is None:
            return self.original(faces, bary)
        uv = bary @ self.surface.uv[self.surface.uv_faces[face]]
        pairs = domain.tree.query(shapely.points(uv), predicate="intersects")
        ids = np.full(len(uv), -1, dtype=np.int64)
        if pairs.size:
            order = np.lexsort((domain.faces[pairs[1]], pairs[0]))
            ordered = pairs[:, order]
            unique = np.r_[True, np.diff(ordered[0]) != 0]
            ids[ordered[0, unique]] = ordered[1, unique]
        inside = ids >= 0
        # A reconstructed texel center may differ by one floating-point ULP
        # at a common edge. Only that numerical boundary is tolerated.
        if (~inside).any():
            ids[~inside] = np.asarray(domain.tree.nearest(shapely.points(uv[~inside])), dtype=np.int64)
        coordinates = _project_uv_barycentric(domain.uv[ids], uv)
        projected = np.einsum("ni,nij->nj", coordinates, domain.uv[ids])
        inside |= np.linalg.norm(projected - uv, axis=1) <= 1e-12
        samples = self.original(domain.faces[ids], coordinates)
        master, valid_master = interpolated_frames(*self.frames, domain.faces[ids], coordinates)
        member, valid_member = interpolated_frames(*self.frames, faces, bary)
        local = np.einsum("nji,nj->ni", master, samples.normal_world)
        world = np.einsum("nij,nj->ni", member, local)
        return MaterialSamples(samples.base_color, world, samples.orm,
            samples.valid & inside & valid_master & valid_member, samples.distances,
            samples.target_faces, samples.barycentric)


def differences(first: MaterialSamples, second: MaterialSamples) -> FloatArray:
    cosine = np.sum(first.normal_world * second.normal_world, axis=1)
    return np.column_stack((np.linalg.norm(first.base_color[:, :3] - second.base_color[:, :3], axis=1),
        np.degrees(np.arccos(np.clip(cosine, -1, 1))), np.abs(first.orm - second.orm)))


def source_and_target(target: PBRTarget, face: int, bary: FloatArray) -> tuple[MaterialSamples, MaterialSamples]:
    """Reuse an identical query only where the frozen target is unchanged."""
    original = target.original(np.full(len(bary), face, dtype=np.int64), bary)
    return original, original if face not in target.slaves else target.sample(face, bary)


def freeze_target(request: dict[str, object], output: Path, deadline: float) -> dict[str, object]:
    from .material import _reference

    snapshot = require_snapshot(request)
    check_deadline(deadline)
    policy = request.get("policy", "source_original")
    if policy not in ("source_original", "master"):
        raise ValueError("PBR_POLICY_SOURCE_ORIGINAL_OR_MASTER")
    protected = request.get("protected_faces", [])
    nfaces = len(snapshot.state.surface.mesh.faces)
    if not isinstance(protected, list) or any(type(f) is not int or not 0 <= f < nfaces for f in protected):
        raise ValueError("INVALID_PROTECTED_FACES")
    locked = set(protected) | set(color_locked_faces(snapshot))
    for region in cast(list[dict[str, object]], snapshot.metadata.get("regions", [])):
        locked.update(cast(list[int], region.get("protected_faces", [])))
        if region.get("role") == "protected":
            locked.update(cast(list[int], region.get("faces", [])))
    masters = request.get("master_members", {})
    if not isinstance(masters, dict) or any(v not in ("first", "second") for v in masters.values()):
        raise ValueError("INVALID_PBR_MASTER_MEMBERS")
    groups: list[dict[str, object]] = []
    for group in snapshot.state.groups:
        members = member_faces(group)
        master = members[1] if masters.get(group.group_id) == "second" else members[0]
        all_faces = sorted({f for member in members for f in member})
        slave = sorted(set(all_faces) - set(master))
        if policy == "source_original" and locked.intersection(all_faces):
            raise ValueError("PROTECTED_PBR_COMPOSITION_CONFLICT:" + group.group_id)
        if policy == "master" and locked.intersection(slave):
            raise ValueError("PROTECTED_PBR_TARGET_CONFLICT:" + group.group_id)
        groups.append({"id": group.group_id, "master_faces": list(master), "slave_faces": slave,
                       "all_faces": all_faces})
    profile = _geometry_profile(request)
    visual = visual_policy(request, snapshot)
    safety = geometry_report(snapshot.state, _prerequisite_profile(profile, request))
    write_json(output / "geometry.json", safety)
    if safety.get("valid") is not True and not visual:
        return {"status": "REJECTED", "reason": "GEOMETRY_UNSAFE", "geometry": safety}
    source_reference = request.get("source_reference")
    if source_reference is not None and not isinstance(source_reference, str):
        raise TypeError("PBR_SOURCE_REFERENCE_PATH_REQUIRED")
    source_snapshot = snapshot if source_reference is None else replace(
        snapshot, metadata={**snapshot.metadata, "reference_glb": str(Path(source_reference).resolve())})
    reference, source = _reference(source_snapshot, request)
    fraction = _number(request, "max_projection_fraction", .02)
    if fraction <= 0:
        raise ValueError("POSITIVE_PROJECTION_FRACTION_REQUIRED")
    method = request.get("projection_method", "nearest_surface")
    if method not in ("nearest_surface", "finite_ray", "same_surface_face_corner"):
        raise ValueError("PBR_PROJECTION_NEAREST_SURFACE_FINITE_RAY_OR_SAME_SURFACE_FACE_CORNER")
    front, back = _number(request, "front_fraction", .02), _number(request, "back_fraction", .02)
    if method == "finite_ray" and (front <= 0 or back <= 0):
        raise ValueError("PBR_FINITE_RAY_POSITIVE_DISTANCES_REQUIRED")
    record: dict[str, object] = {
        "schema_version": 1, "scope": SCOPE, "asset_id": snapshot.asset_id,
        "geometry_hash": _geometry_hash(snapshot), "snapshot": str(snapshot.root),
        "frozen_frame_hash": frame_hash(frame_arrays(snapshot.state.surface)),
        "state_hash": snapshot.state_hash, "source": str(source), "source_sha256": file_hash(source),
        "policy": policy, "groups": groups, "protected_faces": sorted(locked),
        "max_projection_fraction": fraction, "projection": "nearest_surface_low_bbox_diagonal",
        "projection_method": method, "front_fraction": front, "back_fraction": back,
        "geometry_profile": profile, "acceptance_policy": "visual" if visual else "numeric",
        "normal_transport": "source mapped normal world -> frozen master tangent -> frozen member world",
        "source_geometry_and_normal_map_included": True,
        "source_double_sided": reference.metadata.get("source_double_sided") is True,
        "source_side_contract": SIDE_CONTRACT,
        "render_validated": False, "target_appearance_reviewed": False,
    }
    if method == "finite_ray":
        record["projection"] = "finite_front_facing_rays_low_bbox_diagonal_no_nearest_fallback"
    elif method == "same_surface_face_corner":
        record["same_surface_identity"] = face_corner_identity(
            snapshot.state.surface.mesh.vertices[snapshot.state.surface.mesh.faces],
            reference.geometry.vertices[reference.geometry.faces])
        record["projection"] = "verified_same_surface_face_corner_barycentric_no_spatial_lookup"
    record["target_hash"] = object_hash(record)
    write_json(output / "target.json", record)
    target = PBRTarget(snapshot, output / "target.json", deadline)
    bary = _stratified_barycentric(4)
    reports: dict[str, object] = {}
    for group in groups:
        values: list[FloatArray] = []
        invalid = 0
        for face in cast(list[int], group["all_faces"]):
            a = target.original(np.full(len(bary), face, dtype=np.int64), bary)
            b = target.sample(face, bary)
            invalid += int((~(a.valid & b.valid)).sum())
            values.append(differences(a, b))
        delta = np.concatenate(values)
        reports[str(group["id"])] = {"mean": delta.mean(axis=0).tolist(), "maximum": delta.max(axis=0).tolist(),
                                    "invalid_samples": invalid, "samples": len(delta)}
    write_json(output / "target_changes.json", {"columns": ["linear_rgb_l2", "normal_degrees", "AO", "roughness", "metallic"],
        "groups": reports, "source_vs_target_not_reconstruction": True})
    return {"status": "OK", "target": str(output / "target.json"), "target_hash": record["target_hash"],
            "changes": str(output / "target_changes.json"), "full_pbr_passed": False}


def bake_pbr_reuse(request: dict[str, object], output: Path, deadline: float) -> dict[str, object]:
    from .material import _save_bake

    snapshot = require_snapshot(request)
    resolution = _integer(request, "resolution", 128, 8, 2048)
    if resolution & (resolution - 1):
        raise ValueError("PBR_REUSE_POWER_OF_TWO_REQUIRED")
    padding = _integer(request, "padding_pixels", 4, 0, resolution // 2 - 1)
    raw_target = request.get("target")
    if not isinstance(raw_target, str):
        raise TypeError("PBR_FROZEN_TARGET_REQUIRED")
    target = PBRTarget(snapshot, Path(raw_target), deadline)
    profile = _geometry_profile(request, cast(dict[str, object], target.record["geometry_profile"]), resolution)
    visual = visual_policy(request, snapshot)
    safety = geometry_report(snapshot.state, _prerequisite_profile(profile, request))
    write_json(output / "geometry.json", safety)
    if safety.get("valid") is not True and not visual:
        return {"status": "REJECTED", "reason": "GEOMETRY_UNSAFE", "geometry": safety}
    preserved_pairs: set[tuple[int, int]] = set()
    for retained in cast(list[str], safety.get("preserved_source_conflicts", [])):
        _, a, b = retained.split(":")
        preserved_pairs.add((min(int(a), int(b)), max(int(a), int(b))))
    atlas, audit = compose(snapshot, target, resolution, padding, deadline, preserved_pairs)
    write_json(output / "member_audit.json", audit)
    if audit["invalid_samples"]:
        return {"status": "REJECTED", "reason": "PBR_PROJECTION_OR_TARGET_COVERAGE",
                "audit": str(output / "member_audit.json"), "samples": audit["samples"],
                "invalid_samples": audit["invalid_samples"], "invalid_faces": audit["invalid_faces"]}
    # Incompatible groups require explicit visual decision or unsharing; no
    # unchecked fallback to last-writer composition is possible.
    limits = {"color": _number(request, "shared_color_max", .2),
              "normal": _number(request, "shared_normal_degrees", 30),
              "orm": _number(request, "shared_orm_max", .15)}
    conflicts = [name for name, item in cast(dict[str, dict[str, object]], audit["groups"]).items()
                 if float(cast(float, item["max_color"])) > limits["color"]
                 or float(cast(float, item["max_normal_degrees"])) > limits["normal"]
                 or float(cast(float, item["max_orm"])) > limits["orm"]]
    if conflicts and not visual:
        return {"status": "REJECTED", "reason": "PBR_SHARED_MEMBER_CONFLICT", "groups": conflicts,
                "audit": str(output / "member_audit.json")}
    require_snapshot(request)
    if file_hash(Path(str(target.record["source"]))) != target.record["source_sha256"]:
        raise ValueError("PBR_TARGET_SOURCE_CHANGED_DURING_BAKE")
    if read_object(target.path) != target.record:
        raise ValueError("PBR_TARGET_CHANGED_DURING_BAKE")
    backend = ("cpu_same_surface_pbr_reuse" if target.projection_method == "same_surface_face_corner"
               else "cpu_nearest_pbr_reuse")
    result = _save_bake(snapshot, atlas, output, Path(str(target.record["source"])), backend,
        {"resolution": resolution, "padding_pixels": padding, "max_projection_fraction": target.record["max_projection_fraction"]})
    path = output / "bake.json"
    record = read_object(path)
    record.update(scope=SCOPE, target=str(target.path), target_sha256=file_hash(target.path),
        target_hash=target.record["target_hash"], geometry_profile=profile,
        baked_frame_hash=frame_hash(frame_arrays(snapshot.state.surface)),
        member_conflicts=conflicts, compatibility_limits=limits, acceptance_policy="visual" if visual else "numeric",
        target_policy=target.record["policy"], projection=target.record["projection"])
    write_json(path, record)
    result.update(bake_hash=file_hash(path), scope=SCOPE, member_conflicts=conflicts,
                  audit=str(output / "member_audit.json"))
    return result


def _raster_overlap_allowed(
    surface: MeshState, face: int, previous: IntArray, x: IntArray, y: IntArray,
    resolution: int, preserved_pairs: set[tuple[int, int]],
) -> tuple[BoolArray, int]:
    """Only a true shared UV edge or a frozen source conflict permits duplication."""
    allowed = np.zeros(len(previous), dtype=bool)
    preserved = 0
    points = np.column_stack(((x + .5) / resolution, 1 - (y + .5) / resolution))
    vertices = surface.mesh.faces[face]
    for prior in np.unique(previous):
        selected = previous == prior
        if (min(face, int(prior)), max(face, int(prior))) in preserved_pairs:
            allowed[selected] = True
            preserved += int(selected.sum())
            continue
        if not 0 <= prior < len(surface.mesh.faces):
            continue
        common = np.intersect1d(vertices, surface.mesh.faces[prior])
        if len(common) != 2:
            continue
        first = [int(np.flatnonzero(vertices == v)[0]) for v in common]
        second = [int(np.flatnonzero(surface.mesh.faces[prior] == v)[0]) for v in common]
        edge = surface.uv[surface.uv_faces[face, first]]
        prior_edge = surface.uv[surface.uv_faces[prior, second]]
        if not np.allclose(edge, prior_edge, rtol=0, atol=1e-12):
            continue
        delta = edge[1] - edge[0]
        squared = float(delta @ delta)
        if squared <= 0:
            continue
        t = np.clip((points[selected] - edge[0]) @ delta / squared, 0, 1)
        closest = edge[0] + t[:, None] * delta
        allowed[selected] = np.linalg.norm(points[selected] - closest, axis=1) <= 1e-12
    return allowed, preserved


def compose(snapshot: Snapshot, target: PBRTarget, resolution: int, padding: int,
            deadline: float, preserved_pairs: set[tuple[int, int]] | None = None,
            ) -> tuple[MaterialAtlas, dict[str, object]]:
    """Sample every member first; only explicit group members can share pixels."""
    surface = snapshot.state.surface
    frames = frame_arrays(surface)
    owners = np.full((resolution, resolution), -1, dtype=np.int32)
    values = np.zeros((resolution, resolution, 10))
    values[..., 4:7] = [0, 0, 1]
    values[..., 7:] = [1, 1, 0]
    weights = np.zeros((resolution, resolution))
    face_owners = np.arange(len(surface.mesh.faces), dtype=np.int32)
    shared: dict[int, str] = {}
    member_ids = chart_labels(surface)
    next_member = int(member_ids.max()) + 1
    for index, group in enumerate(snapshot.state.groups):
        for member in member_faces(group):
            for face in member:
                face_owners[face] = len(face_owners) + index
                shared[face] = group.group_id
                member_ids[face] = next_member
            next_member += 1
    visited: dict[int, dict[int, int]] = {}
    report: dict[str, dict[str, object]] = {}
    count = invalid = 0
    preserved_pixels = 0
    bad_faces: list[int] = []
    for face in range(len(surface.mesh.faces)):
        check_deadline(deadline)
        x, y, bary = _face_pixels(surface.uv[surface.uv_faces[face]], resolution, resolution)
        if not len(x):
            continue
        ids = np.full(len(x), face, dtype=np.int64)
        original, samples = source_and_target(target, face, bary)
        matrix, valid = interpolated_frames(*frames, ids, bary)
        local = np.einsum("nji,nj->ni", matrix, samples.normal_world)
        valid &= samples.valid & original.valid
        invalid += int((~valid).sum())
        if not valid.all():
            bad_faces.append(face)
        count += len(x)
        data = np.column_stack((samples.base_color, local, samples.orm))
        # Query every face first, but a member contributes once at a shared
        # triangulation edge regardless of its tessellation or face ordering.
        seen = visited.setdefault(int(member_ids[face]), {})
        keys = y * resolution + x
        keep = np.asarray([int(key) not in seen for key in keys], dtype=bool)
        # Validate every repeated texel BEFORE deduplication: same-chart
        # membership is not permission for positive-area self-overlap.
        if (~keep).any():
            previous = np.asarray([seen[int(key)] for key in keys[~keep]], dtype=np.int64)
            allowed, retained = _raster_overlap_allowed(surface, face, previous, x[~keep], y[~keep],
                resolution, preserved_pairs or set())
            preserved_pixels += retained
            if not allowed.all():
                raise ValueError("UNAUTHORIZED_PBR_RASTER_OVERLAP:" + str(face))
        for key in keys[keep]:
            seen[int(key)] = face
        if face in shared:
            item = report.setdefault(shared[face], {"samples": 0, "overlap_texels": 0,
                "max_color": 0., "max_normal_degrees": 0., "max_orm": 0., "source_target_max": [0.] * 5})
            item["samples"] = int(cast(int, item["samples"])) + len(x)
            delta = differences(original, samples)
            item["source_target_max"] = np.maximum(item["source_target_max"], delta.max(axis=0)).tolist()
        x, y, data, valid = x[keep], y[keep], data[keep], valid[keep]
        occupied = weights[y, x] > 0
        permitted = owners[y, x] == face_owners[face]
        # Neighbor triangles can share raster centers on their common edge.
        unpermitted = occupied & ~permitted
        if np.any(unpermitted):
            previous = np.asarray(owners[y[unpermitted], x[unpermitted]], dtype=np.int64)
            allowed, retained = _raster_overlap_allowed(surface, face, previous, x[unpermitted], y[unpermitted],
                resolution, preserved_pairs or set())
            preserved_pixels += retained
            if not allowed.all():
                raise ValueError("UNAUTHORIZED_PBR_RASTER_OVERLAP:" + str(face))
        if face in shared:
            item = report[shared[face]]
            if occupied.any():
                existing = values[y[occupied], x[occupied]] / weights[y[occupied], x[occupied], None]
                incoming = data[occupied]
                norm_a = existing[:, 4:7] / np.maximum(np.linalg.norm(existing[:, 4:7], axis=1, keepdims=True), 1e-30)
                norm_b = incoming[:, 4:7] / np.maximum(np.linalg.norm(incoming[:, 4:7], axis=1, keepdims=True), 1e-30)
                item["max_color"] = max(float(cast(float, item["max_color"])), float(np.linalg.norm(existing[:, :3] - incoming[:, :3], axis=1).max()))
                item["max_normal_degrees"] = max(float(cast(float, item["max_normal_degrees"])), float(np.degrees(np.arccos(np.clip(np.sum(norm_a * norm_b, axis=1), -1, 1))).max()))
                item["max_orm"] = max(float(cast(float, item["max_orm"])), float(np.abs(existing[:, 7:] - incoming[:, 7:]).max()))
                item["overlap_texels"] = int(cast(int, item["overlap_texels"])) + int(occupied.sum())
        # Deterministic arithmetic mean after all face/member source queries;
        # the raw source-target differences remain separately inspectable.
        new = ~occupied
        values[y[new], x[new]] = 0
        values[y[valid], x[valid]] += data[valid]
        weights[y[valid], x[valid]] += 1
        owners[y[new], x[new]] = face_owners[face]
    coverage = weights > 0
    values[coverage] /= weights[coverage, None]
    if coverage.any() and padding:
        padded = distance_transform_edt(~coverage, return_indices=True)
        if not isinstance(padded, tuple):
            raise TypeError("PADDING_INDICES_REQUIRED")
        distance, nearest = padded
        pad = ~coverage & (distance <= padding)
        values[pad] = values[nearest[0][pad], nearest[1][pad]]
    audit: dict[str, object] = {"samples": count, "invalid_samples": invalid, "invalid_faces": bad_faces,
        "preserved_source_overlap_samples": preserved_pixels,
        "preserved_source_pairs": sorted(preserved_pairs or set()),
        "groups": report, "projection": target.record["projection"], "source_normal_map_included": True,
        "composition": "member values transformed to target frame before averaging; no last writer"}
    if target.projection is not None:
        audit["ray_projection"] = target.projection.audit()
    audit["source_side_orientation"] = {"source_double_sided": target.source_double_sided,
        "queried_samples": target.queried_samples, "flipped_samples": target.side_flips,
        "flipped_low_faces": target.flipped_faces, "single_sided_opposite_rejected": target.single_sided_opposite,
        "rule": "sign(dot(source geometric normal, low geometric normal)); never use bumped normal sign"}
    metadata = dict(audit)
    if isinstance(metadata.get("ray_projection"), dict):
        metadata["ray_projection"] = {key: value for key, value in cast(dict[str, object], metadata["ray_projection"]).items()
                                      if key != "witnesses"}
    atlas = atlas_from_arrays(values[..., :4], values[..., 4:7], values[..., 7:], coverage, {
        **metadata, "padding_pixels": padding, "target_hash": target.record["target_hash"],
        "invalid_projection_member_texels": invalid, "invalid_projection_faces": bad_faces,
        "support_query_policy": "every rasterized original face before composition",
    })
    return atlas, audit


def validate_pbr_reuse(request: dict[str, object], output: Path, deadline: float) -> dict[str, object]:
    """Independent finite probes; Blender/artist acceptance remains separate."""
    from .material import _load_bake

    snapshot = require_snapshot(request)
    bake_path = Path(str(request["bake"]))
    atlas, baked = _load_bake(snapshot, bake_path)
    target_path = Path(str(baked["target"]))
    if file_hash(target_path) != baked["target_sha256"]:
        raise ValueError("PBR_BAKE_TARGET_CHANGED")
    target = PBRTarget(snapshot, target_path, deadline)
    mips = request.get("mip_levels", [0, 1, 2])
    if not isinstance(mips, list) or not mips or any(type(m) is not int or not 0 <= m < len(atlas.levels) for m in mips):
        raise ValueError("INVALID_PBR_MIPS")
    bary = _stratified_barycentric(4)
    rows: list[dict[str, object]] = []
    frames = frame_arrays(snapshot.state.surface)
    expected_samples = [target.sample(face, bary) for face in range(len(snapshot.state.surface.mesh.faces))]
    for mip in mips:
        values: list[FloatArray] = []
        invalid = 0
        for face in range(len(snapshot.state.surface.mesh.faces)):
            check_deadline(deadline)
            expected = expected_samples[face]
            actual = _sample_material_with_frames(snapshot.state.surface, atlas,
                np.full(len(bary), face, dtype=np.int64), bary, mip, frames)
            invalid += int((~(actual.valid & expected.valid)).sum())
            values.append(differences(expected, actual))
        array = np.concatenate(values)
        rows.append({"mip": mip, "mean": array.mean(axis=0).tolist(), "p95": np.quantile(array, .95, axis=0).tolist(),
                     "maximum": array.max(axis=0).tolist(), "invalid": invalid, "samples": len(array)})
    result: dict[str, object] = {"schema_version": 1, "status": "DIAGNOSTIC", "valid": False,
        "scope": SCOPE, "state_hash": snapshot.state_hash, "asset_id": snapshot.asset_id,
        "bake_sha256": file_hash(bake_path), "target_sha256": file_hash(target_path),
        "columns": ["linear_rgb_l2", "normal_degrees", "AO", "roughness", "metallic"], "mips": rows,
        "profile": {"mip_levels": mips, "samples_per_face": len(bary)},
        "render": {"valid": None, "status": "BLENDER_REVIEW_REQUIRED"},
        "full_pbr_passed": False, "artist_reviewed": False, "gpu_verified": False,
        "filtering_note": "Point source probes versus filtered atlas; reports aliasing too; no continuous or mip quality proof"}
    result["profile_hash"] = object_hash(result["profile"])
    write_json(output / "validation.json", result)
    return {"status": "OK", "validation": str(output / "validation.json"), "quality_accepted": False,
            "scope": SCOPE, "full_pbr_passed": False}
