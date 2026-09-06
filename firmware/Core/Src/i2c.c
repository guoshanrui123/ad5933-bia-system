#include "i2c.h"

I2C_HandleTypeDef hi2c1;

void MX_I2C1_Init(void)
{
    GPIO_InitTypeDef GPIO_InitStruct = {0};

    __HAL_RCC_GPIOB_CLK_ENABLE();
    __HAL_RCC_GPIOC_CLK_ENABLE();

    GPIO_InitStruct.Pin = GPIO_PIN_9 | GPIO_PIN_10;
    GPIO_InitStruct.Mode = GPIO_MODE_OUTPUT_OD;
    GPIO_InitStruct.Pull = GPIO_PULLUP;
    GPIO_InitStruct.Speed = GPIO_SPEED_FREQ_LOW;
    HAL_GPIO_Init(GPIOB, &GPIO_InitStruct);

    HAL_GPIO_WritePin(GPIOB, GPIO_PIN_9 | GPIO_PIN_10, GPIO_PIN_SET);

    GPIO_InitStruct.Pin = GPIO_PIN_8 | GPIO_PIN_9;
    HAL_GPIO_Init(GPIOC, &GPIO_InitStruct);

    HAL_GPIO_WritePin(GPIOC, GPIO_PIN_8 | GPIO_PIN_9, GPIO_PIN_SET);
}

void HAL_I2C_MspInit(I2C_HandleTypeDef *i2cHandle)
{
    (void)i2cHandle;
}

static GPIO_TypeDef *soft_i2c_port = GPIOB;
static uint16_t soft_i2c_scl = GPIO_PIN_10;
static uint16_t soft_i2c_sda = GPIO_PIN_9;

static void SoftI2C_Delay(void)
{
    for (volatile uint32_t i = 0; i < 220; i++) {
        __NOP();
    }
}

static void SoftI2C_SCL(uint8_t level)
{
    HAL_GPIO_WritePin(soft_i2c_port, soft_i2c_scl, level ? GPIO_PIN_SET : GPIO_PIN_RESET);
    SoftI2C_Delay();
}

static void SoftI2C_SDA(uint8_t level)
{
    HAL_GPIO_WritePin(soft_i2c_port, soft_i2c_sda, level ? GPIO_PIN_SET : GPIO_PIN_RESET);
    SoftI2C_Delay();
}

static uint8_t SoftI2C_ReadSDA(void)
{
    return HAL_GPIO_ReadPin(soft_i2c_port, soft_i2c_sda) == GPIO_PIN_SET;
}

void SoftI2C_SelectBus(uint8_t bus)
{
    if (bus == 1) {
        soft_i2c_port = GPIOC;
        soft_i2c_scl = GPIO_PIN_8;
        soft_i2c_sda = GPIO_PIN_9;
    } else if (bus == 2) {
        soft_i2c_port = GPIOC;
        soft_i2c_scl = GPIO_PIN_9;
        soft_i2c_sda = GPIO_PIN_8;
    } else {
        soft_i2c_port = GPIOB;
        soft_i2c_scl = GPIO_PIN_10;
        soft_i2c_sda = GPIO_PIN_9;
    }

    SoftI2C_SDA(1);
    SoftI2C_SCL(1);
}

void SoftI2C_SelectPins(uint8_t swapped)
{
    soft_i2c_port = GPIOB;
    if (swapped) {
        soft_i2c_scl = GPIO_PIN_9;
        soft_i2c_sda = GPIO_PIN_10;
    } else {
        soft_i2c_scl = GPIO_PIN_10;
        soft_i2c_sda = GPIO_PIN_9;
    }

    SoftI2C_SDA(1);
    SoftI2C_SCL(1);
}

static void SoftI2C_Start(void)
{
    SoftI2C_SDA(1);
    SoftI2C_SCL(1);
    SoftI2C_SDA(0);
    SoftI2C_SCL(0);
}

static void SoftI2C_Stop(void)
{
    SoftI2C_SDA(0);
    SoftI2C_SCL(1);
    SoftI2C_SDA(1);
}

static uint8_t SoftI2C_WriteByte(uint8_t value)
{
    for (uint8_t i = 0; i < 8; i++) {
        SoftI2C_SDA((value & 0x80) != 0);
        SoftI2C_SCL(1);
        SoftI2C_SCL(0);
        value <<= 1;
    }

    SoftI2C_SDA(1);
    SoftI2C_SCL(1);
    uint8_t ack = !SoftI2C_ReadSDA();
    SoftI2C_SCL(0);
    return ack;
}

static uint8_t SoftI2C_ReadByte(uint8_t ack)
{
    uint8_t value = 0;

    SoftI2C_SDA(1);
    for (uint8_t i = 0; i < 8; i++) {
        value <<= 1;
        SoftI2C_SCL(1);
        if (SoftI2C_ReadSDA()) {
            value |= 1;
        }
        SoftI2C_SCL(0);
    }

    SoftI2C_SDA(ack ? 0 : 1);
    SoftI2C_SCL(1);
    SoftI2C_SCL(0);
    SoftI2C_SDA(1);

    return value;
}

uint8_t SoftI2C_IsDeviceReady(uint8_t addr7)
{
    uint8_t ack;

    SoftI2C_Start();
    ack = SoftI2C_WriteByte((uint8_t)(addr7 << 1));
    SoftI2C_Stop();

    return ack;
}

uint8_t SoftI2C_WriteRegister(uint8_t addr7, uint8_t reg, uint8_t value)
{
    uint8_t ok = 1;

    SoftI2C_Start();
    ok &= SoftI2C_WriteByte((uint8_t)(addr7 << 1));
    ok &= SoftI2C_WriteByte(reg);
    ok &= SoftI2C_WriteByte(value);
    SoftI2C_Stop();

    return ok;
}

uint8_t SoftI2C_ReadRegister(uint8_t addr7, uint8_t reg, uint8_t *value)
{
    uint8_t ok = 1;

    SoftI2C_Start();
    ok &= SoftI2C_WriteByte((uint8_t)(addr7 << 1));
    ok &= SoftI2C_WriteByte(0xB0);
    ok &= SoftI2C_WriteByte(reg);
    SoftI2C_Stop();

    SoftI2C_Start();
    ok &= SoftI2C_WriteByte((uint8_t)((addr7 << 1) | 1));
    *value = SoftI2C_ReadByte(0);
    SoftI2C_Stop();

    return ok;
}

uint8_t SoftI2C_WriteMem8(uint8_t addr7, uint8_t reg, uint8_t value)
{
    uint8_t ok = 1;

    SoftI2C_Start();
    ok &= SoftI2C_WriteByte((uint8_t)(addr7 << 1));
    ok &= SoftI2C_WriteByte(reg);
    ok &= SoftI2C_WriteByte(value);
    SoftI2C_Stop();

    return ok;
}

uint8_t SoftI2C_ReadMem8(uint8_t addr7, uint8_t reg, uint8_t *value)
{
    return SoftI2C_ReadMem8Multi(addr7, reg, value, 1);
}

uint8_t SoftI2C_ReadMem8Multi(uint8_t addr7, uint8_t reg, uint8_t *data, uint8_t len)
{
    uint8_t ok = 1;

    if (len == 0) {
        return 0;
    }

    SoftI2C_Start();
    ok &= SoftI2C_WriteByte((uint8_t)(addr7 << 1));
    ok &= SoftI2C_WriteByte(reg);

    SoftI2C_Start();
    ok &= SoftI2C_WriteByte((uint8_t)((addr7 << 1) | 1));
    for (uint8_t i = 0; i < len; i++) {
        data[i] = SoftI2C_ReadByte(i < (uint8_t)(len - 1));
    }
    SoftI2C_Stop();

    return ok;
}
