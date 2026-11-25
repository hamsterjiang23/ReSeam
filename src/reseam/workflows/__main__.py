"""Run a Skill workflow with immutable outputs and JSON-only stdout."""

from __future__ import annotations

import argparse
import contextlib
import importlib.util
import json
import time
import traceback
from pathlib import Path

from ..agent_tools.state import read_object, write_json

COMMANDS = ("rebuild", "preview", "collect", "report")


def check_dependencies(command: str | None = None) -> dict[str, object]:
    """Check imports separately from an external-backend execution smoke."""
    names = ["numpy", "scipy", "trimesh", "PIL", "shapely", "xatlas", "reseam.agent_tools.runner"]
    if command == "report":
        names.append("matplotlib")
    dependencies = {name: importlib.util.find_spec(name) is not None for name in names}
    ready = all(dependencies.values())
    return {"status": "READY" if ready else "DEPENDENCY_MISSING", "execution_success": ready,
            "dependencies": dependencies, "workflow_commands": list(COMMANDS),
            "backend_execution_tested": False, "quality_accepted": False}


def run_workflow(command: str, request_path: Path, output: Path) -> dict[str, object]:
    """Persist the request, log and result without replacing an existing run."""
    if command not in COMMANDS:
        raise ValueError("UNKNOWN_WORKFLOW:" + command)
    request = read_object(request_path.resolve())
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    write_json(output / "request.json", request)
    started = time.monotonic()
    with ((output / "process.log").open("w", encoding="utf-8") as log,
          contextlib.redirect_stdout(log), contextlib.redirect_stderr(log)):
        try:
            if command == "report":
                from .report import report

                result = report(request, output)
            else:
                from .parts import collect, preview, rebuild

                result = {"rebuild": rebuild, "preview": preview, "collect": collect}[command](request, output)
            result.setdefault("status", "OK")
            result.setdefault("execution_success", True)
        except Exception as error:  # noqa: BLE001 - CLI boundary retains traceback and machine-readable failure.
            traceback.print_exc()
            result = {"status": "ERROR", "execution_success": False,
                      "reason": str(error), "error_type": type(error).__name__}
    result.update(workflow=command, output=str(output), wall_seconds=time.monotonic() - started)
    write_json(output / "result.json", result)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=COMMANDS, nargs="?")
    parser.add_argument("--request", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    try:
        if args.check:
            result = check_dependencies(args.command)
        elif args.command is None or args.request is None or args.output is None:
            parser.error("command, --request and --output are required unless --check is supplied")
        else:
            result = run_workflow(args.command, args.request, args.output)
    except (OSError, ValueError, TypeError) as error:
        result = {"status": "ERROR", "execution_success": False,
                  "reason": str(error), "error_type": type(error).__name__}
    print(json.dumps(result, ensure_ascii=False, allow_nan=False))
    if result.get("execution_success") is not True:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
