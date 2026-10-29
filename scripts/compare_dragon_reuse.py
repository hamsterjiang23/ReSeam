"""Replay frozen UV states under identical color protocols; no simulated LLM choices."""

import argparse
import json
from pathlib import Path

from reseam.agent_tools.experiment import Experiment, snapshot_metrics, write_step_report
from reseam.agent_tools.state import file_hash, load_snapshot, read_object, write_json


def _obj(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        raise TypeError("OBJECT_REQUIRED")
    return value


def input_manifest(plan: dict[str, object], plan_path: Path) -> dict[str, str]:
    """Pin states, targets, original sources and preparation evidence, not path strings."""
    files = {plan_path.resolve()}
    identities: set[tuple[str, str]] = set()
    for entry in _obj(plan["paths"]).values():
        path = _obj(entry)
        source_snapshot = path.get("snapshot") or path.get("last_valid_snapshot")
        if not isinstance(source_snapshot, str):
            continue
        snapshot = load_snapshot(Path(source_snapshot))
        files.update((snapshot.root / "snapshot.json", snapshot.root / "arrays.npz"))
        source = Path(str(snapshot.metadata["reference_glb"]))
        identities.add((snapshot.asset_id, file_hash(source)))
        files.add(source)
        source_records = snapshot.metadata.get("source_manifest", [])
        if not isinstance(source_records, list):
            raise TypeError("SOURCE_MANIFEST_LIST_REQUIRED")
        for record in source_records:
            if isinstance(record, dict) and "path" in record:
                files.add(Path(str(record["path"])))
        receipts = path.get("setup_receipts", [])
        if not isinstance(receipts, list):
            raise TypeError("SETUP_RECEIPTS_LIST_REQUIRED")
        files.update(Path(str(receipt)) for receipt in receipts)
    if len(identities) != 1:
        raise ValueError("COMPARISON_REQUIRES_SAME_ASSET_AND_ORIGINAL_MATERIAL")
    for value in _obj(plan["targets"]).values():
        if value is not None:
            target = Path(str(value))
            if plan.get("require_original_topology") is True and not read_object(target).get("original_topology"):
                raise ValueError("FORMAL_SOURCE_SEAM_ANCESTRY_REQUIRED")
            files.update((target, target.parent / "target.npz"))
        elif plan.get("require_original_topology") is True:
            raise ValueError("FORMAL_FROZEN_TARGET_REQUIRED")
    return {str(path.resolve()): file_hash(path) for path in sorted(files)}


def verify_inputs(manifest: dict[str, str]) -> None:
    if any(not Path(path).is_file() or file_hash(Path(path)) != digest for path, digest in manifest.items()):
        raise ValueError("FROZEN_INPUT_OR_TARGET_CHANGED_START_NEW_ROUND")


def preparation_cost(entry: dict[str, object]) -> dict[str, object]:
    receipts = entry.get("setup_receipts", [])
    if not isinstance(receipts, list) or not receipts:
        return {"available": False, "seconds": None, "note": "missing preparation receipts; replay cost only"}
    seconds = 0.
    for value in receipts:
        record = read_object(Path(str(value)))
        seconds += float(str(record.get("wall_seconds_including_runner", record.get("wall_seconds", 0))))
    return {"available": True, "seconds": seconds, "receipt_count": len(set(map(str, receipts))),
            "token_cost": entry.get("token_cost", "host not separately available"),
            "allocation": "one setup per method; do not multiply by target resolutions"}


def report(output: Path, rows: list[dict[str, object]]) -> None:
    lines = ["# 整龙 UV 与颜色复用对照", "",
             "验收范围为 BaseColor；完整PBR、GPU与人工验收未通过本报告认定。`[verified: protocol.json scope]`", "",
             "|目标|路径|尺寸|连通岛|独立图块|活动共享组|状态|颜色通过|交付成功|完整mip字节|包体字节|证据|",
             "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for row in rows:
        metrics = _obj(row.get("snapshot_metrics", {}))
        label = str(row["label"])
        geometry = _obj(row.get("geometry", {}))
        lines.append(f"|{row['target']}|{row['method']}|{row['resolution']}|{metrics.get('chart_count','—')}"
                     f"|{geometry.get('packing_groups','—')}|{geometry.get('active_reuse_groups','—')}"
                     f"|{row['status']}|{row.get('quality_accepted',False)}|{row.get('delivery_succeeded',False)}"
                     f"|{row.get('payload_bytes','—')}|{row.get('package_bytes','—')}"
                     f"|[记录](rows/{label}.json) `[verified: rows/{label}.json]`|")
    lines += ["", "## 已验收最小存储", ""]
    for target, method in sorted({(str(row["target"]), str(row["method"])) for row in rows}):
        accepted = [row for row in rows if row["target"] == target and row["method"] == method
                    and row.get("quality_accepted") is True and row.get("delivery_succeeded") is True
                    and type(row.get("payload_bytes")) is int]
        best = min(accepted, key=lambda row: int(str(row["payload_bytes"]))) if accepted else None
        text = f"{best['payload_bytes']} 字节，{best['resolution']}²" if best else "无已验收结果，最小存储未知"
        lines.append(f"- {target}/{method}：{text}。`[verified: matrix.json]`")
    lines += ["", "## 实际产物", ""]
    for row in rows:
        label = str(row["label"])
        bake = _obj(row.get("bake", {}))
        exported = _obj(row.get("export", {}))
        if "bake" in bake:
            root = Path(str(bake["bake"])).parent
            images = sorted(root.rglob("*mip0.png"))
            for path in images:
                lines += [f"### {label}", "", f"![实际颜色贴图]({path.relative_to(output).as_posix()})", ""]
        if "asset" in exported:
            path = Path(str(exported["asset"]))
            lines.append(f"[预览模型]({path.relative_to(output).as_posix()}) `[verified: {label}_export/result.json]`")
    lines += ["", "[每一步的输入、理由、前后变化与失败](steps.md)。`[verified: steps.json]`", ""]
    (output / "report.md").write_text("\n".join(lines), encoding="utf-8")


def record_row(experiment: Experiment, row: dict[str, object], rows: list[dict[str, object]]) -> None:
    output = experiment.output
    write_json(output / "rows" / f"{row['label']}.json", row)
    write_json(output / "matrix.json", rows)
    write_json(output / "summary.json", experiment.summary())
    write_step_report(output)
    report(output, rows)
    print(json.dumps({"label": row["label"], "status": row["status"], "payload_bytes": row.get("payload_bytes")}), flush=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    plan = read_object(args.plan)
    paths, targets = _obj(plan["paths"]), _obj(plan["targets"])
    resolutions = plan.get("resolutions", [512, 1024, 2048, 4096, 8192])
    if not isinstance(resolutions, list) or any(type(size) is not int for size in resolutions):
        raise ValueError("INTEGER_RESOLUTIONS_REQUIRED")
    profile = _obj(plan.get("geometry_profile", {}))
    pinned_inputs = input_manifest(plan, args.plan)
    root = Path(__file__).resolve().parents[1]
    protocol = {**plan, "scope": "basecolor_reuse", "mip_levels": [0, 1, 2],
                "plan_file": str(args.plan.resolve()), "plan_sha256": file_hash(args.plan),
                "same_geometry_required": True, "artist_reviewed": False, "gpu_verified": False,
                "full_pbr_passed": False, "minimum_claim": "accepted enumerated candidates only"}
    protocol["input_sha256"] = pinned_inputs
    protocol["preparation_costs"] = {name: preparation_cost(_obj(entry)) for name, entry in paths.items()}
    experiment = Experiment(root, args.output, protocol)
    output = experiment.output
    rows: list[dict[str, object]] = []
    for target in targets:
        for method in paths:
            for size in resolutions:
                rows.append({"label": f"{target}_{method}_{size}", "target": target, "method": method,
                             "resolution": size, "status": "NOT_RUN", "quality_accepted": False})
    geometry_ids = {snapshot_metrics(str(_obj(path)["snapshot"]))["geometry_hash"] for path in paths.values()
                    if isinstance(_obj(path).get("snapshot"), str)}
    if len(geometry_ids) != 1:
        raise ValueError("CROSS_TOPOLOGY_COMPARISON_FORBIDDEN")
    write_json(output / "matrix.json", rows)
    for row in rows:
        verify_inputs(pinned_inputs)
        label = str(row["label"])
        path = _obj(paths[str(row["method"])])
        if path.get("setup_status", "READY") != "READY" or not isinstance(path.get("snapshot"), str):
            row.update(status="SETUP_" + str(path.get("setup_status", "MISSING_SNAPSHOT")), setup=path)
            record_row(experiment, row, rows)
            continue
        current = str(path["snapshot"])
        size = row["resolution"]
        packed = experiment.call("uv.pack", {"snapshot": current, "profile": {**profile, "resolution": size}},
                                 label + "_pack", reason="统一尺寸与间距，独立从冻结路径状态开始")
        row["pack"] = packed
        if "report" in packed:
            geometry = read_object(Path(str(packed["report"])))
            row["geometry"] = {key: value for key, value in geometry.items()
                               if not isinstance(value, list) and key != "geometry_protocol"}
        if "snapshot" not in packed:
            row.update(status="PACK_REJECTED", snapshot_metrics=snapshot_metrics(current))
        else:
            current = str(packed["snapshot"])
            row.update(snapshot=current, snapshot_metrics=snapshot_metrics(current))
            request: dict[str, object] = {"snapshot": current, "mode": "basecolor_reuse", "resolution": size,
                                         "padding_pixels": plan.get("padding_pixels", 8), "timeout_seconds": 300}
            target = targets[str(row["target"])]
            if target is not None:
                request["target"] = target
            baked = experiment.call("material.bake", request, label + "_bake",
                                    reason="从冻结目标重建实际颜色贴图，保存完整mip")
            row["bake"] = baked
            row["payload_bytes"] = baked.get("payload_bytes")
            if "bake" in baked:
                row["package_bytes"] = read_object(Path(str(baked["bake"]))).get("package_bytes")
            row["status"] = "BAKE_FAILED"
            if "bake" in baked:
                validated = experiment.call("quality.validate", {"snapshot": current, "bake": baked["bake"],
                    "mip_levels": [0, 1, 2], "geometry_profile": {**profile, "resolution": size},
                    "thresholds": plan.get("color_thresholds", {}), "timeout_seconds": 300},
                    label + "_validate", reason="同协议独立验收颜色、几何、mip和渲染")
                row["validation"] = validated
                if "validation" in validated:
                    evidence = read_object(Path(str(validated["validation"])))
                    row["quality_metrics"] = {key: evidence.get(key) for key in ("material", "render", "seams")}
                row["quality_accepted"] = validated.get("quality_accepted") is True
                row["status"] = ("ACCEPTED_BASECOLOR" if row["quality_accepted"] else "QUALITY_REJECTED") if (
                    "validation" in validated) else "VALIDATION_" + str(validated.get("status", "FAILED"))
                if "validation" in validated:
                    row["export"] = experiment.call("asset.export", {"snapshot": current, "bake": baked["bake"],
                        "validation": validated["validation"], "mode": "accepted" if row["quality_accepted"] else "diagnostic"},
                        label + "_export", reason="导出实际贴图和颜色预览，完整PBR状态保持未验收")
                    exported = _obj(row["export"])
                    delivered = exported.get("status") == "OK" and "asset" in exported and "manifest" in exported
                    if delivered:
                        manifest_path = Path(str(exported["manifest"]))
                        manifest = read_object(manifest_path)
                        artifacts = _obj(manifest.get("files", {}))
                        delivered = bool(artifacts) and all((manifest_path.parent / key).is_file()
                            and file_hash(manifest_path.parent / key) == digest for key, digest in artifacts.items())
                    row["delivery_succeeded"] = delivered
                    if not delivered:
                        row["status"] = "EXPORT_FAILED"
        verify_inputs(pinned_inputs)
        record_row(experiment, row, rows)
    verify_inputs(pinned_inputs)
    write_json(output / "summary.json", {**experiment.summary(), "inputs_unchanged": True, "accepted_rows": sum(
        row["quality_accepted"] is True for row in rows), "planned_rows": len(rows), "completed_rows": sum(
        row["status"] != "NOT_RUN" for row in rows)})


if __name__ == "__main__":
    main()
