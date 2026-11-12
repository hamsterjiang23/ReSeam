"""Run one reproducible PBR target/bake/probe/export trial through public tools."""

import argparse
from pathlib import Path

import numpy as np

from reseam.agent_tools.runner import run_tool
from reseam.agent_tools.state import load_snapshot, read_object, snapshot_result, write_json


def invoke(tool: str, request: dict[str, object], output: Path) -> dict[str, object]:
    path = output.parent / (output.name + '_request.json')
    write_json(path, request)
    result = run_tool(tool, path, output)
    print(tool, result.get('status'), result.get('reason', ''), flush=True)
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--snapshot', type=Path, required=True)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--policy', choices=['source_original', 'master'], default='source_original')
    parser.add_argument('--resolution', type=int, default=128)
    parser.add_argument('--target', type=Path)
    parser.add_argument('--timeout', type=int, default=300)
    parser.add_argument('--projection', choices=['nearest_surface', 'finite_ray', 'same_surface_face_corner'],
                        default='nearest_surface')
    args = parser.parse_args()
    root = args.output.resolve()
    root.mkdir(parents=True, exist_ok=False)
    snapshot = load_snapshot(args.snapshot.resolve())
    centers = snapshot.state.surface.mesh.vertices[snapshot.state.surface.mesh.faces].mean(axis=1)
    protected = np.flatnonzero((centers[:, 1] < .10) | ((centers[:, 1] > .45) & (np.abs(centers[:, 0]) < .23))).tolist()
    common = {**snapshot_result(snapshot), 'mode': 'pbr_reuse', 'acceptance_policy': 'visual',
              'geometry_profile': {'quality_constraints': False, 'resolution': args.resolution}, 'timeout_seconds': args.timeout}
    summary: dict[str, object] = {}
    if args.target:
        target = str(args.target.resolve())
    else:
        result = invoke('material.target', {**common, 'source_reference': str(args.source.resolve()),
                        'policy': args.policy, 'protected_faces': protected, 'max_projection_fraction': .02,
                        'projection_method': args.projection, 'front_fraction': .02, 'back_fraction': .02}, root / 'target')
        summary['target'] = result
        if result.get('status') != 'OK':
            write_json(root / 'trial.json', summary)
            return
        target = str(result['target'])
    record = read_object(Path(target))
    common['geometry_profile'] = record['geometry_profile']
    bake = invoke('material.bake', {**common, 'target': target, 'resolution': args.resolution,
                  'padding_pixels': 1 if args.resolution == 128 else 8}, root / 'bake')
    summary['bake'] = bake
    if bake.get('status') == 'OK':
        summary['validation'] = invoke('quality.validate', {**common, 'bake': bake['bake'],
            'mip_levels': [0, 1, 2]}, root / 'validation')
        summary['export'] = invoke('asset.export', {**snapshot_result(snapshot), 'bake': bake['bake'],
                                  'mode': 'diagnostic', 'timeout_seconds': 300}, root / 'export')
    write_json(root / 'trial.json', summary)


if __name__ == '__main__':
    main()
