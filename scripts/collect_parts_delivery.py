"""Collect reviewed BaseColor artifacts and a named-object copy, verifying every part."""

import argparse
import shutil
from pathlib import Path
from typing import cast

import numpy as np
import trimesh
from trimesh.visual.material import PBRMaterial
from trimesh.visual.texture import TextureVisuals

from reseam.agent_tools.state import file_hash, read_object, write_json


def collect(run: Path, parts_file: Path, output: Path) -> None:
    output.mkdir(parents=True, exist_ok=False)
    receipt = read_object(run / "visual_export/result.json")
    if receipt.get("visual_accepted") is not True:
        raise ValueError("PART_DELIVERY_REQUIRES_ACTUAL_VISUAL_RECEIPT")
    source = Path(str(receipt["model"]))
    if file_hash(source) != receipt["model_sha256"]:
        raise ValueError("PART_DELIVERY_REVIEWED_MODEL_CHANGED")
    model = trimesh.load(source, force="mesh", process=False)
    if not isinstance(model, trimesh.Trimesh):
        raise TypeError("PART_DELIVERY_TRIANGLES_REQUIRED")
    parts = cast(list[dict[str, object]], read_object(parts_file)["parts"])
    scene = trimesh.Scene()
    expected: dict[str, np.ndarray] = {}
    covered: list[int] = []
    visual = model.visual
    if not isinstance(visual, TextureVisuals) or not isinstance(visual.material, PBRMaterial):
        raise TypeError("PART_DELIVERY_TEXTURED_PBR_PREVIEW_REQUIRED")
    if visual.uv is None or visual.material.baseColorTexture is None:
        raise ValueError("PART_DELIVERY_BASECOLOR_AND_UV_REQUIRED")
    material = visual.material
    uv = np.asarray(visual.uv)
    for part in parts:
        faces = np.asarray(part["faces"], dtype=np.int64)
        points = model.vertices[model.faces[faces]]
        mesh = trimesh.Trimesh(vertices=points.reshape(-1, 3), faces=np.arange(len(faces)*3).reshape(-1, 3), process=False)
        mesh.visual = TextureVisuals(uv=uv[model.faces[faces]].reshape(-1, 2), material=material)
        name = str(part["id"])
        scene.add_geometry(mesh, node_name=name, geom_name=name)
        expected[name] = points
        covered.extend(map(int, faces))
    if sorted(covered) != list(range(len(model.faces))):
        raise ValueError("PART_DELIVERY_FACE_COVERAGE_MISMATCH")
    multi = output / "model-parts.glb"
    scene.export(multi, file_type="glb")
    reloaded = trimesh.load_scene(multi, process=False)
    for node in reloaded.graph.nodes_geometry:
        transform, name = reloaded.graph[node]
        geometry = reloaded.geometry[name]
        if not isinstance(geometry, trimesh.Trimesh):
            raise TypeError("PART_DELIVERY_REIMPORT_TRIANGLES_REQUIRED")
        xyz = trimesh.transform_points(geometry.vertices, transform)[geometry.faces]
        np.testing.assert_array_equal(xyz, expected[str(node)])
        original_part = next(part for part in parts if part["id"] == str(node))
        child_visual = geometry.visual
        if not isinstance(child_visual, TextureVisuals) or not isinstance(child_visual.material, PBRMaterial):
            raise TypeError("PART_DELIVERY_REIMPORT_MATERIAL_REQUIRED")
        np.testing.assert_array_equal(np.asarray(child_visual.uv)[geometry.faces],
                                      uv[model.faces[cast(list[int], original_part["faces"])]])
        np.testing.assert_array_equal(np.asarray(child_visual.material.baseColorTexture),
                                      np.asarray(material.baseColorTexture))
    if set(reloaded.graph.nodes_geometry) != set(expected):
        raise ValueError("PART_DELIVERY_REIMPORT_OBJECTS_MISMATCH")
    shutil.copy2(source, output / "model.glb")
    shutil.copytree(run / "visual_export/texture", output / "texture")
    shutil.copy2(parts_file, output / "parts.json")
    shutil.copy2(run / "mirror_shared2048/parts/uv.png", output / "uv.png")
    shutil.copy2(run / "mirror_shared2048/actual_views/front.png", output / "preview.png")
    shutil.copy2(run / "visual_keep/review.json", output / "visual-review.json")
    validation_path = run / "mirror_shared2048/validate/validation.json"
    validation = read_object(validation_path) if validation_path.is_file() else {}
    bake_path = run / "mirror_shared2048/bake/bake.json"
    bake = read_object(bake_path)
    numeric_accepted = (validation.get("valid") is True and validation.get("bake_sha256") == file_hash(bake_path)
                        and validation.get("state_hash") == bake.get("state_hash")
                        and validation.get("target_hash") == bake.get("target_hash"))
    if validation:
        shutil.copy2(validation_path, output / "numeric-validation.json")
    write_json(output / "verification.json", {"named_objects": len(expected), "faces": len(model.faces),
               "face_coverage_exact": True, "part_positions_uv_and_textures_reimported_exact": True,
               "visual_accepted": True, "reviewer": "agent", "numeric_accepted": numeric_accepted,
               "full_pbr_passed": False, "human_reviewed": False,
               "files": {str(path.relative_to(output)): file_hash(path) for path in sorted(output.rglob("*")) if path.is_file()}})


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--parts", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    collect(args.run.resolve(), args.parts.resolve(), args.output.resolve())


if __name__ == "__main__":
    main()
