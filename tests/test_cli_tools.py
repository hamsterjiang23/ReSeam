"""The real tools CLI maps execution success independently of semantic status."""

import json
import sys
from pathlib import Path

import pytest

from reseam import cli
from reseam.agent_tools import runner


@pytest.mark.parametrize(("tool", "result", "exit_code"), [
    ("artist.review", {"status": "VISUAL_KEEP", "execution_success": True}, 0),
    ("artist.review", {"status": "VISUAL_REVERT", "execution_success": True}, 0),
    ("artist.export", {"status": "EXPORTED_VISUAL", "execution_success": True}, 0),
    ("artist.export", {"status": "REJECTED", "execution_success": False}, 1),
    ("artist.export", {"status": "ERROR", "execution_success": False}, 1),
    ("artist.review", {"status": "OK", "execution_success": False}, 1),
    ("artist.review", {"status": "OK", "execution_success": "true"}, 1),
    ("artist.review", {"status": "OK"}, 0),
    ("artist.export", {"status": "REJECTED"}, 1),
    ("artist.review", {}, 0),
])
def test_tools_cli_honors_execution_result_with_legacy_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
    tool: str, result: dict[str, object], exit_code: int,
) -> None:
    request, output = tmp_path / "request.json", tmp_path / "output"

    def fake_run(name: str, request_path: Path, destination: Path) -> dict[str, object]:
        assert name == tool and request_path == request and destination == output
        return result.copy()

    monkeypatch.setattr(runner, "run_tool", fake_run)
    monkeypatch.setattr(sys, "argv", ["reseam", "tools", "run", tool,
                                     "--request", str(request), "--output", str(output)])
    if exit_code:
        with pytest.raises(SystemExit) as caught:
            cli.main()
        assert caught.value.code == exit_code
    else:
        cli.main()
    assert json.loads(capsys.readouterr().out) == result
