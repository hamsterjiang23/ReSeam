"""Isolated official PartUV wheel adapter, based on the prior controlled experiment."""

import argparse
import hashlib
import inspect
import json
import os
import random
import time
from functools import partial
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--sha256", required=True)
    parser.add_argument("--config", required=True)
    parser.add_argument("--seed", type=int, required=True)
    args = parser.parse_args()
    with args.checkpoint.open("rb") as stream:
        if hashlib.file_digest(stream, "sha256").hexdigest() != args.sha256:
            raise ValueError("Checkpoint digest mismatch")
    os.environ.update({"HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1", "OMP_NUM_THREADS": "2"})
    import importlib.metadata

    import numpy as np
    import partuv
    import torch
    import trimesh
    from partuv.preprocess import preprocess, save_results
    from partuv.preprocess_utils.partfield_official.run_PF import PFInferenceModel

    random.seed(args.seed)
    np.random.seed(args.seed)
    torch.manual_seed(args.seed)
    torch.set_num_threads(2)
    start = time.monotonic()
    model = PFInferenceModel(checkpoint_path=str(args.checkpoint), device="cuda")
    if "seed" not in inspect.signature(model.process_face).parameters:
        raise RuntimeError("Installed PartField cannot accept the requested sampling seed")
    model.process_face = partial(model.process_face, seed=args.seed)
    output: Path = args.output
    prep = output / "preprocess"
    (prep / args.input.stem).mkdir(parents=True, exist_ok=True)
    mesh, _, tree, _ = preprocess(
        str(args.input),
        model,
        str(prep / "placeholder"),
        save_tree_file=True,
        save_processed_mesh=True,
        sample_on_faces=10,
        sample_batch_size=100_000,
        merge_vertices_epsilon=None,
    )
    before = trimesh.load(prep / args.input.stem / (args.input.stem + ".obj"), force="mesh", process=False)
    center = before.bounds.mean(axis=0)
    scale = 0.9 / before.extents.max()
    final, parts = partuv.pipeline_numpy(
        V=np.asarray(mesh.vertices),
        F=np.asarray(mesh.faces),
        tree_dict=tree,
        configPath=args.config,
        threshold=1.25,
    )
    save_results(output, final, parts)
    aligned = trimesh.load(output / "final_components.obj", force="mesh", process=False)
    aligned.vertices = (aligned.vertices - 0.5) / scale + center
    aligned.export(output / "aligned.obj")
    (output / "backend.json").write_text(
        json.dumps(
            {
                "partuv": importlib.metadata.version("partuv"),
                "seed": args.seed,
                "seconds": time.monotonic() - start,
                "inverse_scale": float(scale),
                "inverse_center": center.tolist(),
                "preprocessed_faces": len(mesh.faces),
            }
        ),
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
