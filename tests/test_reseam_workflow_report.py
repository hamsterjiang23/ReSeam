"""Report evidence bindings and failure columns must survive composition."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import cast

import pytest
from PIL import Image

from reseam.agent_tools.state import file_hash, write_json
from reseam.workflows.__main__ import check_dependencies, run_workflow

pytestmark = pytest.mark.skipif(importlib.util.find_spec("matplotlib") is None, reason="report extra required")


def retained_cases(root: Path) -> dict[str, object]:
    evidence = root / "retained.json"
    write_json(evidence, {"rows": [{"metrics": {"faces": 4, "islands": 2, "tiles": 1, "coverage": .4}}]})
    image = root / "texture.png"
    Image.new("RGB", (16, 16), "blue").save(image)
    return {"cases": [
        {"id": "kept", "title": "保留记录", "evidence": str(evidence), "record_path": ["rows", 0],
         "images": {"basecolor": str(image), "texture": str(image)}, "status": "Agent保留，数值未测"},
        {"id": "failed", "title": "失败记录", "images": {"uv": str(root / "missing.png")},
         "status": "REJECTED", "notes": "排布被拒绝"}],
        "overview": ["kept", "failed"], "history": ["failed", "kept"]}


def test_retained_success_failure_and_missing_images_remain_separate(tmp_path: Path) -> None:
    request = retained_cases(tmp_path)
    source_hash = file_hash(tmp_path / "retained.json")
    path = tmp_path / "request.json"
    write_json(path, request)
    result = run_workflow("report", path, tmp_path / "result")
    assert result["execution_success"] is True, result
    evidence = json.loads((tmp_path / "result/evidence.json").read_text(encoding="utf-8"))
    assert evidence["cases"]["kept"]["metrics"]["tiles"] == 1
    assert evidence["cases"]["kept"]["numeric_accepted"] is None
    assert evidence["cases"]["failed"]["status"] == "REJECTED"
    assert evidence["cases"]["failed"]["missing"] == [str(tmp_path / "missing.png")]
    assert evidence["checks"]["sources_unchanged"] is True
    assert file_hash(tmp_path / "retained.json") == source_hash
    for stem in ("all-methods-basecolor", "all-methods-checker-uv", "history-basecolor", "history-checker-uv"):
        with Image.open(tmp_path / f"result/{stem}.png") as image:
            assert image.width > 0 and image.height > 0
    assert (tmp_path / "result/comparison-book.pdf").read_bytes().startswith(b"%PDF")
    assert "REJECTED" in (tmp_path / "result/index.html").read_text(encoding="utf-8")


@pytest.mark.parametrize("mutation", ["duplicate", "unknown", "unsafe", "bad_pointer"])
def test_invalid_request_is_recorded_without_changing_sources(tmp_path: Path, mutation: str) -> None:
    request = retained_cases(tmp_path)
    cases = request["cases"]
    assert isinstance(cases, list)
    if mutation == "duplicate":
        cases.append(cases[0])
    elif mutation == "unknown":
        request["overview"] = ["absent"]
    elif mutation == "unsafe":
        cases[0]["id"] = "../escape"
    else:
        cases[0]["record_path"] = ["rows", "bad"]
    path = tmp_path / "request.json"
    write_json(path, request)
    result = run_workflow("report", path, tmp_path / "result")
    assert result["execution_success"] is False
    assert (tmp_path / "result/result.json").is_file()
    assert (tmp_path / "retained.json").is_file()


def test_output_directory_is_never_overwritten(tmp_path: Path) -> None:
    request = tmp_path / "request.json"
    write_json(request, retained_cases(tmp_path))
    output = tmp_path / "exists"
    output.mkdir()
    sentinel = output / "retained.txt"
    sentinel.write_text("keep", encoding="utf-8")
    with pytest.raises(FileExistsError):
        run_workflow("report", request, output)
    assert sentinel.read_text(encoding="utf-8") == "keep"


def test_dependency_check_does_not_claim_backend_or_quality_acceptance() -> None:
    result = check_dependencies("report")
    assert result["backend_execution_tested"] is False
    assert result["quality_accepted"] is False
    assert isinstance(result["dependencies"], dict)
    assert "matplotlib" in result["dependencies"]


def test_changed_baked_texture_cannot_keep_old_acceptance(tmp_path: Path) -> None:
    import numpy as np

    from reseam.agent_tools.state import save_snapshot
    from reseam.budget_state import BudgetState
    from reseam.mesh import make_mesh
    from reseam.types import MeshState

    mesh = make_mesh(np.array([[0., 0, 0], [1., 0, 0], [0., 1., 0]]), np.array([[0, 1, 2]]))
    surface = MeshState(mesh, frozenset(), np.array([[0., 0], [1., 0], [0., 1.]]), np.array([[0, 1, 2]]))
    snapshot = save_snapshot(BudgetState(surface), tmp_path / "snapshot", {})
    texture = tmp_path / "bake/texture/BaseColor_mip0.png"
    texture.parent.mkdir(parents=True)
    Image.new("RGBA", (8, 8), "blue").save(texture)
    bake = tmp_path / "bake/bake.json"
    write_json(bake, {"state_hash": snapshot.state_hash, "target_hash": "target",
                     "files": {"texture/BaseColor_mip0.png": file_hash(texture)}})
    validation = tmp_path / "validation.json"
    write_json(validation, {"state_hash": snapshot.state_hash, "target_hash": "target",
                           "bake_sha256": file_hash(bake), "quality_accepted": True, "profile": {}})
    Image.new("RGBA", (8, 8), "red").save(texture)
    request = tmp_path / "request.json"
    write_json(request, {"cases": [{"id": "changed", "snapshot": str(snapshot.root / "snapshot.json"),
                                   "bake": str(bake), "validation": str(validation)}]})
    result = run_workflow("report", request, tmp_path / "report")
    assert result["execution_success"] is False
    assert "REPORT_BAKE_FILE_CHANGED" in str(result["reason"])


def test_missing_uv_uses_actual_render_result_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from reseam.workflows import report as module

    image = tmp_path / "actual-checker-image.png"
    uv = tmp_path / "actual-uv-image.png"
    Image.new("RGB", (8, 8), "blue").save(image)
    Image.new("RGB", (8, 8), "red").save(uv)
    tools: list[str] = []

    def renderer(tool: str, request: Path, output: Path) -> dict[str, object]:
        tools.append(tool)
        assert json.loads(request.read_text(encoding="utf-8"))["mode"] == "checker"
        return {"status": "OK", "images": {"front": str(image), "uv": str(uv)}}

    monkeypatch.setattr(module, "run_tool", renderer)
    card = module.Card("sample", "Sample", "Retained", "", {}, snapshot=tmp_path / "snapshot.json")
    sources: dict[str, str] = {}
    module._render(card, tmp_path, {}, sources)
    assert card.images["uv"] == uv
    assert card.images["checker"] == image
    assert tools == ["view.render"]


def test_unknown_retained_metrics_are_explicitly_unverified() -> None:
    from reseam.workflows.report import Card, _metric_lines

    card = Card("failed", "Failed", "REJECTED", "", {"tiles": None, "islands": None,
                                                       "resolution": 2048, "payload_bytes": None})
    lines = " ".join(_metric_lines(card))
    assert "None" not in lines
    assert "未核验" in lines


def test_single_column_headers_fit_canvas(tmp_path: Path) -> None:
    from matplotlib.backends.backend_agg import FigureCanvasAgg

    from reseam.workflows.report import Card, _figure, _font

    figure = _figure([Card("single", "单部件", "未重新验收", "", {})],
                     "all-methods-basecolor", "basecolor", tmp_path, _font({}))
    renderer = cast(FigureCanvasAgg, figure.canvas).get_renderer()
    for text in figure.texts:
        bounds = text.get_window_extent(renderer)
        assert bounds.x0 >= 0 and bounds.x1 <= figure.bbox.width
        assert bounds.y0 >= 0 and bounds.y1 <= figure.bbox.height
