"""Adapters share immutable input geometry and are all evaluated by the same validator."""

import json
import sys
import time
from pathlib import Path
from typing import Any

import numpy as np

from .initialization import orient_and_scale
from .mesh import import_uv_obj, load_mesh, state_from_corner_uv
from .runtime import linux_path, run_process, wsl_command
from .types import MeshState


def baseline(
    root: Path,
    input_path: Path,
    method: str,
    runtime: dict[str, Any],
    folder: Path,
    seconds: float,
    seed: int,
    randomized_start: bool = False,
) -> tuple[MeshState, dict[str, Any]]:
    folder.mkdir(parents=True, exist_ok=False)
    mesh = load_mesh(input_path)
    started = time.monotonic()
    initializer_face = 0
    if method == "optcuts":
        if randomized_start:
            initializer_face = (seed * 2654435761) % len(mesh.faces)
            order = [initializer_face] + [i for i in range(len(mesh.faces)) if i != initializer_face]
            reordered = mesh.faces[order].copy()
            reordered[0] = np.roll(reordered[0], seed % 3)
            input_path = folder / "initial_order.obj"
            with input_path.open("w", encoding="utf-8") as stream:
                for vertex in mesh.vertices:
                    stream.write("v " + " ".join(format(float(x), ".17g") for x in vertex) + "\n")
                for face in reordered:
                    stream.write("f " + " ".join(str(int(x) + 1) for x in face) + "\n")
        (folder / "output").mkdir()
        command = wsl_command(
            runtime,
            [
                linux_path(Path(runtime["optcuts_binary"])),
                "100",
                linux_path(input_path),
                "0.999",
                "1",
                "0",
                "4.1",
                "1",
                "0",
                f"reseam_{seed}",
            ],
            seconds,
            folder,
        )
        run_process(command, folder, seconds)
        outputs = list(folder.rglob("finalResult_mesh.obj"))
        if len(outputs) != 1:
            raise RuntimeError("OPTCUTS_OUTPUT_MISSING")
        state = import_uv_obj(mesh, outputs[0])
    elif method == "xatlas":
        output = folder / "native.obj"
        run_process(
            [
                sys.executable,
                str(root / "scripts/baseline_worker.py"),
                "--input",
                str(input_path),
                "--output",
                str(output),
            ],
            folder,
            seconds,
        )
        state = import_uv_obj(mesh, output)
    elif method == "blender_smart":
        request = folder / "request.json"
        request.write_text(
            json.dumps(
                {
                    "mode": "smart",
                    "vertices": mesh.vertices.tolist(),
                    "faces": mesh.faces.tolist(),
                    "output": str(folder / "uv.json"),
                }
            ),
            encoding="utf-8",
        )
        run_process(
            [
                runtime["blender"],
                "--background",
                "--factory-startup",
                "--threads",
                "4",
                "--python",
                str(root / "scripts/blender_worker.py"),
                "--",
                str(request),
            ],
            folder,
            seconds,
        )
        result = json.loads((folder / "uv.json").read_text())
        edge_lookup = {edge: index for index, edge in enumerate(mesh.edges)}
        seams = frozenset(
            edge_lookup[tuple(edge)]
            for edge in result["seam_edges"]
            if len(mesh.edge_faces[edge_lookup[tuple(edge)]]) == 2
        )
        state = state_from_corner_uv(mesh, np.asarray(result["corner_uv"], dtype=float), seam_ids=seams)
    elif method == "partuv":
        command = wsl_command(
            runtime,
            [
                linux_path(Path(runtime["partuv_python"])),
                "-u",
                linux_path(root / "scripts/partuv_worker.py"),
                "--input",
                linux_path(input_path),
                "--output",
                linux_path(folder),
                "--checkpoint",
                linux_path(Path(runtime["partfield_checkpoint"])),
                "--sha256",
                runtime["partfield_sha256"],
                "--config",
                linux_path(root / "references/PartUV/config/config.yaml"),
                "--seed",
                str(seed),
            ],
            seconds,
        )
        run_process(command, folder, seconds)
        state = import_uv_obj(mesh, folder / "aligned.obj")
    else:
        raise ValueError(f"Unknown baseline: {method}")
    return orient_and_scale(state), {
        "backend": method,
        "seed": seed,
        "initializer_face_id": initializer_face,
        "seed_effect": "first_face_permutation"
        if randomized_start
        else "partfield_sampling_seed"
        if method == "partuv"
        else "deterministic_repeat",
        "seconds": time.monotonic() - started,
        "coordinate_evaluation": "canonical geometry, common optimal UV scale",
    }
