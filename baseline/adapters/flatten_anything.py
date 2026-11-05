"""Use the official FAM model and training loop; only adapt IO and extension loading."""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import random
import sys
import time
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    for key in ("input", "output", "source"):
        parser.add_argument("--" + key, type=Path, required=True)
    parser.add_argument("--points", type=int, default=10000)
    parser.add_argument("--iterations", type=int, default=10000)
    parser.add_argument("--seed", type=int, default=17)
    args = parser.parse_args()
    if args.points < 64 or args.iterations < 10:
        raise ValueError("FAM_REQUIRES_POINTS_GE_64_AND_ITERATIONS_GE_10")
    runtime = args.source.parent / "runtime/fam-addons"
    sys.path[:0] = [str(runtime), str(args.source)]
    extensions = list(runtime.glob("nearest_neighbors*.so"))
    if len(extensions) != 1:
        raise ModuleNotFoundError("BUILD_UPSTREAM_FAM_KNN_FOR_CURRENT_PYTHON_FIRST")
    name = "util.custo_knn_cpu.nearest_neighbors"
    spec = importlib.util.spec_from_file_location(name, extensions[0])
    if spec is None or spec.loader is None:
        raise ImportError("FAM_KNN_EXTENSION_LOADER_MISSING")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    os.environ.setdefault("MPLBACKEND", "Agg")
    import numpy as np
    import torch
    import trimesh
    from util import func
    from util.model import FlattenAnythingModel
    from util.workflow import train_fam

    random.seed(args.seed)
    np.random.seed(args.seed)
    torch.manual_seed(args.seed)
    torch.set_num_threads(1)
    torch.cuda.reset_peak_memory_stats()
    started = time.monotonic()
    mesh = trimesh.load(str(args.input), force="mesh", process=False)
    vertices = np.asarray(mesh.vertices, dtype=np.float32)
    normalized = func.normalize_coordinates(vertices)
    normals = func.normalize_normals(np.asarray(mesh.vertex_normals, dtype=np.float32))
    sampled, sampled_normals = func.sample_points_from_mesh_approx(
        normalized, np.asarray(mesh.faces, dtype=np.int32), args.points * 2, normals)
    points_tensor = torch.tensor(sampled).unsqueeze(0).float().cuda()
    normals_tensor = torch.tensor(func.normalize_normals(sampled_normals)).unsqueeze(0).float().cuda()
    selected = func.get_fps_idx(points_tensor, args.points)
    points = func.as_arr(func.index_points(points_tensor, selected).squeeze(0))
    sampled_normals = func.as_arr(func.index_points(normals_tensor, selected).squeeze(0))
    args.output.mkdir(exist_ok=True)
    print(json.dumps({"phase": "train", "points": args.points, "iterations": args.iterations}), flush=True)
    train_fam(points, sampled_normals, args.iterations, str(args.output))
    net = FlattenAnythingModel().cuda()
    net.load_state_dict(torch.load(args.output / "fam.pth", map_location="cuda", weights_only=True))
    with torch.no_grad():
        _, uv = net.unwrapping(torch.tensor(normalized).unsqueeze(0).float().cuda())
        uv = func.batch_normalize_uv_points(uv)
    np.savez_compressed(args.output / "uv.npz", uv=func.as_arr(uv.squeeze(0)))
    record = {"points": args.points, "iterations": args.iterations, "seed": args.seed,
              "seconds": time.monotonic() - started, "peak_cuda_bytes": torch.cuda.max_memory_allocated(),
              "torch": torch.__version__, "implementation": "official model.py and workflow.train_fam",
              "geometry": "process=False; inference at all original vertices; no remeshing",
              "seam_extraction": "continuous vertex UV; no undocumented seam repair or triangle isolation"}
    (args.output / "backend.json").write_text(json.dumps(record, indent=2), encoding="utf-8")
    print(json.dumps(record), flush=True)


if __name__ == "__main__":
    main()
