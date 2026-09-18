"""Read frozen evidence and create a separate, non-overwriting delivery summary."""

import argparse
import json
from collections import Counter
from pathlib import Path
from statistics import median
from typing import Any


def read(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def number(value: float | None, digits: int = 5) -> str:
    return "—" if value is None else f"{value:.{digits}f}"


def summarize(source: Path, output: Path) -> None:
    ledger = read(source / "ledger.json")
    config = read(source / "config.json")
    version = read(source / "source.json")
    output.mkdir(parents=True, exist_ok=False)
    overview: dict[str, Any] = {
        "round": ledger["round_id"],
        "source_directory": str(source),
        "source_commit": version["git_head"],
        "source_dirty": version["dirty"],
        "geometry_status_counts": dict(Counter(r["status"] for r in ledger["runs"])),
        "downstream_status_counts": dict(Counter(r["status"] for r in ledger["downstream"])),
        "execution_complete": ledger["execution_complete"],
        "artist_assessment": ledger["artist_assessment"],
        "seconds": ledger.get("seconds"),
        "budget_seconds": config["total_seconds"],
        "audits": ledger["audits"],
        "models": {},
    }
    for model in config["models"]:
        records = [r for r in ledger["runs"] if r["model"] == model]
        by_method: dict[str, Any] = {}
        lines = [
            f"# {model} / {ledger['round_id']}",
            "",
            f"源码 `{version['git_head']}`；每次重新初始化，总预算 {config['run_seconds']} 秒；种子 {config['seeds']}。",
            "",
            "本页只汇总本轮原始证据。SUCCESS 表示该配置通过自动门禁，不表示已达到 artist 交付标准。",
            "",
            "## 几何配置",
            "",
            "| 方法 | 有效/计划 | 接缝长度中位数 | 岛数中位数 | 平均SD中位数 | 全部运行耗时中位数 |",
            "|---|---|---|---|---|---|",
        ]
        for method in config["methods"]:
            rows = [r for r in records if r["method"] == method]
            valid = [r for r in rows if r["status"] == "SUCCESS"]
            times = [r["wall_seconds"] for r in rows if r.get("wall_seconds") is not None]
            quality_medians = {
                key: median(r["quality"][key] for r in valid) if valid else None
                for key in (
                    "seam_length_normalized",
                    "weighted_seam_length",
                    "charts",
                    "sd_mean",
                    "sd_p95",
                    "sd_max",
                )
            }
            rejection_counts: Counter[str] = Counter()
            support_counts: Counter[str] = Counter()
            accepted, attempted = 0, 0
            for row in rows:
                log = source / row["path"] / "search/candidates.jsonl"
                if not log.exists():
                    continue
                for text in log.read_text(encoding="utf-8").splitlines():
                    item = json.loads(text)
                    attempted += 1
                    accepted += int(item["accepted"])
                    rejection_counts[item["status"]] += 1
                    for stage in item.get("supports", []):
                        support_counts[stage["status"]] += 1
            data = {
                "planned": len(rows),
                "successful": len(valid),
                "statuses": dict(Counter(r["status"] for r in rows)),
                "valid_output_medians": quality_medians,
                "all_run_seconds_median": median(times) if times else None,
                "attempted_candidates": attempted,
                "accepted_candidates": accepted,
                "candidate_statuses": dict(rejection_counts),
                "support_statuses": dict(support_counts),
            }
            by_method[method] = data
            lines.append(
                f"| {method} | {len(valid)}/{len(rows)} | {number(quality_medians['seam_length_normalized'])} "
                f"| {number(quality_medians['charts'], 0)} | {number(quality_medians['sd_mean'])} "
                f"| {number(data['all_run_seconds_median'], 2)} s |"
            )
        lines += [
            "",
            "只对有效输出汇总质量，失败率单独保留；不能只按幸存样本的中位数宣布方法获胜。",
            "低碎片与 artist 配置改变了目标，不能将目标变化归因于搜索机制；所有指标原值在 result.json。",
            "",
            "## 预定下游组合",
            "",
            "| 方法 | 状态 | Blender MAE | 优化后 MAE | 原因 |",
            "|---|---|---|---|---|",
        ]
        downstream = []
        for row in ledger["downstream"]:
            if row["model"] != model:
                continue
            evidence = source / row["path"] / "result.json"
            data = read(evidence) if evidence.exists() else {}
            downstream.append({**row, "metrics": data})
            reason = str(row.get("reason") or row.get("upstream_status") or "").replace("|", "\\|")
            lines.append(
                f"| {row['method']} | {row['status']} | {number(data.get('blender_mae'))} "
                f"| {number(data.get('differentiable_mae'))} | {reason} |"
            )
        lines += [
            "",
            "下游仅预定种子17；diagnostic_only 输出不计作通过 UV 质量门禁。",
            "法线视图、mip、接缝带及投射探针原始指标收录于 delivery.json；四个面内探针不等于逐 texel 投射验证。",
            "",
            "## 支持域审计",
            "",
        ]
        audit_path = source / "support_audit" / model / "result.json"
        audit = read(audit_path) if audit_path.exists() else None
        if audit:
            lines += [
                f"共同候选 {audit['audited_count']}/{audit['pool_size_requested']}；小范围失败 {audit['small_failure_count']}；大范围救回且改善 {audit['rescued_and_improving_count']}。",
                f"缺失候选 {audit['missing_candidates']}；审计耗时 {audit['seconds']:.2f} 秒。完整搜索轨迹会分叉，不能代替共同起点诊断。",
            ]
        else:
            lines.append("审计结果缺失，不能声称已验证支持域机制。")
        lines += [
            "",
            "## 人工验收边界",
            "",
            "待真人确认语义区域、完成匿名限时修整和重烘焙检查。未经确认的建议只改变软权重。",
            "两套固定模型是反复使用的开发与回归集，不代表未见模型泛化。",
        ]
        overview["models"][model] = {"methods": by_method, "downstream": downstream, "audit": audit}
        (output / f"{model}.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (output / "delivery.json").write_text(
        json.dumps(overview, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8"
    )
    print(json.dumps({key: value for key, value in overview.items() if key != "models"}, ensure_ascii=False))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    summarize(args.source.resolve(), args.output.resolve())


if __name__ == "__main__":
    main()
