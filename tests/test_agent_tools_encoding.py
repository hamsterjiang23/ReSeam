"""Actual DDS blocks, complete small mips and independent post-codec quality gates."""

import struct
import time
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from reseam.agent_tools import encoding
from reseam.agent_tools.color_reuse import _load, bake_color_reuse, export_color_reuse, validate_color_reuse
from reseam.agent_tools.encoding import (
    BC7_CODEC,
    HEADER_BYTES,
    block_bytes,
    dds_header,
    dds_layout,
    decode_bc1,
    decode_bc7,
    decode_dds_base,
    encode_bc1,
    encode_bc7,
    encode_material,
)
from reseam.agent_tools.runner import run_tool
from reseam.agent_tools.state import Snapshot, file_hash, read_object, save_snapshot, write_json
from reseam.budget_material import atlas_from_arrays, export_material_glb
from reseam.budget_state import BudgetState
from reseam.budget_texture import decode_rgba8, mip_dimensions
from reseam.mesh import make_mesh, state_from_corner_uv


def _deadline() -> float:
    return time.monotonic() + 60


def _folder(parent: Path, name: str) -> Path:
    path = parent / name
    path.mkdir()
    return path


def _baked(parent: Path) -> tuple[Snapshot, Path]:
    vertices = np.asarray([[0., 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0]])
    faces = np.asarray([[0, 1, 2], [0, 2, 3]], dtype=np.int64)
    surface = state_from_corner_uv(make_mesh(vertices, faces), .25 + .5 * vertices[faces, :2],
                                   seam_ids=frozenset())
    color = np.full((16, 16, 4), [20, 70, 220, 255], dtype=np.uint8)
    atlas = atlas_from_arrays(decode_rgba8(color), np.full((16, 16, 3), [0., 0, 1]),
                              np.full((16, 16, 3), [1., 1., 0]))
    reference = parent / "reference.glb"
    export_material_glb(surface, atlas, reference)
    snapshot = save_snapshot(BudgetState(surface), parent / "snapshot", {"reference_glb": str(reference)})
    result = bake_color_reuse({"snapshot": str(snapshot.root), "resolution": 32, "padding_pixels": 8},
                             _folder(parent, "bake"), _deadline())
    assert result["status"] == "OK"
    return snapshot, Path(str(result["bake"]))


@pytest.mark.parametrize(("width", "height"), [(1, 1), (2, 2), (3, 5), (8, 8)])
def test_real_bc1_block_roundtrip_allocates_minimum_block(width: int, height: int) -> None:
    source = np.full((height, width, 3), [20, 70, 220], dtype=np.uint8)
    payload = encode_bc1(source)
    assert len(payload) == ((width + 3) // 4) * ((height + 3) // 4) * 8
    decoded = decode_bc1(payload, width, height)
    assert tuple(decoded[0, 0]) == (16, 69, 222, 255)
    assert not np.array_equal(decoded[..., :3], source)


def test_full_dds_header_mip_offsets_and_each_independent_color(tmp_path: Path) -> None:
    dimensions = mip_dimensions(8, 8)
    colors = [np.full((h, w, 3), [20 + level * 30, 70, 220], dtype=np.uint8)
              for level, (w, h) in enumerate(dimensions)]
    payloads = [encode_bc1(color) for color in colors]
    path = tmp_path / "full.dds"
    path.write_bytes(dds_header(8, 8, len(dimensions)) + b"".join(payloads))
    raw = path.read_bytes()
    assert len(raw) == HEADER_BYTES + 32 + 8 + 8 + 8
    assert struct.unpack_from("<I", raw, 4)[0] == 124
    assert raw[84:88] == b"DX10"
    assert struct.unpack_from("<5I", raw, 128) == (72, 3, 0, 1, 3)
    layout = dds_layout(path)
    assert np.array_equal(decode_dds_base(path), decode_bc1(payloads[0], 8, 8))
    assert path.read_bytes() == raw  # The CPU reader adapter never modifies the delivered sRGB header.
    for mip, payload in zip(layout, payloads, strict=True):
        actual = raw[mip.offset:mip.offset + mip.payload_bytes]
        assert actual == payload
        assert np.array_equal(decode_bc1(actual, mip.width, mip.height),
                              decode_bc1(encode_bc1(colors[mip.level]), mip.width, mip.height))
    path.write_bytes(raw[:128] + struct.pack("<I", 71) + raw[132:])
    with pytest.raises(ValueError, match="DDS_HEADER_OR_COMPLETE_MIP_CHAIN_MISMATCH"):
        dds_layout(path)
    path.write_bytes(raw[:-1])
    with pytest.raises(ValueError, match="DDS_FILE_SIZE_MISMATCH"):
        dds_layout(path)


def test_material_encoding_preserves_support_and_source_while_recording_real_payload(tmp_path: Path) -> None:
    snapshot, source = _baked(tmp_path)
    before = {str(path): file_hash(path) for path in tmp_path.rglob("*") if path.is_file()}
    original, _, original_levels = _load(snapshot, source)
    result = encode_material({"snapshot": str(snapshot.root), "bake": str(source)},
                             tmp_path / "encoded", _deadline())
    assert result["status"] == "OK" and result["quality_accepted"] is False
    path = Path(str(result["bake"]))
    record, target, decoded = _load(snapshot, path)
    dds = path.parent / "texture" / "BaseColor.dds"
    layout = dds_layout(dds)
    payload_bytes = record["payload_bytes"]
    assert type(payload_bytes) is int
    assert payload_bytes == sum(block_bytes(w, h) for w, h in mip_dimensions(32, 32))
    assert record["package_bytes"] == dds.stat().st_size == payload_bytes + HEADER_BYTES
    assert record["decoded_preview_bytes"] == original["payload_bytes"]
    assert record["runtime_artifacts"] == ["texture/BaseColor.dds"]
    assert target.path == Path(str(original["target"]))
    assert record["encoding_parent_bake_sha256"] == file_hash(source)
    with dds.open("rb") as stream:
        for mip, before_level, after_level in zip(layout, original_levels, decoded, strict=True):
            stream.seek(mip.offset)
            rgb = decode_bc1(stream.read(mip.payload_bytes), mip.width, mip.height)
            assert np.array_equal(rgb[..., :3], after_level[..., :3])
            assert np.array_equal(before_level[..., 3], after_level[..., 3])
            with Image.open(path.parent / "texture" / f"BaseColor_support_mip{mip.level}.png") as mask:
                assert np.array_equal(np.asarray(mask), before_level[..., 3])
    assert any(not np.array_equal(a[..., :3], b[..., :3]) for a, b in zip(original_levels, decoded, strict=True))
    assert all(file_hash(Path(name)) == digest for name, digest in before.items())
    with pytest.raises(ValueError, match="ENCODING_REQUIRES_ORIGINAL_RGBA8"):
        encode_material({"snapshot": str(snapshot.root), "bake": str(path)}, tmp_path / "twice", _deadline())
    # Every delivered byte is authenticated by the existing independent loader.
    data = bytearray(dds.read_bytes())
    data[-1] ^= 1
    dds.write_bytes(data)
    with pytest.raises(ValueError, match="BAKE_FILE_CHANGED"):
        _load(snapshot, path)


def test_compression_success_can_fail_independent_color_quality(tmp_path: Path) -> None:
    snapshot, source = _baked(tmp_path)
    request = {"snapshot": str(snapshot.root), "bake": str(source), "render_size": 16,
               "geometry_profile": {"gap_pixels": 0, "border_pixels": 0},
               "mip_levels": [0, 1, 2], "thresholds": {"color_mean": 1e-4, "color_p95": 1e-4, "color_max": 1e-4}}
    baseline = validate_color_reuse(request, _folder(tmp_path, "original_quality"), _deadline())
    assert baseline["quality_accepted"] is True, read_object(Path(str(baseline["validation"])))
    encoded = encode_material({"snapshot": str(snapshot.root), "bake": str(source)},
                              tmp_path / "encoded", _deadline())
    checked = validate_color_reuse({**request, "bake": encoded["bake"]},
                                  _folder(tmp_path, "compressed_quality"), _deadline())
    assert checked["quality_accepted"] is False
    evidence = read_object(Path(str(checked["validation"])))
    material = evidence["material"]
    assert isinstance(material, dict)
    assert material["color_failed_faces"] and material["coverage_failed_faces"] == []


def test_input_tampering_and_expired_deadline_cannot_modify_parent(tmp_path: Path) -> None:
    snapshot, source = _baked(tmp_path)
    request: dict[str, object] = {"snapshot": str(snapshot.root), "bake": str(source)}
    with pytest.raises(TimeoutError, match="TOOL_DEADLINE_EXHAUSTED"):
        encode_material(request, tmp_path / "expired", time.monotonic() - 1)
    assert not (tmp_path / "expired").exists()
    image = source.parent / "texture" / "BaseColor_mip0.png"
    image.write_bytes(image.read_bytes() + b"tampered")
    with pytest.raises(ValueError, match="BAKE_FILE_CHANGED"):
        encode_material(request, tmp_path / "bad_input", _deadline())
    assert not (tmp_path / "bad_input").exists()


def test_public_encode_tool_reports_bytes_without_quality_acceptance(tmp_path: Path) -> None:
    snapshot, source = _baked(tmp_path)
    request = tmp_path / "encode_request.json"
    write_json(request, {"snapshot": str(snapshot.root), "bake": str(source), "codec": "BC1_SRGB",
                         "timeout_seconds": 30})
    result = run_tool("material.encode", request, tmp_path / "public_tool")
    assert result["status"] == "OK", result
    assert result["codec"] == "BC1_SRGB" and result["quality_accepted"] is False
    assert result["payload_bytes"] == 696
    assert result["package_bytes"] == 844
    assert (tmp_path / "public_tool" / "provenance.json").is_file()


def test_unlisted_source_mip_cannot_bypass_input_authentication(tmp_path: Path) -> None:
    snapshot, source = _baked(tmp_path)
    manifest = read_object(source)
    files = manifest["files"]
    assert isinstance(files, dict)
    manifest["files"] = {key: digest for key, digest in files.items() if "BaseColor_mip0.png" not in key}
    write_json(source, manifest)
    with pytest.raises(ValueError, match="AUTHENTICATED_SOURCE_MIPS_AND_COVERAGE_REQUIRED"):
        encode_material({"snapshot": str(snapshot.root), "bake": str(source)},
                        tmp_path / "missing_authentication", _deadline())


def test_exported_compressed_bake_has_all_authenticated_files(tmp_path: Path) -> None:
    snapshot, source = _baked(tmp_path)
    encoded = encode_material({"snapshot": str(snapshot.root), "bake": str(source)},
                              tmp_path / "encoded", _deadline())
    destination = _folder(tmp_path, "delivered")
    exported = export_color_reuse({"snapshot": str(snapshot.root), "bake": encoded["bake"],
                                   "mode": "diagnostic"}, destination, _deadline())
    assert exported["status"] == "OK"
    record, _, levels = _load(snapshot, destination / "bake.json")
    assert record["codec"] == "BC1_SRGB" and len(levels) == 6
    files = record["files"]
    assert isinstance(files, dict)
    for name, digest in files.items():
        assert isinstance(name, str)
        assert file_hash(destination / name) == digest
    assert (destination / "coverage.npy").is_file()
    assert (destination / "geometric_coverage.npy").is_file()


@pytest.mark.parametrize(("width", "height"), [(1, 1), (2, 2), (3, 5), (8, 8)])
def test_bc7_edge_padding_and_actual_srgb_block_decode(width: int, height: int) -> None:
    pytest.importorskip("ispc_texcomp")
    source = np.full((height, width, 3), [20, 70, 220], dtype=np.uint8)
    payload = encode_bc7(source)
    assert len(payload) == ((width + 3) // 4) * ((height + 3) // 4) * 16
    decoded = decode_bc7(payload, width, height)
    assert decoded.shape == (height, width, 4)
    assert np.max(np.abs(decoded[..., :3].astype(int) - source.astype(int))) <= 1
    assert np.all(decoded[..., 3] == 255)
    assert np.array_equal(decoded, decode_bc7(encode_bc7(source), width, height))


def test_bc7_full_mip_container_offsets_and_direct_pillow_readback(tmp_path: Path) -> None:
    pytest.importorskip("ispc_texcomp")
    dimensions = mip_dimensions(8, 8)
    payloads = [encode_bc7(np.full((h, w, 3), [20 + level * 30, 70, 220], dtype=np.uint8))
                for level, (w, h) in enumerate(dimensions)]
    path = tmp_path / "bc7.dds"
    path.write_bytes(dds_header(8, 8, len(dimensions), BC7_CODEC) + b"".join(payloads))
    raw = path.read_bytes()
    assert len(raw) == HEADER_BYTES + 64 + 16 + 16 + 16
    assert struct.unpack_from("<5I", raw, 128) == (99, 3, 0, 1, 3)
    layout = dds_layout(path)
    with Image.open(path) as delivered:
        assert np.array_equal(np.asarray(delivered.convert("RGBA")), decode_dds_base(path))
    for mip, payload in zip(layout, payloads, strict=True):
        actual = raw[mip.offset:mip.offset + mip.payload_bytes]
        assert actual == payload
        assert abs(int(decode_bc7(actual, mip.width, mip.height)[0, 0, 0]) - (20 + mip.level * 30)) <= 1
    assert path.read_bytes() == raw


def test_bc7_public_encode_provenance_support_quality_and_export(tmp_path: Path) -> None:
    pytest.importorskip("ispc_texcomp")
    snapshot, source = _baked(tmp_path)
    before = {str(path): file_hash(path) for path in tmp_path.rglob("*") if path.is_file()}
    _, _, original_levels = _load(snapshot, source)
    request = tmp_path / "bc7_request.json"
    write_json(request, {"snapshot": str(snapshot.root), "bake": str(source), "codec": BC7_CODEC,
                         "timeout_seconds": 30})
    result = run_tool("material.encode", request, tmp_path / "bc7")
    assert result["status"] == "OK", result
    assert result["codec"] == BC7_CODEC and result["quality_accepted"] is False
    assert result["payload_bytes"] == 1392 and result["package_bytes"] == 1540
    record, _, levels = _load(snapshot, Path(str(result["bake"])))
    encoder = record["encoder"]
    assert isinstance(encoder, dict)
    assert encoder["profile"] == "slow" and encoder["version"] == "1.0.1"
    assert file_hash(Path(str(encoder["native_path"]))) == encoder["native_sha256"]
    assert "DXGI99 directly" in str(record["cpu_decode_adapter"])
    assert all(np.array_equal(original[..., 3], decoded[..., 3])
               for original, decoded in zip(original_levels, levels, strict=True))
    assert all(file_hash(Path(name)) == digest for name, digest in before.items())
    # Public execution still requires a separate, actual post-codec quality gate.
    quality = validate_color_reuse({"snapshot": str(snapshot.root), "bake": result["bake"],
                                   "render_size": 16, "mip_levels": [0, 1, 2],
                                   "geometry_profile": {"gap_pixels": 0, "border_pixels": 0}},
                                  _folder(tmp_path, "bc7_quality"), _deadline())
    assert quality["quality_accepted"] is True
    destination = _folder(tmp_path, "bc7_delivery")
    exported = export_color_reuse({"snapshot": str(snapshot.root), "bake": result["bake"],
                                  "mode": "diagnostic"}, destination, _deadline())
    assert exported["status"] == "OK"
    delivered, _, _ = _load(snapshot, destination / "bake.json")
    assert delivered["codec"] == BC7_CODEC
    assert delivered["runtime_package_bytes"] == 1540
    assert (destination / "texture" / "BaseColor.dds").read_bytes() == (
        tmp_path / "bc7" / "texture" / "BaseColor.dds").read_bytes()


def test_missing_optional_bc7_dependency_is_explicit_and_bc1_still_works(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def missing_backend(name: str) -> object:
        raise ModuleNotFoundError(name)

    monkeypatch.setattr(encoding.importlib, "import_module", missing_backend)
    source = np.full((4, 4, 3), [20, 70, 220], dtype=np.uint8)
    assert len(encode_bc1(source)) == 8
    with pytest.raises(ModuleNotFoundError, match="BC7_DEPENDENCY_MISSING"):
        encode_bc7(source)
