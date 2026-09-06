#ifndef __I2C_H
#define __I2C_H

#include "main.h"

extern I2C_HandleTypeDef hi2c1;

void MX_I2C1_Init(void);
uint8_t SoftI2C_IsDeviceReady(uint8_t addr7);
void SoftI2C_SelectBus(uint8_t bus);
void SoftI2C_SelectPins(uint8_t swapped);
uint8_t SoftI2C_WriteRegister(uint8_t addr7, uint8_t reg, uint8_t value);
uint8_t SoftI2C_ReadRegister(uint8_t addr7, uint8_t reg, uint8_t *value);
uint8_t SoftI2C_WriteMem8(uint8_t addr7, uint8_t reg, uint8_t value);
uint8_t SoftI2C_ReadMem8(uint8_t addr7, uint8_t reg, uint8_t *value);
uint8_t SoftI2C_ReadMem8Multi(uint8_t addr7, uint8_t reg, uint8_t *data, uint8_t len);

#endif
