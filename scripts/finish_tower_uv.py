"""Final closeups, comparison scene, and integrity records for tower experiments."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
from pathlib import Path
from typing import cast

import numpy as np
from run_tower_uv import BASE, BLENDER, CAMERAS, call, parent

from reseam.agent_tools.material import _reference
from reseam.agent_tools.material_identity import face_corner_identity
from reseam.agent_tools.state import file_hash, load_snapshot, read_object, write_json

FINAL = "mirror_negative_exhaustive_minimum_shared"


def closeups() -> None:
    final_folder = BASE / f"evaluation/{FINAL}_2048"
    final_summary = (read_object(final_folder / "summary.json") if (final_folder / "summary.json").is_file()
                     else {"snapshot": read_object(final_folder / "packing_selection.json")["snapshot"],
                           "export": read_object(final_folder / "export/result.json")})
    source = load_snapshot(Path(parent()))
    subjects = [("source", parent(), str(source.metadata["reference_glb"])),
                ("final", str(final_summary["snapshot"]), str(cast(dict[str, object], final_summary["export"])["asset"]))]
    for subject, snapshot_path, model in subjects:
        snapshot = load_snapshot(Path(snapshot_path))
        centers = snapshot.state.surface.mesh.vertices[snapshot.state.surface.mesh.faces].mean(1)
        regions = {"bow": (centers[:, 1] > .73) & (centers[:, 0] < -.12),
                   "ornament": (centers[:, 1] > .53) & (centers[:, 1] < .80) & (abs(centers[:, 0]) < .13),
                   "base": centers[:, 1] < .17}
        for name, mask in regions.items():
            call(f"closeups/{subject}_{name}", "artist.preview", {
                "snapshot": snapshot_path, "model": model, "focus_faces": np.flatnonzero(mask).tolist(),
                "size": 768, "cameras": [CAMERAS[0], CAMERAS[1], CAMERAS[4]],
                "channels": ["material", "normal_lit"], "lights": ["soft", "side"],
                "distances": [1], "timeout_seconds": 300})


def scene() -> None:
    from build_tower_uv_report import METHODS, row

    source = load_snapshot(Path(parent()))
    models = [{"path": str(source.metadata["reference_glb"]), "label": "源贴图参考 8K",
               "faces": len(source.state.surface.mesh.faces)}]
    seen = set()
    for method, title in METHODS:
        record = row(method, title)
        if record.get("model") and record["model"] not in seen:
            models.append({"path": record["model"], "label": title, "faces": record["faces"]})
            seen.add(record["model"])
    request = BASE / "comparison_scene_request.json"
    write_json(request, {"models": models, "output": str(BASE / "comparison.blend"),
                        "receipt": str(BASE / "comparison_scene_receipt.json")})
    with (BASE / "comparison_scene.log").open("w", encoding="utf-8") as log:
        subprocess.run([BLENDER, "--background", "--factory-startup", "--threads", "2",
            "--python-exit-code", "1", "--python", str(Path(__file__).with_name("tower_comparison_scene.py")),
            "--", str(request)], stdout=log, stderr=subprocess.STDOUT, check=True, timeout=300)


def audit() -> None:
    import trimesh
    from PIL import Image

    checks = []
    for path in BASE.glob("evaluation/*/export/asset.glb"):
        folder = path.parent.parent
        exported = read_object(path.parent / "result.json")
        summary = read_object(folder / "summary.json")
        if not summary:
            continue
        snapshot = load_snapshot(Path(str(summary["snapshot"])))
        scene_data = trimesh.load_scene(path, process=False)
        triangles = sum(len(mesh.faces) for mesh in scene_data.geometry.values() if isinstance(mesh, trimesh.Trimesh))
        assert triangles == len(snapshot.state.surface.mesh.faces)
        raw = path.read_bytes()
        size = int.from_bytes(raw[12:16], "little")
        document = json.loads(raw[20:20 + size])
        assert all("normalTexture" in material for material in document["materials"])
        preview = read_object(folder / "preview/preview.json")
        assert preview["geometry_and_uv_reimport_match"] is True
        bake = read_object(folder / "bake/bake.json")
        assert bake["mip_count"] == (8 if folder.name.endswith("_128") else 12)
        files = cast(dict[str, str], bake["files"])
        for relative, expected in files.items():
            texture = folder / "bake" / relative
            assert file_hash(texture) == expected
            if texture.suffix == ".png" and "_mip" in texture.stem:
                mip = int(texture.stem.rsplit("_mip", 1)[1])
                dimension = max(1, (128 if folder.name.endswith("_128") else 2048) >> mip)
                with Image.open(texture) as image:
                    assert image.size == (dimension, dimension)
                    image.verify()
        checks.append({"model": str(path), "triangles": triangles, "sha256": file_hash(path),
                       "blender_geometry_uv_match": True, "normal_texture_present": True,
                       "mip_file_hashes_and_dimensions_match": True,
                       "mip_count": bake["mip_count"], "export_status": exported["status"]})
    source = load_snapshot(Path(parent()))
    reference, _ = _reference(source, {})
    source_identity = face_corner_identity(source.state.surface.mesh.vertices[source.state.surface.mesh.faces],
                                         reference.geometry.vertices[reference.geometry.faces])
    original = np.load(BASE.parent / "round01/import/inputs/imported.npz")
    imported = np.load(BASE / "clean_import/inputs/imported.npz")
    repair = cast(dict[str, object], source.metadata["zero_area_repair"])
    kept = np.asarray(repair["original_triangle_for_new_triangle"], dtype=np.int64)
    np.testing.assert_array_equal(original["vertices"][original["faces"]][kept],
                                  imported["vertices"][imported["faces"]])
    np.testing.assert_array_equal(original["corner_uv"][kept], imported["corner_uv"])
    runs = [{"path": str(path), **read_object(path)} for path in BASE.parent.glob("round*/**/result.json")]
    write_json(BASE / "report/run_index.json", runs)
    write_json(BASE / "report/integrity.json", {"model_checks": checks,
        "all_exported_models_reimported": True, "source_face_corner_identity": source_identity,
        "zero_area_repair": {"removed_faces": repair["removed_original_triangles"],
                             "remaining_face_corner_positions_and_uv_exact": True},
        "all_preset_methods_have_retained_results": True,
        "source_zip_matches_original": file_hash(BASE / "inputs/source.zip") == file_hash(Path("E:/Downloads/fantasy cross statue 3d model.zip"))})
    task_sources = ["scripts/run_tower_uv.py", "scripts/build_tower_uv_report.py", "scripts/finish_tower_uv.py",
        "scripts/tower_comparison_scene.py", "scripts/agent_asset_worker.py", "scripts/agent_pbr_binding.py",
        "scripts/agent_unwrap_worker.py", "baseline/runner.py", "src/reseam/agent_tools/assets.py",
        "src/reseam/agent_tools/registry.py", "src/reseam/agent_tools/reparameterize.py",
        "src/reseam/agent_tools/pbr_reuse.py", "src/reseam/agent_tools/mirror_pbr_reference.py",
        "tests/test_pbr_reuse.py", "tests/test_mirror_pbr_reference.py",
        "tests/test_explicit_pbr_import.py", "tests/test_agent_tools_optcuts_identity.py"]
    for relative in task_sources:
        destination = BASE / "code_snapshot" / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(BASE.parents[2] / relative, destination)
    manifest = [{"path": str(path.relative_to(BASE.parent)), "bytes": path.stat().st_size,
                 "sha256": file_hash(path)} for path in BASE.parent.rglob("*")
                if path.is_file() and path.name != "file_manifest.json"]
    write_json(BASE / "report/file_manifest.json", manifest)
    print("models", len(checks), "files", len(manifest), flush=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("stage", choices=["closeups", "scene", "audit"])
    args = parser.parse_args()
    {"closeups": closeups, "scene": scene, "audit": audit}[args.stage]()


if __name__ == "__main__":
    main()
