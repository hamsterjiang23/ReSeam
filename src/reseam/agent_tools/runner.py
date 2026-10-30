"""Each public operation has a bounded child process and JSON-only stdout."""

import math
import os
import subprocess
import sys
import time
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from .registry import TOOLS
from .state import file_hash, read_object, write_json


def _versions() -> dict[str, str]:
    packages: dict[str, str] = {}
    for name in ("numpy", "scipy", "trimesh", "pillow", "shapely", "xatlas", "ispc-texcomp"):
        try:
            packages[name] = version(name)
        except PackageNotFoundError:
            packages[name] = "not-installed"
    return packages


def run_tool(name: str, request_path: Path, output: Path) -> dict[str, object]:
    if name not in TOOLS:
        raise ValueError("UNKNOWN_TOOL:" + name)
    request_path = request_path.resolve()
    request = read_object(request_path)
    timeout = float(str(request.get("timeout_seconds", 300)))
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("POSITIVE_FINITE_TIMEOUT_REQUIRED")
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    write_json(output / "request.json", request)
    package = Path(__file__).resolve().parents[1]
    sources = sorted(package.rglob("*.py"))
    scripts = package.parents[1] / "scripts"
    sources.extend(p for p in (scripts / "bake_variant_worker.py", scripts / "agent_asset_worker.py",
                              scripts / "agent_unwrap_worker.py", scripts / "agent_pack_worker.py") if p.is_file())
    write_json(output / "provenance.json", {
        "python": sys.version, "request_sha256": file_hash(output / "request.json"),
        "source_sha256": {str(p): file_hash(p) for p in sources},
        "packages": _versions(),
    })
    environment = os.environ.copy()
    environment.update({"PYTHONUNBUFFERED": "1", "OMP_NUM_THREADS": "1", "OPENBLAS_NUM_THREADS": "1",
                        "MKL_NUM_THREADS": "1"})
    argv = [sys.executable, "-m", "reseam.agent_tools.worker", name, str(output), str(timeout)]
    started = time.monotonic()
    write_json(output / "command.json", argv)
    with (output / "process.log").open("w", encoding="utf-8") as log:
        process = subprocess.Popen(argv, stdout=log, stderr=subprocess.STDOUT, env=environment,
                                   creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        try:
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            if os.name == "nt":
                subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                               stdout=log, stderr=subprocess.STDOUT, check=False)
            else:
                process.kill()
            process.wait()
            result: dict[str, object] = {"status": "TIMEOUT", "reason": "TOOL_PROCESS_DEADLINE",
                "execution_success": False, "tool": name, "output": str(output),
                "wall_seconds": time.monotonic() - started, "final_quality_passed": False}
            write_json(output / "result.json", result)
            return result
    result_path = output / "result.json"
    if not result_path.is_file():
        result = {"status": "ERROR", "reason": "WORKER_EXIT_WITHOUT_RESULT", "tool": name,
                  "execution_success": False, "returncode": process.returncode}
    else:
        result = read_object(result_path)
    result.update({"wall_seconds": time.monotonic() - started, "output": str(output)})
    write_json(result_path, result)
    return result
