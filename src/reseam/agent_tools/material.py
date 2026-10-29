"""File-bound material baking, independent validation, and honest asset exports."""

import hashlib
import json
import shutil
import struct
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np
import trimesh
from PIL import Image
from trimesh.visual.material import PBRMaterial

from ..bake_variant_bake import DEFAULT_BLENDER, BakeVariantRequest, bake_variant, read_source_materials
from ..budget_material import (
    MaterialAtlas,
    MaterialReference,
    MaterialSamples,
    MaterialThresholds,
    atlas_from_arrays,
    bake_material,
    evaluate_material,
    export_material_glb,
    interpolated_frames,
    mikktspace_frames,
    refresh_material_support,
    sample_linear_rgba8,
    save_material,
)
from ..budget_render import evaluate_render, prepare_render_reference
from ..budget_state import owners_and_members
from ..budget_texture import ReferenceSurface, _face_pixels, decode_rgba8, mip_dimensions, sample_rgba8
from ..types import FloatArray
from .state import (
    Snapshot,
    check_deadline,
    load_snapshot,
    object_hash,
    read_object,
    require_snapshot,
    write_json,
)


def _hash(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _number(request: dict[str, object], key: str, default: float) -> float:
    value = request.get(key, default)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not np.isfinite(value):
        raise ValueError("INVALID_NUMERIC_PARAMETER:" + key)
    return float(value)


def _integer(request: dict[str, object], key: str, default: int) -> int:
    value = request.get(key, default)
    if type(value) is not int:
        raise ValueError("INVALID_INTEGER_PARAMETER:" + key)
    return value


@dataclass
class _SamplerReference(MaterialReference):
    """Retain actual per-channel repeat/clamp rules under the LOD0 reference."""

    wrap: tuple[bool, bool, bool, bool] = (True, True, True, True)

    def sample(
        self, points: FloatArray, max_distance: float, *, deadline: float | None = None
    ) -> MaterialSamples:
        hit = self.geometry.sample(points, max_distance, deadline=deadline)
        ids, bary = hit.target_faces, hit.barycentric
        uv = np.einsum("nk,nkj->nj", bary, self.metadata_corner_uv(ids))
        color = sample_rgba8(self.geometry.image, uv, repeat=self.wrap[0]) * self.geometry.base_factor
        color[:, 3] = 1  # OPAQUE contract ignores source alpha, including factors.
        normal = sample_linear_rgba8(self.normal_image, uv, repeat=self.wrap[1])[:, :3] * 2 - 1
        normal[:, :2] *= self.normal_scale
        lengths = np.linalg.norm(normal, axis=1, keepdims=True)
        matrix, valid = interpolated_frames(self.normals, self.tangents, self.signs, ids, bary)
        world = np.einsum("nij,nj->ni", matrix, normal / np.maximum(lengths, 1e-30))
        world /= np.maximum(np.linalg.norm(world, axis=1, keepdims=True), 1e-30)
        mr = sample_linear_rgba8(self.orm_image, uv, repeat=self.wrap[2])
        orm = np.column_stack(
            (np.ones(len(points)), mr[:, 1] * self.roughness_factor, mr[:, 2] * self.metallic_factor)
        )
        if self.occlusion_image is not None:
            ao = sample_linear_rgba8(self.occlusion_image, uv, repeat=self.wrap[3])[:, 0]
            orm[:, 0] = 1 + self.occlusion_strength * (ao - 1)
        return MaterialSamples(
            color, world, orm, hit.valid & valid & (lengths[:, 0] > 1e-10), hit.distances, ids, bary
        )


def _source_document(path: Path) -> tuple[dict[str, object], bytes]:
    data = path.read_bytes()
    size = struct.unpack_from("<I", data, 12)[0]
    document = json.loads(data[20 : 20 + size])
    binary_offset = 20 + size
    if not isinstance(document, dict) or data[binary_offset + 4 : binary_offset + 8] != b"BIN\0":
        raise ValueError("UNSUPPORTED_REFERENCE:EMBEDDED_BINARY_REQUIRED")
    return document, data[binary_offset + 8 :]


def _accessor(document: dict[str, object], binary: bytes, index: int) -> FloatArray:
    # Dynamic JSON is validated at this external file-format boundary.
    accessors, views = document.get("accessors"), document.get("bufferViews")
    if not isinstance(accessors, list) or not isinstance(views, list):
        raise TypeError("INVALID_SOURCE_ACCESSORS")
    spec = accessors[index]
    if not isinstance(spec, dict) or "sparse" in spec or spec.get("normalized"):
        raise ValueError("UNSUPPORTED_REFERENCE:SPARSE_OR_NORMALIZED_ATTRIBUTE")
    view = views[spec["bufferView"]]
    if not isinstance(view, dict) or view.get("buffer", 0) != 0:
        raise ValueError("UNSUPPORTED_REFERENCE:ATTRIBUTE_BUFFER")
    dtype = {5126: "<f4", 5125: "<u4", 5123: "<u2", 5121: "<u1"}.get(spec["componentType"])
    width = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4}.get(spec["type"])
    if dtype is None or width is None:
        raise ValueError("UNSUPPORTED_REFERENCE:ATTRIBUTE_LAYOUT")
    itemsize = np.dtype(dtype).itemsize
    offset = view.get("byteOffset", 0) + spec.get("byteOffset", 0)
    stride = view.get("byteStride", itemsize * width)
    return np.asarray(
        np.ndarray(
            (spec["count"], width), dtype=dtype, buffer=binary, offset=offset, strides=(stride, itemsize)
        ),
        dtype=float,
    ).copy()


def _wraps(document: dict[str, object], definitions: tuple[object, ...]) -> tuple[bool, bool, bool, bool]:
    textures, samplers = document.get("textures", []), document.get("samplers", [])
    if not isinstance(textures, list) or not isinstance(samplers, list):
        raise TypeError("INVALID_SOURCE_SAMPLERS")
    modes: list[bool] = []
    for definition in definitions:
        sampler: dict[str, object] = {}
        if isinstance(definition, dict):
            texture = textures[definition["index"]]
            if not isinstance(texture, dict) or texture.get("extensions"):
                raise ValueError("UNSUPPORTED_REFERENCE:TEXTURE_EXTENSION")
            if "sampler" in texture:
                sampler = samplers[texture["sampler"]]
        s, t = sampler.get("wrapS", 10497), sampler.get("wrapT", 10497)
        if s != t or s not in (10497, 33071) or sampler.get("magFilter", 9729) != 9729:
            raise ValueError("UNSUPPORTED_REFERENCE:SAMPLER_NEEDS_BILINEAR_REPEAT_OR_CLAMP")
        modes.append(s == 10497)
    return modes[0], modes[1], modes[2], modes[3]


def _reference(snapshot: Snapshot, request: dict[str, object]) -> tuple[MaterialReference, Path]:
    source = snapshot.metadata.get("reference_glb")
    if not isinstance(source, str) or not source:
        raise ValueError("MATERIAL_REFERENCE_REQUIRED")
    path = Path(source)
    declarations = read_source_materials(path)
    document, binary = _source_document(path)
    entries = declarations["materials"]
    if not isinstance(entries, list) or len(entries) != 1:
        raise ValueError("UNSUPPORTED_REFERENCE:MULTIPLE_MATERIALS")
    entry = entries[0]
    assert isinstance(entry, dict)
    definition = entry["source_definition"]
    assert isinstance(definition, dict)
    pbr = definition.get("pbrMetallicRoughness", {})
    assert isinstance(pbr, dict)
    for block in (pbr, definition):
        for key, value in block.items():
            if (
                key.endswith("Texture")
                and isinstance(value, dict)
                and (value.get("texCoord", 0) != 0 or value.get("extensions"))
            ):
                raise ValueError("UNSUPPORTED_REFERENCE:TEXTURE_COORDINATES")
    if definition.get("emissiveTexture") or any(definition.get("emissiveFactor", [0, 0, 0])):
        raise ValueError("UNSUPPORTED_REFERENCE:EMISSION_CHANNEL")
    scene = trimesh.load_scene(path, process=False)
    nodes = list(scene.graph.nodes_geometry)
    if len(nodes) != 1:
        raise ValueError("UNSUPPORTED_REFERENCE:MULTIPLE_GEOMETRY_INSTANCES")
    transform, geometry_name = scene.graph[nodes[0]]
    mesh = scene.geometry[geometry_name]
    if not isinstance(mesh, trimesh.Trimesh):
        raise TypeError("UNSUPPORTED_REFERENCE:NOT_TRIANGLES")
    material = getattr(mesh.visual, "material", None)
    uv = getattr(mesh.visual, "uv", None)
    if not isinstance(material, PBRMaterial) or uv is None:
        raise ValueError("UNSUPPORTED_REFERENCE:PBR_AND_UV_REQUIRED")
    linear = np.asarray(transform[:3, :3], dtype=float)
    gram = linear.T @ linear
    if not np.allclose(gram, np.eye(3) * np.trace(gram) / 3, atol=1e-9) or np.linalg.det(linear) <= 0:
        raise ValueError("UNSUPPORTED_REFERENCE:NONUNIFORM_OR_REFLECTED_TRANSFORM")
    vertices = trimesh.transform_points(np.asarray(mesh.vertices, dtype=float), transform)
    faces = np.asarray(mesh.faces, dtype=np.int64)
    corner_uv = np.asarray(uv, dtype=float)[faces]
    normals = np.asarray(mesh.vertex_normals, dtype=float) @ np.linalg.inv(linear)
    normals /= np.maximum(np.linalg.norm(normals, axis=1, keepdims=True), 1e-30)
    normals = normals[faces]
    meshes = document.get("meshes")
    if not isinstance(meshes, list) or len(meshes) != 1:
        raise ValueError("UNSUPPORTED_REFERENCE:SINGLE_PRIMITIVE_REQUIRED")
    primitives = meshes[0].get("primitives", [])
    if len(primitives) != 1:
        raise ValueError("UNSUPPORTED_REFERENCE:SINGLE_PRIMITIVE_REQUIRED")
    primitive = primitives[0]
    attributes = primitive["attributes"]
    if primitive.get("targets") or "COLOR_0" in attributes or "JOINTS_0" in attributes:
        raise ValueError("UNSUPPORTED_REFERENCE:DEFORMED_OR_VERTEX_COLORED")
    raw_positions = _accessor(document, binary, attributes["POSITION"])
    if raw_positions.shape != mesh.vertices.shape or not np.allclose(raw_positions, mesh.vertices):
        raise ValueError("UNSUPPORTED_REFERENCE:SOURCE_VERTEX_ORDER_CHANGED")
    if "NORMAL" in attributes:
        actual_normals = _accessor(document, binary, attributes["NORMAL"]) @ np.linalg.inv(linear)
        actual_normals /= np.maximum(np.linalg.norm(actual_normals, axis=1, keepdims=True), 1e-30)
        normals = actual_normals[faces]
    else:
        triangle = vertices[faces]
        flat = np.cross(triangle[:, 1] - triangle[:, 0], triangle[:, 2] - triangle[:, 0])
        flat /= np.maximum(np.linalg.norm(flat, axis=1, keepdims=True), 1e-30)
        normals = np.repeat(flat[:, None, :], 3, axis=1)
    uv_gltf = corner_uv.copy()
    uv_gltf[..., 1] = 1 - uv_gltf[..., 1]
    library = request.get("mikk_library")
    if library is not None and not isinstance(library, str):
        raise ValueError("INVALID_MIKK_LIBRARY")
    tangents, signs, hashes = mikktspace_frames(
        vertices[faces], normals, uv_gltf, None if library is None else Path(library)
    )
    custom_tangents = False
    if "TANGENT" in attributes:
        original = _accessor(document, binary, attributes["TANGENT"])
        original_tangents = original[:, :3] @ linear.T
        original_tangents /= np.maximum(np.linalg.norm(original_tangents, axis=1, keepdims=True), 1e-30)
        actual_tangents, actual_signs = original_tangents[faces], original[faces, 3]
        custom_tangents = not (
            np.allclose(tangents, actual_tangents, atol=1e-4) and np.allclose(signs, actual_signs, atol=1e-4)
        )
        tangents, signs = actual_tangents, actual_signs
    factor = np.asarray(pbr.get("baseColorFactor", [1, 1, 1, 1]), dtype=float)
    color = (
        np.asarray(material.baseColorTexture.convert("RGBA"), dtype=np.uint8)
        if material.baseColorTexture is not None
        else np.full((1, 1, 4), 255, dtype=np.uint8)
    )
    normal = (
        np.asarray(material.normalTexture.convert("RGBA"), dtype=np.uint8)
        if material.normalTexture is not None
        else np.asarray([[[128, 128, 255, 255]]], dtype=np.uint8)
    )
    orm = (
        np.asarray(material.metallicRoughnessTexture.convert("RGBA"), dtype=np.uint8)
        if material.metallicRoughnessTexture is not None
        else np.full((1, 1, 4), 255, dtype=np.uint8)
    )
    normal_definition = definition.get("normalTexture", {})
    occlusion_definition = definition.get("occlusionTexture", {})
    assert isinstance(normal_definition, dict) and isinstance(occlusion_definition, dict)
    occlusion = (
        np.asarray(material.occlusionTexture.convert("RGBA"), dtype=np.uint8)
        if material.occlusionTexture is not None
        else None
    )
    if definition.get("occlusionTexture") is not None and occlusion is None:
        raise ValueError("SOURCE_AO_NOT_LOADED")
    reference = _SamplerReference(
        ReferenceSurface(
            vertices,
            faces,
            corner_uv,
            color,
            factor,
            material.normalTexture is not None,
            material.metallicRoughnessTexture is not None,
        ),
        normal,
        orm,
        normals,
        tangents,
        signs,
        float(normal_definition.get("scale", 1)) if material.normalTexture is not None else 0.0,
        float(pbr.get("metallicFactor", 1)),
        float(pbr.get("roughnessFactor", 1)),
        occlusion,
        float(occlusion_definition.get("strength", 1)),
        {
            "source": str(path),
            "source_frame": "declared_glTF_tangents_or_official_MikkTSpace_top_V",
            "source_mikk": hashes,
            "custom_source_tangents": custom_tangents,
            "constant_channels": "Only glTF-declared scalar factors/defaults; no invented source textures",
        },
        corner_uv,
        _wraps(
            document,
            (
                pbr.get("baseColorTexture"),
                definition.get("normalTexture"),
                pbr.get("metallicRoughnessTexture"),
                definition.get("occlusionTexture"),
            ),
        ),
    )
    return reference, path


def _geometry(snapshot: Snapshot, profile: dict[str, object]) -> dict[str, object]:
    from .geometry import geometry_report

    return geometry_report(snapshot.state, profile)


def _save_bake(
    snapshot: Snapshot,
    atlas: MaterialAtlas,
    output: Path,
    source: Path,
    backend: str,
    settings: dict[str, object],
) -> dict[str, object]:
    save_material(atlas, output / "texture")
    np.save(output / "coverage.npy", atlas.coverage, allow_pickle=False)
    files = {
        path.relative_to(output).as_posix(): _hash(path)
        for path in sorted((output / "texture").iterdir())
        if path.is_file()
    }
    files["coverage.npy"] = _hash(output / "coverage.npy")
    result: dict[str, object] = {
        "schema_version": 1,
        "status": "BAKED",
        "quality_accepted": False,
        "state_hash": snapshot.state_hash,
        "asset_id": snapshot.asset_id,
        "snapshot": str(snapshot.root),
        "backend": backend,
        "source_reference": str(source.resolve()),
        "source_sha256": _hash(source),
        "settings": settings,
        "metadata": atlas.metadata,
        "payload_bytes": atlas.payload_bytes,
        "file_bytes": sum((output / name).stat().st_size for name in files),
        "mip_count": len(atlas.base_color),
        "files": files,
        "claims": {"full_mips_written": True, "quality_validated": False, "gpu_verified": False},
    }
    write_json(output / "bake.json", result)
    return {
        "status": "OK",
        "bake": str(output / "bake.json"),
        "bake_hash": _hash(output / "bake.json"),
        "payload_bytes": atlas.payload_bytes,
        "quality_accepted": False,
        "state_hash": snapshot.state_hash,
        "asset_id": snapshot.asset_id,
    }


def _load_bake(snapshot: Snapshot, path: Path) -> tuple[MaterialAtlas, dict[str, object]]:
    record = read_object(path)
    if record.get("state_hash") != snapshot.state_hash or record.get("asset_id") != snapshot.asset_id:
        raise ValueError("STALE_BAKE")
    files = record.get("files")
    if not isinstance(files, dict):
        raise TypeError("INVALID_BAKE_MANIFEST")
    for name, digest in files.items():
        target = (path.parent / str(name)).resolve()
        if not target.is_relative_to(path.parent.resolve()) or _hash(target) != digest:
            raise ValueError("BAKE_ARTIFACT_HASH_MISMATCH")
    source = record.get("source_reference")
    if not isinstance(source, str) or _hash(Path(source)) != record.get("source_sha256"):
        raise ValueError("SOURCE_REFERENCE_CHANGED")
    count = record.get("mip_count")
    if type(count) is not int or count < 1:
        raise ValueError("INVALID_BAKE_MIPS")
    chains = []
    for name in ("BaseColor", "Normal", "ORM"):
        images = []
        for level in range(count):
            filename = f"texture/{name}_mip{level}.png"
            if filename not in files:
                raise ValueError("INCOMPLETE_BAKE_MANIFEST")
            with Image.open(path.parent / filename) as image:
                if image.mode != "RGBA" or image.format != "PNG":
                    raise ValueError("INVALID_BAKE_IMAGE_FORMAT")
                images.append(np.asarray(image, dtype=np.uint8).copy())
        chains.append(tuple(images))
    height, width = chains[0][0].shape[:2]
    dimensions = mip_dimensions(width, height)
    if count != len(dimensions) or any(
        image.shape != (h, w, 4) for chain in chains for image, (w, h) in zip(chain, dimensions, strict=True)
    ):
        raise ValueError("INCOMPLETE_OR_INVALID_MIP_CHAIN")
    metadata = record.get("metadata")
    if "coverage.npy" not in files or not isinstance(metadata, dict):
        raise ValueError("INCOMPLETE_BAKE_MANIFEST")
    coverage = np.load(path.parent / "coverage.npy", allow_pickle=False)
    if coverage.shape != (height, width) or coverage.dtype != np.bool_:
        raise ValueError("INVALID_COVERAGE_MAP")
    return MaterialAtlas(chains[0], chains[1], chains[2], coverage, metadata), record


def _blender_atlas(
    snapshot: Snapshot,
    request: dict[str, object],
    reference: MaterialReference,
    source: Path,
    output: Path,
    deadline: float,
) -> MaterialAtlas:
    if reference.metadata.get("custom_source_tangents") is True:
        raise ValueError("UNSUPPORTED_BLENDER_REFERENCE:CUSTOM_SOURCE_TANGENTS")
    surface = snapshot.state.surface
    resolution = _integer(request, "resolution", 256)
    # All independent regions plus the first sheet of each reuse pair can be
    # baked in one pass. Second sheets form a separate, non-overlapping pass.
    second = {face for group in snapshot.state.groups for face in group.second_faces}
    first_ids = np.asarray([f for f in range(len(surface.mesh.faces)) if f not in second], dtype=np.int64)
    passes = [first_ids]
    if second:
        passes.append(np.asarray(sorted(second), dtype=np.int64))
    sums = np.zeros((resolution, resolution, 10), dtype=float)
    weights = np.zeros((resolution, resolution), dtype=float)
    blender = request.get("blender", str(DEFAULT_BLENDER))
    if not isinstance(blender, str):
        raise TypeError("INVALID_BLENDER_PATH")
    for index, ids in enumerate(passes):
        check_deadline(deadline)
        folder = output / f"blender_pass_{index}"
        bake_variant(
            BakeVariantRequest(
                surface.mesh.vertices,
                surface.mesh.faces[ids],
                surface.uv[surface.uv_faces[ids]],
                source,
                resolution,
                _number(request, "cage_extrusion", reference.diagonal * 0.02),
                _integer(request, "padding_pixels", 4),
            ),
            folder,
            deadline=deadline,
            blender=Path(blender),
        )
        with Image.open(folder / "BaseColor.png") as image:
            colors = decode_rgba8(np.asarray(image.convert("RGBA"), dtype=np.uint8))
        with Image.open(folder / "Normal.png") as image:
            normal = np.asarray(image.convert("RGBA"), dtype=float)[..., :3] / 255 * 2 - 1
        with Image.open(folder / "ORM.png") as image:
            orm = np.asarray(image.convert("RGBA"), dtype=float)[..., :3] / 255
        values = np.concatenate((colors, normal, orm), axis=-1)
        local_weights = np.zeros_like(weights)
        for face_id in ids:
            tri = surface.uv[surface.uv_faces[face_id]]
            x, y, _ = _face_pixels(tri, resolution, resolution)
            if len(x) and np.any(colors[y, x, 3] < 0.99):
                raise ValueError(f"BLENDER_BAKE_EMPTY_TEXEL:face={int(face_id)}")
            area = abs(float(np.linalg.det(np.column_stack((tri[1] - tri[0], tri[2] - tri[0]))))) / 2
            local_weights[y, x] = surface.mesh.areas[face_id] / max(area, 1e-30)
        sums += values * local_weights[..., None]
        weights += local_weights
    valid = weights > 0
    values = sums / np.maximum(weights[..., None], 1e-30)
    values[~valid, 4:7] = [0, 0, 1]
    values[~valid, 7:] = [1, 1, 0]
    if valid.any():
        from scipy.ndimage import distance_transform_edt

        dilation = distance_transform_edt(~valid, return_indices=True)
        if not isinstance(dilation, tuple):
            raise TypeError("PADDING_INDICES_REQUIRED")
        distance, nearest = dilation
        pad = (~valid) & (distance <= _integer(request, "padding_pixels", 4))
        values[pad] = values[nearest[0][pad], nearest[1][pad]]
    atlas = atlas_from_arrays(
        values[..., :4],
        values[..., 4:7],
        values[..., 7:],
        valid,
        {
            "backend": "Blender Cycles",
            "member_composition": "separate nonoverlapping passes; surface-area weights",
            "source_frame": "Blender imported source shading; tangent bake converted to target glTF basis",
        },
    )
    return refresh_material_support(
        surface,
        reference,
        atlas,
        max_projection_fraction=_number(request, "max_projection_fraction", 0.02),
        deadline=deadline,
    )


def bake_material_tool(request: dict[str, object], output: Path, deadline: float) -> dict[str, object]:
    if request.get("mode") == "basecolor_reuse":
        from .color_reuse import bake_color_reuse

        return bake_color_reuse(request, output, deadline)
    if request.get("mode", "pbr") == "basecolor_diagnostic":
        from .color import bake_color_diagnostic

        return bake_color_diagnostic(request, output, deadline)
    if request.get("mode", "pbr") != "pbr":
        raise ValueError("UNSUPPORTED_MATERIAL_MODE")
    check_deadline(deadline)
    snapshot = require_snapshot(request)
    resolution = _integer(request, "resolution", 256)
    if not 8 <= resolution <= 2048:
        raise ValueError("INVALID_BAKE_RESOLUTION")
    safety = _geometry(
        snapshot,
        {"resolution": resolution, "quality_constraints": False, "gap_pixels": 0, "border_pixels": 0},
    )
    if safety.get("valid") is not True:
        return {
            "status": "REJECTED",
            "execution_success": True,
            "reason": "GEOMETRY_UNSAFE",
            "geometry": safety,
        }
    reference, source = _reference(snapshot, request)
    before = _hash(source)
    backend = request.get("backend", "cpu")
    padding = _integer(request, "padding_pixels", 4)
    projection = _number(request, "max_projection_fraction", 0.02)
    if padding < 0 or padding * 2 >= resolution or projection <= 0:
        raise ValueError("INVALID_BAKE_SETTINGS")
    settings: dict[str, object] = {
        "resolution": resolution,
        "padding_pixels": padding,
        "max_projection_fraction": projection,
    }
    if backend == "cpu":
        _, members = owners_and_members(snapshot.state)
        atlas = bake_material(
            snapshot.state.surface,
            reference,
            resolution,
            member_ids=members,
            padding_pixels=padding,
            max_projection_fraction=projection,
            deadline=deadline,
        )
    elif backend == "blender":
        atlas = _blender_atlas(snapshot, request, reference, source, output, deadline)
    else:
        raise ValueError("UNSUPPORTED_BAKE_BACKEND")
    check_deadline(deadline)
    if _hash(source) != before:
        raise ValueError("SOURCE_REFERENCE_CHANGED")
    result = _save_bake(snapshot, atlas, output, source, str(backend), settings)
    check_deadline(deadline)
    return result


def validate_quality(request: dict[str, object], output: Path, deadline: float) -> dict[str, object]:
    candidate_bake = request.get("bake")
    if isinstance(candidate_bake, str) and read_object(Path(candidate_bake)).get("scope") == "basecolor_reuse":
        from .color_reuse import validate_color_reuse

        return validate_color_reuse(request, output, deadline)
    check_deadline(deadline)
    snapshot = require_snapshot(request)
    bake = request.get("bake")
    if not isinstance(bake, str):
        raise TypeError("BAKE_PATH_REQUIRED")
    bake_path = Path(bake)
    bake_digest = _hash(bake_path)
    atlas, baked = _load_bake(snapshot, bake_path)
    raw_limits = request.get("thresholds", {})
    if not isinstance(raw_limits, dict) or set(raw_limits) - set(MaterialThresholds.__dataclass_fields__):
        raise ValueError("INVALID_MATERIAL_THRESHOLDS")
    limits = MaterialThresholds(**{str(k): _number(raw_limits, str(k), 0) for k in raw_limits})
    mips = request.get("mip_levels", [0, 1])
    if not isinstance(mips, list) or not mips or any(type(m) is not int for m in mips):
        raise ValueError("INVALID_MIP_LEVELS")
    reference, source = _reference(snapshot, request)
    profile = request.get("geometry_profile", {})
    if not isinstance(profile, dict):
        raise TypeError("INVALID_GEOMETRY_PROFILE")
    profile = {**profile, "resolution": atlas.base_color[0].shape[0], "quality_constraints": True}
    geometry = _geometry(snapshot, profile)
    projection = _number(request, "max_projection_fraction", 0.02)
    important = request.get("important_faces", [])
    if not isinstance(important, list) or any(
        type(face) is not int or not 0 <= face < len(snapshot.state.surface.mesh.faces) for face in important
    ):
        raise ValueError("INVALID_IMPORTANT_FACES")
    material = evaluate_material(
        snapshot.state.surface,
        reference,
        atlas,
        mip_levels=tuple(mips),
        thresholds=limits,
        max_projection_fraction=projection,
        raster_checks=True,
        deadline=deadline,
        important_faces=tuple(important),
    )
    render = prepare_render_reference(
        snapshot.state.surface.mesh,
        reference,
        size=_integer(request, "render_size", 48),
        max_projection_fraction=projection,
        deadline=deadline,
    )
    rendering = evaluate_render(
        snapshot.state.surface,
        atlas,
        render,
        output / "render",
        mip_levels=tuple(mips),
        mean_limit=limits.render_mean,
        p95_limit=limits.render_p95,
        max_limit=limits.render_max,
        deadline=deadline,
    )
    check_deadline(deadline)
    # Re-read every input after the independent checks, including decoded maps.
    _load_bake(snapshot, bake_path)
    require_snapshot(request)
    if _hash(bake_path) != bake_digest or _hash(source) != baked["source_sha256"]:
        raise ValueError("VALIDATION_INPUT_CHANGED")
    valid = all(item.get("valid") is True for item in (geometry, material, rendering))
    result: dict[str, object] = {
        "schema_version": 1,
        "valid": valid,
        "status": "VALIDATED" if valid else "QUALITY_REJECTED",
        "state_hash": snapshot.state_hash,
        "asset_id": snapshot.asset_id,
        "bake": str(bake_path.resolve()),
        "bake_sha256": bake_digest,
        "geometry": geometry,
        "material": material,
        "render": rendering,
        "profile": {
            "thresholds": asdict(limits),
            "geometry": {key: value for key, value in profile.items() if key != "resolution"},
            "mip_levels": mips,
            "render_size": render.size,
            "max_projection_fraction": projection,
            "important_faces": important,
        },
        "payload_bytes": atlas.payload_bytes,
        "gpu_verified": False,
        "artist_reviewed": False,
        "scope": "finite CPU probes, actual texel centers and fixed-view rendering; not continuous bounds",
    }
    result["profile_hash"] = object_hash(result["profile"])
    write_json(output / "validation.json", result)
    check_deadline(deadline)
    return {
        "status": "OK" if valid else "REJECTED",
        "execution_success": True,
        "quality_accepted": valid,
        "validation": str(output / "validation.json"),
        "validation_hash": _hash(output / "validation.json"),
        "payload_bytes": atlas.payload_bytes,
        "geometry_valid": geometry.get("valid"),
        "material_valid": material.get("valid"),
        "render_valid": rendering.get("valid"),
    }


def export_asset(request: dict[str, object], output: Path, deadline: float) -> dict[str, object]:
    candidate_bake = request.get("bake")
    if isinstance(candidate_bake, str) and read_object(Path(candidate_bake)).get("scope") == "basecolor_reuse":
        from .color_reuse import export_color_reuse

        return export_color_reuse(request, output, deadline)
    check_deadline(deadline)
    snapshot = require_snapshot(request)
    bake = request.get("bake")
    if not isinstance(bake, str):
        raise TypeError("BAKE_PATH_REQUIRED")
    path = Path(bake)
    atlas, record = _load_bake(snapshot, path)
    mode = request.get("mode", "accepted")
    if mode not in ("accepted", "diagnostic"):
        raise ValueError("INVALID_EXPORT_MODE")
    evidence: dict[str, object] | None = None
    validation = request.get("validation")
    if isinstance(validation, str):
        evidence = read_object(Path(validation))
        if (
            evidence.get("state_hash") != snapshot.state_hash
            or evidence.get("asset_id") != snapshot.asset_id
            or evidence.get("bake_sha256") != _hash(path)
        ):
            raise ValueError("STALE_VALIDATION")
    if mode == "accepted" and not _accepted_evidence(evidence):
        return {
            "status": "REJECTED",
            "execution_success": True,
            "reason": "ACCEPTED_EXPORT_REQUIRES_VALIDATION",
        }
    comparison: dict[str, object] = {"comparable": False, "status": "BASELINE_NOT_PROVIDED"}
    baseline_path, baseline_validation = request.get("baseline_bake"), request.get("baseline_validation")
    if isinstance(baseline_path, str) and isinstance(baseline_validation, str):
        baseline_record = read_object(Path(baseline_path))
        baseline_snapshot_path = baseline_record.get("snapshot")
        if not isinstance(baseline_snapshot_path, str):
            raise TypeError("BASELINE_SNAPSHOT_REQUIRED")
        baseline_snapshot = load_snapshot(Path(baseline_snapshot_path))
        baseline_atlas, baseline_record = _load_bake(baseline_snapshot, Path(baseline_path))
        baseline_gate = read_object(Path(baseline_validation))
        if (
            baseline_snapshot.asset_id != snapshot.asset_id
            or baseline_record.get("source_sha256") != record.get("source_sha256")
            or not np.array_equal(
                baseline_snapshot.state.surface.mesh.vertices, snapshot.state.surface.mesh.vertices
            )
            or not np.array_equal(
                baseline_snapshot.state.surface.mesh.faces, snapshot.state.surface.mesh.faces
            )
            or baseline_gate.get("state_hash") != baseline_snapshot.state_hash
            or baseline_gate.get("bake_sha256") != _hash(Path(baseline_path))
        ):
            raise ValueError("BASELINE_IDENTITY_OR_VALIDATION_MISMATCH")
        comparable = (
            _accepted_evidence(evidence)
            and _accepted_evidence(baseline_gate)
            and evidence is not None
            and baseline_gate.get("profile") == evidence.get("profile")
        )
        comparison = {
            "comparable": comparable,
            "status": "MEASURED_SAME_PROFILE" if comparable else "NOT_COMPARABLE",
            "baseline_bake": baseline_path,
            "baseline_validation": baseline_validation,
            "baseline_payload_bytes": baseline_atlas.payload_bytes,
            "candidate_payload_bytes": atlas.payload_bytes,
            "saved_payload_bytes": baseline_atlas.payload_bytes - atlas.payload_bytes if comparable else None,
        }
    mesh_info = export_material_glb(snapshot.state.surface, atlas, output / "asset.glb")
    shutil.copytree(path.parent / "texture", output / "texture")
    (output / "snapshot").mkdir()
    for name in ("snapshot.json", "arrays.npz"):
        shutil.copy2(snapshot.root / name, output / "snapshot" / name)
    shutil.copy2(path.parent / "coverage.npy", output / "coverage.npy")
    write_json(output / "bake.json", record)
    if evidence is not None:
        write_json(output / "validation.json", evidence)
    check_deadline(deadline)
    _load_bake(snapshot, output / "bake.json")
    require_snapshot(request)
    validation_profile = evidence.get("profile") if evidence is not None else None
    validated_lods = validation_profile.get("mip_levels") if isinstance(validation_profile, dict) else "未验收"
    report = [
        "# ReSeam 材质导出",
        "",
        f"- 导出模式：`{mode}`；质量已通过：`{mode == 'accepted'}`。`[verified: export.json mode/quality_accepted]`",
        f"- 验收 LOD：`{validated_lods}`；完整 mip 写出与验收范围分别判定。`[verified: validation.json profile.mip_levels，如提供]`",
        (
            f"- 完整 RGBA8 mip 纹理载荷：**{atlas.payload_bytes} 字节**；压缩包/PNG 文件大小另计。"
            "`[verified: bake.json payload_bytes/file_bytes]`"
        ),
        (
            f"- 状态：`{snapshot.state_hash}`；材质证据：`{_hash(path)}`。"
            "`[verified: export.json state_hash/bake_sha256]`"
        ),
        (
            "- GLB 是 LOD0 预览；完整 mip PNG 链随包交付，目标引擎需显式导入。"
            "`[verified: asset.glb extras.reseam.sampler；texture/*_mip*.png]`"
        ),
        (
            "- 质量范围是有限 CPU 采样、逐面纹素中心与固定视角；GPU 和艺术验收均未执行。"
            "`[verified: export.json gpu_verified/artist_reviewed；validation.json scope，如提供]`"
        ),
        "",
        "## 实际生成的贴图",
        "",
        "![BaseColor](texture/BaseColor_mip0.png)",
        "",
        "![Normal](texture/Normal_mip0.png)",
        "",
        "![ORM](texture/ORM_mip0.png)",
        "",
        "## 同质量对照",
        "",
        "```json",
        json.dumps(comparison, ensure_ascii=False, indent=2),
        "```",
        "",
        "没有通过同一质量协议的基线时，不据此宣称存储收益。`[opinion]`",
        "",
    ]
    (output / "report.md").write_text("\n".join(report), encoding="utf-8")
    deliverables = [output / name for name in ("asset.glb", "coverage.npy", "bake.json", "report.md")]
    deliverables += sorted((output / "texture").iterdir())
    deliverables += sorted((output / "snapshot").iterdir())
    if evidence is not None:
        deliverables.append(output / "validation.json")
    artifacts = {p.relative_to(output).as_posix(): _hash(p) for p in deliverables if p.is_file()}
    manifest = {
        "status": "EXPORTED",
        "mode": mode,
        "quality_accepted": mode == "accepted",
        "state_hash": snapshot.state_hash,
        "asset_id": snapshot.asset_id,
        "source_snapshot": str(snapshot.root),
        "bake_sha256": _hash(path),
        "payload_bytes": atlas.payload_bytes,
        "mesh": mesh_info,
        "artifacts": artifacts,
        "comparison": comparison,
        "gpu_verified": False,
        "artist_reviewed": False,
    }
    write_json(output / "export.json", manifest)
    check_deadline(deadline)
    return {
        "status": "OK",
        "execution_success": True,
        "asset": str(output / "asset.glb"),
        "manifest": str(output / "export.json"),
        "report": str(output / "report.md"),
        "quality_accepted": mode == "accepted",
        "payload_bytes": atlas.payload_bytes,
    }


def _accepted_evidence(evidence: dict[str, object] | None) -> bool:
    """A status flag alone cannot substitute for the independent gate record."""
    if (
        evidence is None
        or evidence.get("valid") is not True
        or evidence.get("status") != "VALIDATED"
        or evidence.get("schema_version") != 1
        or not isinstance(evidence.get("profile"), dict)
        or evidence.get("profile_hash") != object_hash(evidence["profile"])
    ):
        return False
    for name in ("geometry", "material", "render"):
        gate = evidence.get(name)
        if not isinstance(gate, dict) or gate.get("valid") is not True:
            return False
    return True
