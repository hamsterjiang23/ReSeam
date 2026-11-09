"""Independent OptCuts reference initialization under the shared repair contract."""

import math
import re
import time
import uuid
from pathlib import Path

import numpy as np

from .backends import baseline
from .budget_state import BudgetState, repair_independent_initialization
from .mesh import load_mesh, save_state, sha256, write_obj
from .pipeline import write_json
from .runtime import read_runtime
from .types import Mesh, MeshState
from .validation import topology_errors


def _identity(mesh: Mesh, state: MeshState) -> None:
    if not np.array_equal(mesh.vertices, state.mesh.vertices) or not np.array_equal(
        mesh.faces, state.mesh.faces
    ):
        raise ValueError("OPTCUTS_CHANGED_ORIGINAL_GEOMETRY_OR_FACE_IDENTITY")


def prepare_optcuts_reference(
    root: Path,
    input_path: Path,
    mesh: Mesh,
    seed: int,
    deadline: float,
    maximum_anisotropy: float = 2,
) -> tuple[BudgetState, dict[str, object]]:
    """Generate an explicitly separate reference; never a new-solver initializer.

    The caller may cache the result read-only and clone it per target budget.
    Native execution, repair, hashes and IO all count towards its setup deadline.
    The existing WSL timeout/kill-after adapter remains responsible for native
    termination. No geometry or material acceptance is inferred here.
    """
    started = time.monotonic()
    if (type(seed) is not int or seed < 0 or not math.isfinite(deadline)
            or not math.isfinite(maximum_anisotropy) or maximum_anisotropy < 1):
        raise ValueError("Nonnegative integer seed and finite reference contract required")
    if started >= deadline:
        raise TimeoutError("OPTCUTS_REFERENCE_SETUP_BUDGET_EXHAUSTED")
    root, input_path = root.resolve(), input_path.resolve()
    label = re.sub(r"[^a-zA-Z0-9_-]", "-", input_path.parent.name)[:64] or "asset"
    folder = root / "artifacts/budget_v1/optcuts_initializations" / f"{label}_{uuid.uuid4().hex}"
    folder.mkdir(parents=True, exist_ok=False)
    record: dict[str, object] = {
        "status": "RUNNING", "method": "optcuts_reference", "seed": seed,
        "input_path": str(input_path), "folder": str(folder),
        "maximum_anisotropy": maximum_anisotropy,
        "role": "separate_baseline_only_not_new_solver_initialization",
        "material_validated": False, "geometry_acceptance": "CALLER_REQUIRED",
        "native_seed_effect": "deterministic_repeat_unless_adapter_explicitly_changes_it",
    }
    inputs_before: dict[str, str] = {}
    backend_before: dict[str, str] = {}
    try:
        disk_mesh = load_mesh(input_path)
        if not np.array_equal(mesh.vertices, disk_mesh.vertices) or not np.array_equal(mesh.faces, disk_mesh.faces):
            raise ValueError("OPTCUTS_REFERENCE_INPUT_MESH_IDENTITY_MISMATCH")
        runtime = read_runtime(root)
        binary = Path(runtime["optcuts_binary"]).resolve()
        inputs_before = {str(input_path): sha256(input_path)}
        backend_before = {str(path): sha256(path) for path in (
            binary, root / "configs/runtime.local.json", Path(__file__).resolve(),
            root / "src/reseam/backends.py", root / "src/reseam/runtime.py",
            root / "src/reseam/initialization.py", root / "src/reseam/budget_state.py",
        )}
        record.update(input_sha256=inputs_before, backend_sha256=backend_before)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("OPTCUTS_REFERENCE_SETUP_BUDGET_EXHAUSTED")
        native_seconds = min(60.0, remaining)
        record["native_seconds_limit"] = native_seconds
        write_json(folder / "protocol.json", record)
        native_started = time.monotonic()
        raw, metadata = baseline(root, input_path, "optcuts", runtime, folder / "native", native_seconds, seed)
        record["native_wall_seconds"] = time.monotonic() - native_started
        record["backend"] = metadata
        _identity(mesh, raw)
        raw_errors = topology_errors(raw)
        record["raw_topology_errors"] = raw_errors
        save_state(folder / "raw_state.json", raw)
        write_obj(folder / "raw_uv.obj", raw)
        if raw_errors:
            raise ValueError("OPTCUTS_REFERENCE_RAW_TOPOLOGY_INVALID:" + ";".join(raw_errors))
        repair_started = time.monotonic()
        repaired, repair = repair_independent_initialization(raw, maximum_anisotropy=maximum_anisotropy)
        _identity(mesh, repaired.surface)
        repaired_errors = topology_errors(repaired.surface)
        record.update(repair=repair, repair_wall_seconds=time.monotonic() - repair_started,
                      repaired_topology_errors=repaired_errors,
                      raw_uv_vertices=len(raw.uv), repaired_uv_vertices=len(repaired.surface.uv))
        save_state(folder / "repaired_state.json", repaired.surface)
        write_obj(folder / "repaired_uv.obj", repaired.surface)
        if repaired_errors:
            raise ValueError("OPTCUTS_REFERENCE_REPAIRED_TOPOLOGY_INVALID:" + ";".join(repaired_errors))
        if time.monotonic() >= deadline:
            raise TimeoutError("OPTCUTS_REFERENCE_SETUP_BUDGET_EXHAUSTED")
        record["status"] = "REFERENCE_INITIALIZED"
        return repaired, record
    except Exception as error:
        record.update(status="SETUP_TIMEOUT" if isinstance(error, TimeoutError) else "INITIALIZATION_FAILED",
                      reason=f"{type(error).__name__}: {error}")
        raise
    finally:
        hash_errors: dict[str, str] = {}
        after: dict[str, str] = {}
        for value in inputs_before | backend_before:
            try:
                after[value] = sha256(Path(value))
            except OSError as error:
                hash_errors[value] = f"{type(error).__name__}: {error}"
        record.update(wall_seconds=time.monotonic() - started,
                      input_unchanged=bool(inputs_before) and all(after.get(p) == v for p, v in inputs_before.items()),
                      backend_unchanged=bool(backend_before) and all(after.get(p) == v for p, v in backend_before.items()),
                      hash_errors_after=hash_errors, sha256_after=after)
        successful = record.get("status") == "REFERENCE_INITIALIZED"
        changed = not record["input_unchanged"] or not record["backend_unchanged"]
        expired = time.monotonic() >= deadline
        if successful and changed:
            record.update(status="INPUT_OR_BACKEND_CHANGED", reason="OPTCUTS_REFERENCE_INTEGRITY_CHANGED")
        elif successful and expired:
            record.update(status="SETUP_TIMEOUT", reason="OPTCUTS_REFERENCE_SETUP_BUDGET_EXHAUSTED")
        write_json(folder / "result.json", record)
        if successful and changed:
            raise RuntimeError("OPTCUTS_REFERENCE_INTEGRITY_CHANGED")
        if successful and expired:
            raise TimeoutError("OPTCUTS_REFERENCE_SETUP_BUDGET_EXHAUSTED")
