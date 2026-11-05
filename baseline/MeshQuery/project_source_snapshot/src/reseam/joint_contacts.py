"""Boundary contacts authorized by original vertex AND inherited UV provenance, never proximity welding."""

import numpy as np
from shapely import LineString, Point, Polygon, STRtree

from .types import IntArray, MeshState


def sheet_intersections(
    state: MeshState, sheet_ids: IntArray, contact_classes: tuple[int, ...] | None = None
) -> list[str]:
    if contact_classes is not None and len(contact_classes) != len(state.uv):
        return ["INVALID_CONTACT_PROVENANCE"]
    polygons = [Polygon(state.uv[face]) for face in state.uv_faces]
    pairs = STRtree(polygons).query(polygons, predicate="intersects")
    errors = []
    for a, b in zip(*pairs, strict=True):
        if a >= b or sheet_ids[a] != sheet_ids[b]:
            continue
        overlap = polygons[a].intersection(polygons[b])
        common = np.intersect1d(state.uv_faces[a], state.uv_faces[b])
        # Convex triangle intersection with zero area is exactly the common edge/vertex.
        if overlap.area == 0 and (len(common) == 2 or (len(common) == 1 and overlap.geom_type == "Point")):
            continue
        if len(common) == 1:
            overlap = overlap.difference(Point(state.uv[common[0]]).buffer(1e-10))
        elif len(common) == 2:
            overlap = overlap.difference(LineString(state.uv[common]).buffer(1e-10))
        if contact_classes is not None:
            allowed_point = False
            for vertex in np.intersect1d(state.mesh.faces[a], state.mesh.faces[b]):
                ca = int(np.flatnonzero(state.mesh.faces[a] == vertex)[0])
                cb = int(np.flatnonzero(state.mesh.faces[b] == vertex)[0])
                ua, ub = int(state.uv_faces[a, ca]), int(state.uv_faces[b, cb])
                if contact_classes[ua] == contact_classes[ub]:
                    if overlap.geom_type == "Point" and np.array_equal(state.uv[ua], state.uv[ub]):
                        allowed_point = True
                        break
                    allowed = (
                        Point(state.uv[ua]).buffer(1e-10).intersection(Point(state.uv[ub]).buffer(1e-10))
                    )
                    overlap = overlap.difference(allowed)
            if allowed_point:
                continue
        if not overlap.is_empty:
            errors.append(f"MEMBER_SELF_INTERSECTION:{a}:{b}")
            if len(errors) >= 10:
                return errors
    return errors
