"""Persistence and public process contracts, including failure rollback."""

import json
import subprocess
import sys
import time
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

import numpy as np
import pytest

from reseam.agent_tools import runner
from reseam.agent_tools.assets import import_asset, render_views
from reseam.agent_tools.runner import run_tool
from reseam.agent_tools.state import load_snapshot, read_object, save_snapshot, write_json
from reseam.budget_state import BudgetState
from reseam.mesh import make_mesh, state_from_corner_uv


def make_state() -> BudgetState:
    mesh = make_mesh(np.array([[0., 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0]]),
                     np.array([[0, 1, 2], [0, 2, 3]]))
    corner_uv = np.array([[[.1, .1], [.9, .1], [.9, .9]], [[.1, .1], [.9, .9], [.1, .9]]])
    return BudgetState(state_from_corner_uv(mesh, corner_uv, corner_ids=np.arange(6).reshape(2, 3)))


def test_snapshot_identity_and_child_do_not_mutate_parent(tmp_path: Path) -> None:
    first = save_snapshot(make_state(), tmp_path / "parent", {})
    before = (first.root / "arrays.npz").read_bytes()
    loaded = load_snapshot(first.root)
    assert len(loaded.state.surface.uv) == 6  # same-coordinate seam copies remain distinct
    loaded.state.surface.uv *= .8
    loaded.state.invalidate()
    child = save_snapshot(loaded.state, tmp_path / "child", loaded.metadata, first)
    assert child.state_hash != first.state_hash
    assert (first.root / "arrays.npz").read_bytes() == before
    assert load_snapshot(first.root).state_hash == first.state_hash


def test_tampered_snapshot_is_rejected(tmp_path: Path) -> None:
    snapshot = save_snapshot(make_state(), tmp_path / "snapshot", {})
    record = read_object(snapshot.root / "snapshot.json")
    record["seams"] = []
    write_json(snapshot.root / "snapshot.json", record)
    with pytest.raises(ValueError, match="HASH_MISMATCH"):
        load_snapshot(snapshot.root)


def test_explicit_obj_uv_initialization(tmp_path: Path) -> None:
    source = tmp_path / "source.obj"
    source.write_text("v 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 3\n", encoding="utf-8")
    refused = tmp_path / "refused"
    refused.mkdir()
    with pytest.raises(ValueError, match="UV_MISSING"):
        import_asset({"path": str(source)}, refused, time.monotonic() + 10)
    accepted = tmp_path / "accepted"
    accepted.mkdir()
    result = import_asset({"path": str(source), "initialize_uv": "xatlas"}, accepted,
                          time.monotonic() + 20)
    loaded = load_snapshot(Path(str(result["snapshot"])))
    assert loaded.state.surface.mesh.faces.tolist() == [[0, 1, 2]]
    assert source.read_text(encoding="utf-8").endswith("f 1 2 3\n")
    preview = tmp_path / "preview"
    preview.mkdir()
    pictures = render_views({"snapshot": result["snapshot"], "size": 128}, preview, time.monotonic() + 10)
    assert not pictures["quality_evidence"]
    assert (preview / "uv.png").is_file()


def test_degenerate_uv_repair_preserves_original_face(tmp_path: Path) -> None:
    source = tmp_path / "source.obj"
    source.write_text("v 0 0 0\nv 1 0 0\nv 0 1 0\nvt 0 0\nf 1/1 2/1 3/1\n", encoding="utf-8")
    destination = tmp_path / "import"
    destination.mkdir()
    result = import_asset({"path": str(source), "repair_degenerate_faces": True}, destination,
                          time.monotonic() + 10)
    loaded = load_snapshot(Path(str(result["snapshot"])))
    assert loaded.state.surface.mesh.faces.tolist() == [[0, 1, 2]]
    assert len(loaded.state.surface.uv) == 3
    assert not result["source_uv_preserved"]
    repair = loaded.metadata["uv_repair"]
    assert isinstance(repair, dict) and repair["faces"] == [0]


def test_cli_discovery_stdout_is_json() -> None:
    completed = subprocess.run([sys.executable, "-m", "reseam.cli", "tools", "list"],
                               capture_output=True, text=True, check=True)
    result = json.loads(completed.stdout)
    names = [tool["name"] for tool in result["tools"]]
    assert {"asset.import", "reuse.check_pair", "material.bake", "region.define", "material.target"} <= set(names)
    assert len(names) == len(set(names))


def test_hard_deadline_and_existing_output(tmp_path: Path) -> None:
    snapshot = save_snapshot(make_state(), tmp_path / "snapshot", {})
    request = tmp_path / "request.json"
    write_json(request, {"snapshot": str(snapshot.root), "timeout_seconds": .001})
    result = run_tool("view.render", request, tmp_path / "run")
    assert result["status"] == "TIMEOUT"
    assert load_snapshot(snapshot.root).state_hash == snapshot.state_hash
    with pytest.raises(FileExistsError):
        run_tool("view.render", request, tmp_path / "run")


def test_mixed_obj_uv_is_not_silently_replaced(tmp_path: Path) -> None:
    source = tmp_path / "source.obj"
    source.write_text("v 0 0 0\nv 1 0 0\nv 1 1 0\nv 0 1 0\nvt 0 0\nvt 1 0\nvt 1 1\nf 1/1 2/2 3/3\nf 1 3 4\n", encoding="utf-8")
    output = tmp_path / "run"
    output.mkdir()
    with pytest.raises(ValueError, match="MIXED_UV_PRESENCE"):
        import_asset({"path": str(source), "initialize_uv": "xatlas"}, output, time.monotonic() + 10)


def test_unrelated_backend_version_missing_does_not_block_view(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def missing_xatlas(name: str) -> str:
        if name == "xatlas":
            raise PackageNotFoundError(name)
        return version(name)

    monkeypatch.setattr(runner, "version", missing_xatlas)
    snapshot = save_snapshot(make_state(), tmp_path / "snapshot", {})
    request = tmp_path / "request.json"
    write_json(request, {"snapshot": str(snapshot.root), "size": 64})
    result = run_tool("view.render", request, tmp_path / "run")
    assert result["status"] == "OK"
