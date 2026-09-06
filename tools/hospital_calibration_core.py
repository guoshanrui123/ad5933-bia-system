"""Lightweight frozen CAL01-CAL08 calibration and hull utilities."""

from __future__ import annotations

from pathlib import Path

import numpy as np

import human_qc_core


FREQUENCIES = np.asarray(human_qc_core.FREQUENCIES, dtype=int)
SERIES_RESISTOR_OHM = human_qc_core.SERIES_RESISTOR_OHM
LOAD_TOPOLOGY = human_qc_core.LOAD_TOPOLOGY

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


def theoretical_impedance(r1_ohm: float, r2_ohm: float, capacitance_f: float):
    omega = 2.0 * np.pi * FREQUENCIES
    series_rc = r2_ohm + 1.0 / (1j * omega * capacitance_f)
    load = 1.0 / (1.0 / r1_ohm + 1.0 / series_rc)
    return SERIES_RESISTOR_OHM + load


def mean_raw_by_frequency(sweeps):
    return np.array([
        np.mean([sweep[int(frequency)] for sweep in sweeps.values()])
        for frequency in FREQUENCIES
    ])


def convex_hull(points):
    points = sorted(set((float(x), float(y)) for x, y in points))
    if len(points) <= 2:
        return points

    def cross(origin, a, b):
        return ((a[0] - origin[0]) * (b[1] - origin[1])
                - (a[1] - origin[1]) * (b[0] - origin[0]))

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
        cross = ((b[0] - a[0]) * (point[1] - a[1])
                 - (b[1] - a[1]) * (point[0] - a[0]))
        signs.append(cross)
    tolerance = 1e-9
    return (all(value >= -tolerance for value in signs)
            or all(value <= tolerance for value in signs))


def build_engine(reference_dir: Path):
    calibration_data = []
    missing = []
    for tag, filename, r1, r2, capacitance in CALIBRATION_LOADS:
        path = reference_dir / filename
        if not path.is_file():
            missing.append(filename)
            continue
        sweeps = human_qc_core.parse_complete_sweeps(path)
        if not sweeps:
            raise ValueError(f"No complete sweep in frozen calibration file: {path}")
        calibration_data.append({
            "tag": tag,
            "raw_mean": mean_raw_by_frequency(sweeps),
            "z_theory": theoretical_impedance(r1, r2, capacitance),
        })
    if missing:
        raise FileNotFoundError("Missing frozen calibration files: " + ", ".join(missing))

    models = []
    for index, frequency in enumerate(FREQUENCIES):
        raw = np.array([item["raw_mean"][index] for item in calibration_data])
        admittance = np.array([1.0 / item["z_theory"][index] for item in calibration_data])
        features = np.column_stack((raw.real, raw.imag))
        center = features.mean(axis=0)
        scale = features.std(axis=0, ddof=0)
        if np.any(scale == 0):
            raise ValueError(f"Degenerate frozen calibration at {frequency} Hz")
        design = np.column_stack(((features - center) / scale, np.ones(len(features))))
        target = np.column_stack((admittance.real, admittance.imag))
        coefficients = np.linalg.lstsq(design, target, rcond=None)[0]
        hull = convex_hull(features)
        models.append((center, scale, coefficients, hull))
    return models


def predict_total_impedance(raw_values, models):
    predicted = []
    for raw, (center, scale, coefficients, _) in zip(raw_values, models):
        design = np.array([
            (raw.real - center[0]) / scale[0],
            (raw.imag - center[1]) / scale[1],
            1.0,
        ])
        y_parts = design @ coefficients
        predicted.append(1.0 / complex(y_parts[0], y_parts[1]))
    return np.asarray(predicted)


def model_for_frequency(models, frequency_hz: int):
    index = int(np.where(FREQUENCIES == int(frequency_hz))[0][0])
    return models[index]
