"""FBX polygon-vertex UV identity, independent of Blender coordinate welding."""

from pathlib import Path
from typing import Any, cast

import numpy as np


def _child(node: Any, name: bytes) -> Any | None:
    return next((item for item in node.elems if item.id == name), None)


def read_fbx_uv(path: Path) -> dict[str, dict[str, Any]]:
    # Blender's bundled parser is the same parser used by inspect_gt_blender.py.
    from io_scene_fbx import parse_fbx  # pyright: ignore[reportMissingImports]

    root, _ = parse_fbx.parse(str(path))
    objects = {item.props[0]: item for item in cast(Any, _child(root, b"Objects")).elems}
    parents = {link.props[1]: link.props[2] for link in cast(Any, _child(root, b"Connections")).elems
               if link.props[0] == b"OO" and objects.get(link.props[1]) is not None
               and objects[link.props[1]].id == b"Geometry"}
    result: dict[str, dict[str, Any]] = {}
    for identifier, geometry in objects.items():
        if geometry.id != b"Geometry" or geometry.props[-1] != b"Mesh":
            continue
        model = objects.get(parents.get(identifier))
        if model is None:
            continue
        name = model.props[1].split(b"\x00")[0].decode("utf-8", errors="replace")
        polygons: list[list[int]] = []
        polygon: list[int] = []
        for value in cast(Any, _child(geometry, b"PolygonVertexIndex")).props[0]:
            polygon.append(int(value if value >= 0 else -value - 1))
            if value < 0:
                polygons.append(polygon)
                polygon = []
        layers: dict[str, dict[str, Any]] = {}
        for layer in geometry.elems:
            if layer.id != b"LayerElementUV":
                continue
            layer_name = cast(Any, _child(layer, b"Name")).props[0].decode("utf-8", errors="replace")
            mapping = cast(Any, _child(layer, b"MappingInformationType")).props[0]
            reference = cast(Any, _child(layer, b"ReferenceInformationType")).props[0]
            record: dict[str, Any] = {"mapping": mapping.decode(), "reference": reference.decode()}
            if mapping == b"ByPolygonVertex" and reference == b"IndexToDirect":
                record["indices"] = np.asarray(cast(Any, _child(layer, b"UVIndex")).props[0], dtype=np.int64)
                record["uv"] = np.asarray(cast(Any, _child(layer, b"UV")).props[0], dtype=float).reshape(-1, 2)
            layers[layer_name] = record
        # Duplicate names cannot provide an unambiguous object provenance key.
        if name in result:
            result[name] = {"error": "AMBIGUOUS_FBX_MODEL_NAME"}
        else:
            shading: list[dict[str, object]] = []
            for layer in geometry.elems:
                if layer.id in (b"LayerElementNormal", b"LayerElementSmoothing"):
                    shading.append({"kind": layer.id.decode(),
                        "mapping": str(cast(Any, _child(layer, b"MappingInformationType")).props[0]),
                        "reference": str(cast(Any, _child(layer, b"ReferenceInformationType")).props[0])})
            result[name] = {"polygons": polygons, "layers": layers, "shading_layers": shading}
    return result


def restore_loop_uv_ids(
    raw: dict[str, Any] | None, layer_name: str, polygons: list[list[int]],
    loop_vertices: list[int], loop_uv: list[list[float]],
) -> tuple[list[int], dict[str, object]]:
    """Validate exact polygon/control-point ancestry and UV values before using IDs.

    Keys include control-point identity: equal UV direct IDs never merge separate
    3D vertices, including vertices with coincident xyz positions.
    """
    fallback = list(range(len(loop_vertices)))
    failure: str | None = None
    if raw is None or raw.get("error"):
        failure = "RAW_FBX_OBJECT_UNAVAILABLE_OR_AMBIGUOUS"
    elif raw.get("polygons") != polygons:
        failure = "RAW_FBX_POLYGON_OR_CONTROL_POINT_ANCESTRY_MISMATCH"
    elif [v for polygon in polygons for v in polygon] != loop_vertices:
        failure = "BLENDER_LOOP_ORDER_DOES_NOT_MATCH_ORIGINAL_POLYGONS"
    layer = raw.get("layers", {}).get(layer_name, {}) if raw else {}
    if failure is None and (layer.get("mapping") != "ByPolygonVertex"
                            or layer.get("reference") != "IndexToDirect"):
        failure = "RAW_FBX_UV_MAPPING_UNSUPPORTED_OR_MISSING"
    if failure is None:
        indices = np.asarray(layer["indices"], dtype=np.int64)
        uv = np.asarray(layer["uv"], dtype=float)
        if (indices.shape != (len(loop_vertices),) or np.any(indices < 0)
                or np.any(indices >= len(uv)) or not np.isfinite(uv).all()):
            failure = "RAW_FBX_UV_INDEX_INVALID"
        elif not np.allclose(uv[indices], np.asarray(loop_uv), rtol=0., atol=2e-6):
            failure = "RAW_FBX_UV_VALUES_DO_NOT_MATCH_IMPORTED_LOOPS"
        else:
            keys: dict[tuple[int, int], int] = {}
            ids = [keys.setdefault((vertex, int(uv_id)), len(keys))
                   for vertex, uv_id in zip(loop_vertices, indices, strict=True)]
            return ids, {"exact": True, "policy": "original FBX control-point ID + LayerElementUV UVIndex",
                         "mapping": "ByPolygonVertex", "reference": "IndexToDirect",
                         "uv_nodes": len(keys)}
    return fallback, {"exact": False, "policy": "conservative imported polygon-loop identity",
                      "reason": failure, "uv_nodes": len(fallback)}
