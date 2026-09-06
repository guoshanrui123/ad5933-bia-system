import argparse
import csv
import json
import sys
import time
from pathlib import Path

import serial
import serial.tools.list_ports


CSV_HEADER = [
    "record_type", "ms", "sweep_id", "point", "freq_hz", "real", "imag",
    "mag_x100", "phase_x100deg", "note", "imu_addr", "imu_whoami",
    "accel_x", "accel_y", "accel_z", "gyro_x", "gyro_y", "gyro_z",
    "imu_temp_raw", "temp_addr", "temp_x100",
]
VALID_RECORD_TYPES = {"data", "status", "imu"}


def find_port(device: str):
    for port in serial.tools.list_ports.comports():
        if port.device.upper() == device.upper():
            return port
    return None


def is_direct_usb(port) -> bool:
    text = f"{port.description} {port.hwid}".lower()
    return "usb" in text or "vid:pid" in text


def safe_name(value: str) -> str:
    cleaned = "".join(char for char in value.strip() if char.isalnum() or char in "-_")
    return cleaned or "unknown"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Save one timestamped AD5933/IMU measurement session."
    )
    parser.add_argument("--port", required=True, help="HC-08 Bluetooth virtual COM port")
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument("--seconds", type=int, default=60)
    parser.add_argument("--subject", required=True)
    parser.add_argument("--leg", choices=("left", "right"), required=True)
    parser.add_argument("--posture", required=True)
    parser.add_argument("--run", type=int, default=1)
    parser.add_argument("--electrode-distance-cm", type=float, required=True)
    args = parser.parse_args()

    port = find_port(args.port)
    if port is None:
        print(f"Port {args.port} was not found.")
        return 2
    if is_direct_usb(port):
        print(
            f"REFUSED: {args.port} is a direct USB serial device ({port.description}).\n"
            "Human-connected acquisition requires battery-powered hardware and an "
            "isolated wireless data link. Select the HC-08 Bluetooth COM port."
        )
        return 3

    stamp = time.strftime("%Y%m%d_%H%M%S")
    base = (
        f"{stamp}_{safe_name(args.subject)}_{args.leg}_"
        f"{safe_name(args.posture)}_run{args.run:02d}"
    )
    output_dir = Path("logs") / "human_sessions"
    output_dir.mkdir(parents=True, exist_ok=True)
    csv_path = output_dir / f"{base}.csv"
    metadata_path = output_dir / f"{base}.json"

    metadata = {
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "subject": args.subject,
        "leg": args.leg,
        "posture": args.posture,
        "run": args.run,
        "electrode_distance_cm": args.electrode_distance_cm,
        "duration_seconds": args.seconds,
        "port": args.port,
        "port_description": port.description,
        "baud": args.baud,
        "electrode_order": "red=proximal, black=distal",
        "power_requirement": "battery powered; USB/ST-Link/charger disconnected",
    }
    metadata_path.write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    try:
        ser = serial.Serial(args.port, args.baud, timeout=0.2)
        ser.reset_input_buffer()
    except serial.SerialException as exc:
        print(f"Failed to open {args.port}: {exc}")
        return 4

    print(f"Saving to {csv_path}")
    buffer = ""
    rows = 0
    start = time.monotonic()
    try:
        with csv_path.open("w", newline="", encoding="utf-8-sig") as file:
            writer = csv.writer(file)
            writer.writerow(CSV_HEADER)
            while time.monotonic() - start < args.seconds:
                chunk = ser.read(512)
                if not chunk:
                    continue
                buffer += chunk.decode("utf-8", errors="replace")
                while "\n" in buffer:
                    line, buffer = buffer.split("\n", 1)
                    line = line.strip()
                    if not line:
                        continue
                    print(line)
                    parts = next(csv.reader([line]))
                    if parts and parts[0] in VALID_RECORD_TYPES:
                        parts += [""] * (len(CSV_HEADER) - len(parts))
                        writer.writerow(parts[: len(CSV_HEADER)])
                        file.flush()
                        rows += 1
    except KeyboardInterrupt:
        print("Stopped by user.")
    finally:
        ser.close()

    print(f"Saved {rows} rows to {csv_path}")
    print(f"Metadata saved to {metadata_path}")
    return 0 if rows else 5


if __name__ == "__main__":
    sys.exit(main())
