"""Explicit local runtime configuration; never alter the old asset project."""

import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any


def linux_path(path: Path) -> str:
    value = path.resolve().as_posix()
    if len(value) > 1 and value[1] == ":":
        return "/mnt/" + value[0].lower() + value[2:]
    return value


def read_runtime(root: Path) -> dict[str, Any]:
    path = root / "configs/runtime.local.json"
    if not path.exists():
        raise FileNotFoundError("Configure configs/runtime.local.json; see configs/runtime.example.json")
    return json.loads(path.read_text(encoding="utf-8-sig"))


def run_process(
    argv: list[str], folder: Path, timeout: float, cwd: Path | None = None, env: dict[str, str] | None = None
) -> float:
    if timeout <= 0:
        raise TimeoutError("BUDGET_EXHAUSTED")
    start = time.monotonic()
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "command.json").write_text(json.dumps(argv, ensure_ascii=False, indent=2), encoding="utf-8")
    environment = os.environ.copy()
    environment.update({"OMP_NUM_THREADS": "4", "OPENBLAS_NUM_THREADS": "4", "PYTHONUNBUFFERED": "1"})
    environment.update(env or {})
    with (folder / "process.log").open("w", encoding="utf-8") as log:
        try:
            process = subprocess.Popen(argv, cwd=cwd, env=environment, stdout=log, stderr=subprocess.STDOUT)
            returncode = process.wait(timeout=timeout + (5 if argv[0] == "wsl" else 0))
        except subprocess.TimeoutExpired as error:
            if os.name == "nt":
                subprocess.run(
                    ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    check=False,
                )
            else:
                process.kill()
            process.wait()
            raise TimeoutError("PROCESS_TIMEOUT") from error
    if returncode in (124, 137):
        raise TimeoutError("PROCESS_TIMEOUT")
    if returncode:
        raise RuntimeError(f"PROCESS_FAILED:{returncode}:{folder / 'process.log'}")
    return time.monotonic() - start


def wsl_command(
    runtime: dict[str, Any], argv: list[str], seconds: float, cwd: Path | None = None
) -> list[str]:
    prefix = ["wsl", "-d", runtime.get("wsl_distro", "Ubuntu-22.04")]
    if cwd is not None:
        prefix += ["--cd", linux_path(cwd)]
    return prefix + ["--", "timeout", "--kill-after=2", str(max(0.1, seconds)), *argv]
