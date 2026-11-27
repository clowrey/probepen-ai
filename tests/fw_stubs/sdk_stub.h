/* Declarations of the Pico SDK 2.x calls the firmware uses, with the SDK's signatures, so the tests can
 * type-check the hardware code path (-DPICO_RP2350) without the SDK installed. Nothing here links or runs. */
#ifndef PROBEPEN_SDK_STUB_H
#define PROBEPEN_SDK_STUB_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>

typedef unsigned int uint;
typedef volatile uint32_t io_rw_32;
typedef const volatile uint32_t io_ro_32;
typedef volatile uint32_t io_wo_32;

/* pico/time, pico/stdlib */
typedef uint64_t absolute_time_t;
absolute_time_t get_absolute_time(void);
uint32_t to_ms_since_boot(absolute_time_t t);
void sleep_ms(uint32_t ms);
static inline void tight_loop_contents(void) {}
bool stdio_init_all(void);
int getchar_timeout_us(uint32_t timeout_us);
#define PICO_ERROR_TIMEOUT (-1)
#define PICO_OK 0
void hard_assert(bool condition);
typedef struct stdio_driver {
    void (*out_chars)(const char *buf, int len);
    void (*out_flush)(void);
    int (*in_chars)(char *buf, int len);
} stdio_driver_t;
extern stdio_driver_t stdio_usb;
void stdio_set_translate_crlf(stdio_driver_t *driver, bool translate);

/* hardware/gpio */
enum gpio_function_rp2350 { GPIO_FUNC_SPI = 1, GPIO_FUNC_PIO0 = 6, GPIO_FUNC_PIO1 = 7, GPIO_FUNC_NULL = 0x1f };
typedef enum gpio_function_rp2350 gpio_function_t;
#define GPIO_OUT 1
#define GPIO_IN 0
void gpio_init(uint gpio);
void gpio_set_function(uint gpio, gpio_function_t fn);
void gpio_set_dir(uint gpio, bool out);
void gpio_put(uint gpio, bool value);
bool gpio_get(uint gpio);
void gpio_pull_up(uint gpio);
void gpio_disable_pulls(uint gpio);
enum gpio_slew_rate { GPIO_SLEW_RATE_SLOW = 0, GPIO_SLEW_RATE_FAST = 1 };
enum gpio_drive_strength { GPIO_DRIVE_STRENGTH_2MA = 0, GPIO_DRIVE_STRENGTH_4MA = 1, GPIO_DRIVE_STRENGTH_8MA = 2,
                           GPIO_DRIVE_STRENGTH_12MA = 3 };
void gpio_set_slew_rate(uint gpio, enum gpio_slew_rate slew);
void gpio_set_drive_strength(uint gpio, enum gpio_drive_strength drive);

/* hardware/adc */
void adc_init(void);
void adc_gpio_init(uint gpio);
void adc_select_input(uint input);
uint16_t adc_read(void);

/* hardware/spi */
typedef struct { io_rw_32 cr0, cr1, dr, sr, cpsr, imsc, ris, mis, icr, dmacr; } spi_hw_t;
typedef struct spi_inst spi_inst_t;
extern spi_inst_t *const spi1_inst;
#define spi1 spi1_inst
typedef enum { SPI_CPOL_0 = 0, SPI_CPOL_1 = 1 } spi_cpol_t;
typedef enum { SPI_CPHA_0 = 0, SPI_CPHA_1 = 1 } spi_cpha_t;
typedef enum { SPI_LSB_FIRST = 0, SPI_MSB_FIRST = 1 } spi_order_t;
uint spi_init(spi_inst_t *spi, uint baudrate);
void spi_set_format(spi_inst_t *spi, uint data_bits, spi_cpol_t cpol, spi_cpha_t cpha, spi_order_t order);
int spi_write_blocking(spi_inst_t *spi, const uint8_t *src, size_t len);
spi_hw_t *spi_get_hw(spi_inst_t *spi);
uint spi_get_dreq(spi_inst_t *spi, bool is_tx);
bool spi_is_busy(const spi_inst_t *spi);

/* hardware/dma */
enum dma_channel_transfer_size { DMA_SIZE_8 = 0, DMA_SIZE_16 = 1, DMA_SIZE_32 = 2 };
typedef struct { uint32_t ctrl; } dma_channel_config;
int dma_claim_unused_channel(bool required);
dma_channel_config dma_channel_get_default_config(uint channel);
void channel_config_set_transfer_data_size(dma_channel_config *c, enum dma_channel_transfer_size size);
void channel_config_set_read_increment(dma_channel_config *c, bool incr);
void channel_config_set_write_increment(dma_channel_config *c, bool incr);
void channel_config_set_dreq(dma_channel_config *c, uint dreq);
void channel_config_set_ring(dma_channel_config *c, bool write, uint size_bits);
typedef struct { io_rw_32 read_addr, write_addr, transfer_count, ctrl_trig; } dma_channel_hw_t;
dma_channel_hw_t *dma_channel_hw_addr(uint channel);
void dma_channel_configure(uint channel, const dma_channel_config *config, volatile void *write_addr,
                           const volatile void *read_addr, uint transfer_count, bool trigger);
void dma_channel_wait_for_finish_blocking(uint channel);
bool dma_channel_is_busy(uint channel);
void dma_channel_abort(uint channel);
void dma_start_channel_mask(uint32_t chan_mask);
void dma_channel_unclaim(uint channel);

/* hardware/pio */
typedef struct { io_rw_32 ctrl, fstat, fdebug, flevel; io_wo_32 txf[4]; io_ro_32 rxf[4]; } pio_hw_t;
typedef pio_hw_t *PIO;
#define PIO_FDEBUG_RXSTALL_LSB 0u
extern pio_hw_t *const pio0_hw_ptr;
extern pio_hw_t *const pio1_hw_ptr;
#define pio0 pio0_hw_ptr
#define pio1 pio1_hw_ptr
typedef struct { const uint16_t *instructions; uint8_t length; int8_t origin; uint8_t pio_version; } pio_program_t;
typedef struct { uint32_t clkdiv, execctrl, shiftctrl, pinctrl; } pio_sm_config;
enum pio_fifo_join { PIO_FIFO_JOIN_NONE = 0, PIO_FIFO_JOIN_TX = 1, PIO_FIFO_JOIN_RX = 2 };
pio_sm_config pio_get_default_sm_config(void);
int pio_add_program(PIO pio, const pio_program_t *program);
int pio_set_gpio_base(PIO pio, uint gpio_base);
void pio_gpio_init(PIO pio, uint pin);
int pio_sm_set_consecutive_pindirs(PIO pio, uint sm, uint pin_base, uint pin_count, bool is_out);
void sm_config_set_in_pins(pio_sm_config *c, uint in_base);
void sm_config_set_sideset_pins(pio_sm_config *c, uint sideset_base);
void sm_config_set_sideset(pio_sm_config *c, uint bit_count, bool optional, bool pindirs);
void sm_config_set_wrap(pio_sm_config *c, uint wrap_target, uint wrap);
void sm_config_set_in_shift(pio_sm_config *c, bool shift_right, bool autopush, uint push_threshold);
void sm_config_set_fifo_join(pio_sm_config *c, enum pio_fifo_join join);
void sm_config_set_clkdiv(pio_sm_config *c, float div);
void sm_config_set_clkdiv_int_frac(pio_sm_config *c, uint16_t div_int, uint8_t div_frac);
void pio_enable_sm_mask_in_sync(PIO pio, uint32_t mask);
void pio_set_sm_mask_enabled(PIO pio, uint32_t mask, bool enabled);
int pio_sm_init(PIO pio, uint sm, uint initial_pc, const pio_sm_config *config);
void pio_sm_set_enabled(PIO pio, uint sm, bool enabled);
void pio_sm_clear_fifos(PIO pio, uint sm);
void pio_sm_restart(PIO pio, uint sm);
void pio_sm_exec(PIO pio, uint sm, uint instr);
void pio_sm_set_clkdiv_int_frac(PIO pio, uint sm, uint16_t div_int, uint8_t div_frac);
uint pio_get_dreq(PIO pio, uint sm, bool is_tx);
uint pio_encode_jmp(uint addr);

/* hardware/flash, hardware/sync, addressmap */
#define XIP_BASE 0x10000000u
#define FLASH_PAGE_SIZE (1u << 8)
#define FLASH_SECTOR_SIZE (1u << 12)
void flash_range_erase(uint32_t flash_offs, size_t count);
void flash_range_program(uint32_t flash_offs, const uint8_t *data, size_t count);
uint32_t save_and_disable_interrupts(void);
void restore_interrupts(uint32_t status);

#endif
