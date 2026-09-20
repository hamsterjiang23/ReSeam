"""Run native Python backends in a killable process."""

import argparse
from pathlib import Path

import numpy as np
import xatlas

from reseam.mesh import load_mesh, state_from_corner_uv, write_obj


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    mesh = load_mesh(args.input)
    mapping, faces, uv = xatlas.parametrize(mesh.vertices.astype(np.float32), mesh.faces.astype(np.uint32))
    if not np.array_equal(mapping[faces], mesh.faces):
        raise ValueError("xatlas changed face order/identity")
    state = state_from_corner_uv(mesh, np.asarray(uv[faces], dtype=float), corner_ids=faces)
    write_obj(args.output, state)


if __name__ == "__main__":
    main()
