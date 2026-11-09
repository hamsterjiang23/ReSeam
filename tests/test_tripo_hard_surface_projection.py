"""Material-cue projection uses the same physical bound as the frozen color target."""

from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from reseam.agent_tools.state import read_object, save_snapshot
from reseam.budget_state import BudgetState
from reseam.budget_texture import ReferenceSurface
from reseam.mesh import make_mesh, state_from_corner_uv
from scripts import run_tripo_spike_hard_surface as driver


def test_normal_cue_bound_uses_low_aabb_diagonal_not_sqrt_area(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    vertices = np.asarray([[0., 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0]])
    faces = np.asarray([[0, 1, 2], [0, 2, 3]])
    uv = vertices[faces, :2]
    surface = state_from_corner_uv(make_mesh(vertices, faces), uv, corner_ids=faces)
    frozen = tmp_path / "frozen_reference.glb"
    frozen.write_bytes(b"immutable-reference-fixture")
    snapshot = save_snapshot(BudgetState(surface), tmp_path / "source", {"reference_glb": str(frozen)})
    # 0.10 is above .08*sqrt(area)=.08, but below .08*AABB_diagonal=.1131.
    reference = ReferenceSurface(vertices + [0, 0, .1], faces, uv,
                                 np.full((4, 4, 4), 255, dtype=np.uint8))
    monkeypatch.setattr(driver, "load_reference", lambda _: reference)
    normal = tmp_path / "normal.png"
    Image.new("RGB", (4, 4), (128, 128, 255)).save(normal)
    trial = driver.TripoTrial(tmp_path / "experiment", "original")
    record = read_object(Path(trial.normal_cues(str(snapshot.root / "snapshot.json"), normal)))
    assert record["projection_scale"] == pytest.approx(np.sqrt(2))
    assert len(record["edges"]) == 1  # type: ignore[arg-type]
    assert record["edges"][0]["valid"] is True  # type: ignore[index]
