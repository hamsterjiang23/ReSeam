"""Positive-area overlaps cannot hide behind member/chart raster deduplication."""

import time
from pathlib import Path

import numpy as np
import pytest

from reseam.agent_tools.pbr_reuse import PBRTarget, compose, freeze_target
from reseam.agent_tools.state import Snapshot, save_snapshot
from reseam.budget_material import atlas_from_arrays, export_material_glb
from reseam.budget_state import BudgetState
from reseam.mesh import make_mesh, state_from_corner_uv


def folded_quad(root: Path, split: bool, folded: bool) -> Snapshot:
    vertices = np.asarray([[0., 0., 0.], [1., 0., 0.], [1., 1., 0.], [0., 1., 0.]])
    faces = np.asarray([[0, 1, 2], [0, 2, 3]])
    mesh = make_mesh(vertices, faces)
    uv = vertices[:, :2] * .8 + .1
    original = state_from_corner_uv(mesh, uv[faces], corner_ids=faces)
    atlas = atlas_from_arrays(np.full((8, 8, 4), [.4, .2, .1, 1.]),
        np.full((8, 8, 3), [0., 0., 1.]), np.full((8, 8, 3), [1., .5, 0.]))
    reference = root / "source.glb"
    export_material_glb(original, atlas, reference)
    if folded:
        uv[3] = uv[1]
    surface = state_from_corner_uv(mesh, uv[faces], corner_ids=np.arange(6).reshape(2, 3) if split else faces)
    return save_snapshot(BudgetState(surface), root / "snapshot", {"reference_glb": str(reference)})


@pytest.mark.parametrize("split", [False, True])
def test_folded_adjacent_faces_rejected_before_deduplication(tmp_path: Path, split: bool) -> None:
    snapshot = folded_quad(tmp_path, split, True)
    deadline = time.monotonic() + 30
    result = freeze_target({"snapshot": str(snapshot.root), "acceptance_policy": "visual"},
        tmp_path / "target", deadline)
    target = PBRTarget(snapshot, Path(str(result["target"])), deadline)
    with pytest.raises(ValueError, match="UNAUTHORIZED_PBR_RASTER_OVERLAP"):
        compose(snapshot, target, 32, 1, deadline)
    # A separately declared frozen source exception remains auditable.
    _, audit = compose(snapshot, target, 32, 1, deadline, {(0, 1)})
    retained = audit["preserved_source_overlap_samples"]
    assert isinstance(retained, int) and retained > 0


@pytest.mark.parametrize("split", [False, True])
def test_common_edge_raster_samples_remain_allowed(tmp_path: Path, split: bool) -> None:
    snapshot = folded_quad(tmp_path, split, False)
    deadline = time.monotonic() + 30
    result = freeze_target({"snapshot": str(snapshot.root), "acceptance_policy": "visual"},
        tmp_path / "target", deadline)
    target = PBRTarget(snapshot, Path(str(result["target"])), deadline)
    atlas, audit = compose(snapshot, target, 32, 1, deadline)
    assert atlas.coverage.any()
    assert audit["invalid_samples"] == 0
    assert audit["preserved_source_overlap_samples"] == 0
