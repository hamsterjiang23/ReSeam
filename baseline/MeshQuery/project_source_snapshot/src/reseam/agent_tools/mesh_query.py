"""Queryable, hash-bound original mesh attributes for Agent seam planning."""

import json
import math
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import cast

import numpy as np
from scipy.spatial import KDTree
from scipy.stats import rankdata

from ..mesh import chart_labels
from ..types import FloatArray, IntArray
from .state import Snapshot, check_deadline, file_hash, object_hash, read_object, require_snapshot, write_json


@dataclass
class _BVH:
    low: FloatArray
    high: FloatArray
    indices: IntArray
    left: "_BVH | None" = None
    right: "_BVH | None" = None


def _build_bvh(triangles: FloatArray, indices: IntArray) -> _BVH:
    selected = triangles[indices]
    node = _BVH(selected.min(axis=(0, 1)), selected.max(axis=(0, 1)), indices)
    if len(indices) > 16:
        axis = int(np.argmax(node.high - node.low))
        order = np.argsort(selected.mean(axis=1)[:, axis], kind="stable")
        mid = len(indices) // 2
        node.indices = np.empty(0, dtype=np.int64)
        node.left = _build_bvh(triangles, indices[order[:mid]])
        node.right = _build_bvh(triangles, indices[order[mid:]])
    return node


def _ray_hit(root: _BVH, triangles: FloatArray, origin: FloatArray, direction: FloatArray,
             limit: float, epsilon: float) -> float | None:
    stack = [root]
    best = limit
    found = False
    while stack:
        node = stack.pop()
        parallel = np.abs(direction) < 1e-14
        if np.any(parallel & ((origin < node.low) | (origin > node.high))):
            continue
        inv = np.divide(1., direction, out=np.zeros(3), where=~parallel)
        near = np.where(parallel, -np.inf, np.minimum((node.low-origin)*inv, (node.high-origin)*inv))
        far = np.where(parallel, np.inf, np.maximum((node.low-origin)*inv, (node.high-origin)*inv))
        if max(float(near.max()), 0.) > min(float(far.min()), best):
            continue
        if node.left is not None and node.right is not None:
            stack.extend((node.left, node.right))
            continue
        tri = triangles[node.indices]
        e1, e2 = tri[:, 1]-tri[:, 0], tri[:, 2]-tri[:, 0]
        h = np.cross(np.broadcast_to(direction, e2.shape), e2)
        determinant = np.einsum("ij,ij->i", e1, h)
        valid = np.abs(determinant) > epsilon**2
        inverse = np.divide(1., determinant, out=np.zeros_like(determinant), where=valid)
        s = origin-tri[:, 0]
        u = np.einsum("ij,ij->i", s, h)*inverse
        q = np.cross(s, e1)
        v = q@direction*inverse
        distance = np.einsum("ij,ij->i", e2, q)*inverse
        hit = valid & (u >= -1e-10) & (v >= -1e-10) & (u+v <= 1+1e-10)
        hit &= (distance > epsilon) & (distance < best)
        if np.any(hit):
            best = float(distance[hit].min())
            found = True
    return best if found else None


def _components(snapshot: Snapshot) -> IntArray:
    mesh = snapshot.state.surface.mesh
    neighbors: list[list[int]] = [[] for _ in mesh.faces]
    for entries in mesh.edge_faces:
        for f, _ in entries:
            neighbors[f].extend(g for g, _ in entries if g != f)
    labels = np.full(len(mesh.faces), -1, dtype=np.int64)
    component = 0
    for seed in range(len(labels)):
        if labels[seed] >= 0:
            continue
        pending = [seed]
        labels[seed] = component
        while pending:
            for neighbor in neighbors[pending.pop()]:
                if labels[neighbor] < 0:
                    labels[neighbor] = component
                    pending.append(neighbor)
        component += 1
    return labels


def _percentiles(values: FloatArray) -> FloatArray:
    return (rankdata(values, method="min")-1)/max(1, len(values)-1)


def _semantic_labels(snapshot: Snapshot) -> list[str | None]:
    labels: list[str | None] = [None]*len(snapshot.state.surface.mesh.faces)
    regions = snapshot.metadata.get("regions", [])
    if isinstance(regions, list):
        for region in regions:
            if isinstance(region, dict):
                for face in region.get("faces", []):
                    labels[int(face)] = str(region.get("name", "unlabelled"))
    return labels


def _edge_runs(edges: list[int], endpoints: tuple[tuple[int, int], ...]) -> list[list[int]]:
    incident: dict[int, list[int]] = {}
    for edge in edges:
        for vertex in endpoints[edge]:
            incident.setdefault(vertex, []).append(edge)
    remaining = set(edges)
    runs: list[list[int]] = []
    while remaining:
        seed = min(remaining)
        remaining.remove(seed)
        run, pending = [seed], [seed]
        while pending:
            for vertex in endpoints[pending.pop()]:
                for edge in incident[vertex]:
                    if edge in remaining:
                        remaining.remove(edge)
                        pending.append(edge)
                        run.append(edge)
        runs.append(sorted(run))
    return runs


def _topology_genus(faces: IntArray) -> tuple[int, int] | None:
    """Return genus and boundary count only for connected orientable manifolds."""
    vertices = np.unique(faces)
    # Positions do not determine this purely combinatorial certificate.
    edges: dict[tuple[int, int], list[tuple[int, int]]] = {}
    incident_faces: dict[int, list[int]] = {}
    edge_faces: dict[tuple[int, int], list[int]] = {}
    for face_id, face in enumerate(faces):
        for vertex in face:
            incident_faces.setdefault(int(vertex), []).append(face_id)
        for i in range(3):
            a, b = int(face[i]), int(face[(i+1) % 3])
            edges.setdefault((min(a, b), max(a, b)), []).append((a, b))
            edge_faces.setdefault((min(a, b), max(a, b)), []).append(face_id)
    if any(len(entries) > 2 or (len(entries) == 2 and entries[0] != entries[1][::-1])
           for entries in edges.values()):
        return None
    adjacency: list[list[int]] = [[] for _ in faces]
    for members in edge_faces.values():
        if len(members) == 2:
            a, b = members
            adjacency[a].append(b)
            adjacency[b].append(a)
    seen, pending = {0}, [0]
    while pending:
        for neighbor in adjacency[pending.pop()]:
            if neighbor not in seen:
                seen.add(neighbor)
                pending.append(neighbor)
    if len(seen) != len(faces):
        return None
    boundary = [edge for edge, entries in edges.items() if len(entries) == 1]
    degree: dict[int, int] = {}
    for a, b in boundary:
        degree[a] = degree.get(a, 0)+1
        degree[b] = degree.get(b, 0)+1
    if any(value != 2 for value in degree.values()):
        return None
    loops = len(_edge_runs(list(range(len(boundary))), tuple(boundary)))
    chi = len(vertices)-len(edges)+len(faces)
    numerator = 2-loops-chi
    # Vertex links must be a circle/interior or interval/boundary, not a bowtie.
    for vertex in vertices:
        star = faces[incident_faces[int(vertex)]]
        link = [tuple(int(v) for v in face if v != vertex) for face in star]
        if any(len(pair) != 2 for pair in link):
            return None
        if len(_edge_runs(list(range(len(link))), cast(tuple[tuple[int, int], ...], tuple(link)))) != 1:
            return None
    if numerator < 0 or numerator % 2:
        return None
    return numerator//2, loops


def _handle_candidate(snapshot: Snapshot, faces: list[int], genus: int,
                      deadline: float) -> list[int] | None:
    from .regional_cut import cut_region

    mesh = snapshot.state.surface.mesh
    members = set(faces)
    edge_ids = [i for i, entries in enumerate(mesh.edge_faces) if any(f in members for f, _ in entries)]
    adjacent: dict[int, list[tuple[int, int]]] = {}
    for edge in edge_ids:
        a, b = mesh.edges[edge]
        adjacent.setdefault(a, []).append((b, edge))
        adjacent.setdefault(b, []).append((a, edge))
    root = min(adjacent)
    parent = {root: root}
    parent_edge: dict[int, int] = {}
    pending = [root]
    for vertex in pending:
        for neighbor, edge in sorted(adjacent[vertex]):
            if neighbor not in parent:
                parent[neighbor], parent_edge[neighbor] = vertex, edge
                pending.append(neighbor)
    cycles: list[list[int]] = []
    tree_edges = set(parent_edge.values())
    for edge in edge_ids:
        if edge in tree_edges:
            continue
        check_deadline(deadline)
        a, b = mesh.edges[edge]
        ancestors = {a}
        cursor = a
        while parent[cursor] != cursor:
            cursor = parent[cursor]
            ancestors.add(cursor)
        right: list[int] = []
        while b not in ancestors:
            right.append(parent_edge[b])
            b = parent[b]
        left: list[int] = []
        while a != b:
            left.append(parent_edge[a])
            a = parent[a]
        cycles.append(sorted([edge, *left, *right]))
    cycles.sort(key=lambda ids: (sum(float(np.linalg.norm(mesh.vertices[mesh.edges[e][0]]-
                                                         mesh.vertices[mesh.edges[e][1]])) for e in ids), ids))
    for cycle in cycles[:128]:
        check_deadline(deadline)
        cut = cut_region(snapshot.state.surface, tuple(faces), [list(mesh.edges[e]) for e in cycle], deadline)
        topology = _topology_genus(cut.surface.mesh.faces)
        if topology is not None and topology[0] < genus:
            return cycle
    return None


def _features(snapshot: Snapshot, rays: int, deadline: float) -> dict[str, object]:
    mesh = snapshot.state.surface.mesh
    triangles = mesh.vertices[mesh.faces]
    cross = np.cross(triangles[:, 1]-triangles[:, 0], triangles[:, 2]-triangles[:, 0])
    normals = cross/np.maximum(np.linalg.norm(cross, axis=1)[:, None], 1e-30)
    centers = triangles.mean(axis=1)
    edges = np.asarray(mesh.edges, dtype=np.int64)
    midpoint = mesh.vertices[edges].mean(axis=1)
    length = np.linalg.norm(mesh.vertices[edges[:, 1]]-mesh.vertices[edges[:, 0]], axis=1)
    angle_sum = np.zeros(len(mesh.vertices))
    for corner in range(3):
        a = triangles[:, (corner+1)%3]-triangles[:, corner]
        b = triangles[:, (corner+2)%3]-triangles[:, corner]
        cosine = np.einsum("ij,ij->i", a, b)/np.maximum(np.linalg.norm(a, axis=1)*np.linalg.norm(b, axis=1), 1e-30)
        np.add.at(angle_sum, mesh.faces[:, corner], np.arccos(np.clip(cosine, -1, 1)))
    boundary_vertices = {v for edge, entries in zip(mesh.edges, mesh.edge_faces, strict=True)
                         if len(entries) == 1 for v in edge}
    curvature = np.asarray([math.pi if v in boundary_vertices else 2*math.pi for v in range(len(angle_sum))])-angle_sum
    edge_curvature = curvature[edges].mean(axis=1)
    dihedral = np.zeros(len(edges))
    edge_normals = np.zeros((len(edges), 3))
    for i, entries in enumerate(mesh.edge_faces):
        edge_normals[i] = normals[[f for f, _ in entries]].mean(axis=0)
        if len(entries) == 2:
            dihedral[i] = math.acos(float(np.clip(normals[entries[0][0]]@normals[entries[1][0]], -1, 1)))
    edge_normals /= np.maximum(np.linalg.norm(edge_normals, axis=1)[:, None], 1e-30)
    components = _components(snapshot)
    open_components = {int(components[entries[0][0]]) for entries in mesh.edge_faces if len(entries) != 2}
    ao: list[float | None] = [None]*len(edges)
    thickness: list[float | None] = [None]*len(edges)
    if rays:
        bvh = _build_bvh(triangles, np.arange(len(triangles), dtype=np.int64))
        diagonal = float(np.linalg.norm(np.ptp(mesh.vertices, axis=0)))
        epsilon = max(diagonal*1e-7, 1e-12)
        for i, normal in enumerate(edge_normals):
            if i % 32 == 0:
                check_deadline(deadline)
            tangent = np.cross(normal, np.eye(3)[int(np.argmin(np.abs(normal)))])
            tangent /= max(float(np.linalg.norm(tangent)), 1e-30)
            bitangent = np.cross(normal, tangent)
            hits = 0
            for sample in range(rays):
                z = (sample+.5)/rays
                phi = sample*math.pi*(3-math.sqrt(5))
                direction = normal*z+math.sqrt(1-z*z)*(tangent*math.cos(phi)+bitangent*math.sin(phi))
                hits += _ray_hit(bvh, triangles, midpoint[i]+normal*epsilon*4, direction,
                                 diagonal*.3, epsilon) is not None
            ao[i] = hits/rays
            if int(components[mesh.edge_faces[i][0][0]]) not in open_components:
                distances: list[float] = []
                for sample in range(4):
                    phi = sample*math.pi/2
                    direction = -normal*math.cos(math.radians(8)) + math.sin(math.radians(8))*(
                        tangent*math.cos(phi)+bitangent*math.sin(phi))
                    distance = _ray_hit(bvh, triangles, midpoint[i]-normal*epsilon*4,
                                        direction, diagonal*2, epsilon)
                    if distance is not None:
                        distances.append(distance)
                if distances:
                    thickness[i] = float(np.median(distances))
    return {"centers": centers, "normals": normals, "midpoint": midpoint, "length": length,
            "dihedral": dihedral, "curvature": edge_curvature, "edge_normals": edge_normals,
            "components": components, "ao": ao, "thickness": thickness}


def index_mesh(request: dict[str, object], output: Path, deadline: float) -> dict[str, object]:
    snapshot = require_snapshot(request)
    check_deadline(deadline)
    rays = int(str(request.get("ao_rays", 8)))
    if rays < 0 or rays > 64:
        raise ValueError("AO_RAYS_RANGE_0_64")
    features = _features(snapshot, rays, deadline)
    mesh = snapshot.state.surface.mesh
    centers = cast(FloatArray, features["centers"])
    normals = cast(FloatArray, features["normals"])
    midpoint = cast(FloatArray, features["midpoint"])
    length = cast(FloatArray, features["length"])
    dihedral = cast(FloatArray, features["dihedral"])
    curvature = cast(FloatArray, features["curvature"])
    edge_normals = cast(FloatArray, features["edge_normals"])
    components = cast(IntArray, features["components"])
    ao = cast(list[float | None], features["ao"])
    thickness = cast(list[float | None], features["thickness"])
    labels = _semantic_labels(snapshot)
    charts = chart_labels(snapshot.state.surface)
    low = mesh.vertices.min(axis=0)
    span = max(float(np.ptp(mesh.vertices, axis=0).max()), 1e-30)
    output.mkdir(parents=True, exist_ok=True)
    database = output / "mesh.db"
    if database.exists():
        raise ValueError("INDEX_OUTPUT_EXISTS")
    connection = sqlite3.connect(database)
    try:
        connection.executescript('''
CREATE TABLE collection(id INTEGER PRIMARY KEY, asset_id TEXT, snapshot TEXT);
CREATE TABLE mesh(id INTEGER PRIMARY KEY, state_hash TEXT, geometry_hash TEXT, scale REAL);
CREATE TABLE component(id INTEGER PRIMARY KEY, face_count INTEGER, area REAL, closed INTEGER);
CREATE TABLE face(id INTEGER PRIMARY KEY, v0 INTEGER, v1 INTEGER, v2 INTEGER, component_id INTEGER,
 chart_id INTEGER, label TEXT, area_raw REAL, area_pct REAL, x REAL,y REAL,z REAL,nx REAL,ny REAL,nz REAL);
CREATE TABLE edge(id INTEGER PRIMARY KEY, v0 INTEGER,v1 INTEGER,component_id INTEGER,label TEXT,
 semantic_boundary INTEGER,boundary INTEGER,nonmanifold INTEGER,seam INTEGER,length_raw REAL,length_pct REAL,
 dihedral_raw REAL,dihedral_pct REAL,curvature_raw REAL,curvature_pct REAL,ao REAL,thickness_raw REAL,
 x REAL,y REAL,z REAL,nx REAL,ny REAL,nz REAL);
CREATE TABLE face_edge(face_id INTEGER, edge_id INTEGER, corner INTEGER, PRIMARY KEY(face_id,edge_id));
CREATE TABLE edge_neighbor(edge_id INTEGER, neighbor_id INTEGER, PRIMARY KEY(edge_id,neighbor_id));
CREATE TABLE feature(id INTEGER PRIMARY KEY, type TEXT, axis INTEGER, offset REAL, score REAL, evidence TEXT,
 edge_ids TEXT, seed_edge INTEGER);
CREATE INDEX face_label ON face(label);
CREATE INDEX edge_label ON edge(label);
''')
        geometry_hash = object_hash({"vertices": mesh.vertices.tolist(), "faces": mesh.faces.tolist()})
        connection.execute("INSERT INTO collection VALUES(0,?,?)", (snapshot.asset_id, str(snapshot.root)))
        connection.execute("INSERT INTO mesh VALUES(0,?,?,?)", (snapshot.state_hash, geometry_hash, span))
        for component in np.unique(components):
            mask = components == component
            closed = all(len(entries) == 2 for entries in mesh.edge_faces if components[entries[0][0]] == component)
            connection.execute("INSERT INTO component VALUES(?,?,?,?)",
                               (int(component), int(mask.sum()), float(mesh.areas[mask].sum()), int(closed)))
        area_pct, length_pct = _percentiles(mesh.areas), _percentiles(length)
        dihedral_pct, curvature_pct = _percentiles(dihedral), _percentiles(curvature)
        for i, face in enumerate(mesh.faces):
            connection.execute("INSERT INTO face VALUES("+",".join(["?"]*15)+")",
                               (i, *map(int, face), int(components[i]), int(charts[i]), labels[i],
                                float(mesh.areas[i]), float(area_pct[i]), *map(float, (centers[i]-low)/span),
                                *map(float, normals[i])))
        incident: dict[int, list[int]] = {}
        for i, (edge, entries) in enumerate(zip(mesh.edges, mesh.edge_faces, strict=True)):
            check_deadline(deadline)
            member_labels = {labels[f] for f, _ in entries}
            connection.execute("INSERT INTO edge VALUES("+",".join(["?"]*23)+")",
                               (i, *edge, int(components[entries[0][0]]), labels[entries[0][0]],
                                int(len(member_labels)>1), int(len(entries)==1), int(len(entries)>2),
                                int(i in snapshot.state.surface.seams), float(length[i]), float(length_pct[i]),
                                float(dihedral[i]), float(dihedral_pct[i]), float(curvature[i]),
                                float(curvature_pct[i]), ao[i], thickness[i],
                                *map(float, (midpoint[i]-low)/span), *map(float, edge_normals[i])))
            connection.executemany("INSERT INTO face_edge VALUES(?,?,?)", [(f, i, c) for f, c in entries])
            for vertex in edge:
                incident.setdefault(vertex, []).append(i)
        neighbors = {(i, j) for values in incident.values() for i in values for j in values if i != j}
        connection.executemany("INSERT INTO edge_neighbor VALUES(?,?)", sorted(neighbors))
        tree = KDTree(midpoint)
        sample = midpoint[np.linspace(0, len(midpoint)-1, min(1500, len(midpoint)), dtype=int)]
        diagonal = float(np.linalg.norm(np.ptp(mesh.vertices, axis=0)))
        for axis in range(3):
            offset = float((mesh.vertices[:, axis].min()+mesh.vertices[:, axis].max())/2)
            reflected = sample.copy()
            reflected[:, axis] = 2*offset-reflected[:, axis]
            distances, _ = tree.query(reflected)
            score = float(np.mean(distances <= .04*diagonal))
            if float(np.median(distances)) < .06*diagonal and score >= .4:
                connection.execute("INSERT INTO feature VALUES(?,?,?,?,?,?,?,?)", (axis, "symmetry_plane", axis,
                                   offset, score, "heuristic edge-midpoint reflection; not calibrated similarity", "[]", None))
        next_feature = 3
        transitions = [i for i, entries in enumerate(mesh.edge_faces)
                       if len({labels[f] for f, _ in entries}) > 1 or curvature_pct[i] >= .9 or dihedral_pct[i] >= .9]
        for run in _edge_runs(transitions, mesh.edges):
            check_deadline(deadline)
            if len(run) < 6:
                continue
            points = midpoint[run]
            centered = points-points.mean(axis=0)
            _, _, axes = np.linalg.svd(centered, full_matrices=False)
            planar = centered@axes[:2].T
            radius = float(np.linalg.norm(planar, axis=1).mean())
            thickness = float(np.ptp(centered@axes[2]))
            angles = np.sort(np.arctan2(planar[:, 1], planar[:, 0]))
            coverage = 1-float(np.diff(np.r_[angles, angles[0]+2*np.pi]).max())/(2*np.pi)
            if radius > 1e-12 and thickness/radius <= .15 and coverage >= .9:
                connection.execute("INSERT INTO feature VALUES(?,?,?,?,?,?,?,?)", (next_feature, "ring", None,
                                   None, coverage, "PCA thickness/radius <= .15; angular coverage >= .9",
                                   json.dumps(run), run[0]))
                next_feature += 1
        for run in _edge_runs(np.flatnonzero(dihedral_pct >= .92).tolist(), mesh.edges):
            if len(run) >= 6:
                connection.execute("INSERT INTO feature VALUES(?,?,?,?,?,?,?,?)", (next_feature, "curvature_ridge",
                                   None, None, float(dihedral_pct[run].mean()), "connected high dihedral rank >= .92",
                                   json.dumps(run), run[0]))
                next_feature += 1
        for component in np.unique(components):
            check_deadline(deadline)
            faces = np.flatnonzero(components == component).tolist()
            topology = _topology_genus(mesh.faces[faces])
            if topology is not None and topology[0] > 0:
                cycle = _handle_candidate(snapshot, faces, topology[0], deadline)
                connection.execute("INSERT INTO feature VALUES(?,?,?,?,?,?,?,?)", (next_feature,
                                   "genus_neck" if cycle else "genus_unlocalized", None, None, topology[0],
                                   "orientable manifold genus; neck verified by temporary cut" if cycle else
                                   "orientable manifold genus; no neck found within 128 fundamental cycles",
                                   json.dumps(cycle or []), cycle[0] if cycle else None))
                next_feature += 1
        connection.commit()
    finally:
        connection.close()
    check_deadline(deadline)
    record: dict[str, object] = {
        "schema_version": 1, "asset_id": snapshot.asset_id, "state_hash": snapshot.state_hash,
        "geometry_hash": geometry_hash, "database": str(database.resolve()), "database_sha256": file_hash(database),
        "geometry_version": geometry_hash, "uv_semantic_version": snapshot.state_hash,
        "face_count": len(mesh.faces), "edge_count": len(mesh.edges), "ao_rays": rays,
        "coordinate_transform": {"origin": low.tolist(), "scale": span},
        "polygon_topology": snapshot.metadata.get("polygon_topology"),
        "definitions": {"curvature": "boundary-aware vertex angle deficit, mean of edge endpoints",
                        "ao": "uniform Fibonacci hemisphere hit fraction; NULL if disabled",
                        "thickness": "median four inward 8-degree cone hits; NULL for open components or misses"},
    }
    record["index_hash"] = object_hash(record)
    write_json(output / "index.json", record)
    return {"index": str((output / "index.json").resolve()), "index_hash": record["index_hash"],
            "face_count": len(mesh.faces), "edge_count": len(mesh.edges), "geometry_modified": False,
            "uv_modified": False, "state_hash": snapshot.state_hash}


def load_index(request: dict[str, object], snapshot: Snapshot) -> tuple[sqlite3.Connection, dict[str, object]]:
    value = request.get("index")
    if not isinstance(value, str):
        raise TypeError("MESH_INDEX_REQUIRED")
    manifest = Path(value)
    if manifest.is_dir():
        manifest /= "index.json"
    record = read_object(manifest)
    if record.get("schema_version") != 1:
        raise ValueError("MESH_INDEX_SCHEMA_VERSION_UNSUPPORTED")
    digest = record.pop("index_hash", None)
    if digest != object_hash(record):
        raise ValueError("MESH_INDEX_MANIFEST_HASH_MISMATCH")
    record["index_hash"] = digest
    if record.get("state_hash") != snapshot.state_hash or record.get("asset_id") != snapshot.asset_id:
        raise ValueError("STALE_MESH_INDEX")
    if request.get("index_hash", digest) != digest:
        raise ValueError("STALE_MESH_INDEX_REQUEST")
    database = Path(str(record["database"]))
    if file_hash(database) != record["database_sha256"]:
        raise ValueError("MESH_INDEX_DATABASE_HASH_MISMATCH")
    connection = sqlite3.connect(database.resolve().as_uri()+"?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    return connection, record


def query_rows(connection: sqlite3.Connection, sql: str, deadline: float) -> list[dict[str, object]]:
    allowed = {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_FUNCTION, sqlite3.SQLITE_RECURSIVE}
    def authorize(action: int, arg1: str | None, arg2: str | None, db: str | None, trigger: str | None) -> int:
        del arg1, db, trigger
        if action == sqlite3.SQLITE_FUNCTION and arg2 in {"load_extension", "writefile", "readfile"}:
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK if action in allowed else sqlite3.SQLITE_DENY
    connection.set_authorizer(authorize)
    connection.set_progress_handler(lambda: int(time.monotonic() >= deadline), 1000)
    check_deadline(deadline)
    try:
        cursor = connection.execute(sql)
        rows: list[sqlite3.Row] = []
        while batch := cursor.fetchmany(2048):
            check_deadline(deadline)
            rows.extend(batch)
            if len(rows) > 1_000_000:
                raise ValueError("MESH_QUERY_RESULT_TOO_LARGE: narrow query; no truncated selection returned")
    except sqlite3.Error as error:
        check_deadline(deadline)
        raise ValueError("MESH_QUERY_REJECTED:"+str(error)) from error
    finally:
        connection.set_authorizer(None)
        connection.set_progress_handler(None, 0)
    check_deadline(deadline)
    return [dict(row) for row in rows]


def query_mesh(request: dict[str, object], output: Path, deadline: float) -> dict[str, object]:
    snapshot = require_snapshot(request)
    sql = request.get("sql")
    if not isinstance(sql, str) or not sql.strip():
        raise ValueError("SQL_QUERY_REQUIRED")
    connection, manifest = load_index(request, snapshot)
    try:
        rows = query_rows(connection, sql, deadline)
    finally:
        connection.close()
    output.mkdir(parents=True, exist_ok=True)
    selection = {"state_hash": snapshot.state_hash, "index_hash": manifest["index_hash"], "sql": sql,
                 "row_count": len(rows), "rows": rows}
    write_json(output / "selection.json", selection)
    numeric: dict[str, object] = {}
    if rows:
        for key in rows[0]:
            values = [float(cast(int | float, row[key])) for row in rows if isinstance(row[key], (int, float))]
            if values:
                numeric[key] = {"min": min(values), "max": max(values), "mean": sum(values)/len(values)}
    return {"selection": str((output / "selection.json").resolve()), "row_count": len(rows),
            "preview": rows[:20], "numeric_summary": numeric, "state_hash": snapshot.state_hash,
            "index_hash": manifest["index_hash"], "geometry_modified": False, "uv_modified": False}


def query_selection(snapshot: Snapshot, index_path: str | Path, sql: str,
                    deadline: float) -> dict[str, object]:
    """Resolve an explicitly aliased SQL selection to stable mesh identities."""
    connection, _ = load_index({"index": str(index_path)}, snapshot)
    try:
        rows = query_rows(connection, sql, deadline)
    finally:
        connection.close()
    entities = [entity for entity in ("edge", "face") if rows and f"{entity}_id" in rows[0]]
    if not rows:
        raise ValueError("SQL_SELECTION_EMPTY")
    if len(entities) != 1:
        raise ValueError("SQL_SELECTION_REQUIRES_EDGE_ID_OR_FACE_ID_ALIAS")
    entity = entities[0]
    ids = [row[f"{entity}_id"] for row in rows]
    count = len(snapshot.state.surface.mesh.edges if entity == "edge" else snapshot.state.surface.mesh.faces)
    if any(type(value) is not int or value < 0 or value >= count for value in ids):
        raise ValueError("SQL_SELECTION_ID_OUT_OF_RANGE")
    return {"entity": entity, "ids": sorted(set(cast(list[int], ids)))}
