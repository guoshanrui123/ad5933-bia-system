import argparse
import csv
import sys
import time

import serial
import serial.tools.list_ports


HEADER = [
    "record_type", "ms", "sweep_id", "point", "freq_hz", "real", "imag",
    "mag_x100", "phase_x100deg", "note", "imu_addr", "imu_whoami",
    "accel_x", "accel_y", "accel_z", "gyro_x", "gyro_y", "gyro_z",
    "imu_temp_raw", "temp_addr", "temp_x100",
]


def show_ports() -> None:
    ports = list(serial.tools.list_ports.comports())
    if not ports:
        print("没有发现串口。")
        return
    print("可用串口：")
    for port in ports:
        print(f"  {port.device}: {port.description}")


def print_record(line: str, raw: bool) -> None:
    parts = next(csv.reader([line]))
    if not parts or parts[0] not in {"data", "imu", "status"}:
        print(f"[串口] {line}")
        return

    if raw:
        print(f"[RAW] {line}")

    values = dict(zip(HEADER, parts + [""] * (len(HEADER) - len(parts))))
    record_type = values["record_type"]
    if record_type == "imu":
        print(
            "[IMU] "
            f"地址={values['imu_addr']} "
            f"WHO_AM_I={values['imu_whoami']} "
            f"加速度=({values['accel_x']}, {values['accel_y']}, {values['accel_z']}) "
            f"角速度=({values['gyro_x']}, {values['gyro_y']}, {values['gyro_z']}) "
            f"状态={values['note']}"
        )
    elif record_type == "data":
        print(
            "[AD5933] "
            f"频率={values['freq_hz']} Hz "
            f"Real={values['real']} Imag={values['imag']} "
            f"Mag×100={values['mag_x100']} "
            f"Phase×100°={values['phase_x100deg']}"
        )
    else:
        print(f"[状态] {values['note']}")


def main() -> int:
    parser = argparse.ArgumentParser(description="实时显示 STM32 的 IMU 和 AD5933 数据")
    parser.add_argument("--port", default="COM5", help="串口号，默认 COM5")
    parser.add_argument("--baud", type=int, default=115200, help="波特率，默认 115200")
    parser.add_argument("--seconds", type=int, default=0, help="运行秒数；0 表示一直运行")
    parser.add_argument("--list", action="store_true", help="列出可用串口")
    parser.add_argument("--raw", action="store_true", help="同时打印原始 CSV 行")
    args = parser.parse_args()

    if args.list:
        show_ports()
        return 0

    print(f"正在打开 {args.port}，波特率 {args.baud}。按 Ctrl+C 停止。")
    try:
        ser = serial.Serial(args.port, args.baud, timeout=0.2)
    except serial.SerialException as exc:
        print(f"打开串口失败：{exc}")
        print("请先关闭串口助手、BLE 调试工具或 CubeIDE 串口窗口。")
        return 1

    buffer = ""
    start = time.time()
    try:
        while args.seconds == 0 or time.time() - start < args.seconds:
            chunk = ser.read(512)
            if not chunk:
                continue
            buffer += chunk.decode("utf-8", errors="replace")
            while "\n" in buffer:
                line, buffer = buffer.split("\n", 1)
                line = line.strip()
                if line:
                    print_record(line, args.raw)
    except KeyboardInterrupt:
        print("\n已停止实时显示。")
    finally:
        ser.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
