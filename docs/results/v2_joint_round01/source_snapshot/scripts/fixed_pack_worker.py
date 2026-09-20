"""Isolated xatlas packing-only adapter; existing UV topology is never reparameterized."""

import json
import sys
from importlib.metadata import version
from pathlib import Path

import numpy as np
import xatlas


def main() -> None:
    request = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    if request["backend"] != "xatlas":
        raise ValueError("This worker handles xatlas only")
    atlas = xatlas.Atlas()
    faces = np.asarray(request["uv_faces"], dtype=np.uint32)
    atlas.add_uv_mesh(np.asarray(request["uv"], dtype=np.float32), faces)
    options = xatlas.PackOptions()
    options.resolution = request["resolution"]
    options.padding = request["padding"]
    options.bilinear = True
    # Axis alignment/90 degree swaps can reflect charts. The caller pre-rotates.
    options.rotate_charts = False
    options.rotate_charts_to_axis = False
    atlas.generate(pack_options=options)
    mapping, indices, uv = atlas[0]
    if atlas.atlas_count != 1 or not np.array_equal(mapping[indices], faces):
        raise ValueError("XATLAS_FACE_IDENTITY_OR_ATLAS_COUNT")
    # Recover pixel aspect before fitting a rectangular result into a square.
    uv = uv * np.asarray([atlas.width, atlas.height])
    low, high = uv.min(axis=0), uv.max(axis=0)
    border = request["border"] / request["resolution"]
    uv = (uv - (low + high) / 2) * ((1 - 2 * border) / float((high - low).max())) + 0.5
    result = {
        "corner_uv": uv[indices].tolist(),
        "version": version("xatlas"),
        "atlas_width": atlas.width,
        "atlas_height": atlas.height,
    }
    Path(request["output"]).write_text(json.dumps(result, allow_nan=False), encoding="utf-8")


if __name__ == "__main__":
    main()
