"""Read artist FBX files in a disposable Blender process; never save over the inputs."""

import hashlib
import json
import sys
from pathlib import Path
from typing import Any

import bpy


def find_root(parent: list[int], value: int) -> int:
    while parent[value] != value:
        parent[value] = parent[parent[value]]
        value = parent[value]
    return value


def fbx_uv_topology(path: Path) -> dict[str, Any]:
    from io_scene_fbx import parse_fbx

    def child(node: Any, name: bytes) -> Any | None:
        return next((item for item in node.elems if item.id == name), None)

    root, _ = parse_fbx.parse(str(path))
    objects = {item.props[0]: item for item in child(root, b"Objects").elems}
    parents = {
        link.props[1]: link.props[2]
        for link in child(root, b"Connections").elems
        if link.props[0] == b"OO"
        and objects.get(link.props[1]) is not None
        and objects[link.props[1]].id == b"Geometry"
    }
    result = {}
    for identifier, geometry in objects.items():
        if geometry.id != b"Geometry" or geometry.props[-1] != b"Mesh":
            continue
        model = objects[parents[identifier]]
        name = model.props[1].split(b"\x00")[0].decode("utf-8", errors="replace")
        raw = child(geometry, b"PolygonVertexIndex").props[0]
        polygons, face = [], []
        for corner, value in enumerate(raw):
            face.append((int(value if value >= 0 else -value - 1), corner))
            if value < 0:
                polygons.append(face)
                face = []
        layers = {}
        for layer in geometry.elems:
            if layer.id != b"LayerElementUV":
                continue
            mapping = child(layer, b"MappingInformationType").props[0]
            reference = child(layer, b"ReferenceInformationType").props[0]
            if mapping != b"ByPolygonVertex" or reference != b"IndexToDirect":
                continue
            uv_ids = child(layer, b"UVIndex").props[0]
            edges: dict[tuple[int, int], list[tuple[int, int, int]]] = {}
            for fi, polygon in enumerate(polygons):
                for i, (a, ca) in enumerate(polygon):
                    b, cb = polygon[(i + 1) % len(polygon)]
                    key = (min(a, b), max(a, b))
                    ua, ub = (uv_ids[ca], uv_ids[cb]) if a < b else (uv_ids[cb], uv_ids[ca])
                    edges.setdefault(key, []).append((fi, int(ua), int(ub)))
            parent = list(range(len(polygons)))
            surface_parent = list(range(len(polygons)))

            seams = []
            for edge, adjacent in sorted(edges.items()):
                for item in adjacent[1:]:
                    surface_parent[find_root(surface_parent, item[0])] = find_root(
                        surface_parent, adjacent[0][0]
                    )
                if len(adjacent) != 2:
                    continue
                first, second = adjacent
                if first[1:] == second[1:]:
                    parent[find_root(parent, first[0])] = find_root(parent, second[0])
                else:
                    seams.append(edge)
            layers[child(layer, b"Name").props[0].decode()] = {
                "chart_count": len({find_root(parent, fi) for fi in range(len(polygons))}),
                "edge_connected_surface_components": len(
                    {find_root(surface_parent, fi) for fi in range(len(polygons))}
                ),
                "seam_edge_count": len(seams),
                "seam_vertex_pairs": seams,
                "original_boundary_edges": sum(len(v) == 1 for v in edges.values()),
                "nonmanifold_edges": sum(len(v) > 2 for v in edges.values()),
                "identity_source": "Original FBX polygon vertex IDs and UVIndex; no coordinate-based welding",
            }
        result[name] = layers
    return result


def main() -> None:
    source, destination = map(Path, sys.argv[sys.argv.index("--") + 1 :])
    records: list[dict[str, Any]] = []
    for path in sorted(source.glob("*.FBX")):
        bpy.ops.wm.read_factory_settings(use_empty=True)
        bpy.ops.import_scene.fbx(filepath=str(path), use_image_search=False, use_anim=False)
        topology = fbx_uv_topology(path)
        record: dict[str, Any] = {
            "file": path.name,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "blender_version": bpy.app.version_string,
            "meshes": [],
        }
        for obj in sorted(bpy.context.scene.objects, key=lambda item: item.name):
            if obj.type != "MESH":
                continue
            mesh = obj.data
            mesh.calc_loop_triangles()
            materials = []
            for material in mesh.materials:
                images = []
                if material and material.use_nodes:
                    for node in material.node_tree.nodes:
                        if node.type == "TEX_IMAGE" and node.image:
                            images.append(
                                {
                                    "name": node.image.name,
                                    "file": Path(node.image.filepath.replace("\\", "/")).name,
                                    "has_pixels": bool(node.image.has_data),
                                }
                            )
                materials.append({"name": material.name if material else None, "images": images})
            record["meshes"].append(
                {
                    "name": obj.name,
                    "vertices": [list(obj.matrix_world @ vertex.co) for vertex in mesh.vertices],
                    "polygons": [list(face.vertices) for face in mesh.polygons],
                    "triangles": [list(triangle.vertices) for triangle in mesh.loop_triangles],
                    "triangle_materials": [triangle.material_index for triangle in mesh.loop_triangles],
                    "triangle_polygon_ids": [triangle.polygon_index for triangle in mesh.loop_triangles],
                    "uv_layers": [
                        {
                            "name": layer.name,
                            "corner_uv": [
                                [list(layer.data[loop].uv) for loop in triangle.loops]
                                for triangle in mesh.loop_triangles
                            ],
                        }
                        for layer in mesh.uv_layers
                    ],
                    "materials": materials,
                    "modifiers": [modifier.type for modifier in obj.modifiers],
                    "geometry_scope": "Imported undeformed mesh with object transforms; armature deformation is not applied",
                    "uv_topology": topology.get(obj.name, {}),
                }
            )
        records.append(record)
    with destination.open("x", encoding="utf-8") as stream:
        json.dump({"records": records}, stream, ensure_ascii=False, indent=2)
    print(f"EXPORTED {len(records)} artist FBX records without modifying inputs")


if __name__ == "__main__":
    main()
