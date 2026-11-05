"""Explicit regional parameterization with immutable original face identities."""

import json
import os
import subprocess
import time
from pathlib import Path

import numpy as np
from scipy.sparse import csr_matrix
from scipy.sparse.linalg import spsolve

from ..budget_state import embed_region
from ..mesh import chart_labels, make_mesh, read_obj, state_from_corner_uv, write_obj
from ..runtime import linux_path, read_runtime, run_process, wsl_command
from ..types import FloatArray, IntArray, MeshState
from .regional_cut import cut_region
from .state import check_deadline, file_hash, require_snapshot, save_snapshot, snapshot_result, write_json


def _blender_unwrap(
    local: MeshState, request: dict[str, object], output: Path, deadline: float,
) -> tuple[MeshState, dict[str, object]]:
    root = Path(__file__).resolve().parents[3]
    if "blender" in request:
        blender = Path(str(request["blender"]))
    else:
        runtime = read_runtime(root)
        blender = Path(str(runtime.get("blender", "E:/Blender 4.5.11/blender.exe")))
    if not blender.is_file():
        raise FileNotFoundError("BLENDER_MISSING:" + str(blender))
    methods = {"blender_lscm": "CONFORMAL", "blender_abf": "ANGLE_BASED",
               "blender_minimum_stretch": "MINIMUM_STRETCH"}
    method = methods[str(request["backend"])]
    folder = output / "blender"
    folder.mkdir()
    source = folder / "input.npz"
    np.savez_compressed(source, vertices=local.mesh.vertices, faces=local.mesh.faces)
    worker = root / "scripts/agent_unwrap_worker.py"
    config = folder / "request.json"
    write_json(config, {"input": str(source.resolve()), "output": str(folder.resolve()), "method": method})
    argv = [str(blender), "--background", "--factory-startup", "--threads", "1", "--python-exit-code", "1",
            "--python", str(worker), "--", str(config.resolve())]
    write_json(folder / "command.json", argv)
    environment = {**os.environ, "OMP_NUM_THREADS": "1", "OPENBLAS_NUM_THREADS": "1",
                   "MKL_NUM_THREADS": "1", "PYTHONUNBUFFERED": "1"}
    check_deadline(deadline)
    with (folder / "process.log").open("w", encoding="utf-8") as log:
        process = subprocess.Popen(argv, stdout=log, stderr=subprocess.STDOUT, env=environment,
                                   creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        try:
            process.wait(timeout=max(.01, deadline - time.monotonic()))
        except subprocess.TimeoutExpired as error:
            if os.name == "nt":
                subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                               stdout=log, stderr=subprocess.STDOUT, check=False)
            else:
                process.kill()
            process.wait()
            raise TimeoutError("BLENDER_UNWRAP_TIMEOUT") from error
    if process.returncode:
        details = (folder / "process.log").read_text(encoding="utf-8", errors="replace")
        reason = next((line.strip() for line in details.splitlines() if "ValueError:" in line), "see process.log")
        raise ValueError("BLENDER_UNWRAP_FAILED:" + reason)
    check_deadline(deadline)
    with np.load(folder / "unwrapped.npz", allow_pickle=False) as arrays:
        if not np.array_equal(arrays["faces"], local.mesh.faces):
            raise ValueError("BLENDER_ORIGINAL_FACE_IDENTITY_CHANGED")
        if not np.allclose(arrays["vertices"], local.mesh.vertices, rtol=1e-6, atol=local.mesh.scale * 1e-7):
            raise ValueError("BLENDER_ORIGINAL_GEOMETRY_CHANGED")
        state = state_from_corner_uv(local.mesh, arrays["corner_uv"], corner_ids=arrays["corner_ids"])
    report = json.loads((folder / "backend.json").read_text(encoding="utf-8"))
    report.update({"executable": str(blender), "worker_sha256": file_hash(worker),
                   "input_sha256": file_hash(source), "output_sha256": file_hash(folder / "unwrapped.npz")})
    return state, report


def boundary_loop(faces: IntArray) -> list[int]:
    edges: dict[tuple[int, int], list[tuple[int, int]]] = {}
    for face in faces:
        for i in range(3):
            a, b = int(face[i]), int(face[(i+1) % 3])
            edges.setdefault((min(a,b),max(a,b)), []).append((a,b))
    boundary = [values[0] for values in edges.values() if len(values) == 1]
    if not boundary or any(len(values) > 2 for values in edges.values()):
        raise ValueError("HARMONIC_REQUIRES_MANIFOLD_DISK")
    outgoing = dict(boundary)
    if len(outgoing) != len(boundary):
        raise ValueError("HARMONIC_BOUNDARY_BRANCH")
    start = min(outgoing)
    result, vertex = [], start
    while vertex not in result:
        result.append(vertex)
        if vertex not in outgoing:
            raise ValueError("HARMONIC_BOUNDARY_OPEN")
        vertex = outgoing[vertex]
    if vertex != start or len(result) != len(boundary):
        raise ValueError("HARMONIC_MULTIPLE_BOUNDARIES_OR_COMPONENTS")
    vertices = np.unique(faces)
    if len(vertices)-len(edges)+len(faces) != 1:
        raise ValueError("HARMONIC_REQUIRES_DISK_EULER_CHARACTERISTIC")
    return result


def harmonic_uv(
    surface: MeshState, faces: tuple[int, ...], boundary_values: FloatArray | None = None,
) -> tuple[FloatArray, IntArray, list[int]]:
    """Uniform positive-weight Laplacian with a prescribed simple convex boundary."""
    selected = surface.mesh.faces[list(faces)]
    vertices = np.unique(selected)
    lookup = {int(v): i for i, v in enumerate(vertices)}
    loop = boundary_loop(selected)
    positions = surface.mesh.vertices[loop]
    lengths = np.linalg.norm(np.roll(positions, -1, axis=0)-positions, axis=1)
    if np.any(lengths <= 0):
        raise ValueError("ZERO_LENGTH_BOUNDARY_EDGE")
    angles = np.concatenate(([0.], np.cumsum(lengths[:-1]))) / lengths.sum() * 2*np.pi
    boundary = np.column_stack((np.cos(angles), np.sin(angles))) if boundary_values is None else boundary_values
    if boundary.shape != (len(loop),2) or not np.isfinite(boundary).all():
        raise ValueError("INVALID_HARMONIC_BOUNDARY")
    uv = np.zeros((len(vertices), 2))
    boundary_ids = [lookup[v] for v in loop]
    uv[boundary_ids] = boundary
    neighbors: dict[int, set[int]] = {int(v): set() for v in vertices}
    for face in selected:
        for vertex in face:
            neighbors[int(vertex)].update(int(v) for v in face if v != vertex)
    inner = sorted(set(map(int, vertices))-set(loop))
    if inner:
        index = {v: i for i, v in enumerate(inner)}
        rows, cols, data = [], [], []
        rhs = np.zeros((len(inner), 2))
        for vertex in inner:
            i = index[vertex]
            rows.append(i); cols.append(i); data.append(float(len(neighbors[vertex])))
            for neighbor in neighbors[vertex]:
                if neighbor in index:
                    rows.append(i); cols.append(index[neighbor]); data.append(-1.)
                else:
                    rhs[i] += uv[lookup[neighbor]]
        matrix = csr_matrix((data, (rows, cols)), shape=(len(inner),len(inner)))
        uv[[lookup[v] for v in inner]] = np.asarray(spsolve(matrix, rhs), dtype=float)
    ids = np.asarray([[lookup[int(v)] for v in face] for face in selected], dtype=np.int64)
    return uv[ids], ids, loop


def _import_optcuts(local: MeshState, path: Path) -> MeshState:
    """Resolve full triangles, never weld coincident input vertex identities."""
    vertices, faces, uv, uv_faces = read_obj(path)
    if len(faces) != len(local.mesh.faces):
        raise ValueError("OPTCUTS_FACE_COUNT_CHANGED")
    # main.cpp saveInfo passes original F to TriMesh::saveAsMesh(F0). That
    # overload preserves geometry indices while UV indices may split. Its OBJ
    # writer uses scientific notation with the stream's default precision six.
    # Verify the explicit indices and serialized positions, then keep *input*
    # geometry. This does not weld coincident attribute vertices by position.
    if (vertices.shape == local.mesh.vertices.shape and np.array_equal(faces, local.mesh.faces)
            and np.allclose(vertices, local.mesh.vertices, rtol=5.1e-7, atol=1e-14)):
        return state_from_corner_uv(local.mesh, uv[uv_faces], corner_ids=uv_faces)
    # Other outputs require unambiguous full-triangle identity, never nearest
    # vertex welding. Unsupported reorderings remain explicit failures.
    scale = local.mesh.scale
    from scipy.spatial import KDTree
    tree = KDTree(local.mesh.vertices)
    domains = tree.query_ball_point(vertices, max(scale*1e-7,1e-12))
    source_lookup = {frozenset(map(int, face)): i for i,face in enumerate(local.mesh.faces)}
    corners = np.empty((len(faces),3,2))
    identities = np.empty((len(faces),3), dtype=np.int64)
    used: set[int] = set()
    for row, face in enumerate(faces):
        from itertools import product
        choices = [(source_lookup[frozenset(combo)], combo) for combo in product(*(domains[int(v)] for v in face))
                   if len(set(combo)) == 3 and frozenset(combo) in source_lookup]
        if len(choices) != 1 or choices[0][0] in used:
            raise ValueError("OPTCUTS_FACE_CORNER_IDENTITY_AMBIGUOUS")
        original, combo = choices[0]
        used.add(original)
        for ci, vertex in enumerate(local.mesh.faces[original]):
            source_corner = combo.index(int(vertex))
            corners[original,ci] = uv[uv_faces[row, source_corner]]
            identities[original,ci] = uv_faces[row, source_corner]
    return state_from_corner_uv(local.mesh, corners, corner_ids=identities)


def reparameterize(request: dict[str, object], output: Path, deadline: float) -> dict[str, object]:
    from .geometry import _edited_metadata, _face_list, _packed, _profile, geometry_report
    check_deadline(deadline)
    snapshot = require_snapshot(request)
    raw = request.get("faces")
    if raw is None:
        raise ValueError("REGION_FACE_IDS_REQUIRED")
    faces = _face_list(raw, snapshot.state)
    base = snapshot.state
    if any(set(faces).intersection(g.first_faces+g.second_faces) for g in base.groups):
        raise ValueError("REPARAMETERIZE_SHARED_REGION_REQUIRES_UNSHARE")
    backend = request.get("backend", "harmonic")
    initial_uv = request.get("initial_uv", "existing")
    if initial_uv not in ("existing", "geometry"):
        raise ValueError("UNKNOWN_REPARAMETERIZATION_INITIAL_UV")
    cut = cut_region(base.surface, faces, request.get("seam_edges", []), deadline)
    local_mesh, local = cut.surface.mesh, cut.surface
    record: dict[str,object] = {
        "backend": backend, "initial_uv": initial_uv, "faces": list(faces), "original_face_count": len(faces),
        "requested_seam_edges": [list(edge) for edge in cut.seam_edges],
        "temporary_original_vertex_ids": cut.original_vertex_ids.tolist(),
        "temporary_corner_vertex_ids": local_mesh.faces.tolist(),
        "old_region_chart_count": len(np.unique(chart_labels(base.surface)[list(faces)])),
    }
    output.mkdir(parents=True, exist_ok=True)
    if backend == "harmonic":
        corners, ids, loop = harmonic_uv(local, tuple(range(len(faces))))
        local = state_from_corner_uv(local_mesh, corners, corner_ids=ids)
        record["boundary_vertices"] = loop
        record["boundary_original_vertices"] = cut.original_vertex_ids[loop].tolist()
    elif backend in {"blender_lscm", "blender_abf", "blender_minimum_stretch"}:
        local, backend_report = _blender_unwrap(local, request, output, deadline)
        record["blender"] = backend_report
    elif backend == "optcuts":
        root = Path(__file__).resolve().parents[3]
        runtime = read_runtime(root)
        binary = Path(runtime["optcuts_binary"])
        folder = output / "native"
        folder.mkdir()
        (folder/"output").mkdir()
        path = folder/"input.obj"
        # Compact unused vertices while preserving an explicit face/corner map.
        used = np.unique(local_mesh.faces)
        compact = make_mesh(local_mesh.vertices[used], np.searchsorted(used,local_mesh.faces))
        source = state_from_corner_uv(compact, local.uv[local.uv_faces], corner_ids=local.uv_faces)
        if initial_uv == "geometry":
            # OptCuts otherwise trusts locally injective one-chart input UV
            # without checking global overlap (main.cpp initialization branch).
            # Supply only the requested cut geometry; let its own initializer
            # create UV, without isolating any original triangle beforehand.
            with path.open("w", encoding="utf-8", newline="\n") as stream:
                for vertex in compact.vertices:
                    stream.write("v " + " ".join(format(float(x), ".17g") for x in vertex) + "\n")
                for face in compact.faces:
                    stream.write("f " + " ".join(str(int(v) + 1) for v in face) + "\n")
        else:
            write_obj(path, source)
        remaining = max(0., deadline-time.monotonic())
        check_deadline(deadline)
        command = wsl_command(runtime, [linux_path(binary),"100",linux_path(path.resolve()),
            "0.999","1","0","4.1","1","0","regional"], remaining, folder.resolve())
        run_process(command, folder, remaining)
        outputs = list(folder.rglob("finalResult_mesh.obj"))
        if len(outputs) != 1:
            raise ValueError("OPTCUTS_OUTPUT_MISSING")
        imported = _import_optcuts(source, outputs[0])
        local = state_from_corner_uv(local_mesh, imported.uv[imported.uv_faces], corner_ids=imported.uv_faces)
        record.update(binary=str(binary), binary_sha256=file_hash(binary), input_sha256=file_hash(path),
                      output_sha256=file_hash(outputs[0]), original_vertex_ids=used.tolist())
    else:
        raise ValueError("UNKNOWN_REPARAMETERIZATION_BACKEND")
    check_deadline(deadline)
    # Restore original geometry and original face rows before embedding UV.
    # Only temporary vertices were split; the asset topology never changes.
    original_mesh = make_mesh(base.surface.mesh.vertices, base.surface.mesh.faces[list(faces)])
    original_local = state_from_corner_uv(original_mesh, local.uv[local.uv_faces], corner_ids=local.uv_faces)
    record["result_seam_edges"] = [list(original_mesh.edges[index]) for index in sorted(original_local.seams)]
    record["result_chart_count"] = int(chart_labels(original_local).max()) + 1
    record["result_corner_uv_ids"] = original_local.uv_faces.tolist()
    profile = _profile(request)
    proposed = _packed(embed_region(base,original_local,faces,None), profile,deadline=deadline)
    report = geometry_report(proposed,profile)
    record["geometry"] = report
    write_json(output/"reparameterization.json",record)
    if not report["hard_valid"]:
        return {"status":"REJECTED","execution_success":True,"reason":"REPARAMETERIZATION_GEOMETRY_REJECTED",
                "report":str(output/"reparameterization.json")}
    result = save_snapshot(proposed,output/"snapshot",_edited_metadata(snapshot,"uv.reparameterize",profile),snapshot)
    return {**snapshot_result(result),"status":"OK","execution_success":True,
            "geometry_passed":report["valid"],"hard_geometry_passed":True,"requires_rebake":True,
            "report":str(output/"reparameterization.json")}
