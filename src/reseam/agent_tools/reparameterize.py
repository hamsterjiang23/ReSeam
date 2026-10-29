"""Explicit regional parameterization with immutable original face identities."""

import time
from pathlib import Path

import numpy as np
from scipy.sparse import csr_matrix
from scipy.sparse.linalg import spsolve

from ..budget_state import embed_region
from ..mesh import make_mesh, read_obj, state_from_corner_uv, write_obj
from ..runtime import linux_path, read_runtime, run_process, wsl_command
from ..types import FloatArray, IntArray, MeshState
from .state import check_deadline, file_hash, require_snapshot, save_snapshot, snapshot_result, write_json


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
    snapshot = require_snapshot(request)
    raw = request.get("faces")
    if raw is None:
        raise ValueError("REGION_FACE_IDS_REQUIRED")
    faces = _face_list(raw, snapshot.state)
    base = snapshot.state
    if any(set(faces).intersection(g.first_faces+g.second_faces) for g in base.groups):
        raise ValueError("REPARAMETERIZE_SHARED_REGION_REQUIRES_UNSHARE")
    backend = request.get("backend", "harmonic")
    local_mesh = make_mesh(base.surface.mesh.vertices, base.surface.mesh.faces[list(faces)])
    local = state_from_corner_uv(local_mesh, base.surface.uv[base.surface.uv_faces[list(faces)]],
                                 corner_ids=base.surface.uv_faces[list(faces)])
    record: dict[str,object] = {"backend": backend, "faces": list(faces), "original_face_count": len(faces)}
    output.mkdir(parents=True, exist_ok=True)
    if backend == "harmonic":
        corners, ids, loop = harmonic_uv(base.surface, faces)
        local = state_from_corner_uv(local_mesh, corners, corner_ids=ids)
        record["boundary_vertices"] = loop
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
    profile = _profile(request)
    proposed = _packed(embed_region(base,local,faces,None), profile,deadline=deadline)
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
