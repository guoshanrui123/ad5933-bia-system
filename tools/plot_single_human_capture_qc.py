"""Create publication-ready QC figures for one 5-minute human capture."""

from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path

import matplotlib.pyplot as plt
from matplotlib import font_manager
import numpy as np

import hospital_calibration_core as calibration
import human_qc_core


REPORT_FREQUENCIES = (5_000, 10_000, 15_000, 20_000, 25_000)
FORMAL_FREQUENCIES = (10_000, 15_000, 20_000)
COLORS = {
    "selected": "#0072B2",
    "valid": "#7F8C8D",
    "rejected": "#D55E00",
    "threshold": "#CC0000",
}


def configure_plot_style() -> None:
    available = {item.name for item in font_manager.fontManager.ttflist}
    family = next(
        (name for name in ("Microsoft YaHei", "SimHei", "Noto Sans CJK SC", "Arial Unicode MS")
         if name in available),
        "DejaVu Sans",
    )
    plt.rcParams.update({
        "font.family": family,
        "axes.unicode_minus": False,
        "font.size": 9,
        "axes.titlesize": 11,
        "axes.labelsize": 9,
        "legend.fontsize": 8,
        "axes.linewidth": 0.8,
        "pdf.fonttype": 42,
    })


def read_decisions(path: Path) -> dict[int, str]:
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        return {int(row["sweep_id"]): row["decision"] for row in csv.DictReader(stream)}


def read_sweep_times(path: Path) -> dict[int, int]:
    result: dict[int, int] = {}
    with path.open("r", encoding="utf-8", errors="replace") as stream:
        for line in stream:
            fields = line.strip().split(",")
            if len(fields) < 5 or fields[0] != "data":
                continue
            try:
                timestamp_ms = int(fields[1])
                sweep_id = int(fields[2])
            except ValueError:
                continue
            result.setdefault(sweep_id, timestamp_ms)
    return result


def trimmed_mean(values: np.ndarray, proportion: float = 0.10) -> np.ndarray:
    ordered = np.sort(np.asarray(values), axis=0)
    cut = int(math.floor(len(ordered) * proportion))
    kept = ordered[cut:len(ordered) - cut] if cut else ordered
    return np.mean(kept, axis=0)


def cv_pct(values: np.ndarray, axis: int = 0) -> np.ndarray:
    magnitude = np.abs(values)
    return np.std(magnitude, axis=axis, ddof=1) / np.mean(magnitude, axis=axis) * 100.0


def save_figure(fig: plt.Figure, stem: Path) -> None:
    fig.savefig(stem.with_suffix(".png"), dpi=600, bbox_inches="tight", facecolor="white")
    fig.savefig(stem.with_suffix(".pdf"), bbox_inches="tight", facecolor="white")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("--qc-dir", type=Path, required=True)
    parser.add_argument("--calibration-dir", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()

    args.out.mkdir(parents=True, exist_ok=True)
    report = json.loads((args.qc_dir / "qc_report.json").read_text(encoding="utf-8"))
    decisions = read_decisions(args.qc_dir / "sweep_decisions.csv")
    sweeps = human_qc_core.parse_complete_sweeps(args.input)
    selected_ids = [int(value) for value in report["selected_valid_sweep_ids"]]
    rejected_ids = [int(value) for value in report["rejected_sweep_ids"]]
    all_ids = sorted(sweeps)
    valid_unselected_ids = [
        sweep_id for sweep_id in all_ids
        if sweep_id not in selected_ids and sweep_id not in rejected_ids
    ]
    frequencies = np.asarray(REPORT_FREQUENCIES)
    raw_all = np.asarray([[sweeps[sweep_id][int(f)] for f in frequencies] for sweep_id in all_ids])
    raw_selected = np.asarray([[sweeps[sweep_id][int(f)] for f in frequencies] for sweep_id in selected_ids])
    raw_rejected = np.asarray([[sweeps[sweep_id][int(f)] for f in frequencies] for sweep_id in rejected_ids])

    times = read_sweep_times(args.input)
    first_timestamp = min(times[sweep_id] for sweep_id in all_ids)
    elapsed_minutes = np.asarray([(times[sweep_id] - first_timestamp) / 60_000 for sweep_id in all_ids])
    band_level = np.exp(np.mean(np.log(np.maximum(np.abs(raw_all), 1e-12)), axis=1))
    index_by_id = {sweep_id: index for index, sweep_id in enumerate(all_ids)}

    configure_plot_style()
    fig, axes = plt.subplots(2, 2, figsize=(12.2, 8.4), constrained_layout=True)

    ax = axes[0, 0]
    ax.plot(elapsed_minutes, band_level, color="#B8C2CC", linewidth=1.0, zorder=1)
    for ids, color, label, marker in (
        (selected_ids, COLORS["selected"], f"选入的{len(selected_ids)}轮", "o"),
        (valid_unselected_ids, COLORS["valid"], "有效但未选入", "o"),
        (rejected_ids, COLORS["rejected"], "异常剔除", "X"),
    ):
        indices = [index_by_id[value] for value in ids]
        if indices:
            ax.scatter(elapsed_minutes[indices], band_level[indices], s=28, color=color,
                       marker=marker, label=label, zorder=3, edgecolors="white", linewidths=0.4)
    ax.set_title("A  5–25 kHz整轮幅值随时间变化")
    ax.set_xlabel("采集时间（min）")
    ax.set_ylabel("频带几何平均幅值（AD5933原始计数）")
    ax.legend(frameon=False, loc="best")
    ax.grid(axis="y", color="#E5E7EB", linewidth=0.6)

    ax = axes[0, 1]
    for row in np.abs(raw_selected):
        ax.plot(frequencies / 1000, row, color=COLORS["selected"], alpha=0.12, linewidth=0.8)
    for row in np.abs(raw_rejected):
        ax.plot(frequencies / 1000, row, color=COLORS["rejected"], alpha=0.55, linewidth=1.0)
    selected_trimmed = trimmed_mean(np.abs(raw_selected))
    ax.plot(frequencies / 1000, selected_trimmed, color=COLORS["selected"], linewidth=2.5,
            marker="o", label=f"选入{len(selected_ids)}轮的10%截尾均值")
    if len(raw_rejected):
        ax.plot([], [], color=COLORS["rejected"], linewidth=1.5, label="异常轮")
    ax.set_title("B  原始频谱及异常轮")
    ax.set_xlabel("频率（kHz）")
    ax.set_ylabel("幅值（AD5933原始计数）")
    ax.set_xticks(frequencies / 1000)
    ax.legend(frameon=False, loc="best")
    ax.grid(color="#E5E7EB", linewidth=0.6)

    ax = axes[1, 0]
    raw_cv = cv_pct(raw_all)
    clean_cv = cv_pct(raw_selected)
    x = np.arange(len(frequencies))
    width = 0.34
    ax.bar(x - width / 2, raw_cv, width, color="#AAB2BD", label=f"清洗前（{len(all_ids)}轮）")
    ax.bar(x + width / 2, clean_cv, width, color=COLORS["selected"],
           label=f"清洗后（{len(selected_ids)}轮）")
    ax.axhline(5.0, color=COLORS["threshold"], linestyle="--", linewidth=1.2, label="CV阈值 5%")
    for xi, value in zip(x + width / 2, clean_cv):
        ax.text(xi, value + 0.35, f"{value:.2f}%", ha="center", va="bottom", fontsize=7)
    ax.set_title("C  清洗前后各频点变异系数")
    ax.set_xlabel("频率（kHz）")
    ax.set_ylabel("幅值CV（%）")
    ax.set_xticks(x, [str(int(value / 1000)) for value in frequencies])
    ax.legend(frameon=False, loc="upper left")
    ax.grid(axis="y", color="#E5E7EB", linewidth=0.6)

    ax = axes[1, 1]
    palette = ["#E69F00", "#56B4E9", "#009E73", "#0072B2", "#CC79A7"]
    for index, (frequency, color) in enumerate(zip(frequencies, palette)):
        ax.scatter(raw_selected[:, index].real, raw_selected[:, index].imag, s=18,
                   color=color, alpha=0.75, label=f"{frequency / 1000:g} kHz")
        if len(raw_rejected):
            ax.scatter(raw_rejected[:, index].real, raw_rejected[:, index].imag, s=34,
                       facecolors="none", edgecolors=COLORS["rejected"], marker="s", linewidths=1.0)
    ax.plot([], [], marker="s", markerfacecolor="none", markeredgecolor=COLORS["rejected"],
            linestyle="none", label="异常轮")
    ax.set_title("D  Real–Imag复数平面分布")
    ax.set_xlabel("Real（AD5933原始计数）")
    ax.set_ylabel("Imag（AD5933原始计数）")
    ax.legend(frameon=False, ncol=2, loc="best")
    ax.grid(color="#E5E7EB", linewidth=0.6)

    summary = (
        f"完整扫频 {len(all_ids)}轮｜选入 {len(selected_ids)}轮｜"
        f"异常 {len(rejected_ids)}轮（{report['anomaly_pct']:.1f}%）｜"
        f"清洗后最大CV {report['maximum_clean_magnitude_cv_pct']:.3f}%｜采集质控 {report['final_decision']}"
    )
    fig.suptitle("Sherry 左腿第1次：5分钟人体阻抗采集质量概览\n" + summary,
                 fontsize=13, fontweight="bold")
    save_figure(fig, args.out / "图1_采集时序_异常轮与清洗效果")
    plt.close(fig)

    models = calibration.build_engine(args.calibration_dir)
    frequency_indices = {int(frequency): index for index, frequency in enumerate(calibration.FREQUENCIES)}
    body_by_sweep = []
    coverage = {}
    for sweep_id in selected_ids:
        raw_full = np.asarray([sweeps[sweep_id][int(f)] for f in calibration.FREQUENCIES])
        body_by_sweep.append(calibration.predict_total_impedance(raw_full, models) - calibration.SERIES_RESISTOR_OHM)
    body_by_sweep = np.asarray(body_by_sweep)
    report_indices = [frequency_indices[int(value)] for value in frequencies]
    body = body_by_sweep[:, report_indices]
    for index, frequency in enumerate(frequencies):
        hull = calibration.model_for_frequency(models, int(frequency))[3]
        inside = [calibration.point_in_convex_hull((value.real, value.imag), hull)
                  for value in raw_selected[:, index]]
        coverage[int(frequency)] = float(np.mean(inside) * 100.0)

    body_trimmed_real = trimmed_mean(body.real)
    body_trimmed_imag = trimmed_mean(body.imag)
    body_real_sd = np.std(body.real, axis=0, ddof=1)
    body_imag_sd = np.std(body.imag, axis=0, ddof=1)

    fig, axes = plt.subplots(1, 2, figsize=(11.8, 4.4), constrained_layout=True)
    ax = axes[0]
    ax.errorbar(frequencies / 1000, body_trimmed_real, yerr=body_real_sd, color="#0072B2",
                marker="o", linewidth=2.0, capsize=3, label="R：10%截尾均值 ± SD")
    ax.errorbar(frequencies / 1000, body_trimmed_imag, yerr=body_imag_sd, color="#D55E00",
                marker="s", linewidth=2.0, capsize=3, label="X：10%截尾均值 ± SD")
    for frequency in FORMAL_FREQUENCIES:
        ax.axvspan(frequency / 1000 - 0.35, frequency / 1000 + 0.35,
                   color="#009E73", alpha=0.08)
    ax.set_title("A  校准后人体阻抗频谱")
    ax.set_xlabel("频率（kHz）")
    ax.set_ylabel("人体支路阻抗（Ω）")
    ax.set_xticks(frequencies / 1000)
    ax.legend(frameon=False)
    ax.grid(color="#E5E7EB", linewidth=0.6)

    ax = axes[1]
    bars = ax.bar(np.arange(len(frequencies)), [coverage[int(f)] for f in frequencies],
                  color=["#AAB2BD", "#0072B2", "#0072B2", "#0072B2", "#AAB2BD"])
    ax.axhline(80.0, color=COLORS["threshold"], linestyle="--", linewidth=1.2, label="覆盖阈值 80%")
    for bar, frequency in zip(bars, frequencies):
        value = coverage[int(frequency)]
        ax.text(bar.get_x() + bar.get_width() / 2, min(value + 2, 96), f"{value:.0f}%",
                ha="center", va="bottom", fontsize=8)
    ax.set_ylim(0, 108)
    ax.set_xticks(np.arange(len(frequencies)), [str(int(value / 1000)) for value in frequencies])
    ax.set_xlabel("频率（kHz）")
    ax.set_ylabel("位于冻结CAL凸包内的轮次比例（%）")
    ax.set_title("B  冻结CAL凸包覆盖")
    ax.legend(frameon=False, loc="lower left")
    ax.grid(axis="y", color="#E5E7EB", linewidth=0.6)

    formal_pass = all(coverage[value] >= 80.0 for value in FORMAL_FREQUENCIES)
    fig.suptitle(
        "校准后阻抗与适用域检查\n"
        f"正确模型：外部10 kΩ + [R1 ∥ (R2 + C)]｜10–20 kHz域：{'PASS' if formal_pass else 'FAIL'}",
        fontsize=13, fontweight="bold",
    )
    save_figure(fig, args.out / "图2_校准后阻抗谱与CAL凸包覆盖")
    plt.close(fig)

    summary_payload = {
        "input": str(args.input.resolve()),
        "complete_sweeps": len(all_ids),
        "selected_sweeps": len(selected_ids),
        "rejected_sweep_ids": rejected_ids,
        "anomaly_pct": report["anomaly_pct"],
        "maximum_clean_magnitude_cv_pct": report["maximum_clean_magnitude_cv_pct"],
        "acquisition_qc": report["final_decision"],
        "calibration_hull_coverage_pct": {str(key): value for key, value in coverage.items()},
        "formal_10_20khz_domain": "PASS" if formal_pass else "FAIL",
        "body_real_trimmed_mean_10pct_ohm": {
            str(int(f)): float(value) for f, value in zip(frequencies, body_trimmed_real)
        },
        "body_imag_trimmed_mean_10pct_ohm": {
            str(int(f)): float(value) for f, value in zip(frequencies, body_trimmed_imag)
        },
    }
    (args.out / "绘图数据摘要.json").write_text(
        json.dumps(summary_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(summary_payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
