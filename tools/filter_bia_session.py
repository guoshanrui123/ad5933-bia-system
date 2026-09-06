"""Filter an AD5933 repeated-sweep session without modifying the raw log.

Pipeline (independently for Real and Imag at each frequency):
  complete-sweep selection -> conservative Hampel spike replacement
  -> centred 5-point moving-average FIR -> non-overlapping 10-sweep summaries.

This is a signal-quality/exploration tool. Filtering does not turn a failed
session into valid physiological or clinical data.
"""

from __future__ import annotations

import argparse
import csv
import math
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np


FREQUENCIES = tuple(range(5_000, 100_001, 5_000))


def parse_complete_sweeps(path: Path):
    sweeps: dict[int, dict[int, tuple[int, float, float]]] = {}
    with path.open(encoding="utf-8", errors="replace", newline="") as stream:
        for row in csv.reader(line for line in stream if not line.startswith("#")):
            if len(row) < 10 or row[0] != "data":
                continue
            try:
                timestamp = int(row[1])
                sweep_id = int(row[2])
                frequency = int(row[4])
                real, imag = float(row[5]), float(row[6])
            except ValueError:
                continue
            if frequency in FREQUENCIES:
                sweeps.setdefault(sweep_id, {})[frequency] = (timestamp, real, imag)
    expected = set(FREQUENCIES)
    complete = {key: value for key, value in sweeps.items() if set(value) == expected}
    ids = sorted(complete)
    if not ids:
        raise ValueError("no complete 20-frequency sweeps found")
    timestamps = np.array([complete[key][FREQUENCIES[0]][0] for key in ids], dtype=float)
    values = np.array([[[complete[key][frequency][1], complete[key][frequency][2]]
                        for frequency in FREQUENCIES] for key in ids], dtype=float)
    return ids, timestamps, values, len(sweeps) - len(complete)


def rolling_hampel(values: np.ndarray, radius: int = 2, threshold: float = 5.0):
    """Replace only isolated component spikes; return cleaned data and flags."""
    cleaned = values.copy()
    flags = np.zeros_like(values, dtype=bool)
    for index in range(len(values)):
        start, stop = max(0, index - radius), min(len(values), index + radius + 1)
        window = values[start:stop]
        if len(window) < 3:
            continue
        median = np.median(window, axis=0)
        mad = np.median(np.abs(window - median), axis=0)
        scale = 1.4826 * mad
        valid_scale = scale > 1e-12
        flagged = valid_scale & (np.abs(values[index] - median) > threshold * scale)
        cleaned[index][flagged] = median[flagged]
        flags[index] = flagged
    return cleaned, flags


def centred_moving_average(values: np.ndarray, window: int = 5) -> np.ndarray:
    if window < 1 or window % 2 == 0:
        raise ValueError("FIR window must be a positive odd integer")
    radius = window // 2
    padded = np.pad(values, ((radius, radius), (0, 0), (0, 0)), mode="reflect")
    cumulative = np.cumsum(padded, axis=0, dtype=float)
    cumulative = np.concatenate((np.zeros_like(cumulative[:1]), cumulative), axis=0)
    return (cumulative[window:] - cumulative[:-window]) / window


def write_csv(path: Path, rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def magnitude_phase(values: np.ndarray):
    magnitude = np.linalg.norm(values, axis=2)
    phase = np.degrees(np.arctan2(values[:, :, 1], values[:, :, 0]))
    return magnitude, phase


def main() -> int:
    parser = argparse.ArgumentParser(description="Filter one repeated-sweep BIA TXT session")
    parser.add_argument("input", type=Path, help="raw TXT log")
    parser.add_argument("--out", type=Path, default=None, help="output directory")
    parser.add_argument("--fir-window", type=int, default=5)
    parser.add_argument("--aggregate", type=int, default=10)
    parser.add_argument("--hampel-radius", type=int, default=2)
    parser.add_argument("--hampel-threshold", type=float, default=5.0)
    parser.add_argument("--show", action="store_true")
    args = parser.parse_args()

    output = args.out or args.input.with_name(args.input.stem + "_filtered")
    output.mkdir(parents=True, exist_ok=True)
    ids, timestamps, raw, partial_count = parse_complete_sweeps(args.input)
    elapsed_seconds = (timestamps - timestamps[0]) / 1000.0

    hampel, flags = rolling_hampel(raw, args.hampel_radius, args.hampel_threshold)
    filtered = centred_moving_average(hampel, args.fir_window)
    raw_mag, raw_phase = magnitude_phase(raw)
    filtered_mag, filtered_phase = magnitude_phase(filtered)

    sweep_rows = []
    for i, sweep_id in enumerate(ids):
        for j, frequency in enumerate(FREQUENCIES):
            sweep_rows.append({
                "sweep_id": sweep_id, "elapsed_seconds": elapsed_seconds[i],
                "frequency_hz": frequency,
                "raw_real": raw[i, j, 0], "raw_imag": raw[i, j, 1],
                "raw_magnitude": raw_mag[i, j], "raw_phase_deg": raw_phase[i, j],
                "hampel_real_flag": int(flags[i, j, 0]),
                "hampel_imag_flag": int(flags[i, j, 1]),
                "filtered_real": filtered[i, j, 0], "filtered_imag": filtered[i, j, 1],
                "filtered_magnitude": filtered_mag[i, j],
                "filtered_phase_deg": filtered_phase[i, j],
            })
    write_csv(output / "filtered_sweeps.csv", sweep_rows)

    aggregate_rows = []
    block_index = 0
    for start in range(0, len(ids), args.aggregate):
        stop = min(start + args.aggregate, len(ids))
        if stop - start < max(3, args.aggregate // 2):
            continue
        block_index += 1
        block = filtered[start:stop]
        block_mag, block_phase = magnitude_phase(block)
        for j, frequency in enumerate(FREQUENCIES):
            real_median = np.median(block[:, j, 0])
            imag_median = np.median(block[:, j, 1])
            aggregate_rows.append({
                "window": block_index, "first_sweep_id": ids[start],
                "last_sweep_id": ids[stop - 1], "sweep_count": stop - start,
                "mid_elapsed_seconds": float(np.mean(elapsed_seconds[start:stop])),
                "frequency_hz": frequency,
                "real_median": real_median, "imag_median": imag_median,
                "magnitude_from_median_vector": math.hypot(real_median, imag_median),
                "magnitude_median": np.median(block_mag[:, j]),
                "magnitude_mad": np.median(np.abs(block_mag[:, j] - np.median(block_mag[:, j]))),
                "magnitude_cv_pct": (np.std(block_mag[:, j], ddof=1) /
                                      np.mean(block_mag[:, j]) * 100),
                "phase_median_deg": np.median(block_phase[:, j]),
            })
    write_csv(output / "aggregated_10sweep_windows.csv", aggregate_rows)

    quality_rows = []
    for j, frequency in enumerate(FREQUENCIES):
        raw_cv = np.std(raw_mag[:, j], ddof=1) / np.mean(raw_mag[:, j]) * 100
        filtered_cv = (np.std(filtered_mag[:, j], ddof=1) /
                       np.mean(filtered_mag[:, j]) * 100)
        blocks = [row for row in aggregate_rows if row["frequency_hz"] == frequency]
        block_values = np.array([row["magnitude_from_median_vector"] for row in blocks])
        block_cv = (np.std(block_values, ddof=1) / np.mean(block_values) * 100
                    if len(block_values) > 1 else math.nan)
        quality_rows.append({
            "frequency_hz": frequency, "complete_sweeps": len(ids),
            "partial_sweeps_excluded": partial_count,
            "hampel_component_flags": int(flags[:, j, :].sum()),
            "raw_magnitude_cv_pct": raw_cv,
            "filtered_magnitude_cv_pct": filtered_cv,
            "aggregate_window_count": len(block_values),
            "between_aggregate_windows_cv_pct": block_cv,
            "raw_session_pass_cv_lt_5pct": int(raw_cv < 5),
            "filtered_session_pass_cv_lt_5pct": int(filtered_cv < 5),
        })
    write_csv(output / "quality_summary.csv", quality_rows)

    selected = [0, 9, 14, 19]  # 5, 50, 75, 100 kHz
    fig, axes = plt.subplots(2, 2, figsize=(12, 8), constrained_layout=True)
    for axis, j in zip(axes.flat, selected):
        axis.plot(elapsed_seconds, raw_mag[:, j], color="0.75", lw=1, label="raw")
        axis.plot(elapsed_seconds, filtered_mag[:, j], color="tab:blue", lw=2,
                  label=f"{args.fir_window}-point FIR")
        axis.set(title=f"{FREQUENCIES[j] / 1000:g} kHz", xlabel="Time (s)",
                 ylabel="Raw magnitude")
        axis.grid(alpha=.25); axis.legend()
    fig.savefig(output / "time_series_raw_vs_filtered.png", dpi=180)

    fig, axis = plt.subplots(figsize=(10, 5), constrained_layout=True)
    x = np.array(FREQUENCIES) / 1000
    axis.plot(x, [row["raw_magnitude_cv_pct"] for row in quality_rows], "o-", label="raw")
    axis.plot(x, [row["filtered_magnitude_cv_pct"] for row in quality_rows], "o-",
              label=f"{args.fir_window}-point FIR")
    axis.axhline(5, color="tab:green", ls="--", lw=1, label="5% candidate threshold")
    axis.axhline(10, color="tab:red", ls="--", lw=1, label="10% warning threshold")
    axis.set(xlabel="Frequency (kHz)", ylabel="Session CV (%)",
             title="Filtering changes variability, not session validity")
    axis.grid(alpha=.25); axis.legend()
    fig.savefig(output / "cv_before_after_filter.png", dpi=180)

    with (output / "README.txt").open("w", encoding="utf-8") as stream:
        stream.write(
            f"Input: {args.input.resolve()}\nComplete sweeps: {len(ids)}\n"
            f"Partial sweeps excluded: {partial_count}\n"
            f"Hampel: radius={args.hampel_radius}, threshold={args.hampel_threshold} MAD\n"
            f"FIR: centred moving average, window={args.fir_window}\n"
            f"Aggregation: non-overlapping {args.aggregate}-sweep windows\n"
            "Real and Imag were filtered separately; magnitude and phase were recomputed.\n"
            "These outputs are exploratory and do not override raw-session quality failure.\n"
        )

    print(f"Complete sweeps: {len(ids)}; partial excluded: {partial_count}")
    print(f"Hampel component flags: {int(flags.sum())}")
    print(f"Mean raw magnitude CV: {np.mean([r['raw_magnitude_cv_pct'] for r in quality_rows]):.2f}%")
    print(f"Mean filtered magnitude CV: {np.mean([r['filtered_magnitude_cv_pct'] for r in quality_rows]):.2f}%")
    print(f"Frequencies with filtered CV < 5%: "
          f"{sum(r['filtered_session_pass_cv_lt_5pct'] for r in quality_rows)}/20")
    print(f"Outputs: {output.resolve()}")
    if args.show:
        plt.show()
    else:
        plt.close("all")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
