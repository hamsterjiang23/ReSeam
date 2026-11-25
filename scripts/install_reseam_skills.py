"""Install a verified ReSeam skill bundle with backups and rollback."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import stat
import tempfile
import zipfile
from pathlib import Path, PurePosixPath

SKILL_NAMES = ("reseam-uv", "reseam-hard-surface")


def validate_runtime(bundle: Path, entry: dict[str, object], validated: dict[str, str]) -> None:
    """Check nested execution bytes before installing a source-bearing v2 bundle."""
    archive_name = entry.get("archive")
    manifest_name = entry.get("manifest")
    if not isinstance(archive_name, str) or not isinstance(manifest_name, str):
        raise TypeError("Runtime archive/manifest paths missing")
    if archive_name not in validated or manifest_name not in validated:
        raise ValueError("Runtime resources absent from bundle manifest")
    manifest = json.loads((bundle / manifest_name).read_text(encoding="utf-8"))
    files = manifest.get("files") if isinstance(manifest, dict) else None
    if not isinstance(files, dict) or not files:
        raise ValueError("Runtime source file manifest missing")
    content_hash = hashlib.sha256(
        json.dumps(files, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    if manifest.get("source_sha256") != content_hash or entry.get("source_sha256") != content_hash:
        raise ValueError("Runtime source content hash mismatch")
    archive_path = bundle / archive_name
    if manifest.get("archive_sha256") != hashlib.sha256(archive_path.read_bytes()).hexdigest():
        raise ValueError("Runtime archive checksum mismatch")
    with zipfile.ZipFile(archive_path) as archive:
        members = archive.infolist()
        names = [member.filename for member in members]
        if len(set(names)) != len(names) or set(names) != set(files):
            raise ValueError("Runtime archive file set mismatch")
        for member in members:
            pure = PurePosixPath(member.filename)
            if (not pure.parts or pure.is_absolute() or ".." in pure.parts or "\\" in member.filename
                    or ":" in member.filename or member.filename != pure.as_posix()
                    or member.is_dir() or stat.S_ISLNK(member.external_attr >> 16)):
                raise ValueError("Invalid runtime source path:" + member.filename)
            if hashlib.sha256(archive.read(member)).hexdigest() != files[member.filename]:
                raise ValueError("Runtime source checksum mismatch:" + member.filename)


def validate_bundle(bundle: Path) -> dict[str, str]:
    manifest = json.loads((bundle / "bundle-manifest.json").read_text(encoding="utf-8"))
    if not isinstance(manifest, dict) or manifest.get("skills") != list(SKILL_NAMES):
        raise ValueError("Unexpected bundle skill list")
    files = manifest.get("files")
    if not isinstance(files, dict) or not files:
        raise ValueError("Bundle file manifest is missing")
    validated: dict[str, str] = {}
    for relative, expected in files.items():
        if not isinstance(relative, str) or not isinstance(expected, str):
            raise TypeError("Invalid bundle file entry")
        pure = PurePosixPath(relative)
        if pure.is_absolute() or ".." in pure.parts or "\\" in relative or ":" in relative:
            raise ValueError(f"Invalid bundle path: {relative}")
        source = bundle.joinpath(*pure.parts)
        if source.is_symlink() or not source.resolve().is_relative_to(bundle.resolve()):
            raise ValueError(f"Bundle path escapes its directory: {relative}")
        if not source.is_file() or hashlib.sha256(source.read_bytes()).hexdigest() != expected:
            raise ValueError(f"Bundle checksum mismatch: {relative}")
        validated[relative] = expected
    for name in SKILL_NAMES:
        if f"skills/{name}/SKILL.md" not in files:
            raise ValueError(f"Skill entry missing from manifest: {name}")
    actual = {
        file.relative_to(bundle).as_posix()
        for name in SKILL_NAMES
        for file in (bundle / "skills" / name).rglob("*")
        if file.is_file()
    }
    listed = {name for name in files if name.startswith("skills/")}
    if actual != listed:
        raise ValueError("Skill directory contains files absent from the manifest")
    runtime = manifest.get("runtime_source")
    if runtime is not None:
        if not isinstance(runtime, dict):
            raise ValueError("Invalid runtime source declaration")
        validate_runtime(bundle, runtime, validated)
    return validated


def install_bundle(bundle: Path, destination: Path) -> dict[str, object]:
    bundle = bundle.resolve()
    files = validate_bundle(bundle)
    destination = destination.expanduser().resolve()
    destination.mkdir(parents=True, exist_ok=True)
    lock = destination / ".reseam-install.lock"
    with lock.open("x", encoding="utf-8") as stream:
        stream.write(str(os.getpid()))
    try:
        staging = Path(tempfile.mkdtemp(prefix=".reseam-install-", dir=destination)).resolve()
        if staging.parent != destination:
            raise ValueError("Unexpected staging location")
        backup = staging / "previous"
        backup.mkdir()
        for name in SKILL_NAMES:
            target = destination / name
            if target.is_symlink():
                raise ValueError(f"Resolve the existing skill symlink before installing: {target}")
            if target.exists():
                shutil.copytree(target, staging / name, symlinks=True)
            else:
                (staging / name).mkdir()
        for relative in files:
            parts = PurePosixPath(relative).parts
            if len(parts) < 3 or parts[0] != "skills" or parts[1] not in SKILL_NAMES:
                continue
            target = staging.joinpath(*parts[1:])
            # Remove a conflicting link in staging without following its target.
            for parent in reversed(target.relative_to(staging).parents):
                candidate = staging / parent
                if candidate.is_symlink():
                    candidate.unlink()
            if target.is_symlink():
                target.unlink()
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(bundle / relative, target)

        moved: list[str] = []
        published: list[str] = []
        try:
            for name in SKILL_NAMES:
                target = destination / name
                if target.exists():
                    os.replace(target, backup / name)
                    moved.append(name)
                os.replace(staging / name, target)
                published.append(name)
        except OSError:
            for name in reversed(published):
                os.replace(destination / name, staging / name)
            for name in reversed(moved):
                os.replace(backup / name, destination / name)
            raise
        return {
            "status": "SKILLS_INSTALLED",
            "destination": str(destination),
            "skills": list(SKILL_NAMES),
            "previous_skills": str(backup) if moved else None,
            "next_step": str(destination / "reseam-uv/references/full-installation.md"),
            "environment_installation": "Continue with the agent-managed all-components installation contract",
        }
    finally:
        lock.unlink()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, default=Path(__file__).resolve().parent)
    codex_home = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex"))
    parser.add_argument("--dest", type=Path, default=codex_home / "skills")
    args = parser.parse_args()
    result = install_bundle(args.bundle, args.dest)
    print(json.dumps(result, ensure_ascii=True, indent=2))


if __name__ == "__main__":
    main()
