"""A reproducible CPU glTF material contract for the budget solver.

Source normals use official MikkTSpace when the input omits tangents. Internal
UV has bottom V=0; glTF UV and the corresponding tangent basis have top V=0.
The target has explicit flat corner normals and tangent/sign attributes. This
is a finite-sample CPU acceptance contract, not a GPU or artist certification.
"""

import ctypes
import hashlib
import io
import json
import os
import struct
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, TypeAlias

import numpy as np
import trimesh
from PIL import Image
from scipy.ndimage import distance_transform_edt
from trimesh.visual.material import PBRMaterial

from .budget_texture import (
    BoolArray,
    ReferenceSurface,
    Uint8Array,
    _face_pixels,
    build_rgba8_mips,
    encode_rgba8,
    mip_dimensions,
    sample_rgba8,
)
from .mesh import chart_labels
from .types import FloatArray, IntArray, MeshState

if TYPE_CHECKING:
    _FloatPointer: TypeAlias = ctypes._Pointer[ctypes.c_float]
else:
    _FloatPointer = ctypes.POINTER(ctypes.c_float)
_MIKK_SOURCE_SHA256 = "de87e74107df766ce68108801262bd8d53899414236b59810509a8fc2a51e288"
_MIKK_HEADER_SHA256 = "17fc433894f24c73753d548086cc4d8c5c0379f4a6edfb98b5da243e4f0bc3d0"


def _check_deadline(deadline: float | None) -> None:
    if deadline is not None and time.monotonic() >= deadline:
        raise TimeoutError("MATERIAL_DEADLINE")


def _support_fingerprint(state: MeshState) -> str:
    """Bind raster/projection evidence to complete face and UV-copy identity."""
    digest = hashlib.sha256()
    for array in (state.mesh.vertices, state.mesh.faces, state.uv, state.uv_faces):
        digest.update(str((array.shape, array.dtype.str)).encode("ascii"))
        digest.update(array.tobytes())
    digest.update(np.asarray(sorted(state.seams), dtype=np.int64).tobytes())
    return digest.hexdigest()


def _unit(values: FloatArray) -> FloatArray:
    return values / np.maximum(np.linalg.norm(values, axis=-1, keepdims=True), 1e-30)


def _linear_rgba8(values: FloatArray) -> Uint8Array:
    if not np.isfinite(values).all():
        raise ValueError("Nonfinite material texels")
    return np.asarray(np.rint(np.clip(values, 0, 1) * 255), dtype=np.uint8)


def sample_linear_rgba8(image: Uint8Array, uv: FloatArray, *, repeat: bool = False) -> FloatArray:
    """Linear-data counterpart of the explicit texel-center color sampler."""
    if image.ndim != 3 or image.shape[2] != 4 or image.dtype != np.uint8:
        raise ValueError("RGBA8 material image required")
    if uv.ndim != 2 or uv.shape[1] != 2 or not np.isfinite(uv).all():
        raise ValueError("Finite (n,2) UV required")
    height, width = image.shape[:2]
    px, py = uv[:, 0] * width - 0.5, (1 - uv[:, 1]) * height - 0.5
    x, y = np.floor(px).astype(np.int64), np.floor(py).astype(np.int64)
    a, b = (px - x)[:, None], (py - y)[:, None]
    if repeat:
        x0, x1, y0, y1 = x % width, (x + 1) % width, y % height, (y + 1) % height
    else:
        x0, x1 = np.clip(x, 0, width - 1), np.clip(x + 1, 0, width - 1)
        y0, y1 = np.clip(y, 0, height - 1), np.clip(y + 1, 0, height - 1)
    return np.asarray(
        ((1 - a) * (1 - b) * image[y0, x0] + a * (1 - b) * image[y0, x1]
         + (1 - a) * b * image[y1, x0] + a * b * image[y1, x1]) / 255,
        dtype=np.float64,
    )


def frame_arrays(state: MeshState) -> tuple[FloatArray, FloatArray, FloatArray]:
    """Flat explicit target corner normals, tangents and signs, in glTF UV basis."""
    xyz = state.mesh.vertices[state.mesh.faces]
    uv = state.uv[state.uv_faces].copy()
    uv[..., 1] = 1 - uv[..., 1]
    p, q = xyz[:, 1] - xyz[:, 0], xyz[:, 2] - xyz[:, 0]
    a, b = uv[:, 1] - uv[:, 0], uv[:, 2] - uv[:, 0]
    det = a[:, 0] * b[:, 1] - a[:, 1] * b[:, 0]
    if np.any(np.abs(det) <= 1e-20):
        raise ValueError("FRAME_INVALID: degenerate target UV")
    normal = _unit(np.cross(p, q))
    tangent = (p * b[:, 1, None] - q * a[:, 1, None]) / det[:, None]
    tangent = _unit(tangent - np.sum(tangent * normal, axis=1)[:, None] * normal)
    bitangent = (-p * b[:, 0, None] + q * a[:, 0, None]) / det[:, None]
    signs = np.sign(np.sum(np.cross(normal, tangent) * bitangent, axis=1))
    if np.any(signs == 0) or np.any(np.linalg.norm(tangent, axis=1) < 0.99):
        raise ValueError("FRAME_INVALID: zero target tangent or sign")
    return (
        np.repeat(normal[:, None], 3, axis=1),
        np.repeat(tangent[:, None], 3, axis=1),
        np.repeat(signs[:, None], 3, axis=1),
    )


def interpolated_frames(
    normals: FloatArray, tangents: FloatArray, signs: FloatArray,
    face_ids: IntArray, barycentric: FloatArray,
) -> tuple[FloatArray, BoolArray]:
    n = np.einsum("nk,nkj->nj", barycentric, normals[face_ids])
    t = np.einsum("nk,nkj->nj", barycentric, tangents[face_ids])
    sign = np.einsum("nk,nk->n", barycentric, signs[face_ids])
    nl = np.linalg.norm(n, axis=1)
    n = _unit(n)
    t -= np.sum(t * n, axis=1)[:, None] * n
    tl = np.linalg.norm(t, axis=1)
    t = _unit(t)
    bitangent = np.cross(n, t) * np.sign(sign)[:, None]
    valid = (nl > 1e-10) & (tl > 1e-10) & (np.abs(sign) > 0.99)
    return np.stack((t, bitangent, n), axis=-1), valid


def mikktspace_frames(
    positions: FloatArray, normals: FloatArray, uv_gltf: FloatArray,
    library_path: Path | None = None,
) -> tuple[FloatArray, FloatArray, dict[str, object]]:
    """Call the unmodified upstream MikkTSpace generator with corner callbacks.

    No native code is downloaded or built implicitly. The locked official
    mikktspace.c/.h and a compiled library must already be present together.
    """
    if positions.shape != normals.shape or uv_gltf.shape != positions.shape[:2] + (2,):
        raise ValueError("Mikk corner array shape mismatch")
    if library_path is None:
        configured = os.environ.get("RESEAM_MIKKTSPACE_LIBRARY")
        default = Path(__file__).resolve().parents[2] / "artifacts/runtime/mikktspace"
        library_path = Path(configured) if configured else default / (
            "mikktspace.dll" if os.name == "nt" else "libmikktspace.so"
        )
    hashes: dict[str, object] = {}
    for filename, expected in (("mikktspace.c", _MIKK_SOURCE_SHA256), ("mikktspace.h", _MIKK_HEADER_SHA256)):
        path = library_path.parent / filename
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual != expected:
            raise ValueError(f"MIKKTSPACE_SOURCE_HASH_MISMATCH:{filename}")
        hashes[filename] = actual
    hashes["library_sha256"] = hashlib.sha256(library_path.read_bytes()).hexdigest()
    hashes["source_url"] = "https://github.com/mmikk/MikkTSpace"
    lib = ctypes.CDLL(str(library_path))
    get_count_type = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_void_p)
    get_vertices_type = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_int)
    get_vector_type = ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.POINTER(ctypes.c_float),
                                     ctypes.c_int, ctypes.c_int)
    set_tangent_type = ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.POINTER(ctypes.c_float),
                                      ctypes.c_float, ctypes.c_int, ctypes.c_int)

    class Interface(ctypes.Structure):
        _fields_ = [
            ("get_faces", get_count_type), ("get_vertices", get_vertices_type),
            ("get_position", get_vector_type), ("get_normal", get_vector_type),
            ("get_uv", get_vector_type), ("set_basic", set_tangent_type),
            ("set_full", ctypes.c_void_p),
        ]

    class Context(ctypes.Structure):
        _fields_ = [("interface", ctypes.POINTER(Interface)), ("user", ctypes.c_void_p)]

    tangents = np.zeros_like(positions)
    signs = np.zeros(positions.shape[:2])

    def get_faces(context: object) -> int:
        return len(positions)

    def get_vertices(context: object, face: int) -> int:
        return 3

    def get_position(context: object, output: _FloatPointer, face: int, corner: int) -> None:
        for axis in range(3):
            output[axis] = float(positions[face, corner, axis])

    def get_normal(context: object, output: _FloatPointer, face: int, corner: int) -> None:
        for axis in range(3):
            output[axis] = float(normals[face, corner, axis])

    def get_uv(context: object, output: _FloatPointer, face: int, corner: int) -> None:
        for axis in range(2):
            output[axis] = float(uv_gltf[face, corner, axis])

    def set_basic(context: object, vector: _FloatPointer, sign: float, face: int, corner: int) -> None:
        tangents[face, corner] = [vector[axis] for axis in range(3)]
        signs[face, corner] = sign

    interface = Interface(get_count_type(get_faces), get_vertices_type(get_vertices),
                          get_vector_type(get_position), get_vector_type(get_normal),
                          get_vector_type(get_uv), set_tangent_type(set_basic), None)
    context = Context(ctypes.pointer(interface), None)
    lib.genTangSpaceDefault.argtypes = [ctypes.POINTER(Context)]
    lib.genTangSpaceDefault.restype = ctypes.c_int
    if lib.genTangSpaceDefault(ctypes.byref(context)) != 1:
        raise ValueError("MIKKTSPACE_INITIALIZATION_FAILED")
    if np.any(np.linalg.norm(tangents, axis=2) < 0.9) or np.any(np.abs(signs) != 1):
        raise ValueError("MIKKTSPACE_INVALID_FRAMES")
    return tangents, signs, hashes


@dataclass(frozen=True)
class MaterialSamples:
    base_color: FloatArray
    normal_world: FloatArray
    orm: FloatArray
    valid: BoolArray
    distances: FloatArray
    target_faces: IntArray
    barycentric: FloatArray

    @property
    def rgba_linear(self) -> FloatArray:
        return self.base_color


@dataclass
class MaterialReference:
    geometry: ReferenceSurface
    normal_image: Uint8Array
    orm_image: Uint8Array
    normals: FloatArray
    tangents: FloatArray
    signs: FloatArray
    normal_scale: float = 1.0
    metallic_factor: float = 1.0
    roughness_factor: float = 1.0
    occlusion_image: Uint8Array | None = None
    occlusion_strength: float = 1.0
    metadata: dict[str, object] = field(default_factory=dict)
    full_corner_uv: FloatArray | None = None

    def __post_init__(self) -> None:
        if not np.isfinite([self.normal_scale, self.metallic_factor, self.roughness_factor,
                            self.occlusion_strength]).all():
            raise ValueError("Nonfinite material factors")
        if (self.normal_scale < 0 or not 0 <= self.metallic_factor <= 1
                or not 0 <= self.roughness_factor <= 1 or not 0 <= self.occlusion_strength <= 1):
            raise ValueError("Material factors outside glTF bounds")
        if self.normals.shape != self.tangents.shape or self.normals.shape[1:] != (3, 3):
            raise ValueError("Source corner frame shape mismatch")
        if self.signs.shape != self.normals.shape[:2]:
            raise ValueError("Source corner sign shape mismatch")
        for image in (self.normal_image, self.orm_image, self.occlusion_image):
            if image is not None and (image.ndim != 3 or image.shape[2] != 4 or image.dtype != np.uint8):
                raise ValueError("Source material images must be RGBA8")

    @property
    def diagonal(self) -> float:
        return self.geometry.diagonal

    def restrict_bounds(self, bounds: FloatArray, max_distance: float) -> "MaterialReference":
        return MaterialReference(
            self.geometry.restrict_bounds(bounds, max_distance), self.normal_image, self.orm_image,
            self.normals, self.tangents, self.signs, self.normal_scale, self.metallic_factor,
            self.roughness_factor, self.occlusion_image, self.occlusion_strength, self.metadata,
            self.full_corner_uv,
        )

    def sample(
        self, points: FloatArray, max_distance: float, *, deadline: float | None = None,
    ) -> MaterialSamples:
        samples = self.geometry.sample(points, max_distance, deadline=deadline)
        ids, bary = samples.target_faces, samples.barycentric
        # original target IDs deliberately index the unsliced frame arrays.
        uv = np.einsum("nk,nkj->nj", bary, self.metadata_corner_uv(ids))
        encoded = sample_linear_rgba8(self.normal_image, uv, repeat=True)
        normal = encoded[:, :3] * 2 - 1
        normal[:, :2] *= self.normal_scale
        lengths = np.linalg.norm(normal, axis=1)
        matrix, frame_valid = interpolated_frames(self.normals, self.tangents, self.signs, ids, bary)
        world = _unit(np.einsum("nij,nj->ni", matrix, _unit(normal)))
        mr = sample_linear_rgba8(self.orm_image, uv, repeat=True)
        orm = np.column_stack((np.ones(len(points)), mr[:, 1] * self.roughness_factor,
                               mr[:, 2] * self.metallic_factor))
        if self.occlusion_image is not None:
            occlusion = sample_linear_rgba8(self.occlusion_image, uv, repeat=True)[:, 0]
            orm[:, 0] = 1 + self.occlusion_strength * (occlusion - 1)
        valid = samples.valid & frame_valid & (lengths > 1e-10) & np.isfinite(world).all(axis=1)
        return MaterialSamples(samples.rgba_linear, world, orm, valid, samples.distances, ids, bary)

    def metadata_corner_uv(self, ids: IntArray) -> FloatArray:
        # Geometry restrictions preserve original IDs; frames and this table
        # deliberately remain indexed by those full-source face IDs.
        if self.full_corner_uv is not None:
            return self.full_corner_uv[ids]
        assert self.geometry.original_face_ids is not None
        if np.array_equal(self.geometry.original_face_ids, np.arange(len(self.geometry.faces))):
            return self.geometry.corner_uv[ids]
        lookup = {int(original_id): i for i, original_id in enumerate(self.geometry.original_face_ids)}
        return self.geometry.corner_uv[[lookup[int(i)] for i in ids]]


def _glb_document(path: Path) -> dict[str, object]:
    raw = path.read_bytes()
    if raw[:4] != b"glTF" or struct.unpack_from("<I", raw, 4)[0] != 2:
        raise ValueError("Material source must be GLB2")
    size = struct.unpack_from("<I", raw, 12)[0]
    document = json.loads(raw[20:20 + size])
    if not isinstance(document, dict):
        raise TypeError("Invalid glTF document")
    return document


def load_material_reference(path: Path, *, mikk_library: Path | None = None) -> MaterialReference:
    """Load a single fixed PBR source, preserving real smooth source normals."""
    scene = trimesh.load_scene(path, process=False)
    nodes = list(scene.graph.nodes_geometry)
    if len(nodes) != 1:
        raise ValueError("UNSUPPORTED_REFERENCE: exactly one geometry instance required")
    transform, name = scene.graph[nodes[0]]
    mesh = scene.geometry[name]
    if not isinstance(mesh, trimesh.Trimesh):
        raise TypeError("Triangle source required")
    material = getattr(mesh.visual, "material", None)
    uv = getattr(mesh.visual, "uv", None)
    if not isinstance(material, PBRMaterial) or uv is None or material.baseColorTexture is None:
        raise ValueError("PBR texture source with TEXCOORD_0 required")
    if material.normalTexture is None or material.metallicRoughnessTexture is None:
        raise ValueError("SOURCE_CHANNEL_MISSING: Normal and MetallicRoughness required")
    document = _glb_document(path)
    # json.loads returns dynamic values: this is an external file-format boundary.
    definitions = document.get("materials")
    if not isinstance(definitions, list) or len(definitions) != 1 or not isinstance(definitions[0], dict):
        raise ValueError("Exactly one material definition required")
    definition = definitions[0]
    for item in (definition.get("normalTexture", {}), definition.get("occlusionTexture", {})):
        if not isinstance(item, dict) or item.get("texCoord", 0) != 0 or item.get("extensions"):
            raise ValueError("UNSUPPORTED_REFERENCE: nonzero/extended texture coordinates")
    normal_info = definition.get("normalTexture", {})
    occlusion_info = definition.get("occlusionTexture", {})
    if not isinstance(normal_info, dict) or not isinstance(occlusion_info, dict):
        raise TypeError("Invalid source normal/occlusion texture info")
    normal_scale = float(normal_info.get("scale", 1))
    occlusion_strength = float(occlusion_info.get("strength", 1))
    linear_transform = np.asarray(transform[:3, :3], dtype=float)
    # Fixed sources are identity transforms. Similarities are exact here;
    # nonuniform scale would require transforming locally generated tangents.
    gram = linear_transform.T @ linear_transform
    if not np.allclose(gram, np.eye(3) * np.trace(gram) / 3, atol=1e-9) or np.linalg.det(gram) <= 0:
        raise ValueError("UNSUPPORTED_REFERENCE: nonuniform/degenerate source transform")
    vertices = trimesh.transform_points(np.asarray(mesh.vertices, dtype=float), transform)
    faces = np.asarray(mesh.faces, dtype=np.int64)
    corner_uv = np.asarray(uv, dtype=float)[faces]
    vertex_normals = _unit(np.asarray(mesh.vertex_normals, dtype=float) @ np.linalg.inv(linear_transform))
    normals = vertex_normals[faces]
    uv_gltf = corner_uv.copy()
    uv_gltf[..., 1] = 1 - uv_gltf[..., 1]
    tangents, signs, hashes = mikktspace_frames(vertices[faces], normals, uv_gltf, mikk_library)
    factor = np.ones(4)
    if material.baseColorFactor is not None:
        raw_factor = np.asarray(material.baseColorFactor)
        factor = raw_factor.astype(float) / (255 if np.issubdtype(raw_factor.dtype, np.integer) else 1)
    geometry = ReferenceSurface(vertices, faces, corner_uv,
                                np.asarray(material.baseColorTexture.convert("RGBA"), dtype=np.uint8),
                                factor, True, True)
    return MaterialReference(
        geometry, np.asarray(material.normalTexture.convert("RGBA"), dtype=np.uint8),
        np.asarray(material.metallicRoughnessTexture.convert("RGBA"), dtype=np.uint8),
        normals, tangents, signs, normal_scale,
        float(material.metallicFactor if material.metallicFactor is not None else 1),
        float(material.roughnessFactor if material.roughnessFactor is not None else 1),
        (np.asarray(material.occlusionTexture.convert("RGBA"), dtype=np.uint8)
         if material.occlusionTexture is not None else None),
        occlusion_strength,
        {"source": str(path), "source_frame": "official_MikkTSpace_glTF_top_V",
         "source_mikk": hashes,
         "source_sampling": "bilinear_repeat_mip0; RGB decoded sRGB before filter"},
        corner_uv,
    )


@dataclass(frozen=True)
class MaterialAtlas:
    base_color: tuple[Uint8Array, ...]
    normal: tuple[Uint8Array, ...]
    orm: tuple[Uint8Array, ...]
    coverage: BoolArray
    metadata: dict[str, object]

    @property
    def levels(self) -> tuple[Uint8Array, ...]:
        return self.base_color

    @property
    def payload_bytes(self) -> int:
        return sum(image.nbytes for chain in (self.base_color, self.normal, self.orm) for image in chain)


def _data_mips(base: Uint8Array, normal: bool = False) -> tuple[Uint8Array, ...]:
    levels = [base.copy()]
    for width, height in mip_dimensions(base.shape[1], base.shape[0])[1:]:
        previous = np.asarray(levels[-1], dtype=float) / 255
        channels = [np.asarray(Image.fromarray(previous[..., c].astype(np.float32)).resize(
            (width, height), resample=Image.Resampling.BOX), dtype=float) for c in range(4)]
        reduced = np.stack(channels, axis=-1)
        if normal:
            reduced[..., :3] = 0.5 + 0.5 * _unit(reduced[..., :3] * 2 - 1)
        levels.append(_linear_rgba8(reduced))
    return tuple(levels)


def atlas_from_arrays(
    base_color_linear: FloatArray, normal_tangent: FloatArray, orm: FloatArray,
    coverage: BoolArray | None = None, metadata: dict[str, object] | None = None,
) -> MaterialAtlas:
    """Quantize all material channels and generate the actual complete mip chain."""
    shape = base_color_linear.shape[:2]
    if (base_color_linear.shape != shape + (4,) or normal_tangent.shape != shape + (3,)
            or orm.shape != shape + (3,) or not np.isfinite(normal_tangent).all()):
        raise ValueError("Material array shape/finite mismatch")
    cover = np.ones(shape, dtype=bool) if coverage is None else np.asarray(coverage, dtype=bool)
    if cover.shape != shape:
        raise ValueError("Material coverage shape mismatch")
    alpha = np.ones(shape + (1,))
    normal = np.concatenate((0.5 + 0.5 * _unit(normal_tangent), alpha), axis=-1)
    orm_rgba = np.concatenate((orm, alpha), axis=-1)
    record = dict(metadata or {})
    record.update({"codec": "RGBA8", "channels": ["BaseColor_sRGB", "Normal_linear", "ORM_linear"],
                   "mip_policy": "complete floor-halved BOX; normal renormalized at each mip",
                   "target_frame": "flat NORMAL and glTF UV-derivative TANGENT explicitly exported",
                   "sampling": "CPU bilinear/clamp; V=0 internal bottom; independent requested integer LOD",
                   "gpu_validated": False, "artist_reviewed": False})
    atlas = MaterialAtlas(build_rgba8_mips(encode_rgba8(base_color_linear)),
                          _data_mips(_linear_rgba8(normal), normal=True),
                          _data_mips(_linear_rgba8(orm_rgba)), cover.copy(), record)
    record["payload_bytes"] = atlas.payload_bytes
    return atlas


def save_material(atlas: MaterialAtlas, output: Path) -> dict[str, int]:
    output.mkdir(parents=True, exist_ok=False)
    sizes: dict[str, int] = {}
    for name, chain in (("BaseColor", atlas.base_color), ("Normal", atlas.normal), ("ORM", atlas.orm)):
        for level, image in enumerate(chain):
            path = output / f"{name}_mip{level}.png"
            Image.fromarray(image).save(path)
            sizes[path.name] = path.stat().st_size
    (output / "material_metadata.json").write_text(
        json.dumps(atlas.metadata, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    return sizes


def export_material_glb(state: MeshState, atlas: MaterialAtlas, path: Path) -> dict[str, object]:
    """Export the exact flat NORMAL/TANGENT frame and all actual PBR channels.

    glTF core embeds base PNGs, not author-provided mip chains. This preview
    deliberately has a LOD0 sampler; save_material exports the separately
    validated mip chain for a target importer rather than claiming that glTF
    viewers necessarily reproduce the CPU mip generator.
    """
    if path.exists():
        raise FileExistsError(path)
    normals, tangents, signs = frame_arrays(state)
    tangent4 = np.concatenate((tangents, signs[..., None]), axis=-1)
    uv = state.uv[state.uv_faces].copy()
    uv[..., 1] = 1 - uv[..., 1]
    xyz = state.mesh.vertices[state.mesh.faces]
    buffers = bytearray()
    views: list[dict[str, object]] = []
    accessors: list[dict[str, object]] = []

    def append_bytes(raw: bytes, target: int | None = None) -> int:
        buffers.extend(b"\0" * (-len(buffers) % 4))
        view: dict[str, object] = {"buffer": 0, "byteOffset": len(buffers), "byteLength": len(raw)}
        if target is not None:
            view["target"] = target
        views.append(view)
        buffers.extend(raw)
        return len(views) - 1

    def append_attribute(array: FloatArray, width: int, minimum: bool = False) -> int:
        flattened = np.asarray(array.reshape(-1, width), dtype="<f4")
        view_id = append_bytes(flattened.tobytes(), 34962)
        accessor: dict[str, object] = {"bufferView": view_id, "componentType": 5126,
                                       "count": len(flattened), "type": f"VEC{width}"}
        if minimum:
            accessor["min"] = flattened.min(axis=0).tolist()
            accessor["max"] = flattened.max(axis=0).tolist()
        accessors.append(accessor)
        return len(accessors) - 1

    attributes = {"POSITION": append_attribute(xyz, 3, minimum=True),
                  "NORMAL": append_attribute(normals, 3),
                  "TANGENT": append_attribute(tangent4, 4),
                  "TEXCOORD_0": append_attribute(uv, 2)}
    indices = np.arange(3 * len(state.mesh.faces), dtype="<u4")
    index_view = append_bytes(indices.tobytes(), 34963)
    accessors.append({"bufferView": index_view, "componentType": 5125, "count": len(indices),
                       "type": "SCALAR", "min": [0], "max": [len(indices) - 1]})
    images: list[dict[str, object]] = []
    for name, image in (("BaseColor", atlas.base_color[0]), ("Normal", atlas.normal[0]), ("ORM", atlas.orm[0])):
        encoded = io.BytesIO()
        Image.fromarray(image).save(encoded, format="PNG")
        images.append({"bufferView": append_bytes(encoded.getvalue()), "mimeType": "image/png", "name": name})
    buffers.extend(b"\0" * (-len(buffers) % 4))
    document: dict[str, object] = {
        "asset": {"version": "2.0", "generator": "ReSeam fixed-corner material exporter"},
        "scene": 0, "scenes": [{"nodes": [0]}], "nodes": [{"mesh": 0}],
        "meshes": [{"primitives": [{"attributes": attributes, "indices": len(accessors) - 1, "material": 0}]}],
        "materials": [{"name": "ReSeam", "doubleSided": True,
                       "pbrMetallicRoughness": {"baseColorTexture": {"index": 0},
                                                "metallicRoughnessTexture": {"index": 2},
                                                "baseColorFactor": [1, 1, 1, 1],
                                                "metallicFactor": 1, "roughnessFactor": 1},
                       "normalTexture": {"index": 1, "scale": 1},
                       "occlusionTexture": {"index": 2, "strength": 1}}],
        "textures": [{"source": i, "sampler": 0} for i in range(3)], "images": images,
        "samplers": [{"magFilter": 9729, "minFilter": 9729, "wrapS": 33071, "wrapT": 33071}],
        "accessors": accessors, "bufferViews": views, "buffers": [{"byteLength": len(buffers)}],
        "extras": {"reseam": {"original_faces": len(state.mesh.faces), "vertex_policy": "explicit_per_corner",
                               "tangent_contract": "matches frame_arrays exactly",
                               "sampler": "LOD0 preview; validated complete mip PNGs are separate sidecars",
                               "gpu_validated": False, "artist_reviewed": False}},
    }
    text = json.dumps(document, separators=(",", ":"), allow_nan=False).encode("utf-8")
    text += b" " * (-len(text) % 4)
    raw = (struct.pack("<III", 0x46546C67, 2, 12 + 8 + len(text) + 8 + len(buffers))
           + struct.pack("<II", len(text), 0x4E4F534A) + text
           + struct.pack("<II", len(buffers), 0x004E4942) + buffers)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as stream:
        stream.write(raw)
    return {"path": str(path), "bytes": len(raw), "vertices": len(indices),
            "faces": len(state.mesh.faces), "vertex_attribute_bytes": len(indices) * (3 + 3 + 4 + 2) * 4,
            "index_bytes": indices.nbytes, "normal_tangent_exact": True,
            "sampler": "LOD0 preview; complete encoded mip sidecars exported separately"}


def bake_material(
    state: MeshState, reference: MaterialReference, resolution: int,
    output: Path | None = None, *, member_ids: IntArray | None = None,
    padding_pixels: int = 4, max_projection_fraction: float = 0.02,
    deadline: float | None = None,
) -> MaterialAtlas:
    """Bake a complete material after caller authorization of shared overlaps."""
    mip_dimensions(resolution, resolution)
    if resolution > 2048 or padding_pixels < 0 or max_projection_fraction <= 0:
        raise ValueError("Invalid material bake bounds")
    if output is not None and output.exists():
        raise FileExistsError(output)
    if not np.isfinite(state.uv).all() or np.any(state.uv < 0) or np.any(state.uv > 1):
        raise ValueError("Atlas UV outside [0,1]")
    members = chart_labels(state) if member_ids is None else np.asarray(member_ids, dtype=np.int64)
    if members.shape != (len(state.mesh.faces),):
        raise ValueError("One member ID per original face required")
    sums = np.zeros((resolution, resolution, 10))
    weights = np.zeros((resolution, resolution))
    frames = frame_arrays(state)
    seen: dict[int, set[int]] = {}
    invalid = count = 0
    invalid_faces: list[int] = []
    for fi, face in enumerate(state.uv_faces):
        _check_deadline(deadline)
        x, y, bary = _face_pixels(state.uv[face], resolution, resolution)
        if not len(x):
            continue
        # Validate every face's rasterized source point before member-level
        # composition deduplication. An overlap cannot hide an invalid source.
        samples = reference.sample(bary @ state.mesh.vertices[state.mesh.faces[fi]],
                                   max_projection_fraction * reference.diagonal, deadline=deadline)
        matrix, frame_valid = interpolated_frames(*frames, np.full(len(x), fi, dtype=np.int64), bary)
        local_normal = np.einsum("nji,nj->ni", matrix, samples.normal_world)
        valid = samples.valid & frame_valid
        invalid += int((~valid).sum())
        if not valid.all():
            invalid_faces.append(fi)
        count += len(x)
        values = np.column_stack((samples.base_color, local_normal, samples.orm))
        visited = seen.setdefault(int(members[fi]), set())
        keys = y * resolution + x
        keep = np.asarray([int(key) not in visited for key in keys], dtype=bool)
        x, y, bary, keys = x[keep], y[keep], bary[keep], keys[keep]
        valid, values = valid[keep], values[keep]
        visited.update(map(int, keys))
        if not len(x):
            continue
        tri = state.uv[face]
        uv_area = abs(float(np.linalg.det(np.column_stack((tri[1] - tri[0], tri[2] - tri[0]))))) / 2
        weight = state.mesh.areas[fi] / max(uv_area, 1e-30)
        sums[y[valid], x[valid]] += weight * values[valid]
        weights[y[valid], x[valid]] += weight
    coverage = weights > 0
    values = sums / np.maximum(weights[..., None], 1e-30)
    values[~coverage, 4:7] = [0, 0, 1]
    values[~coverage, 7:] = [1, 1, 0]
    if coverage.any() and padding_pixels:
        result = distance_transform_edt(~coverage, return_indices=True)
        if not isinstance(result, tuple):
            raise TypeError("Padding indices absent")
        distances, nearest = result
        pad = (~coverage) & (distances <= padding_pixels)
        values[pad] = values[nearest[0][pad], nearest[1][pad]]
    atlas = atlas_from_arrays(values[..., :4], values[..., 4:7], values[..., 7:10], coverage,
                              {"projected_member_texels": count, "invalid_projection_member_texels": invalid,
                               "invalid_projection_faces": invalid_faces,
                               "padding_pixels": padding_pixels, "reference_diagonal": reference.diagonal,
                               "source_frame": reference.metadata.get("source_frame", "explicit_test_frames"),
                               "source_mikk": reference.metadata.get("source_mikk", {}),
                               "support_state_sha256": _support_fingerprint(state),
                               "support_resolution": resolution,
                               "support_projection_fraction": max_projection_fraction,
                               "support_query_policy": "every rasterized original face texel before member dedup",
                               "overlap_authorization": "CALLER_REQUIRED"})
    if output is not None:
        save_material(atlas, output)
    return atlas


def refresh_material_support(
    state: MeshState, reference: MaterialReference, atlas: MaterialAtlas, *,
    member_ids: IntArray | None = None, padding_pixels: int = 4,
    max_projection_fraction: float = 0.02, deadline: float | None = None,
) -> MaterialAtlas:
    """Recheck final-UV raster support while preserving optimized encoded texels.

    A fresh bake supplies only coverage and full-raster projection evidence;
    its material values are discarded. The caller's optimized mip chains are
    preserved exactly rather than overwritten with another averaged bake.
    """
    height, width = atlas.base_color[0].shape[:2]
    if height != width or any(chain[0].shape != (height, width, 4)
                              for chain in (atlas.base_color, atlas.normal, atlas.orm)):
        raise ValueError("Support refresh requires equal square channel layouts")
    fresh = bake_material(state, reference, width, member_ids=member_ids,
                          padding_pixels=padding_pixels, max_projection_fraction=max_projection_fraction,
                          deadline=deadline)
    metadata = dict(atlas.metadata)
    metadata.update(fresh.metadata)
    metadata["support_refreshed_preserving_optimized_texels"] = True
    metadata["support_previous_state_sha256"] = atlas.metadata.get("support_state_sha256")
    return MaterialAtlas(atlas.base_color, atlas.normal, atlas.orm, fresh.coverage.copy(), metadata)


def sample_material(
    state: MeshState, atlas: MaterialAtlas, face_ids: IntArray, barycentric: FloatArray,
    mip: int = 0,
) -> MaterialSamples:
    return _sample_material_with_frames(state, atlas, face_ids, barycentric, mip, frame_arrays(state))


def _sample_material_with_frames(
    state: MeshState, atlas: MaterialAtlas, face_ids: IntArray, barycentric: FloatArray,
    mip: int, frames: tuple[FloatArray, FloatArray, FloatArray],
) -> MaterialSamples:
    if mip < 0 or mip >= len(atlas.base_color):
        raise ValueError("Unavailable material mip")
    uv = np.einsum("nk,nkj->nj", barycentric, state.uv[state.uv_faces[face_ids]])
    color = sample_rgba8(atlas.base_color[mip], uv)
    normal = sample_linear_rgba8(atlas.normal[mip], uv)[:, :3] * 2 - 1
    matrix, frame_valid = interpolated_frames(*frames, face_ids, barycentric)
    valid = frame_valid & (np.linalg.norm(normal, axis=1) > 1e-10) & (color[:, 3] >= 0.99)
    world = _unit(np.einsum("nij,nj->ni", matrix, _unit(normal)))
    orm = sample_linear_rgba8(atlas.orm[mip], uv)[:, :3]
    return MaterialSamples(color, world, orm, valid, np.zeros(len(uv)), face_ids.copy(), barycentric.copy())


@dataclass(frozen=True)
class MaterialThresholds:
    color_mean: float = 0.025
    color_p95: float = 0.08
    color_max: float = 0.20
    normal_mean_degrees: float = 5.0
    normal_p95_degrees: float = 15.0
    normal_max_degrees: float = 30.0
    orm_mean: float = 0.025
    orm_p95: float = 0.08
    orm_max: float = 0.20
    render_mean: float = 0.035
    render_p95: float = 0.10
    render_max: float = 0.25
    important_multiplier: float = 1.0

    def __post_init__(self) -> None:
        values = tuple(vars(self).values())
        if not all(np.isfinite(v) and v > 0 for v in values) or self.important_multiplier > 1:
            raise ValueError("Finite positive material thresholds; importance multiplier <=1 required")


@dataclass(frozen=True)
class PreparedMaterialSamples:
    face_ids: IntArray
    barycentric: FloatArray
    reference: MaterialSamples
    weights: FloatArray
    seam_samples: BoolArray
    count_per_face: int
    geometry_sha256: str


def _geometry_fingerprint(state: MeshState) -> str:
    digest = hashlib.sha256()
    digest.update(state.mesh.vertices.tobytes())
    digest.update(state.mesh.faces.tobytes())
    return digest.hexdigest()


def prepare_material_samples(
    state: MeshState, reference: MaterialReference, *, samples_per_face: int = 16,
    max_projection_fraction: float = 0.02, phase: float = 0.5,
    include_seam_samples: bool = True, deadline: float | None = None,
) -> PreparedMaterialSamples:
    """World-space probes independent of UV, reusable across all candidates.

    phase=0.5 is the default evaluation lattice. A different frozen phase is
    needed for optimization; no claim of unseen-model evaluation is implied.
    Edge-near probes are present for every edge so seam edits cannot hide them.
    """
    if samples_per_face < 1 or not 0 <= phase < 1 or max_projection_fraction <= 0:
        raise ValueError("Invalid material sampling contract")
    index = np.arange(samples_per_face, dtype=float)
    a = np.sqrt((index + phase) / samples_per_face)
    b = np.mod((index + phase) * ((np.sqrt(5) - 1) / 2), 1)
    bary = np.column_stack((1 - a, a * (1 - b), a * b))
    seam = np.zeros(len(bary), dtype=bool)
    if include_seam_samples:
        edge_near = []
        for edge in range(3):
            for base_fraction in (0.2, 0.5, 0.8):
                fraction = base_fraction + (phase - 0.5) * 0.1
                point = np.zeros(3)
                epsilon = 0.002 + (phase - 0.5) * 0.0005
                point[edge] = epsilon
                point[(edge + 1) % 3] = (1 - epsilon) * fraction
                point[(edge + 2) % 3] = (1 - epsilon) * (1 - fraction)
                edge_near.append(point)
        bary = np.vstack((bary, edge_near))
        seam = np.r_[seam, np.ones(9, dtype=bool)]
    per_face = len(bary)
    face_ids = np.repeat(np.arange(len(state.mesh.faces), dtype=np.int64), per_face)
    bary = np.tile(bary, (len(state.mesh.faces), 1))
    points = np.einsum("nk,nkj->nj", bary, state.mesh.vertices[state.mesh.faces[face_ids]])
    samples = reference.sample(points, max_projection_fraction * reference.diagonal, deadline=deadline)
    return PreparedMaterialSamples(face_ids, bary, samples,
                                   state.mesh.areas[face_ids] / per_face,
                                   np.tile(seam, len(state.mesh.faces)), per_face,
                                   _geometry_fingerprint(state))


def shade_material(
    samples: MaterialSamples, light: FloatArray, view: FloatArray | None = None,
) -> FloatArray:
    """Explicit finite CPU Lambert+GGX contract; no shadows, IBL or tonemapping."""
    light = _unit(np.asarray(light, dtype=float).reshape(1, 3))
    view_dir = _unit(np.asarray(view if view is not None else [0.3, 0.4, 1], dtype=float).reshape(1, 3))
    n, color = samples.normal_world, samples.base_color[:, :3]
    h = _unit(light + view_dir)
    nl = np.clip(n @ light[0], 0, 1)
    nv = np.clip(n @ view_dir[0], 0, 1)
    nh = np.clip(n @ h[0], 0, 1)
    vh = float(np.clip(view_dir[0] @ h[0], 0, 1))
    occ, roughness, metallic = samples.orm.T
    alpha2 = np.maximum(roughness, 0.045) ** 4
    distribution = alpha2 / (np.pi * (nh**2 * (alpha2 - 1) + 1)**2)
    k = (np.maximum(roughness, 0.045) + 1)**2 / 8
    visibility = (nl / np.maximum(nl * (1 - k) + k, 1e-12)
                  * nv / np.maximum(nv * (1 - k) + k, 1e-12))
    f0 = 0.04 * (1 - metallic[:, None]) + metallic[:, None] * color
    fresnel = f0 + (1 - f0) * (1 - vh)**5
    specular = distribution[:, None] * visibility[:, None] * fresnel / np.maximum(4 * nl * nv, 1e-10)[:, None]
    diffuse = (1 - fresnel) * (1 - metallic[:, None]) * color / np.pi
    return np.asarray((diffuse + specular) * nl[:, None] + 0.03 * occ[:, None] * color)


def _metric(values: FloatArray) -> dict[str, float]:
    return {"mean": float(values.mean()), "p95": float(np.quantile(values, 0.95)), "max": float(values.max())}


def _material_error_arrays(actual: MaterialSamples, target: MaterialSamples) -> dict[str, FloatArray]:
    lights = (np.asarray([0.6, -0.2, 1.0]), np.asarray([-0.5, 0.7, 0.6]))
    return {
        "color": np.linalg.norm(actual.base_color[:, :3] - target.base_color[:, :3], axis=1),
        "normal": np.degrees(np.arccos(np.clip(np.sum(actual.normal_world * target.normal_world, axis=1), -1, 1))),
        "orm": np.max(np.abs(actual.orm - target.orm), axis=1),
        "render": np.max(np.stack([np.linalg.norm(shade_material(actual, light) - shade_material(target, light),
                                                   axis=1) for light in lights]), axis=0),
    }


def evaluate_raster_material(
    state: MeshState, reference: MaterialReference, atlas: MaterialAtlas, *,
    mip_levels: tuple[int, ...] = (0, 1), thresholds: MaterialThresholds | None = None,
    max_projection_fraction: float = 0.02, important_faces: tuple[int, ...] = (),
    deadline: float | None = None,
) -> dict[str, object]:
    """Check decoded errors at every original face's covered texel center.

    Shared pixels are checked independently for every covering face. Missing
    centers do not remove a face from the separate fixed-probe contract. Each
    mip is compared to the point reference, not an integrated filter footprint.
    This routine is intended for final acceptance and is not a training loss.
    """
    limits = thresholds or MaterialThresholds()
    if not mip_levels or min(mip_levels) < 0 or max(mip_levels) >= len(atlas.base_color):
        raise ValueError("Requested raster material LOD unavailable")
    frames = frame_arrays(state)
    records: list[dict[str, object]] = []
    failed: set[int] = set()
    invalid: set[int] = set()
    empty: set[int] = set()
    channels: dict[str, list[FloatArray]] = {"color": [], "normal": [], "orm": [], "render": []}
    all_weights: list[FloatArray] = []
    violation = 0.0
    count = 0
    for mip in mip_levels:
        height, width = atlas.base_color[mip].shape[:2]
        for fi, face in enumerate(state.uv_faces):
            _check_deadline(deadline)
            _, _, bary = _face_pixels(state.uv[face], width, height)
            if not len(bary):
                empty.add(fi)
                records.append({"face": fi, "mip": mip, "samples": 0,
                                "status": "NO_TEXEL_CENTER_FIXED_PROBES_STILL_REQUIRED", "passed": True})
                continue
            ids = np.full(len(bary), fi, dtype=np.int64)
            actual = _sample_material_with_frames(state, atlas, ids, bary, mip, frames)
            points = bary @ state.mesh.vertices[state.mesh.faces[fi]]
            target = reference.sample(points, max_projection_fraction * reference.diagonal, deadline=deadline)
            arrays = _material_error_arrays(actual, target)
            valid = actual.valid & target.valid
            passed = bool(valid.all())
            metrics: dict[str, object] = {"face": fi, "mip": mip, "samples": len(bary),
                                          "valid_sample_fraction": float(valid.mean()),
                                          "reference_valid": bool(target.valid.all())}
            multiplier = limits.important_multiplier if fi in important_faces else 1.0
            for name, error in arrays.items():
                measure = _metric(error)
                metrics[name] = measure
                channels[name].append(error)
                suffix = "_degrees" if name == "normal" else ""
                for aggregate, value in measure.items():
                    bound = float(getattr(limits, f"{name}_{aggregate}{suffix}")) * multiplier
                    passed &= value <= bound
                    violation = max(violation, value / bound - 1)
            all_weights.append(np.full(len(bary), state.mesh.areas[fi] / len(bary)))
            count += len(bary)
            if not target.valid.all():
                invalid.add(fi)
            if not passed:
                failed.add(fi)
            metrics["passed"] = passed
            records.append(metrics)
    summaries: dict[str, object] = {}
    for name, values in channels.items():
        if not values:
            summaries[name] = {"mean": None, "p95": None, "max": None, "area_weighted_mean": None}
            continue
        errors, weights = np.concatenate(values), np.concatenate(all_weights)
        measure = _metric(errors)
        measure["area_weighted_mean"] = float(np.sum(errors * weights) / weights.sum())
        summaries[name] = measure
    return {"checked": True, "valid": bool(count > 0 and not failed), "count": count,
            "failed_faces": sorted(failed), "reference_invalid_faces": sorted(invalid),
            "empty_center_faces": sorted(empty), "faces": records, "channels": summaries,
            "quality_violation": float(max(0, violation) + (1 if invalid or count == 0 else 0)),
            "reference": "fixed high-surface point signal at actual mip texel centers, not integrated footprints",
            "sampling_scope": "every covering original face independently, including authorized shared overlap"}


def evaluate_material(
    state: MeshState, reference: MaterialReference, atlas: MaterialAtlas, *,
    samples_per_face: int = 16, mip_levels: tuple[int, ...] = (0, 1),
    thresholds: MaterialThresholds | None = None, max_projection_fraction: float = 0.02,
    important_faces: tuple[int, ...] = (), prepared: PreparedMaterialSamples | None = None,
    deadline: float | None = None, raster_checks: bool = False,
) -> dict[str, object]:
    """Final decoded material gate at independently frozen probes and lighting."""
    limits = thresholds or MaterialThresholds()
    if not mip_levels or min(mip_levels) < 0 or max(mip_levels) >= len(atlas.base_color):
        raise ValueError("Requested material LOD unavailable")
    cache = prepared or prepare_material_samples(state, reference, samples_per_face=samples_per_face,
        max_projection_fraction=max_projection_fraction, deadline=deadline)
    if (len(cache.face_ids) != len(state.mesh.faces) * cache.count_per_face
            or cache.geometry_sha256 != _geometry_fingerprint(state)):
        raise ValueError("Material probe cache face coverage mismatch")
    records: list[dict[str, object]] = []
    failed: set[int] = set()
    invalid: set[int] = set()
    reconstruction_invalid: set[int] = set()
    channels: dict[str, list[FloatArray]] = {"color": [], "normal": [], "orm": [], "render": []}
    violations: list[float] = []
    all_valid = True
    for mip in mip_levels:
        _check_deadline(deadline)
        actual = sample_material(state, atlas, cache.face_ids, cache.barycentric, mip)
        target = cache.reference
        arrays = _material_error_arrays(actual, target)
        valid = actual.valid & target.valid
        all_valid &= bool(valid.all())
        for name, array in arrays.items():
            channels[name].append(array)
        for fi in range(len(state.mesh.faces)):
            selected = slice(fi * cache.count_per_face, (fi + 1) * cache.count_per_face)
            multiplier = limits.important_multiplier if fi in important_faces else 1.0
            passed = bool(valid[selected].all())
            metrics: dict[str, object] = {"face": fi, "mip": mip, "reference_valid": bool(target.valid[selected].all()),
                                          "valid_sample_fraction": float(valid[selected].mean()),
                                          "important": fi in important_faces}
            for name, error in arrays.items():
                measure = _metric(error[selected])
                metrics[name] = measure
                suffix = "_degrees" if name == "normal" else ""
                for aggregate, value in measure.items():
                    bound = float(getattr(limits, f"{name}_{aggregate}{suffix}")) * multiplier
                    passed &= value <= bound
                    violations.append(max(0, value / bound - 1))
            metrics["passed"] = passed
            if not passed:
                failed.add(fi)
            if not target.valid[selected].all():
                invalid.add(fi)
            if not actual.valid[selected].all():
                reconstruction_invalid.add(fi)
            records.append(metrics)
    baked_invalid = atlas.metadata.get("invalid_projection_member_texels")
    support_matches = (atlas.metadata.get("support_state_sha256") == _support_fingerprint(state)
                       and atlas.metadata.get("support_resolution") == atlas.base_color[0].shape[0]
                       and atlas.metadata.get("support_projection_fraction") == max_projection_fraction)
    baked_valid = (support_matches and isinstance(baked_invalid, int) and baked_invalid == 0
                   and bool(atlas.coverage.any()))
    raster_invalid_faces = atlas.metadata.get("invalid_projection_faces", []) if support_matches else []
    if isinstance(raster_invalid_faces, list):
        failed.update(int(fi) for fi in raster_invalid_faces if isinstance(fi, int))
    source_valid = bool(cache.reference.valid.all())
    verdict = all_valid and baked_valid and not failed
    raster: dict[str, object] = {"checked": False, "valid": None, "count": 0,
                                 "faces": [], "channels": {}, "quality_violation": 0.0}
    if raster_checks:
        raster = evaluate_raster_material(state, reference, atlas, mip_levels=mip_levels, thresholds=limits,
            max_projection_fraction=max_projection_fraction, important_faces=important_faces, deadline=deadline)
        raster_failed = raster["failed_faces"]
        if isinstance(raster_failed, list):
            failed.update(int(fi) for fi in raster_failed if isinstance(fi, int))
        verdict &= raster["valid"] is True
    summaries: dict[str, object] = {}
    for name, values in channels.items():
        concatenated = np.concatenate(values)
        weight = np.tile(cache.weights, len(values))
        summary_metrics = _metric(concatenated)
        summary_metrics["area_weighted_mean"] = float(np.sum(concatenated * weight) / weight.sum())
        seam = np.tile(cache.seam_samples, len(values))
        if seam.any():
            summary_metrics["seam_near_mean"] = float(concatenated[seam].mean())
            summary_metrics["seam_near_max"] = float(concatenated[seam].max())
        summaries[name] = summary_metrics
    raster_violation = raster["quality_violation"]
    combined_violation = max(violations, default=0) + (0 if baked_valid and all_valid else 1)
    if isinstance(raster_violation, (int, float)):
        combined_violation = max(combined_violation, raster_violation)
    return {"status": ("CPU_MATERIAL_PROBES_AND_RASTER_VALIDATED" if raster_checks else
                        "CPU_MATERIAL_PROBES_VALIDATED") if verdict else "CPU_MATERIAL_QUALITY_REJECTED",
            "valid": bool(verdict), "failed_faces": sorted(failed), "reference_invalid_faces": sorted(invalid),
            "reconstruction_invalid_faces": sorted(reconstruction_invalid),
            "quality_violation": float(combined_violation),
            "reference_coverage": float(cache.reference.valid.mean()), "source_reference_valid": source_valid,
            "bake_reference_valid": baked_valid, "channels": summaries, "faces": records,
            "support_state_matches": support_matches,
            "support_state_sha256": atlas.metadata.get("support_state_sha256"),
            "support_status": ("CURRENT_RASTER_SOURCE_VALID" if baked_valid else
                               "STALE_OR_MISSING_SUPPORT_EVIDENCE" if not support_matches else
                               "RASTER_SOURCE_INVALID"),
            "raster_reference_invalid_faces": raster_invalid_faces,
            "raster_checked": raster_checks, "raster_valid": raster["valid"],
            "raster_count": raster["count"], "raster_faces": raster["faces"],
            "raster_channels": raster["channels"], "raster": raster,
            "acceptance_scope": ("fixed probes plus all actual requested-LOD face texel centers"
                                  if raster_checks else "fixed probes only; raster quality not checked"),
            "required_channels": ["BaseColor", "Normal", "ORM"], "normal_status": "MEASURED",
            "orm_status": "MEASURED", "render_status": "CPU_FIXED_LIGHT_POINT_SHADER_MEASURED",
            "render_contract": "two frozen lights Lambert+GGX; no shadows/IBL; evaluated on same low surface",
            "mip_levels": list(mip_levels), "mip_reference": "fixed high-surface point signal; not footprint integrated",
            "seam_policy": "all original edges have fixed near-edge probes regardless of candidate seam set",
            "sampling_boundary": "finite independent probes; no continuous bound or GPU verification",
            "payload_bytes": atlas.payload_bytes, "gpu_validated": False, "artist_reviewed": False}
