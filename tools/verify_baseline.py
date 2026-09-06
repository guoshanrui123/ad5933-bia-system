"""Offline packaging/calibration regression; not a hardware or clinical test."""

import importlib
from pathlib import Path

import numpy as np
import hospital_calibration_core as calibration
import human_qc_core


def main():
    for name in ("hospital_capture_app", "postprocess_5min_human",
                 "quality_control_session", "calibrate_ser10k_2r1c",
                 "log_5min_txt", "filter_bia_session", "plot_single_human_capture_qc"):
        importlib.import_module(name)
    reference = Path(__file__).resolve().parents[1] / "calibration" / "reference_loads"
    models = calibration.build_engine(reference)
    assert len(models) == 20
    paths = sorted(reference.glob("*VALID*.txt"))
    assert len(paths) == 3
    for path in paths:
        sweeps = human_qc_core.parse_complete_sweeps(path)
        assert sweeps, path.name
        raw = calibration.mean_raw_by_frequency(sweeps)
        prediction = calibration.predict_total_impedance(raw, models)
        assert prediction.shape == (20,) and np.isfinite(prediction).all()
        print(f"{path.name}: {len(sweeps)} complete sweeps; finite predictions")
        if "VALID03" in path.name:
            truth = calibration.theoretical_impedance(100_000, 840, 11e-9) - 10_000
            body = prediction - 10_000
            selected = calibration.FREQUENCIES <= 25_000
            error = np.abs(body[selected] - truth[selected]) / np.abs(truth[selected]) * 100
            print("VALID03 5–25 kHz complex relative error (%):", np.round(error, 3))
    print("PASS: imports, frozen calibration, independent validation parsing (not clinical acceptance)")


if __name__ == "__main__":
    main()
