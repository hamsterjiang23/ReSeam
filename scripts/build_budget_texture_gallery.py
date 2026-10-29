"""Build a read-only inventory and previews of physically produced comparison PNGs."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path
from textwrap import wrap
from typing import cast
from urllib.parse import quote, unquote

from PIL import Image, ImageDraw

CHANNELS = ("BaseColor", "Normal", "ORM")
PNG_NAME = re.compile(r"^(BaseColor|Normal|ORM)_mip(\d+)\.png$")
ACTIVE_STATUSES = {"RUNNING", "NOT_RUN", "PENDING", "PLANNED"}
PREVIEW_SEED = 17


@dataclass
class TextureFile:
    channel: str
    mip: int
    source_path: str
    source_link: str
    source_relative_path: str
    bytes: int | None = None
    sha256: str | None = None
    width: int | None = None
    height: int | None = None
    mode: str | None = None
    decoded: bool = False
    errors: list[str] = field(default_factory=list)


@dataclass
class TextureSource:
    kind: str
    candidate: str | None
    directory: str
    directory_link: str
    files: list[TextureFile]
    complete_mip0: bool
    complete_mip_chain: bool
    errors: list[str]


@dataclass
class GalleryCase:
    row_index: int
    model: str
    method: str
    seed: int
    resolution: int
    status: str
    valid: bool
    record_path: str
    record_link: str
    record_exists: bool
    record_sha256: str | None
    preview_selected: bool
    texture_availability: str
    selected_source_index: int | None
    sources: list[TextureSource]
    thumbnails: dict[str, str] = field(default_factory=dict)
    contact_sheet: str | None = None
    errors: list[str] = field(default_factory=list)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def link_to(path: Path, output: Path) -> str:
    try:
        relative = os.path.relpath(path, output).replace(os.sep, "/")
    except ValueError:
        relative = path.as_posix()
    return quote(relative, safe="/:")


def round_file(round_path: Path, relative: str) -> Path:
    result = (round_path / relative).resolve()
    if not result.is_relative_to(round_path):
        raise ValueError(f"Path escapes round directory: {relative}")
    return result


def source_directories(case_path: Path) -> list[tuple[str, str | None, Path]]:
    sources: list[tuple[str, str | None, Path]] = [("CASE_TEXTURES", None, case_path / "textures")]
    validation = case_path / "final_validation"
    if validation.is_dir():
        sources.extend(
            ("FINAL_VALIDATION_CANDIDATE", candidate.name, candidate / "textures")
            for candidate in sorted(validation.glob("candidate_*")) if candidate.is_dir()
        )
    baseline = case_path / "baseline_validation"
    if baseline.is_dir():
        sources.extend(
            ("BASELINE_VALIDATION_CANDIDATE", candidate.name, candidate / "textures")
            for candidate in sorted(baseline.glob("candidate_*")) if candidate.is_dir()
        )
    return sources


def source_pngs(case_path: Path) -> set[Path]:
    return {
        path.resolve() for _, _, directory in source_directories(case_path)
        for path in directory.glob("*.png") if PNG_NAME.fullmatch(path.name)
    }


def inspect_texture(path: Path, round_path: Path, output: Path, resolution: int) -> TextureFile:
    match = PNG_NAME.fullmatch(path.name)
    if match is None:
        raise ValueError(f"Unexpected texture filename: {path.name}")
    channel, mip_text = match.groups()
    texture = TextureFile(
        channel, int(mip_text), str(path.resolve()), link_to(path, output),
        path.relative_to(round_path).as_posix(),
    )
    try:
        data = path.read_bytes()
        texture.bytes = len(data)
        texture.sha256 = hashlib.sha256(data).hexdigest()
    except OSError as error:
        texture.errors.append(f"READ_FAILED: {type(error).__name__}: {error}")
        return texture
    try:
        with Image.open(path) as image:
            image.load()
            texture.width, texture.height = image.size
            texture.mode = image.mode
            if image.format != "PNG":
                texture.errors.append(f"FORMAT_MISMATCH: {image.format}")
            else:
                texture.decoded = True
        expected = max(1, resolution >> texture.mip)
        if (texture.width, texture.height) != (expected, expected):
            texture.errors.append(f"DIMENSION_MISMATCH: expected {expected}x{expected}")
    except (OSError, ValueError, SyntaxError, Image.DecompressionBombError) as error:
        texture.errors.append(f"DECODE_FAILED: {type(error).__name__}: {error}")
    return texture


def inspect_source(
    kind: str, candidate: str | None, directory: Path, round_path: Path,
    output: Path, resolution: int,
) -> TextureSource:
    files = [
        inspect_texture(path, round_path, output, resolution)
        for path in directory.glob("*.png") if PNG_NAME.fullmatch(path.name)
    ]
    files.sort(key=lambda texture: (CHANNELS.index(texture.channel), texture.mip))
    expected_mips = set(range(resolution.bit_length()))
    good = {(texture.channel, texture.mip) for texture in files if texture.decoded and not texture.errors}
    missing = [(channel, mip) for channel in CHANNELS for mip in sorted(expected_mips)
               if (channel, mip) not in good]
    errors = [f"MISSING_OR_INVALID: {channel}_mip{mip}.png" for channel, mip in missing]
    errors.extend(
        f"{Path(texture.source_path).name}: {error}" for texture in files for error in texture.errors
    )
    unexpected = [(texture.channel, texture.mip) for texture in files if texture.mip not in expected_mips]
    errors.extend(f"UNEXPECTED_MIP: {channel}_mip{mip}.png" for channel, mip in unexpected)
    return TextureSource(
        kind, candidate, str(directory.resolve()), link_to(directory, output), files,
        all((channel, 0) in good for channel in CHANNELS), not missing and not unexpected, errors,
    )


def choose_source(sources: list[TextureSource]) -> int | None:
    # Source priority is independent of quality metrics and matrix acceptance.
    complete = next((index for index, source in enumerate(sources) if source.complete_mip0), None)
    if complete is not None:
        return complete
    return next((index for index, source in enumerate(sources) if source.files), None)


def preview_case(case: GalleryCase, output: Path) -> None:
    if case.selected_source_index is None:
        return
    source = case.sources[case.selected_source_index]
    folder = output / "previews" / f"row_{case.row_index:03d}"
    mip0 = {texture.channel: texture for texture in source.files if texture.mip == 0 and texture.decoded}
    if not mip0:
        return
    folder.mkdir(parents=True, exist_ok=False)
    thumbnails: dict[str, Image.Image] = {}
    for channel in CHANNELS:
        texture = mip0.get(channel)
        if texture is None:
            continue
        with Image.open(texture.source_path) as image:
            thumbnail = image.convert("RGBA")
            thumbnail.thumbnail((256, 256), Image.Resampling.LANCZOS)
        preview = folder / f"{channel}_mip0_preview.png"
        thumbnail.save(preview)
        case.thumbnails[channel] = link_to(preview, output)
        thumbnails[channel] = thumbnail
    if not source.complete_mip0:
        return
    sheet = Image.new("RGB", (792, 340), "#eeeeee")
    draw = ImageDraw.Draw(sheet)
    title = f"{case.model} / {case.method} / seed {case.seed} / {case.resolution}px"
    draw.text((10, 8), title, fill="black")
    draw.text((10, 26), f"PREVIEW ONLY | {case.status} | valid={case.valid}", fill="black")
    draw.text((10, 44), f"source: {source.kind} {source.candidate or ''}", fill="black")
    for index, channel in enumerate(CHANNELS):
        x = 8 + index * 264
        draw.text((x, 66), channel, fill="black")
        thumbnail = thumbnails[channel]
        sheet.paste(thumbnail, (x, 84), thumbnail)
    sheet_path = folder / "three_channel_preview.png"
    sheet.save(sheet_path)
    case.contact_sheet = link_to(sheet_path, output)


def inspect_case(index: int, row: dict[str, object], round_path: Path, output: Path) -> GalleryCase:
    model, method, status = (str(row[key]) for key in ("model", "method", "status"))
    seed, resolution = int(str(row["seed"])), int(str(row["resolution"]))
    if resolution <= 0 or resolution & (resolution - 1):
        raise ValueError(f"Expected positive power-of-two resolution in row {index}")
    record_path = str(row["record_path"])
    record = round_file(round_path, record_path)
    sources = [inspect_source(kind, candidate, directory, round_path, output, resolution)
               for kind, candidate, directory in source_directories(record.parent)]
    selected = choose_source(sources)
    availability = "NOT_PRODUCED" if selected is None else (
        "PRODUCED" if sources[selected].complete_mip_chain else "PARTIAL_OR_INVALID"
    )
    case = GalleryCase(
        index, model, method, seed, resolution, status, row.get("valid") is True,
        record_path, link_to(record, output), record.is_file(), None,
        seed == PREVIEW_SEED, availability, selected, sources,
    )
    try:
        case.record_sha256 = sha256(record)
    except OSError as error:
        case.errors.append(f"RECORD_READ_FAILED: {type(error).__name__}: {error}")
    if selected is not None and sources[selected].kind == "FINAL_VALIDATION_CANDIDATE":
        case.errors.append("CANDIDATE_DIAGNOSTIC_ONLY: selected candidate is not an accepted final output")
    if selected is not None and sources[selected].kind == "BASELINE_VALIDATION_CANDIDATE":
        case.errors.append("BASELINE_DIAGNOSTIC_ONLY: selected baseline is not an accepted final output")
    preview_case(case, output)
    return case


def markdown_text(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ").replace("`", "'")


def markdown_gallery(
    cases: list[GalleryCase], matrix_link: str, original_matrix_link: str, matrix_source_kind: str,
    channel_contacts: dict[str, str] | None = None,
) -> str:
    qualification = (
        "本附件为中止轮的终态覆盖诊断，不代表完整正式对比。"
        if matrix_source_kind == "DIAGNOSTIC_TERMINAL_OVERLAY"
        else "本附件仅证明物理贴图清单与来源，不构成方法验收。"
    )
    lines = [
        "# 实际贴图附件",
        "",
        f"{qualification} `[verified: texture_manifest.json qualification]`",
        "",
        ("清单保留所选矩阵全部预定项及其 status/valid；存在 PNG 不代表通过验收。 "
         "`[verified: texture_manifest.json rows[*].status/valid]`"),
        "",
        (f"状态来源：{matrix_source_kind}；覆盖矩阵只提供诊断终态，原始矩阵保持只读。 "
         "`[verified: texture_manifest.json matrix_source_kind/selected_matrix/original_matrix/source_integrity]`"),
        "",
        ("预览比较固定使用 seed17；不会根据质量或是否成功改选种子。 "
         "`[verified: texture_manifest.json preview_policy]`"),
        "",
        ("缩略图与三列拼图仅为原始 PNG 的 Pillow 缩小预览；各项原图及完整 mip 文件链接见下表。 "
         "`[verified: texture_manifest.json rows[*].thumbnails/contact_sheet/sources]`"),
        "",
        ("来源优先级为 case/textures、final_validation/candidate_*、baseline_validation/candidate_*；"
         "各级按目录名排序，先选完整解码 mip0 三通道，否则选首个有物理 PNG 的来源。"
         "候选及 baseline 均为诊断贴图，不能视为最终接受结果；所有来源保留在清单中。 "
         "`[verified: texture_manifest.json source_selection_policy/rows[*].sources]`"),
        "",
        (f"[所选状态矩阵]({matrix_link}) · [原始矩阵]({original_matrix_link}) · "
         "[完整逐 PNG 清单](texture_manifest.json) "
         "`[verified: texture_manifest.json selected_matrix/original_matrix/source_integrity]`"),
        "",
        "## 固定 seed17 比较预览",
        "",
    ]
    for channel, contact in (channel_contacts or {}).items():
        lines.extend([
            f"### {channel} / seed17 实际贴图总览", "",
            (f"![{channel} seed17 实际贴图联系表]({contact}) "
             "`[verified: texture_manifest.json channel_contacts]`"), "",
        ])
    for case in cases:
        if not case.preview_selected:
            continue
        title = f"{case.model} / {case.method} / seed{case.seed} / {case.resolution}px"
        lines.extend([f"### {markdown_text(title)}", ""])
        marker = f"`[verified: texture_manifest.json rows[{case.row_index}]]`"
        lines.extend([f"{case.status}；valid={case.valid}；{case.texture_availability}。 {marker}", ""])
        if case.contact_sheet:
            lines.extend([f"![三通道实际贴图缩小预览]({case.contact_sheet}) {marker}", ""])
        else:
            lines.extend([f"完整三通道预览未生成；已有文件与缺失原因见逐项清单。 {marker}", ""])
    lines.extend(["## 全部预定项的贴图与 mip 链", ""])
    for case in cases:
        title = f"{case.model} / {case.method} / seed{case.seed} / {case.resolution}px"
        marker = f"`[verified: texture_manifest.json rows[{case.row_index}]]`"
        lines.extend([
            f"### row {case.row_index}: {markdown_text(title)}", "",
            (f"{case.status}；valid={case.valid}；{case.texture_availability}；"
             f"[record]({case.record_link})。 {marker}"), "",
        ])
        if case.selected_source_index is None:
            lines.extend([f"NOT_PRODUCED：未发现实际物理贴图，不生成替代图。 {marker}", ""])
            continue
        source = case.sources[case.selected_source_index]
        lines.extend([
            (f"来源：{source.kind} / {source.candidate or 'case/textures'}；"
             f"[原始目录]({source.directory_link})。 {marker}"), "",
            "|通道|实际 mip0 缩略预览|mip0 原图|完整物理 mip 文件列表|",
            "|---|---|---|---|",
        ])
        for channel in CHANNELS:
            files = [texture for texture in source.files if texture.channel == channel]
            mip0 = next((texture for texture in files if texture.mip == 0), None)
            preview = case.thumbnails.get(channel)
            image = f"![{channel} preview]({preview})" if preview else "NOT_PRODUCED_OR_INVALID"
            original = f"[原图]({mip0.source_link})" if mip0 else "NOT_PRODUCED"
            chain = " · ".join(f"[mip{texture.mip}]({texture.source_link})" for texture in files)
            lines.append(f"|{channel}|{image}|{original}|{chain or 'NOT_PRODUCED'} {marker}|")
        lines.append("")
        if len(case.sources) > 1:
            lines.extend(["全部来源及原始 mip 文件：", ""])
            for source_index, alternate in enumerate(case.sources):
                selected_label = "SELECTED" if source_index == case.selected_source_index else "NOT_SELECTED"
                links = " · ".join(f"[{texture.channel} mip{texture.mip}]({texture.source_link})"
                                   for texture in alternate.files)
                lines.extend([
                    (f"- {alternate.kind} / {alternate.candidate or 'case/textures'}；{selected_label}；"
                     f"complete_mip_chain={alternate.complete_mip_chain}；"
                     f"[目录]({alternate.directory_link})；{links or 'NOT_PRODUCED'}。 {marker}"),
                ])
            lines.append("")
        if source.errors or case.errors:
            errors = "; ".join(case.errors + source.errors)
            lines.extend([f"检查记录：{markdown_text(errors)}。 {marker}", ""])
    return "\n".join(lines) + "\n"


def channel_contact_sheets(cases: list[GalleryCase], output: Path) -> dict[str, str]:
    """Three seed17 contact sheets drawn solely from inspected physical PNGs.

    Every planned seed17 row keeps its cell, including rejected and absent
    outputs. A channel sheet is omitted when it has no actual decoded image.
    """
    selected = [case for case in cases if case.preview_selected]
    contacts: dict[str, str] = {}
    if not selected:
        return contacts
    columns, cell_width, cell_height = min(4, len(selected)), 288, 362
    rows = (len(selected) + columns - 1) // columns
    for channel in CHANNELS:
        if not any(channel in case.thumbnails for case in selected):
            continue
        sheet = Image.new("RGB", (columns * cell_width, rows * cell_height), "#f0f0f0")
        draw = ImageDraw.Draw(sheet)
        for index, case in enumerate(selected):
            x, y = (index % columns) * cell_width, (index // columns) * cell_height
            draw.rectangle((x + 2, y + 2, x + cell_width - 3, y + cell_height - 3), outline="#aaaaaa")
            source = None if case.selected_source_index is None else case.sources[case.selected_source_index]
            label = (f"{case.model} / {case.method}\nseed{case.seed} / {case.resolution}px / valid={case.valid}\n"
                     f"{case.status}\n{source.kind if source else 'NOT_PRODUCED'}")
            line_y = y + 8
            for line in label.splitlines():
                for part in wrap(line, 43) or [""]:
                    draw.text((x + 8, line_y), part, fill="black")
                    line_y += 12
            preview = case.thumbnails.get(channel)
            if preview is None:
                draw.text((x + 12, y + 160), f"{channel}: NOT_PRODUCED_OR_INVALID", fill="#555555")
                continue
            with Image.open(output / unquote(preview)) as image:
                thumbnail = image.convert("RGBA")
                thumbnail.thumbnail((256, 256), Image.Resampling.LANCZOS)
            sheet.paste(thumbnail, (x + 16, y + 98), thumbnail)
        path = output / f"{channel}_seed17_contact.png"
        sheet.save(path)
        contacts[channel] = link_to(path, output)
    return contacts


def load_matrix(path: Path) -> list[dict[str, object]]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, list) or not all(isinstance(row, dict) for row in raw):
        raise ValueError("Expected the complete planned matrix list")
    return cast(list[dict[str, object]], raw)


def build_gallery(round_path: Path, output: Path, matrix_path: Path | None = None) -> dict[str, object]:
    round_path, output = round_path.resolve(), output.resolve()
    if output.exists():
        raise FileExistsError(output)
    original_matrix = round_path / "matrix.json"
    matrix = original_matrix if matrix_path is None else matrix_path.resolve()
    if not matrix.is_relative_to(round_path):
        raise ValueError(f"Selected matrix escapes round directory: {matrix}")
    if not matrix.is_file():
        raise FileNotFoundError(f"Selected matrix must be an existing file: {matrix}")
    matrix_hash, original_matrix_hash = sha256(matrix), sha256(original_matrix)
    rows = load_matrix(matrix)
    matrix_source_kind = "ORIGINAL_MATRIX" if matrix == original_matrix else "DIAGNOSTIC_TERMINAL_OVERLAY"
    if matrix != original_matrix:
        identity = ("model", "method", "seed", "resolution", "record_path")
        original_rows = load_matrix(original_matrix)
        if [tuple(row.get(key) for key in identity) for row in rows] != [
            tuple(row.get(key) for key in identity) for row in original_rows
        ]:
            raise ValueError("Selected matrix must retain all original planned row identities in order")
    if any(str(row.get("status")) in ACTIVE_STATUSES for row in rows):
        raise ValueError("Round has active or unscheduled rows; wait until the run is terminal")
    for row in rows:
        for key in ("model", "method", "seed", "resolution", "status", "record_path"):
            if key not in row:
                raise ValueError(f"Matrix row missing {key}")
        round_file(round_path, str(row["record_path"]))
    before_files = set().union(*(source_pngs(round_file(round_path, str(row["record_path"])).parent)
                                 for row in rows))
    output.mkdir(parents=True, exist_ok=False)
    cases = [inspect_case(index, row, round_path, output) for index, row in enumerate(rows)]
    channel_contacts = channel_contact_sheets(cases, output)
    checked_files = {texture.source_path: texture.sha256 for case in cases
                     for source in case.sources for texture in source.files}
    changed_files: list[str] = []
    for path, expected_hash in checked_files.items():
        try:
            if sha256(Path(path)) != expected_hash:
                changed_files.append(path)
        except OSError:
            changed_files.append(path)
    after_files = set().union(*(source_pngs(round_file(round_path, str(row["record_path"])).parent)
                                for row in rows))
    matrix_unchanged = sha256(matrix) == matrix_hash
    original_matrix_unchanged = sha256(original_matrix) == original_matrix_hash
    unchanged = matrix_unchanged and original_matrix_unchanged and before_files == after_files and not changed_files
    manifest: dict[str, object] = {
        "schema_version": 1,
        "round": str(round_path), "matrix": str(matrix), "matrix_sha256": matrix_hash,
        "selected_matrix": str(matrix), "selected_matrix_sha256": matrix_hash,
        "original_matrix": str(original_matrix), "original_matrix_sha256": original_matrix_hash,
        "matrix_source_kind": matrix_source_kind,
        "qualification": (
            "ABORTED_DIAGNOSTIC_ONLY_NOT_COMPLETE_FORMAL_COMPARISON"
            if matrix_source_kind == "DIAGNOSTIC_TERMINAL_OVERLAY"
            else "TEXTURE_INVENTORY_ONLY_NOT_ACCEPTANCE_EVIDENCE"
        ),
        "matrix_link": link_to(matrix, output), "planned_rows": len(cases),
        "preview_policy": {"seed": PREVIEW_SEED, "selection": "all model/method/resolution rows at seed17",
                           "quality_based_selection": False, "thumbnail_max_size": [256, 256]},
        "channel_contacts": channel_contacts,
        "source_selection_policy": "case/textures first; then lexicographic final_validation/candidate_*/textures; "
                                   "then lexicographic baseline_validation/candidate_*/textures (diagnostic only); "
                                   "first complete decoded mip0 triplet; otherwise first source with physical PNGs",
        "availability_counts": dict(Counter(case.texture_availability for case in cases)),
        "source_integrity": {"unchanged": unchanged, "matrix_unchanged": matrix_unchanged,
                             "selected_matrix_unchanged": matrix_unchanged,
                             "original_matrix_unchanged": original_matrix_unchanged,
                             "png_file_set_unchanged": before_files == after_files,
                             "png_files_checked": len(checked_files), "changed_files": changed_files},
        "rows": [asdict(case) for case in cases],
    }
    (output / "texture_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
    )
    (output / "gallery.md").write_text(
        markdown_gallery(cases, link_to(matrix, output), link_to(original_matrix, output), matrix_source_kind,
                         channel_contacts),
        encoding="utf-8",
    )
    if not unchanged:
        raise RuntimeError("Source changed while building gallery; inspect texture_manifest.json source_integrity")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--round", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--matrix", type=Path, help="Optional terminal status overlay inside the round directory")
    args = parser.parse_args()
    result = build_gallery(args.round, args.output, args.matrix)
    print(json.dumps({key: value for key, value in result.items() if key != "rows"}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
