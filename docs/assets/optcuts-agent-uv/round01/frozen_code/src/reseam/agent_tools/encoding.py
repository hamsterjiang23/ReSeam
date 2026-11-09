"""Real opaque BC1/BC7 sRGB DDS encoding with separately retained sampling support.

DDS_HEADER / DDS_HEADER_DXT10 layout follows Microsoft's DDS reference:
https://learn.microsoft.com/en-us/windows/win32/direct3ddds/dds-header
https://learn.microsoft.com/en-us/windows/win32/direct3ddds/dds-header-dxt10
DXGI_FORMAT_BC1_UNORM_SRGB=72; BC7_UNORM_SRGB=99; TEXTURE2D=3.
"""

import importlib
import importlib.util
import shutil
import struct
from dataclasses import asdict, dataclass
from io import BytesIO
from pathlib import Path
from types import ModuleType

import numpy as np
import PIL
from PIL import Image

from ..budget_texture import Uint8Array, mip_dimensions
from .color_reuse import SCOPE, _load
from .state import check_deadline, file_hash, require_snapshot, write_json

CODEC = "BC1_SRGB"
BC7_CODEC = "BC7_SRGB"
BC7_VERSION = "1.0.1"
BC7_PROFILE = "slow"
_FORMATS = {CODEC: (72, 8), BC7_CODEC: (99, 16)}
HEADER_BYTES = 148


@dataclass(frozen=True)
class CompressedMip:
    level: int
    width: int
    height: int
    offset: int
    payload_bytes: int


def block_bytes(width: int, height: int, codec: str = CODEC) -> int:
    if type(width) is not int or type(height) is not int or width < 1 or height < 1:
        raise ValueError("POSITIVE_INTEGER_TEXTURE_DIMENSIONS_REQUIRED")
    if codec not in _FORMATS:
        raise ValueError("UNSUPPORTED_MATERIAL_CODEC")
    return ((width + 3) // 4) * ((height + 3) // 4) * _FORMATS[codec][1]


def dds_header(width: int, height: int, mip_count: int, codec: str = CODEC) -> bytes:
    """A 124-byte DDS header plus DX10 metadata; the alpha mode is opaque."""
    if type(mip_count) is not int or mip_count < 1:
        raise ValueError("POSITIVE_INTEGER_MIP_COUNT_REQUIRED")
    flags = 0x1 | 0x2 | 0x4 | 0x1000 | 0x80000
    caps = 0x1000
    if mip_count > 1:
        flags |= 0x20000
        caps |= 0x8 | 0x400000
    size = block_bytes(width, height, codec)
    return (b"DDS " + struct.pack("<7I", 124, flags, height, width, size, 0, mip_count)
            + struct.pack("<11I", *([0] * 11))
            + struct.pack("<8I", 32, 0x4, int.from_bytes(b"DX10", "little"), 0, 0, 0, 0, 0)
            + struct.pack("<5I", caps, 0, 0, 0, 0)
            + struct.pack("<5I", _FORMATS[codec][0], 3, 0, 1, 3))


def encode_bc1(rgb: Uint8Array) -> bytes:
    if rgb.dtype != np.uint8 or rgb.ndim != 3 or rgb.shape[2] != 3:
        raise ValueError("UINT8_RGB_REQUIRED")
    height, width = rgb.shape[:2]
    expected = block_bytes(width, height)
    stream = BytesIO()
    Image.fromarray(rgb).save(stream, format="DDS", pixel_format="DXT1")
    legacy = stream.getvalue()
    if (len(legacy) != 128 + expected or legacy[:4] != b"DDS " or legacy[84:88] != b"DXT1"
            or struct.unpack_from("<I", legacy, 4)[0] != 124
            or struct.unpack_from("<2I", legacy, 12) != (height, width)):
        raise ValueError("ENCODER_DID_NOT_RETURN_EXPECTED_BC1_BLOCKS")
    return legacy[128:]


def decode_bc1(payload: bytes, width: int, height: int) -> Uint8Array:
    if len(payload) != block_bytes(width, height):
        raise ValueError("BC1_PAYLOAD_SIZE_MISMATCH")
    decoded = _numeric_decode(dds_header(width, height, 1) + payload)
    if decoded.shape != (height, width, 4) or np.any(decoded[..., 3] != 255):
        raise ValueError("BC1_OPAQUE_DECODE_CONTRACT_FAILED")
    return decoded


def _bc7_backend() -> ModuleType:
    """Keep the optional native dependency out of analysis and BC1 imports."""
    try:
        backend = importlib.import_module("ispc_texcomp")
    except ImportError as error:
        raise ModuleNotFoundError("BC7_DEPENDENCY_MISSING: run with uv run --extra compression") from error
    if backend.__version__ != BC7_VERSION:
        raise ValueError(f"BC7_BACKEND_VERSION_MISMATCH: expected {BC7_VERSION}")
    return backend


def encode_bc7(rgb: Uint8Array) -> bytes:
    if rgb.dtype != np.uint8 or rgb.ndim != 3 or rgb.shape[2] != 3:
        raise ValueError("UINT8_RGB_REQUIRED")
    height, width = rgb.shape[:2]
    expected = block_bytes(width, height, BC7_CODEC)
    backend = _bc7_backend()
    # The native compressor requires complete 4x4 blocks. Replicate edge texels
    # before compression, including 1x1/2x2 tail mips, then decode at actual size.
    padded = np.pad(rgb, ((0, (-height) % 4), (0, (-width) % 4), (0, 0)), mode="edge")
    rgba = np.full((*padded.shape[:2], 4), 255, dtype=np.uint8)
    rgba[..., :3] = padded
    h, w = padded.shape[0], padded.shape[1]
    surface = backend.RGBASurface(rgba.tobytes(), w, h, w * 4)
    settings = backend.BC7EncSettings.from_profile(BC7_PROFILE)
    payload = backend.compress_blocks_bc7(surface, settings)
    if not isinstance(payload, bytes) or len(payload) != expected:
        raise ValueError("ENCODER_DID_NOT_RETURN_EXPECTED_BC7_BLOCKS")
    return payload


def decode_bc7(payload: bytes, width: int, height: int) -> Uint8Array:
    if len(payload) != block_bytes(width, height, BC7_CODEC):
        raise ValueError("BC7_PAYLOAD_SIZE_MISMATCH")
    decoded = _numeric_decode(dds_header(width, height, 1, BC7_CODEC) + payload)
    if decoded.shape != (height, width, 4) or np.any(decoded[..., 3] != 255):
        raise ValueError("BC7_OPAQUE_DECODE_CONTRACT_FAILED")
    return decoded


def _encoder_metadata(codec: str) -> dict[str, object]:
    result: dict[str, object] = {
        "source_mips": "each authenticated original mip encoded independently; no recursive lossy mip generation"}
    if codec == CODEC:
        result.update({"name": "Pillow DDS DXT1", "version": PIL.__version__})
        return result
    backend = _bc7_backend()
    spec = importlib.util.find_spec("ispc_texcomp._ispc_texcomp")
    if spec is None or spec.origin is None:
        raise ValueError("BC7_NATIVE_BACKEND_PROVENANCE_MISSING")
    native = Path(spec.origin).resolve()
    result.update({"name": "ispc_texcomp compress_blocks_bc7", "version": backend.__version__,
                   "profile": BC7_PROFILE, "channels": 3, "native_path": str(native),
                   "native_sha256": file_hash(native), "execution": "CPU native ISPC compressor",
                   "padding": "edge replication to multiples of four; decoded at original mip dimensions"})
    return result


def _numeric_decode(data: bytes) -> Uint8Array:
    # Pillow 12.3.0's DX10 dispatcher supports BC1_UNORM (71), but omits
    # BC1_UNORM_SRGB (72). BC1 block decoding is identical; the transfer function
    # is applied later by sample_rgba8. Alias only this in-memory format field.
    if len(data) < HEADER_BYTES or data[84:88] != b"DX10":
        raise ValueError("SRGB_DX10_HEADER_REQUIRED")
    format_id = struct.unpack_from("<I", data, 128)[0]
    if format_id not in (72, 99):
        raise ValueError("SUPPORTED_SRGB_DX10_FORMAT_REQUIRED")
    # Pillow directly supports DXGI99 and returns its sRGB code values. The
    # optional gamma metadata is not applied to numeric array conversion.
    compatible = data[:128] + struct.pack("<I", 71) + data[132:] if format_id == 72 else data
    with Image.open(BytesIO(compatible)) as image:
        return np.array(image.convert("RGBA"), dtype=np.uint8, copy=True)


def decode_dds_base(path: Path) -> Uint8Array:
    """Validate the whole delivered container, then decode its base numerically."""
    dds_layout(path)
    return _numeric_decode(path.read_bytes())


def dds_layout(path: Path) -> list[CompressedMip]:
    """Check the actual complete file, including 1x1 and 2x2 block allocation."""
    with path.open("rb") as stream:
        header = stream.read(HEADER_BYTES)
    if len(header) != HEADER_BYTES or header[:4] != b"DDS ":
        raise ValueError("INVALID_DDS_HEADER")
    height, width = struct.unpack_from("<2I", header, 12)
    if not 1 <= width <= 8192 or not 1 <= height <= 8192:
        raise ValueError("DDS_DIMENSIONS_OUT_OF_RANGE")
    dimensions = mip_dimensions(width, height)
    format_id = struct.unpack_from("<I", header, 128)[0]
    codec = next((name for name, (value, _) in _FORMATS.items() if value == format_id), None)
    if codec is None or header != dds_header(width, height, len(dimensions), codec):
        raise ValueError("DDS_HEADER_OR_COMPLETE_MIP_CHAIN_MISMATCH")
    result: list[CompressedMip] = []
    offset = HEADER_BYTES
    for level, (w, h) in enumerate(dimensions):
        size = block_bytes(w, h, codec)
        result.append(CompressedMip(level, w, h, offset, size))
        offset += size
    if path.stat().st_size != offset:
        raise ValueError("DDS_FILE_SIZE_MISMATCH")
    return result


def encode_material(request: dict[str, object], output: Path, deadline: float) -> dict[str, object]:
    check_deadline(deadline)
    codec = request.get("codec", CODEC)
    if not isinstance(codec, str) or codec not in _FORMATS:
        raise ValueError("UNSUPPORTED_MATERIAL_CODEC")
    encoder = _encoder_metadata(codec)
    encode = encode_bc1 if codec == CODEC else encode_bc7
    decode = decode_bc1 if codec == CODEC else decode_bc7
    snapshot = require_snapshot(request)
    path = Path(str(request.get("bake", ""))).resolve()
    if output.resolve().is_relative_to(path.parent):
        raise ValueError("ENCODING_OUTPUT_MUST_BE_OUTSIDE_IMMUTABLE_INPUT")
    parent_hash = file_hash(path)
    record, target, levels = _load(snapshot, path)
    if record.get("codec") != "RGBA8_SRGB_RGB_LINEAR_ALPHA":
        raise ValueError("ENCODING_REQUIRES_ORIGINAL_RGBA8_BASECOLOR_BAKE")
    resolution = record.get("resolution")
    if type(resolution) is not int or not 1 <= resolution <= 8192:
        raise ValueError("INVALID_ENCODING_RESOLUTION")
    dimensions = mip_dimensions(resolution, resolution)
    if len(levels) != len(dimensions):
        raise ValueError("ENCODING_REQUIRES_COMPLETE_SOURCE_MIPS")
    inputs = {path: parent_hash, target.path: str(record["target_sha256"]),
              target.path.parent / "target.npz": str(target.record["arrays_sha256"]),
              Path(str(snapshot.metadata["reference_glb"])): str(target.record["source_sha256"])}
    files = record["files"]
    if not isinstance(files, dict):
        raise TypeError("BAKE_FILES_REQUIRED")
    inputs.update({path.parent / str(name): str(digest) for name, digest in files.items()})
    required = {path.parent / "texture" / f"BaseColor_mip{level}.png" for level in range(len(dimensions))}
    required.update(path.parent / name for name in ("coverage.npy", "geometric_coverage.npy"))
    if not required <= set(inputs):
        raise ValueError("AUTHENTICATED_SOURCE_MIPS_AND_COVERAGE_REQUIRED")
    topology = target.record.get("original_topology")
    if isinstance(topology, dict):
        inputs[target.path.parent / "original_topology.npz"] = str(topology["sha256"])
    folder = output / "texture"
    folder.mkdir(parents=True, exist_ok=False)
    texture = folder / "BaseColor.dds"
    masks: list[Path] = []
    with texture.open("xb") as stream:
        stream.write(dds_header(resolution, resolution, len(dimensions), codec))
        for level, (width, height) in enumerate(dimensions):
            check_deadline(deadline)
            source = levels[level]
            if source.shape != (height, width, 4) or source.dtype != np.uint8:
                raise ValueError("SOURCE_MIP_DIMENSIONS_OR_TYPE_MISMATCH")
            stream.write(encode(source[..., :3]))
            mask = folder / f"BaseColor_support_mip{level}.png"
            Image.fromarray(source[..., 3]).save(mask)
            masks.append(mask)
            # The original files stay immutable; release loaded RGB before the
            # second pass reads the actual DDS payload and support mask files.
            levels[level] = np.empty((0, 0, 4), dtype=np.uint8)
    del levels, source
    layout = dds_layout(texture)
    previews: list[Path] = []
    with texture.open("rb") as stream:
        for mip, mask in zip(layout, masks, strict=True):
            check_deadline(deadline)
            stream.seek(mip.offset)
            decoded = decode(stream.read(mip.payload_bytes), mip.width, mip.height)
            if mip.level == 0 and not np.array_equal(decode_dds_base(texture), decoded):
                raise ValueError("DDS_CONTAINER_AND_MIP_OFFSET_DECODE_DISAGREE")
            with Image.open(mask) as support:
                decoded[..., 3] = np.asarray(support.convert("L"))
            preview = folder / f"BaseColor_mip{mip.level}.png"
            Image.fromarray(decoded).save(preview)
            previews.append(preview)
    copied = []
    for name in ("coverage.npy", "geometric_coverage.npy"):
        check_deadline(deadline)
        source_file = path.parent / name
        if name not in files:
            raise ValueError("AUTHENTICATED_COVERAGE_FILE_REQUIRED")
        shutil.copy2(source_file, output / name)
        copied.append(output / name)
    check_deadline(deadline)
    if require_snapshot(request).state_hash != snapshot.state_hash or any(
        file_hash(item) != digest for item, digest in inputs.items()
    ):
        raise ValueError("ENCODING_INPUT_CHANGED_DURING_OPERATION")
    manifest = dict(record)
    payload_bytes = sum(mip.payload_bytes for mip in layout)
    manifest.update({
        "codec": codec, "payload_bytes": payload_bytes, "runtime_texture_bytes": payload_bytes,
        "package_bytes": texture.stat().st_size, "runtime_package_bytes": texture.stat().st_size,
        "package_bytes_definition": "DDS runtime file only, including its 148-byte header; previews and masks separate",
        "runtime_artifacts": [texture.relative_to(output).as_posix()],
        "decoded_preview_bytes": sum(width * height * 4 for width, height in dimensions),
        "decoded_preview_package_bytes": sum(item.stat().st_size for item in previews),
        "support_mask_bytes": sum(item.stat().st_size for item in masks),
        "encoding_parent_bake": str(path), "encoding_parent_bake_sha256": parent_hash,
        "encoding_inputs_sha256": {str(item): digest for item, digest in inputs.items()},
        "encoder": encoder,
        "dds": {"format": f"DXGI_FORMAT_{codec.removesuffix('_SRGB')}_UNORM_SRGB", "dxgi_format": _FORMATS[codec][0],
                "resource_dimension": 3, "array_size": 1, "alpha_mode": "OPAQUE", "header_bytes": HEADER_BYTES,
                "mips": [asdict(mip) for mip in layout]},
        "support_alpha": {"meaning": "source-sampled support, not authored transparency",
                          "files": [item.relative_to(output).as_posix() for item in masks],
                          "decoded_preview_alpha": "exact original support mask retained for independent coverage validation",
                          "dds_alpha": "opaque; support masks are verification artifacts, not runtime alpha"},
        "decoded_preview_rgb": "decoded from the delivered DDS bytes at each recorded mip offset",
        "cpu_decode_adapter": (
            "Pillow DXGI72 dispatcher absent: in-memory header aliases72 to71; block bytes unchanged; sample_rgba8 applies sRGB transfer"
            if codec == CODEC else
            "Pillow decodes delivered DXGI99 directly to sRGB code values; sample_rgba8 applies sRGB transfer"),
        "files": {item.relative_to(output).as_posix(): file_hash(item) for item in [texture, *previews, *masks, *copied]},
        "quality_accepted": False, "full_pbr_passed": False, "gpu_verified": False, "artist_reviewed": False,
    })
    check_deadline(deadline)
    write_json(output / "bake.json", manifest)
    return {"status": "OK", "execution_success": True, "scope": SCOPE, "codec": codec,
            "bake": str((output / "bake.json").resolve()), "target": str(target.path),
            "payload_bytes": payload_bytes, "package_bytes": texture.stat().st_size,
            "quality_accepted": False, "full_pbr_passed": False, "gpu_verified": False}
