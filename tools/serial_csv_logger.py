import argparse
import csv
import sys
import time
from pathlib import Path

import serial


CSV_HEADER = [
    "record_type",
    "ms",
    "sweep_id",
    "point",
    "freq_hz",
    "real",
    "imag",
    "mag_x100",
    "phase_x100deg",
    "note",
    "imu_addr",
    "imu_whoami",
    "accel_x",
    "accel_y",
    "accel_z",
    "gyro_x",
    "gyro_y",
    "gyro_z",
    "imu_temp_raw",
    "temp_addr",
    "temp_x100",
]

VALID_RECORD_TYPES = {"data", "status", "imu"}


def main() -> int:
    parser = argparse.ArgumentParser(description="Log STM32 sensor CSV serial output to a CSV file.")
    parser.add_argument("--port", default="COM5")
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument("--seconds", type=int, default=60)
    parser.add_argument("--out", default="")
    args = parser.parse_args()

    if args.out:
        out_path = Path(args.out)
    else:
        stamp = time.strftime("%Y%m%d_%H%M%S")
        out_path = Path("logs") / f"ad5933_{stamp}.csv"

    out_path.parent.mkdir(parents=True, exist_ok=True)

    print(f"Opening {args.port} at {args.baud}")
    print(f"Logging for {args.seconds} seconds to: {out_path}")

    try:
        ser = serial.Serial(args.port, args.baud, timeout=0.2)
        ser.reset_input_buffer()
    except serial.SerialException as exc:
        print(f"Failed to open {args.port}: {exc}")
        print("Close other serial tools first, then try again.")
        return 1

    buffer = ""
    rows = 0
    start = time.time()

    with out_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(CSV_HEADER)
        while time.time() - start < args.seconds:
            data = ser.read(512)
            if not data:
                continue

            buffer += data.decode("utf-8", errors="replace")
            while "\n" in buffer:
                line, buffer = buffer.split("\n", 1)
                line = line.strip()
                if not line:
                    continue

                print(line)
                parts = line.split(",")
                if parts[0] == "record_type":
                    continue
                if parts[0] in VALID_RECORD_TYPES and len(parts) >= 10:
                    if len(parts) < len(CSV_HEADER):
                        parts += [""] * (len(CSV_HEADER) - len(parts))
                    writer.writerow(parts[:len(CSV_HEADER)])
                    rows += 1

    ser.close()
    print(f"Saved {rows} CSV row(s) to {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
