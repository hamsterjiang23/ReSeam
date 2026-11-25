from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import subprocess
import zipfile
from pathlib import Path
from types import ModuleType

import pytest


def load_launcher() -> ModuleType:
    path = Path(__file__).resolve().parents[1] / "skills/reseam-uv/scripts/reseam_workflow.py"
    spec = importlib.util.spec_from_file_location("reseam_skill_launcher", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def make_runtime(tmp_path: Path) -> tuple[Path, dict[str, object]]:
    skill = tmp_path / "skill"
    assets = skill / "assets"
    assets.mkdir(parents=True)
    files = {
        "pyproject.toml": b"[project]\nname='reseam'\nversion='0.1.0'\n",
        "uv.lock": b"version=1\n",
        "src/reseam/__init__.py": b"",
        "src/reseam/workflows/__init__.py": b"",
        "src/reseam/workflows/__main__.py": b"print('workflow')\n",
    }
    archive = assets / "runtime-source.zip"
    with zipfile.ZipFile(archive, "w") as stream:
        for name, data in files.items():
            stream.writestr(name, data)
    hashes = {name: hashlib.sha256(data).hexdigest() for name, data in files.items()}
    content_hash = hashlib.sha256(json.dumps(hashes, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    manifest: dict[str, object] = {
        "schema_version": 1, "files": hashes, "source_sha256": content_hash,
        "archive_sha256": hashlib.sha256(archive.read_bytes()).hexdigest(), "source_revision": "test-base",
    }
    (assets / "runtime-manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return skill, manifest


def test_reuses_exact_source_and_preserves_local_config(tmp_path: Path) -> None:
    launcher = load_launcher()
    skill, manifest = make_runtime(tmp_path)
    root = launcher.extract_runtime(skill, manifest, tmp_path / "cache")
    (root / "configs").mkdir()
    configuration = root / "configs/runtime.local.json"
    configuration.write_text('{"blender":"user-config"}', encoding="utf-8")
    selected, origin = launcher.select_runtime(skill, manifest, root)
    assert selected == root
    assert origin == "matching_checkout"
    assert configuration.read_text(encoding="utf-8") == '{"blender":"user-config"}'
    (root / "src/reseam/extra.py").write_text("changed", encoding="utf-8")
    assert launcher.source_matches(root, manifest) is False


def test_mismatched_checkout_uses_cache_without_replacing_user_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    launcher = load_launcher()
    skill, manifest = make_runtime(tmp_path)
    user_repo = tmp_path / "checkout"
    user_repo.mkdir()
    existing = user_repo / "pyproject.toml"
    existing.write_text("user content", encoding="utf-8")
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    selected, origin = launcher.select_runtime(skill, manifest, user_repo)
    assert origin == "verified_cache"
    assert selected.is_relative_to(tmp_path / "codex/cache/reseam-workflows")
    assert existing.read_text(encoding="utf-8") == "user content"
    assert launcher.source_matches(selected, manifest) is True
    assert selected.name == str(manifest["source_sha256"])[:16]


def test_corrupted_archive_is_rejected_before_extracting(tmp_path: Path) -> None:
    launcher = load_launcher()
    skill, manifest = make_runtime(tmp_path)
    (skill / "assets/runtime-source.zip").write_bytes(b"tampered")
    cache = tmp_path / "cache"
    with pytest.raises(ValueError, match="ARCHIVE_HASH_MISMATCH"):
        launcher.extract_runtime(skill, manifest, cache)
    assert not cache.exists()


def test_tampered_cache_is_not_overwritten(tmp_path: Path) -> None:
    launcher = load_launcher()
    skill, manifest = make_runtime(tmp_path)
    cache = tmp_path / "cache"
    selected = launcher.extract_runtime(skill, manifest, cache)
    source = selected / "src/reseam/workflows/__main__.py"
    source.write_text("changed", encoding="utf-8")
    with pytest.raises(ValueError, match="CACHED_RUNTIME_MODIFIED"):
        launcher.extract_runtime(skill, manifest, cache)
    assert source.read_text(encoding="utf-8") == "changed"


def test_manifest_rejects_modified_hashes_and_paths(tmp_path: Path) -> None:
    launcher = load_launcher()
    skill, manifest = make_runtime(tmp_path)
    manifest["source_sha256"] = "0" * 64
    path = skill / "assets/runtime-manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match="MANIFEST_HASH_MISMATCH"):
        launcher.read_manifest(skill)
    manifest["files"] = {"../outside.py": "0" * 64}
    path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match="INVALID_RUNTIME_PATH"):
        launcher.read_manifest(skill)


def test_report_launcher_uses_locked_reports_extra_and_verified_imports(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    launcher = load_launcher()
    skill, manifest = make_runtime(tmp_path)
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    monkeypatch.setattr(launcher.shutil, "which", lambda _name: "/tool/uv")
    observed: list[list[str]] = []

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        observed.append(command)
        assert kwargs["check"] is False
        assert kwargs["cwd"] == tmp_path / "codex/cache/reseam-workflows" / str(manifest["source_sha256"])[:16]
        return subprocess.CompletedProcess(command, 0, '{"status":"READY","execution_success":true}', "")

    monkeypatch.setattr(launcher.subprocess, "run", fake_run)
    args = argparse.Namespace(command="report", check=True, repo=None, request=None, output=None)
    response, code = launcher.run_launcher(args, skill)
    assert code == 0
    assert response["status"] == "READY"
    command = observed[0]
    assert "--locked" in command
    assert command[command.index("--extra") + 1] == "reports"
    assert command[-3:] == ["reseam.workflows", "report", "--check"]
    assert response["launcher"]["source_hash_verified"] is True


def test_missing_uv_is_reported_without_running_workflow(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    launcher = load_launcher()
    skill, _manifest = make_runtime(tmp_path)
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    monkeypatch.setattr(launcher.shutil, "which", lambda _name: None)
    args = argparse.Namespace(command=None, check=True, repo=None, request=None, output=None)
    response, code = launcher.run_launcher(args, skill)
    assert code == 1
    assert response["status"] == "DEPENDENCY_MISSING"
    assert response["reason"] == "UV_NOT_FOUND"
    assert response["execution_success"] is False
