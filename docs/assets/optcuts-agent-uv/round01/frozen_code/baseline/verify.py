"""Independently reimport deliverables and verify the frozen comparison receipts."""

from __future__ import annotations

import argparse
import hashlib
from dataclasses import asdict
from pathlib import Path

import numpy as np
import trimesh
from PIL import Image
from trimesh.visual.material import PBRMaterial
from trimesh.visual.texture import TextureVisuals

from reseam.agent_tools.state import file_hash, load_snapshot, object_hash, read_object, write_json

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, default=ROOT / "artifacts/dragon_paper_baselines/round02")
    parser.add_argument("--delivery", type=Path, default=ROOT / "docs/assets/dragon-paper-baselines")
    args = parser.parse_args()
    base, delivery = args.input.resolve(), args.delivery.resolve()
    report = read_object(delivery / "measurements.json")
    rows = report["rows"]
    assert isinstance(rows, list) and len(rows) == 8
    fixed = load_snapshot(base / "parent")
    reimports: list[dict[str, object]] = []
    native_hashes: set[str] = set()
    completed, rejected = [], []
    for row in rows:
        assert isinstance(row, dict)
        method = str(row["method"])
        snapshot = load_snapshot(Path(str(row["snapshot"])))
        np.testing.assert_array_equal(snapshot.state.surface.mesh.vertices, fixed.state.surface.mesh.vertices)
        np.testing.assert_array_equal(snapshot.state.surface.mesh.faces, fixed.state.surface.mesh.faces)
        result = base / "methods" / method / "result.json"
        if result.exists():
            record = read_object(result)
            native_hashes.add(str(record["input_geometry_sha256"]))
            assert record["geometry_unchanged"] is True
        model = delivery / method / "model.glb"
        if not model.exists():
            rejected.append(method)
            assert read_object(base / "evaluation" / f"{method}_2048" / "pack/result.json")["status"] == "REJECTED"
            continue
        completed.append(method)
        imported = trimesh.load(model, force="mesh", process=False)
        assert isinstance(imported, trimesh.Trimesh)
        visual = imported.visual
        assert isinstance(visual, TextureVisuals) and isinstance(visual.material, PBRMaterial)
        image_texture = visual.material.baseColorTexture
        assert image_texture is not None
        surface = snapshot.state.surface
        positions = imported.vertices[imported.faces]
        uvs = visual.uv[imported.faces]
        error = float(np.max(abs(positions - surface.mesh.vertices[surface.mesh.faces])))
        uv_error = float(np.max(abs(uvs - surface.uv[surface.uv_faces])))
        with Image.open(delivery / method / "texture.png") as texture:
            pixel_match = np.array_equal(np.asarray(image_texture.convert("RGBA")),
                                         np.asarray(texture.convert("RGBA")))
        assert len(imported.faces) == 11338 and error < 1e-6 and uv_error < 1e-6 and pixel_match
        total_bytes = 0
        mips = sorted((delivery / method / "mips").glob("*.png"))
        assert len(mips) == 12
        for level in range(12):
            with Image.open(delivery / method / "mips" / f"BaseColor_mip{level}.png") as image:
                assert image.size == (max(1, 2048 >> level),) * 2 and image.mode == "RGBA"
                total_bytes += image.width * image.height * 4
        assert total_bytes == row["payload_bytes"] == 22369620
        reimports.append({"method": method, "faces": len(imported.faces), "position_max_error": error,
                          "uv_max_error": uv_error, "texture_pixels_equal": pixel_match,
                          "full_mips": len(mips), "payload_bytes": total_bytes})
    assert len(native_hashes) == 1 and set(rejected) == {"flatten_anything", "xatlas"}
    frozen = read_object(base / "frozen_code_manifest.json")
    eol_only: list[str] = []
    for path, digest in frozen.items():
        relative = Path(path.replace("\\", "/"))
        current = ROOT / relative
        if file_hash(current) == digest:
            continue
        archived = ROOT / "baseline/MeshQuery/project_source_snapshot" / relative
        assert file_hash(archived) == digest
        assert current.read_bytes().replace(b"\r\n", b"\n") == archived.read_bytes().replace(b"\r\n", b"\n")
        eol_only.append(path)
    replay = load_snapshot(base.parent / "replay_smoke01/parent")
    np.testing.assert_array_equal(replay.state.surface.mesh.vertices, fixed.state.surface.mesh.vertices)
    np.testing.assert_array_equal(replay.state.surface.mesh.faces, fixed.state.surface.mesh.faces)
    np.testing.assert_array_equal(replay.state.surface.uv, fixed.state.surface.uv)
    np.testing.assert_array_equal(replay.state.surface.uv_faces, fixed.state.surface.uv_faces)
    assert object_hash([asdict(g) for g in replay.state.groups]) == object_hash([asdict(g) for g in fixed.state.groups])
    repeat = read_object(base.parent / "replay_smoke01/methods/xatlas/result.json")
    assert repeat["chart_count"] == 643
    decoded = 0
    for path in delivery.rglob("*.png"):
        with Image.open(path) as image:
            image.load()
            assert image.format == "PNG"
            decoded += 1
    write_json(delivery / "verification.json", {"all_passed": True, "native_input_geometry_hashes": list(native_hashes),
        "geometry_faces": 11338, "models_reimported": reimports, "failed_native_uv_paths_retained": rejected,
        "frozen_source_files_unchanged": len(frozen), "checkout_eol_only_differences": eol_only,
        "source_hash_scope": "exact archived bytes; checkout differences allowed only for Git CRLF/LF normalization",
        "portable_replay_geometry_uv_groups_equal": True,
        "replayed_xatlas_charts": 643, "png_images_decoded": decoded,
        "note": "Integrity verification, not upgrading quality-rejected or artist/GPU/PBR states"})
    manifest = [{"path": str(path.relative_to(delivery)), "bytes": path.stat().st_size,
                 "sha256": hashlib.sha256(path.read_bytes()).hexdigest()} for path in delivery.rglob("*")
                if path.is_file() and path.name != "manifest.json"]
    write_json(delivery / "manifest.json", {"files": manifest})
    print({"all_passed": True, "models": completed, "failed_paths_preserved": rejected,
           "source_files": len(frozen), "png_decoded": decoded, "manifest_files": len(manifest)})


if __name__ == "__main__":
    main()
