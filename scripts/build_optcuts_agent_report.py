"""Package the fixed-geometry OptCuts/Agent trial without rerunning UV tools."""

from __future__ import annotations

import argparse
import csv
import shutil
import time
from pathlib import Path
from typing import cast

import numpy as np
from PIL import Image, ImageDraw

from reseam.agent_tools.state import file_hash, load_snapshot, read_object, write_json
from scripts.build_chloe_hard_surface_report import paste_image
from scripts.build_legacy_reseam_report import font

ROOT = Path(__file__).resolve().parents[1]


def display_value(row: dict[str, object], key: str, percent: bool = False) -> str:
    item = row[key]
    return "未测" if item is None else f"{float(str(item))*100:.2f}%" if percent else f"{float(str(item)):.3f}"


def check_completion(root: Path) -> None:
    """Refuse a live/partial matrix instead of silently making it look complete."""
    expected = {(method, seed) for method in ("optcuts", "agent_merge") for seed in (17, 29, 43)}
    for asset in ("tripo", "dragon"):
        folder = root / asset
        rows = cast(list[dict[str, object]], read_object(folder / "evaluations.json")["rows"])
        keys = [(str(row["method"]), int(str(row["seed"]))) for row in rows]
        pending = [str(path.parent) for path in folder.glob("**/command.json")
                   if (path.parent / "provenance.json").is_file() and (path.parent / "request.json").is_file()
                   and not (path.parent / "result.json").is_file()]
        if len(keys) != len(expected) or set(keys) != expected or pending:
            raise ValueError(f"EXPERIMENT_NOT_COMPLETE:{asset}:rows={keys}:pending={pending}")


def build(root: Path, output: Path, report: Path) -> None:
    started = time.monotonic()
    check_completion(root)
    if output.exists():
        raise ValueError("REPORT_OUTPUT_MUST_BE_NEW")
    output.mkdir(parents=True)
    frozen = cast(dict[str, str], read_object(root / "code_freeze.json")["sha256"])
    own = read_object(root / "agent_script.json")
    frozen[str(own["path"])] = str(own["sha256"])
    for name, digest in frozen.items():
        path = Path(name)
        if file_hash(path) != digest:
            raise ValueError("SOURCE_CHANGED_BEFORE_ARCHIVE:" + name)
        destination = output / "frozen_code" / path.relative_to(ROOT)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, destination)
    provenance = []
    for path in root.glob("*/**/provenance.json"):
        sources = cast(dict[str, str], read_object(path)["source_sha256"])
        mismatches = [name for name, digest in sources.items() if name in frozen and frozen[name] != digest]
        provenance.append({"path": str(path), "mismatches": mismatches, "passed": not mismatches})
    if any(not row["passed"] for row in provenance):
        raise ValueError("MIXED_KERNEL_CODE")
    for name in ("code_freeze.json", "agent_script.json"):
        shutil.copy2(root / name, output / name)
    rows: list[dict[str, object]] = []
    timing: dict[str, object] = {}
    failures: list[dict[str, object]] = []
    integrity: list[dict[str, object]] = []
    text = ["# OptCuts 提缝 → Agent 合岛与整理：Tripo 刺模型和历史镜像龙", "",
        "流程收敛为一个现有Skill入口：OptCuts生成初始UV及切缝，Agent看图决定保留、删缝或局部重展开，再调用已有排布、烘焙与导出工具；本轮没有增加registry工具或另建Skill。`[opinion]`", "",
        "两个资产分别固定原几何与颜色目标，256×256、4像素gap/border/padding、统一密度规则，正式排布种子17/29/43；OptCuts初始化seed=0且不输入旧UV。数值质量与Agent外观判断分列。`[verified: 对应 initial/optcuts/request.json、evaluate/*/seed*/pack/request.json、target/freeze/request.json]`", "",
        "本轮只检验静态UV＋BaseColor，LOD 0/1/2；写出完整9级mip不表示全部LOD通过。normal/AO可供选边，本轮不重烘焙这些材质通道，也没有PBR、GPU或人工验收。`[verified: 对应quality/request.json与export/result.json；opinion: 范围界定]`", ""]
    for asset, title in (("tripo", "Tripo 424面带刺匕首"), ("dragon", "历史11338面固定镜像龙")):
        folder = root / asset
        state = read_object(folder / "state.json")
        source = load_snapshot(Path(str(state["source"])))
        evaluations = cast(list[dict[str, object]], read_object(folder / "evaluations.json")["rows"])
        canvas = Image.new("RGB", (960, 1080), (246, 248, 251))
        draw = ImageDraw.Draw(canvas)
        draw.text((16, 12), title + " · 256² / seed 17", font=font(26), fill=(25, 45, 60))
        draw.text((16, 50), "冻结目标 → OptCuts无共享 → Agent候选；外观与数值分列", font=font(19), fill=(45, 65, 80))
        paste_image(canvas, folder / "target/views/oblique.png", (8, 130, 312, 450))
        draw.text((16, 90), "原始颜色 / 冻结目标", font=font(22), fill=(25, 45, 60))
        for column, method in enumerate(("optcuts", "agent_merge"), 1):
            row = next((r for r in evaluations if r["method"] == method and r["seed"] == 17), None)
            if row is None or "texture" not in row:
                continue
            left = column * 320 + 8
            label = "OptCuts 无共享" if method == "optcuts" else "Agent真实合岛" if asset == "tripo" else "Agent左右共享整理"
            draw.text((left, 90), label, font=font(21), fill=(25, 45, 60))
            base = folder / "evaluate" / method / "seed17"
            paste_image(canvas, base / "basecolor/oblique.png", (left, 130, left+304, 450))
            paste_image(canvas, base / "groups/uv.png", (left, 470, left+304, 750))
            paste_image(canvas, Path(str(row["texture"])), (left+24, 770, left+280, 1026))
            passed = cast(dict[str, object], row.get("quality", {})).get("quality_accepted")
            draw.text((left, 1030), "数值通过" if passed else "数值拒绝 · diagnostic", font=font(18), fill=(135, 40, 45))
        canvas.save(output / f"{asset}_comparison.png")
        text.extend([f"## {title}", "", f"![实际模型、UV和贴图](../assets/optcuts-agent-uv/{output.name}/{asset}_comparison.png)", ""])
        if asset == "tripo":
            text.extend(["OptCuts为19岛。Agent先试相邻岛10/12，保留其它开口，只删共同原边377，局部LSCM重展开到18岛；局部最大拉伸1.261。三种子最佳覆盖45.17%→43.87%，因此最终保留OptCuts。第二处候选也到18岛，但最佳覆盖43.84%，在排布筛查后停止，未烘焙/导出。`[verified: tripo/merge1/final_decision.json、state.json merge_attempts、merge0/packing_comparison.json、final_decision.json]`", "",
                "覆盖下降本身不是外观质量证明；此处没有观察到足以补偿覆盖损失的局部外观改善。保留真实合岛候选作诊断，不宣称Agent优于OptCuts。`[opinion]`", ""])
        else:
            text.extend(["固定原边拓扑有80个连通分量，OptCuts已经80岛，没有可直接沿原边删缝的相邻岛；这只是当前几何/身份约束下的结论。Agent改做左右部件堆叠与排布，不把这种复用叫真实合岛。`[verified: dragon/initial/component_audit.json；opinion: 结论范围]`", "",
                f"40组左右候选实际接受{state.get('accepted_groups', 0)}组；被拒绝组保留独占，失败原因及耗时全部保留。`[verified: dragon/state.json accepted_groups；dragon/calls.json organize/*]`", ""])
            text.extend(["历史交付已经是80岛/40图块，本轮OptCuts重新初始化后只有37组通过，不替换旧交付。旧结果是2048图且密度/留边不同，只列历史计数，不能与本轮256图的覆盖和画质混排。`[verified: dragon-midplane-cleanup.md 修复后的UV与外观表、排布说明；opinion: 比较边界]`", ""])
            text.extend(["Agent斜视对照中未见共享后新增的大片错色，身体、白肚皮与爪子保留；两条256图路径的眼部细节都比冻结目标模糊，只接受为流程诊断预览。`[verified: dragon/appearance_review.json evidence；opinion: 有限视角外观判断]`", ""])
        text.extend(["|路径 / 种子|连通岛 / 独立图块|P95 / 最大拉伸|覆盖 / 实际共享表面|数值 / 回载|实际GLB|", "|---|---|---|---|---|---|"])
        for row in evaluations:
            numeric: dict[str, object] = {"asset": asset, "method": row["method"], "seed": row["seed"]}
            if "snapshot" not in row:
                rows.append({**numeric, "status": "NOT_COMPLETED", "packing": row.get("packing")})
                text.append(f"|{row['method']} / {row['seed']}|未完成|未测|未测|未测|保留失败 `[verified: {asset}/evaluations.json]`|")
                continue
            candidate = load_snapshot(Path(str(row["snapshot"])))
            vertices_equal = np.array_equal(source.state.surface.mesh.vertices, candidate.state.surface.mesh.vertices)
            faces_equal = np.array_equal(source.state.surface.mesh.faces, candidate.state.surface.mesh.faces)
            if not vertices_equal or not faces_equal:
                raise ValueError("GEOMETRY_CHANGED:" + str(row["snapshot"]))
            inspection = cast(dict[str, object], row.get("inspection", {}))
            uv = read_object(Path(str(inspection["metrics_report"]))) if "metrics_report" in inspection else {}
            measured = cast(dict[str, object], row["metrics"])
            numeric.update({k: measured[k] for k in ("charts", "anisotropy_median", "anisotropy_p95", "anisotropy_max", "seam_length_normalized")})
            numeric.update(independent_tiles=uv.get("actual_independent_tiles"), unique_coverage=inspection.get("unique_footprint"),
                actual_shared_surface_fraction=cast(dict[str, object], uv.get("global", {})).get("actual_shared_surface_fraction"),
                above_two_faces=measured["anisotropy_above_two_faces"], quality_accepted=cast(dict[str, object], row.get("quality", {})).get("quality_accepted"),
                reimport_passed=cast(dict[str, object], row.get("reimport", {})).get("passed"), snapshot=row["snapshot"])
            numeric["phase_status"] = {phase: {key: cast(dict[str, object], row.get(phase, {})).get(key)
                for key in ("status", "reason")} for phase in ("packing", "inspection", "bake", "quality", "export")}
            destination = output / "models" / asset / str(row["method"]) / f"seed{row['seed']}"
            exported = cast(dict[str, object], row.get("export", {})).get("asset")
            if exported:
                destination.mkdir(parents=True)
                shutil.copy2(Path(str(exported)), destination / "asset.glb")
                for path in Path(str(row["texture"])).parent.glob("BaseColor_mip*.png"):
                    shutil.copy2(path, destination / path.name)
                write_json(destination / "reimport.json", row["reimport"])
                numeric.update(asset_glb=str(destination / "asset.glb"), mip_count=len(list(destination.glob("BaseColor_mip*.png"))))
            rows.append(numeric)
            integrity.append({"asset": asset, "method": row["method"], "seed": row["seed"], "vertices_equal": vertices_equal,
                "faces_equal": faces_equal, "reimport": row.get("reimport"), "snapshot": row["snapshot"]})
            link = ("../assets/optcuts-agent-uv/" + output.name + "/models/" + asset + "/" + str(row["method"]) + f"/seed{row['seed']}/asset.glb")
            model_link = f"[模型]({link})" if exported else "未导出；状态见summary.phase_status"
            label = "OptCuts" if row["method"] == "optcuts" else "真实合岛候选" if asset == "tripo" else "左右共享整理"
            text.append(f"|{label} / {row['seed']}|{numeric['charts']} / {numeric['independent_tiles']}|"
                f"{display_value(numeric, 'anisotropy_p95')} / {display_value(numeric, 'anisotropy_max')}|{display_value(numeric, 'unique_coverage', True)} / {display_value(numeric, 'actual_shared_surface_fraction', True)}|"
                f"{numeric['quality_accepted']} / {numeric['reimport_passed']}|{model_link} `[verified: {asset}/evaluations.json 对应行；inspect/uv_metrics.json]`|")
        text.append("")
        calls = cast(list[dict[str, object]], read_object(folder / "calls.json")["calls"])
        failures.extend({"asset": asset, **row} for row in calls if cast(dict[str, object], row["result"]).get("status") not in ("OK", "SUCCESS"))
        labels = {str(row["label"]) for row in calls}
        missing = [str(p.parent.relative_to(folder)).replace("\\", "/") for p in folder.glob("**/result.json")
                   if str(p.parent.relative_to(folder)).replace("\\", "/") not in labels]
        timing[asset] = {"tool_seconds": sum(float(str(row["seconds"])) for row in calls),
            "known_direct_seconds": read_object(folder / "direct_timing.json")["seconds"],
            "call_count": len(calls), "unledgered_results": missing, "budget_seconds": 2400,
            "scope": "tool time includes failures; one-shot orchestration/Agent image review duration not fully instrumented; not exact end-to-end budget certification"}
        commands = list(folder.glob("**/command.json"))
        cast(dict[str, object], timing[asset])["observed_wall_span_seconds"] = time.time() - min(p.stat().st_ctime for p in commands)
        cast(dict[str, object], timing[asset])["wall_span_scope"] = "Windows original command creation time to report; includes intervening programming, review and idle time, not only compute"
        evidence = output / "evidence" / asset
        evidence.mkdir(parents=True)
        for name in ("state.json", "calls.json", "evaluations.json", "direct_timing.json", "final_decision.json", "appearance_review.json"):
            if (folder / name).exists():
                shutil.copy2(folder / name, evidence / name)
    report_seconds = time.monotonic() - started
    write_json(output / "summary.json", {"rows": rows, "timing": timing, "report_seconds": report_seconds,
        "report_code_sha256": file_hash(Path(__file__)),
        "source_root": str(root.resolve()), "provenance": provenance, "geometry_integrity": integrity,
        "full_pbr_passed": False, "gpu_verified": False, "human_accepted": False})
    write_json(output / "failures.json", {"calls": failures})
    with (output / "measurements.csv").open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(dict.fromkeys(k for row in rows for k in row)))
        writer.writeheader()
        writer.writerows(rows)
    text.extend(["## 证据与限制", "", "完整中位数/P95/最大拉伸、超限面ID、接缝长度、覆盖、实际共享表面、耗时见 [summary.json](../assets/optcuts-agent-uv/" + output.name + "/summary.json)，失败见 [failures.json](../assets/optcuts-agent-uv/" + output.name + "/failures.json)。原始请求、状态哈希、提案与棋盘格在不可变实验目录。`[verified: 对应文件]`", "",
        "所有成功导出均重新读取GLB核对几何、UV和内嵌BaseColor；固定几何按顶点/原面数组逐项比较，并保留快照geometry_hash作追溯。旧实验只作来源与历史参考，未混入本轮数值排名。`[verified: summary.json geometry_integrity；opinion: 对照规则]`", "",
        "每种几何使用2400秒工具预算，每次工具至多300秒。工具账包含失败；单次编排的外层时间未完整测量，另外记录原始命令文件创建至报告的墙钟跨度，包含期间编程、看图与等待，不能把工具耗时冒充精确端到端耗时。`[verified: frozen_code/scripts/run_chloe_hard_surface.py Trial._call_serial；summary.json timing]`", ""])
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text("\n".join(text), encoding="utf-8")
    print({"rows": len(rows), "exports": len(list((output / "models").glob("**/asset.glb"))), "report_seconds": report_seconds}, flush=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    build(args.root, args.output, args.report)


if __name__ == "__main__":
    main()
