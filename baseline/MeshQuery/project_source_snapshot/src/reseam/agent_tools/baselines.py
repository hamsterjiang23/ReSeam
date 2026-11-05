"""Thin public-tool bridge to the separately archived baseline implementation."""

import runpy
from pathlib import Path
from typing import cast

from .registry import Handler


def run_baseline(request: dict[str, object], output: Path, deadline: float) -> dict[str, object]:
    entry = Path(__file__).resolve().parents[3] / "baseline" / "runner.py"
    function = cast(Handler, runpy.run_path(str(entry))["run_baseline"])
    return function(request, output, deadline)
