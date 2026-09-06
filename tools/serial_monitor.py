import argparse
import re
import sys
import time

import serial
import serial.tools.list_ports


def list_ports() -> None:
    ports = list(serial.tools.list_ports.comports())
    if not ports:
        print("No serial ports found.")
        return

    print("Available serial ports:")
    for port in ports:
        print(f"  {port.device}: {port.description}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Simple STM32 serial monitor for I2C scan output.")
    parser.add_argument("--port", default="COM5", help="Serial port, default: COM5")
    parser.add_argument("--baud", default=115200, type=int, help="Baud rate, default: 115200")
    parser.add_argument("--seconds", default=0, type=int, help="Stop after N seconds. 0 means run forever.")
    parser.add_argument("--list", action="store_true", help="List available serial ports and exit.")
    args = parser.parse_args()

    if args.list:
        list_ports()
        return 0

    found = set()
    start = time.time()

    print(f"Opening {args.port} at {args.baud}. Press Ctrl+C to stop.")
    print("Looking for lines like: Found I2C device: 0x0D")

    try:
        ser = serial.Serial(args.port, args.baud, timeout=0.2)
    except serial.SerialException as exc:
        print(f"Failed to open {args.port}: {exc}")
        print("Tip: close other serial tools first. A COM port can only be opened by one program.")
        return 1

    try:
        while True:
            data = ser.read(512)
            if data:
                text = data.decode("utf-8", errors="replace")
                print(text, end="")
                for match in re.findall(r"Found I2C device(?: with swapped pins)?: 0x([0-9A-Fa-f]{2})", text):
                    found.add(match.upper())

            if args.seconds and time.time() - start >= args.seconds:
                break

    except KeyboardInterrupt:
        print("\nStopped by user.")
    finally:
        ser.close()

    if found:
        print("\nDetected I2C address(es): " + ", ".join(f"0x{x}" for x in sorted(found)))
        if "0D" in found:
            print("AD5933 detected successfully.")
    else:
        print("\nNo I2C devices detected in the captured output.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
