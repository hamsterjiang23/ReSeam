"""Build a small adapter using verified objects from a prepared official OptCuts build."""

import argparse
import hashlib
import json
import shlex
import subprocess
from pathlib import Path


def linux(path: Path) -> str:
    value = path.resolve().as_posix()
    return "/mnt/" + value[0].lower() + value[2:]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--optcuts-build", type=Path, required=True)
    parser.add_argument("--optcuts-source", type=Path, required=True)
    parser.add_argument("--distro", default="Ubuntu-22.04")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    official = root / "references/OptCuts"
    source: Path = args.optcuts_source.resolve()
    build: Path = args.optcuts_build.resolve()
    commit = subprocess.check_output(["git", "-C", str(official), "rev-parse", "HEAD"], text=True).strip()
    other_commit = subprocess.check_output(["git", "-C", str(source), "rev-parse", "HEAD"], text=True).strip()
    if commit != other_commit:
        raise ValueError("Prepared build source commit differs from pinned reference")
    link_file = build / "CMakeFiles/OptCuts_bin.dir/link.txt"
    link = shlex.split(link_file.read_text(encoding="utf-8"))
    objects = [s for s in link if s.endswith(".cpp.o") and not s.endswith("/main.cpp.o")]
    for obj in objects:
        relative = obj.split(".dir/", 1)[1][:-2]
        if (source / relative).read_bytes() != (official / relative).read_bytes():
            raise ValueError(f"Changed upstream source: {relative}")
    flags_file = build / "CMakeFiles/OptCuts_bin.dir/flags.make"
    flags = flags_file.read_text(encoding="utf-8").splitlines()
    includes = shlex.split(next(s.partition("=")[2] for s in flags if s.startswith("CXX_INCLUDES =")))
    libraries = link[link.index("-o") + 2 :]
    output = root / "artifacts/runtime/reseam-continuous"
    output.parent.mkdir(parents=True, exist_ok=True)
    command = [
        "wsl",
        "-d",
        args.distro,
        "--cd",
        linux(build),
        "--",
        "g++",
        "-std=c++14",
        "-O2",
        *includes,
        linux(root / "native/continuous.cpp"),
        *objects,
        "-o",
        linux(output),
        *libraries,
    ]
    subprocess.run(command, check=True, timeout=120)
    evidence = {
        "upstream_commit": commit,
        "prepared_build": str(build),
        "prepared_source": str(source),
        "command": command,
        "binary_sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
        "source_sha256": hashlib.sha256((root / "native/continuous.cpp").read_bytes()).hexdigest(),
    }
    (output.parent / "build.json").write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": "BUILT", "output": str(output), "upstream_commit": commit}))


if __name__ == "__main__":
    main()
