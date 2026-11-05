"""Paper baseline adapters; upstream sources remain unmodified in sibling folders."""

from __future__ import annotations

import sys
import time
from pathlib import Path

import numpy as np

from reseam.agent_tools.reparameterize import _import_optcuts
from reseam.agent_tools.state import (
    check_deadline,
    file_hash,
    read_object,
    require_snapshot,
    save_snapshot,
    snapshot_result,
    write_json,
)
from reseam.budget_state import BudgetState
from reseam.mesh import chart_labels, state_from_corner_uv
from reseam.runtime import linux_path, read_runtime, run_process, wsl_command
from reseam.types import MeshState

ROOT = Path(__file__).resolve().parents[1]


def write_geometry(path: Path, state: MeshState) -> None:
    """Deliberately omit source UV: no baseline may silently reuse the completed atlas."""
    with path.open("w", encoding="utf-8") as stream:
        for vertex in state.mesh.vertices:
            stream.write("v " + " ".join(format(float(x), ".17g") for x in vertex) + "\n")
        for face in state.mesh.faces:
            stream.write("f " + " ".join(str(int(x) + 1) for x in face) + "\n")


def run_baseline(request: dict[str, object], output: Path, deadline: float) -> dict[str, object]:
    parent = require_snapshot(request)
    original = parent.state.surface
    method = str(request.get("method"))
    if method not in {"xatlas", "blender_smart", "optcuts", "partuv", "flatten_anything"}:
        raise ValueError("UNKNOWN_PAPER_BASELINE:" + method)
    seed = int(str(request.get("seed", 0 if method == "optcuts" else 42 if method == "partuv" else 17)))
    runtime = read_runtime(ROOT)
    native = output / "native"
    native.mkdir()
    geometry = native / "input.obj"
    write_geometry(geometry, original)
    started = time.monotonic()
    settings: dict[str, object] = {"seed": seed, "input_uv_omitted": True,
                                   "automatic_geometry_repair": False}
    if method == "xatlas":
        import xatlas

        mapping, indices, uv = xatlas.parametrize(original.mesh.vertices.astype(np.float32),
                                                 original.mesh.faces.astype(np.uint32))
        if not np.array_equal(mapping[indices], original.mesh.faces):
            raise ValueError("XATLAS_FACE_CORNER_IDENTITY_CHANGED")
        surface = state_from_corner_uv(original.mesh, np.asarray(uv[indices], dtype=float),
                                       corner_ids=np.asarray(indices, dtype=np.int64))
        np.savez_compressed(native / "raw.npz", mapping=mapping, uv=uv, indices=indices)
        settings["chart_options"] = "official defaults via xatlas.parametrize"
    elif method == "blender_smart":
        job = native / "request.json"
        write_json(job, {"vertices": original.mesh.vertices.tolist(), "faces": original.mesh.faces.tolist(),
                         "angle_degrees": 66, "island_margin": .03, "output": str(native / "uv.json")})
        worker = ROOT / "baseline/adapters/blender_smart.py"
        run_process([str(runtime["blender"]), "--background", "--factory-startup", "--threads", "1",
                     "--python-exit-code", "1", "--python", str(worker), "--", str(job)], native,
                    deadline - time.monotonic(), env={"OMP_NUM_THREADS": "1", "OPENBLAS_NUM_THREADS": "1"})
        data = read_object(native / "uv.json")
        edge_ids = {tuple(edge): i for i, edge in enumerate(original.mesh.edges)}
        seams = frozenset(edge_ids[tuple(edge)] for edge in data["seams"])  # type: ignore[union-attr]
        surface = state_from_corner_uv(original.mesh, np.asarray(data["corner_uv"], dtype=float), seam_ids=seams)
        settings.update({"angle_degrees": 66, "island_margin": .03, "blender": data["version"],
                         "worker_sha256": file_hash(worker)})
    elif method == "optcuts":
        (native / "output").mkdir()
        binary = Path(str(runtime["optcuts_binary"]))
        args = [linux_path(binary), "100", linux_path(geometry), "0.999", "1", "0", "4.1", "1", "0",
                f"paper_{seed}"]
        seconds = deadline - time.monotonic() - 2
        run_process(wsl_command(runtime, args, seconds, native), native, seconds)
        outputs = list(native.rglob("finalResult_mesh.obj"))
        if len(outputs) != 1:
            raise ValueError("OPTCUTS_FINAL_OUTPUT_MISSING")
        surface = _import_optcuts(original, outputs[0])
        settings.update({"lambda": .999, "distortion_bound": 4.1, "binary_sha256": file_hash(binary),
                         "geometry_mapping": "existing original-face/corner importer; ambiguous identity rejects"})
    elif method == "partuv":
        worker = ROOT / "baseline/adapters/partuv_worker.py"
        args = [linux_path(Path(str(runtime["partuv_python"]))), "-u", linux_path(worker),
                "--input", linux_path(geometry), "--output", linux_path(native),
                "--checkpoint", linux_path(Path(str(runtime["partfield_checkpoint"]))),
                "--sha256", str(runtime["partfield_sha256"]),
                "--config", linux_path(ROOT / "baseline/PartUV/config/config.yaml"), "--seed", str(seed)]
        seconds = deadline - time.monotonic() - 2
        run_process(wsl_command(runtime, args, seconds), native, seconds)
        surface = _import_optcuts(original, native / "aligned.obj")
        settings.update({"threshold": 1.25, "backend": read_object(native / "backend.json"),
                         "worker_sha256": file_hash(worker)})
    else:
        worker = ROOT / "baseline/adapters/flatten_anything.py"
        args = [linux_path(Path(str(runtime["partuv_python"]))), "-u", linux_path(worker),
                "--input", linux_path(geometry), "--output", linux_path(native), "--source",
                linux_path(ROOT / "baseline/FlattenAnything"), "--seed", str(seed),
                "--points", str(request.get("points", 10000)),
                "--iterations", str(request.get("iterations", 10000))]
        seconds = deadline - time.monotonic() - 2
        run_process(wsl_command(runtime, args, seconds), native, seconds)
        data = np.load(native / "uv.npz", allow_pickle=False)
        uv = data["uv"].copy()
        if uv.shape != (len(original.mesh.vertices), 2):
            raise ValueError("FAM_VERTEX_UV_SHAPE_CHANGED")
        surface = state_from_corner_uv(original.mesh, uv[original.mesh.faces], corner_ids=original.mesh.faces)
        settings.update(read_object(native / "backend.json"))
    check_deadline(deadline)
    # Geometry always comes from the fixed parent, after explicit identity checks.
    surface.version = original.version + 1
    state = BudgetState(surface, version=surface.version)
    metadata = {**parent.metadata, "last_operation": "uv.baseline", "requires_rebake": True,
                "baseline_method": method, "baseline_settings": settings}
    for key in ("packing_record", "packing_backend", "geometry_profile"):
        metadata.pop(key, None)
    child = save_snapshot(state, output / "snapshot", metadata, parent)
    report = {"method": method, "settings": settings, "seconds": time.monotonic() - started,
              "faces": len(original.mesh.faces), "chart_count": len(np.unique(chart_labels(surface))),
              "input_geometry_sha256": file_hash(geometry), "geometry_unchanged": True,
              "sharing_groups": 0, "quality_accepted": None, "python": sys.version}
    write_json(output / "baseline.json", report)
    return {**snapshot_result(child), **report, "baseline": str(output / "baseline.json"),
            "execution_success": True, "status": "OK", "requires_rebake": True}
