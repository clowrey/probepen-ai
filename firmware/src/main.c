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
#include "hardware/clocks.h"
#include "hardware/dma.h"
#include "hardware/vreg.h"
#include "hardware/flash.h"
#include "hardware/gpio.h"
#include "hardware/sync.h"
#include "hardware/pio.h"
#include "hardware/spi.h"
#include "pico/stdlib.h"
#include "pico/stdio/driver.h"
#include "pico/stdio_usb.h"
#include "ad9288_capture.pio.h"
#include "pod_capture.pio.h"
#endif

#define CAPTURE_MAX 4096u
/* STREAM: the DMA ring the ADC runs into continuously, 64 KB = 16384 words = 8 ms at 2 MS/s raw. The scope
 * captures use its first CAPTURE_MAX words (their 16 KB ring is aligned too). */
#define STREAM_RING_BITS 16u
#define STREAM_RING_WORDS (1u << (STREAM_RING_BITS - 2u))

/* One buffer, used by whichever runs: the scope capture ring (first 16 KB), the stream ring (first 64 KB) or
 * a deep record (all of it, DEEP_BYTES). Aligned for the 64 KB DMA ring. The LCD framebuffer is its last
 * 63 KB: nothing is drawn while a deep record fills it, and the next frame is drawn from scratch. */
static uint32_t g_words[DEEP_BYTES / 4u] __attribute__((aligned(4u * STREAM_RING_WORDS)));
static uint16_t *const g_fb = (uint16_t *)((uint8_t *)g_words + DEEP_BYTES - 2u * LCD_PIXELS);
_Static_assert(DEEP_BYTES - 2u * LCD_PIXELS >= 4u * STREAM_RING_WORDS, "framebuffer must not overlap the stream ring");
static uint16_t g_ch_a[CAPTURE_MAX];
static uint16_t g_ch_b[CAPTURE_MAX];
static uint8_t g_nib[CAPTURE_MAX];
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

static bool g_lcd_busy;

/* Starts sending the framebuffer (about 13 ms at 40 MHz); lcd_flush_poll() finishes it. */
static void lcd_flush_begin(void) {
    lcd_panel_set_window(&LCD_BUS, 0u, 0u, (uint16_t)(LCD_WIDTH - 1u), (uint16_t)(LCD_HEIGHT - 1u));
    /* 16-bit frames send each RGB565 pixel MSB first, as the panel wants */
    spi_set_format(spi1, 16u, SPI_CPOL_0, SPI_CPHA_0, SPI_MSB_FIRST);
    gpio_put(PIN_LCD_DC, 1);
    gpio_put(PIN_LCD_CS_N, 0);
    dma_channel_config c = dma_channel_get_default_config((uint)g_lcd_dma);
    channel_config_set_transfer_data_size(&c, DMA_SIZE_16);
    channel_config_set_dreq(&c, spi_get_dreq(spi1, true));
    dma_channel_configure((uint)g_lcd_dma, &c, &spi_get_hw(spi1)->dr, g_fb, LCD_PIXELS, true);
    g_lcd_busy = true;
}

/* True when no frame is on its way (a frame in flight is finished here once its DMA is done). */
static bool lcd_flush_poll(void) {
    if (!g_lcd_busy) {
        return true;
    }
    if (dma_channel_is_busy((uint)g_lcd_dma) || spi_is_busy(spi1)) {
        return false;
    }
    gpio_put(PIN_LCD_CS_N, 1);
    spi_set_format(spi1, 8u, SPI_CPOL_0, SPI_CPHA_0, SPI_MSB_FIRST);
    g_lcd_busy = false;
    return true;
}

static void lcd_flush(void) {
    while (!lcd_flush_poll()) {   /* a frame from the decode screen may still be going out */
        tight_loop_contents();
    }
    lcd_flush_begin();
    while (!lcd_flush_poll()) {
        tight_loop_contents();
    }
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
    if (PROBEPEN_SYS_MHZ != 150) {
        /* Overclocked build: raise the core voltage first, then the PLL. clk_peri follows clk_sys, so this
         * comes before the LCD's SPI is set up; USB runs from its own PLL. To check on the first board: the
         * flash clock (QMI divider) and the AD9288 at 125 MS/s, both beyond their ratings. */
        vreg_set_voltage(VREG_VOLTAGE_1_20);
        sleep_ms(2);
        set_sys_clock_khz((uint32_t)PROBEPEN_SYS_MHZ * 1000u, true);
    }
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

static bool g_capturing;
static int g_adc_offset = -1;
static int g_pod_offset = -1;
static int g_cap_dma = -1;
static int g_deep_dma_b = -1;
static int g_lead_offset = -1;
static int g_encb_offset = -1;
#define ENCB_SM 3u   /* PIO0 state machine that clocks channel B (GPIO28) next to the 32-bit capture */

/* ENCB follows SM0's divider; started together with it, so both encode clocks rise on the same cycle */
static void encb_prepare(uint32_t clkdiv) {
    pio_sm_set_enabled(pio0, ENCB_SM, false);
    pio_sm_set_clkdiv_int_frac(pio0, ENCB_SM, (uint16_t)clkdiv, 0u);
    pio_sm_restart(pio0, ENCB_SM);
    pio_sm_exec(pio0, ENCB_SM, pio_encode_jmp((uint)g_encb_offset));
}
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
    g_cap_logic = (app_view(&g_app) == APP_MODE_LOGIC);
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
        encb_prepare(APP_PEAK_CLKDIV);
        g_peak_groups = n / 2u;
        g_peak_out = 0u;
        g_peak_done = 0u;
        g_peak_overrun = false;
        g_cap_words = g_peak_groups * g_peak_m;
    } else {
        pio_sm_set_clkdiv_int_frac(pio, sm, (uint16_t)k, 0u);   /* two PIO cycles per ADC sample */
        encb_prepare(k);
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
    if (g_cap_logic) {
        pio_sm_set_enabled(pio, sm, true);
    } else {
        pio_enable_sm_mask_in_sync(pio0, (1u << sm) | (1u << ENCB_SM));   /* ENCA and ENCB in phase */
    }
}

static void capture_finish(void) {
    PIO pio = g_cap_logic ? pio1 : pio0;
    pio_sm_set_enabled(pio, 0u, false);
    pio_sm_set_enabled(pio0, ENCB_SM, false);
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
    g_deep_dma_b = dma_claim_unused_channel(true);
    g_lead_offset = pio_add_program(pio0, &ad9288_byte_lead_program);
    g_encb_offset = pio_add_program(pio0, &ad9288_encb_program);
    ad9288_encb_pin_init(pio0, ENCB_SM);
    ad9288_encb_init(pio0, ENCB_SM, (uint)g_encb_offset, 2u);

    /* ADC_CLK runs at up to 75 MHz: fast slew and more drive than the 4 mA default */
    gpio_set_slew_rate(PIN_ADC_CLK, GPIO_SLEW_RATE_FAST);
    gpio_set_drive_strength(PIN_ADC_CLK, GPIO_DRIVE_STRENGTH_8MA);
    gpio_set_slew_rate(PIN_ADC_CLKB, GPIO_SLEW_RATE_FAST);
    gpio_set_drive_strength(PIN_ADC_CLKB, GPIO_DRIVE_STRENGTH_8MA);
    /* RP2350 erratum E9 (A2 stepping): a pad with its pull-down on can latch high at about 2 V once
     * driven, so the pod inputs run without pulls */
    for (uint pin = PIN_POD_CH0; pin <= PIN_POD_CH3; ++pin) {
        gpio_disable_pulls(pin);
    }
}

/* ---- USB streaming (STREAM command) ----------------------------------------------------------- */

#define STREAM_DMA_COUNT 0x0FFFFFFFu   /* words per DMA run (28-bit count): 134 s at 2 MS/s, then it restarts */
static stream_t g_stream;
static uint32_t g_stream_consumed;     /* raw words taken from the ring in this DMA run */
static uint64_t g_stream_bytes_last;
static uint32_t g_stream_t_last;

static void stream_emit(const uint8_t *block, size_t len, void *ctx) {
    (void)ctx;
    stdio_usb.out_chars((const char *)block, (int)len);   /* straight to the CDC driver, no translation */
}

static void stream_dma_start(void) {
    dma_channel_config c = dma_channel_get_default_config((uint)g_cap_dma);
    channel_config_set_transfer_data_size(&c, DMA_SIZE_32);
    channel_config_set_read_increment(&c, false);
    channel_config_set_write_increment(&c, true);
    channel_config_set_ring(&c, true, STREAM_RING_BITS);
    channel_config_set_dreq(&c, pio_get_dreq(pio0, 0u, false));
    g_stream_consumed = 0u;
    dma_channel_configure((uint)g_cap_dma, &c, g_words, &pio0->rxf[0], STREAM_DMA_COUNT, true);
}

static void stream_start(void) {
    /* stop whatever capture is in flight */
    dma_channel_abort((uint)g_cap_dma);
    pio_set_sm_mask_enabled(pio0, 1u | (1u << ENCB_SM), false);
    pio_sm_set_enabled(pio1, 0u, false);
    g_capturing = false;
    g_stream = g_app.stream_plan;      /* the plan app.c made when it accepted the command */
    gpio_put(PIN_CH1_GAIN_LO, g_app.range[0] == AFE_RANGE_10X);
    gpio_put(PIN_CH2_GAIN_LO, g_app.range[1] == AFE_RANGE_10X);
    pio_sm_set_clkdiv_int_frac(pio0, 0u, (uint16_t)g_stream.clkdiv, 0u);   /* two PIO cycles per sample */
    encb_prepare(g_stream.clkdiv);
    pio_sm_clear_fifos(pio0, 0u);
    pio_sm_restart(pio0, 0u);
    pio_sm_exec(pio0, 0u, pio_encode_jmp((uint)g_adc_offset));
    stream_dma_start();
    pio_enable_sm_mask_in_sync(pio0, 1u | (1u << ENCB_SM));
    g_app.streaming = true;
    g_app.stream_lost = 0u;
    g_app.stream_bps = 0.0f;
    g_stream_bytes_last = 0u;
    g_stream_t_last = to_ms_since_boot(get_absolute_time());
}

static void stream_stop(void) {
    pio_set_sm_mask_enabled(pio0, 1u | (1u << ENCB_SM), false);
    dma_channel_abort((uint)g_cap_dma);
    stream_flush(&g_stream, stream_emit, NULL);
    printf("STREAM END SAMPLES=%lu BLOCKS=%lu BYTES=%llu LOST=%lu LOSTSAMPLES=%lu\n",
           (unsigned long)g_stream.out_index, (unsigned long)g_stream.blocks, (unsigned long long)g_stream.bytes,
           (unsigned long)g_stream.lost_events, (unsigned long)g_stream.lost_samples);
    fflush(stdout);
    g_app.streaming = false;
}

/* Takes what the DMA has written since the last call, at most a few blocks' worth, so buttons and
 * commands are still read in between. */
static void stream_poll(uint32_t now_ms) {
    if (!dma_channel_is_busy((uint)g_cap_dma)) {
        /* the 28-bit count ran out (or the DMA stopped): drain what is left, then start a new run */
        uint32_t rest = STREAM_DMA_COUNT - g_stream_consumed;
        stream_skip_raw(&g_stream, rest, stream_emit, NULL);
        stream_dma_start();
        return;
    }
    uint32_t remaining = dma_channel_hw_addr((uint)g_cap_dma)->transfer_count & 0x0FFFFFFFu;
    uint32_t written = STREAM_DMA_COUNT - remaining;
    uint32_t avail = written - g_stream_consumed;
    if (avail > STREAM_RING_WORDS - 512u) {
        /* the host fell behind and the ring lapped: skip to half a ring behind the DMA */
        uint32_t drop = avail - STREAM_RING_WORDS / 2u;
        stream_skip_raw(&g_stream, drop, stream_emit, NULL);
        g_stream_consumed += drop;
        avail -= drop;
    }
    for (int pass = 0; pass < 8 && avail > 0u; ++pass) {
        uint32_t idx = g_stream_consumed % STREAM_RING_WORDS;
        uint32_t n = avail;
        if (n > STREAM_RING_WORDS - idx) {
            n = STREAM_RING_WORDS - idx;
        }
        if (n > 2048u) {
            n = 2048u;
        }
        stream_push_words(&g_stream, &g_words[idx], n, stream_emit, NULL);
        g_stream_consumed += n;
        avail -= n;
    }
    if (now_ms - g_stream_t_last >= 500u) {
        g_app.stream_bps = (float)(g_stream.bytes - g_stream_bytes_last) * 1000.0f / (float)(now_ms - g_stream_t_last);
        g_app.stream_lost = g_stream.lost_events;
        g_stream_bytes_last = g_stream.bytes;
        g_stream_t_last = now_ms;
    }
}

/* ---- deep record (DEEP command) --------------------------------------------------------------- */

#define DEEP_SM_LEAD   1u
#define DEEP_SM_FOLLOW 2u

static void deep_dma(uint ch, uint sm, uint8_t *dst, uint32_t words) {
    dma_channel_config c = dma_channel_get_default_config(ch);
    channel_config_set_transfer_data_size(&c, DMA_SIZE_32);
    channel_config_set_read_increment(&c, false);
    channel_config_set_write_increment(&c, true);
    channel_config_set_dreq(&c, pio_get_dreq(pio0, sm, false));
    dma_channel_configure(ch, &c, dst, &pio0->rxf[sm], words, false);
}

/* One byte-wide record of n samples per channel at 75 MHz / clkdiv: the leader reads CH1 (or CH2 alone),
 * the follower CH2. Also used by the burst decoder. */
static void deep_capture_start(uint8_t chmask, uint32_t n, uint32_t clkdiv) {
    dma_channel_abort((uint)g_cap_dma);
    pio_set_sm_mask_enabled(pio0, 1u | (1u << ENCB_SM) | (1u << DEEP_SM_LEAD) | (1u << DEEP_SM_FOLLOW), false);
    pio_sm_set_enabled(pio1, 0u, false);
    g_capturing = false;
    gpio_put(PIN_CH1_GAIN_LO, g_app.range[0] == AFE_RANGE_10X);
    gpio_put(PIN_CH2_GAIN_LO, g_app.range[1] == AFE_RANGE_10X);
    uint8_t *buf = (uint8_t *)g_words;
    uint32_t words = n / 4u;
    bool both = chmask == 3u;
    pio_sm_config lead = ad9288_byte_lead_program_get_default_config((uint)g_lead_offset);
    sm_config_set_sideset_pins(&lead, chmask == 2u ? AD9288_CLKB_PIN : AD9288_CLK_PIN);   /* clock the channel it reads */
    ad9288_byte_init(pio0, DEEP_SM_LEAD, (uint)g_lead_offset, lead, chmask == 2u ? 32u : 16u, clkdiv);
    pio_sm_clear_fifos(pio0, DEEP_SM_LEAD);
    deep_dma((uint)g_cap_dma, DEEP_SM_LEAD, buf, words);
    uint32_t mask = 1u << DEEP_SM_LEAD;
    if (both) {
        pio_sm_config fol = ad9288_byte_lead_program_get_default_config((uint)g_lead_offset);
        sm_config_set_sideset_pins(&fol, AD9288_CLKB_PIN);   /* channel B's own encode clock, in phase */
        ad9288_byte_init(pio0, DEEP_SM_FOLLOW, (uint)g_lead_offset, fol, 32u, clkdiv);
        pio_sm_clear_fifos(pio0, DEEP_SM_FOLLOW);
        deep_dma((uint)g_deep_dma_b, DEEP_SM_FOLLOW, buf + n, words);
        mask |= 1u << DEEP_SM_FOLLOW;
    }
    dma_start_channel_mask((1u << (uint)g_cap_dma) | (both ? (1u << (uint)g_deep_dma_b) : 0u));
    pio_enable_sm_mask_in_sync(pio0, mask);   /* same divider, same start cycle: the two stay in lockstep */
}

/* Takes the record planned by app.c. */
static void deep_start(void) {
    g_app.deep_busy = true;
    deep_capture_start(g_app.deep_mask, g_app.deep_n, g_app.deep_clkdiv);
}

static void deep_emit(const uint8_t *block, size_t len, void *ctx) {
    (void)ctx;
    stdio_usb.out_chars((const char *)block, (int)len);
}

/* When the DMA is done: codes, trigger, then the record over USB. */
static bool deep_poll(void) {
    if (dma_channel_is_busy((uint)g_cap_dma) || dma_channel_is_busy((uint)g_deep_dma_b)) {
        return false;
    }
    pio_set_sm_mask_enabled(pio0, (1u << DEEP_SM_LEAD) | (1u << DEEP_SM_FOLLOW), false);
    uint8_t *buf = (uint8_t *)g_words;
    uint32_t n = g_app.deep_n;
    uint8_t *ch1 = (g_app.deep_mask & 1u) ? buf : NULL;
    uint8_t *ch2 = (g_app.deep_mask == 3u) ? buf + n : ((g_app.deep_mask == 2u) ? buf : NULL);
    if (ch1) {
        deep_decode(ch1, n, true);     /* channel A's bus is bit-reversed on GPIO16..23 */
    }
    if (ch2) {
        deep_decode(ch2, n, false);
    }
    const uint8_t *src = (g_app.trig_src & 1u) ? ch2 : ch1;
    long trig = src ? deep_find_trigger(src, n, 1u, (uint8_t)g_app.trig_level, 3u, g_app.trig_slope == TRIG_FALLING) : -1;
    printf("DEEP DATA N=%lu TRIG=%ld\n", (unsigned long)n, trig);
    fflush(stdout);
    stream_t s;
    stream_plan(&s, g_app.deep_rate_hz, g_app.deep_mask, APP_ADC_MAX_HZ, 0u);
    s.avg = 1u;
    s.range_flags = (uint8_t)((g_app.range[0] == AFE_RANGE_10X ? STREAM_FLAG_CH1_10X : 0u) |
                              (g_app.range[1] == AFE_RANGE_10X ? STREAM_FLAG_CH2_10X : 0u));
    stream_push_codes(&s, ch1, ch2, n, deep_emit, NULL);
    stream_flush(&s, deep_emit, NULL);
    printf("DEEP END\n");
    fflush(stdout);
    g_app.deep_busy = false;
    return true;
}

/* ---- bus decoding (DECODE command, see probepen_decode.h) ------------------------------------- */

_Static_assert(DEC_BURST_BYTES <= DEEP_BYTES - 2u * LCD_PIXELS, "a decode burst must leave the framebuffer alone");
static dec_t g_dec;
static uint32_t g_dec_consumed;        /* live: raw words taken from the ring in this DMA run */
static uint64_t g_dec_t0_us;           /* when sample 0 was taken */
static uint64_t g_dec_burst_us;        /* burst: when the burst in flight started */

static void decode_emit(const dec_record_t *rec, void *ctx) {
    (void)ctx;
    char line[DEC_LINE_MAX];
    char text[DEC_TEXT_MAX];
    dec_format_line(&g_dec, rec, line, sizeof line);
    printf("%s\n", line);
    dec_format_text(rec, text, sizeof text);
    app_decode_text(&g_app, text);
}

static void decode_burst_start(void) {
    uint8_t mask = g_dec.src == DEC_SRC_DIFF ? 3u : g_dec.src;
    g_dec_burst_us = to_us_since_boot(get_absolute_time());
    deep_capture_start(mask, g_dec.burst_n, g_dec.clkdiv);
}

static void decode_start(void) {
    dma_channel_abort((uint)g_cap_dma);
    pio_set_sm_mask_enabled(pio0, 1u | (1u << ENCB_SM), false);
    pio_sm_set_enabled(pio1, 0u, false);
    g_capturing = false;
    g_dec = g_app.decode_plan;
    dec_reset(&g_dec);                 /* a restart's plan may carry the last run's figures */
    g_dec_t0_us = to_us_since_boot(get_absolute_time());
    if (g_dec.burst) {
        decode_burst_start();
    } else {   /* live: both channels run into the stream ring, as for STREAM */
        gpio_put(PIN_CH1_GAIN_LO, g_app.range[0] == AFE_RANGE_10X);
        gpio_put(PIN_CH2_GAIN_LO, g_app.range[1] == AFE_RANGE_10X);
        pio_sm_set_clkdiv_int_frac(pio0, 0u, (uint16_t)g_dec.clkdiv, 0u);
        encb_prepare(g_dec.clkdiv);
        pio_sm_clear_fifos(pio0, 0u);
        pio_sm_restart(pio0, 0u);
        pio_sm_exec(pio0, 0u, pio_encode_jmp((uint)g_adc_offset));
        stream_dma_start();
        g_dec_consumed = 0u;
        pio_enable_sm_mask_in_sync(pio0, 1u | (1u << ENCB_SM));
    }
    g_app.decoding = true;
}

static void decode_stop(void) {
    pio_set_sm_mask_enabled(pio0, 1u | (1u << ENCB_SM) | (1u << DEEP_SM_LEAD) | (1u << DEEP_SM_FOLLOW), false);
    dma_channel_abort((uint)g_cap_dma);
    dma_channel_abort((uint)g_deep_dma_b);
    dec_flush(&g_dec, decode_emit, NULL);
    printf("DECODE END RECORDS=%lu ERRORS=%lu LOST=%lu SAMPLES=%llu\n", (unsigned long)g_dec.records,
           (unsigned long)g_dec.errors, (unsigned long)g_dec.lost_events, (unsigned long long)g_dec.pos);
    fflush(stdout);
    dec_copy_stats(&g_app.decode_plan, &g_dec);
    g_app.decoding = false;
    while (!lcd_flush_poll()) {
        tight_loop_contents();
    }
}

static void decode_poll(void) {
    if (!g_dec.burst) {
        if (!dma_channel_is_busy((uint)g_cap_dma)) {   /* the 28-bit count ran out: start a new run */
            dec_skip(&g_dec, STREAM_DMA_COUNT - g_dec_consumed, true, decode_emit, NULL);
            stream_dma_start();
            g_dec_consumed = 0u;
            return;
        }
        uint32_t remaining = dma_channel_hw_addr((uint)g_cap_dma)->transfer_count & 0x0FFFFFFFu;
        uint32_t avail = (STREAM_DMA_COUNT - remaining) - g_dec_consumed;
        if (avail > STREAM_RING_WORDS - 512u) {   /* the decoder fell behind and the ring lapped */
            uint32_t drop = avail - STREAM_RING_WORDS / 2u;
            dec_skip(&g_dec, drop, true, decode_emit, NULL);
            g_dec_consumed += drop;
            avail -= drop;
        }
        for (int pass = 0; pass < 8 && avail > 0u; ++pass) {
            uint32_t idx = g_dec_consumed % STREAM_RING_WORDS;
            uint32_t n = avail;
            if (n > STREAM_RING_WORDS - idx) {
                n = STREAM_RING_WORDS - idx;
            }
            if (n > 2048u) {
                n = 2048u;
            }
            dec_push_words(&g_dec, &g_words[idx], n, decode_emit, NULL);
            g_dec_consumed += n;
            avail -= n;
        }
    } else {
        if (dma_channel_is_busy((uint)g_cap_dma) || dma_channel_is_busy((uint)g_deep_dma_b)) {
            return;
        }
        pio_set_sm_mask_enabled(pio0, (1u << DEEP_SM_LEAD) | (1u << DEEP_SM_FOLLOW), false);
        uint8_t *buf = (uint8_t *)g_words;
        uint32_t n = g_dec.burst_n;
        uint8_t *codes = buf;
        if (g_dec.src == DEC_SRC_CH2) {
            deep_decode(buf, n, false);
        } else {
            deep_decode(buf, n, true);          /* channel A's bus is bit-reversed on GPIO16..23 */
            if (g_dec.src == DEC_SRC_DIFF) {
                uint8_t *b = buf + n;
                deep_decode(b, n, false);
                for (uint32_t i = 0u; i < n; ++i) {
                    int v = 128 + (int)buf[i] - (int)b[i];
                    buf[i] = (uint8_t)(v < 0 ? 0 : (v > 255 ? 255 : v));
                }
            }
        }
        /* the time since the last burst ended went unseen */
        uint64_t at = (uint64_t)((double)(g_dec_burst_us - g_dec_t0_us) * 1e-6 * (double)g_dec.fs);
        if (at > g_dec.pos) {
            dec_skip(&g_dec, at - g_dec.pos, false, decode_emit, NULL);
        }
        dec_push_codes(&g_dec, codes, n, decode_emit, NULL);
        decode_burst_start();
    }
    dec_copy_stats(&g_app.decode_plan, &g_dec);   /* for the screen */
    app_decode_tick(&g_app);   /* UART auto-baud */
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
    char reply[512];
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
    g_capturing = true;
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
        app_ai_tick(&g_app, now);
        if (now - last_dc >= 100u) {
            app_set_dc(&g_app, adc_read_pin(PIN_CH1_DC_SENSE), adc_read_pin(PIN_CH2_DC_SENSE),
                       adc_read_pin(PIN_VBUS_SENSE));
            last_dc = now;
        }
        if (g_app.stream_req) {
            g_app.stream_req = false;
            if (g_app.stream_want_hz > 0.0f) {
                if (g_app.streaming) {
                    stream_stop();
                }
                stream_start();
                app_render(&g_app, g_fb, g_ch_a, g_ch_b, g_n, g_nib, g_n_nib);
                lcd_flush();
                last_draw = now;
            } else if (g_app.streaming) {
                stream_stop();
                capture_start();
                g_capturing = true;
            }
        }
        if (g_app.decode_req) {
            g_app.decode_req = false;
            if (g_app.decoding) {
                decode_stop();
            }
            if (!g_app.decode_stop) {
                decode_start();
            } else {
                capture_start();
                g_capturing = true;
            }
        }
        if (g_app.decoding) {
            decode_poll();
            /* the screen goes out by DMA while decoding goes on; drawn when the last frame is done */
            if (now - last_draw >= 250u && lcd_flush_poll()) {
                app_render(&g_app, g_fb, g_ch_a, g_ch_b, g_n, g_nib, g_n_nib);
                lcd_flush_begin();
                last_draw = now;
            }
            continue;
        }
        if (g_app.deep_req && !g_app.streaming) {
            g_app.deep_req = false;
            deep_start();
        }
        if (g_app.deep_busy) {
            if (deep_poll()) {
                capture_start();
                g_capturing = true;
            }
            continue;
        }
        if (g_app.streaming) {
            stream_poll(now);
            /* the LCD flush blocks for about 13 ms: refresh only while the ring holds far more than that */
            if (now - last_draw >= 1000u && g_stream.raw_hz < 500e3f) {
                app_render(&g_app, g_fb, g_ch_a, g_ch_b, g_n, g_nib, g_n_nib);
                lcd_flush();
                last_draw = now;
            }
            continue;
        }
        if (dma_channel_is_busy((uint)g_cap_dma)) {
            capture_poll();
            continue;
        }
        if (g_capturing) {
            capture_finish();
            g_capturing = false;
            fresh = true;
        }
        if (g_app.cal_changed) {
            cal_save();
            g_app.cal_changed = false;
        }
        if (g_app.ask_pending) {
            char meas[512];
            app_format_meas(&g_app, meas, sizeof meas);
            printf("ASK %s\n", meas);
            send_frames(g_app.win_start, g_app.win_len);
            g_app.ask_pending = false;
        }
        if (g_app.capture_req) {
            send_frames(g_app.win_start, g_app.capture_req);
            g_app.capture_req = 0u;
        }
        if (g_app.ai_req && app_view(&g_app) == APP_MODE_DUAL) {   /* AI mode's question: this capture */
            char meas[512];
            app_format_meas(&g_app, meas, sizeof meas);
            printf("AIASK %s\n", meas);
            send_frames(g_app.win_start, g_app.win_len);
            g_app.ai_req = false;
        }
        if (fresh || now - last_draw >= 50u) {   /* in HOLD, redraw at 20 Hz so button changes show */
            app_render(&g_app, g_fb, g_ch_a, g_ch_b, g_n, g_nib, g_n_nib);
            lcd_flush();
            last_draw = now;
            fresh = false;
        }
        if (app_view(&g_app) == APP_MODE_LOGIC) {
            g_app.cal_pending = false;     /* CAL needs a scope capture; dropped if the mode changed */
        }
        if (g_app.run || g_app.cal_pending) {
            capture_start();
            g_capturing = true;
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
    char meas[512];
    app_format_meas(&g_app, meas, sizeof meas);
    printf("%s\n", meas);
    (void)send_frames;
    return 0;
}

#endif
