"""Automatic whole-sweep quality control for repeated AD5933 sessions.

The script never edits individual frequency samples. It either keeps or rejects
an entire sweep, then reports stability and calibration-hull coverage.
"""

from __future__ import annotations

import argparse
import csv
import importlib.util
import json
import math
from pathlib import Path

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LOG_DIR = ROOT / "calibration" / "reference_loads"


def load_calibration_module():
    path = Path(__file__).with_name("calibrate_ser10k_2r1c.py")
    spec = importlib.util.spec_from_file_location("ser10k_calibration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def write_csv(path: Path, rows: list[dict]) -> None:
    if not rows:
        return
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def detect_multistate(levels: np.ndarray, sweep_ids: list[int]):
    """Find two substantial amplitude states separated by at least 15%."""
    count = len(levels)
    minimum_cluster = max(3, math.ceil(0.20 * count))
    if count < 2 * minimum_cluster:
        return False, set(), None

    order = np.argsort(levels)
    sorted_levels = levels[order]
    best = None
    for split in range(minimum_cluster, count - minimum_cluster + 1):
        low = sorted_levels[:split]
        high = sorted_levels[split:]
        separation = float(np.exp(np.mean(high) - np.mean(low)) - 1.0)
        if separation < 0.15:
            continue
        gap = float(sorted_levels[split] - sorted_levels[split - 1])
        low_internal_gap = float(np.max(np.diff(low))) if len(low) > 1 else 0.0
        high_internal_gap = float(np.max(np.diff(high))) if len(high) > 1 else 0.0
        if low_internal_gap > 1.5 * gap or high_internal_gap > 1.5 * gap:
            continue
        score = (separation, gap)
        if best is None or score > best[0]:
            best = (score, split, separation)
    if best is None:
        return False, set(), None

    _, split, separation = best
    low_indices = order[:split]
    high_indices = order[split:]
    rejected_indices = low_indices if len(low_indices) <= len(high_indices) else high_indices
    rejected = {sweep_ids[int(index)] for index in rejected_indices}
    return True, rejected, separation * 100.0


def build_calibration_hulls(module, log_dir: Path, frequencies: list[int]):
    raw_means = []
    for _, filename, _, _, _ in module.CALIBRATION_LOADS:
        sweeps = module.parse_complete_sweeps(log_dir / filename)
        raw_means.append(module.mean_raw_by_frequency(sweeps))
    frequency_indices = {int(f): i for i, f in enumerate(module.FREQUENCIES)}
    return {
        frequency: module.convex_hull(
            [
                (values[frequency_indices[frequency]].real, values[frequency_indices[frequency]].imag)
                for values in raw_means
            ]
        )
        for frequency in frequencies
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Whole-sweep AD5933 QC with CV, calibration coverage, and PASS/FAIL."
    )
    parser.add_argument("input", type=Path, help="raw repeated-sweep TXT file")
    parser.add_argument("--out", type=Path, default=None, help="output directory")
    parser.add_argument("--min-frequency", type=int, default=5_000)
    parser.add_argument("--max-frequency", type=int, default=30_000)
    parser.add_argument("--minimum-valid-sweeps", type=int, default=27)
    parser.add_argument("--maximum-anomaly-pct", type=float, default=10.0)
    parser.add_argument("--maximum-clean-cv-pct", type=float, default=5.0)
    parser.add_argument("--minimum-coverage-pct", type=float, default=80.0)
    args = parser.parse_args()

    module = load_calibration_module()
    frequencies = [
        int(f)
        for f in module.FREQUENCIES
        if args.min_frequency <= int(f) <= args.max_frequency
    ]
    if not frequencies:
        parser.error("requested frequency band contains no sweep frequencies")

    sweeps = module.parse_complete_sweeps(args.input)
    sweep_ids = sorted(sweeps)
    raw = np.array([[sweeps[sweep_id][f] for f in frequencies] for sweep_id in sweep_ids])
    magnitudes = np.abs(raw)
    log_magnitudes = np.log(np.maximum(magnitudes, 1e-12))
    levels = np.mean(log_magnitudes, axis=1)

    level_median = float(np.median(levels))
    level_mad = float(np.median(np.abs(levels - level_median)))
    level_scale = max(1.4826 * level_mad, math.log1p(0.02))
    level_deviation = np.abs(levels - level_median)
    whole_level_flags = level_deviation > max(6.0 * level_scale, math.log1p(0.15))

    frequency_median = np.median(log_magnitudes, axis=0)
    frequency_mad = np.median(np.abs(log_magnitudes - frequency_median), axis=0)
    frequency_scale = np.maximum(1.4826 * frequency_mad, math.log1p(0.02))
    shape_score = np.sqrt(
        np.mean(((log_magnitudes - frequency_median) / frequency_scale) ** 2, axis=1)
    )
    shape_flags = shape_score > 10.0

    multistate, multistate_rejected, state_separation_pct = detect_multistate(
        levels, sweep_ids
    )
    rejected_ids = {
        sweep_ids[index]
        for index in range(len(sweep_ids))
        if whole_level_flags[index] or shape_flags[index]
    } | multistate_rejected
    valid_ids = [sweep_id for sweep_id in sweep_ids if sweep_id not in rejected_ids]

    hulls = build_calibration_hulls(module, DEFAULT_LOG_DIR, frequencies)
    index_by_id = {sweep_id: index for index, sweep_id in enumerate(sweep_ids)}
    valid_indices = [index_by_id[sweep_id] for sweep_id in valid_ids]

    sweep_rows = []
    for index, sweep_id in enumerate(sweep_ids):
        reasons = []
        if whole_level_flags[index]:
            reasons.append("whole_sweep_amplitude_shift")
        if shape_flags[index]:
            reasons.append("whole_sweep_shape_outlier")
        if sweep_id in multistate_rejected:
            reasons.append("minority_amplitude_state")
        sweep_rows.append(
            {
                "sweep_id": sweep_id,
                "decision": "REJECT" if sweep_id in rejected_ids else "KEEP",
                "reason": ";".join(reasons),
                "band_geometric_mean_magnitude": float(np.exp(levels[index])),
                "shape_score": float(shape_score[index]),
            }
        )

    frequency_rows = []
    for column, frequency in enumerate(frequencies):
        all_values = magnitudes[:, column]
        clean_values = magnitudes[valid_indices, column] if valid_indices else np.array([])
        raw_cv = (
            float(np.std(all_values, ddof=1) / np.mean(all_values) * 100.0)
            if len(all_values) > 1
            else math.nan
        )
        clean_cv = (
            float(np.std(clean_values, ddof=1) / np.mean(clean_values) * 100.0)
            if len(clean_values) > 1
            else math.nan
        )
        inside = [
            module.point_in_convex_hull(
                (raw[index, column].real, raw[index, column].imag), hulls[frequency]
            )
            for index in valid_indices
        ]
        coverage = float(np.mean(inside) * 100.0) if inside else 0.0
        frequency_rows.append(
            {
                "frequency_hz": frequency,
                "raw_magnitude_cv_pct": raw_cv,
                "clean_magnitude_cv_pct": clean_cv,
                "valid_sweep_count": len(valid_ids),
                "inside_calibration_hull_pct": coverage,
                "pass_clean_cv": int(clean_cv <= args.maximum_clean_cv_pct),
                "pass_calibration_coverage": int(coverage >= args.minimum_coverage_pct),
            }
        )

    complete_count = len(sweep_ids)
    rejected_count = len(rejected_ids)
    anomaly_pct = rejected_count / complete_count * 100.0
    checks = {
        "valid_sweeps": len(valid_ids) >= args.minimum_valid_sweeps,
        "anomaly_rate": anomaly_pct <= args.maximum_anomaly_pct,
        "single_state": not multistate,
        "clean_cv": all(row["pass_clean_cv"] for row in frequency_rows),
        "calibration_coverage": all(
            row["pass_calibration_coverage"] for row in frequency_rows
        ),
    }
    final_pass = all(checks.values())

    output = args.out or args.input.with_name(args.input.stem + "_qc")
    output.mkdir(parents=True, exist_ok=True)
    write_csv(output / "sweep_decisions.csv", sweep_rows)
    write_csv(output / "frequency_quality.csv", frequency_rows)

    report = {
        "input": str(args.input.resolve()),
        "load_topology": module.LOAD_TOPOLOGY,
        "external_series_resistor_ohm": module.SERIES_RESISTOR_OHM,
        "frequency_band_hz": [frequencies[0], frequencies[-1]],
        "complete_sweeps": complete_count,
        "valid_sweeps": len(valid_ids),
        "rejected_sweeps": sorted(rejected_ids),
        "anomaly_pct": anomaly_pct,
        "multistate_detected": multistate,
        "state_separation_pct": state_separation_pct,
        "checks": checks,
        "final_decision": "PASS" if final_pass else "FAIL",
    }
    (output / "qc_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    lines = [
        f"input={report['input']}",
        f"frequency_band_hz={frequencies[0]}-{frequencies[-1]}",
        f"complete_sweeps={complete_count}",
        f"valid_sweeps={len(valid_ids)}",
        "rejected_sweeps=" + ",".join(str(x) for x in sorted(rejected_ids)),
        f"anomaly_pct={anomaly_pct:.2f}",
        f"multistate_detected={int(multistate)}",
        f"state_separation_pct={state_separation_pct if state_separation_pct is not None else 'NA'}",
        *[f"check_{name}={'PASS' if passed else 'FAIL'}" for name, passed in checks.items()],
        f"final_decision={'PASS' if final_pass else 'FAIL'}",
    ]
    (output / "qc_report.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    print(f"outputs={output.resolve()}")
    return 0 if final_pass else 2


if __name__ == "__main__":
    raise SystemExit(main())
