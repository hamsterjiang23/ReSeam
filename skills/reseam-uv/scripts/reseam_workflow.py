"""Launch the packaged ReSeam workflows from verified source using only stdlib here."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path, PurePosixPath
from typing import cast

COMMANDS = ("rebuild", "preview", "collect", "report")


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _is_digest(value: object) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(c in "0123456789abcdef" for c in value)


def _relative_path(value: str) -> PurePosixPath:
    path = PurePosixPath(value)
    if (not value or not path.parts or path.is_absolute() or ".." in path.parts or "\\" in value or ":" in value
            or value != path.as_posix()):
        raise ValueError("INVALID_RUNTIME_PATH:" + value)
    return path


def read_manifest(skill: Path) -> dict[str, object]:
    path = skill / "assets/runtime-manifest.json"
    if not path.is_file():
        raise ValueError("RUNTIME_MANIFEST_MISSING:use the built Skill bundle, or run reseam.workflows in checkout")
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or raw.get("schema_version") != 1:
        raise ValueError("INVALID_RUNTIME_MANIFEST")
    files = raw.get("files")
    if not isinstance(files, dict) or not files:
        raise ValueError("INVALID_RUNTIME_FILES")
    for name, digest in files.items():
        if not isinstance(name, str) or not _is_digest(digest):
            raise ValueError("INVALID_RUNTIME_FILE_HASH")
        _relative_path(name)
    if not _is_digest(raw.get("source_sha256")) or not _is_digest(raw.get("archive_sha256")):
        raise ValueError("INVALID_RUNTIME_DIGEST")
    content_hash = _sha256(json.dumps(files, sort_keys=True, separators=(",", ":")).encode("utf-8"))
    if content_hash != raw["source_sha256"]:
        raise ValueError("RUNTIME_MANIFEST_HASH_MISMATCH")
    for required in ("pyproject.toml", "uv.lock", "src/reseam/workflows/__main__.py"):
        if required not in files:
            raise ValueError("RUNTIME_ENTRY_MISSING:" + required)
    return cast(dict[str, object], raw)


def source_matches(root: Path, manifest: dict[str, object]) -> bool:
    files = cast(dict[str, str], manifest["files"])
    if not root.is_dir() or root.is_symlink():
        return False
    resolved = root.resolve()
    for name, digest in files.items():
        path = root.joinpath(*_relative_path(name).parts)
        try:
            if (path.is_symlink() or not path.resolve().is_relative_to(resolved) or not path.is_file()
                    or _sha256(path.read_bytes()) != digest):
                return False
        except OSError:
            return False
    # Additional first-party Python could change imports even if every expected file still matches.
    source = root / "src/reseam"
    actual = {
        path.relative_to(root).as_posix() for path in source.rglob("*.py")
        if "workbench_ui" not in path.parts and not path.name.startswith("workbench")
    }
    expected = {name for name in files if name.startswith("src/reseam/") and name.endswith(".py")}
    return actual == expected


def cache_root() -> Path:
    codex = os.environ.get("CODEX_HOME")
    if codex:
        return Path(codex).expanduser().resolve() / "cache/reseam-workflows"
    return Path.home() / ".cache/reseam-workflows"


def extract_runtime(skill: Path, manifest: dict[str, object], cache: Path | None = None) -> Path:
    archive_path = skill / "assets/runtime-source.zip"
    if not archive_path.is_file() or _sha256(archive_path.read_bytes()) != manifest["archive_sha256"]:
        raise ValueError("RUNTIME_ARCHIVE_HASH_MISMATCH")
    files = cast(dict[str, str], manifest["files"])
    # Verify all bytes and archive paths before creating any execution files.
    with zipfile.ZipFile(archive_path) as archive:
        members = archive.infolist()
        names = [member.filename for member in members]
        if len(set(names)) != len(names) or set(names) != set(files):
            raise ValueError("RUNTIME_ARCHIVE_FILE_SET_MISMATCH")
        for member in members:
            _relative_path(member.filename)
            if member.is_dir() or stat.S_ISLNK(member.external_attr >> 16):
                raise ValueError("RUNTIME_ARCHIVE_NOT_REGULAR:" + member.filename)
            if _sha256(archive.read(member)) != files[member.filename]:
                raise ValueError("RUNTIME_FILE_HASH_MISMATCH:" + member.filename)
        destination_root = (cache if cache is not None else cache_root()).expanduser().resolve()
        # Keep native-library paths below Windows' DLL loader limits. Full content hashes
        # are still checked on every use, including a possible short-key collision.
        destination = destination_root / str(manifest["source_sha256"])[:16]
        if destination.exists() or destination.is_symlink():
            if not source_matches(destination, manifest):
                raise ValueError("CACHED_RUNTIME_MODIFIED:" + str(destination))
            return destination
        destination_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix=".extract-", dir=destination_root) as temporary:
            staging = Path(temporary)
            for member in members:
                target = staging.joinpath(*_relative_path(member.filename).parts)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(archive.read(member))
            if not source_matches(staging, manifest):
                raise ValueError("EXTRACTED_RUNTIME_HASH_MISMATCH")
            try:
                staging.rename(destination)
            except OSError:
                # A concurrent launcher may have published the same verified cache.
                if not source_matches(destination, manifest):
                    raise
    return destination


def select_runtime(skill: Path, manifest: dict[str, object], repo: Path | None) -> tuple[Path, str]:
    candidates: list[Path] = []
    if repo is not None:
        candidates.append(repo.expanduser().resolve())
    configured = os.environ.get("RESEAM_ROOT")
    if configured:
        candidates.append(Path(configured).expanduser().resolve())
    candidates.extend((Path.cwd(), *Path.cwd().parents))
    candidates.append(skill.parent.parent)
    for candidate in dict.fromkeys(candidates):
        if source_matches(candidate, manifest):
            return candidate, "matching_checkout"
    return extract_runtime(skill, manifest), "verified_cache"


def run_launcher(args: argparse.Namespace, skill: Path) -> tuple[dict[str, object], int]:
    manifest = read_manifest(skill)
    root, origin = select_runtime(skill, manifest, args.repo)
    context: dict[str, object] = {
        "source_sha256": manifest["source_sha256"], "source_revision": manifest.get("source_revision"),
        "source_origin": origin, "repo": str(root), "source_hash_verified": True,
        "runtime_config_present": (root / "configs/runtime.local.json").is_file(),
    }
    uv = shutil.which("uv")
    if uv is None:
        return {
            "status": "DEPENDENCY_MISSING", "execution_success": False,
            "reason": "UV_NOT_FOUND", "launcher": context, "workflow_commands": list(COMMANDS),
        }, 1
    command = [uv, "run", "--project", str(root), "--locked"]
    if args.command == "report":
        command.extend(("--extra", "reports"))
    command.extend(("python", "-m", "reseam.workflows"))
    if args.command:
        command.append(args.command)
    if args.check:
        command.append("--check")
    else:
        command.extend(("--request", str(args.request), "--output", str(args.output)))
    environment = os.environ.copy()
    environment.update(PYTHONPATH=str(root / "src"), PYTHONUNBUFFERED="1", PYTHONIOENCODING="utf-8")
    environment.pop("PYTHONHOME", None)
    result = subprocess.run(
        command, cwd=root, env=environment, capture_output=True, text=True, encoding="utf-8", check=False,
    )
    if result.stderr:
        sys.stderr.write(result.stderr)
    try:
        response = json.loads(result.stdout)
    except json.JSONDecodeError:
        reason = "WORKFLOW_PROCESS_FAILED"
        if "filename or extension is too long" in result.stderr.lower() or "winerror 206" in result.stderr.lower():
            reason = "WINDOWS_CACHE_PATH_TOO_LONG:set CODEX_HOME to a shorter user directory"
        return {
            "status": "ERROR", "execution_success": False, "reason": reason,
            "exit_code": result.returncode, "stdout": result.stdout[-8000:], "launcher": context,
        }, result.returncode or 1
    if not isinstance(response, dict):
        raise TypeError("WORKFLOW_INVALID_RESPONSE")
    response["launcher"] = context
    return cast(dict[str, object], response), result.returncode


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=COMMANDS, nargs="?")
    parser.add_argument("--request", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--repo", type=Path, help="Reuse only if every packaged source hash matches")
    parser.add_argument("--check", action="store_true", help="Check source, core dependencies and workflow interface")
    args = parser.parse_args()
    if not args.check:
        if args.command is None or args.request is None or args.output is None:
            parser.error("command, --request and --output are required unless --check is used")
        if not args.request.is_absolute() or not args.output.is_absolute():
            parser.error("--request and --output must be absolute paths")
        if not args.request.is_file():
            parser.error("--request must be an existing JSON file")
        if args.output.exists():
            parser.error("--output must be a new directory")
    try:
        result, code = run_launcher(args, Path(__file__).resolve().parents[1])
    except (OSError, ValueError, TypeError, zipfile.BadZipFile) as error:
        result = {"status": "ERROR", "execution_success": False, "reason": str(error)}
        code = 1
    print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
    raise SystemExit(code)


if __name__ == "__main__":
    main()
