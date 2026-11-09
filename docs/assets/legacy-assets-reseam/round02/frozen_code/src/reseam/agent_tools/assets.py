"""Asset ingestion and numbered CPU previews, independent of optional UV backends."""

import json
import os
import shutil
import struct
import subprocess
import time
from pathlib import Path
from typing import cast

import numpy as np
import trimesh
from PIL import Image
from trimesh.visual.material import PBRMaterial
from trimesh.visual.texture import TextureVisuals

from ..budget_state import BudgetState
from ..mesh import make_mesh, read_obj, state_from_corner_uv
from ..types import FloatArray, IntArray
from .extra_uv import append_extra_uv_glb, glb_with_uv_attributes, write_extra_uv
from .state import (
    check_deadline,
    file_hash,
    object_hash,
    save_snapshot,
    snapshot_result,
    write_json,
)


def _glb_arrays(
    path: Path, uv_channel: str | int | None = None, extra_layers: dict[str, FloatArray] | None = None,
) -> tuple[FloatArray, IntArray, FloatArray, IntArray, dict[str, object]]:
    data = path.read_bytes()
    if data[:4] != b"glTF" or len(data) < 20:
        raise ValueError("INVALID_GLB_HEADER")
    json_size = struct.unpack_from("<I", data, 12)[0]
    document = json.loads(data[20:20 + json_size])
    if document.get("animations") or document.get("skins") or any(
        primitive.get("targets") for mesh in document.get("meshes", []) for primitive in mesh.get("primitives", [])
    ):
        raise ValueError("UNSUPPORTED_DYNAMIC_ASSET: static triangles required")
    if any(item.get("uri") for category in ("images", "buffers") for item in document.get(category, [])):
        raise ValueError("UNSUPPORTED_EXTERNAL_GLB_RESOURCES: embedded GLB required")
    primitives = [p for m in document.get("meshes", []) for p in m.get("primitives", [])]
    if any(p.get("mode", 4) != 4 for p in primitives):
        raise ValueError("UNSUPPORTED_PRIMITIVE_MODE: explicit triangles required")
    presence = ["TEXCOORD_0" in p.get("attributes", {}) for p in primitives]
    if any(presence) and not all(presence):
        raise ValueError("UNSUPPORTED_MIXED_UV_PRESENCE: preserve authored UV; initialize missing objects separately")
    scene = trimesh.load_scene(glb_with_uv_attributes(path, uv_channel), file_type="glb", process=False)
    vertices, faces, corners, identities = [], [], [], []
    objects: list[dict[str, object]] = []
    vertex_offset = 0
    face_offset = 0
    all_uv = True
    extra_parts: dict[str, list[FloatArray]] = {}
    extra_names: set[str] | None = None
    authored_names = document.get("extras", {}).get("reseam_extra_uv", {})
    for node in sorted(scene.graph.nodes_geometry):
        transform, name = scene.graph[node]
        geometry = scene.geometry[name]
        if not isinstance(geometry, trimesh.Trimesh):
            continue
        xyz = trimesh.transform_points(geometry.vertices, transform)
        tri = np.asarray(geometry.faces, dtype=np.int64).copy()
        if np.linalg.det(transform[:3, :3]) < 0:
            tri = tri[:, [0, 2, 1]]
        uv = getattr(geometry.visual, "uv", None)
        primary = geometry.vertex_attributes.get("_RESEAM_PRIMARY_UV")
        if primary is not None:
            uv = np.asarray(primary, dtype=float).copy()
            uv[:, 1] = 1. - uv[:, 1]
        all_uv &= uv is not None
        corners.append(np.zeros((*tri.shape, 2)) if uv is None else np.asarray(uv, dtype=float)[tri])
        vertices.append(xyz)
        faces.append(tri + vertex_offset)
        identities.append(tri + vertex_offset)
        layer_keys = {key for key in geometry.vertex_attributes if key.startswith("_RESEAM_TEXCOORD_")}
        if extra_names is not None and layer_keys != extra_names:
            raise ValueError("UNSUPPORTED_MIXED_EXTRA_UV_LAYOUT")
        extra_names = layer_keys
        for key in sorted(layer_keys):
            values = np.asarray(geometry.vertex_attributes[key], dtype=float)[tri].copy()
            values[..., 1] = 1. - values[..., 1]
            attribute = key.removeprefix("_RESEAM_")
            name = str(authored_names.get(attribute, attribute))
            extra_parts.setdefault(name, []).append(values)
        objects.append({"node": node, "geometry": name, "face_start": face_offset,
                        "face_count": len(tri), "vertex_start": vertex_offset,
                        "transform": np.asarray(transform).tolist(), "source_uv": uv is not None})
        vertex_offset += len(xyz)
        face_offset += len(tri)
    if not faces:
        raise ValueError("NO_TRIANGLE_GEOMETRY")
    if extra_layers is not None:
        extra_layers.update({name: np.concatenate(parts) for name, parts in extra_parts.items()})
    return (np.concatenate(vertices), np.concatenate(faces), np.concatenate(corners),
            np.concatenate(identities), {"objects": objects, "source_uv_present": all_uv})


def _initialize_uv(vertices: FloatArray, faces: IntArray) -> tuple[FloatArray, IntArray]:
    import xatlas

    atlas = xatlas.Atlas()
    atlas.add_mesh(vertices.astype(np.float32), faces.astype(np.uint32))
    atlas.generate()
    mapping, indices, uv = atlas[0]
    if not np.array_equal(np.asarray(mapping)[np.asarray(indices)], faces):
        raise ValueError("XATLAS_ORIGINAL_FACE_IDENTITY_CHANGED")
    coords = np.asarray(uv, dtype=float)
    # Preserve xatlas' pixel aspect ratio before fitting into a square domain.
    coords *= np.array([atlas.width, atlas.height]) / max(atlas.width, atlas.height)
    return coords[np.asarray(indices, dtype=np.int64)], np.asarray(indices, dtype=np.int64)


def import_asset(request: dict[str, object], output: Path, deadline: float) -> dict[str, object]:
    check_deadline(deadline)
    raw = request.get("path")
    if not isinstance(raw, str):
        raise TypeError("ASSET_PATH_REQUIRED")
    source = Path(raw).resolve()
    suffix = source.suffix.lower()
    uv_channel = request.get("uv_channel")
    if (uv_channel is not None and (type(uv_channel) not in (int, str)
            or isinstance(uv_channel, int) and uv_channel < 0
            or isinstance(uv_channel, str) and not uv_channel)):
        raise ValueError("UV_CHANNEL_MUST_BE_NONNEGATIVE_INDEX_OR_EXACT_NAME")
    selected_uv = cast(str | int | None, uv_channel)
    texture = request.get("basecolor_texture")
    texture_source: Path | None = None
    if texture is not None:
        if not isinstance(texture, str) or not Path(texture).is_absolute():
            raise ValueError("BASECOLOR_TEXTURE_MUST_BE_ABSOLUTE_PATH")
        if selected_uv is None:
            raise ValueError("EXPLICIT_BASECOLOR_REQUIRES_UV_CHANNEL")
        if request.get("reference") is not None:
            raise ValueError("EXPLICIT_BASECOLOR_CONFLICTS_WITH_REFERENCE")
        texture_source = Path(texture).resolve()
        with Image.open(texture_source) as image:
            image.load()
            if image.mode not in ("RGB", "RGBA"):
                raise ValueError("BASECOLOR_TEXTURE_REQUIRES_RGB_OR_RGBA")
    restore_requested = any(key in request for key in (
        "original_topology", "restore_regions", "source_parent_snapshot",
    ))
    if restore_requested and (suffix != ".glb" or request.get("repair_degenerate_faces", False)
                              or request.get("initialize_uv") is not None):
        raise ValueError("TOPOLOGY_RESTORE_REQUIRES_AUTHORED_GLB_UV_WITHOUT_OTHER_IMPORT_REPAIRS")
    if suffix not in {".obj", ".glb", ".fbx"}:
        raise ValueError("UNSUPPORTED_ASSET_FORMAT: GLB, OBJ or FBX required")
    frozen = output / "inputs"
    frozen.mkdir()
    copied = frozen / source.name
    shutil.copy2(source, copied)
    sources: list[dict[str, object]] = [
        {"path": str(copied), "original_path": str(source), "sha256": file_hash(copied)}
    ]
    frozen_texture: Path | None = None
    if texture_source is not None:
        (frozen / "basecolor").mkdir()
        frozen_texture = frozen / "basecolor" / texture_source.name
        shutil.copy2(texture_source, frozen_texture)
        sources.append({"path": str(frozen_texture.resolve()), "original_path": str(texture_source),
                        "sha256": file_hash(frozen_texture)})
    if suffix == ".fbx":
        sidecar = source.with_suffix(".fbm")
        if sidecar.is_dir():
            shutil.copytree(sidecar, frozen / sidecar.name)
            sources.extend({"path": str(path), "original_path": str(sidecar / path.relative_to(frozen / sidecar.name)),
                            "sha256": file_hash(path)}
                           for path in sorted((frozen / sidecar.name).rglob("*")) if path.is_file())
    reference = request.get("reference")
    if reference is not None and not isinstance(reference, str):
        raise ValueError("REFERENCE_PATH_MUST_BE_STRING")
    metadata: dict[str, object]
    extra_layers: dict[str, FloatArray] = {}
    polygon_obj = suffix == ".obj" and any(
        len(row) != 4 for line in copied.read_text(encoding="utf-8-sig").splitlines()
        if (row := line.split()) and row[0] == "f"
    )
    if suffix == ".obj":
        uv_presence = [
            len(parts := token.split("/")) > 1 and bool(parts[1])
            for line in copied.read_text(encoding="utf-8-sig").splitlines()
            if line.split() and line.split()[0] == "f"
            for token in line.split()[1:]
        ]
        if any(uv_presence) and not all(uv_presence):
            raise ValueError("UNSUPPORTED_MIXED_UV_PRESENCE: preserve authored OBJ UV")
    if suffix == ".obj" and not polygon_obj:
        if selected_uv not in (None, 0, "TEXCOORD_0"):
            raise ValueError("OBJ_UV_CHANNEL_MUST_BE_ZERO_OR_TEXCOORD_0")
        vertices, faces, uv, uv_faces = read_obj(copied)
        present = uv_faces.shape == faces.shape and len(uv) > 0
        corners = uv[uv_faces] if present else np.zeros((*faces.shape, 2))
        ids = uv_faces if present else faces.copy()
        metadata = {"source_uv_present": present, "objects": [{"node": source.stem, "face_start": 0,
                     "face_count": len(faces)}], "obj_material_policy": "explicit reference GLB required"}
    elif suffix == ".glb":
        vertices, faces, corners, ids, metadata = _glb_arrays(copied, selected_uv, extra_layers)
        if reference is None and frozen_texture is None:
            if selected_uv not in (None, 0, "TEXCOORD_0"):
                selected_reference = frozen / "selected_reference.glb"
                selected_reference.write_bytes(glb_with_uv_attributes(copied, selected_uv).getvalue())
                reference = str(selected_reference)
            else:
                reference = str(copied)
    else:
        blender = Path(str(request.get("blender", "E:/Blender 4.5.11/blender.exe")))
        worker = Path(__file__).resolve().parents[3] / "scripts/agent_asset_worker.py"
        if not blender.is_file():
            raise FileNotFoundError("BLENDER_MISSING")
        worker_input = output / "import_request.json"
        write_json(worker_input, {"source": str(copied), "output": str(frozen), "uv_channel": selected_uv,
                                  "basecolor_texture": str(frozen_texture) if frozen_texture else None})
        argv = [str(blender), "--background", "--factory-startup", "--threads", "1",
                "--python-exit-code", "1", "--python", str(worker), "--", str(worker_input)]
        with (output / "import.log").open("w", encoding="utf-8") as log:
            completed = subprocess.run(argv, stdout=log, stderr=subprocess.STDOUT,
                                       timeout=max(0.01, deadline - time.monotonic()), check=False,
                                       creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        if completed.returncode:
            details = (output / "import.log").read_text(encoding="utf-8", errors="replace")
            reasons = [line for line in details.splitlines() if "Error:" in line]
            raise ValueError("FBX_IMPORT_REJECTED: " + (" | ".join(reasons) or f"exit {completed.returncode}; see import.log"))
        metadata = json.loads((frozen / "identity.json").read_text(encoding="utf-8"))
        with np.load(frozen / "imported.npz", allow_pickle=False) as data:
            vertices, faces = data["vertices"].copy(), data["faces"].copy()
            corners, ids = data["corner_uv"].copy(), data["corner_ids"].copy()
            extra_layers = {name: data[f"extra_uv_{index}"].copy()
                            for index, name in enumerate(cast(list[str], metadata.pop("extra_uv_names", [])))}
        sources.extend({"path": str(p), "sha256": file_hash(p)} for p in
                       (frozen / "imported.npz", frozen / "identity.json", frozen / "reference.glb") if p.is_file())
        if reference is None and frozen_texture is None:
            reference = str(frozen / "reference.glb")
    if "polygon_topology" not in metadata:
        metadata["polygon_topology"] = {"polygons": faces.tolist(),
            "triangle_to_polygon": list(range(len(faces))),
            "source": "input explicit triangles; no inferred quads"}
    mesh = make_mesh(vertices, faces)
    if not metadata["source_uv_present"]:
        if request.get("initialize_uv") != "xatlas":
            raise ValueError("UV_MISSING: explicitly request initialize_uv=xatlas")
        corners, ids = _initialize_uv(vertices, faces)
        metadata["uv_initialization"] = "xatlas"
    else:
        metadata["uv_initialization"] = "preserved_input_uv"
    repaired: list[int] = []
    repair = request.get("repair_degenerate_faces", False)
    if not isinstance(repair, bool):
        raise TypeError("BOOLEAN_REQUIRED:repair_degenerate_faces")
    if repair:
        # xatlas can return (0,0) for every corner of a tiny 3D triangle. Keep
        # that original face and isolate an intrinsic, isometric UV triangle.
        delta = corners[:, 1:] - corners[:, :1]
        determinant = delta[:, 0, 0] * delta[:, 1, 1] - delta[:, 0, 1] * delta[:, 1, 0]
        for face_id in np.flatnonzero(np.abs(determinant) <= 1e-16):
            triangle = vertices[faces[face_id]]
            first, second = triangle[1] - triangle[0], triangle[2] - triangle[0]
            length = float(np.linalg.norm(first))
            intrinsic = np.array([[0., 0.], [length, 0.],
                                  [float(first @ second) / length, float(np.linalg.norm(np.cross(first, second))) / length]])
            corners[face_id] = intrinsic / mesh.scale + np.array([1.1 + len(repaired), 0.])
            ids[face_id] = np.arange(3) + int(ids.max()) + 1
            repaired.append(int(face_id))
        metadata["uv_repair"] = {"faces": repaired, "method": "explicit intrinsic whole-face isolation",
                                  "requires_pack": bool(repaired)}
    surface = state_from_corner_uv(mesh, corners, corner_ids=ids)
    if frozen_texture is not None:
        # One explicit static material with exact imported face/corner ancestry.
        # Source Unity shader transforms, blending, and animation are not inferred.
        static = trimesh.Trimesh(vertices=vertices[faces].reshape(-1, 3),
                                 faces=np.arange(len(faces) * 3).reshape(-1, 3), process=False)
        with Image.open(frozen_texture) as image:
            static.visual = TextureVisuals(uv=corners.reshape(-1, 2),
                material=PBRMaterial(baseColorTexture=image.copy(),
                    metallicFactor=0., roughnessFactor=1., doubleSided=True))
        reference = str(frozen / "explicit_reference.glb")
        static.export(reference, file_type="glb")
        metadata["static_color_binding"] = {"source_texture": str(texture_source),
            "frozen_texture": str(frozen_texture.resolve()), "texture_sha256": file_hash(frozen_texture),
            "uv_channel": selected_uv, "shader": "explicit standard static BaseColor",
            "unity_material_reproduced": False, "source_shader_reproduced": False,
            "scope": "RGB/RGBA image + chosen UV only; Unity UV transforms, animation, blending unverified"}
    restoration: dict[str, object] | None = None
    if restore_requested:
        from .topology_restore import restore_import_topology

        surface, restoration, original_source = restore_import_topology(surface, request, frozen, deadline)
        sources.append(original_source)
        metadata["topology_restoration"] = restoration
        # Restored vertex indices are different from the importer namespace.
        # Original quad provenance cannot be asserted without a separate map.
        metadata["polygon_topology"] = {"polygons": surface.mesh.faces.tolist(),
            "triangle_to_polygon": list(range(len(surface.mesh.faces))),
            "source": "restored explicit triangles; source polygon topology not transferred"}
    if reference is not None:
        ref = Path(reference).resolve()
        if ref.suffix.lower() != ".glb":
            raise ValueError("MATERIAL_REFERENCE_MUST_BE_GLB")
        destination = frozen / "material_reference.glb"
        if ref != destination.resolve():
            shutil.copy2(ref, destination)
        sources.append({"path": str(destination), "original_path": str(ref), "sha256": file_hash(destination)})
        metadata["reference_glb"] = str(destination)
    metadata.update({"source_manifest": sources, "source_path": str(source),
                     "asset_id": object_hash({"geometry": sources[0]["sha256"],
                                               "reference": sources[-1]["sha256"]}),
                     "normal_policy": "explicit_flat_target_with_original_source_shading",
                     "identity_policy": ("per-object uv_identity records: verified raw FBX UVIndex or conservative loops" if suffix == ".fbx"
                                         else "source indices; no coordinate proximity welding"),
                     "active_uv_channel": selected_uv if selected_uv is not None else (
                         "per-object active layer in metadata" if suffix == ".fbx" else 0)})
    if frozen_texture is not None:
        metadata["normal_policy"] = "explicit static BaseColor reference; source normals not reproduced"
    write_extra_uv(metadata, frozen, extra_layers)
    if frozen_texture is not None:
        append_extra_uv_glb(Path(str(metadata["reference_glb"])), metadata, len(faces))
        # The immutable source manifest hashes the final file, including auxiliary UVs.
        for item in sources:
            if item["path"] == metadata["reference_glb"]:
                item["sha256"] = file_hash(Path(str(item["path"])))
        metadata["asset_id"] = object_hash({"geometry": sources[0]["sha256"],
            "texture": file_hash(frozen_texture), "uv_channel": selected_uv,
            "reference": file_hash(Path(str(metadata["reference_glb"])))})
    if restoration is not None:
        metadata["asset_id"] = object_hash({
            "source_asset_id": metadata["asset_id"],
            "source_parent_state_hash": restoration["source_parent_state_hash"],
            "geometry_representation_hash": restoration["geometry_representation_hash"],
            "restoration_config_hash": restoration["restoration_config_hash"],
            "original_topology_sha256": restoration["original_topology_sha256"],
        })
        metadata["identity_policy"] = "verified original vertex IDs within selected regions; exact face corners"
    check_deadline(deadline)
    snapshot = save_snapshot(BudgetState(surface), output / "snapshot", metadata)
    if restoration is not None:
        write_json(output / "topology_restoration.json", restoration)
    return {**snapshot_result(snapshot), "faces": len(surface.mesh.faces), "vertices": len(surface.mesh.vertices),
            "source_uv_preserved": bool(metadata["source_uv_present"]) and not repaired,
            "material_validation": "NOT_RUN", "geometry_passed": None,
            **({"topology_restoration": str((output / "topology_restoration.json").resolve())}
               if restoration is not None else {})}


def render_views(request: dict[str, object], output: Path, deadline: float) -> dict[str, object]:
    """Keep the existing entry point while isolating diagnostic rasterization."""
    from .views import render_views as render
    return render(request, output, deadline)
