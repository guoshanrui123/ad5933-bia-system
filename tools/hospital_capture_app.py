"""Hospital AD5933 human-baseline acquisition with descriptive QC and complete data retention."""

from __future__ import annotations

import argparse
import copy
import csv
import json
import math
import os
import queue
import re
import shutil
import sys
import threading
import time
import uuid
from pathlib import Path
import tkinter as tk
from tkinter import messagebox, ttk

import numpy as np
import serial
from openpyxl import load_workbook
from openpyxl.utils import get_column_letter, range_boundaries
from serial.tools import list_ports

import hospital_calibration_core as calibration
import human_qc_core
import postprocess_5min_human


CAPTURE_SECONDS = 300.0
BAUD_RATE = 115200
FORMAL_FREQUENCIES = (10_000, 15_000, 20_000)
BOUNDARY_FREQUENCIES = (5_000, 25_000)
REPORT_FREQUENCIES = (5_000, 10_000, 15_000, 20_000, 25_000)
MINIMUM_HULL_COVERAGE_PCT = 80.0
WORKBOOK_NAME = "医院BIA人体校零记录表.xlsx"
EDEMA_GRADE_VALUES = ("0", "1", "2", "3", "4")
EXPECTED_FREQUENCIES = set(int(value) for value in human_qc_core.FREQUENCIES)


SESSION_HEADERS = [
    "record_id", "participant_id", "session_id", "collection_date", "start_time", "end_time",
    "hospital_code", "operator_id", "cohort", "clinical_edema_grade", "affected_side", "measurement_side",
    "repeat_index", "sex", "age_years", "gestational_age_weeks", "parity_count", "height_cm", "weight_kg",
    "bmi_kg_m2", "systolic_bp_mmhg", "diastolic_bp_mmhg", "urine_protein_dipstick",
    "skin_temperature_c", "calf_circumference_cm", "posture", "skin_preparation", "electrode_lot",
    "com_port", "hardware_range", "pga_gain", "electrode_mode", "external_series_resistor_ohm",
    "capture_duration_s", "complete_sweeps", "selected_valid_sweeps", "rejected_sweeps", "anomaly_pct",
    "multistate_detected", "state_separation_pct", "max_clean_magnitude_cv_pct", "acquisition_qc",
    "domain_10_20_status", "boundary_5khz_coverage_pct", "boundary_25khz_coverage_pct", "final_decision",
    "failure_reasons", "raw_txt_relpath", "qc_result_relpath", "recorded_at", "notes", "collection_mode",
]

FREQUENCY_HEADERS = [
    "record_id", "participant_id", "session_id", "frequency_hz", "analysis_role", "selected_sweeps",
    "raw_real_mean", "raw_real_median", "raw_real_trimmed_mean_10pct", "raw_imag_mean", "raw_imag_median",
    "raw_imag_trimmed_mean_10pct", "raw_magnitude_mean", "raw_magnitude_median", "raw_magnitude_cv_pct",
    "r_body_mean_ohm", "r_body_median_ohm", "r_body_trimmed_mean_10pct_ohm", "x_body_mean_ohm",
    "x_body_median_ohm", "x_body_trimmed_mean_10pct_ohm", "z_body_mean_ohm", "phase_body_mean_deg",
    "inside_calibration_hull_pct", "domain_status",
]

SWEEP_HEADERS = [
    "record_id", "participant_id", "session_id", "sweep_id", "frequency_hz", "analysis_role",
    "raw_real", "raw_imag", "raw_magnitude", "r_body_ohm", "x_body_ohm", "z_body_ohm",
    "phase_body_deg", "qc_rejected", "selected_for_summary", "inside_calibration_hull",
    "domain_status",
]


def application_root() -> Path:
    override = os.environ.get("AD5933_HOSPITAL_PACKAGE_ROOT", "").strip()
    if override:
        return Path(override).resolve()
    if getattr(sys, "frozen", False):
        executable_dir = Path(sys.executable).resolve().parent
        for candidate in (executable_dir, executable_dir.parent, executable_dir.parent.parent):
            if (candidate / WORKBOOK_NAME).is_file() and (candidate / "raw_txt").is_dir():
                return candidate
        return executable_dir
    project_root = Path(__file__).resolve().parents[1]
    return project_root / "logs" / "0904-医院校准数据"


def safe_component(value: str) -> str:
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", str(value).strip())
    return value.rstrip(". ")


def optional_number(value: str, integer=False):
    value = str(value).strip()
    if not value:
        return None
    number = float(value)
    return int(number) if integer else number


def trimmed_mean(values, proportion=0.10):
    ordered = np.sort(np.asarray(values, dtype=float))
    cut = int(math.floor(len(ordered) * proportion))
    kept = ordered[cut:len(ordered) - cut] if cut else ordered
    return float(np.mean(kept)) if len(kept) else math.nan


def write_csv(path: Path, rows: list[dict], fieldnames=None):
    if not rows and not fieldnames:
        return
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]) if rows else list(fieldnames))
        writer.writeheader()
        if rows:
            writer.writerows(rows)


def relative_to_package(path: Path, root: Path):
    try:
        return str(path.resolve().relative_to(root.resolve())).replace("\\", "/")
    except ValueError:
        return str(path.resolve())


def evaluate_sweeps(sweeps: dict, models) -> dict:
    sweep_ids = sorted(sweeps)
    if not sweep_ids:
        return {
            "status": "WAITING", "complete": 0, "valid": 0, "selected": [],
            "rejected": set(), "multistate": False, "max_cv": None,
            "domain_pass": None, "coverage": {}, "message": "等待第一个完整扫频",
        }

    band = list(REPORT_FREQUENCIES)
    raw = np.array([[sweeps[sweep_id][frequency] for frequency in band] for sweep_id in sweep_ids])
    log_magnitude = np.log(np.maximum(np.abs(raw), 1e-12))
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

    multistate, minority_ids, separation = human_qc_core.detect_multistate(levels, sweep_ids)
    rejected = {
        sweep_ids[index] for index in range(len(sweep_ids))
        if level_flags[index] or shape_flags[index]
    } | minority_ids
    valid_ids = [sweep_id for sweep_id in sweep_ids if sweep_id not in rejected]
    selected = [] if multistate else valid_ids[:30]
    valid_so_far = len(valid_ids)
    anomaly_pct = len(rejected) / len(sweep_ids) * 100.0

    max_cv = None
    coverage = {}
    clean_pass = None
    domain_pass = None
    if selected:
        cv_values = []
        for frequency in REPORT_FREQUENCIES:
            values = np.asarray([sweeps[sweep_id][frequency] for sweep_id in selected])
            magnitudes = np.abs(values)
            cv_values.append(float(np.std(magnitudes, ddof=1) / np.mean(magnitudes) * 100.0))
            model = calibration.model_for_frequency(models, frequency)
            inside = [
                calibration.point_in_convex_hull((value.real, value.imag), model[3])
                for value in values
            ]
            coverage[frequency] = float(np.mean(inside) * 100.0)
        max_cv = max(cv_values)
        clean_pass = max_cv <= 5.0
        domain_pass = all(coverage.get(frequency, 0.0) >= MINIMUM_HULL_COVERAGE_PCT
                          for frequency in FORMAL_FREQUENCIES)

    if multistate:
        status = "WARNING"
        message = f"持续多状态风险（分离约{separation:.1f}%）"
    elif selected and clean_pass and domain_pass:
        status = "PROVISIONAL PASS"
        message = "已找到30个有效轮；CV与10–20 kHz域暂时合格"
    elif selected:
        status = "WARNING"
        issues = []
        if not clean_pass:
            issues.append(f"最大CV {max_cv:.2f}%")
        if not domain_pass:
            issues.append("10–20 kHz域覆盖不足")
        message = "；".join(issues)
    elif rejected:
        status = "WARNING"
        message = f"已见异常轮；当前有效{valid_so_far}/30"
    else:
        status = "WAITING"
        message = f"当前有效{valid_so_far}/30"

    return {
        "status": status, "complete": len(sweep_ids), "valid": valid_so_far,
        "selected": selected, "rejected": rejected, "anomaly_pct": anomaly_pct,
        "multistate": multistate, "state_separation_pct": separation,
        "max_cv": max_cv, "domain_pass": domain_pass, "coverage": coverage,
        "message": message,
    }


def build_frequency_rows(sweeps, selected_ids, models, record_id, participant_id, session_id):
    rows = []
    frequency_indices = {int(frequency): index for index, frequency in enumerate(calibration.FREQUENCIES)}
    calibrated_by_sweep = {}
    for sweep_id in selected_ids:
        raw_all = np.asarray([sweeps[sweep_id][int(frequency)] for frequency in calibration.FREQUENCIES])
        total = calibration.predict_total_impedance(raw_all, models)
        calibrated_by_sweep[sweep_id] = total - calibration.SERIES_RESISTOR_OHM

    for frequency in REPORT_FREQUENCIES:
        raw = np.asarray([sweeps[sweep_id][frequency] for sweep_id in selected_ids])
        index = frequency_indices[frequency]
        body = np.asarray([calibrated_by_sweep[sweep_id][index] for sweep_id in selected_ids])
        magnitude = np.abs(raw)
        hull = calibration.model_for_frequency(models, frequency)[3]
        inside = [calibration.point_in_convex_hull((value.real, value.imag), hull) for value in raw]
        coverage = float(np.mean(inside) * 100.0)
        role = "formal" if frequency in FORMAL_FREQUENCIES else "boundary"
        rows.append({
            "record_id": record_id,
            "participant_id": participant_id,
            "session_id": session_id,
            "frequency_hz": frequency,
            "analysis_role": role,
            "selected_sweeps": len(selected_ids),
            "raw_real_mean": float(np.mean(raw.real)),
            "raw_real_median": float(np.median(raw.real)),
            "raw_real_trimmed_mean_10pct": trimmed_mean(raw.real),
            "raw_imag_mean": float(np.mean(raw.imag)),
            "raw_imag_median": float(np.median(raw.imag)),
            "raw_imag_trimmed_mean_10pct": trimmed_mean(raw.imag),
            "raw_magnitude_mean": float(np.mean(magnitude)),
            "raw_magnitude_median": float(np.median(magnitude)),
            "raw_magnitude_cv_pct": float(np.std(magnitude, ddof=1) / np.mean(magnitude) * 100.0),
            "r_body_mean_ohm": float(np.mean(body.real)),
            "r_body_median_ohm": float(np.median(body.real)),
            "r_body_trimmed_mean_10pct_ohm": trimmed_mean(body.real),
            "x_body_mean_ohm": float(np.mean(body.imag)),
            "x_body_median_ohm": float(np.median(body.imag)),
            "x_body_trimmed_mean_10pct_ohm": trimmed_mean(body.imag),
            "z_body_mean_ohm": float(np.mean(np.abs(body))),
            "phase_body_mean_deg": float(np.mean(np.degrees(np.angle(body)))),
            "inside_calibration_hull_pct": coverage,
            "domain_status": "INSIDE" if coverage >= MINIMUM_HULL_COVERAGE_PCT else "OUTSIDE",
        })
    return rows


def build_sweep_rows(sweeps, selected_ids, rejected_ids, models, record_id, participant_id, session_id):
    """Retain every complete sweep so human ranges and state changes can be derived later."""
    rows = []
    selected_ids = set(int(value) for value in selected_ids)
    rejected_ids = set(int(value) for value in rejected_ids)
    for sweep_id in sorted(sweeps):
        raw_all = np.asarray([sweeps[sweep_id][int(frequency)] for frequency in calibration.FREQUENCIES])
        body_all = calibration.predict_total_impedance(raw_all, models) - calibration.SERIES_RESISTOR_OHM
        for index, frequency in enumerate(calibration.FREQUENCIES):
            frequency = int(frequency)
            raw = raw_all[index]
            body = body_all[index]
            hull = calibration.model_for_frequency(models, frequency)[3]
            inside = calibration.point_in_convex_hull((raw.real, raw.imag), hull)
            role = "formal" if frequency in FORMAL_FREQUENCIES else (
                "boundary" if frequency in BOUNDARY_FREQUENCIES else "diagnostic"
            )
            rows.append({
                "record_id": record_id,
                "participant_id": participant_id,
                "session_id": session_id,
                "sweep_id": sweep_id,
                "frequency_hz": frequency,
                "analysis_role": role,
                "raw_real": float(raw.real),
                "raw_imag": float(raw.imag),
                "raw_magnitude": float(abs(raw)),
                "r_body_ohm": float(body.real),
                "x_body_ohm": float(body.imag),
                "z_body_ohm": float(abs(body)),
                "phase_body_deg": float(np.degrees(np.angle(body))),
                "qc_rejected": int(sweep_id in rejected_ids),
                "selected_for_summary": int(sweep_id in selected_ids),
                "inside_calibration_hull": int(inside),
                "domain_status": "INSIDE" if inside else "OUTSIDE",
            })
    return rows


def run_postprocessor(raw_path: Path, qc_dir: Path):
    old_argv = sys.argv[:]
    try:
        sys.argv = [str(Path(postprocess_5min_human.__file__)), str(raw_path), "--out", str(qc_dir)]
        return postprocess_5min_human.main()
    finally:
        sys.argv = old_argv


def finalize_recording(raw_path: Path, qc_dir: Path, metadata: dict, models,
                       package_root: Path, interrupted=False):
    qc_dir.mkdir(parents=True, exist_ok=True)
    acquisition_code = run_postprocessor(raw_path, qc_dir)
    report_path = qc_dir / "qc_report.json"
    acquisition_report = json.loads(report_path.read_text(encoding="utf-8"))
    selected_ids = [int(value) for value in acquisition_report.get("selected_valid_sweep_ids", [])]
    sweeps = human_qc_core.parse_complete_sweeps(raw_path)
    rejected_sweeps = [int(value) for value in acquisition_report.get("rejected_sweep_ids", [])]

    frequency_rows = []
    if selected_ids:
        frequency_rows = build_frequency_rows(
            sweeps, selected_ids, models, metadata["record_id"],
            metadata["participant_id"], metadata["session_id"],
        )
    write_csv(qc_dir / "calibrated_frequency_summary.csv", frequency_rows, FREQUENCY_HEADERS)
    sweep_rows = build_sweep_rows(
        sweeps, selected_ids, rejected_sweeps, models, metadata["record_id"],
        metadata["participant_id"], metadata["session_id"],
    )
    write_csv(qc_dir / "calibrated_sweep_level.csv", sweep_rows, SWEEP_HEADERS)

    coverage = {row["frequency_hz"]: row["inside_calibration_hull_pct"] for row in frequency_rows}
    domain_pass = bool(frequency_rows) and all(
        coverage.get(frequency, 0.0) >= MINIMUM_HULL_COVERAGE_PCT
        for frequency in FORMAL_FREQUENCIES
    )
    acquisition_pass = acquisition_code == 0 and acquisition_report.get("final_decision") == "PASS"
    failure_reasons = list(acquisition_report.get("failure_reasons", []))
    if interrupted:
        failure_reasons.append("capture_interrupted")
        acquisition_pass = False
    failure_reasons = list(dict.fromkeys(failure_reasons))
    # During human-baseline collection the frozen circuit-load hull is descriptive only.
    # It must not censor the human distribution that this phase is intended to learn.
    final_pass = acquisition_pass and not interrupted

    complete = int(acquisition_report.get("complete_sweeps", len(sweeps)))
    anomaly_pct = acquisition_report.get("anomaly_pct")
    final_report = {
        **acquisition_report,
        "record_id": metadata["record_id"],
        "participant_id": metadata["participant_id"],
        "session_id": metadata["session_id"],
        "collection_mode": metadata.get("collection_mode", "baseline"),
        "load_topology": calibration.LOAD_TOPOLOGY,
        "external_series_resistor_ohm": calibration.SERIES_RESISTOR_OHM,
        "formal_frequency_band_hz": list(FORMAL_FREQUENCIES),
        "boundary_frequency_hz": list(BOUNDARY_FREQUENCIES),
        "calibration_hull_coverage_pct": {str(key): value for key, value in coverage.items()},
        "domain_10_20_status": "PASS" if domain_pass else "FAIL",
        "acquisition_qc": "PASS" if acquisition_pass else "FAIL",
        "failure_reasons": failure_reasons,
        "final_decision": "PASS" if final_pass else "FAIL",
    }
    (qc_dir / "final_report.json").write_text(
        json.dumps(final_report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    report_lines = [
        f"record_id={metadata['record_id']}",
        f"participant_id={metadata['participant_id']}",
        f"session_id={metadata['session_id']}",
        f"complete_sweeps={complete}",
        f"selected_valid_sweeps={len(selected_ids)}",
        f"acquisition_qc={final_report['acquisition_qc']}",
        f"domain_10_20_status={final_report['domain_10_20_status']}",
        "boundary_5khz_coverage_pct=" + str(coverage.get(5_000, "NA")),
        "boundary_25khz_coverage_pct=" + str(coverage.get(25_000, "NA")),
        "failure_reasons=" + ";".join(failure_reasons),
        f"final_decision={final_report['final_decision']}",
    ]
    (qc_dir / "final_report.txt").write_text("\n".join(report_lines) + "\n", encoding="utf-8")

    session_row = dict(metadata)
    session_row.update({
        "capture_duration_s": metadata.get("capture_duration_s", CAPTURE_SECONDS),
        "complete_sweeps": complete,
        "selected_valid_sweeps": len(selected_ids),
        "rejected_sweeps": ",".join(str(value) for value in rejected_sweeps),
        "anomaly_pct": anomaly_pct,
        "multistate_detected": int(bool(acquisition_report.get("multistate_detected"))),
        "state_separation_pct": acquisition_report.get("state_separation_pct"),
        "max_clean_magnitude_cv_pct": acquisition_report.get("maximum_clean_magnitude_cv_pct"),
        "acquisition_qc": final_report["acquisition_qc"],
        "domain_10_20_status": final_report["domain_10_20_status"],
        "boundary_5khz_coverage_pct": coverage.get(5_000),
        "boundary_25khz_coverage_pct": coverage.get(25_000),
        "final_decision": final_report["final_decision"],
        "failure_reasons": ";".join(failure_reasons),
        "raw_txt_relpath": relative_to_package(raw_path, package_root),
        "qc_result_relpath": relative_to_package(qc_dir, package_root),
        "recorded_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    })
    payload = {"session": session_row, "frequency": frequency_rows, "sweep": sweep_rows}
    return final_report, payload


def archive_failed_formal_measurement(raw_path: Path, qc_dir: Path, final_report: dict,
                                      payload: dict, package_root: Path):
    """Move a completed formal FAIL out of the model-data folders."""
    failed_root = package_root / "failed_tests"
    failed_raw = failed_root / "raw_txt" / raw_path.name
    failed_qc = failed_root / "qc_results" / qc_dir.name
    failed_raw.parent.mkdir(parents=True, exist_ok=True)
    failed_qc.parent.mkdir(parents=True, exist_ok=True)
    if failed_raw.exists() or failed_qc.exists():
        raise FileExistsError(f"失败记录归档目标已存在：{failed_raw} 或 {failed_qc}")
    shutil.move(str(raw_path), str(failed_raw))
    try:
        shutil.move(str(qc_dir), str(failed_qc))
    except Exception:
        shutil.move(str(failed_raw), str(raw_path))
        raise

    raw_relpath = relative_to_package(failed_raw, package_root)
    qc_relpath = relative_to_package(failed_qc, package_root)
    payload["session"]["raw_txt_relpath"] = raw_relpath
    payload["session"]["qc_result_relpath"] = qc_relpath
    final_report["raw_txt_relpath"] = raw_relpath
    final_report["qc_result_relpath"] = qc_relpath
    (failed_qc / "final_report.json").write_text(
        json.dumps(final_report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return failed_raw, failed_qc


def copy_template_row(ws, source_row: int, target_row: int, max_column: int):
    if target_row == source_row:
        return
    for column in range(1, max_column + 1):
        source = ws.cell(source_row, column)
        target = ws.cell(target_row, column)
        if source.has_style:
            target._style = copy.copy(source._style)
        if source.number_format:
            target.number_format = source.number_format
        target.alignment = copy.copy(source.alignment)


def next_data_row(ws, id_value: str):
    for row in range(5, ws.max_row + 1):
        if str(ws.cell(row, 1).value or "") == id_value:
            return None
    for row in range(5, ws.max_row + 1):
        if ws.cell(row, 1).value in (None, ""):
            return row
    return max(ws.max_row + 1, 5)


def extend_tables(ws, last_row: int):
    for table in ws.tables.values():
        min_col, min_row, max_col, _ = range_boundaries(table.ref)
        table.ref = f"{get_column_letter(min_col)}{min_row}:{get_column_letter(max_col)}{last_row}"


def append_payload_to_workbook(workbook_path: Path, payload: dict):
    session = payload.get("session", {})
    if session.get("collection_mode") != "baseline":
        raise ValueError("Only human-baseline records may be written to the baseline workbook")
    workbook = load_workbook(workbook_path)
    sessions = workbook["Sessions"]
    frequency = workbook["Frequency_Summary"]
    sweep_level = workbook["Sweep_Level"]

    record_id = str(payload["session"]["record_id"])
    session_row_index = next_data_row(sessions, record_id)
    if session_row_index is not None:
        copy_template_row(sessions, 5, session_row_index, len(SESSION_HEADERS))
        for column, header in enumerate(SESSION_HEADERS, 1):
            value = payload["session"].get(header)
            if header == "collection_mode" and not value:
                value = "baseline"
            sessions.cell(session_row_index, column).value = value
        extend_tables(sessions, max(session_row_index, 5))

    existing_frequency = {
        (str(frequency.cell(row, 1).value or ""), frequency.cell(row, 4).value)
        for row in range(5, frequency.max_row + 1)
    }
    last_frequency_row = 5
    for item in payload.get("frequency", []):
        key = (record_id, item.get("frequency_hz"))
        if key in existing_frequency:
            continue
        row_index = next_data_row(frequency, "__never_match__")
        copy_template_row(frequency, 5, row_index, len(FREQUENCY_HEADERS))
        for column, header in enumerate(FREQUENCY_HEADERS, 1):
            frequency.cell(row_index, column).value = item.get(header)
        existing_frequency.add(key)
        last_frequency_row = max(last_frequency_row, row_index)
    extend_tables(frequency, max(last_frequency_row, frequency.max_row, 5))

    existing_sweeps = {
        (str(sweep_level.cell(row, 1).value or ""), sweep_level.cell(row, 4).value,
         sweep_level.cell(row, 5).value)
        for row in range(5, sweep_level.max_row + 1)
    }
    last_sweep_row = 5
    for item in payload.get("sweep", []):
        key = (record_id, item.get("sweep_id"), item.get("frequency_hz"))
        if key in existing_sweeps:
            continue
        row_index = next_data_row(sweep_level, "__never_match__")
        copy_template_row(sweep_level, 5, row_index, len(SWEEP_HEADERS))
        for column, header in enumerate(SWEEP_HEADERS, 1):
            sweep_level.cell(row_index, column).value = item.get(header)
        existing_sweeps.add(key)
        last_sweep_row = max(last_sweep_row, row_index)
    extend_tables(sweep_level, max(last_sweep_row, sweep_level.max_row, 5))

    temporary = workbook_path.with_name(workbook_path.stem + ".writing.xlsx")
    workbook.save(temporary)
    try:
        os.replace(temporary, workbook_path)
    except Exception:
        if temporary.exists():
            temporary.unlink()
        raise


def save_pending_payload(root: Path, payload: dict):
    pending = root / "pending_excel_records"
    pending.mkdir(parents=True, exist_ok=True)
    path = pending / f"{safe_component(payload['session']['record_id'])}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def write_or_queue_excel(root: Path, payload: dict):
    session = payload.get("session", {})
    if session.get("collection_mode") != "baseline":
        return True, "ineligible_no_excel"
    workbook_path = root / WORKBOOK_NAME
    try:
        append_payload_to_workbook(workbook_path, payload)
        return True, str(workbook_path)
    except Exception as exc:
        pending = save_pending_payload(root, payload)
        return False, f"{exc}; pending={pending}"


def sync_pending(root: Path):
    pending = root / "pending_excel_records"
    if not pending.is_dir():
        return 0, []
    synced = 0
    errors = []
    for path in sorted(pending.glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            session = payload.get("session", {})
            if session.get("collection_mode") != "baseline":
                rejected = root / "failed_tests" / "pending_excel_records"
                rejected.mkdir(parents=True, exist_ok=True)
                shutil.move(str(path), str(rejected / path.name))
                continue
            append_payload_to_workbook(root / WORKBOOK_NAME, payload)
            path.unlink()
            synced += 1
        except Exception as exc:
            errors.append(f"{path.name}: {exc}")
    return synced, errors


def metadata_for_existing(participant_id: str, side: str, repeat_index: int, raw_path: Path):
    stamp = time.strftime("%Y%m%d_%H%M%S")
    participant_id = safe_component(participant_id)
    session_id = f"{participant_id}_{side}_R{repeat_index}_{stamp}"
    return {
        "record_id": str(uuid.uuid4()), "participant_id": participant_id, "session_id": session_id,
        "collection_date": time.strftime("%Y-%m-%d"), "start_time": "", "end_time": "",
        "hospital_code": "TEST", "operator_id": "TEST", "cohort": "unknown",
        "clinical_edema_grade": "unknown", "affected_side": "unknown", "measurement_side": side,
        "repeat_index": repeat_index, "sex": "unknown", "age_years": None,
        "gestational_age_weeks": None, "parity_count": None, "height_cm": None,
        "weight_kg": None, "bmi_kg_m2": None, "systolic_bp_mmhg": None,
        "diastolic_bp_mmhg": None, "urine_protein_dipstick": "",
        "skin_temperature_c": None, "calf_circumference_cm": None,
        "posture": "unknown", "skin_preparation": "historical",
        "electrode_lot": "", "com_port": "historical_file",
        "hardware_range": "Range4", "pga_gain": "x1", "electrode_mode": "two-electrode",
        "external_series_resistor_ohm": 10000, "capture_duration_s": None,
        "notes": f"Historical regression: {raw_path.name}", "collection_mode": "test",
    }


class HospitalCaptureApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.package_root = application_root()
        self.models = calibration.build_engine(self.package_root / "calibration_reference")
        self.events = queue.Queue()
        self.stop_event = threading.Event()
        self.running = False
        self.vars = {}

        root.title("AD5933 医院人体校零数据采集")
        root.geometry("1060x820")
        root.minsize(940, 720)
        style = ttk.Style()
        style.configure("Status.TLabel", font=("Microsoft YaHei UI", 15, "bold"), padding=10)

        outer = ttk.Frame(root, padding=14)
        outer.pack(fill="both", expand=True)
        ttk.Label(outer, text="AD5933 医院人体校零数据采集（五分钟）",
                  font=("Microsoft YaHei UI", 17, "bold")).pack(anchor="w")
        ttk.Label(outer, text="建立人体经验参考范围｜全量保存完整扫频｜QC与旧校准域仅作标记，不删除人体记录",
                  foreground="#24557A").pack(anchor="w", pady=(2, 10))

        notebook = ttk.Notebook(outer)
        notebook.pack(fill="x")
        basic = ttk.Frame(notebook, padding=12)
        measurement = ttk.Frame(notebook, padding=12)
        notebook.add(basic, text="受试者与临床信息")
        notebook.add(measurement, text="测量信息")

        self.add_fields(basic, [
            ("采集用途*", "collection_mode", "combo", ("人体校零采集（全部写入Excel）", "设备测试（不写Excel）")),
            ("医院代码*", "hospital_code", "entry", "HOSP01"),
            ("操作者编号*", "operator_id", "entry", "OP01"),
            ("匿名受试者编号*", "participant_id", "entry", ""),
            ("分组*", "cohort", "combo", ("healthy", "patient")),
            ("医生判断凹陷性水肿等级*（0–4）", "clinical_edema_grade", "combo", EDEMA_GRADE_VALUES),
            ("患侧*", "affected_side", "combo", ("left", "right", "bilateral", "none", "unknown")),
            ("测量侧*", "measurement_side", "combo", ("left", "right")),
            ("重复次数*", "repeat_index", "entry", "1"),
            ("性别", "sex", "combo", ("female", "male", "other", "unknown")),
            ("年龄（岁）*", "age_years", "entry", ""),
            ("孕周（周）*", "gestational_age_weeks", "entry", ""),
            ("产次*", "parity_count", "entry", ""),
            ("身高（cm）*", "height_cm", "entry", ""),
            ("体重（kg）*", "weight_kg", "entry", ""),
        ])
        self.add_fields(measurement, [
            ("小腿围度（cm；选填）", "calf_circumference_cm", "entry", ""),
            ("收缩压（mmHg；选填）", "systolic_bp_mmhg", "entry", ""),
            ("舒张压（mmHg；选填）", "diastolic_bp_mmhg", "entry", ""),
            ("尿蛋白（选填）", "urine_protein_dipstick", "combo",
             ("", "negative", "trace", "1+", "2+", "3+", "4+", "unknown")),
            ("皮肤温度（°C；选填）", "skin_temperature_c", "entry", ""),
            ("体位*", "posture", "combo", ("supine", "seated", "standing")),
            ("皮肤准备*", "skin_preparation", "entry", "clean_dry"),
            ("电极批号", "electrode_lot", "entry", ""),
            ("备注", "notes", "entry", ""),
        ])

        serial_frame = ttk.Frame(outer)
        serial_frame.pack(fill="x", pady=(12, 4))
        ttk.Label(serial_frame, text="串口*").pack(side="left")
        self.port_var = tk.StringVar(value="COM5")
        self.port_box = ttk.Combobox(serial_frame, textvariable=self.port_var, width=18)
        self.port_box.pack(side="left", padx=8)
        ttk.Button(serial_frame, text="刷新串口", command=self.refresh_ports).pack(side="left")
        ttk.Label(serial_frame, text="采集前请关闭Excel记录表；若忘记关闭，结果会进入待同步队列。",
                  foreground="#7A5A00").pack(side="right")

        controls = ttk.Frame(outer)
        controls.pack(fill="x", pady=8)
        self.start_button = ttk.Button(controls, text="开始采集（5分钟）", command=self.start)
        self.start_button.pack(side="left", fill="x", expand=True)
        self.stop_button = ttk.Button(controls, text="安全停止", command=self.stop, state="disabled")
        self.stop_button.pack(side="left", padx=(8, 0))

        self.progress = ttk.Progressbar(outer, maximum=CAPTURE_SECONDS)
        self.progress.pack(fill="x", pady=(2, 6))
        self.status_var = tk.StringVar(value="准备就绪")
        self.status_label = ttk.Label(outer, textvariable=self.status_var, style="Status.TLabel")
        self.status_label.pack(fill="x")
        self.metrics_var = tk.StringVar(value="完整轮 0｜当前有效 0/30｜最大CV —｜10–20 kHz域 —")
        ttk.Label(outer, textvariable=self.metrics_var).pack(anchor="w", pady=(3, 6))

        self.log = tk.Text(outer, height=12, wrap="word", state="disabled", font=("Consolas", 9))
        self.log.pack(fill="both", expand=True)
        self.refresh_ports()
        synced, errors = sync_pending(self.package_root)
        if synced:
            self.append_log(f"已自动同步{synced}条待写Excel记录。")
        if errors:
            self.append_log("待同步记录仍未写入：" + " | ".join(errors))
        self.root.after(100, self.poll_events)
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

    def add_fields(self, parent, definitions):
        for index, (label, key, kind, default) in enumerate(definitions):
            row = index // 3
            column = (index % 3) * 2
            ttk.Label(parent, text=label).grid(row=row, column=column, sticky="w", padx=(0, 6), pady=5)
            if kind == "combo":
                values = tuple(default)
                variable = tk.StringVar(value=values[0])
                widget = ttk.Combobox(parent, textvariable=variable, values=values, state="readonly", width=18)
            else:
                variable = tk.StringVar(value=str(default))
                widget = ttk.Entry(parent, textvariable=variable, width=22)
            widget.grid(row=row, column=column + 1, sticky="ew", padx=(0, 16), pady=5)
            self.vars[key] = variable
        for column in (1, 3, 5):
            parent.columnconfigure(column, weight=1)

    def refresh_ports(self):
        ports = [item.device for item in list_ports.comports()]
        self.port_box["values"] = ports
        if ports and self.port_var.get() not in ports:
            self.port_var.set(ports[0])

    def append_log(self, text):
        self.log.configure(state="normal")
        self.log.insert("end", str(text) + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def collect_metadata(self):
        values = {key: variable.get().strip() for key, variable in self.vars.items()}
        collection_mode = "baseline" if values["collection_mode"].startswith("人体校零") else "test"
        required = ["hospital_code", "operator_id", "measurement_side", "repeat_index", "posture", "skin_preparation"]
        if collection_mode == "baseline":
            required.extend([
                "participant_id", "cohort", "clinical_edema_grade", "affected_side",
                "age_years", "gestational_age_weeks", "parity_count", "height_cm", "weight_kg",
            ])
        missing = [key for key in required if not values.get(key)]
        if missing:
            raise ValueError("缺少必填字段：" + ", ".join(missing))
        participant = safe_component(values["participant_id"]) if collection_mode == "baseline" else "DEVICE_TEST"
        if collection_mode == "baseline" and not participant:
            raise ValueError("匿名受试者编号无效")
        repeat_index = optional_number(values["repeat_index"], integer=True)
        if repeat_index is None or repeat_index < 1:
            raise ValueError("重复次数必须为正整数")
        age = optional_number(values["age_years"], integer=True)
        gestational_age = optional_number(values["gestational_age_weeks"])
        parity = optional_number(values["parity_count"], integer=True)
        height = optional_number(values["height_cm"])
        weight = optional_number(values["weight_kg"])
        systolic_bp = optional_number(values["systolic_bp_mmhg"])
        diastolic_bp = optional_number(values["diastolic_bp_mmhg"])
        skin_temperature = optional_number(values["skin_temperature_c"])
        if collection_mode == "baseline" and (age is None or not 10 <= age <= 60):
            raise ValueError("年龄必须在10至60岁之间")
        if collection_mode == "baseline" and (gestational_age is None or not 0 < gestational_age <= 45):
            raise ValueError("孕周必须在0至45周之间")
        if collection_mode == "baseline" and (parity is None or not 0 <= parity <= 20):
            raise ValueError("产次必须为0至20之间的整数")
        if collection_mode == "baseline" and (height is None or not 100 <= height <= 220):
            raise ValueError("身高必须在100至220 cm之间")
        if collection_mode == "baseline" and (weight is None or not 25 <= weight <= 300):
            raise ValueError("体重必须在25至300 kg之间")
        if (systolic_bp is None) != (diastolic_bp is None):
            raise ValueError("血压为选填，但收缩压和舒张压必须同时填写或同时留空")
        if systolic_bp is not None:
            if not 50 <= systolic_bp <= 300 or not 30 <= diastolic_bp <= 200:
                raise ValueError("血压超出允许录入范围")
            if systolic_bp <= diastolic_bp:
                raise ValueError("收缩压必须高于舒张压")
        if skin_temperature is not None and not 20 <= skin_temperature <= 45:
            raise ValueError("皮肤温度必须在20至45°C之间")
        bmi = weight / ((height / 100.0) ** 2) if height and weight else None
        now = time.localtime()
        stamp = time.strftime("%Y%m%d_%H%M%S", now)
        side = values["measurement_side"]
        session_id = safe_component(f"{participant}_{side}_R{repeat_index}_{stamp}")
        return {
            "record_id": str(uuid.uuid4()), "participant_id": participant, "session_id": session_id,
            "collection_date": time.strftime("%Y-%m-%d", now), "start_time": time.strftime("%H:%M:%S", now),
            "end_time": "", "hospital_code": safe_component(values["hospital_code"]),
            "operator_id": safe_component(values["operator_id"]),
            "cohort": values["cohort"] if collection_mode == "baseline" else "test",
            "clinical_edema_grade": values["clinical_edema_grade"] if collection_mode == "baseline" else "unknown",
            "affected_side": values["affected_side"] if collection_mode == "baseline" else "unknown",
            "measurement_side": side, "repeat_index": repeat_index, "sex": values["sex"],
            "age_years": age, "gestational_age_weeks": gestational_age,
            "parity_count": parity, "height_cm": height, "weight_kg": weight, "bmi_kg_m2": bmi,
            "systolic_bp_mmhg": systolic_bp, "diastolic_bp_mmhg": diastolic_bp,
            "urine_protein_dipstick": values["urine_protein_dipstick"],
            "skin_temperature_c": skin_temperature,
            "calf_circumference_cm": optional_number(values["calf_circumference_cm"]),
            "posture": values["posture"], "skin_preparation": values["skin_preparation"],
            "electrode_lot": values["electrode_lot"], "com_port": self.port_var.get().strip(),
            "hardware_range": "Range4", "pga_gain": "x1", "electrode_mode": "two-electrode",
            "external_series_resistor_ohm": 10000, "capture_duration_s": CAPTURE_SECONDS,
            "notes": values["notes"], "collection_mode": collection_mode,
        }

    def start(self):
        try:
            metadata = self.collect_metadata()
        except (ValueError, TypeError) as exc:
            messagebox.showerror("字段错误", str(exc))
            return
        if not metadata["com_port"]:
            messagebox.showerror("串口错误", "请选择串口")
            return
        data_root = self.package_root if metadata["collection_mode"] == "baseline" else self.package_root / "device_tests"
        raw_path = data_root / "raw_txt" / f"{metadata['session_id']}.txt"
        qc_dir = data_root / "qc_results" / metadata["session_id"]
        self.running = True
        self.stop_event.clear()
        self.progress["value"] = 0
        self.start_button.configure(state="disabled")
        self.stop_button.configure(state="normal")
        self.status_var.set("WAITING｜等待完整扫频")
        self.status_label.configure(background="#D9EAF7", foreground="#17365D")
        self.append_log(f"原始文件：{raw_path}")
        threading.Thread(
            target=self.capture_worker,
            args=(metadata, raw_path, qc_dir), daemon=True,
        ).start()

    def stop(self):
        if self.running and messagebox.askyesno("安全停止", "停止后本次仍会保存为不完整的人体校零记录，确定吗？"):
            self.stop_event.set()

    def capture_worker(self, metadata, raw_path, qc_dir):
        try:
            port = serial.Serial(metadata["com_port"], BAUD_RATE, timeout=0.2)
            port.reset_input_buffer()
        except serial.SerialException as exc:
            self.events.put(("error", f"无法打开{metadata['com_port']}：{exc}"))
            return
        raw_path.parent.mkdir(parents=True, exist_ok=True)
        started = time.monotonic()
        deadline = started + CAPTURE_SECONDS
        buffer = ""
        partial = {}
        complete = {}
        lines = 0
        try:
            with raw_path.open("w", encoding="utf-8", newline="") as stream:
                stream.write(
                    f"# hospital_capture,duration_seconds=300,port={metadata['com_port']},baud={BAUD_RATE},"
                    f"session_id={metadata['session_id']},collection_mode={metadata['collection_mode']},"
                    f"started={time.strftime('%Y-%m-%d %H:%M:%S')}\n"
                )
                while time.monotonic() < deadline and not self.stop_event.is_set():
                    chunk = port.read(512)
                    if chunk:
                        buffer += chunk.decode("utf-8", errors="replace")
                        while "\n" in buffer:
                            line, buffer = buffer.split("\n", 1)
                            line = line.rstrip("\r")
                            if not line:
                                continue
                            stream.write(line + "\n")
                            lines += 1
                            completed = self.consume_line(line, partial, complete)
                            if completed:
                                live = evaluate_sweeps(complete, self.models)
                                self.events.put(("live", live))
                    self.events.put(("progress", min(time.monotonic() - started, CAPTURE_SECONDS)))
                if buffer.strip():
                    stream.write(buffer.rstrip("\r") + "\n")
                elapsed = time.monotonic() - started
                interrupted = self.stop_event.is_set()
                state = "interrupted" if interrupted else "complete"
                stream.write(
                    f"# hospital_capture_{state},elapsed_seconds={elapsed:.3f},lines={lines},"
                    f"complete_sweeps={len(complete)},finished={time.strftime('%Y-%m-%d %H:%M:%S')}\n"
                )
                metadata["capture_duration_s"] = elapsed
                metadata["end_time"] = time.strftime("%H:%M:%S")
        except (OSError, serial.SerialException) as exc:
            self.events.put(("error", f"采集过程中发生错误：{exc}"))
            return
        finally:
            port.close()

        if metadata["collection_mode"] == "baseline":
            self.events.put(("log", "正在整理人体校零数据；全部完整扫频与QC标签都会保存并写入Excel……"))
        else:
            self.events.put(("log", "正在执行设备测试质控；本次不会写入Excel……"))
        try:
            final_report, payload = finalize_recording(
                raw_path, qc_dir, metadata, self.models, self.package_root,
                interrupted=self.stop_event.is_set(),
            )
            if metadata["collection_mode"] == "baseline":
                excel_ok, excel_message = write_or_queue_excel(self.package_root, payload)
            else:
                excel_ok, excel_message = True, "test_mode_no_excel"
            self.events.put(("complete", final_report, str(raw_path), str(qc_dir), excel_ok, excel_message))
        except Exception as exc:
            self.events.put(("error", f"人体校零数据整理或写表失败：{exc}"))

    @staticmethod
    def consume_line(line, partial, complete):
        try:
            row = next(csv.reader([line]))
            if len(row) < 7 or row[0] != "data":
                return False
            sweep_id = int(row[2])
            frequency = int(row[4])
            value = complex(float(row[5]), float(row[6]))
        except (ValueError, csv.Error, StopIteration):
            return False
        if frequency not in EXPECTED_FREQUENCIES:
            return False
        partial.setdefault(sweep_id, {})[frequency] = value
        if set(partial[sweep_id]) == EXPECTED_FREQUENCIES and sweep_id not in complete:
            complete[sweep_id] = dict(partial[sweep_id])
            return True
        return False

    def poll_events(self):
        try:
            while True:
                event = self.events.get_nowait()
                kind = event[0]
                if kind == "progress":
                    self.progress["value"] = event[1]
                elif kind == "live":
                    live = event[1]
                    self.status_var.set(f"{live['status']}｜{live['message']}")
                    color = {"WAITING": "#D9EAF7", "WARNING": "#FFF2CC", "PROVISIONAL PASS": "#E2F0D9"}[live["status"]]
                    self.status_label.configure(background=color, foreground="#17365D")
                    cv = "—" if live["max_cv"] is None else f"{live['max_cv']:.2f}%"
                    domain = "—" if live["domain_pass"] is None else ("PASS" if live["domain_pass"] else "RISK")
                    self.metrics_var.set(
                        f"完整轮 {live['complete']}｜当前有效 {live['valid']}/30｜最大CV {cv}｜10–20 kHz域 {domain}"
                    )
                elif kind == "log":
                    self.append_log(event[1])
                elif kind == "error":
                    self.finish_ui()
                    self.status_var.set("程序错误｜本次采集未完成")
                    self.status_label.configure(background="#FCE4D6", foreground="#9C0006")
                    self.append_log(event[1])
                    messagebox.showerror("采集失败", event[1])
                elif kind == "complete":
                    self.finish_ui()
                    report, raw, qc, excel_ok, excel_message = event[1:]
                    passed = report["final_decision"] == "PASS"
                    result = "技术QC PASS" if passed else "技术QC REVIEW"
                    is_test = report.get("collection_mode") == "test"
                    if is_test:
                        detail = "设备状态正常；不进入Excel" if passed else ";".join(report["failure_reasons"])
                    else:
                        detail = "人体参考范围候选（技术QC通过）" if passed else "人体校零记录已保留，技术QC需复核"
                    self.status_var.set(result + "｜" + detail)
                    self.status_label.configure(
                        background="#E2F0D9" if passed else "#FCE4D6",
                        foreground="#375623" if passed else "#9C0006",
                    )
                    self.append_log(f"原始数据：{raw}")
                    self.append_log(f"质控结果：{qc}")
                    if is_test:
                        self.append_log("设备测试模式：未写入Excel。")
                    else:
                        self.append_log("人体校零记录已写入Excel。" if excel_ok else "Excel被占用或写入失败，已保存待同步记录：" + excel_message)
                    dialog = messagebox.showinfo if passed else messagebox.showwarning
                    if is_test:
                        dialog("设备测试完成", result + "\n原始数据和质控报告已保存\n未写入Excel")
                    else:
                        dialog("人体校零采集完成", result + "\n原始完整扫频和QC标签均已保存" + ("\nExcel已写入" if excel_ok else "\n已进入Excel待同步队列"))
        except queue.Empty:
            pass
        self.root.after(100, self.poll_events)

    def finish_ui(self):
        self.running = False
        self.start_button.configure(state="normal")
        self.stop_button.configure(state="disabled")

    def on_close(self):
        if self.running:
            messagebox.showwarning("正在采集", "请先点击安全停止并等待不完整记录保存完成。")
        else:
            self.root.destroy()


def process_existing(args):
    root = application_root()
    models = calibration.build_engine(root / "calibration_reference")
    metadata = metadata_for_existing(args.participant, args.side, args.repeat, args.input)
    output = root / "qc_results" / metadata["session_id"]
    report, payload = finalize_recording(args.input, output, metadata, models, root)
    if args.no_excel or metadata["collection_mode"] == "test":
        excel_result = (True, "skipped")
    elif metadata["collection_mode"] == "baseline":
        excel_result = write_or_queue_excel(root, payload)
    print(json.dumps({"report": report, "excel": excel_result, "qc_dir": str(output)}, ensure_ascii=False))
    return 0 if report["final_decision"] == "PASS" else 2


def main():
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--smoke-test", action="store_true")
    parser.add_argument("--process-existing", dest="input", type=Path)
    parser.add_argument("--participant", default="REGRESSION")
    parser.add_argument("--side", default="left")
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--no-excel", action="store_true")
    args, _ = parser.parse_known_args()
    if args.smoke_test:
        root = application_root()
        models = calibration.build_engine(root / "calibration_reference")
        print(f"root={root}")
        print(f"workbook={(root / WORKBOOK_NAME).is_file()}")
        print(f"models={len(models)}")
        print("smoke_test=PASS")
        return 0
    if args.input:
        return process_existing(args)
    root = tk.Tk()
    HospitalCaptureApp(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
