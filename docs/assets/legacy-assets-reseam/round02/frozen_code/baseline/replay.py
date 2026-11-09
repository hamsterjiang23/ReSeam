"""Rebase archived input paths without changing any geometry, UV, groups or arrays."""

import argparse
import shutil
from pathlib import Path

import numpy as np

from reseam.agent_tools.state import (
    file_hash,
    load_snapshot,
    object_hash,
    read_object,
    save_snapshot,
    write_json,
)
from reseam.budget_state import BudgetState
from reseam.mesh import make_mesh, state_from_corner_uv


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    source, output = args.input.resolve(), args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    frozen = output / "inputs"
    frozen.mkdir()
    reference = frozen / "completed_dragon.glb"
    shutil.copy2(source / "completed_dragon.glb", reference)
    record = read_object(source / "snapshot.json")
    record.pop("state_hash")
    record["metadata"] = {"reference_glb": str(reference), "source_uv_present": True,
                          "source_manifest": [{"path": str(reference), "sha256": file_hash(reference)}]}
    temporary = output / "rebased_input"
    temporary.mkdir()
    shutil.copy2(source / "arrays.npz", temporary / "arrays.npz")
    if file_hash(temporary / "arrays.npz") != record["arrays_sha256"]:
        raise ValueError("ARCHIVED_ARRAY_HASH_MISMATCH")
    write_json(temporary / "snapshot.json", {**record, "state_hash": object_hash(record)})
    imported = load_snapshot(temporary)
    save_snapshot(imported.state, output / "parent", imported.metadata)
    save_snapshot(BudgetState(imported.state.surface.copy(), version=imported.state.version),
                  output / "unshared_parent", imported.metadata)
    mesh = make_mesh(np.array([[-1., -1., 0.], [1., -1., 0.], [1., 1., 0.], [-1., 1., 0.]]),
                     np.array([[0, 1, 2], [0, 2, 3]]))
    surface = state_from_corner_uv(mesh, mesh.vertices[mesh.faces, :2]*.25+.5, corner_ids=mesh.faces)
    save_snapshot(BudgetState(surface), output / "smoke_parent", {})
    if (source.parent / "protocol.json").exists():
        write_json(output / "protocol.json", {**read_object(source.parent / "protocol.json"),
                   "replayed_from": str(source), "parent": str(output / "parent/snapshot.json"),
                   "state_hash": load_snapshot(output / "parent").state_hash})
    print(output / "parent/snapshot.json")


if __name__ == "__main__":
    main()
