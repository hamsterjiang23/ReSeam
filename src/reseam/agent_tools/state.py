"""Hash-bound snapshots. UV identity is serialized, never reconstructed by proximity."""

import hashlib
import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, cast

import numpy as np

from ..budget_state import BudgetState
from ..joint_types import Correspondence, ReuseGroup
from ..mesh import make_mesh
from ..reuse_transform import similarity_transform
from ..types import MeshState


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def read_object(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict) or not all(isinstance(k, str) for k in value):
        raise ValueError("JSON_OBJECT_REQUIRED")
    return value


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def object_hash(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def check_deadline(deadline: float) -> None:
    if not np.isfinite(deadline) or time.monotonic() >= deadline:
        raise TimeoutError("TOOL_DEADLINE_EXHAUSTED")


@dataclass(frozen=True)
class Snapshot:
    state: BudgetState
    metadata: dict[str, object]
    root: Path
    state_hash: str
    asset_id: str


def save_snapshot(
    state: BudgetState, output: Path, metadata: dict[str, object], parent: Snapshot | None = None,
) -> Snapshot:
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    surface = state.surface
    arrays = output / "arrays.npz"
    np.savez_compressed(arrays, vertices=surface.mesh.vertices, faces=surface.mesh.faces,
                        uv=surface.uv, uv_faces=surface.uv_faces,
                        texture=np.empty(0) if state.texture is None else state.texture)
    meta = json.loads(json.dumps(metadata, allow_nan=False))
    geometry_hash = hashlib.sha256(surface.mesh.vertices.tobytes() + surface.mesh.faces.tobytes()).hexdigest()
    asset_id = parent.asset_id if parent else str(meta.get("asset_id", geometry_hash))
    if parent is not None and (
        not np.array_equal(surface.mesh.vertices, parent.state.surface.mesh.vertices) or not np.array_equal(
            surface.mesh.faces, parent.state.surface.mesh.faces
        )
    ):
        raise ValueError("SNAPSHOT_GEOMETRY_CHANGED")
    record: dict[str, object] = {
        "schema_version": 1, "asset_id": asset_id, "geometry_hash": geometry_hash,
        "parent_hash": None if parent is None else parent.state_hash,
        "arrays_sha256": file_hash(arrays), "metadata": meta,
        "seams": sorted(surface.seams), "surface_version": surface.version, "version": state.version,
        "groups": [asdict(g) for g in state.groups],
        "correspondences": [asdict(c) for c in state.correspondences],
        "correspondence_records": list(state.correspondence_records), "evidence": state.evidence,
    }
    digest = object_hash(record)
    write_json(output / "snapshot.json", {**record, "state_hash": digest})
    return Snapshot(state.copy(), meta, output, digest, asset_id)


def load_snapshot(path: Path) -> Snapshot:
    manifest = path / "snapshot.json" if path.is_dir() else path
    # JSON is an external format boundary; the digest and structural checks below
    # validate it before converting it to the typed domain dataclasses.
    record = cast(dict[str, Any], read_object(manifest))
    digest = record.pop("state_hash", None)
    if record.get("schema_version") != 1 or digest != object_hash(record):
        raise ValueError("SNAPSHOT_MANIFEST_HASH_MISMATCH")
    arrays = manifest.parent / "arrays.npz"
    if file_hash(arrays) != record["arrays_sha256"]:
        raise ValueError("SNAPSHOT_ARRAY_HASH_MISMATCH")
    metadata = record["metadata"]
    if not isinstance(metadata, dict):
        raise TypeError("SNAPSHOT_METADATA_INVALID")
    for source in metadata.get("source_manifest", []):
        if file_hash(Path(source["path"])) != source["sha256"]:
            raise ValueError("SNAPSHOT_SOURCE_CHANGED")
    with np.load(arrays, allow_pickle=False) as data:
        mesh = make_mesh(data["vertices"], data["faces"])
        uv, faces = data["uv"].copy(), data["uv_faces"].copy()
        if (faces.shape != mesh.faces.shape or uv.ndim != 2 or uv.shape[1] != 2
                or not np.isfinite(uv).all() or faces.min() < 0 or faces.max() >= len(uv)):
            raise ValueError("SNAPSHOT_UV_INVALID")
        surface = MeshState(mesh, frozenset(record["seams"]), uv, faces, int(record["surface_version"]))
        texture = data["texture"].copy() if data["texture"].size else None
    groups = tuple(ReuseGroup(
        str(g["group_id"]), tuple(g["first_faces"]), tuple(g["second_faces"]),
        tuple(g["plane_normal"]), float(g["plane_offset"]),
        int(g["first_orientation"]), int(g["second_orientation"]),
        None if g.get("first_to_second") is None else similarity_transform(g["first_to_second"]),
    ) for g in record["groups"])
    correspondences = tuple(Correspondence(
        int(c["source_face"]), tuple(c["source_barycentric"]), int(c["target_face"]),
        tuple(c["target_barycentric"]), float(c["weight"]),
    ) for c in record["correspondences"])
    state = BudgetState(surface, groups, correspondences, texture, record["evidence"],
                        tuple(record["correspondence_records"]), int(record["version"]))
    return Snapshot(state, metadata, manifest.parent.resolve(), str(digest), str(record["asset_id"]))


def require_snapshot(request: dict[str, object]) -> Snapshot:
    source = request.get("snapshot")
    if not isinstance(source, str):
        raise TypeError("SNAPSHOT_PATH_REQUIRED")
    snapshot = load_snapshot(Path(source))
    if request.get("state_hash", snapshot.state_hash) != snapshot.state_hash:
        raise ValueError("STALE_SNAPSHOT_REQUEST")
    return snapshot


def snapshot_result(snapshot: Snapshot) -> dict[str, object]:
    return {"snapshot": str(snapshot.root / "snapshot.json"), "state_hash": snapshot.state_hash,
            "asset_id": snapshot.asset_id}
