"""Package actual Chloe hard-surface outputs without solving or rebaking."""

from __future__ import annotations

import argparse
import csv
import shutil
import time
from pathlib import Path
from typing import cast

from PIL import Image, ImageDraw

from reseam.agent_tools.state import file_hash, load_snapshot, read_object, write_json
from reseam.agent_tools.views import _texture
from scripts.build_legacy_reseam_report import font

ROOT = Path(__file__).resolve().parents[1]
LABELS = {"reference": "原始 UV / 固定几何参考", "optcuts_unshared": "OptCuts 无共享",
          "hard_unshared": "硬边展开 无共享", "hard_shared": "硬边展开 复用", "harmonic_shared": "harmonic 复用对照"}


def flatten_row(root: Path, row: dict[str, object]) -> dict[str, object]:
    metrics = cast(dict[str, object], row.get("metrics", {}))
    inspection = cast(dict[str, object], row.get("inspection", {}))
    quality = cast(dict[str, object], row.get("quality", {}))
    result: dict[str, object] = {k: row.get(k) for k in ("geometry", "method", "seed", "status", "snapshot", "texture")}
    result.update({k: metrics.get(k) for k in ("charts", "anisotropy_median", "anisotropy_p95", "anisotropy_max",
                                             "seam_length_normalized", "geometry_hash")})
    result["above_two_face_count"] = len(cast(list[int], metrics.get("anisotropy_above_two_faces", [])))
    uv = read_object(Path(str(inspection["metrics_report"]))) if "metrics_report" in inspection else {}
    result["independent_tiles"] = uv.get("actual_independent_tiles")
    result["actual_shared_surface_fraction"] = cast(dict[str, object], uv.get("global", {})).get("actual_shared_surface_fraction")
    result["quality_accepted"] = quality.get("quality_accepted")
    result["shared_groups"] = inspection.get("shared_group_count")
    result["unique_coverage"] = inspection.get("unique_footprint")
    result["reimport_passed"] = cast(dict[str, object], row.get("reimport", {})).get("passed")
    if isinstance(row.get("texture"), str):
        texture = Path(str(row["texture"]))
        mips = list(texture.parent.glob("BaseColor_mip*.png"))
        result["png_full_mip_file_bytes"] = sum(path.stat().st_size for path in mips)
        runtime = 0
        for path in mips:
            with Image.open(path) as image:
                runtime += image.width * image.height * 4
        result["rgba8_full_mip_runtime_bytes"] = runtime
        result["written_mip_count"] = len(mips)
    exported = cast(dict[str, object], row.get("export", {})).get("asset")
    result["glb_bytes"] = Path(str(exported)).stat().st_size if exported else None
    result["evidence"] = str(root / str(row["geometry"]) / "evaluate" / str(row["key"]))
    return result


def paste_image(canvas: Image.Image, path: Path, box: tuple[int, int, int, int]) -> None:
    if not path.is_file():
        return
    with Image.open(path) as image:
        tile = image.convert("RGB")
        tile.thumbnail((box[2] - box[0], box[3] - box[1]), Image.Resampling.LANCZOS)
        canvas.paste(tile, (box[0] + (box[2]-box[0]-tile.width)//2, box[1] + (box[3]-box[1]-tile.height)//2))


def figure(root: Path, geometry: str, state: dict[str, object], rows: list[dict[str, object]], output: Path) -> None:
    methods = ["reference", "optcuts_unshared", "hard_unshared", "hard_shared"]
    if geometry == "mirror":
        methods.append("harmonic_shared")
    canvas = Image.new("RGB", (320 * len(methods), 1080), (246, 248, 251))
    draw = ImageDraw.Draw(canvas)
    draw.text((18, 12), f"Chloe {'原始模型' if geometry == 'original' else '固定镜像模型'} · UV＋BaseColor", font=font(24), fill=(26, 42, 58))
    draw.text((18, 48), "256² · seed 17 · 外观与数值分列；不按岛数排名", font=font(17), fill=(60, 75, 90))
    for i, method in enumerate(methods):
        left = i * 320 + 8
        draw.text((left + 4, 88), LABELS[method], font=font(19), fill=(26, 42, 58))
        row = next((row for row in rows if row["method"] == method and row["seed"] == 17), None)
        if method == "reference":
            color = root / geometry / "prepare/source_views/oblique.png"
            uv = root / geometry / "prepare/source_views/uv.png"
            pixels, _, _, _, _ = _texture({}, load_snapshot(Path(str(state["source"]))))
            texture = output / (geometry + "_reference_texture.png")
            Image.fromarray(pixels).save(texture)
            summary = "原始布局参考；未重新烘焙"
        elif row is None or "texture" not in row:
            draw.text((left+5, 150), str(row.get("status", "NOT_RUN") if row else "NOT_RUN"), font=font(13), fill=(150, 40, 50))
            continue
        else:
            base = root / geometry / "evaluate" / str(row["key"])
            color = base / "views_basecolor/oblique.png"
            uv = base / "views_groups/uv.png"
            texture = Path(str(row["texture"]))
            metrics = cast(dict[str, object], row["metrics"])
            summary = f"{metrics['charts']} 岛 · P95 {float(str(metrics['anisotropy_p95'])):.2f}"
            summary += "\n数值通过" if cast(dict[str, object], row.get("quality", {})).get("quality_accepted") else "\n数值拒绝 / diagnostic"
        paste_image(canvas, color, (left, 125, left+304, 420))
        paste_image(canvas, uv, (left, 445, left+304, 720))
        paste_image(canvas, texture, (left+24, 745, left+280, 1001))
        draw.text((left+6, 1012), summary, font=font(16), fill=(35, 55, 75))
    canvas.save(output / (geometry + "_comparison.png"))


def build(root: Path, output: Path, report: Path) -> None:
    started = time.monotonic()
    if output.exists():
        raise ValueError("REPORT_OUTPUT_MUST_BE_NEW")
    output.mkdir(parents=True)
    frozen = cast(dict[str, str], read_object(root / "code_freeze.json")["sha256"])
    provenance_audits: list[dict[str, object]] = []
    for path in root.glob("*/**/provenance.json"):
        sources = cast(dict[str, str], read_object(path)["source_sha256"])
        mismatches = [name for name, digest in sources.items() if name in frozen and frozen[name] != digest]
        provenance_audits.append({"path": str(path), "mismatches": mismatches, "passed": not mismatches})
    if any(not row["passed"] for row in provenance_audits):
        raise ValueError("MIXED_TOOL_CODE_PROVENANCE")
    # Archive the executed source, not a later working-tree revision.
    for name, digest in frozen.items():
        path = Path(name)
        if file_hash(path) != digest:
            raise ValueError("FROZEN_SOURCE_CHANGED_BEFORE_ARCHIVE:" + name)
        destination = output / "frozen_code" / path.relative_to(ROOT)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, destination)
    summary: list[dict[str, object]] = []
    text = ["# Chloe 硬边展开与 UV 复用对照", "",
        "原始与镜像几何分别固定；外观优先、数值辅助，岛数增加不作为失败。`[opinion]`", "",
        "原始模型分支使用几何AO辅助侧面连接排序；镜像分支的折角路线已成功，AO仅计算供诊断。两者读取导入自定义逐角法线与原多边形身份；本资产没有绑定normal/height/AO贴图。静态BaseColor不代表Unity动态材质或完整PBR。`[verified: original/candidates/polygon_fan_plan/hard_edges.json surface_cue_sources；mirror/state.json hard_choice]`", "",
        "先比较30/45/60度折角候选；保留失败后改用有限原多边形侧面片及dual-tree开口。AO仅影响相邻侧面连接优先级，不是强制断缝。45度/最多8多边形均为启发式。`[opinion]`", ""]
    for geometry in ("original", "mirror"):
        state = read_object(root / geometry / "state.json")
        path = root / geometry / "evaluations.json"
        rows = cast(list[dict[str, object]], read_object(path)["rows"]) if path.exists() else []
        figure(root, geometry, state, rows, output)
        text.extend([f"## {'原始450面模型' if geometry == 'original' else '固定590面镜像模型'}", "",
            f"![实际模型、UV、贴图](../assets/chloe-hard-surface/{output.name}/{geometry}_comparison.png)", "",
            "|路径 / 种子|岛 / 图块|拉伸 P95 / 最大|实际共享表面|数值质量 / 回读|证据|", "|---|---|---|---|---|---|"])
        for row in rows:
            numeric = flatten_row(root, row)
            summary.append(numeric)
            def show(value: object) -> str:
                return f"{value:.3f}" if isinstance(value, float) else "未测" if value is None else str(value)

            text.append(f"|{LABELS.get(str(row['method']), str(row['method']))} / {row['seed']}|"
                f"{show(numeric['charts'])} / {show(numeric['independent_tiles'])}|"
                f"{show(numeric['anisotropy_p95'])} / {show(numeric['anisotropy_max'])}|"
                f"{show(numeric['actual_shared_surface_fraction'])}|{show(numeric['quality_accepted'])} / "
                f"{show(numeric['reimport_passed'])}|[实际产物]({Path(str(numeric['evidence'])).as_posix()}) "
                "`[verified: 对应 inspect/uv_metrics.json、quality/validation.json、reimport.json]`|")
        text.append("")
        review = root / geometry / "appearance_review.json"
        if review.exists():
            text.extend(["Agent 外观判断：" + str(read_object(review)["conclusion"]) +
                         "`[verified: " + str(review) + "]`", ""])
    with (output / "measurements.csv").open("w", newline="", encoding="utf-8-sig") as stream:
        fields = list(dict.fromkeys(key for row in summary for key in row)) if summary else ["status"]
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(summary)
    calls: list[dict[str, object]] = []
    timing: dict[str, object] = {}
    for geometry in ("original", "mirror"):
        records = cast(list[dict[str, object]], read_object(root / geometry / "calls.json")["calls"])
        calls.extend({"geometry": geometry, **row} for row in records)
        tool_seconds = sum(float(str(row["seconds"])) for row in records)
        direct = float(str(read_object(root / geometry / "direct_timing.json")["seconds"]))
        timing[geometry] = {"tool_seconds": tool_seconds, "direct_seconds": direct,
            "report_seconds": (time.monotonic() - started) / 2,
            "budget_seconds": 2400, "scope": "machine execution; Agent image review time not measured"}
    write_json(output / "summary.json", {"rows": summary, "timing": timing, "provenance": provenance_audits,
        "report_code_sha256": file_hash(Path(__file__)), "source_root": str(root),
        "full_pbr_passed": False, "gpu_verified": False, "human_accepted": False})
    write_json(output / "failures.json", {"calls": [row for row in calls if cast(dict[str, object], row["result"]).get("status") not in ("OK", "SUCCESS")]})
    text.extend(["## 完整证据", "",
        (f"[完整矩阵及数值](../assets/chloe-hard-surface/{output.name}/summary.json) · "
        f"[CSV](../assets/chloe-hard-surface/{output.name}/measurements.csv) · "
        f"[全部失败](../assets/chloe-hard-surface/{output.name}/failures.json) · "
        f"[请求、纹理、mip、模型和逐步记录]({root.as_posix()})"), "",
        "拉伸为逐面Jacobian奇异值之比，统计不按面积加权；共享率统计组内双方的实际正面积交叠。覆盖率、图块及岛数各自报告，不能由共享率推断同质量存储节省。`[verified: metrics.anisotropy_definition；inspect/uv_metrics.json measurement_method]`", "",
        "开发轮dev01/dev02/dev03保留；正式轮使用冻结源码和新烘焙，三种子独立从同一方法父快照排布。原始既存UV冲突只作参考。验收LOD为0/1/2；完整mip文件不代表其它LOD通过。`[verified: code_freeze.json；各 request.json 与 validation.json profile]`", ""])
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text("\n".join(text), encoding="utf-8")
    print({"rows": len(summary), "report": str(report), "timing": timing}, flush=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    build(args.root.resolve(), args.output.resolve(), args.report.resolve())


if __name__ == "__main__":
    main()
