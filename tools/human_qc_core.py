"""Lightweight shared primitives for human acquisition QC."""

from __future__ import annotations

import csv
import math
from pathlib import Path

import numpy as np


FREQUENCIES = np.arange(5_000, 100_001, 5_000)
SERIES_RESISTOR_OHM = 10_000.0
LOAD_TOPOLOGY = "R1_parallel_(R2_series_C)"


def parse_complete_sweeps(path: Path):
    sweeps = {}
    with path.open("r", encoding="utf-8-sig", errors="ignore", newline="") as handle:
        for row in csv.reader(handle):
            if len(row) < 7 or row[0] != "data":
                continue
            try:
                sweep_id = int(row[2]); frequency = int(row[4])
                raw = complex(float(row[5]), float(row[6]))
            except ValueError:
                continue
            if frequency in FREQUENCIES:
                sweeps.setdefault(sweep_id, {})[frequency] = raw
    expected = set(int(value) for value in FREQUENCIES)
    return {key: value for key, value in sweeps.items() if set(value) == expected}


def detect_multistate(levels: np.ndarray, sweep_ids: list[int]):
    count = len(levels)
    minimum_cluster = max(3, math.ceil(0.20 * count))
    if count < 2 * minimum_cluster:
        return False, set(), None
    order = np.argsort(levels); sorted_levels = levels[order]; best = None
    for split in range(minimum_cluster, count - minimum_cluster + 1):
        low, high = sorted_levels[:split], sorted_levels[split:]
        separation = float(np.exp(np.mean(high) - np.mean(low)) - 1.0)
        if separation < 0.15:
            continue
        gap = float(sorted_levels[split] - sorted_levels[split - 1])
        low_gap = float(np.max(np.diff(low))) if len(low) > 1 else 0.0
        high_gap = float(np.max(np.diff(high))) if len(high) > 1 else 0.0
        if low_gap > 1.5 * gap or high_gap > 1.5 * gap:
            continue
        score = (separation, gap)
        if best is None or score > best[0]:
            best = (score, split, separation)
    if best is None:
        return False, set(), None
    _, split, separation = best
    low_indices, high_indices = order[:split], order[split:]
    rejected_indices = low_indices if len(low_indices) <= len(high_indices) else high_indices
    return True, {sweep_ids[int(index)] for index in rejected_indices}, separation * 100.0
