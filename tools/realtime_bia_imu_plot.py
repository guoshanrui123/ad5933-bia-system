import argparse
import csv
import math
import queue
import threading
import time
from collections import defaultdict, deque
from pathlib import Path

import matplotlib

# Use the native Tk window on Windows instead of Qt, which may emit unrelated
# font-plugin warnings in this environment.
matplotlib.use("TkAgg", force=True)

# Prefer an installed Windows CJK font so Chinese labels do not emit glyph warnings.
matplotlib.rcParams["font.family"] = "sans-serif"
matplotlib.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "SimSun", "DejaVu Sans"]
matplotlib.rcParams["axes.unicode_minus"] = False

import matplotlib.pyplot as plt
from matplotlib.animation import FuncAnimation
import serial


HEADER = [
    "record_type", "ms", "sweep_id", "point", "freq_hz", "real", "imag",
    "mag_x100", "phase_x100deg", "note", "imu_addr", "imu_whoami",
    "accel_x", "accel_y", "accel_z", "gyro_x", "gyro_y", "gyro_z",
    "imu_temp_raw", "temp_addr", "temp_x100",
]


def read_calibration(path: Path):
    """Read the existing per-frequency calibration table, if available."""
    if not path.exists():
        return {}
    nodes = defaultdict(list)
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        for row in csv.DictReader(file):
            try:
                freq = int(row["freq_hz"])
                if row.get("model") == "logZ_poly4_logMagnitude":
                    nodes[freq].append({
                        "coefficients": [float(row[f"coef_x{i}"]) for i in range(4, -1, -1)],
                        "min_mag": float(row["min_mag"]),
                        "max_mag": float(row["max_mag"]),
                    })
                else:
                    nodes[freq].append(
                        {"mag": float(row["mag_mean"]), "ohm": float(row["known_ohm"])}
                    )
            except (KeyError, TypeError, ValueError):
                continue
    return dict(nodes)


def calibrated_ohm(nodes, magnitude):
    """Log-log interpolation matching convert_measurement_to_impedance.py."""
    if nodes and "coefficients" in nodes[0]:
        if magnitude <= 0:
            return float("nan")
        coefficients = nodes[0]["coefficients"]
        x = math.log(magnitude)
        log_z = sum(coef * x ** power for coef, power in zip(coefficients, range(4, -1, -1)))
        return math.exp(log_z)
    nodes = sorted((item for item in nodes if item["mag"] > 0), key=lambda item: item["mag"])
    if len(nodes) < 2 or magnitude <= 0:
        return float("nan")
    if magnitude <= nodes[0]["mag"]:
        low, high = nodes[0], nodes[1]
    elif magnitude >= nodes[-1]["mag"]:
        low, high = nodes[-2], nodes[-1]
    else:
        low, high = nodes[0], nodes[-1]
        for left, right in zip(nodes, nodes[1:]):
            if left["mag"] <= magnitude <= right["mag"]:
                low, high = left, right
                break
    x0, x1 = math.log(low["mag"]), math.log(high["mag"])
    y0, y1 = math.log(low["ohm"]), math.log(high["ohm"])
    if x1 == x0:
        return math.sqrt(low["ohm"] * high["ohm"])
    return math.exp(y0 + (math.log(magnitude) - x0) * (y1 - y0) / (x1 - x0))


def serial_reader(port, baud, output_queue, stop_event):
    try:
        ser = serial.Serial(port, baud, timeout=0.2)
    except serial.SerialException as exc:
        output_queue.put(("error", str(exc)))
        return

    buffer = ""
    try:
        while not stop_event.is_set():
            chunk = ser.read(512)
            if not chunk:
                continue
            buffer += chunk.decode("utf-8", errors="replace")
            while "\n" in buffer:
                line, buffer = buffer.split("\n", 1)
                line = line.strip()
                if line:
                    output_queue.put(("line", line))
    finally:
        ser.close()


def main():
    parser = argparse.ArgumentParser(description="10 Hz real-time BIA and IMU plot")
    parser.add_argument("--port", default="COM5")
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument("--bia-freq", type=int, default=50000, help="BIA frequency in Hz")
    parser.add_argument(
        "--calibration",
        type=Path,
        default=Path("logs/resistor_calibration_model_final.csv"),
    )
    parser.add_argument("--seconds", type=int, default=0, help="0 means run until window closes")
    args = parser.parse_args()

    calibration = read_calibration(args.calibration)
    if calibration:
        calibration_text = f"calibration: {args.calibration}"
    else:
        calibration_text = "calibration: unavailable; plotting magnitude proxy"

    data_queue = queue.Queue()
    stop_event = threading.Event()
    reader = threading.Thread(
        target=serial_reader,
        args=(args.port, args.baud, data_queue, stop_event),
        daemon=True,
    )
    reader.start()

    max_points = 600
    bia_time_axis = deque(maxlen=max_points)
    imu_time_axis = deque(maxlen=max_points)
    bia_axis = deque(maxlen=max_points)
    bia_raw_axis = deque(maxlen=max_points)
    accel = {axis: deque(maxlen=max_points) for axis in "xyz"}
    gyro = {axis: deque(maxlen=max_points) for axis in "xyz"}
    start_time = time.monotonic()
    latest_frequency = None
    latest_imu = "waiting for IMU"
    latest_status = "waiting for data"
    connection_error = ""

    fig, (bia_plot, imu_plot) = plt.subplots(2, 1, figsize=(12, 8), sharex=True)
    fig.canvas.manager.set_window_title("STM32 BIA + IMU real-time monitor")
    bia_line, = bia_plot.plot([], [], label="BIA impedance", color="tab:blue")
    bia_raw_line, = bia_plot.plot([], [], label="AD5933 magnitude proxy", color="tab:gray", alpha=0.45)
    ax_line, = imu_plot.plot([], [], label="Accel X", color="tab:red")
    ay_line, = imu_plot.plot([], [], label="Accel Y", color="tab:green")
    az_line, = imu_plot.plot([], [], label="Accel Z", color="tab:blue")
    gx_line, = imu_plot.plot([], [], label="Gyro X / 100", color="tab:orange", linestyle="--")
    gy_line, = imu_plot.plot([], [], label="Gyro Y / 100", color="tab:purple", linestyle="--")
    gz_line, = imu_plot.plot([], [], label="Gyro Z / 100", color="tab:brown", linestyle="--")

    bia_plot.set_title(f"BIA at {args.bia_freq} Hz")
    bia_plot.set_ylabel("Ohm / magnitude proxy")
    bia_plot.grid(True, alpha=0.3)
    bia_plot.legend(loc="upper left", ncol=2)
    imu_plot.set_title("IMU (raw accelerometer; gyro divided by 100 for display)")
    imu_plot.set_xlabel("Time (s)")
    imu_plot.set_ylabel("Raw counts")
    imu_plot.grid(True, alpha=0.3)
    imu_plot.legend(loc="upper left", ncol=3, fontsize=8)
    fig.text(0.01, 0.01, calibration_text, fontsize=8)
    status_text = fig.text(0.5, 0.98, "正在连接串口...", ha="center", va="top", color="tab:blue")
    error_text = fig.text(0.5, 0.50, "", ha="center", va="center", color="tab:red", fontsize=16)
    fig.tight_layout(rect=(0, 0.03, 1, 1))

    def update(_frame):
        nonlocal latest_frequency, latest_imu, latest_status, connection_error
        now = time.monotonic() - start_time
        while True:
            try:
                kind, payload = data_queue.get_nowait()
            except queue.Empty:
                break
            if kind == "error":
                connection_error = f"串口打开失败：{payload}\n请关闭串口助手/BLE工具/CubeIDE后重新运行"
                latest_status = "COM port unavailable"
                stop_event.set()
                continue
            print(payload, flush=True)
            parts = next(csv.reader([payload]))
            if not parts:
                continue
            values = dict(zip(HEADER, parts + [""] * (len(HEADER) - len(parts))))
            if values["record_type"] == "data":
                try:
                    frequency = int(values["freq_hz"])
                    magnitude = float(values["mag_x100"]) / 100.0
                except (TypeError, ValueError):
                    continue
                if frequency == args.bia_freq:
                    impedance = calibrated_ohm(calibration.get(frequency, []), magnitude)
                    bia_value = impedance if math.isfinite(impedance) else magnitude
                    bia_time_axis.append(now)
                    bia_axis.append(bia_value)
                    bia_raw_axis.append(magnitude)
                    latest_frequency = frequency
                    latest_status = f"AD5933 {frequency} Hz, mag={magnitude:.3f}"
            elif values["record_type"] == "imu" and values["note"] == "imu_sample":
                try:
                    ax, ay, az = (float(values[key]) for key in ("accel_x", "accel_y", "accel_z"))
                    gx, gy, gz = (float(values[key]) / 100.0 for key in ("gyro_x", "gyro_y", "gyro_z"))
                except (TypeError, ValueError):
                    continue
                imu_time_axis.append(now)
                for axis, value in zip("xyz", (ax, ay, az)):
                    accel[axis].append(value)
                for axis, value in zip("xyz", (gx, gy, gz)):
                    gyro[axis].append(value)
                latest_imu = f"IMU {values['imu_addr']} WHO_AM_I={values['imu_whoami']}"
            elif values["record_type"] == "status":
                latest_status = values["note"]

        bia_x = list(bia_time_axis)
        imu_x = list(imu_time_axis)
        if bia_x:
            bia_line.set_data(bia_x, list(bia_axis))
            bia_raw_line.set_data(bia_x, list(bia_raw_axis))
            bia_plot.set_xlim(max(0.0, bia_x[-1] - 60.0), max(60.0, bia_x[-1]))
            bia_plot.relim()
            bia_plot.autoscale_view(scaley=True)
        if imu_x:
            ax_line.set_data(imu_x, list(accel["x"]))
            ay_line.set_data(imu_x, list(accel["y"]))
            az_line.set_data(imu_x, list(accel["z"]))
            gx_line.set_data(imu_x, list(gyro["x"]))
            gy_line.set_data(imu_x, list(gyro["y"]))
            gz_line.set_data(imu_x, list(gyro["z"]))
            imu_plot.set_xlim(max(0.0, imu_x[-1] - 60.0), max(60.0, imu_x[-1]))
            imu_plot.relim()
            imu_plot.autoscale_view(scaley=True)
        status_text.set_text(f"{latest_imu} | {latest_status}")
        error_text.set_text(connection_error)
        return (bia_line, bia_raw_line, ax_line, ay_line, az_line, gx_line, gy_line, gz_line)

    def close(_event):
        stop_event.set()

    fig.canvas.mpl_connect("close_event", close)
    interval_ms = 100
    animation = FuncAnimation(fig, update, interval=interval_ms, blit=False, cache_frame_data=False)
    _ = animation
    try:
        plt.show(block=True)
    finally:
        stop_event.set()
        reader.join(timeout=1.0)


if __name__ == "__main__":
    main()
