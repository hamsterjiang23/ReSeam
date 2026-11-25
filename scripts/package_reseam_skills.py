"""Build the two skills with a checksummed, asset-free execution source snapshot."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

SKILL_NAMES = ("reseam-uv", "reseam-hard-surface")
SKILL_SUFFIXES = {".md", ".json", ".yaml", ".yml", ".py", ".png", ".jpg", ".jpeg", ".webp", ".svg", ".txt"}
RUNTIME_ARCHIVE = "skills/reseam-uv/assets/runtime-source.zip"
RUNTIME_MANIFEST = "skills/reseam-uv/assets/runtime-manifest.json"
RUNTIME_SUPPORT_SCRIPTS = (
    "agent_fbx_uv.py", "agent_pbr_binding.py", "triangle_identity_match.py",
    "build_budget_mikktspace.py", "build_native.py", "reseam_annotation_addon.py",
    "plot_budget_comparison.py", "build_budget_texture_gallery.py",
)
INSTALL_TEXT = """# ReSeam Skill完整安装包

将本目录交给Agent，发送下面这句话。`[opinion]`

> 安装这个ReSeam Skill包，并按全量安装合同安装所有依赖与工具。由你检测机器、获取锁定源码、安装配置全部可用组件、执行小型冒烟并修复可恢复问题。完成后给我逐项可用状态、日志与使用方式。

先读取 [包内全量安装合同](skills/reseam-uv/references/full-installation.md) 和
[依赖清单](skills/reseam-uv/references/install-manifest.json)，运行下面的Skill安装入口，
再继续执行合同中的全部环境安装步骤。`[opinion]`

[工具目录](TOOL-CATALOG.json) 保存打包时实际CLI返回的工具与参数说明。
目标机器安装后重新运行tools list/describe核实实际接口。`[opinion]`

```text
python -X utf8 install.py
```

缺少Python时，Agent先按官方方法准备uv，然后运行
`uv run --no-project --python 3.11 python -X utf8 install.py`。
目标目录可用 `--dest <目标Agent的skills目录>` 指定。`[opinion]`

安装器核对bundle-manifest.json哈希、合并并备份已有Skill，发布失败时回滚。
安装后的入口为 `reseam-uv/references/full-installation.md`，由Agent继续处理全部依赖。
GPU、系统操作或访问条件按该合同逐项记录实际状态。`[opinion]`

包内 `reseam-uv/assets/runtime-source.zip` 保存与当前工具匹配的执行源码，
`runtime-manifest.json` 以逐文件SHA-256及内容哈希标识实际版本。源码中的CLI采用所记录提交
的基线，排除无关workbench；其它执行模块捕获打包时的内容，不声称等于HEAD。
模型、权重、实验记录和本机runtime.local配置不随源码包分发。`[opinion]`

```text
python skills/reseam-uv/scripts/reseam_workflow.py --check
```

launcher先核对内容匹配的checkout，否则解包到独立用户缓存；不会覆盖已有checkout。
依赖检查不代表Blender／OptCuts实际运行或制作质量已通过。`[opinion]`
"""


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _zip_bytes(files: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in sorted(files.items()):
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, data)
    return buffer.getvalue()


def runtime_payload(root: Path) -> tuple[dict[str, bytes], dict[str, object]]:
    """Freeze first-party source only, without traversing upstream repositories."""
    root = root.resolve()
    revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
    paths = list((root / "src/reseam").rglob("*.py"))
    paths = [
        path for path in paths
        if "__pycache__" not in path.parts and "workbench_ui" not in path.parts
        and not path.name.startswith("workbench")
    ]
    paths.extend((root / "scripts").glob("*_worker.py"))
    paths.extend(root / "scripts" / name for name in RUNTIME_SUPPORT_SCRIPTS)
    paths.extend((root / "baseline").glob("*.py"))
    paths.extend((root / "baseline/adapters").glob("*.py"))
    paths.extend((root / "native").glob("*.cpp"))
    required = (
        "pyproject.toml", "uv.lock", "configs/runtime.example.json", "baseline/sources.json",
        "src/reseam/workflows/__main__.py", "src/reseam/workflows/parts.py", "src/reseam/workflows/report.py",
        "AGENTS.md", "baseline/README.md", "references/README.md",
    )
    for name in required:
        if not (root / name).is_file():
            raise FileNotFoundError(f"Runtime source requirement missing: {name}")
        paths.append(root / name)
    paths.extend(root / name for name in (".gitmodules", "LICENSE", "LICENSE.md", "LICENSE.txt"))
    payload: dict[str, bytes] = {}
    for source in sorted(set(paths)):
        if not source.exists():
            continue
        if source.is_symlink() or not source.resolve().is_relative_to(root):
            raise ValueError(f"Runtime source escapes checkout: {source}")
        if not source.is_file():
            raise ValueError(f"Runtime source is not a regular file: {source}")
        payload[source.relative_to(root).as_posix()] = source.read_bytes()
    # The local workbench is a separate in-flight change. Its CLI is deliberately not shipped.
    cli_path = "src/reseam/cli.py"
    payload[cli_path] = subprocess.check_output(["git", "show", f"{revision}:{cli_path}"], cwd=root)
    hashes = {name: _digest(data) for name, data in sorted(payload.items())}
    content_hash = _digest(json.dumps(hashes, sort_keys=True, separators=(",", ":")).encode("utf-8"))
    manifest: dict[str, object] = {
        "schema_version": 1,
        "source_revision": revision,
        "source_sha256": content_hash,
        "source_identity": "Per-file content hashes, not the base Git revision",
        "files": hashes,
        "overrides": {cli_path: {"origin": "committed CLI baseline", "revision": revision}},
        "excluded": ["workbench Python/UI", "models", "weights", "experiments", "runtime.local.json"],
    }
    return payload, manifest


def _tool_catalog(payload: dict[str, bytes]) -> dict[str, object]:
    """Read catalog metadata from the exact frozen source, never the ambient checkout."""
    with tempfile.TemporaryDirectory(prefix="reseam-catalog-") as temporary:
        folder = Path(temporary)
        for name, data in payload.items():
            target = folder / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        environment = os.environ.copy()
        environment["PYTHONPATH"] = str(folder / "src")
        code = (
            "import json; from reseam.agent_tools.registry import TOOLS, describe; "
            "print(json.dumps({'schema_version':1,'tools':[describe(name) for name in TOOLS]},"
            "ensure_ascii=False))"
        )
        raw = subprocess.check_output(
            [sys.executable, "-X", "utf8", "-c", code], cwd=folder, env=environment,
            text=True, encoding="utf-8",
        )
        result = json.loads(raw)
        if not isinstance(result, dict) or not isinstance(result.get("tools"), list):
            raise TypeError("Invalid runtime tool catalog")
        return result


def build_bundle(root: Path, output: Path) -> dict[str, object]:
    root = root.resolve()
    output = output.resolve()
    if output.exists():
        raise FileExistsError(output)
    payload: dict[str, bytes] = {}
    for name in SKILL_NAMES:
        for source in sorted((root / "skills" / name).rglob("*")):
            if not source.is_file() or source.suffix.lower() not in SKILL_SUFFIXES:
                continue
            if (source.is_symlink() or not source.resolve().is_relative_to(root)
                    or any(part.startswith(".") for part in source.relative_to(root).parts)):
                raise ValueError(f"Unexpected skill resource: {source}")
            payload[source.relative_to(root).as_posix()] = source.read_bytes()
    payload["install.py"] = (root / "scripts/install_reseam_skills.py").read_bytes()
    payload["INSTALL.md"] = INSTALL_TEXT.encode("utf-8")
    source_payload, source_manifest = runtime_payload(root)
    runtime_zip = _zip_bytes(source_payload)
    source_manifest["archive_sha256"] = _digest(runtime_zip)
    payload[RUNTIME_ARCHIVE] = runtime_zip
    payload[RUNTIME_MANIFEST] = (json.dumps(source_manifest, ensure_ascii=False, indent=2) + "\n").encode()
    catalog = _tool_catalog(source_payload)
    catalog_tools = catalog["tools"]
    if not isinstance(catalog_tools, list):
        raise TypeError("Invalid runtime tool catalog")
    payload["TOOL-CATALOG.json"] = (json.dumps(catalog, ensure_ascii=False, indent=2) + "\n").encode()
    revision = source_manifest["source_revision"]
    manifest = {
        "schema_version": 2,
        "skills": list(SKILL_NAMES),
        "repository": "https://github.com/hamsterjiang23/ReSeam.git",
        "source_revision": revision,
        "runtime_source": {
            "archive": RUNTIME_ARCHIVE, "manifest": RUNTIME_MANIFEST,
            "source_sha256": source_manifest["source_sha256"],
            "archive_sha256": source_manifest["archive_sha256"],
            "file_count": len(source_payload),
        },
        "tool_count": len(catalog_tools),
        "runtime_source_sha256": source_manifest["source_sha256"],
        "runtime_file_count": len(source_payload),
        "skill_source": "Current skill files embedded in this archive",
        "environment_installation": "Agent-managed all-components contract in INSTALL.md",
        "files": {name: hashlib.sha256(data).hexdigest() for name, data in sorted(payload.items())},
    }
    payload["bundle-manifest.json"] = (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode()
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="reseam-package-", dir=output.parent) as temporary:
        staging = Path(temporary) / output.name
        with zipfile.ZipFile(staging, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for name, data in sorted(payload.items()):
                archive.writestr(name, data)
        with zipfile.ZipFile(staging) as archive:
            if archive.testzip() is not None:
                raise ValueError("Bundle ZIP integrity check failed")
        # Exclusive publication also prevents a concurrent builder from replacing this artifact.
        with output.open("xb") as stream:
            stream.write(staging.read_bytes())
    digest = hashlib.sha256(output.read_bytes()).hexdigest()
    output.with_suffix(output.suffix + ".sha256").write_text(
        f"{digest}  {output.name}\n", encoding="utf-8"
    )
    return {
        "archive": str(output), "bytes": output.stat().st_size,
        "sha256": digest, "file_count": len(payload), "source_revision": revision,
        "tool_count": len(catalog_tools),
        "runtime_source_sha256": source_manifest["source_sha256"],
        "runtime_file_count": len(source_payload),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(build_bundle(args.root, args.output), ensure_ascii=True, indent=2))


if __name__ == "__main__":
    main()
