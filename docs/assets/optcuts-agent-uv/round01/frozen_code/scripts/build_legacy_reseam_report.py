"""Package retained three-asset tool evidence; never run a solver or a bake.

Every build uses a new directory and rereads the current evaluation/review files.
The package is a static inspection deliverable, not an engine acceptance result.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import cast

import numpy as np
from numpy.typing import NDArray
from PIL import Image, ImageDraw, ImageFont

from reseam.agent_tools.extra_uv import load_extra_uv, read_glb
from reseam.agent_tools.state import file_hash, load_snapshot, read_object, write_json

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ("chloe", "energy_blade", "dragon")
NAMES = {"chloe": "Chloe 刺模型", "energy_blade": "历史能量刀", "dragon": "历史龙"}
METHODS = {"source_uv": "原作者 UV", "xatlas": "xatlas 初始化", "optcuts": "OptCuts 无共享",
           "fixed": "固定几何共享尝试", "mirror_shared": "ReSeam 镜像共享",
           "mirror_unshared": "同镜像几何无共享", "mirror_shared_final": "ReSeam 最终镜像共享",
           "mirror_unshared_final": "最终同镜像几何无共享"}
FONT = Path("C:/Windows/Fonts/msyh.ttc")
SUCCESS = {"OK", "BAKED", "VALIDATED", "EXPORTED", "ACCEPTED", "SUCCESS"}
SCOPE = "静态 CPU UV＋BaseColor；验证 LOD 0/1/2；完整 PBR、GPU、Unity 动态 Shader 与人工验收均独立"


def mapping(value: object) -> dict[str, object]:
    return cast(dict[str, object], value) if isinstance(value, dict) else {}


def sequence(value: object) -> list[object]:
    return cast(list[object], value) if isinstance(value, list) else []


def number(value: object) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def display(value: object, *, percent: bool = False) -> str:
    numeric = number(value)
    if numeric is None:
        return "未测" if value is None else str(value)
    if percent:
        return f"{numeric * 100:.2f}%"
    return str(int(numeric)) if numeric.is_integer() else f"{numeric:.4g}"


def read_optional(path: Path) -> dict[str, object]:
    return read_object(path) if path.is_file() else {}


def snapshot_geometry(path: Path) -> dict[str, object]:
    snapshot = load_snapshot(path)
    surface = snapshot.state.surface
    return {"faces": len(surface.mesh.faces), "vertices": len(surface.mesh.vertices),
            "geometry_hash": read_object(path)["geometry_hash"], "state_hash": snapshot.state_hash,
            "bounds": [surface.mesh.vertices.min(axis=0).tolist(), surface.mesh.vertices.max(axis=0).tolist()],
            "snapshot": str(path)}


def flatten(value: object, prefix: str = "") -> dict[str, object]:
    result: dict[str, object] = {}
    if isinstance(value, dict):
        for key, child in mapping(value).items():
            result.update(flatten(child, f"{prefix}.{key}" if prefix else key))
    elif isinstance(value, list):
        for index, child in enumerate(sequence(value)):
            result.update(flatten(child, f"{prefix}[{index}]"))
    else:
        result[prefix] = value
    return result


def write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    keys = list(dict.fromkeys(key for row in rows for key in row))
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=keys)
        writer.writeheader()
        writer.writerows(rows)


@dataclass
class Package:
    root: Path
    experiment: Path
    sources: dict[str, str] = field(default_factory=dict)

    def copy(self, source: Path, destination: str | Path | None = None) -> Path:
        source = source.resolve()
        relative = Path(destination) if destination is not None else Path("evidence") / source.relative_to(
            self.experiment)
        target = self.root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        digest = file_hash(source)
        if target.exists():
            if file_hash(target) != digest:
                raise ValueError(f"PACKAGE_PATH_COLLISION:{relative}")
        else:
            shutil.copy2(source, target)
        if file_hash(target) != digest:
            raise ValueError(f"PACKAGE_COPY_HASH_MISMATCH:{relative}")
        self.sources[relative.as_posix()] = str(source)
        return target

    def relative(self, path: Path) -> str:
        return path.relative_to(self.root).as_posix()

    def retain_tree(self, source: Path, destination: Path) -> None:
        if source.is_dir():
            for item in sorted(source.rglob("*")):
                if item.is_file():
                    self.copy(item, destination / item.relative_to(source))

    def evidence(self, path: Path) -> str:
        return self.relative(self.copy(path))


def tracked_fingerprint(root: Path) -> dict[str, str]:
    paths = [root / "protocol.json", root / "calls.json"]
    paths += [root / asset / name for asset in ASSETS for name in ("states.json", "evaluations.json")]
    paths += list(root.glob("**/appearance_review.json"))
    return {path.relative_to(root).as_posix(): file_hash(path) for path in sorted(paths) if path.is_file()}


def audit_call_ledger(root: Path) -> dict[str, object]:
    calls = [mapping(item) for item in sequence(read_optional(root / "calls.json").get("calls"))]
    keys = [(str(call.get("asset")), str(call.get("label"))) for call in calls]
    known = set(keys)
    completed: set[tuple[str, str]] = set()
    pending: list[str] = []
    for asset in ASSETS:
        for command in (root / asset).rglob("command.json"):
            folder = command.parent
            if not (folder / "provenance.json").is_file() or not (folder / "request.json").is_file():
                continue
            label = folder.relative_to(root / asset).as_posix()
            if (folder / "result.json").is_file():
                completed.add((asset, label))
            else:
                pending.append(f"{asset}/{label}")
    missing = [f"{asset}/{label}" for asset, label in sorted(completed - known)]
    invalid_costs = [f"{call.get('asset')}/{call.get('label')}" for call in calls
                     if number(call.get("seconds")) is None or float(str(call["seconds"])) < 0]
    return {"valid": not missing and not pending and len(known) == len(keys) and not invalid_costs,
            "executed_result_directories": len(completed), "ledger_entries": len(calls),
            "missing_ledger_records": missing, "pending_tool_directories": pending,
            "duplicate_entries": len(keys) - len(known), "invalid_cost_records": invalid_costs}


def freeze_originals(root: Path) -> list[dict[str, object]]:
    """Compare live originals and frozen inputs to the import-time hashes."""
    records: list[dict[str, object]] = []
    for asset in ASSETS:
        state = read_object(root / asset / "states.json")
        snapshot = load_snapshot(Path(str(mapping(state["import"])["snapshot"])))
        for item in sequence(snapshot.metadata.get("source_manifest")):
            record = mapping(item)
            frozen = Path(str(record["path"]))
            original = Path(str(record.get("original_path", frozen)))
            expected = str(record["sha256"])
            actual, copied = file_hash(original), file_hash(frozen)
            generated_precursor = original != frozen and original.is_relative_to(root)
            records.append({"asset": asset, "original": str(original), "frozen": str(frozen),
                            "import_sha256": expected, "before_sha256": actual,
                            "frozen_sha256": copied, "before_matches_import": actual == expected == copied,
                            "comparison_required": not generated_precursor,
                            "frozen_matches_import": copied == expected,
                            "kind": "generated_reference_precursor" if generated_precursor else
                            "original_input" if "original_path" in record else "frozen_import_evidence"})
    manifest = read_object(ROOT / "assets/v1/manifest.json")
    for model in sequence(manifest["models"]):
        for item in sequence(mapping(model)["files"]):
            record = mapping(item)
            path = ROOT / "assets/v1" / str(record["path"])
            records.append({"asset": mapping(model)["id"], "original": str(path),
                            "import_sha256": record["sha256"], "before_sha256": file_hash(path),
                            "before_matches_import": file_hash(path) == record["sha256"],
                            "comparison_required": True, "kind": "assets_v1_manifest"})
    if not all(record["before_matches_import"] is True or
               (record.get("comparison_required") is False and record.get("frozen_matches_import") is True)
               for record in records):
        raise ValueError("ORIGINAL_INPUT_OR_MANIFEST_HASH_MISMATCH")
    return records


def finish_originals(records: list[dict[str, object]]) -> None:
    for record in records:
        record["after_sha256"] = file_hash(Path(str(record["original"])))
        record["unchanged_during_report"] = record["after_sha256"] == record["before_sha256"]
    if not all(record["unchanged_during_report"] is True for record in records):
        raise ValueError("ORIGINAL_CHANGED_DURING_REPORT")


def glb_accessor(document: dict[str, object], binary: bytes, index: int) -> NDArray[np.float64]:
    accessor = mapping(sequence(document["accessors"])[index])
    if accessor.get("sparse"):
        raise ValueError("AUDIT_SPARSE_ACCESSOR_UNSUPPORTED")
    view = mapping(sequence(document["bufferViews"])[int(str(accessor["bufferView"]))])
    dtype = np.dtype({5120: "i1", 5121: "u1", 5122: "<i2", 5123: "<u2",
                      5125: "<u4", 5126: "<f4"}[int(str(accessor["componentType"]))])
    width = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4}[str(accessor["type"])]
    offset = int(str(view.get("byteOffset", 0))) + int(str(accessor.get("byteOffset", 0)))
    stride = int(str(view.get("byteStride", dtype.itemsize * width)))
    count = int(str(accessor["count"]))
    end = offset + max(0, count - 1) * stride + width * dtype.itemsize
    view_end = int(str(view.get("byteOffset", 0))) + int(str(view["byteLength"]))
    if offset < 0 or end > len(binary) or end > view_end:
        raise ValueError("AUDIT_ACCESSOR_BOUNDS")
    return np.ndarray((count, width), dtype=dtype, buffer=binary, offset=offset,
                      strides=(stride, dtype.itemsize)).astype(np.float64)


def embedded_basecolor(document: dict[str, object], binary: bytes, primitive: dict[str, object]) -> bytes:
    material = mapping(sequence(document["materials"])[int(str(primitive.get("material", 0)))])
    texture_index = mapping(mapping(material["pbrMetallicRoughness"])["baseColorTexture"])["index"]
    texture = mapping(sequence(document["textures"])[int(str(texture_index))])
    image = mapping(sequence(document["images"])[int(str(texture["source"]))])
    if "bufferView" not in image:
        raise ValueError("AUDIT_EXTERNAL_BASECOLOR")
    view = mapping(sequence(document["bufferViews"])[int(str(image["bufferView"]))])
    offset, count = int(str(view.get("byteOffset", 0))), int(str(view["byteLength"]))
    return binary[offset:offset + count]


def audit_glb(path: Path, snapshot_path: Path, texture: Path | None, *, require_uv1: bool) -> dict[str, object]:
    """Read raw GLB indices; do not weld coordinates or remap faces with trimesh."""
    snapshot = load_snapshot(snapshot_path)
    document_raw, binary = read_glb(path)
    document = cast(dict[str, object], document_raw)
    primitives = [mapping(item) for mesh in sequence(document["meshes"])
                  for item in sequence(mapping(mesh)["primitives"])]
    if len(primitives) != 1:
        raise ValueError("AUDIT_EXPECTS_ONE_ORDERED_PRIMITIVE")
    primitive = primitives[0]
    if primitive.get("mode", 4) != 4:
        raise ValueError("AUDIT_EXPECTS_TRIANGLES")
    attributes = mapping(primitive["attributes"])
    positions = glb_accessor(document, binary, int(str(attributes["POSITION"])))
    indices = (glb_accessor(document, binary, int(str(primitive["indices"]))).ravel().astype(np.int64)
               if "indices" in primitive else np.arange(len(positions)))
    surface = snapshot.state.surface
    expected_positions = surface.mesh.vertices[surface.mesh.faces].reshape(-1, 3)
    expected_uv = surface.uv[surface.uv_faces].reshape(-1, 2)
    actual_positions = positions[indices]
    uv = glb_accessor(document, binary, int(str(attributes["TEXCOORD_0"])))[indices]
    uv[:, 1] = 1. - uv[:, 1]
    same_shape = actual_positions.shape == expected_positions.shape and uv.shape == expected_uv.shape
    position_error = float(np.max(np.abs(actual_positions - expected_positions))) if same_shape else None
    uv_error = float(np.max(np.abs(uv - expected_uv))) if same_shape else None
    # The export format stores float32. Bounds include one float32 round-trip in V.
    geometry_ok = same_shape and np.allclose(actual_positions, expected_positions, rtol=0, atol=2e-6)
    uv_ok = same_shape and np.allclose(uv, expected_uv, rtol=0, atol=2e-7)
    extra = load_extra_uv(snapshot.metadata, len(surface.mesh.faces))
    extra_audits: list[dict[str, object]] = []
    for index, (name, expected) in enumerate(extra.items(), 1):
        attribute = f"TEXCOORD_{index}"
        if attribute not in attributes:
            extra_audits.append({"attribute": attribute, "name": name, "passed": False,
                                 "reason": "MISSING_ATTRIBUTE"})
            continue
        values = glb_accessor(document, binary, int(str(attributes[attribute])))[indices]
        values[:, 1] = 1. - values[:, 1]
        expected_flat = expected.reshape(-1, 2)
        error = float(np.max(np.abs(values - expected_flat))) if values.shape == expected_flat.shape else None
        extra_audits.append({"attribute": attribute, "name": name, "max_abs_error": error,
                             "passed": error is not None and error <= 2e-7})
    image_bytes = embedded_basecolor(document, binary, primitive)
    with Image.open(io.BytesIO(image_bytes)) as image:
        pixels = np.asarray(image.convert("RGBA"))
        image_size = list(image.size)
    texture_ok: bool | None = None
    if texture is not None:
        with Image.open(texture) as image:
            texture_ok = np.array_equal(pixels, np.asarray(image.convert("RGBA")))
    extra_ok = all(record["passed"] is True for record in extra_audits)
    required_ok = not require_uv1 or ("TEXCOORD_1" in attributes and bool(extra_audits))
    passed = bool(geometry_ok and uv_ok and extra_ok and required_ok and texture_ok is not False)
    return {"asset": str(path), "snapshot": str(snapshot_path), "state_hash": snapshot.state_hash,
            "asset_sha256": file_hash(path), "geometry_hash": read_object(snapshot_path)["geometry_hash"],
            "faces": len(surface.mesh.faces), "positions_max_abs_error": position_error,
            "corner_uv0_max_abs_error": uv_error, "geometry_passed": bool(geometry_ok),
            "corner_uv0_passed": bool(uv_ok), "attributes": list(attributes), "extra_uv": extra_audits,
            "required_texcoord_1": require_uv1, "embedded_basecolor_size": image_size,
            "embedded_basecolor_pixel_equal_to_mip0": texture_ok, "passed": passed,
            "scope": "raw GLB accessor bounds, ordered indexed face corners, float32 coordinates and UVs, "
                     "hash-bound auxiliary UV sidecar, embedded BaseColor pixels; no engine/GPU/Shader test"}


@dataclass
class Candidate:
    asset: str
    row: dict[str, object]
    metrics: dict[str, object]
    geometry: dict[str, object]
    validation: dict[str, object]
    bake_metadata: dict[str, object] = field(default_factory=dict)
    exported: Path | None = None
    audit: dict[str, object] = field(default_factory=dict)
    geometry_summary: dict[str, object] = field(default_factory=dict)

    @property
    def key(self) -> str:
        return str(self.row["key"])

    @property
    def method(self) -> str:
        return str(self.row["method"])

    @property
    def resolution(self) -> int:
        return int(str(self.row["resolution"]))

    def numeric_row(self) -> dict[str, object]:
        inspection = mapping(self.row.get("inspection"))
        bake = mapping(self.row.get("bake"))
        validation = mapping(self.row.get("validation"))
        exported = mapping(self.row.get("export"))
        return {"asset": self.asset, "key": self.key, "method": self.method, "resolution": self.resolution,
                "status": self.row.get("status"), "chart_count": inspection.get("chart_count"),
                "faces": self.geometry_summary.get("faces"), "vertices": self.geometry_summary.get("vertices"),
                "geometry_hash": self.geometry_summary.get("geometry_hash"),
                "independent_tiles": self.metrics.get("actual_independent_tiles"),
                "packing_owners": self.metrics.get("packing_owners"),
                "shared_groups": inspection.get("shared_group_count"),
                "shared_surface_fraction": mapping(self.metrics.get("global")).get("actual_shared_surface_fraction"),
                "unique_coverage": inspection.get("unique_footprint"),
                "metrics_verified": self.metrics.get("metrics_verified"),
                "geometry_passed": validation.get("geometry_valid", inspection.get("geometry_passed")),
                "material_passed": validation.get("material_valid"), "render_passed": validation.get("render_valid"),
                "seams_passed": validation.get("seams_valid"), "quality_accepted": validation.get("quality_accepted"),
                "export_mode": "accepted" if exported.get("quality_accepted") else "diagnostic" if self.exported else "not_exported",
                "mip_payload_bytes": bake.get("payload_bytes"),
                "texture_file_bytes": self.bake_metadata.get("package_bytes"),
                "validated_mips": json.dumps(mapping(self.validation.get("profile")).get("mip_levels")),
                "glb_bytes": self.exported.stat().st_size if self.exported else None,
                "independent_reimport_passed": self.audit.get("passed"),
                "state_hash": inspection.get("state_hash"), "target": self.row.get("target")}


def collect_candidates(root: Path, asset: str) -> list[Candidate]:
    candidates: list[Candidate] = []
    for item in sequence(read_object(root / asset / "evaluations.json")["rows"]):
        row = mapping(item)
        inspection = mapping(row.get("inspection"))
        report = read_optional(Path(str(inspection.get("report", "__missing__"))))
        metrics = read_optional(Path(str(inspection.get("metrics_report", "__missing__"))))
        validation = read_optional(Path(str(mapping(row.get("validation")).get("validation", "__missing__"))))
        export_path = mapping(row.get("export")).get("asset")
        exported = Path(str(export_path)) if export_path else None
        bake_metadata = read_optional(Path(str(mapping(row.get("bake")).get("bake", "__missing__"))))
        candidate = Candidate(asset, row, metrics, mapping(report.get("geometry")), validation, bake_metadata, exported)
        snapshot_path = Path(str(row.get("snapshot", row.get("source_snapshot", "__missing__"))))
        if snapshot_path.is_file():
            candidate.geometry_summary = snapshot_geometry(snapshot_path)
        if exported is not None and exported.is_file():
            bake_path = Path(str(mapping(row["bake"])["bake"]))
            candidate.audit = audit_glb(exported, Path(str(row["snapshot"])),
                                        bake_path.parent / "texture/BaseColor_mip0.png", require_uv1=asset == "chloe")
            levels: list[dict[str, object]] = []
            side, level = candidate.resolution, 0
            while side:
                path = bake_path.parent / f"texture/BaseColor_mip{level}.png"
                with Image.open(path) as image:
                    levels.append({"level": level, "size": list(image.size), "expected_side": side,
                                   "size_passed": image.size == (side, side), "bytes": path.stat().st_size,
                                   "sha256": file_hash(path)})
                side, level = side // 2, level + 1
            candidate.audit["mip_chain"] = levels
            candidate.audit["mip_chain_complete"] = all(record["size_passed"] is True for record in levels)
            candidate.audit["passed"] = candidate.audit["passed"] is True and candidate.audit["mip_chain_complete"] is True
        candidates.append(candidate)
    return candidates


def appearance_review(root: Path, asset: str) -> dict[str, object]:
    value = read_optional(root / asset / "appearance_review.json")
    if value:
        return value
    value = read_optional(root / "appearance_review.json")
    return mapping(mapping(value.get("assets", value)).get(asset))


def choose_result(candidates: list[Candidate], review: dict[str, object]) -> tuple[Candidate | None, str]:
    selected = review.get("selected_key", review.get("selected_result", review.get("selected")))
    if isinstance(selected, dict):
        selected = mapping(selected).get("key")
    if selected is not None:
        chosen = next((candidate for candidate in candidates if candidate.key == selected), None)
        if chosen is None or chosen.exported is None or chosen.audit.get("passed") is not True:
            raise ValueError(f"REVIEW_SELECTION_NOT_AUDITED:{selected}")
        return chosen, "Agent 外观审阅指定；数值质量另列"
    available = [candidate for candidate in candidates if candidate.method.startswith("mirror_shared")
                 and candidate.exported is not None and candidate.audit.get("passed") is True]
    return (max(available, key=lambda candidate: (candidate.method.endswith("_final"), candidate.resolution)),
            "最高已完成最终镜像共享分辨率；仅数值状态，无外观认可") if available else (
        None, "没有独立载入通过的镜像共享导出")


def retain_evidence(package: Package, calls: list[dict[str, object]]) -> None:
    """Keep requests, logs, reports and snapshot sidecars, including failed calls."""
    for source in sorted(package.experiment.rglob("*")):
        if not source.is_file():
            continue
        if source.suffix.lower() in {".json", ".log", ".txt"} or source.suffix.lower() == ".npz" and not source.name.endswith("_faces.npz") or source.suffix.lower() == ".png" and "texture" not in source.parts:
            package.copy(source)
    # Inputs are self-contained, retaining the exact original FBX/OBJ and reference.
    for asset in ASSETS:
        source = package.experiment / asset / "source/import/inputs"
        package.retain_tree(source, Path("inputs") / asset)
    package.copy(ROOT / "assets/v1/manifest.json", "inputs/assets_v1_manifest.json")
    package.copy(ROOT / "scripts/run_legacy_reseam.py", "reproduce/run_legacy_reseam.py")
    package.copy(Path(__file__), "reproduce/build_legacy_reseam_report.py")
    for call in calls:
        result = mapping(call.get("result"))
        snapshot = result.get("snapshot")
        if isinstance(snapshot, str) and Path(snapshot).is_file():
            record = read_object(Path(snapshot))
            extra = mapping(mapping(record.get("metadata")).get("extra_uv"))
            if "path" in extra:
                package.evidence(Path(str(extra["path"])))


def retain_candidate(package: Package, candidate: Candidate) -> None:
    if candidate.exported is not None and candidate.exported.is_file():
        destination = Path("models") / candidate.asset / candidate.key
        package.retain_tree(candidate.exported.parent, destination)
        write_json(package.root / destination / "independent_reimport.json", candidate.audit)
    bake = mapping(candidate.row.get("bake"))
    if "bake" in bake:
        folder = Path(str(bake["bake"])).parent
        package.retain_tree(folder / "texture", Path("textures") / candidate.asset / candidate.key)


def actual_source_texture(package: Package, asset: str, metadata: dict[str, object]) -> Path:
    binding = mapping(metadata.get("static_color_binding"))
    if "frozen_texture" in binding:
        source = Path(str(binding["frozen_texture"]))
        return package.copy(source, Path("textures") / asset / ("original_basecolor" + source.suffix))
    source = Path(str(metadata["reference_glb"]))
    document_raw, binary = read_glb(source)
    document = cast(dict[str, object], document_raw)
    primitive = mapping(sequence(mapping(sequence(document["meshes"])[0])["primitives"])[0])
    image_bytes = embedded_basecolor(document, binary, primitive)
    with Image.open(io.BytesIO(image_bytes)) as image:
        extension = ".jpg" if image.format == "JPEG" else ".png"
    output = package.root / "textures" / asset / ("original_basecolor" + extension)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(image_bytes)
    package.sources[package.relative(output)] = str(source) + "#embedded_BaseColor"
    return output


def font(size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(FONT), size)


def text(draw: ImageDraw.ImageDraw, position: tuple[int, int], content: str, size: int, width: int,
         *, fill: str = "#263d50") -> None:
    """Wrap on characters so Chinese and long path labels cannot overflow."""
    x, y = position
    face = font(size)
    for paragraph in content.split("\n"):
        line = ""
        for char in paragraph:
            if line and draw.textlength(line + char, font=face) > width:
                draw.text((x, y), line, font=face, fill=fill)
                line, y = char, y + size + 6
            else:
                line += char
        draw.text((x, y), line, font=face, fill=fill)
        y += size + 6


def picture(canvas: Image.Image, path: Path | None, box: tuple[int, int, int, int], *, nearest: bool = False) -> None:
    x, y, width, height = box
    if path is None or not path.is_file():
        draw = ImageDraw.Draw(canvas)
        draw.rectangle((x, y, x + width, y + height), fill="#edf0f3")
        text(draw, (x + 18, y + height // 2 - 20), "未产生对应图像", 23, width - 36, fill="#985353")
        return
    with Image.open(path) as image:
        content = image.convert("RGB")
        content.thumbnail((width, height), Image.Resampling.NEAREST if nearest else Image.Resampling.LANCZOS)
        canvas.paste(content, (x + (width - content.width) // 2, y + (height - content.height) // 2))


@dataclass
class Card:
    title: str
    uv: Path | None
    colors: list[Path | None]
    texture: Path | None
    lines: list[str]


def candidate_card(root: Path, candidate: Candidate) -> Card:
    key_root = root / candidate.asset / candidate.key
    image_root = key_root / "color_views"
    row = candidate.numeric_row()
    bake = mapping(candidate.row.get("bake"))
    texture = Path(str(bake["bake"])).parent / "texture/BaseColor_mip0.png" if "bake" in bake else None
    return Card(METHODS.get(candidate.method, candidate.method), key_root / "final/uv_views/uv.png",
                [image_root / f"{camera}.png" for camera in ("front", "oblique", "back")], texture,
                [f"{candidate.resolution}² · {row['export_mode']}",
                 f"{display(row['faces'])} 面 / {display(row['vertices'])} 顶点",
                 f"连通岛 {display(row['chart_count'])} / 图块 {display(row['independent_tiles'])}",
                 f"共享面积 {display(row['shared_surface_fraction'], percent=True)}",
                 f"覆盖 {display(row['unique_coverage'], percent=True)}",
                 f"数值质量 {'通过' if row['quality_accepted'] else '未通过'}"])


def make_figures(package: Package, asset: str, candidates: list[Candidate], selected: Candidate | None,
                 original_texture: Path) -> dict[str, str]:
    root = package.experiment
    state = read_object(root / asset / "states.json")
    source = mapping(state["import"])
    source_geometry = snapshot_geometry(Path(str(source["snapshot"])))
    inspect = read_object(root / asset / "source/inspect/result.json")
    original = Card("原作者 UV" if asset == "chloe" else "原低模：无 UV",
                    root / asset / "source/uv_views/uv.png" if asset == "chloe" else None,
                    [root / asset / f"source/color_views/{camera}_original.png"
                     for camera in ("front", "oblique", "back")], original_texture,
                    [f"原始 {source_geometry['faces']} 面 / {source_geometry['vertices']} 顶点",
                     f"作者连通岛 {inspect.get('chart_count')}" if asset == "chloe" else "作者 UV：不存在",
                     "原始交叠不算新增复用", "原色：静态绑定" if asset == "chloe" else "原色：高模投射到低模"])
    cards = [original]
    desired_resolution = selected.resolution if selected is not None else max(
        (candidate.resolution for candidate in candidates), default=0)
    shared_method = selected.method if selected is not None else "mirror_shared_final"
    unshared_method = shared_method.replace("mirror_shared", "mirror_unshared")
    for method in (["xatlas"] if asset != "chloe" else []) + ["optcuts", shared_method, unshared_method]:
        candidate = selected if method.startswith("mirror_shared") else next(
            (item for item in candidates if item.method == method and item.resolution == desired_resolution), None)
        if candidate is None:
            available = [item for item in candidates if item.method == method]
            candidate = max(available, key=lambda item: item.resolution) if available else None
        if candidate is not None:
            cards.append(candidate_card(root, candidate))
        else:
            native = root / asset / "optcuts/uv_views/uv.png" if method == "optcuts" else None
            cards.append(Card(METHODS[method], native, [None, None, None], None,
                              ["没有完成的尺寸候选", "实际失败见 failure_index.json"]))
    outputs: dict[str, str] = {}
    panel, gap, margin = 370, 16, 26
    width = 2 * margin + len(cards) * panel + (len(cards) - 1) * gap
    for kind in ("uv", "models", "textures"):
        height = 940 if kind == "uv" else 1420 if kind == "models" else 850
        canvas = Image.new("RGB", (width, height), "#f4f6f9")
        draw = ImageDraw.Draw(canvas)
        label = {"uv": "UV 与实际指标", "models": "同方向镜头的真实颜色", "textures": "实际 BaseColor 贴图"}[kind]
        text(draw, (margin, 18), f"{NAMES[asset]}｜{label}", 35, width - 2 * margin)
        text(draw, (margin, 76), "原始输入 → 独立无共享基线 → ReSeam 镜像共享 → 同镜像几何无共享对照", 23,
             width - 2 * margin)
        for index, card in enumerate(cards):
            x, y = margin + index * (panel + gap), 128
            draw.rounded_rectangle((x, y, x + panel, height - 106), radius=12, fill="white", outline="#ccd8e2", width=2)
            text(draw, (x + 12, y + 13), card.title, 25, panel - 24)
            if kind == "uv":
                if index == 0 and asset != "chloe":
                    text(draw, (x + 25, y + 190), "原 OBJ / GLB 无 UV\n不将 xatlas 初始化岛数\n称为原作者 UV 岛数", 25, panel - 50)
                else:
                    picture(canvas, card.uv, (x + 12, y + 68, panel - 24, 405))
                for line_index, line in enumerate(card.lines):
                    text(draw, (x + 14, y + 500 + line_index * 34), line, 21, panel - 28)
            elif kind == "models":
                for camera_index, path in enumerate(card.colors):
                    top = y + 65 + camera_index * 334
                    text(draw, (x + 12, top), ("正面", "斜面", "背面")[camera_index], 19, panel - 24)
                    picture(canvas, path, (x + 10, top + 26, panel - 20, 300))
                text(draw, (x + 14, y + 1080), card.lines[0], 21, panel - 28)
            else:
                picture(canvas, card.texture, (x + 12, y + 70, panel - 24, 420), nearest=True)
                if card.texture is not None and card.texture.is_file():
                    with Image.open(card.texture) as image:
                        detail = f"{image.width}×{image.height} · {image.mode}\n文件 {card.texture.stat().st_size:,} 字节"
                    text(draw, (x + 14, y + 520), detail, 21, panel - 28)
                text(draw, (x + 14, y + 594), card.lines[0], 21, panel - 28)
        footnote = ("连通岛、独立图块和共享组分开统计；未测值保留。镜像改变几何，跨几何差异不能归于固定几何 UV 机制。"
                    if kind == "uv" else "共用 azimuth/elevation，各网格自动取景；原始与镜像跨几何图仅作外观参考，不是像素对齐的几何质量证据。"
                    if kind == "models" else "源图为实际保留的图像；候选图为真实烘焙 mip0。缩略展示不改写交付贴图；文件字节、运行时 mip 字节分列。")
        text(draw, (margin, height - 82), footnote, 21, width - 2 * margin)
        output = package.root / "figures" / f"{asset}_{kind}.png"
        output.parent.mkdir(parents=True, exist_ok=True)
        canvas.save(output)
        outputs[kind] = package.relative(output)
    return outputs


def call_records(package: Package, calls: list[dict[str, object]]) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    steps: list[dict[str, object]] = []
    failures: list[dict[str, object]] = []
    inspections: dict[str, dict[str, object]] = {}
    views: dict[str, list[dict[str, object]]] = {}
    for call in calls:
        result = mapping(call.get("result"))
        state_hash = str(result.get("state_hash", ""))
        if call.get("tool") == "uv.inspect" and state_hash:
            metrics = read_optional(Path(str(result.get("metrics_report", "__missing__"))))
            inspections[state_hash] = {
                "charts": result.get("chart_count"), "independent_tiles": metrics.get("actual_independent_tiles"),
                "coverage": result.get("unique_footprint"), "shared_groups": result.get("shared_group_count"),
                "shared_surface_fraction": mapping(metrics.get("global")).get("actual_shared_surface_fraction"),
                "report": f"evidence/{call['asset']}/{call['label']}/inspection.json"}
        if call.get("tool") == "view.render" and state_hash:
            views.setdefault(state_hash, []).append({"label": call["label"],
                "images": {name: package.evidence(Path(str(path))) for name, path in mapping(result.get("images")).items()
                           if Path(str(path)).is_file()}})
    for index, call in enumerate(calls):
        request_path = Path(str(call["request"]))
        request = read_optional(request_path)
        result = mapping(call.get("result"))
        before_path, after_path = request.get("snapshot"), result.get("snapshot")
        before = read_optional(Path(str(before_path))) if isinstance(before_path, str) else {}
        after = read_optional(Path(str(after_path))) if isinstance(after_path, str) else {}
        status = str(result.get("status", "UNKNOWN"))
        flags = [key for key in ("geometry_passed", "quality_accepted", "color_accepted") if result.get(key) is False]
        before_metrics = inspections.get(str(before.get("state_hash")), {})
        after_metrics = inspections.get(str(after.get("state_hash")), {})
        deltas = {key: cast(float, number(after_metrics[key])) - cast(float, number(before_metrics[key]))
                  for key in before_metrics.keys() & after_metrics.keys()
                  if number(before_metrics[key]) is not None and number(after_metrics[key]) is not None}
        target_path = result.get("target", request.get("target"))
        target = Path(str(target_path)) if isinstance(target_path, str) else None
        record = {"index": index, "asset": call["asset"], "path": call["path"], "label": call["label"],
                  "tool": call["tool"], "seconds": call["seconds"], "status": status,
                  "request": package.evidence(request_path) if request_path.is_file() else None,
                  "result": f"evidence/{call['asset']}/{call['label']}/result.json",
                  "parent_snapshot": package.evidence(Path(str(before_path))) if before else None,
                  "parent_state_hash": before.get("state_hash"),
                  "child_snapshot": package.evidence(Path(str(after_path))) if after else None,
                  "child_state_hash": after.get("state_hash"), "acceptance_flags_false": flags,
                  "before_metrics": before_metrics, "after_metrics": after_metrics, "metric_deltas": deltas,
                  "before_views": views.get(str(before.get("state_hash")), []),
                  "after_views": views.get(str(after.get("state_hash")), []),
                  "target": package.evidence(target) if target is not None and target.is_file() else None,
                  "target_sha256": file_hash(target) if target is not None and target.is_file() else None,
                  "reason": result.get("reason"), "decision": request.get("reason", request.get("rationale"))}
        steps.append(record)
        if status not in SUCCESS or flags or result.get("execution_success") is False:
            failures.append(record)
    return steps, failures


def matched_control(candidates: list[Candidate], selected: Candidate | None) -> dict[str, object]:
    if selected is None:
        return {"available": False, "reason": "NO_SELECTED_SHARED_EXPORT"}
    method = selected.method.replace("mirror_shared", "mirror_unshared")
    control = next((candidate for candidate in candidates if candidate.method == method
                    and candidate.resolution == selected.resolution), None)
    if control is None:
        return {"available": False, "shared_key": selected.key, "reason": "MISSING_SAME_RESOLUTION_CONTROL"}
    first = read_optional(Path(str(selected.row.get("snapshot", "__missing__"))))
    second = read_optional(Path(str(control.row.get("snapshot", "__missing__"))))
    first_target = Path(str(selected.row["target"]))
    second_target = Path(str(control.row["target"]))
    same_geometry = first.get("geometry_hash") is not None and first.get("geometry_hash") == second.get("geometry_hash")
    same_target = file_hash(first_target) == file_hash(second_target)
    same_bounds = bool(selected.geometry_summary) and selected.geometry_summary.get("bounds") == control.geometry_summary.get("bounds")
    return {"available": True, "shared_key": selected.key, "control_key": control.key,
            "same_geometry": same_geometry, "same_frozen_target": same_target,
            "same_bounds": same_bounds,
            "same_resolution": selected.resolution == control.resolution,
            "shared_geometry_hash": first.get("geometry_hash"), "control_geometry_hash": second.get("geometry_hash"),
            "shared_target_sha256": file_hash(first_target), "control_target_sha256": file_hash(second_target),
            "control_exported": control.exported is not None, "control_numeric": control.numeric_row(),
            "comparison_conditions_passed": same_geometry and same_bounds and same_target}


def stage_records(root: Path, asset: str) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for label in ("source", "optcuts", "mirror", "merge/final", "merge/topology_final"):
        result_path = root / asset / label / "inspect/result.json"
        if not result_path.is_file():
            continue
        result = read_object(result_path)
        metrics = read_optional(Path(str(result.get("metrics_report", "__missing__"))))
        rows.append({"asset": asset, "stage": label, "state_hash": result.get("state_hash"),
                     "charts": result.get("chart_count"), "independent_tiles": metrics.get("actual_independent_tiles"),
                     "shared_groups": result.get("shared_group_count"),
                     "shared_surface_fraction": mapping(metrics.get("global")).get("actual_shared_surface_fraction"),
                     "coverage": result.get("unique_footprint"), "metrics_verified": metrics.get("metrics_verified"),
                     "source_uv_kind": "authored" if asset == "chloe" else "new_xatlas_initialization"})
    return rows


def markdown_report(package: Package, summary: dict[str, object], candidates: list[Candidate],
                    figures: dict[str, dict[str, str]], *, prefix: str) -> str:
    def link(path: str) -> str:
        return prefix + path

    complete = summary["experiment_finished_confirmed"]
    lines = ["# 三个历史资产的 ReSeam UV 复用实测", "",
             (f"实验结束状态：{'父任务已确认实验结束' if complete else '未确认，当前文件快照不能宣称实验完成'}。"
             "`[verified: report.json experiment_finished_confirmed]`"), "",
             f"范围：{SCOPE}。`[verified: evidence/protocol.json scope/mip_levels/gpu_verified/human_accepted/pbr_verified]`", ""]
    for asset in ASSETS:
        chosen = mapping(mapping(summary["selected"])[asset])
        geometry = mapping(mapping(summary["geometry_stages"])[asset])
        original_geometry = mapping(geometry.get("import"))
        mirrored_geometry = mapping(geometry.get("merge", geometry.get("mirror")))
        lines += [f"## {NAMES[asset]}", "", f"![{NAMES[asset]} UV 与指标]({link(figures[asset]['uv'])})", "",
                  f"![{NAMES[asset]} 真实模型对照]({link(figures[asset]['models'])})", "",
                  f"![{NAMES[asset]} 实际贴图对照]({link(figures[asset]['textures'])})", "",
                  (f"展示选择：`{chosen.get('key')}`；{chosen['selection_reason']}。"
                  f"`[verified: report.json selected.{asset}]`"), ""]
        lines += [(f"实测几何变化：原始 {display(original_geometry.get('faces'))} 面 / "
                   f"{display(original_geometry.get('vertices'))} 顶点 → 镜像变体 "
                   f"{display(mirrored_geometry.get('faces'))} 面 / {display(mirrored_geometry.get('vertices'))} 顶点。"
                   f"原始资产保持不变，镜像是独立新变体。`[verified: geometry_stages.json {asset}；original_integrity.json]`"), ""]
        if chosen.get("key"):
            key = str(chosen["key"])
            lines += [(f"[实际 GLB]({link(f'models/{asset}/{key}/asset.glb')}) · "
                      f"[BaseColor mip0]({link(f'textures/{asset}/{key}/BaseColor_mip0.png')}) · "
                      f"[独立载入检查]({link(f'models/{asset}/{key}/independent_reimport.json')})。"
                      f"`[verified: report.json selected.{asset}.key；reimport.json]`"), ""]
        if asset == "chloe":
            lines += [("原 FBX、作者 UV 和第二 UV 均保留；本轮使用指定图像和 UVChannel_1 做静态颜色绑定。"
                      "Unity 材质中的 UV 变换、动画、混合与动态 Shader 没有复现。"
                      "`[verified: inputs/chloe/identity.json；source_attributes.json chloe；reimport.json]`"), ""]
        else:
            lines += [("原始低模没有UV；图中的xatlas岛数来自本轮新初始化，不能称为作者原始岛数。"
                      "颜色从保留高模投射到固定低模，再通过来源对应继承到独立镜像变体。"
                      f"`[verified: source_attributes.json {asset}；inputs/assets_v1_manifest.json]`"), ""]
        lines += [(f"全部尺寸、失败项与逐项数值保存在[CSV]({link('measurements.csv')})和"
                   f"[JSON]({link('measurements.json')})中，正文以实际比较图为主。"
                   "`[verified: measurements.csv；measurements.json]`"), ""]
    lines += ["## 比较口径与保留边界", "",
              ("镜像生成新几何；共享与解除共享在同一镜像几何、同一冻结目标、同尺寸下比较。"
              "原始几何到镜像几何的轮廓、面数或贴图差异不能归因于固定几何 UV 复用。"
              "`[verified: evidence/protocol.json geometry_policy；steps.json；evaluations.json]`"), "",
              ("连通岛按 UV 拓扑连接计数，独立图块按许可交叠后的真实连接计数，共享面积按实际许可正面积交叠测量；"
              "原始交叠不算新增复用，未完成几何测量的值保留为未测。共享面积统计组内双方，100%不能解释为贴图减少100%。"
              "`[verified: evidence/*/*/final/inspect/uv_metrics.json measurement_method/metrics_verified]`"), "",
              ("图片使用真实保留的 UV 图、贴图和 CPU 渲染。模型列使用相同 azimuth/elevation 镜头方向，"
              "各网格自动取景；原始与镜像跨几何图只作外观参考，不是像素对齐的几何质量证据。"
              "共享／无共享正式对照的几何哈希、冻结目标及尺寸另列核对，相同几何具有相同边界范围。"
              "`[verified: evidence/protocol.json cameras；evidence/*/*/color_views/views.json coordinate_convention；matched_controls.json]`"), "",
              ("烘焙与导出失败、严格数值拒绝以及超预算均保留；Agent 外观判断、数值验收与人工认可分别记录。"
              "完整 mip 写出只表示文件存在，验收层级仍由 quality.validate 的实际 profile 决定。"
              "`[verified: failure_index.json；evidence/*/evaluations.json；report.json selected]`"), "",
              ("镜像变体的源颜色来自所保留半侧；变体内颜色球零差不表示原父模型另一侧未改色。"
               "跨几何外观变化由原始／镜像预览单列观察，数值重建与同目标对照仅在声明的变体内成立。"
               "`[verified: source_attributes.json；steps.json；report.json selected；opinion: 比较边界]`"), "",
              ("共享按实际主侧连通图分组；同一主侧包含多个图时保留真实切口，分别建立共享组以便轮廓排布。"
               "迭代轮次、失败与来源筛选核验另见开发记录，不能把不同评价器版本的质量结果混作同轮排名。"
               "`[verified: evidence/protocol.json；steps.json；development_history.json]`"), "",
              ("贴图文件字节、RGBA8 mip 运行时字节、GLB 和整个证据包字节分别报告；"
              "没有同质量基线通过时不能宣布同质量存储节省。`[opinion]`"), "",
              (f"[完整数值行]({link('measurements.csv')}) · [逐数值字段]({link('numeric_fields.csv')}) · "
              f"[阶段变化]({link('stages.csv')}) · [全部调用与成本]({link('calls.csv')}) · "
              f"[父子状态与哈希]({link('steps.json')}) · [失败索引]({link('failure_index.json')}) · "
              f"[原始资产前后核验]({link('original_integrity.json')}) · [全部导出独立核验]({link('reimport.json')}) · "
              f"[源与产物哈希清单]({link('manifest.json')})。`[verified: 包内对应文件]`"), "",
              "复现时从仓库根目录运行：", "", "```powershell",
              "uv run python -X utf8 scripts/build_legacy_reseam_report.py `",
              f"  --input {summary['reproduce_input']} `",
              f"  --output {summary['reproduce_output']} `",
              "  --report docs/results/legacy-assets-reseam.md --experiment-finished", "```", "",
              ("输出目录必须不存在；脚本只打包保留产物，不调用求解、烘焙或渲染。"
              "构建期间评估或审阅文件发生变化会使本次构建失败，防止交付混合快照。"
              "`[verified: reproduce/build_legacy_reseam_report.py main/tracked_fingerprint]`"), ""]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--experiment-finished", action="store_true",
                        help="Set only after the parent task confirms experiment execution has ended.")
    parser.add_argument("--preflight", action="store_true", help="Read-only readiness check; writes nothing.")
    args = parser.parse_args()
    root = args.input.resolve()
    missing = [str(root / asset / "evaluations.json") for asset in ASSETS
               if not (root / asset / "evaluations.json").is_file()]
    ledger_audit = audit_call_ledger(root)
    if args.preflight:
        print(json.dumps({"ready": not missing and ledger_audit["valid"], "missing_evaluations": missing,
                          "call_ledger": ledger_audit,
                          "output_exists": args.output.exists(), "font_available": FONT.is_file()}, ensure_ascii=False))
        return
    if missing:
        raise FileNotFoundError("WAIT_FOR_EVALUATIONS:" + ";".join(missing))
    if not ledger_audit["valid"]:
        raise ValueError("RECONCILE_TOOL_LEDGER_BEFORE_REPORT:" + json.dumps(ledger_audit))
    if not FONT.is_file():
        raise FileNotFoundError("MICROSOFT_YAHEI_REQUIRED:" + str(FONT))
    fingerprint = tracked_fingerprint(root)
    originals = freeze_originals(root)
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    write_json(output / "BUILD_INCOMPLETE.json", {"status": "BUILDING", "source_fingerprint": fingerprint})
    package = Package(output, root)
    write_json(output / "ledger_verification.json", ledger_audit)
    if (root / "development_history.json").is_file():
        package.copy(root / "development_history.json", "development_history.json")
    if (root / "history").is_dir():
        package.retain_tree(root / "history", Path("history"))
    if (root / "frozen_code").is_dir():
        package.retain_tree(root / "frozen_code", Path("frozen_code"))
    for name in ("ledger_audit.json", "post_experiment_code_check.json"):
        if (root / name).is_file():
            package.copy(root / name, name)
    calls = [mapping(item) for item in sequence(read_object(root / "calls.json")["calls"])]
    all_candidates = [candidate for asset in ASSETS for candidate in collect_candidates(root, asset)]
    retain_evidence(package, calls)
    figures: dict[str, dict[str, str]] = {}
    selected: dict[str, object] = {}
    controls: dict[str, object] = {}
    attributes: dict[str, object] = {}
    geometry_stages: dict[str, object] = {}
    reimports: list[dict[str, object]] = []
    stages: list[dict[str, object]] = []
    for asset in ASSETS:
        state = read_object(root / asset / "states.json")
        source_path = Path(str(mapping(state["import"])["snapshot"]))
        source = load_snapshot(source_path)
        metadata = source.metadata
        attributes[asset] = {"faces": len(source.state.surface.mesh.faces),
                             "vertices": len(source.state.surface.mesh.vertices),
                             "source_uv_present": metadata.get("source_uv_present"),
                             "uv_initialization": metadata.get("uv_initialization"),
                             "static_color_binding": metadata.get("static_color_binding"),
                             "extra_uv": metadata.get("extra_uv"), "source_manifest": metadata.get("source_manifest")}
        geometry_stages[asset] = {stage: snapshot_geometry(Path(str(mapping(state[stage])["snapshot"])))
                                 for stage in ("import", "optcuts", "fixed", "mirror", "merge")
                                 if "snapshot" in mapping(state.get(stage))}
        texture = actual_source_texture(package, asset, metadata)
        if asset == "chloe":
            original_audit = audit_glb(Path(str(metadata["reference_glb"])), source_path, texture, require_uv1=True)
            original_audit["role"] = "unchanged_original_static_reference"
            reimports.append(original_audit)
        candidates = [candidate for candidate in all_candidates if candidate.asset == asset]
        review = appearance_review(root, asset)
        chosen, reason = choose_result(candidates, review)
        selected[asset] = {"key": chosen.key if chosen is not None else None, "selection_reason": reason,
                           "review": review, "numeric": chosen.numeric_row() if chosen is not None else None}
        controls[asset] = matched_control(candidates, chosen)
        figures[asset] = make_figures(package, asset, candidates, chosen, texture)
        stages.extend(stage_records(root, asset))
        for candidate in candidates:
            retain_candidate(package, candidate)
            if candidate.audit:
                reimports.append({"asset_name": asset, "key": candidate.key, **candidate.audit})
    steps, failures = call_records(package, calls)
    numeric = [candidate.numeric_row() for candidate in all_candidates]
    costs: dict[str, dict[str, object]] = {}
    for call in calls:
        key = f"{call['asset']}/{call['path']}"
        row = costs.setdefault(key, {"calls": 0, "seconds": 0.0})
        row["calls"] = int(str(row["calls"])) + 1
        row["seconds"] = float(str(row["seconds"])) + float(str(call["seconds"]))
    summary: dict[str, object] = {"schema_version": 1, "scope": SCOPE,
        "reproduce_input": Path(os.path.relpath(root, ROOT)).as_posix(),
        "reproduce_output": Path(os.path.relpath(output, ROOT)).as_posix(),
        "experiment_finished_confirmed": args.experiment_finished, "source_fingerprint": fingerprint,
        "selected": selected, "matched_controls": controls, "geometry_stages": geometry_stages,
        "evaluation_rows": len(all_candidates),
        "exported_glbs": sum(candidate.exported is not None for candidate in all_candidates),
        "reimport_audits": len(reimports),
        "all_reimports_passed": bool(reimports) and all(record["passed"] is True for record in reimports),
        "calls": len(calls), "total_tool_seconds": sum(float(str(call["seconds"])) for call in calls),
        "path_costs": costs, "failure_or_failed_gate_calls": len(failures),
        "llm_cost": "not separately metered; this agent supplied the recorded operation decisions",
        "gpu_verified": False, "human_accepted": False, "pbr_verified": False,
        "inspection_scope": "Existing real outputs only; raw indexed GLB geometry/corner UV and auxiliary UV, embedded BaseColor, "
                            "input and manifest hashes before/after; no solver/bake/full-suite rerun."}
    write_json(output / "report.json", summary)
    write_json(output / "measurements.json", {"rows": numeric})
    write_csv(output / "measurements.csv", numeric)
    numeric_fields = [{"asset": candidate.asset, "key": candidate.key, "source": source, "field": key, "value": value}
                      for candidate in all_candidates for source, document in {
                          "evaluation": candidate.row, "uv_metrics": candidate.metrics, "geometry": candidate.geometry,
                          "quality_validation": candidate.validation, "bake_metadata": candidate.bake_metadata}.items()
                      for key, value in flatten(document).items()
                      if isinstance(value, (int, float, bool))]
    write_csv(output / "numeric_fields.csv", numeric_fields)
    write_json(output / "evaluations.json", {asset: read_object(root / asset / "evaluations.json") for asset in ASSETS})
    write_json(output / "source_attributes.json", attributes)
    write_json(output / "geometry_stages.json", geometry_stages)
    write_json(output / "stages.json", {"rows": stages})
    write_csv(output / "stages.csv", stages)
    write_json(output / "steps.json", {"steps": steps})
    write_json(output / "failure_index.json", {"failures": failures})
    write_json(output / "reimport.json", {"audits": reimports, "scope": summary["inspection_scope"]})
    write_json(output / "matched_controls.json", controls)
    write_json(output / "calls.json", {"calls": calls, "costs": costs})
    write_csv(output / "calls.csv", [{key: value for key, value in row.items() if key != "result"} |
                                    {"status": mapping(row.get("result")).get("status"),
                                     "reason": mapping(row.get("result")).get("reason")} for row in calls])
    finish_originals(originals)
    write_json(output / "original_integrity.json", {"all_unchanged": True, "records": originals,
        "scope": "import-time source hashes, live originals, frozen copies, assets/v1 manifest, report-build before/after; "
                 "not a filesystem observation before the experiment's original import"})
    (output / "README.md").write_text(markdown_report(package, summary, all_candidates, figures, prefix=""), encoding="utf-8")
    if tracked_fingerprint(root) != fingerprint:
        raise RuntimeError("EXPERIMENT_CHANGED_DURING_REPORT_BUILD_REBUILD_IN_NEW_DIRECTORY")
    if summary["all_reimports_passed"] is not True:
        raise RuntimeError("INDEPENDENT_REIMPORT_FAILED_SEE_REIMPORT_JSON")
    if args.report is not None:
        report = args.report.resolve()
        report.parent.mkdir(parents=True, exist_ok=True)
        prefix = Path(os.path.relpath(output, report.parent)).as_posix() + "/"
        report.write_text(markdown_report(package, summary, all_candidates, figures, prefix=prefix), encoding="utf-8")
    (output / "BUILD_INCOMPLETE.json").unlink()
    manifest = [{"path": path.relative_to(output).as_posix(), "bytes": path.stat().st_size,
                 "sha256": file_hash(path), "source": package.sources.get(path.relative_to(output).as_posix(), "generated_by_report")}
                for path in sorted(output.rglob("*")) if path.is_file()]
    write_json(output / "manifest.json", {"schema_version": 1, "files": manifest,
        "total_file_bytes_excluding_manifest": sum(int(str(row["bytes"])) for row in manifest),
        "manifest_self_hash_excluded": True, "source_fingerprint": fingerprint, "inspection_scope": summary["inspection_scope"]})
    for record in manifest:
        if file_hash(output / str(record["path"])) != record["sha256"]:
            raise ValueError("FINAL_MANIFEST_HASH_MISMATCH")
    print(json.dumps({"package": str(output), "rows": len(numeric), "reimports": len(reimports),
                      "manifest_files": len(manifest), "all_originals_unchanged": True,
                      "experiment_finished_confirmed": args.experiment_finished}, ensure_ascii=False))


if __name__ == "__main__":
    main()
