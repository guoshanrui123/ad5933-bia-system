# AD5933_F411RC_HAL

This is a manually prepared STM32F411RCT6 HAL project for the edema hardware prototype.

Configured pins:

- `PB10`: software I2C SCL
- `PB9`: software I2C SDA
- `PA9`: `USART1_TX`
- `PA10`: `USART1_RX`
- `PC13`: GPIO output LED test

Current firmware behavior:

- Outputs the same CSV to USART1 (`115200 8N1`, PA9/PA10) and USART2 (`9600 8N1`, PA2/PA3).
- AD5933 raw sweep: 5–100 kHz, 5 kHz steps, Range 4 / PGA ×1.
- Scans the auxiliary software I2C bus on PC8/PC9 and reports available IMU/temperature data.
- Toggles PC13 between measurement cycles.

Build from PowerShell using `./BUILD.ps1`. CMake, Ninja and ARM GNU tools must be on PATH; alternatively pass `-CMake <cmake.exe> -Ninja <ninja.exe> -ArmToolchainBin <bin-directory>`.
Build success is not a hardware, electrical safety or clinical acceptance test.
