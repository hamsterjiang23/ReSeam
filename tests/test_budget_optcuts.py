"""Mocked native adapter contract; these tests do not run OptCuts."""

import json
import time
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from reseam.budget_optcuts import prepare_optcuts_reference
from reseam.mesh import load_state, make_mesh
from reseam.types import Mesh, MeshState


def context(tmp_path: Path) -> tuple[Path, Path, Mesh, MeshState]:
    root = tmp_path / "project"
    input_path = root / "assets/v1/energy_blade/low.obj"
    input_path.parent.mkdir(parents=True)
    input_path.write_text("v 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 3\n", encoding="utf-8")
    mesh = make_mesh(np.asarray([[0., 0, 0], [1, 0, 0], [0, 1, 0]]),
                     np.asarray([[0, 1, 2]], dtype=np.int64))
    state = MeshState(mesh, frozenset(), np.asarray([[0., 0], [1, 0], [0, 1]]), mesh.faces.copy())
    (root / "configs").mkdir()
    binary = root / "fake_native_binary"
    binary.write_bytes(b"native test double")
    (root / "configs/runtime.local.json").write_text(
        json.dumps({"optcuts_binary": str(binary)}), encoding="utf-8")
    sources = root / "src/reseam"
    sources.mkdir(parents=True)
    for name in ("backends.py", "runtime.py", "initialization.py", "budget_state.py"):
        (sources / name).write_text("# fixed source\n", encoding="utf-8")
    return root, input_path, mesh, state


def test_native_reference_uses_same_repair_and_saves_explicit_artifacts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, path, mesh, raw = context(tmp_path)
    raw.uv[:, 0] *= -1
    before = raw.uv.copy()
    requested: list[float] = []

    def native(
        root: Path, input_path: Path, method: str, runtime: dict[str, object],
        folder: Path, seconds: float, seed: int,
    ) -> tuple[MeshState, dict[str, object]]:
        assert method == "optcuts" and seed == 17
        assert input_path == path and root.is_absolute()
        assert "optcuts_binary" in runtime
        folder.mkdir()
        requested.append(seconds)
        return raw, {"seed_effect": "deterministic_repeat"}

    monkeypatch.setattr("reseam.budget_optcuts.baseline", native)
    state, record = prepare_optcuts_reference(root, path, mesh, 17, time.monotonic() + 30)
    assert 0 < requested[0] <= 30
    assert not state.groups and state.texture is None and not state.evidence
    assert record["role"] == "separate_baseline_only_not_new_solver_initialization"
    assert record["input_unchanged"] and record["backend_unchanged"]
    assert np.array_equal(raw.uv, before)
    assert np.linalg.det(np.column_stack((state.surface.uv[1]-state.surface.uv[0],
                                         state.surface.uv[2]-state.surface.uv[0]))) > 0
    folder = Path(str(record["folder"]))
    assert np.array_equal(load_state(folder / "raw_state.json").uv, before)
    assert (folder / "repaired_state.json").exists()
    assert (folder / "raw_uv.obj").exists() and (folder / "repaired_uv.obj").exists()
    assert json.loads((folder / "result.json").read_text())["status"] == "REFERENCE_INITIALIZED"


def test_timeout_is_preserved_and_not_swallowed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root, path, mesh, _ = context(tmp_path)

    def timeout(*args: object, **kwargs: object) -> tuple[MeshState, dict[str, object]]:
        raise TimeoutError("native timeout test")

    monkeypatch.setattr("reseam.budget_optcuts.baseline", timeout)
    with pytest.raises(TimeoutError, match="native timeout"):
        prepare_optcuts_reference(root, path, mesh, 17, time.monotonic() + 30)
    result_path = next((root / "artifacts/budget_v1/optcuts_initializations").glob("*/result.json"))
    result = json.loads(result_path.read_text())
    assert result["status"] == "SETUP_TIMEOUT"
    assert result["input_unchanged"] and result["backend_unchanged"]


def test_modified_source_input_is_rejected_before_return(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, path, mesh, raw = context(tmp_path)

    def change(*args: object, **kwargs: object) -> tuple[MeshState, dict[str, object]]:
        path.write_text(path.read_text() + "# changed\n", encoding="utf-8")
        return raw, {}

    monkeypatch.setattr("reseam.budget_optcuts.baseline", change)
    with pytest.raises(RuntimeError, match="INTEGRITY_CHANGED"):
        prepare_optcuts_reference(root, path, mesh, 17, time.monotonic() + 30)
    result_path = next((root / "artifacts/budget_v1/optcuts_initializations").glob("*/result.json"))
    assert json.loads(result_path.read_text())["status"] == "INPUT_OR_BACKEND_CHANGED"


def test_changed_native_geometry_is_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root, path, mesh, raw = context(tmp_path)
    changed = make_mesh(raw.mesh.vertices + [0, 0, .1], raw.mesh.faces)

    def wrong(*args: object, **kwargs: object) -> tuple[MeshState, dict[str, object]]:
        return MeshState(changed, raw.seams, raw.uv.copy(), raw.uv_faces.copy()), {}

    monkeypatch.setattr("reseam.budget_optcuts.baseline", wrong)
    with pytest.raises(ValueError, match="CHANGED_ORIGINAL_GEOMETRY"):
        prepare_optcuts_reference(root, path, mesh, 17, time.monotonic() + 30)


def test_expired_or_invalid_contract_does_not_start_native(tmp_path: Path) -> None:
    root, path, mesh, _ = context(tmp_path)
    with pytest.raises(TimeoutError, match="BUDGET_EXHAUSTED"):
        prepare_optcuts_reference(root, path, mesh, 17, time.monotonic() - 1)
    with pytest.raises(ValueError, match="contract"):
        prepare_optcuts_reference(root, path, mesh, True, time.monotonic() + 10)
    assert not (root / "artifacts").exists()


def test_duplicate_uuid_never_overwrites_outputs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root, path, mesh, raw = context(tmp_path)

    def native(*args: object, **kwargs: object) -> tuple[MeshState, dict[str, object]]:
        return raw, {}

    def uuid() -> SimpleNamespace:
        return SimpleNamespace(hex="test_fixed_uuid")

    monkeypatch.setattr("reseam.budget_optcuts.baseline", native)
    monkeypatch.setattr("reseam.budget_optcuts.uuid.uuid4", uuid)
    _, record = prepare_optcuts_reference(root, path, mesh, 17, time.monotonic() + 30)
    saved = Path(str(record["folder"])) / "result.json"
    before = saved.read_bytes()
    with pytest.raises(FileExistsError):
        prepare_optcuts_reference(root, path, mesh, 17, time.monotonic() + 30)
    assert saved.read_bytes() == before


def test_native_return_after_deadline_is_not_an_initialization_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, path, mesh, raw = context(tmp_path)
    now = [0.0]

    def clock() -> float:
        return now[0]

    def late(*args: object, **kwargs: object) -> tuple[MeshState, dict[str, object]]:
        now[0] = 100
        return raw, {}

    monkeypatch.setattr("reseam.budget_optcuts.time", SimpleNamespace(monotonic=clock))
    monkeypatch.setattr("reseam.budget_optcuts.baseline", late)
    with pytest.raises(TimeoutError, match="BUDGET_EXHAUSTED"):
        prepare_optcuts_reference(root, path, mesh, 17, 10)
    result_path = next((root / "artifacts/budget_v1/optcuts_initializations").glob("*/result.json"))
    assert json.loads(result_path.read_text())["status"] == "SETUP_TIMEOUT"
