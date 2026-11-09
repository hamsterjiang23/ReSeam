"""Read-only packaging of the user-selected Tripo 424-face experiment."""

from __future__ import annotations

import argparse
import csv
import shutil
import time
from pathlib import Path
from typing import cast

from PIL import Image, ImageDraw

from reseam.agent_tools.state import file_hash, read_object, write_json
from scripts.build_chloe_hard_surface_report import LABELS, flatten_row, paste_image
from scripts.build_legacy_reseam_report import font

ROOT = Path(__file__).resolve().parents[1]


def build(root: Path, output: Path, report: Path) -> None:
    started = time.monotonic()
    if output.exists():
        raise ValueError("TRIPO_REPORT_OUTPUT_MUST_BE_NEW")
    output.mkdir(parents=True)
    folder = root / "original"
    rows = cast(list[dict[str, object]], read_object(folder / "evaluations.json")["rows"])
    numeric = [flatten_row(root, {**row, "geometry": "original"}) for row in rows]
    for item in numeric:
        item["geometry"] = "tripo_424_fixed"
    channels = read_object(folder / "prepare/channel_sources.json")
    cue = read_object(folder / "prepare/projected_normal_cues.json")
    cut = read_object(folder / "candidates/cut_decision.json")
    with (output / "measurements.csv").open("w", newline="", encoding="utf-8-sig") as stream:
        fields = list(dict.fromkeys(key for row in numeric for key in row))
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(numeric)
    frozen = cast(dict[str, str], read_object(root / "code_freeze.json")["sha256"])
    extra = read_object(root / "tripo_script.json")
    frozen[str(extra["path"])] = str(extra["sha256"])
    audits = []
    for path in folder.rglob("provenance.json"):
        sources = cast(dict[str, str], read_object(path)["source_sha256"])
        mismatches = [name for name, digest in sources.items() if name in frozen and frozen[name] != digest]
        audits.append({"path": str(path), "mismatches": mismatches, "passed": not mismatches})
    if any(not row["passed"] for row in audits):
        raise ValueError("TRIPO_MIXED_TOOL_CODE")
    for name, digest in frozen.items():
        path = Path(name)
        if file_hash(path) != digest:
            raise ValueError("TRIPO_FROZEN_SOURCE_CHANGED:" + name)
        destination = output / "frozen_code" / path.relative_to(ROOT)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, destination)
    methods = ["reference", "optcuts_unshared", "hard_unshared", "hard_shared"]
    canvas = Image.new("RGB", (1280, 1080), (246, 248, 251))
    draw = ImageDraw.Draw(canvas)
    draw.text((18, 12), "Tripo 带刺匕首 · 固定已有424面低模 · UV＋BaseColor", font=font(25), fill=(26, 42, 58))
    draw.text((18, 52), "真实 normal map 投射线索 + 几何 AO；法线未作为最终PBR通道交付", font=font(17), fill=(60, 75, 90))
    for i, method in enumerate(methods):
        left = i * 320 + 8
        draw.text((left+4, 90), LABELS[method], font=font(19), fill=(26, 42, 58))
        row = next((row for row in rows if row["method"] == method and row["seed"] == 17), None)
        if method == "reference":
            paste_image(canvas, folder / "prepare/source_views/oblique.png", (left, 125, left+304, 425))
            paste_image(canvas, Path(str(cast(dict[str, object], channels["normal"])["original_png"])),
                        (left+24, 470, left+280, 726))
            draw.text((left+8, 750), "原材质 normal map（线索来源）", font=font(16), fill=(35, 55, 75))
            continue
        if row is None or "texture" not in row:
            draw.text((left+5, 160), str(row.get("status", "NOT_RUN") if row else "NOT_RUN"), font=font(14), fill=(150, 40, 50))
            continue
        base = folder / "evaluate" / str(row["key"])
        clean = folder / "review" / (method + "_clean_views/oblique.png")
        paste_image(canvas, clean if clean.is_file() else base / "views_basecolor/oblique.png", (left, 125, left+304, 425))
        paste_image(canvas, base / "views_groups/uv.png", (left, 445, left+304, 725))
        paste_image(canvas, Path(str(row["texture"])), (left+24, 745, left+280, 1001))
        metrics = cast(dict[str, object], row["metrics"])
        summary = f"{metrics['charts']} 岛 · P95 {float(str(metrics['anisotropy_p95'])):.2f}"
        summary += "\n数值通过" if cast(dict[str, object], row.get("quality", {})).get("quality_accepted") else "\n数值拒绝 / diagnostic"
        draw.text((left+5, 1012), summary, font=font(16), fill=(35, 55, 75))
    canvas.save(output / "comparison.png")
    calls = cast(list[dict[str, object]], read_object(folder / "calls.json")["calls"])
    timing = {"tool_seconds": sum(float(str(row["seconds"])) for row in calls),
        "direct_seconds": read_object(folder / "direct_timing.json")["seconds"],
        "report_seconds": time.monotonic()-started, "budget_seconds": 2400,
        "scope": "machine execution; Agent image-review time not measured"}
    summary_record = {"rows": numeric, "channel_sources": channels,
        "normal_cue_edges": len(cast(list[object], cue["edges"])),
        "valid_normal_cue_edges": sum(bool(row["valid"]) for row in cast(list[dict[str, object]], cue["edges"])),
        "projection_normalization": cue["projection_normalization"], "cut_decision": cut,
        "cue_edge_nominees": len(cast(list[int], cut["added_cue_edges"])),
        "actual_additional_cut_edges": sorted(set(cast(list[int], cut["cut_edges"])) - set(cast(list[int], cut["base_openings"]))),
        "cut_field_note": "legacy added_cue_edges contains nominees including pre-existing cuts; actual_additional_cut_edges is the true set difference",
        "timing": timing, "provenance": audits, "report_code_sha256": file_hash(Path(__file__)),
        "full_pbr_passed": False, "gpu_verified": False, "human_accepted": False}
    write_json(output / "summary.json", summary_record)
    write_json(output / "failures.json", {"calls": [row for row in calls if cast(dict[str, object], row["result"]).get("status") != "OK"]})
    text = ["# Tripo 带刺匕首：normal 与 AO 辅助硬边展开", "",
        "按用户选择使用已有424面重拓扑版本，固定其几何；原1061面版本因非流形边(708,709)被导入器拒绝，失败保留。原始约191万面高模未运行。`[verified: round01/original/prepare/import/result.json；round03/original/prepare/channel_sources.json]`", "",
        "材质来自冻结的1061面GLB，含真实normalTexture；BaseColor及normal线索均按固定424面低模包围盒对角线的0.08距离界投射到原参考三角形。保存源面、重心坐标、距离和有效性。AO来自16射线几何查询，AO贴图与height/displacement未绑定。`[verified: prepare/channel_sources.json；projected_normal_cues.json；target/freeze/target.json]`", "",
        "normal用于候选切口判断，不是最终法线重烘焙；切线框架近似不代表Mikk/PBR验收。记录G通道翻转以补偿glTF与内部UV的V方向差异。保留OptCuts开口，加入强normal变化或AO+折角线索，再尝试LSCM与minimum-stretch。阈值是启发式，不是理论最优。`[opinion]`", "",
        f"![实际结果](../assets/tripo-spike-hard-surface/{output.name}/comparison.png)", "",
        "|路径 / 种子|岛 / 图块|拉伸P95 / 最大|实际共享表面比例|数值 / GLB回读|", "|---|---|---|---|---|"]
    for item in numeric:
        def show(value: object) -> str:
            return f"{value:.3f}" if isinstance(value, float) else "未测" if value is None else str(value)

        text.append(f"|{LABELS[str(item['method'])]} / {item['seed']}|{show(item['charts'])} / {show(item['independent_tiles'])}|"
            f"{show(item['anisotropy_p95'])} / {show(item['anisotropy_max'])}|{show(item['actual_shared_surface_fraction'])}|"
            f"{show(item['quality_accepted'])} / {show(item['reimport_passed'])} "
            "`[verified: 对应 inspect/uv_metrics.json、quality/validation.json、reimport.json]`|")
    review = folder / "appearance_review.json"
    if review.exists():
        text.extend(["", str(read_object(review)["conclusion"]) + "`[verified: original/appearance_review.json]`"])
    text.extend(["", "诊断边线图见 original/review/normal_cue_edges 与 ao_cue_edges，实际贴图清洁预览见各 *_clean_views。cut_decision.json旧字段added_cue_edges记录候选边，真正新增边为cut_edges减base_openings；不混算为新增收益。`[verified: cue_selection_summary.json；cut_decision.json；summary.json actual_additional_cut_edges]`"])
    if (folder / "ledger_reconciliation.json").exists():
        text.extend(["", "两次诊断视图的并发记账遗漏按原result.json的wall_seconds补入，原账本和补录依据保留；未重算模型或指标，缺失的外层记账开销仍标未知。`[verified: original/ledger_reconciliation.json]`"])
    text.extend(["", (f"[9项完整矩阵](../assets/tripo-spike-hard-surface/{output.name}/summary.json) · "
        f"[原始证据和实际模型]({root.as_posix()})"), "",
        "round01保留1061面导入拒绝；round02保留投射尺度混用的开发结果，不用于正式排名；round03统一尺度后重跑全部受影响路径。三种子分别从同一方法父快照排布。LOD为0/1/2；人工、GPU与完整PBR均未验收。`[verified: 各轮冻结源码、请求与validation.json profile]`", ""])
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text("\n".join(text), encoding="utf-8")
    print({"rows": len(rows), "report": str(report), "timing": timing}, flush=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    build(args.root.resolve(), args.output.resolve(), args.report.resolve())


if __name__ == "__main__":
    main()
