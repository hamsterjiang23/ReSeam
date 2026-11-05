"""Non-mutating geometric candidates and explicit external PartField research adapter."""

import time
from pathlib import Path
from typing import cast

import numpy as np

from ..runtime import linux_path, read_runtime, run_process, wsl_command
from .mesh_query import _components, load_index
from .state import check_deadline, file_hash, object_hash, read_object, require_snapshot, write_json


def _flood(neighbors: list[list[int]], selected: set[int]) -> list[list[int]]:
    remaining = set(selected)
    components: list[list[int]] = []
    while remaining:
        seed = min(remaining)
        pending = [seed]
        remaining.remove(seed)
        component = [seed]
        while pending:
            for neighbor in neighbors[pending.pop()]:
                if neighbor in remaining:
                    remaining.remove(neighbor)
                    pending.append(neighbor)
                    component.append(neighbor)
        components.append(sorted(component))
    return components


def propose_regions(request: dict[str, object], output: Path, deadline: float) -> dict[str, object]:
    snapshot = require_snapshot(request)
    check_deadline(deadline)
    mesh = snapshot.state.surface.mesh
    backend = request.get("backend", "geometry")
    output.mkdir(parents=True, exist_ok=True)
    candidates: list[dict[str, object]] = []
    provenance: dict[str, object] = {"backend": backend}
    if backend == "geometry":
        connection, manifest = load_index(request, snapshot)
        try:
            rows = connection.execute("SELECT id,dihedral_raw FROM edge ORDER BY id").fetchall()
        finally:
            connection.close()
        angles = {int(row["id"]): float(row["dihedral_raw"]) for row in rows}
        thresholds = request.get("crease_degrees", [25., 45., 70.])
        if not isinstance(thresholds, list) or not thresholds:
            raise ValueError("CREASE_DEGREES_LIST_REQUIRED")
        values = [float(str(value)) for value in thresholds]
        if any(not np.isfinite(value) or not 0 < value < 180 for value in values):
            raise ValueError("CREASE_DEGREES_RANGE_0_180")
        component_labels = _components(snapshot)
        for label in np.unique(component_labels):
            faces = np.flatnonzero(component_labels == label).tolist()
            candidates.append({"faces": faces, "method": "connected_component", "scale": None})
        for degrees in values:
            check_deadline(deadline)
            adjacency: list[list[int]] = [[] for _ in mesh.faces]
            for edge_id, entries in enumerate(mesh.edge_faces):
                if len(entries) == 2 and angles[edge_id] < np.radians(degrees):
                    a, b = entries[0][0], entries[1][0]
                    adjacency[a].append(b)
                    adjacency[b].append(a)
            for faces in _flood(adjacency, set(range(len(mesh.faces)))):
                candidates.append({"faces": faces, "method": "crease_connected", "scale": degrees})
        # Two-sided thin surfaces: PCA gives a candidate plane, not a semantic conclusion.
        triangles = mesh.vertices[mesh.faces]
        cross = np.cross(triangles[:, 1]-triangles[:, 0], triangles[:, 2]-triangles[:, 0])
        normals = cross/np.maximum(np.linalg.norm(cross, axis=1)[:, None], 1e-30)
        for label in np.unique(component_labels):
            ids = np.flatnonzero(component_labels == label)
            points = mesh.vertices[np.unique(mesh.faces[ids])]
            _, singular, axes = np.linalg.svd(points-points.mean(axis=0), full_matrices=False)
            if len(singular) < 3 or singular[2] > .15*max(singular[1], 1e-30):
                continue
            adjacency = [[] for _ in mesh.faces]
            for entries in mesh.edge_faces:
                if len(entries) == 2:
                    a, b = entries[0][0], entries[1][0]
                    adjacency[a].append(b)
                    adjacency[b].append(a)
            for sign in (-1, 1):
                selected = set(ids[(normals[ids]@axes[2])*sign > 1e-6].tolist())
                for faces in _flood(adjacency, selected):
                    candidates.append({"faces": faces, "method": "thin_pca_side", "scale": sign})
        provenance["index_hash"] = manifest["index_hash"]
    elif backend == "partfield":
        # Research inference is isolated; the runner must output labels on exact input faces.
        # No upstream manifold repair or coordinate welding is permitted in this adapter.
        command = request.get("partfield_command")
        weights = request.get("weights")
        runtime = None
        if command is None:
            runtime = read_runtime(Path(__file__).resolve().parents[3])
            weights = request.get("weights", runtime.get("partfield_checkpoint"))
        elif not isinstance(command, list) or not command or not all(isinstance(v, str) for v in command):
            raise TypeError("PARTFIELD_COMMAND_ARGUMENT_LIST_REQUIRED")
        if not isinstance(weights, str) or not Path(weights).is_file():
            raise FileNotFoundError("PARTFIELD_WEIGHTS_MISSING")
        input_path = output / "partfield_input.npz"
        result_path = output / "partfield_labels.npz"
        np.savez_compressed(input_path, vertices=mesh.vertices, faces=mesh.faces)
        if runtime is not None:
            executable = Path(str(runtime.get("partuv_python", "")))
            # WSL Python is commonly a Linux symlink that Windows cannot stat.
            if not runtime.get("partuv_python") or not executable.parent.is_dir():
                raise FileNotFoundError("PARTFIELD_PYTHON_MISSING")
            if "weights" not in request and runtime.get("partfield_sha256") != file_hash(Path(weights)):
                raise ValueError("PARTFIELD_CHECKPOINT_HASH_MISMATCH")
            groups = int(str(request.get("groups", 8)))
            if not 1 <= groups <= len(mesh.faces):
                raise ValueError("PARTFIELD_GROUPS_RANGE")
            worker = Path(__file__).with_name("partfield_worker.py")
            remaining = deadline-time.monotonic()
            linux_executable = linux_path(executable.parent) + "/" + executable.name
            argv = wsl_command(runtime, [linux_executable, linux_path(worker), linux_path(input_path),
                                          linux_path(result_path), linux_path(Path(weights)), str(groups),
                                          str(int(str(request.get("seed", 42))))], remaining)
            run_process(argv, output / "partfield_process", remaining,
                        env={"OMP_NUM_THREADS": "1", "OPENBLAS_NUM_THREADS": "1"})
        else:
            argv = [str(v).replace("{input}", str(input_path.resolve())).replace("{output}", str(result_path.resolve()))
                    .replace("{weights}", str(Path(weights).resolve())) for v in cast(list[str], command)]
            run_process(argv, output / "partfield_process", deadline-time.monotonic(),
                        env={"OMP_NUM_THREADS": "1", "OPENBLAS_NUM_THREADS": "1"})
        check_deadline(deadline)
        with np.load(result_path, allow_pickle=False) as result:
            if (not np.array_equal(result["vertices"], mesh.vertices)
                    or not np.array_equal(result["faces"], mesh.faces)):
                raise ValueError("PARTFIELD_ORIGINAL_IDENTITY_CHANGED")
            labels = result["labels"]
            if labels.shape != (len(mesh.faces),) or not np.issubdtype(labels.dtype, np.integer) or np.any(labels < 0):
                raise ValueError("PARTFIELD_FACE_LABELS_INVALID")
        adjacency = [[] for _ in mesh.faces]
        for entries in mesh.edge_faces:
            if len(entries) == 2:
                a, b = entries[0][0], entries[1][0]
                adjacency[a].append(b)
                adjacency[b].append(a)
        for label in np.unique(labels):
            for faces in _flood(adjacency, set(np.flatnonzero(labels == label).tolist())):
                candidates.append({"faces": faces, "method": "partfield_connected_label", "scale": int(label)})
        provenance.update(command=argv, weights_sha256=file_hash(Path(weights)),
                          output_sha256=file_hash(result_path), license_scope="research_only_not_production_default")
        if result_path.with_suffix(".json").is_file():
            provenance["inference"] = read_object(result_path.with_suffix(".json"))
        source = request.get("backend_manifest")
        if isinstance(source, str):
            provenance["backend_manifest"] = read_object(Path(source))
    else:
        raise ValueError("UNKNOWN_REGION_PROPOSAL_BACKEND")
    seen: set[tuple[int, ...]] = set()
    unique: list[dict[str, object]] = []
    for candidate in candidates:
        faces = cast(list[int], candidate["faces"])
        key = tuple(faces)
        if not faces or key in seen:
            continue
        seen.add(key)
        vertices = mesh.vertices[np.unique(mesh.faces[faces])]
        candidate.update(candidate_id="candidate:"+object_hash({"state": snapshot.state_hash, "faces": faces})[:20],
                         face_count=len(faces), surface_area=float(mesh.areas[faces].sum()),
                         bounds=[vertices.min(axis=0).tolist(), vertices.max(axis=0).tolist()],
                         semantic_label=None, requires_vlm_review=True)
        unique.append(candidate)
    unique.sort(key=lambda item: (-float(str(item["surface_area"])), str(item["candidate_id"])))
    record = {"asset_id": snapshot.asset_id, "state_hash": snapshot.state_hash, "candidates": unique,
              "provenance": provenance, "geometry_modified": False, "uv_modified": False,
              "semantic_inference_performed": backend == "partfield", "vlm_labeling_performed": False}
    write_json(output / "proposals.json", record)
    return {"proposals": str((output / "proposals.json").resolve()), "candidate_count": len(unique),
            "preview": [{key: value for key, value in item.items() if key != "faces"} for item in unique[:20]],
            "state_hash": snapshot.state_hash, "geometry_modified": False, "uv_modified": False,
            "backend": backend}
