#include "main.h"
#include "gpio.h"
#include "i2c.h"
#include "usart.h"
#include <math.h>
#include <stdio.h>
#include <string.h>

static void SystemClock_Config(void);
static void UART_Print(const char *text);
static void AD5933_PrintRawSweep(void);
static void SensorI2C_PrintScan(void);
static void Sensor_PrintSample(void);
static uint8_t IMU_ReadSample(int16_t *ax, int16_t *ay, int16_t *az,
                              int16_t *gx, int16_t *gy, int16_t *gz,
                              int16_t *imu_temp, uint8_t *whoami);
static uint32_t sweep_id = 0;
static uint8_t imu_addr = 0;
static uint8_t temp_addr = 0;

int main(void)
{
    HAL_Init();
    SystemClock_Config();

    MX_GPIO_Init();
    MX_I2C1_Init();
    MX_USART1_UART_Init();
    MX_USART2_UART_Init();

    UART_Print("record_type,ms,sweep_id,point,freq_hz,real,imag,mag_x100,phase_x100deg,note,imu_addr,imu_whoami,accel_x,accel_y,accel_z,gyro_x,gyro_y,gyro_z,imu_temp_raw,temp_addr,temp_x100\r\n");

    uint32_t scan_count = 0;
    while (1) {
        HAL_GPIO_TogglePin(GPIOC, GPIO_PIN_13);
        HAL_Delay(500);

        scan_count++;
        if (scan_count >= 6) {
            scan_count = 0;
            SensorI2C_PrintScan();
            Sensor_PrintSample();
            AD5933_PrintRawSweep();
        }
    }
}

static void SensorI2C_PrintScan(void)
{
    char line[160];
    uint8_t found = 0;
    uint8_t found_imu = 0;
    uint8_t found_temp = 0;

    SoftI2C_SelectBus(1);
    snprintf(line, sizeof(line), "status,%lu,0,0,0,0,0,0,0,i2c_pc8_pc9_scan_start\r\n",
             (unsigned long)HAL_GetTick());
    UART_Print(line);

    for (uint8_t addr = 0x08; addr <= 0x77; addr++) {
        SoftI2C_SelectBus(1);
        if (SoftI2C_IsDeviceReady(addr)) {
            found++;
            if ((addr == 0x68 || addr == 0x69) && imu_addr == 0) {
                imu_addr = addr;
                SoftI2C_WriteMem8(imu_addr, 0x6B, 0x00);
            }
            if (addr == 0x68 || addr == 0x69) {
                found_imu = 1;
            }
            if (addr >= 0x48 && addr <= 0x4F && temp_addr == 0) {
                temp_addr = addr;
            }
            if (addr >= 0x48 && addr <= 0x4F) {
                found_temp = 1;
            }
            snprintf(line, sizeof(line), "status,%lu,0,0,0,0,0,0,0,i2c_pc8_pc9_found_0x%02X\r\n",
                     (unsigned long)HAL_GetTick(), addr);
            UART_Print(line);
        }
    }

    if (found == 0) {
        snprintf(line, sizeof(line), "status,%lu,0,0,0,0,0,0,0,i2c_pc8_pc9_none_found\r\n",
                 (unsigned long)HAL_GetTick());
        UART_Print(line);
    }

    snprintf(line, sizeof(line), "status,%lu,0,0,0,0,0,0,0,i2c_pc8_pc9_scan_end_count_%u\r\n",
             (unsigned long)HAL_GetTick(), found);
    UART_Print(line);
    if (!found_imu) {
        imu_addr = 0;
    }
    if (!found_temp) {
        temp_addr = 0;
    }
    SoftI2C_SelectBus(0);
}

static int16_t Sensor_ToInt16(uint8_t hi, uint8_t lo)
{
    return (int16_t)((uint16_t)hi << 8 | lo);
}

static uint8_t IMU_ReadSample(int16_t *ax, int16_t *ay, int16_t *az,
                              int16_t *gx, int16_t *gy, int16_t *gz,
                              int16_t *imu_temp, uint8_t *whoami)
{
    uint8_t data[14] = {0};

    if (imu_addr == 0) {
        return 0;
    }

    SoftI2C_SelectBus(1);
    SoftI2C_WriteMem8(imu_addr, 0x6B, 0x00);
    if (!SoftI2C_ReadMem8(imu_addr, 0x75, whoami)) {
        return 0;
    }
    if (!SoftI2C_ReadMem8Multi(imu_addr, 0x3B, data, sizeof(data))) {
        return 0;
    }

    *ax = Sensor_ToInt16(data[0], data[1]);
    *ay = Sensor_ToInt16(data[2], data[3]);
    *az = Sensor_ToInt16(data[4], data[5]);
    *imu_temp = Sensor_ToInt16(data[6], data[7]);
    *gx = Sensor_ToInt16(data[8], data[9]);
    *gy = Sensor_ToInt16(data[10], data[11]);
    *gz = Sensor_ToInt16(data[12], data[13]);
    return 1;
}

static uint8_t Temp_ReadX100(int16_t *temp_x100)
{
    uint8_t data[2] = {0};
    int16_t raw;

    if (temp_addr == 0) {
        return 0;
    }

    SoftI2C_SelectBus(1);
    if (!SoftI2C_ReadMem8Multi(temp_addr, 0x00, data, sizeof(data))) {
        return 0;
    }

    raw = (int16_t)((uint16_t)data[0] << 8 | data[1]);
    raw >>= 4;
    if (raw & 0x0800) {
        raw |= (int16_t)0xF000;
    }
    *temp_x100 = (int16_t)((int32_t)raw * 625 / 100);
    return 1;
}

static void Sensor_PrintSample(void)
{
    char line[192];
    int16_t ax = 0;
    int16_t ay = 0;
    int16_t az = 0;
    int16_t gx = 0;
    int16_t gy = 0;
    int16_t gz = 0;
    int16_t imu_temp = 0;
    int16_t temp_x100 = 0;
    uint8_t whoami = 0;
    uint8_t imu_ok = IMU_ReadSample(&ax, &ay, &az, &gx, &gy, &gz, &imu_temp, &whoami);
    uint8_t temp_ok = Temp_ReadX100(&temp_x100);

    snprintf(line, sizeof(line),
             "imu,%lu,0,0,0,0,0,0,0,%s,0x%02X,0x%02X,%d,%d,%d,%d,%d,%d,%d,0x%02X,%d\r\n",
             (unsigned long)HAL_GetTick(),
             imu_ok ? "imu_sample" : "imu_not_ready",
             imu_addr, whoami, ax, ay, az, gx, gy, gz, imu_temp,
             temp_ok ? temp_addr : 0, temp_ok ? temp_x100 : 0);
    UART_Print(line);
    SoftI2C_SelectBus(0);
}

#define AD5933_ADDR 0x0D
#define AD5933_MCLK_HZ 16776000.0f
#define AD5933_CTRL_RANGE4_PGA1 0x07U
#define CSV_EMPTY_SENSOR_FIELDS ",0,0,0,0,0,0,0,0,0,0,0"

static uint8_t AD5933_Write(uint8_t reg, uint8_t value)
{
    SoftI2C_SelectPins(0);
    return SoftI2C_WriteRegister(AD5933_ADDR, reg, value);
}

static uint8_t AD5933_Read(uint8_t reg, uint8_t *value)
{
    SoftI2C_SelectPins(0);
    return SoftI2C_ReadRegister(AD5933_ADDR, reg, value);
}

static int16_t AD5933_ReadSigned16(uint8_t high_reg)
{
    uint8_t hi = 0;
    uint8_t lo = 0;
    AD5933_Read(high_reg, &hi);
    AD5933_Read((uint8_t)(high_reg + 1), &lo);
    return (int16_t)((uint16_t)hi << 8 | lo);
}

static void AD5933_WriteFreq(uint8_t reg, float freq_hz)
{
    uint32_t code = (uint32_t)((freq_hz * 536870912.0f / AD5933_MCLK_HZ) + 0.5f);

    AD5933_Write(reg, (uint8_t)(code >> 16));
    AD5933_Write((uint8_t)(reg + 1), (uint8_t)(code >> 8));
    AD5933_Write((uint8_t)(reg + 2), (uint8_t)code);
}

static uint8_t AD5933_WaitDataValid(void)
{
    uint8_t status = 0;

    for (uint32_t i = 0; i < 200; i++) {
        AD5933_Read(0x8F, &status);
        if (status & 0x02) {
            return 1;
        }
        HAL_Delay(2);
    }

    return 0;
}

static void AD5933_PrintRawSweep(void)
{
    char line[160];
    const float start_hz = 5000.0f;
    const float step_hz = 5000.0f;
    const uint16_t increments = 19;
    uint32_t current_sweep = ++sweep_id;

    if (!SoftI2C_IsDeviceReady(AD5933_ADDR)) {
        snprintf(line, sizeof(line), "status,%lu,%lu,0,0,0,0,0,0,ad5933_not_ready" CSV_EMPTY_SENSOR_FIELDS "\r\n",
                 (unsigned long)HAL_GetTick(), (unsigned long)current_sweep);
        UART_Print(line);
        return;
    }

    snprintf(line, sizeof(line), "status,%lu,%lu,0,0,0,0,0,0,sweep_start_5k_to_100k" CSV_EMPTY_SENSOR_FIELDS "\r\n",
             (unsigned long)HAL_GetTick(), (unsigned long)current_sweep);
    UART_Print(line);

    AD5933_Write(0x80, 0xB0U | AD5933_CTRL_RANGE4_PGA1);
    AD5933_Write(0x81, 0x00);
    AD5933_WriteFreq(0x82, start_hz);
    AD5933_WriteFreq(0x85, step_hz);
    AD5933_Write(0x88, (uint8_t)(increments >> 8));
    AD5933_Write(0x89, (uint8_t)increments);
    AD5933_Write(0x8A, 0x00);
    AD5933_Write(0x8B, 0x10);

    AD5933_Write(0x80, 0x10U | AD5933_CTRL_RANGE4_PGA1);
    AD5933_Write(0x81, 0x00);
    HAL_Delay(20);

    AD5933_Write(0x80, 0x20U | AD5933_CTRL_RANGE4_PGA1);
    AD5933_Write(0x81, 0x00);

    for (uint16_t point = 0; point <= increments; point++) {
        float freq = start_hz + step_hz * point;

        if (!AD5933_WaitDataValid()) {
            UART_Print("AD5933 data timeout\r\n");
            break;
        }

        int16_t real = AD5933_ReadSigned16(0x94);
        int16_t imag = AD5933_ReadSigned16(0x96);
        float magnitude = sqrtf((float)real * real + (float)imag * imag);
        float phase_deg = atan2f((float)imag, (float)real) * 57.2957795f;

        int32_t freq_i = (int32_t)(freq + 0.5f);
        int32_t mag_x100 = (int32_t)(magnitude * 100.0f + 0.5f);
        int32_t phase_x100 = (int32_t)(phase_deg * 100.0f);

        snprintf(line, sizeof(line),
                 "data,%lu,%lu,%u,%ld,%d,%d,%ld,%ld,raw_ad5933_range4_pga1" CSV_EMPTY_SENSOR_FIELDS "\r\n",
                 (unsigned long)HAL_GetTick(), (unsigned long)current_sweep, point,
                 (long)freq_i, real, imag, (long)mag_x100, (long)phase_x100);
        UART_Print(line);

        uint8_t status = 0;
        AD5933_Read(0x8F, &status);
        if (status & 0x04) {
            break;
        }

        AD5933_Write(0x80, 0x30U | AD5933_CTRL_RANGE4_PGA1);
        AD5933_Write(0x81, 0x00);
    }

    AD5933_Write(0x80, 0xA0U | AD5933_CTRL_RANGE4_PGA1);
    AD5933_Write(0x81, 0x00);
    snprintf(line, sizeof(line), "status,%lu,%lu,0,0,0,0,0,0,sweep_end" CSV_EMPTY_SENSOR_FIELDS "\r\n",
             (unsigned long)HAL_GetTick(), (unsigned long)current_sweep);
    UART_Print(line);
}

static void UART_Print(const char *text)
{
    HAL_UART_Transmit(&huart1, (uint8_t *)text, (uint16_t)strlen(text), HAL_MAX_DELAY);
    HAL_UART_Transmit(&huart2, (uint8_t *)text, (uint16_t)strlen(text), HAL_MAX_DELAY);
}

static void SystemClock_Config(void)
{
    RCC_OscInitTypeDef RCC_OscInitStruct = {0};
    RCC_ClkInitTypeDef RCC_ClkInitStruct = {0};

    __HAL_RCC_PWR_CLK_ENABLE();
    __HAL_PWR_VOLTAGESCALING_CONFIG(PWR_REGULATOR_VOLTAGE_SCALE2);

    RCC_OscInitStruct.OscillatorType = RCC_OSCILLATORTYPE_HSI;
    RCC_OscInitStruct.HSIState = RCC_HSI_ON;
    RCC_OscInitStruct.HSICalibrationValue = RCC_HSICALIBRATION_DEFAULT;
    RCC_OscInitStruct.PLL.PLLState = RCC_PLL_ON;
    RCC_OscInitStruct.PLL.PLLSource = RCC_PLLSOURCE_HSI;
    RCC_OscInitStruct.PLL.PLLM = 16;
    RCC_OscInitStruct.PLL.PLLN = 336;
    RCC_OscInitStruct.PLL.PLLP = RCC_PLLP_DIV4;
    RCC_OscInitStruct.PLL.PLLQ = 7;
    if (HAL_RCC_OscConfig(&RCC_OscInitStruct) != HAL_OK) {
        Error_Handler();
    }

    RCC_ClkInitStruct.ClockType = RCC_CLOCKTYPE_HCLK | RCC_CLOCKTYPE_SYSCLK |
                                  RCC_CLOCKTYPE_PCLK1 | RCC_CLOCKTYPE_PCLK2;
    RCC_ClkInitStruct.SYSCLKSource = RCC_SYSCLKSOURCE_PLLCLK;
    RCC_ClkInitStruct.AHBCLKDivider = RCC_SYSCLK_DIV1;
    RCC_ClkInitStruct.APB1CLKDivider = RCC_HCLK_DIV2;
    RCC_ClkInitStruct.APB2CLKDivider = RCC_HCLK_DIV1;
    if (HAL_RCC_ClockConfig(&RCC_ClkInitStruct, FLASH_LATENCY_2) != HAL_OK) {
        Error_Handler();
    }
}

void Error_Handler(void)
{
    __disable_irq();
    while (1) {
        HAL_GPIO_TogglePin(GPIOC, GPIO_PIN_13);
        HAL_Delay(100);
    }
}
