/* Pico SDK board header for ProbePen-AI (selected in CMakeLists.txt with PICO_BOARD=probepen_ai).
 * RP2354B: the 80-pin RP2350B die with 2 MB of flash stacked in the package; 12 MHz crystal. */
#ifndef _BOARDS_PROBEPEN_AI_H
#define _BOARDS_PROBEPEN_AI_H

// pico_cmake_set PICO_PLATFORM=rp2350
// pico_cmake_set_default PICO_FLASH_SIZE_BYTES = (2 * 1024 * 1024)

/* 48 GPIOs: the ADC buses use GPIO16..39 and the MCU ADC inputs GPIO40..47 */
#ifndef PICO_RP2350A
#define PICO_RP2350A 0
#endif

#ifndef PICO_FLASH_SIZE_BYTES
#define PICO_FLASH_SIZE_BYTES (2 * 1024 * 1024)
#endif

#ifndef PICO_FLASH_SPI_CLKDIV
#define PICO_FLASH_SPI_CLKDIV 2
#endif

#ifndef PICO_XOSC_STARTUP_DELAY_MULTIPLIER
#define PICO_XOSC_STARTUP_DELAY_MULTIPLIER 64   /* small 2520 crystal: allow a slow start */
#endif

/* No UART on this board: stdio goes over USB */
#define PICO_DEFAULT_UART_TX_PIN -1
#define PICO_DEFAULT_UART_RX_PIN -1

#endif
