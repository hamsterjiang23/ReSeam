"""Build the locked official MikkTSpace dependency without overwriting evidence.

Run with ``uv run python scripts/build_budget_mikktspace.py``. A matching,
complete output is reused without downloads or compilation. Incomplete or
mismatched outputs require a different --output; this script never repairs an
existing directory by overwriting its files.
"""

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

UPSTREAM = "https://github.com/mmikk/MikkTSpace"
SOURCE_LOCKS = {
    "mikktspace.c": {
        "url": "https://raw.githubusercontent.com/mmikk/MikkTSpace/master/mikktspace.c",
        "sha256": "de87e74107df766ce68108801262bd8d53899414236b59810509a8fc2a51e288",
    },
    "mikktspace.h": {
        "url": "https://raw.githubusercontent.com/mmikk/MikkTSpace/master/mikktspace.h",
        "sha256": "17fc433894f24c73753d548086cc4d8c5c0379f4a6edfb98b5da243e4f0bc3d0",
    },
}
MAX_DOWNLOAD_BYTES = 2 * 1024 * 1024


def _sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _library_name() -> str:
    return "mikktspace.dll" if os.name == "nt" else "libmikktspace.so"


def _download(url: str, timeout: float) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "ReSeam-locked-native-builder/1"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        data = response.read(MAX_DOWNLOAD_BYTES + 1)
    if len(data) > MAX_DOWNLOAD_BYTES:
        raise ValueError("MIKKTSPACE_DOWNLOAD_TOO_LARGE")
    return data


def _resolve_compiler(compiler: str) -> str:
    candidate = Path(compiler).expanduser()
    resolved = str(candidate.resolve()) if candidate.is_file() else shutil.which(compiler)
    if resolved is None:
        raise FileNotFoundError(f"GNU-compatible C compiler not found: {compiler}; pass --cc PATH")
    return resolved


def _compile_native(staging: Path, library_name: str, compiler: str, timeout: float) -> tuple[list[str], str]:
    version = subprocess.run([compiler, "--version"], check=True, capture_output=True,
                             text=True, timeout=timeout)
    command = [compiler, "-O2", "-shared", "-o", library_name, "mikktspace.c", "-lm"]
    subprocess.run(command, cwd=staging, check=True, capture_output=True, text=True, timeout=timeout)
    if not (staging / library_name).is_file() or (staging / library_name).stat().st_size == 0:
        raise RuntimeError("MIKKTSPACE_COMPILER_DID_NOT_PRODUCE_LIBRARY")
    return command, version.stdout.strip().splitlines()[0] if version.stdout.strip() else compiler


def _verify_library(path: Path, timeout: float) -> None:
    # A separate process releases the DLL before staging is published on Windows.
    command = [sys.executable, "-c", "import ctypes,sys; ctypes.CDLL(sys.argv[1]).genTangSpaceDefault", str(path)]
    subprocess.run(command, check=True, capture_output=True, text=True, timeout=timeout)


def _validate_existing(output: Path, library_name: str, timeout: float) -> dict[str, object]:
    """Read-only verification; legacy complete manifests are also recognized."""
    if not output.is_dir() or output.is_symlink():
        raise FileExistsError(f"MIKKTSPACE_OUTPUT_NOT_A_REGULAR_DIRECTORY:{output}")
    manifest_path = output / "build_manifest.json"
    if not manifest_path.is_file():
        raise FileExistsError(f"MIKKTSPACE_OUTPUT_INCOMPLETE:{output}; choose a new --output")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (ValueError, OSError) as error:
        raise FileExistsError(f"MIKKTSPACE_MANIFEST_INVALID:{output}") from error
    if not isinstance(manifest, dict) or manifest.get("upstream") != UPSTREAM:
        raise FileExistsError(f"MIKKTSPACE_MANIFEST_INVALID:{output}")
    files = manifest.get("files")
    if not isinstance(files, dict):
        raise FileExistsError(f"MIKKTSPACE_MANIFEST_INCOMPLETE:{output}")
    for name in (*SOURCE_LOCKS, library_name):
        path = output / name
        info = files.get(name)
        if not path.is_file() or path.is_symlink() or not isinstance(info, dict):
            raise FileExistsError(f"MIKKTSPACE_OUTPUT_INCOMPLETE:{path}")
        actual = _sha256(path)
        if info.get("sha256") != actual:
            raise FileExistsError(f"MIKKTSPACE_OUTPUT_HASH_MISMATCH:{path}")
        if name in SOURCE_LOCKS and actual != SOURCE_LOCKS[name]["sha256"]:
            raise FileExistsError(f"MIKKTSPACE_SOURCE_LOCK_MISMATCH:{path}")
    if not isinstance(manifest.get("compiler"), str) or not isinstance(manifest.get("build_command"), list):
        raise FileExistsError(f"MIKKTSPACE_MANIFEST_INCOMPLETE:{output}")
    _verify_library(output / library_name, timeout)
    return manifest


def _publish(staging: Path, output: Path, names: tuple[str, ...]) -> None:
    """Create an exclusive output after validation; copy the manifest last.

    Directory rename may overwrite a racing empty directory on POSIX. Exclusive
    mkdir plus exclusive file creation avoids that; failed publication removes
    only files this call created and never recursively deletes the output.
    """
    output.mkdir(exist_ok=False)
    written: list[Path] = []
    try:
        for name in names:
            destination = output / name
            with destination.open("xb") as target:
                written.append(destination)
                with (staging / name).open("rb") as source:
                    shutil.copyfileobj(source, target)
    except BaseException:
        for path in written:
            path.unlink(missing_ok=True)
        try:
            output.rmdir()
        except OSError:
            # Preserve files potentially created concurrently by another actor.
            pass
        raise


def build_native(output: Path, *, compiler: str = "gcc", timeout: float = 60) -> dict[str, object]:
    """Build in staging or reuse a verified output without changing it."""
    if not 0 < timeout <= 600:
        raise ValueError("timeout must be in (0, 600] seconds")
    if output.is_symlink():
        raise FileExistsError(f"MIKKTSPACE_OUTPUT_SYMLINK:{output}")
    output = output.expanduser().resolve()
    library_name = _library_name()
    if output.exists():
        manifest = _validate_existing(output, library_name, timeout)
        return {"status": "REUSED", "output": str(output), "library": str(output / library_name),
                "source_locks": SOURCE_LOCKS, "manifest": manifest}
    cc = _resolve_compiler(compiler)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=f".{output.name}-build-", dir=output.parent) as temporary:
        staging = Path(temporary).resolve()
        if staging.parent != output.parent or not staging.name.startswith(f".{output.name}-build-"):
            raise RuntimeError("Unexpected native staging location")
        for name, lock in SOURCE_LOCKS.items():
            data = _download(lock["url"], timeout)
            if hashlib.sha256(data).hexdigest() != lock["sha256"]:
                raise ValueError(f"MIKKTSPACE_DOWNLOAD_HASH_MISMATCH:{name}; locked snapshot was not upgraded")
            (staging / name).write_bytes(data)
        command, version = _compile_native(staging, library_name, cc, timeout)
        _verify_library(staging / library_name, timeout)
        files = {name: {"sha256": _sha256(staging / name), "bytes": (staging / name).stat().st_size}
                 for name in (*SOURCE_LOCKS, library_name)}
        manifest = {"schema_version": 1, "upstream": UPSTREAM, "source_locks": SOURCE_LOCKS,
                    "files": files, "compiler": version, "compiler_path": cc, "build_command": command,
                    "platform": sys.platform, "pointer_bits": 64 if sys.maxsize > 2**32 else 32,
                    "library_load_and_symbol_check": "PASS", "sha256_lock_required": True}
        (staging / "build_manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
        _publish(staging, output, (*SOURCE_LOCKS, library_name, "build_manifest.json"))
    return {"status": "BUILT", "output": str(output), "library": str(output / library_name),
            "source_locks": SOURCE_LOCKS, "manifest": manifest}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path,
                        default=Path(__file__).resolve().parents[1] / "artifacts/runtime/mikktspace")
    parser.add_argument("--cc", default="gcc", help="GNU-compatible compiler command or executable path")
    parser.add_argument("--timeout", type=float, default=60, help="per-download/compiler/loader timeout seconds")
    args = parser.parse_args(argv)
    try:
        result = build_native(args.output, compiler=args.cc, timeout=args.timeout)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        print(json.dumps({"status": "ERROR", "error": str(error)}, ensure_ascii=False))
        return 1
    print(json.dumps(result, ensure_ascii=False, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
