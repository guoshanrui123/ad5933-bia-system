"""Record all serial output for five minutes and save it as a TXT file."""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

import serial


def default_output_path() -> Path:
    stamp = time.strftime("%Y%m%d_%H%M%S")
    return Path("logs") / f"ad5933_5min_{stamp}.txt"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Save all AD5933 serial output for a fixed duration (default: 5 minutes)."
    )
    parser.add_argument("--port", default="COM5", help="Serial port (default: COM5)")
    parser.add_argument("--baud", type=int, default=115200, help="Baud rate (default: 115200)")
    parser.add_argument("--seconds", type=float, default=300.0,
                        help="Recording duration in seconds (default: 300)")
    parser.add_argument("--out", type=Path, default=None, help="Output TXT path")
    parser.add_argument("--overwrite", action="store_true", help="Allow overwriting an existing file")
    parser.add_argument("--no-qc", action="store_true",
                        help="Save only; do not run automatic human QC afterwards")
    parser.add_argument("--qc-out", type=Path, default=None,
                        help="Automatic QC output directory")
    args = parser.parse_args()

    if args.seconds <= 0:
        parser.error("--seconds must be greater than zero")

    output_path = args.out if args.out is not None else default_output_path()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if output_path.exists() and not args.overwrite:
        print(f"Refusing to overwrite existing file: {output_path.resolve()}")
        return 2

    print(f"Opening {args.port} at {args.baud} baud")
    print(f"Duration: {args.seconds:.1f} seconds")
    print(f"Output: {output_path.resolve()}")
    print("Close other serial programs before starting. Press Ctrl+C to stop early.\n")

    try:
        port = serial.Serial(args.port, args.baud, timeout=0.2)
        port.reset_input_buffer()
    except serial.SerialException as exc:
        print(f"Failed to open {args.port}: {exc}")
        return 1

    started_wall = time.strftime("%Y-%m-%d %H:%M:%S")
    started = time.monotonic()
    deadline = started + args.seconds
    buffer = ""
    line_count = 0
    data_count = 0
    interrupted = False

    try:
        with output_path.open("w", encoding="utf-8", newline="") as output:
            output.write(
                f"# timed_capture,duration_seconds={args.seconds:g},port={args.port},"
                f"baud={args.baud},started={started_wall}\n"
            )
            output.flush()

            while time.monotonic() < deadline:
                chunk = port.read(512)
                if not chunk:
                    continue
                buffer += chunk.decode("utf-8", errors="replace")

                while "\n" in buffer:
                    line, buffer = buffer.split("\n", 1)
                    line = line.rstrip("\r")
                    if not line:
                        continue
                    output.write(line + "\n")
                    output.flush()
                    print(line)
                    line_count += 1
                    if line.startswith("data,"):
                        data_count += 1

            # Preserve a final complete non-newline-terminated serial fragment.
            if buffer.strip():
                output.write(buffer.rstrip("\r") + "\n")
                line_count += 1

            elapsed = time.monotonic() - started
            output.write(
                f"# timed_capture_complete,elapsed_seconds={elapsed:.3f},"
                f"lines={line_count},data_lines={data_count},"
                f"finished={time.strftime('%Y-%m-%d %H:%M:%S')}\n"
            )
            output.flush()
    except KeyboardInterrupt:
        interrupted = True
        elapsed = time.monotonic() - started
        with output_path.open("a", encoding="utf-8", newline="") as output:
            output.write(
                f"# timed_capture_interrupted,elapsed_seconds={elapsed:.3f},"
                f"lines={line_count},data_lines={data_count},"
                f"finished={time.strftime('%Y-%m-%d %H:%M:%S')}\n"
            )
        print("\nRecording stopped early by user.")
    except serial.SerialException as exc:
        elapsed = time.monotonic() - started
        with output_path.open("a", encoding="utf-8", newline="") as output:
            output.write(
                f"# timed_capture_serial_error,elapsed_seconds={elapsed:.3f},"
                f"error={str(exc).replace(',', ';')},"
                f"finished={time.strftime('%Y-%m-%d %H:%M:%S')}\n"
            )
        print(f"Serial error: {exc}")
        return 3
    finally:
        port.close()

    print(f"\nSaved: {output_path.resolve()}")
    print(f"Lines: {line_count}, data lines: {data_count}")
    if interrupted:
        return 130
    if not args.no_qc:
        qc_script = Path(__file__).with_name("postprocess_5min_human.py")
        qc_out = args.qc_out or output_path.with_name(output_path.stem + "_5min_qc")
        print("\nRunning automatic five-minute human QC...")
        completed = subprocess.run(
            [sys.executable, str(qc_script), str(output_path), "--out", str(qc_out)],
            check=False,
        )
        print(f"QC result: {'PASS' if completed.returncode == 0 else 'FAIL'}")
        return completed.returncode
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
