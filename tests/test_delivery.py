import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from reseam.artist_review import validate_edit
from reseam.constraints import Constraints, mesh_identity
from reseam.diagnostics import artist_metrics
from reseam.mesh import make_mesh, state_from_corner_uv
from reseam.types import Candidate, MeshState, SolveResult


def square(width: float = 1) -> MeshState:
    mesh = make_mesh(
        np.array([[0, 0, 0], [width, 0, 0], [width, 1, 0], [0, 1, 0]], dtype=float),
        np.array([[0, 1, 2], [0, 2, 3]], dtype=np.int64),
    )
    return state_from_corner_uv(mesh, mesh.vertices[mesh.faces, :2] / mesh.scale, corner_ids=mesh.faces)


def test_artist_proxy_definitions_and_no_invented_semantics() -> None:
    data = artist_metrics(square())
    assert data["compactness_area_weighted"] == pytest.approx(4 / math.pi)
    assert data["turn_ratio_area_weighted"] == pytest.approx(1)
    assert data["thin_chart_area_fraction"] == 0
    assert data["semantic_fragmentation"] is None
    assert artist_metrics(square(100))["thin_chart_area_fraction"] == pytest.approx(1)


def test_human_edit_technical_check_does_not_certify_artist_acceptance() -> None:
    state = square()
    data = {
        "kind": "reseam_edited_uv_v1",
        "mesh_identity": mesh_identity(state.mesh),
        "vertices": state.mesh.vertices.tolist(),
        "faces": state.mesh.faces.tolist(),
        "corner_uv": state.uv[state.uv_faces].tolist(),
        "seam_edges": [],
        "reviewer": "unit_test",
    }
    _, result = validate_edit(state.mesh, data, Constraints())
    assert result["technical_checks_passed"]
    assert result["human_acceptance"] == "PENDING_HUMAN"
    data["corner_uv"][0][0] = [100, 100]
    with pytest.raises(ValueError, match="conflicts"):
        validate_edit(state.mesh, data, Constraints())
    data["corner_uv"] = state.uv[state.uv_faces].tolist()
    data["vertices"][0][0] = 0.1
    with pytest.raises(ValueError, match="geometry"):
        validate_edit(state.mesh, data, Constraints())


def test_bridge_failure_is_logged(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from reseam import candidates
    from reseam.initialization import initialize
    from reseam.search import search

    state = square()
    diagonal = state.mesh.edges.index((0, 2))
    split = initialize(state.mesh, frozenset({diagonal}))

    def fail_bridge(*args: Any, **kwargs: Any) -> frozenset[int]:
        raise ValueError("forced_bridge_failure")

    monkeypatch.setattr(candidates, "bridge_holes", fail_bridge)
    _, result = search(split, Constraints(), {}, tmp_path / "search", 5, 17, tau=100, max_candidates=1)
    assert result["candidates"] == 1
    event = json.loads((tmp_path / "search/candidates.jsonl").read_text(encoding="utf-8"))
    assert event["failure_stage"] == "BOUNDARY_BRIDGE"
    assert event["reason"] == "forced_bridge_failure"


def test_middle_support_starts_independently(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from reseam import solver

    state = square()
    candidate = Candidate("test", "test", (0, 1), frozenset(), frozenset(), state, 0, frozenset({0}))
    calls = []

    def fake_solve(
        state: MeshState,
        free: frozenset[int],
        runtime: dict[str, Any],
        folder: Path,
        seconds: float,
        iterations: int = 160,
    ) -> SolveResult:
        calls.append((state.uv.copy(), free, iterations))
        return SolveResult("SOLVED", state.copy(), 0.01)

    monkeypatch.setattr(solver, "solve", fake_solve)
    solver.relax(candidate, "middle", {}, tmp_path, 10, 0.025)
    assert len(calls) == 1 and calls[0][1] == solver.supports(candidate)[1] and calls[0][2] == 160
    np.testing.assert_array_equal(calls[0][0], candidate.state.uv)


def test_checkpoint_deadline_preserves_prevalidated_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from reseam import solver

    state = square()
    clock = {"time": 0.0}

    def now() -> float:
        return clock["time"]

    def fake_process(argv: list[str], folder: Path, timeout: float) -> float:
        bad = {
            "uv": np.zeros_like(state.uv).tolist(),
            "energy_native": 0,
            "iterations": 10,
            "topology_updates": 0,
        }
        (folder / "result.json").write_text(json.dumps(bad))
        clock["time"] = 11
        return 11

    monkeypatch.setattr(solver.time, "monotonic", now)
    monkeypatch.setattr(solver, "run_process", fake_process)
    result = solver.solve(
        state, frozenset(range(len(state.uv))), {"continuous_binary": "unused"}, tmp_path / "run", 10
    )
    assert result.state is not None and result.metadata["checkpoint_scan_truncated"]
    assert result.metadata["selected_checkpoint"] == "prevalidated_input"
    np.testing.assert_array_equal(result.state.uv, state.uv)


def test_blind_ids_survive_new_eligible_results(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from reseam import report

    def fake_scene(repository: Path, run: Path, output: Path) -> Path:
        destination = output / "edit_scene/editable.blend"
        destination.parent.mkdir(exist_ok=True)
        if not destination.exists():
            destination.write_text(run.name, encoding="utf-8")
        return destination

    monkeypatch.setattr(report, "editing_scene", fake_scene)
    records = []
    for name in ("a", "b"):
        base = tmp_path / name / "downstream"
        (base / "renders_blender").mkdir(parents=True)
        (base / "renders_blender/view_00.png").write_bytes(b"fixture")
        (base / "result.json").write_text('{"input_uv_unchanged":true}', encoding="utf-8")
        records.append({"path": name, "model": "energy_blade", "seed": 17, "status": "SUCCESS"})
    report.blind_report(tmp_path, {"runs": records[:1]})
    before = json.loads((tmp_path / "blind_key.private.json").read_text(encoding="utf-8"))
    report.blind_report(tmp_path, {"runs": records})
    after = json.loads((tmp_path / "blind_key.private.json").read_text(encoding="utf-8"))
    assert all(after[code] == path for code, path in before.items())
    for code, path in after.items():
        assert (tmp_path / "blind" / code / "edit_scene/editable.blend").read_text(encoding="utf-8") == path


def test_editing_scene_cache_checks_identity(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from reseam import downstream, report_details
    from reseam.mesh import save_state

    packed = tmp_path / "run/downstream/pack"
    packed.mkdir(parents=True)
    save_state(packed / "state.json", square())
    output = tmp_path / "anonymous"
    output.mkdir()

    def fake_blender(*args: Any) -> None:
        destination = Path(args[2]["output"])
        destination.parent.mkdir(parents=True)
        destination.write_bytes(b"scene")

    monkeypatch.setattr(downstream, "blender", fake_blender)
    monkeypatch.setattr(report_details, "read_runtime", lambda root: {})
    scene = report_details.editing_scene(tmp_path, tmp_path / "run", output)
    assert scene is not None
    assert report_details.editing_scene(tmp_path, tmp_path / "run", output) == scene
    save_state(packed / "state.json", square(2))
    with pytest.raises(ValueError, match="changed"):
        report_details.editing_scene(tmp_path, tmp_path / "run", output)
