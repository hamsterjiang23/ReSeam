"""Fresh finite-budget CLI tool comparison; Agent selections are supplied, never simulated."""

import argparse
import time
from dataclasses import asdict
from pathlib import Path
from typing import cast

from reseam.agent_tools.runner import run_tool
from reseam.agent_tools.state import load_snapshot, read_object, write_json
from reseam.budget_material import MaterialThresholds


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--agent-proposals", type=Path, required=True)
    parser.add_argument("--resolutions", type=int, nargs="+", default=[128, 256])
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    proposals = read_object(args.agent_proposals)
    protocol: dict[str, object] = {
        "purpose": "Agent tool integration comparison; not the full solver benchmark",
        "assets": ["energy_blade", "dragon"], "paths": ["baseline", "geometry", "agent"],
        "resolutions": args.resolutions, "candidate_attempts_per_path": 3,
        "material_thresholds": asdict(MaterialThresholds()), "mip_levels": [0, 1, 2],
        "render_size": 48, "padding_pixels": 4, "timeout_seconds_per_call": 300,
        "agent_proposals": proposals, "geometry_policy": "first three ranked chart candidates",
        "agent_policy": "human-visible calling-Agent selections supplied before comparison",
        "best_policy": "minimum actual payload only among quality-accepted shared states; no monotonic search",
        "claims": {"global_optimum": False, "artist_reviewed": False, "gpu_verified": False},
    }
    write_json(output / "protocol.json", protocol)
    calls: list[dict[str, object]] = []
    rows: list[dict[str, object]] = []
    write_json(output / "matrix.json", rows)

    def call(tool: str, request: dict[str, object], label: str) -> dict[str, object]:
        request_path = output / "requests" / f"{label}.json"
        write_json(request_path, request)
        result = run_tool(tool, request_path, output / label)
        calls.append({"tool": tool, "label": label, "result": result})
        write_json(output / "calls.json", calls)
        print(label, result.get("status"), result.get("reason", ""), flush=True)
        return result

    for asset in ("energy_blade", "dragon"):
        imported = call("asset.import", {"path": str(Path(f"assets/v1/{asset}/low.obj").resolve()),
            "reference": str(Path(f"assets/v1/{asset}/high.glb").resolve()), "initialize_uv": "xatlas",
            "repair_degenerate_faces": True}, f"{asset}_import")
        if "snapshot" not in imported:
            rows.extend({"asset": asset, "method": method, "resolution": resolution,
                         "status": "IMPORT_FAILED", "result": imported}
                        for resolution in args.resolutions for method in ("baseline", "geometry", "agent"))
            write_json(output / "matrix.json", rows)
            continue
        source = str(imported["snapshot"])
        call("uv.inspect", {"snapshot": source}, f"{asset}_inspect")
        call("view.render", {"snapshot": source}, f"{asset}_original_views")
        found = call("reuse.find_candidates", {"snapshot": source, "max_candidates": 12}, f"{asset}_candidates")
        candidates = read_object(Path(str(found["report"]))).get("candidates", []) if "report" in found else []
        assert isinstance(candidates, list)
        selected = proposals.get(asset, [])
        assert isinstance(selected, list)
        for resolution in args.resolutions:
            baseline: dict[str, object] = {}
            for method in ("baseline", "geometry", "agent"):
                start = time.monotonic()
                label = f"{asset}_{method}_{resolution}"
                packed = call("uv.pack", {"snapshot": source, "profile": {"resolution": resolution}}, label + "_pack")
                if "snapshot" not in packed:
                    rows.append({"asset": asset, "method": method, "resolution": resolution,
                                 "status": "PACK_REJECTED", "result": packed})
                    write_json(output / "matrix.json", rows)
                    continue
                current = str(packed["snapshot"])
                attempts = candidates[:3] if method == "geometry" else selected[:3] if method == "agent" else []
                accepted_actions = 0
                for index, candidate in enumerate(attempts):
                    assert isinstance(candidate, dict)
                    request = dict(candidate.get("check_request", candidate))
                    request.update(snapshot=current, profile={"resolution": resolution})
                    request.pop("state_hash", None)
                    checked = call("reuse.check_pair", request, label + f"_check_{index}")
                    if "proposal" in checked:
                        applied = call("reuse.apply", {"snapshot": current, "proposal": checked["proposal"],
                                       "profile": {"resolution": resolution}}, label + f"_apply_{index}")
                        if "snapshot" in applied:
                            current = str(applied["snapshot"])
                            accepted_actions += 1
                call("view.render", {"snapshot": current}, label + "_views")
                bake = call("material.bake", {"snapshot": current, "resolution": resolution}, label + "_bake")
                row: dict[str, object] = {"asset": asset, "method": method, "resolution": resolution,
                    "candidate_discovery_status": found.get("status"),
                    "snapshot": current, "accepted_actions": accepted_actions,
                    "shared_groups": len(load_snapshot(Path(current)).state.groups), "bake": bake,
                    "shared_candidate_obtained": accepted_actions > 0 if method != "baseline" else None}
                if "bake" in bake:
                    validation = call("quality.validate", {"snapshot": current, "bake": bake["bake"],
                        "thresholds": protocol["material_thresholds"], "mip_levels": [0, 1, 2],
                        "render_size": 48}, label + "_quality")
                    row["validation"] = validation
                    row["quality_accepted"] = validation.get("quality_accepted", False)
                    row["payload_bytes"] = bake["payload_bytes"]
                    row["bake_file_bytes"] = read_object(Path(str(bake["bake"])))["file_bytes"]
                    export_request: dict[str, object] = {"snapshot": current, "bake": bake["bake"],
                        "mode": "accepted" if row["quality_accepted"] else "diagnostic"}
                    if "validation" in validation:
                        export_request["validation"] = validation["validation"]
                    if baseline and method != "baseline":
                        export_request.update(baseline)
                    row["export"] = call("asset.export", export_request, label + "_export")
                    if method == "baseline" and "validation" in validation:
                        baseline = {"baseline_bake": bake["bake"], "baseline_validation": validation["validation"]}
                row["wall_seconds"] = time.monotonic() - start
                rows.append(row)
                write_json(output / "matrix.json", rows)
    lines = ["# Agent UV 工具集真实闭环对照", "",
        "本轮是固定两模型、有限预算的工具集验证；质量规则先锁定，未执行 GPU 或艺术验收。`[verified: protocol.json claims]`", "",
        "|模型|路径|尺寸|共享组|质量通过|纹理完整 mip 字节|调用耗时秒|", "|---|---|---|---|---|---|---|"]
    for row in rows:
        lines.append(f"|{row['asset']}|{row.get('method')}|{row.get('resolution')}|{row.get('shared_groups')}|"
                     f"{row.get('quality_accepted', False)}|{row.get('payload_bytes')}|{row.get('wall_seconds', 0):.2f} `[verified: matrix.json]`|")
    lines += ["", "字节为三通道 RGBA8 的完整 mip payload；PNG 包体另列于 matrix.json。未使用 BC/ASTC 编码，不能将估算块存储当本轮结果。`[verified: protocol.json；各 bake.json metadata]`",
              "", "共享未得到合法候选时保留无共享状态作诊断，shared_candidate_obtained=false；不能算作复用收益。`[verified: matrix.json shared_candidate_obtained]`", "",
              "## 已验收最小存储", ""]
    for asset in ("energy_blade", "dragon"):
        for method in ("baseline", "geometry", "agent"):
            valid = [int(str(r["payload_bytes"])) for r in rows if r.get("asset") == asset and r.get("method") == method
                     and r.get("quality_accepted") is True and (method == "baseline" or r.get("shared_candidate_obtained"))]
            lines.append(f"- {asset}/{method}: {min(valid) if valid else '无已验收结果；最小字节未知'}。`[verified: matrix.json]`")
    lines += ["", "## 实际产物", ""]
    for row in rows:
        if "payload_bytes" not in row:
            continue
        label = f"{row['asset']}_{row['method']}_{row['resolution']}"
        lines += [f"### {label}", "", f"[导出与逐项验收]({label}_export/report.md) · [质量证据]({label}_quality/validation.json)", "",
                  f"![UV]({label}_views/uv.png)", f"![实际 BaseColor]({label}_bake/texture/BaseColor_mip0.png)",
                  f"![实际 Normal]({label}_bake/texture/Normal_mip0.png)", f"![实际 ORM]({label}_bake/texture/ORM_mip0.png)", ""]
    seconds = sum(float(str(cast(dict[str, object], c['result']).get('wall_seconds', 0))) for c in calls)
    lines += [f"全部调用 {len(calls)} 次，总 wall time {seconds:.2f} 秒；包括失败，详见 calls.json。`[verified: calls.json]`", ""]
    (output / "report.md").write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    main()
