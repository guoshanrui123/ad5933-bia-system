import argparse
import csv
import math
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np


ROOT = Path(__file__).resolve().parents[1]
LOG_DIR = ROOT / "logs"
FREQUENCIES = np.arange(5_000, 100_001, 5_000)
SERIES_RESISTOR_OHM = 10_000.0
LOAD_TOPOLOGY = "R1_parallel_(R2_series_C)"

CALIBRATION_LOADS = [
    ("CAL01", "SER10K-LOW-CAL01-R1_20K-R2_680R-C_2N2.txt", 20_000.0, 680.0, 2.2e-9),
    ("CAL02", "SER10K-LOW-CAL02-R1_20K-R2_680R-C_22N.txt", 20_000.0, 680.0, 22e-9),
    ("CAL03", "SER10K-LOW-CAL03-R1_20K-R2_1K-C_2N2.txt", 20_000.0, 1_000.0, 2.2e-9),
    ("CAL04", "SER10K-LOW-CAL04-R1_20K-R2_1K-C_22N.txt", 20_000.0, 1_000.0, 22e-9),
    ("CAL05", "SER10K-CAND01-R1_47K-R2_510R-C_11N.txt", 47_000.0, 510.0, 11e-9),
    ("CAL06", "SER10K-CAND02-R1_2K-R2_680R-C_47N.txt", 2_000.0, 680.0, 47e-9),
    ("CAL07", "SER10K-LOW-CAL07-R1_100K-R2_330R-C_11N.txt", 100_000.0, 330.0, 11e-9),
    ("CAL08", "SER10K-LOW-CAL08-R1_100K-R2_3K3-C_2N2.txt", 100_000.0, 3_300.0, 2.2e-9),
]
VALIDATION_LOADS = [
    ("VALID01", "SER10K-LOW-VALID01-R1_20K-R2_840R-C_11N.txt", 20_000.0, 840.0, 11e-9),
    ("VALID02", "SER10K-VALID02-R1_4K7-R2_840R-C_47N.txt", 4_700.0, 840.0, 47e-9),
    ("VALID03", "SER10K-LOW-VALID03-R1_100K-R2_840R-C_11N.txt", 100_000.0, 840.0, 11e-9),
]


def parse_complete_sweeps(path):
    sweeps = {}
    with path.open("r", encoding="utf-8-sig", errors="ignore", newline="") as handle:
        for row in csv.reader(handle):
            if len(row) < 7 or row[0] != "data":
                continue
            try:
                sweep_id = int(row[2])
                frequency = int(row[4])
                raw = complex(float(row[5]), float(row[6]))
            except ValueError:
                continue
            if frequency in FREQUENCIES:
                sweeps.setdefault(sweep_id, {})[frequency] = raw

    expected = set(int(x) for x in FREQUENCIES)
    complete = {key: value for key, value in sweeps.items() if set(value) == expected}
    if not complete:
        raise ValueError(f"No complete 5-100 kHz sweep found in {path}")
    return complete


def theoretical_impedance(r1_ohm, r2_ohm, capacitance_f):
    omega = 2.0 * np.pi * FREQUENCIES
    # Physical fixture topology: R1 || (R2 + C), with an external 10 kohm in series.
    series_rc = r2_ohm + 1.0 / (1j * omega * capacitance_f)
    load = 1.0 / (1.0 / r1_ohm + 1.0 / series_rc)
    return SERIES_RESISTOR_OHM + load


def mean_raw_by_frequency(sweeps):
    return np.array(
        [np.mean([sweep[int(freq)] for sweep in sweeps.values()]) for freq in FREQUENCIES]
    )


def convex_hull(points):
    points = sorted(set((float(x), float(y)) for x, y in points))
    if len(points) <= 2:
        return points

    def cross(origin, a, b):
        return (a[0] - origin[0]) * (b[1] - origin[1]) - (a[1] - origin[1]) * (b[0] - origin[0])

    lower = []
    for point in points:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], point) <= 0:
            lower.pop()
        lower.append(point)
    upper = []
    for point in reversed(points):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], point) <= 0:
            upper.pop()
        upper.append(point)
    return lower[:-1] + upper[:-1]


def point_in_convex_hull(point, hull):
    if len(hull) < 3:
        return False
    signs = []
    for index, a in enumerate(hull):
        b = hull[(index + 1) % len(hull)]
        cross = (b[0] - a[0]) * (point[1] - a[1]) - (b[1] - a[1]) * (point[0] - a[0])
        signs.append(cross)
    tolerance = 1e-9
    return all(value >= -tolerance for value in signs) or all(value <= tolerance for value in signs)


def build_models(calibration_data):
    models = []
    coefficient_rows = []
    for index, frequency in enumerate(FREQUENCIES):
        raw = np.array([item["raw_mean"][index] for item in calibration_data])
        admittance = np.array([1.0 / item["z_theory"][index] for item in calibration_data])

        features = np.column_stack((raw.real, raw.imag))
        center = features.mean(axis=0)
        scale = features.std(axis=0, ddof=0)
        if np.any(scale == 0):
            raise ValueError(f"Degenerate calibration features at {frequency} Hz")
        design = np.column_stack(((features - center) / scale, np.ones(len(features))))
        target = np.column_stack((admittance.real, admittance.imag))
        coefficients = np.linalg.lstsq(design, target, rcond=None)[0]
        predicted = design @ coefficients
        train_error = np.mean(
            np.abs((predicted[:, 0] + 1j * predicted[:, 1]) - admittance)
            / np.abs(admittance)
        ) * 100.0

        hull = convex_hull(features)
        models.append((center, scale, coefficients, hull))
        coefficient_rows.append(
            {
                "load_topology": LOAD_TOPOLOGY,
                "freq_hz": int(frequency),
                "raw_real_center": center[0],
                "raw_imag_center": center[1],
                "raw_real_scale": scale[0],
                "raw_imag_scale": scale[1],
                "y_real_from_real": coefficients[0, 0],
                "y_real_from_imag": coefficients[1, 0],
                "y_real_intercept": coefficients[2, 0],
                "y_imag_from_real": coefficients[0, 1],
                "y_imag_from_imag": coefficients[1, 1],
                "y_imag_intercept": coefficients[2, 1],
                "training_y_mape_pct": train_error,
            }
        )
    return models, coefficient_rows


def predict_impedance(raw_values, models):
    predicted = []
    for raw, (center, scale, coefficients, _) in zip(raw_values, models):
        design = np.array([(raw.real - center[0]) / scale[0], (raw.imag - center[1]) / scale[1], 1.0])
        y_parts = design @ coefficients
        admittance = complex(y_parts[0], y_parts[1])
        predicted.append(1.0 / admittance)
    return np.array(predicted)


def write_rows(path, rows):
    if not rows:
        return
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def validation_rows(sweeps, z_theory, models):
    z_predicted = predict_impedance(mean_raw_by_frequency(sweeps), models)
    rows = []
    raw_mean = mean_raw_by_frequency(sweeps)
    for frequency, theory, predicted, raw, model in zip(FREQUENCIES, z_theory, z_predicted, raw_mean, models):
        body_r_theory = theory.real - SERIES_RESISTOR_OHM
        body_r_predicted = predicted.real - SERIES_RESISTOR_OHM
        rows.append(
            {
                "freq_hz": int(frequency),
                "R_total_theory_ohm": theory.real,
                "X_theory_ohm": theory.imag,
                "Z_total_theory_ohm": abs(theory),
                "R_total_predicted_ohm": predicted.real,
                "X_predicted_ohm": predicted.imag,
                "Z_total_predicted_ohm": abs(predicted),
                "R_total_error_pct": (predicted.real - theory.real) / theory.real * 100.0,
                "R_load_error_pct": (body_r_predicted - body_r_theory) / body_r_theory * 100.0,
                "X_error_pct": (predicted.imag - theory.imag) / abs(theory.imag) * 100.0,
                "Z_error_pct": (abs(predicted) - abs(theory)) / abs(theory) * 100.0,
                "phase_error_deg": np.degrees(np.angle(predicted / theory)),
                "inside_calibration_hull": int(point_in_convex_hull((raw.real, raw.imag), model[3])),
            }
        )
    return rows


def measurement_rows(sweeps, models):
    rows = []
    for sweep_id in sorted(sweeps):
        raw_values = np.array([sweeps[sweep_id][int(freq)] for freq in FREQUENCIES])
        total_z = predict_impedance(raw_values, models)
        for frequency, raw, total, model in zip(FREQUENCIES, raw_values, total_z, models):
            body = complex(total.real - SERIES_RESISTOR_OHM, total.imag)
            rows.append(
                {
                    "sweep_id": sweep_id,
                    "freq_hz": int(frequency),
                    "raw_real": raw.real,
                    "raw_imag": raw.imag,
                    "R_total_ohm": total.real,
                    "X_total_ohm": total.imag,
                    "Z_total_ohm": abs(total),
                    "phase_total_deg": np.degrees(np.angle(total)),
                    "R_body_ohm": body.real,
                    "X_body_ohm": body.imag,
                    "Z_body_ohm": abs(body),
                    "phase_body_deg": np.degrees(np.angle(body)),
                    "inside_calibration_hull": int(
                        point_in_convex_hull((raw.real, raw.imag), model[3])
                    ),
                }
            )
    return rows


def summarize_measurement(rows):
    summary = []
    for frequency in FREQUENCIES:
        group = [row for row in rows if row["freq_hz"] == frequency]
        output = {"freq_hz": int(frequency), "n": len(group)}
        output["inside_calibration_hull_pct"] = (
            np.mean([row["inside_calibration_hull"] for row in group]) * 100.0
        )
        for field in ("R_body_ohm", "X_body_ohm", "Z_body_ohm", "phase_body_deg"):
            values = np.array([row[field] for row in group], dtype=float)
            mean = values.mean()
            sd = values.std(ddof=1) if len(values) > 1 else 0.0
            margin = 1.96 * sd / math.sqrt(len(values))
            stem = field.removesuffix("_ohm").removesuffix("_deg")
            output[f"{stem}_mean"] = mean
            output[f"{stem}_sd"] = sd
            output[f"{stem}_ci95_low"] = mean - margin
            output[f"{stem}_ci95_high"] = mean + margin
            output[f"{stem}_cv_pct"] = abs(sd / mean * 100.0) if mean != 0 else float("nan")
        summary.append(output)
    return summary


def save_plot(path, summary):
    frequency_khz = np.array([row["freq_hz"] for row in summary]) / 1000.0
    fig, axes = plt.subplots(2, 2, figsize=(11, 7.5), sharex=True)
    series = [
        ("R_body", "Body R (ohm)"),
        ("X_body", "Body X (ohm)"),
        ("Z_body", "Body |Z| (ohm)"),
        ("phase_body", "Body phase (deg)"),
    ]
    for axis, (field, label) in zip(axes.flat, series):
        mean = np.array([row[f"{field}_mean"] for row in summary])
        low = np.array([row[f"{field}_ci95_low"] for row in summary])
        high = np.array([row[f"{field}_ci95_high"] for row in summary])
        axis.plot(frequency_khz, mean, marker="o", markersize=3, linewidth=1.5)
        axis.fill_between(frequency_khz, low, high, alpha=0.22)
        outside = np.array([row["inside_calibration_hull_pct"] < 50.0 for row in summary])
        axis.scatter(frequency_khz[outside], mean[outside], color="tab:red", marker="x", zorder=3)
        axis.set_ylabel(label)
        axis.grid(alpha=0.25)
    axes[1, 0].set_xlabel("Frequency (kHz)")
    axes[1, 1].set_xlabel("Frequency (kHz)")
    measurement_name = path.stem.removesuffix("_calibrated")
    fig.suptitle(f"{measurement_name}: calibrated body impedance (red x = calibration extrapolation)")
    fig.tight_layout()
    fig.savefig(path, dpi=180)
    plt.close(fig)


def mean_absolute(rows, field):
    return float(np.mean([abs(row[field]) for row in rows]))


def main():
    parser = argparse.ArgumentParser(
        description="Fit the eight SER10K 2R1C loads, validate on withheld VALID01/02/03, and convert a measurement."
    )
    parser.add_argument("measurement", type=Path)
    parser.add_argument("--reference-dir", type=Path, default=ROOT / "calibration" / "reference_loads")
    parser.add_argument("--output-dir", type=Path, default=LOG_DIR / "ser10k_2r1c_results")
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)

    calibration_data = []
    for tag, filename, r1, r2, capacitance in CALIBRATION_LOADS:
        sweeps = parse_complete_sweeps(args.reference_dir / filename)
        calibration_data.append(
            {
                "tag": tag,
                "sweeps": sweeps,
                "raw_mean": mean_raw_by_frequency(sweeps),
                "z_theory": theoretical_impedance(r1, r2, capacitance),
            }
        )

    models, coefficient_rows = build_models(calibration_data)
    write_rows(args.output_dir / "ser10k_2r1c_model_coefficients.csv", coefficient_rows)

    validation_results = {}
    for tag, valid_filename, r1, r2, capacitance in VALIDATION_LOADS:
        valid_sweeps = parse_complete_sweeps(args.reference_dir / valid_filename)
        rows = validation_rows(
            valid_sweeps, theoretical_impedance(r1, r2, capacitance), models
        )
        validation_results[tag] = rows
        write_rows(args.output_dir / f"{tag.lower()}_prediction.csv", rows)

    measurement_sweeps = parse_complete_sweeps(args.measurement)
    detail_rows = measurement_rows(measurement_sweeps, models)
    summary_rows = summarize_measurement(detail_rows)
    stem = args.measurement.stem
    detail_path = args.output_dir / f"{stem}_calibrated_detail.csv"
    summary_path = args.output_dir / f"{stem}_calibrated_summary.csv"
    plot_path = args.output_dir / f"{stem}_calibrated.png"
    write_rows(detail_path, detail_rows)
    write_rows(summary_path, summary_rows)
    save_plot(plot_path, summary_rows)

    report = args.output_dir / f"{stem}_report.txt"
    report.write_text(
        "\n".join(
            [
                f"measurement={args.measurement}",
                f"complete_sweeps={len(measurement_sweeps)}",
                "model=per-frequency affine raw Real/Imag to complex admittance",
                f"load_topology={LOAD_TOPOLOGY}",
                f"external_series_resistor_ohm={SERIES_RESISTOR_OHM:.1f}",
                *[
                    f"{tag}_{metric}={mean_absolute(rows, field):.4f}"
                    for tag, rows in validation_results.items()
                    for metric, field in (
                        ("R_total_MAPE_pct", "R_total_error_pct"),
                        ("R_load_MAPE_pct", "R_load_error_pct"),
                        ("X_MAPE_pct", "X_error_pct"),
                        ("Z_MAPE_pct", "Z_error_pct"),
                        ("phase_MAE_deg", "phase_error_deg"),
                    )
                ],
                "human_majority_in_calibration_hull_freq_hz="
                + ",".join(
                    str(row["freq_hz"])
                    for row in summary_rows
                    if row["inside_calibration_hull_pct"] >= 50.0
                ),
                "coverage_note=Frequencies outside the calibration hull are exploratory extrapolations, not validated absolute R/X.",
                "note=95% CI reflects repeated-sweep random variation only, not calibration systematic uncertainty.",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    print(f"complete_measurement_sweeps={len(measurement_sweeps)}")
    for tag, rows in validation_results.items():
        print(f"{tag.lower()}_z_mape_pct={mean_absolute(rows, 'Z_error_pct'):.4f}")
    print(f"detail={detail_path}")
    print(f"summary={summary_path}")
    print(f"plot={plot_path}")
    print(f"report={report}")


if __name__ == "__main__":
    main()
