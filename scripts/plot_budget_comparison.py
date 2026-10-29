"""Plot raw quality versus actual texture bytes; rejected rows remain visible."""

import argparse
import hashlib
import json
from pathlib import Path
from typing import cast

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np


def metric(row: dict[str, object], channel: str) -> float | None:
    material = row.get("material")
    if not isinstance(material, dict):
        return None
    channels = material.get("channels")
    if not isinstance(channels, dict) or not isinstance(channels.get(channel), dict):
        return None
    value = channels[channel].get("area_weighted_mean")
    return float(value) if isinstance(value, (int, float)) else None


def integer(row: dict[str, object], key: str) -> int:
    value = row.get(key)
    if type(value) is not int:
        raise ValueError(f"Expected integer matrix field: {key}")
    return value


def plot_round(round_path: Path, output: Path) -> dict[str, object]:
    if output.exists():
        raise FileExistsError(output)
    matrix = round_path / "matrix.json"
    raw = json.loads(matrix.read_text(encoding="utf-8"))
    if not isinstance(raw, list) or not all(isinstance(row, dict) for row in raw):
        raise ValueError("Expected the complete planned matrix list")
    rows = cast(list[dict[str, object]], raw)
    models = list(dict.fromkeys(str(row["model"]) for row in rows))
    methods = list(dict.fromkeys(str(row["method"]) for row in rows))
    palette = plt.get_cmap("tab10")
    if len(methods) > palette.N:
        raise ValueError("More methods than available tab10 colors")
    colors = {method: palette(index) for index, method in enumerate(methods)}
    figure, axes = plt.subplots(len(models), 3, figsize=(13, 3.4 * len(models)), squeeze=False)
    labels = (("color", "Base Color linear RGB L2"),
              ("normal", "World normal angle (degrees)"), ("orm", "ORM max-component error"))
    plotted, omitted = 0, 0
    for mi, model in enumerate(models):
        for ci, (channel, label) in enumerate(labels):
            axis = axes[mi, ci]
            for method in methods:
                selected = [row for row in rows if row["model"] == model and row["method"] == method]
                xs, ys = [], []
                for size in sorted({integer(row, "resolution") for row in selected}):
                    group = [row for row in selected if row["resolution"] == size]
                    values = [(row, metric(row, channel)) for row in group]
                    available = [(row, value) for row, value in values
                                 if value is not None and type(row.get("payload_bytes")) is int]
                    omitted += len(group) - len(available)
                    if not available:
                        continue
                    x = integer(available[0][0], "payload_bytes") / 1048576
                    y = float(np.median([value for _, value in available]))
                    xs.append(x)
                    ys.append(y)
                    valid = len(available) == len(group) and all(row.get("valid") is True for row, _ in available)
                    axis.scatter(x, y, color=colors[method], marker="o" if valid else "x", s=40, zorder=3)
                    plotted += len(available)
                if xs:
                    axis.plot(xs, ys, color=colors[method], label=method, linewidth=1.4)
            axis.set_title(f"{model}: {label}", fontsize=10)
            axis.set_xlabel("Complete 3-channel texture payload (MiB)")
            axis.set_ylabel("Area-weighted raw error")
            axis.set_xlim(left=0)
            axis.set_ylim(bottom=0)
            axis.grid(alpha=.2)
            if axis.lines:
                axis.legend(fontsize=8)
    figure.suptitle("Raw quality / storage: x = not fully validated; o = all contributing rows validated", fontsize=11)
    figure.text(.5, .015, "Points show medians of available seed rows, not confidence intervals. "
                "Full acceptance also requires every face's tails, raster checks, views and budget.",
                ha="center", fontsize=8)
    figure.tight_layout(rect=(0, .045, 1, .95))
    output.mkdir(parents=True, exist_ok=False)
    figure.savefig(output / "quality_storage.png", dpi=180)
    figure.savefig(output / "quality_storage.svg")
    plt.close(figure)
    result: dict[str, object] = {
        "matrix": str(matrix.resolve()), "matrix_sha256": hashlib.sha256(matrix.read_bytes()).hexdigest(),
        "planned_rows": len(rows), "plotted_channel_rows": plotted,
        "missing_channel_rows_not_plotted": omitted, "matplotlib": matplotlib.__version__,
        "seed_aggregation": "median, not an uncertainty estimate",
        "qualification": "Only valid=true rows are fully accepted; x markers include every other status",
    }
    (output / "plot_metadata.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--round", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(plot_round(args.round.resolve(), args.output.resolve()), indent=2))


if __name__ == "__main__":
    main()
