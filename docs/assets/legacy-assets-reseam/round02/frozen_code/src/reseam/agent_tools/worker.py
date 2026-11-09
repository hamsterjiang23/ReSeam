"""Private bounded operation process; stdout is captured by the public runner."""

import subprocess
import sys
import time
import traceback
from pathlib import Path

from .registry import handler
from .state import check_deadline, read_object, write_json


def execute(name: str, request: dict[str, object], output: Path, deadline: float) -> dict[str, object]:
    try:
        check_deadline(deadline)
        result = handler(name)(request, output, deadline)
        check_deadline(deadline)
        result.setdefault("status", "OK")
        result.setdefault("execution_success", result["status"] == "OK")
    except Exception as error:  # noqa: BLE001 -- process boundary persists every failure for the caller
        traceback.print_exc()
        status = "ERROR"
        if isinstance(error, (TimeoutError, subprocess.TimeoutExpired)):
            status = "TIMEOUT"
        elif isinstance(error, (FileNotFoundError, ModuleNotFoundError)):
            status = "DEPENDENCY_MISSING"
        elif isinstance(error, (ValueError, TypeError)):
            status = "REJECTED"
        result = {"status": status, "reason": str(error), "execution_success": False,
                  "final_quality_passed": False}
    result["tool"] = name
    write_json(output / "result.json", result)
    return result


def main() -> None:
    name, directory, seconds = sys.argv[1:]
    output = Path(directory)
    execute(name, read_object(output / "request.json"), output, time.monotonic() + float(seconds))


if __name__ == "__main__":
    main()
