"""Post-process one five-minute AD5933 human recording.

Reject anomalous complete sweeps across the full recording, then select the
earliest 30 valid sweeps. Persistent multiple amplitude states or excessive
cleaned CV invalidate the recording. Statistics are calculated per frequency;
no smoothing is performed across frequencies.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path

import numpy as np
import human_qc_core


def write_csv(path: Path, rows: list[dict]) -> None:
    if not rows:
        return
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def trimmed_mean(values: np.ndarray, proportion: float = 0.10) -> float:
    ordered = np.sort(np.asarray(values, dtype=float))
    cut = int(math.floor(len(ordered) * proportion))
    kept = ordered[cut:len(ordered) - cut] if cut else ordered
    return float(np.mean(kept))


def main() -> int:
    parser = argparse.ArgumentParser(description="Automatic QC and robust summary for a 5-minute human TXT.")
    parser.add_argument("input", type=Path)
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--min-frequency", type=int, default=5_000)
    parser.add_argument("--max-frequency", type=int, default=25_000)
    parser.add_argument("--target-valid-sweeps", type=int, default=30)
    parser.add_argument("--maximum-clean-cv-pct", type=float, default=5.0)
    args = parser.parse_args()

    cal = human_qc_core
    qc = human_qc_core
    frequencies = [int(f) for f in cal.FREQUENCIES
                   if args.min_frequency <= int(f) <= args.max_frequency]
    sweeps = cal.parse_complete_sweeps(args.input)
    sweep_ids = sorted(sweeps)
    output = args.out or args.input.with_name(args.input.stem + "_5min_qc")
    output.mkdir(parents=True, exist_ok=True)

    if not sweep_ids:
        report = {"input": str(args.input.resolve()), "complete_sweeps": 0,
                  "final_decision": "FAIL", "failure_reasons": ["no_complete_sweeps"]}
        (output / "qc_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return 2

    raw = np.array([[sweeps[sid][f] for f in frequencies] for sid in sweep_ids])
    magnitude = np.abs(raw)
    log_magnitude = np.log(np.maximum(magnitude, 1e-12))
    levels = np.mean(log_magnitude, axis=1)

    level_median = float(np.median(levels))
    level_mad = float(np.median(np.abs(levels - level_median)))
    level_scale = max(1.4826 * level_mad, math.log1p(0.02))
    level_flags = np.abs(levels - level_median) > max(6.0 * level_scale, math.log1p(0.15))

    frequency_median = np.median(log_magnitude, axis=0)
    frequency_mad = np.median(np.abs(log_magnitude - frequency_median), axis=0)
    frequency_scale = np.maximum(1.4826 * frequency_mad, math.log1p(0.02))
    shape_scores = np.sqrt(np.mean(((log_magnitude - frequency_median) / frequency_scale) ** 2, axis=1))
    shape_flags = shape_scores > 10.0

    multistate, minority_state_ids, state_separation_pct = qc.detect_multistate(levels, sweep_ids)
    rejected = {sweep_ids[i] for i in range(len(sweep_ids)) if level_flags[i] or shape_flags[i]}
    rejected |= minority_state_ids

    valid_ids = [sweep_id for sweep_id in sweep_ids if sweep_id not in rejected]
    selected_ids = [] if multistate else valid_ids[:args.target_valid_sweeps]
    selected_set = set(selected_ids)

    decision_rows = []
    for index, sweep_id in enumerate(sweep_ids):
        reasons = []
        if level_flags[index]: reasons.append("whole_sweep_amplitude_shift")
        if shape_flags[index]: reasons.append("whole_sweep_shape_outlier")
        if sweep_id in minority_state_ids: reasons.append("minority_amplitude_state")
        if sweep_id in selected_set:
            decision = "SELECTED"
        elif sweep_id in rejected:
            decision = "REJECT"
        else:
            decision = "VALID_NOT_SELECTED"
        decision_rows.append({
            "sweep_id": sweep_id, "decision": decision, "reason": ";".join(reasons),
            "band_geometric_mean_magnitude": float(np.exp(levels[index])),
            "shape_score": float(shape_scores[index]),
        })
    write_csv(output / "sweep_decisions.csv", decision_rows)

    frequency_rows = []
    if selected_ids:
        for column, frequency in enumerate(frequencies):
            values = np.array([sweeps[sid][frequency] for sid in selected_ids])
            mags = np.abs(values)
            frequency_rows.append({
                "frequency_hz": frequency,
                "selected_sweeps": len(values),
                "real_mean": float(np.mean(values.real)),
                "real_median": float(np.median(values.real)),
                "real_trimmed_mean_10pct": trimmed_mean(values.real),
                "imag_mean": float(np.mean(values.imag)),
                "imag_median": float(np.median(values.imag)),
                "imag_trimmed_mean_10pct": trimmed_mean(values.imag),
                "magnitude_mean": float(np.mean(mags)),
                "magnitude_median": float(np.median(mags)),
                "magnitude_cv_pct": float(np.std(mags, ddof=1) / np.mean(mags) * 100.0),
            })
    write_csv(output / "frequency_robust_summary.csv", frequency_rows)

    clean_cv_pass = bool(frequency_rows) and all(
        row["magnitude_cv_pct"] <= args.maximum_clean_cv_pct for row in frequency_rows
    )
    anomaly_pct = len(rejected) / len(sweep_ids) * 100.0
    failure_reasons = []
    if multistate: failure_reasons.append("persistent_multiple_contact_states")
    if len(selected_ids) < args.target_valid_sweeps: failure_reasons.append("fewer_than_30_valid_sweeps_in_full_recording")
    if selected_ids and not clean_cv_pass: failure_reasons.append("clean_cv_exceeds_5pct")
    final_pass = not failure_reasons
    report = {
        "input": str(args.input.resolve()),
        "load_topology": cal.LOAD_TOPOLOGY,
        "external_series_resistor_ohm": cal.SERIES_RESISTOR_OHM,
        "frequency_band_hz": [frequencies[0], frequencies[-1]],
        "complete_sweeps": len(sweep_ids),
        "valid_sweeps": len(valid_ids),
        "rejected_sweep_ids": sorted(rejected),
        "rejected_sweeps": len(rejected),
        "anomaly_pct": anomaly_pct,
        "multistate_detected": multistate,
        "state_separation_pct": state_separation_pct,
        "selected_valid_sweep_ids": selected_ids,
        "selected_valid_sweeps": len(selected_ids),
        "maximum_clean_magnitude_cv_pct": max((row["magnitude_cv_pct"] for row in frequency_rows), default=None),
        "maximum_allowed_clean_cv_pct": args.maximum_clean_cv_pct,
        "clean_cv_pass": clean_cv_pass,
        "failure_reasons": failure_reasons,
        "final_decision": "PASS" if final_pass else "FAIL",
    }
    (output / "qc_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    lines = [
        f"input={report['input']}", f"complete_sweeps={report['complete_sweeps']}",
        f"valid_sweeps={report['valid_sweeps']}",
        f"rejected_sweeps={report['rejected_sweeps']}",
        "rejected_sweep_ids=" + ",".join(map(str, report["rejected_sweep_ids"])),
        f"anomaly_pct={anomaly_pct:.2f}",
        f"multistate_detected={int(multistate)}",
        f"state_separation_pct={state_separation_pct if state_separation_pct is not None else 'NA'}",
        "selected_valid_sweep_ids=" + ",".join(map(str, selected_ids)),
        f"selected_valid_sweeps={len(selected_ids)}",
        f"maximum_clean_magnitude_cv_pct={report['maximum_clean_magnitude_cv_pct']}",
        f"maximum_allowed_clean_cv_pct={args.maximum_clean_cv_pct:.2f}",
        f"clean_cv_check={'PASS' if clean_cv_pass else 'FAIL'}",
        "failure_reasons=" + ";".join(failure_reasons),
        f"final_decision={report['final_decision']}",
    ]
    (output / "qc_report.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    print(f"outputs={output.resolve()}")
    return 0 if final_pass else 2


if __name__ == "__main__":
    raise SystemExit(main())
