from __future__ import annotations

import hashlib
import io
import json
import os
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from scripts import package_reseam_skills
from scripts.install_reseam_skills import SKILL_NAMES, install_bundle
from scripts.package_reseam_skills import RUNTIME_ARCHIVE, RUNTIME_MANIFEST, build_bundle


def make_bundle(root: Path) -> Path:
    bundle = root / "bundle"
    files: dict[str, str] = {}
    for name in SKILL_NAMES:
        relative = f"skills/{name}/SKILL.md"
        source = bundle / relative
        source.parent.mkdir(parents=True)
        source.write_text(f"# {name}\n", encoding="utf-8")
        files[relative] = hashlib.sha256(source.read_bytes()).hexdigest()
    (bundle / "bundle-manifest.json").write_text(
        json.dumps({"skills": list(SKILL_NAMES), "files": files}), encoding="utf-8"
    )
    return bundle


def test_install_and_update_preserves_custom_files(tmp_path: Path) -> None:
    bundle = make_bundle(tmp_path)
    destination = tmp_path / "installed"
    first = install_bundle(bundle, destination)
    assert first["status"] == "SKILLS_INSTALLED"
    custom = destination / "reseam-uv/custom.txt"
    custom.write_text("keep", encoding="utf-8")
    (destination / "reseam-uv/SKILL.md").write_text("previous", encoding="utf-8")
    second = install_bundle(bundle, destination)
    assert custom.read_text(encoding="utf-8") == "keep"
    backup = Path(str(second["previous_skills"]))
    assert (backup / "reseam-uv/SKILL.md").read_text(encoding="utf-8") == "previous"
    assert (destination / "reseam-uv/SKILL.md").read_text(encoding="utf-8") == "# reseam-uv\n"


def test_checksum_failure_does_not_create_destination(tmp_path: Path) -> None:
    bundle = make_bundle(tmp_path)
    (bundle / "skills/reseam-uv/SKILL.md").write_text("tampered", encoding="utf-8")
    destination = tmp_path / "installed"
    with pytest.raises(ValueError, match="checksum"):
        install_bundle(bundle, destination)
    assert not destination.exists()


def test_unlisted_file_is_rejected(tmp_path: Path) -> None:
    bundle = make_bundle(tmp_path)
    (bundle / "skills/reseam-uv/extra.md").write_text("unlisted", encoding="utf-8")
    with pytest.raises(ValueError, match="absent"):
        install_bundle(bundle, tmp_path / "installed")


def test_manifest_path_escape_is_rejected(tmp_path: Path) -> None:
    bundle = make_bundle(tmp_path)
    path = bundle / "bundle-manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    manifest["files"]["../outside.txt"] = "0" * 64
    path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match="Invalid bundle path"):
        install_bundle(bundle, tmp_path / "installed")


def test_second_publish_failure_rolls_back_both_skills(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundle = make_bundle(tmp_path)
    destination = tmp_path / "installed"
    install_bundle(bundle, destination)
    for name in SKILL_NAMES:
        (destination / name / "SKILL.md").write_text("original", encoding="utf-8")
    replace = os.replace

    def fail_second_publish(source: Path, target: Path) -> None:
        if source.name == "reseam-hard-surface" and source.parent.name.startswith(".reseam-install-"):
            raise OSError("simulated publication failure")
        replace(source, target)

    monkeypatch.setattr(os, "replace", fail_second_publish)
    with pytest.raises(OSError, match="simulated"):
        install_bundle(bundle, destination)
    for name in SKILL_NAMES:
        assert (destination / name / "SKILL.md").read_text(encoding="utf-8") == "original"
    assert not (destination / ".reseam-install.lock").exists()


def test_package_can_be_extracted_and_installed_with_its_entrypoint(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    output = tmp_path / "reseam-skills.zip"
    result = build_bundle(root, output)
    assert result["sha256"] == hashlib.sha256(output.read_bytes()).hexdigest()
    extracted = tmp_path / "extracted"
    with zipfile.ZipFile(output) as archive:
        assert archive.testzip() is None
        assert "skills/reseam-uv/scripts/reseam_workflow.py" in archive.namelist()
        assert RUNTIME_ARCHIVE in archive.namelist()
        runtime_manifest = json.loads(archive.read(RUNTIME_MANIFEST))
        with zipfile.ZipFile(io.BytesIO(archive.read(RUNTIME_ARCHIVE))) as runtime:
            names = runtime.namelist()
            assert "src/reseam/workflows/__main__.py" in names
            assert "src/reseam/workflows/parts.py" in names
            assert "src/reseam/workflows/report.py" in names
            assert "scripts/agent_pack_worker.py" in names
            assert "baseline/adapters/blender_smart.py" in names
            assert "configs/runtime.example.json" in names
            assert "AGENTS.md" in names
            assert not any("workbench" in name or "runtime.local" in name for name in names)
            assert not any(name.startswith(("artifacts/", "model/", "assets/", "output/")) for name in names)
            assert {name: hashlib.sha256(runtime.read(name)).hexdigest() for name in names} == runtime_manifest["files"]
            committed_cli = subprocess.check_output(
                ["git", "show", "HEAD:src/reseam/cli.py"], cwd=root,
            )
            assert runtime.read("src/reseam/cli.py") == committed_cli
        archive.extractall(extracted)
    destination = tmp_path / "installed"
    completed = subprocess.run(
        [sys.executable, "-X", "utf8", str(extracted / "install.py"), "--dest", str(destination)],
        check=True, capture_output=True, text=True, encoding="utf-8",
    )
    assert json.loads(completed.stdout)["status"] == "SKILLS_INSTALLED"
    for case in (root / "skills/reseam-uv/examples").glob("*.md"):
        assert (destination / "reseam-uv/examples" / case.name).read_bytes() == case.read_bytes()
    source_manifest = extracted / "skills/reseam-uv/references/install-manifest.json"
    installed_manifest = destination / "reseam-uv/references/install-manifest.json"
    assert installed_manifest.read_bytes() == source_manifest.read_bytes()
    assert not (destination / "reseam-uv/references/runtime.local.json").exists()
    installed_launcher = destination / "reseam-uv/scripts/reseam_workflow.py"
    help_result = subprocess.run(
        [sys.executable, str(installed_launcher), "--help"], check=True,
        capture_output=True, text=True, encoding="utf-8",
    )
    assert "rebuild,preview,collect,report" in help_result.stdout
    # Keep the deep pytest Windows root: the launcher's shorter cache key must handle it.
    environment = os.environ.copy()
    environment.update(CODEX_HOME=str(tmp_path / "codex"), UV_PYTHON=sys.executable)
    check_result = subprocess.run(
        [sys.executable, "-X", "utf8", str(installed_launcher), "--check", "--repo", str(root)],
        check=False, capture_output=True, text=True, encoding="utf-8", env=environment, timeout=120,
    )
    assert check_result.returncode == 0, (check_result.stdout, check_result.stderr)
    result = json.loads(check_result.stdout)
    assert result["status"] == "READY"
    assert result["backend_execution_tested"] is False
    assert result["quality_accepted"] is False
    assert result["launcher"]["source_hash_verified"] is True
    assert result["launcher"]["source_sha256"] == runtime_manifest["source_sha256"]


def test_script_and_image_resources_are_installed(tmp_path: Path) -> None:
    bundle = make_bundle(tmp_path)
    manifest_path = bundle / "bundle-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    additions = {
        "skills/reseam-uv/scripts/helper.py": b"print('helper')\n",
        "skills/reseam-uv/assets/example.png": b"\x89PNG\r\n\x1a\n",
        "skills/reseam-uv/templates/request.json": b"{}\n",
    }
    for relative, data in additions.items():
        source = bundle / relative
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_bytes(data)
        manifest["files"][relative] = hashlib.sha256(data).hexdigest()
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    destination = tmp_path / "installed"
    install_bundle(bundle, destination)
    for relative, data in additions.items():
        assert (destination / Path(relative).relative_to("skills")).read_bytes() == data


def test_packager_keeps_python_images_and_templates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = Path(__file__).resolve().parents[1]
    source, manifest = package_reseam_skills.runtime_payload(root)
    checkout = tmp_path / "source"
    for name in SKILL_NAMES:
        entry = checkout / "skills" / name / "SKILL.md"
        entry.parent.mkdir(parents=True)
        entry.write_text("# Skill\n", encoding="utf-8")
    additions = {
        "skills/reseam-uv/scripts/helper.py": b"print('helper')\n",
        "skills/reseam-uv/assets/example.png": b"\x89PNG\r\n\x1a\n",
        "skills/reseam-uv/templates/request.json": b"{}\n",
    }
    for name, data in additions.items():
        target = checkout / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    (checkout / "scripts").mkdir()
    (checkout / "scripts/install_reseam_skills.py").write_bytes(
        (root / "scripts/install_reseam_skills.py").read_bytes()
    )
    monkeypatch.setattr(package_reseam_skills, "runtime_payload", lambda _root: (source, manifest))
    path = tmp_path / "bundle.zip"
    build_bundle(checkout, path)
    with zipfile.ZipFile(path) as archive:
        for name, data in additions.items():
            assert archive.read(name) == data


def test_nested_source_hash_failure_is_rejected_before_install(tmp_path: Path) -> None:
    bundle = make_bundle(tmp_path)
    manifest_path = bundle / "bundle-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    archive_path = bundle / RUNTIME_ARCHIVE
    archive_path.parent.mkdir(parents=True)
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("src/reseam/__init__.py", "changed source")
    original_hashes = {"src/reseam/__init__.py": hashlib.sha256(b"original source").hexdigest()}
    content_hash = hashlib.sha256(
        json.dumps(original_hashes, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    runtime_manifest = {
        "files": original_hashes, "source_sha256": content_hash,
        "archive_sha256": hashlib.sha256(archive_path.read_bytes()).hexdigest(),
    }
    runtime_path = bundle / RUNTIME_MANIFEST
    runtime_path.write_text(json.dumps(runtime_manifest), encoding="utf-8")
    for path in (archive_path, runtime_path):
        manifest["files"][path.relative_to(bundle).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    manifest["runtime_source"] = {
        "archive": RUNTIME_ARCHIVE, "manifest": RUNTIME_MANIFEST, "source_sha256": content_hash,
    }
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    destination = tmp_path / "installed"
    with pytest.raises(ValueError, match="Runtime source checksum mismatch"):
        install_bundle(bundle, destination)
    assert not destination.exists()
