/* ProbePen-AI main loop.
 *
 * One capture is always in flight: PIO0 clocks the AD9288 and DMA collects one 32-bit word per sample
 * (scope modes), or PIO1 samples the logic pod and DMA collects 8 samples per word (logic mode). While the
 * DMA runs, the loop reads the buttons and the USB command line. When it finishes, the app measures,
 * draws the screen and the next capture starts. The LCD frame goes out over SPI1 with 16-bit DMA.
 *
 * Without PICO_RP2350 (host or plain cross-compile) the file builds a one-shot demo on a synthetic signal.
 */
#include <stdio.h>
#include <string.h>

#include "probepen_app.h"
#include "probepen_lcd.h"
#include "probepen_logic.h"
#include "probepen_pins.h"
#include "probepen_scope.h"
#include "probepen_usb.h"

#include "probepen_calstore.h"

#ifdef PICO_RP2350
#include "hardware/adc.h"
#include "hardware/dma.h"
#include "hardware/flash.h"
#include "hardware/gpio.h"
#include "hardware/sync.h"
#include "hardware/pio.h"
#include "hardware/spi.h"
#include "pico/stdlib.h"
#include "pico/stdio_usb.h"
#include "ad9288_capture.pio.h"
#include "pod_capture.pio.h"
#endif

#define CAPTURE_MAX 4096u

static uint32_t g_words[CAPTURE_MAX] __attribute__((aligned(4u * CAPTURE_MAX)));   /* DMA ring in peak detect */
static uint16_t g_ch_a[CAPTURE_MAX];
static uint16_t g_ch_b[CAPTURE_MAX];
static uint8_t g_nib[CAPTURE_MAX];
static uint16_t g_fb[LCD_PIXELS];
static uint8_t g_frame[7u + 2u * CAPTURE_MAX];
static app_t g_app;
static size_t g_n;       /* scope samples in g_ch_a / g_ch_b */
static size_t g_n_nib;   /* pod samples in g_nib */

/* Sends both channels of the last capture as framed binary (see usb_encode_wave_frame), seq 0 = CH1. */
static void send_frames(size_t start, size_t count) {
    if (start >= g_n) {
        return;
    }
    if (start + count > g_n) {
        count = g_n - start;
    }
    for (uint8_t ch = 0u; ch < 2u; ++ch) {
        size_t len = usb_encode_wave_frame(ch, (ch ? g_ch_b : g_ch_a) + start, count, g_frame, sizeof g_frame);
        fwrite(g_frame, 1u, len, stdout);
    }
    fflush(stdout);
}

#ifdef PICO_RP2350

/* ---- LCD on SPI1 ----------------------------------------------------------------------------- */

static void lcd_write_cmd(void *ctx, uint8_t cmd) {
    (void)ctx;
    gpio_put(PIN_LCD_DC, 0);
    gpio_put(PIN_LCD_CS_N, 0);
    spi_write_blocking(spi1, &cmd, 1u);
    gpio_put(PIN_LCD_CS_N, 1);
}

static void lcd_write_data(void *ctx, const uint8_t *data, size_t len) {
    (void)ctx;
    gpio_put(PIN_LCD_DC, 1);
    gpio_put(PIN_LCD_CS_N, 0);
    spi_write_blocking(spi1, data, len);
    gpio_put(PIN_LCD_CS_N, 1);
}

static void lcd_delay(void *ctx, uint32_t ms) {
    (void)ctx;
    sleep_ms(ms);
}

static const lcd_bus_t LCD_BUS = {lcd_write_cmd, lcd_write_data, lcd_delay, NULL};
static int g_lcd_dma = -1;

static void lcd_flush(void) {
    lcd_panel_set_window(&LCD_BUS, 0u, 0u, (uint16_t)(LCD_WIDTH - 1u), (uint16_t)(LCD_HEIGHT - 1u));
    /* 16-bit frames send each RGB565 pixel MSB first, as the panel wants */
    spi_set_format(spi1, 16u, SPI_CPOL_0, SPI_CPHA_0, SPI_MSB_FIRST);
    gpio_put(PIN_LCD_DC, 1);
    gpio_put(PIN_LCD_CS_N, 0);
    dma_channel_config c = dma_channel_get_default_config((uint)g_lcd_dma);
    channel_config_set_transfer_data_size(&c, DMA_SIZE_16);
    channel_config_set_dreq(&c, spi_get_dreq(spi1, true));
    dma_channel_configure((uint)g_lcd_dma, &c, &spi_get_hw(spi1)->dr, g_fb, LCD_PIXELS, true);
    dma_channel_wait_for_finish_blocking((uint)g_lcd_dma);
    while (spi_is_busy(spi1)) {
        tight_loop_contents();
    }
    gpio_put(PIN_LCD_CS_N, 1);
    spi_set_format(spi1, 8u, SPI_CPOL_0, SPI_CPHA_0, SPI_MSB_FIRST);
}

static void lcd_init_hw(void) {
    spi_init(spi1, 40000000u);
    gpio_set_function(PIN_LCD_SCK, GPIO_FUNC_SPI);
    gpio_set_function(PIN_LCD_MOSI, GPIO_FUNC_SPI);
    const uint8_t outs[] = {PIN_LCD_CS_N, PIN_LCD_DC, PIN_LCD_RST_N};
    for (unsigned i = 0u; i < sizeof outs; ++i) {
        gpio_init(outs[i]);
        gpio_set_dir(outs[i], GPIO_OUT);
        gpio_put(outs[i], 1);
    }
    gpio_put(PIN_LCD_RST_N, 0);
    sleep_ms(10);
    gpio_put(PIN_LCD_RST_N, 1);
    sleep_ms(120);
    g_lcd_dma = dma_claim_unused_channel(true);
    lcd_panel_init(&LCD_BUS);
    gpio_put(PIN_LCD_BLK_GATE_N, 0);   /* P-FET high-side switch: low = backlight on */
}

/* ---- inputs ---------------------------------------------------------------------------------- */

static void gpio_out(uint pin, bool level) {
    gpio_init(pin);
    gpio_set_dir(pin, GPIO_OUT);
    gpio_put(pin, level);
}

static void hal_init(void) {
    stdio_init_all();
    stdio_set_translate_crlf(&stdio_usb, false);   /* binary wave frames go out on the same port */
    gpio_out(PIN_ADC_S1, 1);           /* AD9288 S1/S2 = 1/1: both channels running, outputs aligned */
    gpio_out(PIN_ADC_S2, 1);
    gpio_out(PIN_CH1_GAIN_LO, 1);      /* high = +-25 V range, the safe default */
    gpio_out(PIN_CH2_GAIN_LO, 1);
    gpio_out(PIN_LCD_BLK_GATE_N, 1);   /* backlight off until the panel is initialised */
    const uint8_t buttons[BTN_COUNT] = {PIN_BTN_ASK_AI_N, PIN_BTN_MODE_N, PIN_BTN_UP_N, PIN_BTN_DOWN_N};
    for (unsigned i = 0u; i < BTN_COUNT; ++i) {
        gpio_init(buttons[i]);
        gpio_set_dir(buttons[i], GPIO_IN);
        gpio_pull_up(buttons[i]);
    }
    adc_init();
    adc_gpio_init(PIN_CH1_DC_SENSE);
    adc_gpio_init(PIN_CH2_DC_SENSE);
    adc_gpio_init(PIN_VBUS_SENSE);
}

static uint8_t read_buttons(void) {
    const uint8_t pins[BTN_COUNT] = {PIN_BTN_ASK_AI_N, PIN_BTN_MODE_N, PIN_BTN_UP_N, PIN_BTN_DOWN_N};
    uint8_t mask = 0u;
    for (unsigned i = 0u; i < BTN_COUNT; ++i) {
        if (!gpio_get(pins[i])) {
            mask = (uint8_t)(mask | (1u << i));
        }
    }
    return mask;
}

static uint16_t adc_read_pin(uint pin) {
    adc_select_input(pin - 40u);   /* RP2354B: ADC0..7 = GPIO40..47 */
    uint32_t sum = 0u;
    for (int i = 0; i < 16; ++i) {
        sum += adc_read();
    }
    return (uint16_t)(sum / 16u);
}

/* ---- captures -------------------------------------------------------------------------------- */

static int g_adc_offset = -1;
static int g_pod_offset = -1;
static int g_cap_dma = -1;
static bool g_cap_logic;
static size_t g_cap_words;

/* Peak detect: DMA writes raw words round a 4096-word ring (g_words, 16 KB aligned) while the main loop
 * folds every g_peak_m words into a min/max pair. */
#define RING_BITS 14u   /* 16 KB = CAPTURE_MAX words */
static uint32_t g_peak_m;        /* 0 = direct capture */
static size_t g_peak_groups;     /* min/max pairs to produce */
static size_t g_peak_out;        /* pairs produced so far */
static size_t g_peak_done;       /* raw words consumed so far */
static bool g_peak_overrun;

static void capture_poll(void) {
    if (!g_peak_m) {
        return;
    }
    for (;;) {
        /* re-read every pass: DMA keeps writing (about m/4 words) while a group is folded */
        size_t remaining = dma_channel_hw_addr((uint)g_cap_dma)->transfer_count & 0x0FFFFFFFu;
        size_t written = g_cap_words - remaining;
        if (written - g_peak_done < g_peak_m || g_peak_out >= g_peak_groups) {
            break;
        }
        if (written - g_peak_done + g_peak_m > CAPTURE_MAX) {
            /* the oldest words of this group may be overwritten before they are read: skip ahead */
            g_peak_overrun = true;
            g_peak_done = written - g_peak_m;
        }
        uint16_t a[2], b[2];
        scope_peak_group(g_words, CAPTURE_MAX, g_peak_done % CAPTURE_MAX, g_peak_m, a, b);
        g_ch_a[2u * g_peak_out] = a[0];
        g_ch_a[2u * g_peak_out + 1u] = a[1];
        g_ch_b[2u * g_peak_out] = b[0];
        g_ch_b[2u * g_peak_out + 1u] = b[1];
        g_peak_done += g_peak_m;
        ++g_peak_out;
    }
}

static void capture_start(void) {
    g_cap_logic = (g_app.mode == APP_MODE_LOGIC);
    size_t n = app_capture_samples(&g_app, CAPTURE_MAX);
    uint32_t k = app_clkdiv(&g_app);
    PIO pio = g_cap_logic ? pio1 : pio0;
    uint sm = 0u;
    g_peak_m = g_cap_logic ? 0u : app_peak_factor(&g_app);
    if (g_cap_logic) {
        /* one PIO cycle per pod sample; APP_CLKDIV_MAX keeps 2k inside the 16-bit divider */
        pio_sm_set_clkdiv_int_frac(pio, sm, (uint16_t)(2u * k), 0u);
        g_cap_words = (n + 7u) / 8u;
    } else if (g_peak_m) {
        pio_sm_set_clkdiv_int_frac(pio, sm, (uint16_t)APP_PEAK_CLKDIV, 0u);
        g_peak_groups = n / 2u;
        g_peak_out = 0u;
        g_peak_done = 0u;
        g_peak_overrun = false;
        g_cap_words = g_peak_groups * g_peak_m;
    } else {
        pio_sm_set_clkdiv_int_frac(pio, sm, (uint16_t)k, 0u);   /* two PIO cycles per ADC sample */
        g_cap_words = n;
    }
    gpio_put(PIN_CH1_GAIN_LO, g_app.range[0] == AFE_RANGE_10X);
    gpio_put(PIN_CH2_GAIN_LO, g_app.range[1] == AFE_RANGE_10X);

    dma_channel_config c = dma_channel_get_default_config((uint)g_cap_dma);
    channel_config_set_transfer_data_size(&c, DMA_SIZE_32);
    channel_config_set_read_increment(&c, false);
    channel_config_set_write_increment(&c, true);
    channel_config_set_ring(&c, true, g_peak_m ? RING_BITS : 0u);
    channel_config_set_dreq(&c, pio_get_dreq(pio, sm, false));
    pio_sm_set_enabled(pio, sm, false);
    pio_sm_clear_fifos(pio, sm);
    pio_sm_restart(pio, sm);
    pio_sm_exec(pio, sm, pio_encode_jmp((uint)(g_cap_logic ? g_pod_offset : g_adc_offset)));
    pio->fdebug = 1u << (PIO_FDEBUG_RXSTALL_LSB + sm);   /* clear the stall flag (write 1) */
    dma_channel_configure((uint)g_cap_dma, &c, g_words, &pio->rxf[sm], g_cap_words, true);
    pio_sm_set_enabled(pio, sm, true);
}

static void capture_finish(void) {
    PIO pio = g_cap_logic ? pio1 : pio0;
    pio_sm_set_enabled(pio, 0u, false);
    /* a full RX FIFO stalls `in` (and stretches ADC_CLK) without any other sign: report it */
    g_app.overrun = (pio->fdebug & (1u << PIO_FDEBUG_RXSTALL_LSB)) != 0u || (g_peak_m && g_peak_overrun);
    if (g_cap_logic) {
        g_n_nib = logic_unpack_pod_nibbles(g_words, g_cap_words, g_nib, CAPTURE_MAX);
        app_process_logic(&g_app, g_nib, g_n_nib);
    } else if (g_peak_m) {
        capture_poll();                    /* fold the last words */
        g_n = 2u * g_peak_out;             /* already bit-reversed and inverted */
        app_process_scope(&g_app, g_ch_a, g_ch_b, g_n);
    } else {
        g_n = scope_unpack_ad9288(g_words, g_cap_words, g_ch_a, g_ch_b, CAPTURE_MAX);
        scope_invert_codes(g_ch_a, g_n);   /* inverting gain stage */
        scope_invert_codes(g_ch_b, g_n);
        app_process_scope(&g_app, g_ch_a, g_ch_b, g_n);
    }
}

static void capture_init(void) {
    /* GPIO16..47 window first: the SDK will not move the base once PIO0 holds a program */
    int rc = pio_set_gpio_base(pio0, AD9288_GPIO_BASE);   /* not inside the assert: it may compile out */
    hard_assert(rc == PICO_OK);
    (void)rc;
    g_adc_offset = pio_add_program(pio0, &ad9288_capture_program);
    ad9288_capture_program_init(pio0, 0u, (uint)g_adc_offset, 2.0f);
    g_pod_offset = pio_add_program(pio1, &pod_capture_program);
    pod_capture_program_init(pio1, 0u, (uint)g_pod_offset, 2.0f);
    g_cap_dma = dma_claim_unused_channel(true);

    /* ADC_CLK runs at up to 75 MHz: fast slew and more drive than the 4 mA default */
    gpio_set_slew_rate(PIN_ADC_CLK, GPIO_SLEW_RATE_FAST);
    gpio_set_drive_strength(PIN_ADC_CLK, GPIO_DRIVE_STRENGTH_8MA);
    /* RP2350 erratum E9 (A2 stepping): a pad with its pull-down on can latch high at about 2 V once
     * driven, so the pod inputs run without pulls */
    for (uint pin = PIN_POD_CH0; pin <= PIN_POD_CH3; ++pin) {
        gpio_disable_pulls(pin);
    }
}

/* ---- calibration, one sector below the top of flash ------------------------------------------ */

/* Not the last sector: for RP2350, picotool adds a UF2 block at 0x10FFFF00 (erratum E10 workaround),
 * which may alias into the top sector of a 2 MB part and erase the record on every drag-and-drop. */
#define CAL_FLASH_OFFSET (PICO_FLASH_SIZE_BYTES - 2u * FLASH_SECTOR_SIZE)

static void cal_load(void) {
    afe_cal_t cal[2];
    if (calstore_unpack((const uint8_t *)(XIP_BASE + CAL_FLASH_OFFSET), cal)) {
        g_app.cal[0] = cal[0];
        g_app.cal[1] = cal[1];
    }
}

static void cal_save(void) {
    static uint8_t page[CALSTORE_BYTES];
    calstore_pack(g_app.cal, page);
    uint32_t irq = save_and_disable_interrupts();
    flash_range_erase(CAL_FLASH_OFFSET, FLASH_SECTOR_SIZE);
    flash_range_program(CAL_FLASH_OFFSET, page, FLASH_PAGE_SIZE);
    restore_interrupts(irq);
}

/* ---- USB command line ------------------------------------------------------------------------ */

static void usb_poll(void) {
    static char line[64];
    static size_t len;
    char reply[320];
    for (int guard = 0; guard < 64; ++guard) {
        int c = getchar_timeout_us(0);
        if (c == PICO_ERROR_TIMEOUT) {
            return;
        }
        if (c == '\r' || c == '\n') {
            if (len > 0u) {
                line[len] = '\0';
                app_usb_command(&g_app, line, reply, sizeof reply);
                printf("%s\n", reply);
                fflush(stdout);
                len = 0u;
            }
        } else if (len + 1u < sizeof line) {
            line[len++] = (char)c;
        }
    }
}

int main(void) {
    hal_init();
    app_init(&g_app);
    cal_load();
    buttons_t btn;
    buttons_init(&btn);
    lcd_init_hw();
    capture_init();
    capture_start();
    bool capturing = true;
    bool fresh = false;                /* a capture finished since the last draw */
    uint32_t last_draw = 0u;

    uint32_t last_dc = 0u;
    for (;;) {
        uint32_t now = to_ms_since_boot(get_absolute_time());
        uint8_t ev[BTN_COUNT];
        size_t nev = buttons_update(&btn, read_buttons(), now, ev, BTN_COUNT);
        for (size_t i = 0u; i < nev; ++i) {
            app_button_event(&g_app, (uint8_t)(ev[i] >> 4), (btn_event_t)(ev[i] & 0x0Fu));
        }
        usb_poll();
        if (now - last_dc >= 100u) {
            app_set_dc(&g_app, adc_read_pin(PIN_CH1_DC_SENSE), adc_read_pin(PIN_CH2_DC_SENSE),
                       adc_read_pin(PIN_VBUS_SENSE));
            last_dc = now;
        }
        if (dma_channel_is_busy((uint)g_cap_dma)) {
            capture_poll();
            continue;
        }
        if (capturing) {
            capture_finish();
            capturing = false;
            fresh = true;
        }
        if (g_app.cal_changed) {
            cal_save();
            g_app.cal_changed = false;
        }
        if (g_app.ask_pending) {
            char meas[320];
            app_format_meas(&g_app, meas, sizeof meas);
            printf("ASK %s\n", meas);
            send_frames(g_app.win_start, g_app.win_len);
            g_app.ask_pending = false;
        }
        if (g_app.capture_req) {
            send_frames(g_app.win_start, g_app.capture_req);
            g_app.capture_req = 0u;
        }
        if (fresh || now - last_draw >= 50u) {   /* in HOLD, redraw at 20 Hz so button changes show */
            app_render(&g_app, g_fb, g_ch_a, g_ch_b, g_n, g_nib, g_n_nib);
            lcd_flush();
            last_draw = now;
            fresh = false;
        }
        if (g_app.mode == APP_MODE_LOGIC) {
            g_app.cal_pending = false;     /* CAL needs a scope capture; dropped if the mode changed */
        }
        if (g_app.run || g_app.cal_pending) {
            capture_start();
            capturing = true;
        }
    }
}

#else  /* host / plain cross-compile: one frame on a synthetic signal */

int main(void) {
    app_init(&g_app);
    g_n = app_capture_samples(&g_app, CAPTURE_MAX);
    for (size_t i = 0u; i < g_n; ++i) {
        uint32_t phase = (uint32_t)(i % 64u);
        uint8_t a = (uint8_t)(phase < 32u ? 64u + 4u * phase : 64u + 4u * (63u - phase));
        uint8_t b = (uint8_t)(phase < 32u ? 200u : 60u);
        uint8_t ra = 0u;
        for (int k = 0; k < 8; ++k) {
            ra = (uint8_t)(ra | (((a >> k) & 1u) << (7 - k)));
        }
        g_words[i] = (uint32_t)ra | ((uint32_t)b << 16);
    }
    g_n = scope_unpack_ad9288(g_words, g_n, g_ch_a, g_ch_b, CAPTURE_MAX);
    app_process_scope(&g_app, g_ch_a, g_ch_b, g_n);
    app_render(&g_app, g_fb, g_ch_a, g_ch_b, g_n, g_nib, g_n_nib);
    char meas[320];
    app_format_meas(&g_app, meas, sizeof meas);
    printf("%s\n", meas);
    (void)send_frames;
    return 0;
}

#endif
