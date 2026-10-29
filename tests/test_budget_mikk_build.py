import hashlib
import importlib.util
import json
import subprocess
from pathlib import Path
from types import ModuleType

import pytest


def _load_builder() -> ModuleType:
    path = Path(__file__).resolve().parents[1] / "scripts/build_budget_mikktspace.py"
    spec = importlib.util.spec_from_file_location("reseam_budget_native_builder_tests", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


builder = _load_builder()


@pytest.fixture
def mocked_native(monkeypatch: pytest.MonkeyPatch) -> dict[str, bytes]:
    sources = {"mikktspace.c": b"locked test C source", "mikktspace.h": b"locked test header"}
    locks = {name: {"url": f"https://example.invalid/{name}", "sha256": hashlib.sha256(data).hexdigest()}
             for name, data in sources.items()}
    monkeypatch.setattr(builder, "SOURCE_LOCKS", locks)
    monkeypatch.setattr(builder, "_resolve_compiler", lambda compiler: "mock-cc")
    monkeypatch.setattr(builder, "_download", lambda url, timeout: sources[url.rsplit("/", 1)[-1]])

    def compile_fake(staging: Path, library_name: str, compiler: str, timeout: float) -> tuple[list[str], str]:
        (staging / library_name).write_bytes(b"test native library; no real compiler was started")
        return [compiler, "-O2", "-shared", "-o", library_name, "mikktspace.c", "-lm"], "mock compiler 1"

    def verify_fake(path: Path, timeout: float) -> None:
        assert path.is_file()
        assert path.read_bytes().startswith(b"test native library")

    monkeypatch.setattr(builder, "_compile_native", compile_fake)
    monkeypatch.setattr(builder, "_verify_library", verify_fake)
    return sources


def test_source_locks_match_runtime_without_touching_current_native() -> None:
    # Reading the module is safe while its native library is in use; no imports
    # or native calls are needed to compare these frozen source digests.
    module = Path(__file__).resolve().parents[1] / "src/reseam/budget_material.py"
    text = module.read_text(encoding="utf-8")
    assert f'_MIKK_SOURCE_SHA256 = "{builder.SOURCE_LOCKS["mikktspace.c"]["sha256"]}"' in text
    assert f'_MIKK_HEADER_SHA256 = "{builder.SOURCE_LOCKS["mikktspace.h"]["sha256"]}"' in text


def test_new_mock_build_publishes_verified_manifest(
    tmp_path: Path, mocked_native: dict[str, bytes],
) -> None:
    output = tmp_path / "new-native"
    result = builder.build_native(output)
    assert result["status"] == "BUILT"
    manifest = json.loads((output / "build_manifest.json").read_text(encoding="utf-8"))
    assert manifest["upstream"] == builder.UPSTREAM
    assert manifest["source_locks"] == builder.SOURCE_LOCKS
    assert manifest["library_load_and_symbol_check"] == "PASS"
    assert manifest["compiler"] == "mock compiler 1"
    for name, data in mocked_native.items():
        assert (output / name).read_bytes() == data
    library = output / builder._library_name()
    assert manifest["files"][library.name]["sha256"] == hashlib.sha256(library.read_bytes()).hexdigest()
    assert set(tmp_path.iterdir()) == {output}


def test_matching_complete_native_reuses_without_download_or_compiler(
    tmp_path: Path, mocked_native: dict[str, bytes], monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "native"
    builder.build_native(output)
    before = {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in output.iterdir()}

    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("reuse attempted network or compilation")

    monkeypatch.setattr(builder, "_download", forbidden)
    monkeypatch.setattr(builder, "_compile_native", forbidden)
    monkeypatch.setattr(builder, "_resolve_compiler", forbidden)
    assert builder.build_native(output)["status"] == "REUSED"
    after = {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in output.iterdir()}
    assert before == after


def test_existing_mismatch_is_not_overwritten(
    tmp_path: Path, mocked_native: dict[str, bytes],
) -> None:
    output = tmp_path / "native"
    builder.build_native(output)
    (output / "mikktspace.c").write_bytes(b"unrelated changed source")
    before = {p.name: p.read_bytes() for p in output.iterdir()}
    with pytest.raises(FileExistsError, match="HASH_MISMATCH"):
        builder.build_native(output)
    assert before == {p.name: p.read_bytes() for p in output.iterdir()}


def test_existing_incomplete_output_is_preserved(tmp_path: Path, mocked_native: dict[str, bytes]) -> None:
    output = tmp_path / "native"
    output.mkdir()
    (output / "keep.txt").write_text("existing user file", encoding="utf-8")
    with pytest.raises(FileExistsError, match="INCOMPLETE"):
        builder.build_native(output)
    assert [p.name for p in output.iterdir()] == ["keep.txt"]


def test_bad_download_hash_leaves_no_output_or_staging(
    tmp_path: Path, mocked_native: dict[str, bytes], monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(builder, "_download", lambda url, timeout: b"different upstream revision")
    with pytest.raises(ValueError, match="DOWNLOAD_HASH_MISMATCH"):
        builder.build_native(tmp_path / "native")
    assert list(tmp_path.iterdir()) == []


def test_compile_failure_leaves_no_output_or_staging(
    tmp_path: Path, mocked_native: dict[str, bytes], monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail(staging: Path, library_name: str, compiler: str, timeout: float) -> tuple[list[str], str]:
        raise subprocess.CalledProcessError(1, [compiler])

    monkeypatch.setattr(builder, "_compile_native", fail)
    with pytest.raises(subprocess.CalledProcessError):
        builder.build_native(tmp_path / "native")
    assert list(tmp_path.iterdir()) == []


def test_failed_native_load_leaves_no_output_or_staging(
    tmp_path: Path, mocked_native: dict[str, bytes], monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail(path: Path, timeout: float) -> None:
        raise OSError("wrong architecture or missing symbol")

    monkeypatch.setattr(builder, "_verify_library", fail)
    with pytest.raises(OSError, match="wrong architecture"):
        builder.build_native(tmp_path / "native")
    assert list(tmp_path.iterdir()) == []


def test_publication_race_preserves_existing_directory(
    tmp_path: Path, mocked_native: dict[str, bytes], monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "native"
    original = builder._compile_native

    def racing(staging: Path, library_name: str, compiler: str, timeout: float) -> tuple[list[str], str]:
        result = original(staging, library_name, compiler, timeout)
        output.mkdir()
        (output / "racing.txt").write_bytes(b"another actor's output")
        return result

    monkeypatch.setattr(builder, "_compile_native", racing)
    with pytest.raises(FileExistsError):
        builder.build_native(output)
    assert (output / "racing.txt").read_bytes() == b"another actor's output"
    assert {p.name for p in output.iterdir()} == {"racing.txt"}


def test_cli_reports_reuse_and_errors_as_json(
    tmp_path: Path, mocked_native: dict[str, bytes], capsys: pytest.CaptureFixture[str],
) -> None:
    output = tmp_path / "native"
    assert builder.main(["--output", str(output), "--cc", "mock-cc"]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "BUILT"
    assert builder.main(["--output", str(output)]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "REUSED"
    (output / "mikktspace.h").write_bytes(b"changed")
    assert builder.main(["--output", str(output)]) == 1
    assert json.loads(capsys.readouterr().out)["status"] == "ERROR"


def test_invalid_timeout_creates_no_directory(tmp_path: Path, mocked_native: dict[str, bytes]) -> None:
    with pytest.raises(ValueError, match="timeout"):
        builder.build_native(tmp_path / "native", timeout=0)
    assert list(tmp_path.iterdir()) == []
