cmake_minimum_required(VERSION 3.20)

set(CMAKE_SYSTEM_NAME Generic)
set(CMAKE_SYSTEM_PROCESSOR arm)

project(AD5933_F411RC_HAL C ASM)

set(CMAKE_C_STANDARD 11)
set(CMAKE_C_STANDARD_REQUIRED ON)

add_compile_definitions(
    STM32F411xE
    USE_HAL_DRIVER
)

add_compile_options(
    -mcpu=cortex-m4
    -mthumb
    -mfpu=fpv4-sp-d16
    -mfloat-abi=hard
    -ffunction-sections
    -fdata-sections
    -Wall
)

include_directories(
    Core/Inc
    Drivers/CMSIS/Include
    Drivers/CMSIS/Device/ST/STM32F4xx/Include
    Drivers/STM32F4xx_HAL_Driver/Inc
    Drivers/STM32F4xx_HAL_Driver/Inc/Legacy
)

set(HAL_SOURCES
    Drivers/STM32F4xx_HAL_Driver/Src/stm32f4xx_hal.c
    Drivers/STM32F4xx_HAL_Driver/Src/stm32f4xx_hal_cortex.c
    Drivers/STM32F4xx_HAL_Driver/Src/stm32f4xx_hal_dma.c
    Drivers/STM32F4xx_HAL_Driver/Src/stm32f4xx_hal_flash.c
    Drivers/STM32F4xx_HAL_Driver/Src/stm32f4xx_hal_flash_ex.c
    Drivers/STM32F4xx_HAL_Driver/Src/stm32f4xx_hal_gpio.c
    Drivers/STM32F4xx_HAL_Driver/Src/stm32f4xx_hal_i2c.c
    Drivers/STM32F4xx_HAL_Driver/Src/stm32f4xx_hal_i2c_ex.c
    Drivers/STM32F4xx_HAL_Driver/Src/stm32f4xx_hal_pwr.c
    Drivers/STM32F4xx_HAL_Driver/Src/stm32f4xx_hal_pwr_ex.c
    Drivers/STM32F4xx_HAL_Driver/Src/stm32f4xx_hal_rcc.c
    Drivers/STM32F4xx_HAL_Driver/Src/stm32f4xx_hal_rcc_ex.c
    Drivers/STM32F4xx_HAL_Driver/Src/stm32f4xx_hal_uart.c
)

set(PROJECT_SOURCES
    Startup/startup_stm32f411xe.s
    Core/Src/main.c
    Core/Src/gpio.c
    Core/Src/i2c.c
    Core/Src/usart.c
    Core/Src/stm32f4xx_it.c
    Core/Src/system_stm32f4xx.c
    Core/Src/syscalls.c
    ${HAL_SOURCES}
)

add_executable(${PROJECT_NAME}.elf ${PROJECT_SOURCES})

target_link_options(${PROJECT_NAME}.elf PRIVATE
    -mcpu=cortex-m4
    -mthumb
    -mfpu=fpv4-sp-d16
    -mfloat-abi=hard
    -T${CMAKE_SOURCE_DIR}/STM32F411RCTX_FLASH.ld
    --specs=nano.specs
    -Wl,-Map=${PROJECT_NAME}.map
    -Wl,--gc-sections
    -Wl,--start-group
    -lc
    -lm
    -Wl,--end-group
)

add_custom_command(TARGET ${PROJECT_NAME}.elf POST_BUILD
    COMMAND ${CMAKE_OBJCOPY} -O binary $<TARGET_FILE:${PROJECT_NAME}.elf> ${PROJECT_NAME}.bin
    COMMAND ${CMAKE_SIZE} $<TARGET_FILE:${PROJECT_NAME}.elf>
)
