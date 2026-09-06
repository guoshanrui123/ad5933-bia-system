import argparse
import csv
import sys
import time
from pathlib import Path

import serial


EXPECTED_FREQUENCIES = set(range(5_000, 100_001, 5_000))


def default_output_path() -> Path:
    stamp = time.strftime("%Y%m%d_%H%M%S")
    return Path("logs") / f"ad5933_30sweeps_{stamp}.txt"


def parse_data_line(line: str):
    if not line.startswith("data,"):
        return None
    try:
        row = next(csv.reader([line]))
        return int(row[2]), int(row[4])
    except (IndexError, ValueError, csv.Error):
        return None


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Save serial output to TXT and stop after 30 complete AD5933 sweeps."
    )
    parser.add_argument("--port", default="COM5", help="Serial port (default: COM5)")
    parser.add_argument("--baud", type=int, default=115200, help="Baud rate")
    parser.add_argument("--sweeps", type=int, default=30, help="Number of complete sweeps")
    parser.add_argument("--out", default="", help="Output TXT path")
    parser.add_argument(
        "--timeout-minutes",
        type=float,
        default=15,
        help="Stop with an error if the target is not reached (default: 15 minutes)",
    )
    args = parser.parse_args()

    if args.sweeps < 1:
        parser.error("--sweeps must be at least 1")

    out_path = Path(args.out) if args.out else default_output_path()
    out_path.parent.mkdir(parents=True, exist_ok=True)

    print(f"Opening {args.port} at {args.baud} baud")
    print(f"Target: {args.sweeps} complete sweeps")
    print(f"Output: {out_path.resolve()}")
    print("Close other programs using this serial port before starting.\n")

    try:
        ser = serial.Serial(args.port, args.baud, timeout=0.2)
        ser.reset_input_buffer()
    except serial.SerialException as exc:
        print(f"Failed to open {args.port}: {exc}")
        return 1

    frequencies_by_sweep = {}
    completed_ids = set()
    complete_count = 0
    buffer = ""
    deadline = time.monotonic() + args.timeout_minutes * 60

    try:
        with out_path.open("w", encoding="utf-8", newline="") as output:
            output.write(
                f"# automatic_capture,target_complete_sweeps={args.sweeps},"
                f"port={args.port},baud={args.baud},started={time.strftime('%Y-%m-%d %H:%M:%S')}\n"
            )
            output.flush()

            while complete_count < args.sweeps:
                if time.monotonic() >= deadline:
                    print(f"\nTimeout: only {complete_count}/{args.sweeps} complete sweeps received.")
                    return 2

                chunk = ser.read(512)
                if not chunk:
                    continue
                buffer += chunk.decode("utf-8", errors="replace")

                while "\n" in buffer:
                    line, buffer = buffer.split("\n", 1)
                    line = line.rstrip("\r").strip()
                    if not line:
                        continue

                    output.write(line + "\n")
                    output.flush()
                    print(line)

                    parsed = parse_data_line(line)
                    if parsed is None:
                        continue
                    sweep_id, frequency = parsed
                    frequencies_by_sweep.setdefault(sweep_id, set()).add(frequency)

                    if (
                        sweep_id not in completed_ids
                        and frequencies_by_sweep[sweep_id] == EXPECTED_FREQUENCIES
                    ):
                        completed_ids.add(sweep_id)
                        complete_count += 1
                        print(
                            f"[AUTO] Complete sweep {complete_count}/{args.sweeps} "
                            f"(sweep_id={sweep_id})"
                        )
                        if complete_count >= args.sweeps:
                            break

            output.write(
                f"# automatic_capture_complete,complete_sweeps={complete_count},"
                f"finished={time.strftime('%Y-%m-%d %H:%M:%S')}\n"
            )
            output.flush()
    except KeyboardInterrupt:
        print(f"\nStopped by user. Partial TXT retained: {out_path.resolve()}")
        return 130
    finally:
        ser.close()

    print(f"\nDone: saved {complete_count} complete sweeps to:")
    print(out_path.resolve())
    return 0


if __name__ == "__main__":
    sys.exit(main())
