"""Isolated PartField inference using the configured research Python environment."""

import importlib
import inspect
import json
import sys
import time
from pathlib import Path

import numpy as np


def main() -> None:
    input_file, output_file, weights, groups_text, seed_text = sys.argv[1:]
    groups, seed = int(groups_text), int(seed_text)
    torch = importlib.import_module("torch")
    trimesh = importlib.import_module("trimesh")
    module = importlib.import_module("partuv.preprocess_utils.partfield_official.run_PF")
    if not torch.cuda.is_available():
        raise RuntimeError("PARTFIELD_CUDA_REQUIRED_BY_CONFIGURED_BACKEND")
    torch.set_num_threads(1)
    torch.manual_seed(seed)
    np.random.seed(seed)
    torch.cuda.reset_peak_memory_stats()
    with np.load(input_file, allow_pickle=False) as arrays:
        vertices, faces = arrays["vertices"].copy(), arrays["faces"].copy()
    mesh = trimesh.Trimesh(vertices=vertices.copy(), faces=faces.copy(), process=False)
    started = time.monotonic()
    model = module.PFInferenceModel(checkpoint_path=weights, device="cuda")
    with torch.inference_mode():
        tree, root, returned = model.process_face(input_file, mesh=mesh, output_path=None,
                                                  sample_on_faces=10, sample_batch_size=100000,
                                                  seed=seed, device="cuda")
    # This installed upstream normalizes a temporary mesh in-place. Verify that
    # exact documented transform and triangle order before restoring originals.
    low, high = vertices.min(axis=0), vertices.max(axis=0)
    normalized = (vertices-(low+high)*.5)*(.9/(high-low).max())+.5
    if (not np.array_equal(returned.faces, faces) or returned.vertices.shape != vertices.shape
            or not np.allclose(returned.vertices, normalized, rtol=1e-12, atol=1e-12)):
        raise ValueError("PARTFIELD_TEMPORARY_GEOMETRY_OR_FACE_ORDER_CHANGED")
    active = [int(root)]
    while len(active) < min(groups, len(faces)):
        expandable = [node for node in active if node in tree]
        if not expandable:
            break
        # Hierarchy IDs are chronological merges; undo the latest merge first.
        node = max(expandable)
        active.remove(node)
        active.extend((int(tree[node]["left"]), int(tree[node]["right"])))
    labels = np.full(len(faces), -1, dtype=np.int64)
    for label, node in enumerate(sorted(active)):
        pending = [node]
        while pending:
            value = pending.pop()
            if value in tree:
                pending.extend((int(tree[value]["left"]), int(tree[value]["right"])))
            elif 0 <= value < len(faces) and labels[value] == -1:
                labels[value] = label
            else:
                raise ValueError("PARTFIELD_HIERARCHY_FACE_ID_INVALID")
    if np.any(labels < 0):
        raise ValueError("PARTFIELD_HIERARCHY_FACE_COVERAGE_INCOMPLETE")
    np.savez_compressed(output_file, vertices=vertices, faces=faces, labels=labels)
    source = inspect.getfile(module)
    import hashlib
    record = {"groups": len(active), "faces": len(faces), "seed": seed, "sample_on_faces": 10,
              "source_file": source, "source_sha256": hashlib.sha256(Path(source).read_bytes()).hexdigest(),
              "temporary_geometry": "verified source normalized to bounding cube, restored exact original arrays",
              "seconds": time.monotonic()-started, "peak_cuda_bytes": torch.cuda.max_memory_allocated(),
              "torch_version": torch.__version__}
    Path(output_file).with_suffix(".json").write_text(json.dumps(record, indent=2)+"\n", encoding="utf-8")
    print(json.dumps(record))


if __name__ == "__main__":
    main()
