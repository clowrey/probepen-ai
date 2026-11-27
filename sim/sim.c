/* ProbePen-AI web simulator: the board around the firmware.
 *
 * Everything the user sees on the simulated LCD is drawn by the firmware's own code (firmware/src, all
 * but main.c), compiled to WebAssembly. This file plays the hardware that main.c drives on the real
 * board: the probe signals, the front end (divider, range switch, inverting gain stage, 31 MHz
 * anti-alias pole), the AD9288 and its PIO/DMA capture (raw 32-bit words, bit-reversed channel A, peak
 * detect through the same ring-group code), the logic pod, the DC-sense ADC, the buttons and the USB
 * serial link. The main loop below follows main.c step for step.
 *
 * Builds natively too (tests/test_sim.py) so the same code is checked on the host.
 */
#include <math.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

#include "probepen_app.h"
#include "probepen_lcd.h"
#include "probepen_logic.h"
#include "probepen_scope.h"
#include "probepen_stream.h"
#include "probepen_usb.h"

#if defined(__wasm__)
#define EXPORT __attribute__((visibility("default"), used))
#else
#define EXPORT
#endif

#define CAPTURE_MAX 4096u

static uint32_t g_words[CAPTURE_MAX];
static uint16_t g_ch_a[CAPTURE_MAX];
static uint16_t g_ch_b[CAPTURE_MAX];
static uint8_t g_nib[CAPTURE_MAX];
static uint16_t g_fb[LCD_PIXELS];
static uint8_t g_frame[7u + 2u * CAPTURE_MAX];
static app_t g_app;
static buttons_t g_btn;
static size_t g_n;
static size_t g_n_nib;

/* ---- simulated probe signals ----------------------------------------------------------------- */

enum { SIG_GND = 0, SIG_DC, SIG_SINE, SIG_SQUARE, SIG_TRIANGLE, SIG_SAW, SIG_PULSE, SIG_UART, SIG_AM, SIG_COUNT };

typedef struct {
    int kind;
    float vpp;      /* peak-to-peak (UART / pulse: high level minus low level) */
    float offset;   /* DC level (centre; for UART / pulse the low level) */
    float freq;     /* Hz (UART: baud) */
    float duty;     /* 0..1, pulse width / period */
    float noise;    /* V rms */
} sig_t;

static sig_t g_sig[2] = {
    {SIG_SINE, 3.0f, 0.0f, 50e3f, 0.5f, 0.01f},
    {SIG_SQUARE, 3.3f, 1.65f, 25e3f, 0.5f, 0.005f},
};

enum { POD_COUNTER = 0, POD_SPI, POD_UART_PWM };
static int g_pod_kind = POD_COUNTER;
static float g_pod_freq = 1e6f;

static uint32_t g_rng = 0x12345678u;
static float rnd(void) {   /* uniform 0..1 */
    g_rng = g_rng * 1664525u + 1013904223u;
    return (float)(g_rng >> 8) * (1.0f / 16777216.0f);
}
static float gauss(void) {   /* about N(0, 1), cheap */
    return (rnd() + rnd() + rnd() + rnd() - 2.0f) * 1.7320508f;
}

static const char UART_MSG[] = "ProbePen-AI\r\n";

/* UART level (0 or 1) at bit time b (bits since the message began, 8N1 frames then 20 idle bits) */
static int uart_bit(double bits) {
    long len = (long)(sizeof UART_MSG - 1u);
    long frame = 10L;
    long period = len * frame + 20L;
    long b = (long)floor(bits);
    b %= period;
    if (b < 0) {
        b += period;
    }
    if (b >= len * frame) {
        return 1;   /* idle high */
    }
    long ch = b / frame;
    long k = b % frame;
    if (k == 0) {
        return 0;   /* start bit */
    }
    if (k == 9) {
        return 1;   /* stop bit */
    }
    return ((unsigned char)UART_MSG[ch] >> (k - 1)) & 1;
}

static float sig_value(const sig_t *s, double t) {
    double ph = t * (double)s->freq;
    double f = ph - floor(ph);
    float v;
    switch (s->kind) {
    case SIG_DC:
        v = s->offset;
        break;
    case SIG_SINE:
        v = s->offset + 0.5f * s->vpp * (float)sin(2.0 * M_PI * f);
        break;
    case SIG_SQUARE:
        v = s->offset + (f < 0.5 ? 0.5f : -0.5f) * s->vpp;
        break;
    case SIG_TRIANGLE:
        v = s->offset + s->vpp * (float)(f < 0.5 ? 2.0 * f - 0.5 : 1.5 - 2.0 * f);
        break;
    case SIG_SAW:
        v = s->offset + s->vpp * (float)(f - 0.5);
        break;
    case SIG_PULSE:
        v = s->offset + (f < (double)s->duty ? s->vpp : 0.0f);
        break;
    case SIG_UART:
        v = s->offset + (uart_bit(ph) ? s->vpp : 0.0f);
        break;
    case SIG_AM: {   /* carrier at freq, 10 % depth envelope at freq / 50 */
        double env = 0.75 + 0.25 * sin(2.0 * M_PI * ph / 50.0);
        v = s->offset + 0.5f * s->vpp * (float)(env * sin(2.0 * M_PI * f));
        break;
    }
    case SIG_GND:
    default:
        return 0.0f;   /* a grounded (or floating) tip reads 0 V */
    }
    return v + s->noise * gauss();
}

/* ---- front end and ADC ------------------------------------------------------------------------- */

/* Component tolerances of this simulated unit: a zero offset at the ADC (after the range switch, as the
 * firmware's CAL assumes), in codes: +1.2 on CH1 and -0.8 on CH2, so +47 / -31 mV on the +-5 V range. */
static const float FE_OFFSET_CODES[2] = {1.2f, -0.8f};
#define FE_OFFSET_V(ch, r) (FE_OFFSET_CODES[ch] * (((r) == AFE_RANGE_10X) ? 53.56f : 9.803f) * 1.024f / 256.0f)
#define FE_BUFFER_LIMIT_V 26.0f   /* the input buffer's common-mode range, referred to the tip */

/* tip volts -> raw AD9288 code as the PIO reads it (before the firmware flips it: the gain stage
 * inverts). y_state is the 31 MHz anti-alias pole, stepped at the ADC rate. */
static uint8_t adc_code(int ch, float v, float *y_state, float alpha) {
    afe_range_t r = g_app.range[ch];
    if (v > FE_BUFFER_LIMIT_V) {
        v = FE_BUFFER_LIMIT_V;
    } else if (v < -FE_BUFFER_LIMIT_V) {
        v = -FE_BUFFER_LIMIT_V;
    }
    v += FE_OFFSET_V(ch, r);
    float gain = (r == AFE_RANGE_10X) ? 53.56f : 9.803f;
    *y_state += alpha * (v - *y_state);
    float code = 128.0f + *y_state / (gain * 1.024f / 256.0f) + 0.35f * gauss();
    long c = lroundf(code);
    if (c < 0) {
        c = 0;
    } else if (c > 255) {
        c = 255;
    }
    return (uint8_t)(255 - c);   /* inverting gain stage */
}

static uint8_t rbit8(uint8_t v) {
    uint8_t r = 0u;
    for (int k = 0; k < 8; ++k) {
        r = (uint8_t)(r | (((v >> k) & 1u) << (7 - k)));
    }
    return r;
}

static uint32_t pio_word(uint8_t raw_a, uint8_t raw_b) {
    /* ADCA_D7..D0 on GPIO16..23 (bit-reversed), ADCB_D0..D7 on GPIO32..39; bits 8..15 and 24..31 are
     * other pins (ADC_CLK, LCD, gain switches): set some, as the firmware must mask them off */
    return (uint32_t)rbit8(raw_a) | ((uint32_t)raw_b << 16) | 0x5A00A500u;
}

/* ---- logic pod --------------------------------------------------------------------------------- */

static uint8_t pod_nibble(double t) {
    double ph = t * (double)g_pod_freq;
    switch (g_pod_kind) {
    case POD_SPI: {   /* CS low for 16 clocks of every 24; SCK = freq; MOSI 0xA5 0x3C; MISO counts */
        double cyc = ph / 24.0;
        long frame = (long)floor(cyc);
        double in = (cyc - floor(cyc)) * 24.0;   /* clock periods into the frame */
        int cs = in >= 16.0 ? 1 : 0;
        int bit = (int)floor(in);
        int sck = cs ? 0 : ((in - floor(in)) >= 0.5 ? 1 : 0);
        uint16_t mosi_word = 0xA53Cu;
        uint16_t miso_word = (uint16_t)(frame * 37u);
        int mosi = cs ? 0 : (mosi_word >> (15 - bit)) & 1;
        int miso = cs ? 0 : (miso_word >> (15 - bit)) & 1;
        return (uint8_t)(cs | (sck << 1) | (mosi << 2) | (miso << 3));
    }
    case POD_UART_PWM: {   /* CH0 UART at freq baud, CH1 PWM at freq / 40 with a slowly moving duty */
        int tx = uart_bit(ph);
        double pw = ph / 40.0;
        double duty = 0.5 + 0.4 * sin(2.0 * M_PI * t * 50.0);
        int pwm = (pw - floor(pw)) < duty ? 1 : 0;
        int ch2 = (long)floor(ph / 400.0) & 1;
        return (uint8_t)(tx | (pwm << 1) | (ch2 << 2));
    }
    case POD_COUNTER:
    default: {   /* a 4-bit binary counter clocked at freq: CH0 = freq / 2, CH1 = freq / 4 ... */
        long c = (long)floor(ph);
        return (uint8_t)(c & 0x0F);
    }
    }
}

/* ---- capture (follows main.c capture_start / capture_poll / capture_finish) --------------------- */

static double g_t0;          /* signal time of the capture's first sample, s */
static double g_cap_end_ms;  /* when the capture's DMA would finish */
static bool g_capturing;
static bool g_cap_logic;
static uint32_t g_cap_ms;    /* capture length, for the info panel */

static void capture_start(double now_ms) {
    g_cap_logic = (g_app.mode == APP_MODE_LOGIC);
    size_t n = app_capture_samples(&g_app, CAPTURE_MAX);
    double fs = (double)app_sample_rate_hz(&g_app);
    uint32_t m = g_cap_logic ? 0u : app_peak_factor(&g_app);
    double dur_ms = (m ? (double)(n / 2u) * m / (APP_ADC_MAX_HZ / APP_PEAK_CLKDIV) : (double)n / fs) * 1e3;
    g_cap_ms = (uint32_t)(dur_ms + 0.5);
    /* free-running captures start at an arbitrary point of the signal */
    g_t0 = now_ms * 1e-3 + (double)rnd() * 1e-3;
    g_cap_end_ms = now_ms + dur_ms;
    g_capturing = true;
}

static void capture_finish(void) {
    size_t n = app_capture_samples(&g_app, CAPTURE_MAX);
    g_app.overrun = false;
    if (g_cap_logic) {
        double fs = (double)app_sample_rate_hz(&g_app);
        size_t words = (n + 7u) / 8u;
        for (size_t w = 0u; w < words; ++w) {
            uint32_t word = 0u;
            for (unsigned k = 0u; k < 8u; ++k) {
                word |= (uint32_t)pod_nibble(g_t0 + (double)(w * 8u + k) / fs) << (4u * k);
            }
            g_words[w] = word;
        }
        g_n_nib = logic_unpack_pod_nibbles(g_words, words, g_nib, CAPTURE_MAX);
        app_process_logic(&g_app, g_nib, g_n_nib);
        return;
    }
    uint32_t m = app_peak_factor(&g_app);
    float ya = 0.0f, yb = 0.0f;
    if (m) {
        /* peak detect: raw words at 2.5 MS/s, folded m at a time by the firmware's own scope_peak_group() */
        double fs_raw = APP_ADC_MAX_HZ / APP_PEAK_CLKDIV;
        float alpha = 1.0f - expf(-2.0f * (float)M_PI * 31e6f / (float)fs_raw);
        size_t groups = n / 2u;
        double t = g_t0;
        for (size_t g = 0u; g < groups; ++g) {
            for (uint32_t i = 0u; i < m; ++i) {
                uint8_t a = adc_code(0, sig_value(&g_sig[0], t), &ya, alpha);
                uint8_t b = adc_code(1, sig_value(&g_sig[1], t), &yb, alpha);
                g_words[i] = pio_word(a, b);
                t += 1.0 / fs_raw;
            }
            uint16_t pa[2], pb[2];
            scope_peak_group(g_words, CAPTURE_MAX, 0u, m, pa, pb);
            g_ch_a[2u * g] = pa[0];
            g_ch_a[2u * g + 1u] = pa[1];
            g_ch_b[2u * g] = pb[0];
            g_ch_b[2u * g + 1u] = pb[1];
        }
        g_n = 2u * groups;
    } else {
        double fs = (double)app_sample_rate_hz(&g_app);
        float alpha = 1.0f - expf(-2.0f * (float)M_PI * 31e6f / (float)fs);
        /* settle the filter on the sample before the capture */
        ya = sig_value(&g_sig[0], g_t0) + FE_OFFSET_V(0, g_app.range[0]);
        yb = sig_value(&g_sig[1], g_t0) + FE_OFFSET_V(1, g_app.range[1]);
        for (size_t i = 0u; i < n; ++i) {
            double t = g_t0 + (double)i / fs;
            g_words[i] = pio_word(adc_code(0, sig_value(&g_sig[0], t), &ya, alpha),
                                  adc_code(1, sig_value(&g_sig[1], t), &yb, alpha));
        }
        g_n = scope_unpack_ad9288(g_words, n, g_ch_a, g_ch_b, CAPTURE_MAX);
        scope_invert_codes(g_ch_a, g_n);
        scope_invert_codes(g_ch_b, g_n);
    }
    app_process_scope(&g_app, g_ch_a, g_ch_b, g_n);
}

/* ---- DC sense: CHx_DC_SENSE through 10k / 10nF (100 us), 16 readings averaged ------------------ */

static uint16_t dc_raw(int ch, double t) {
    float sum = 0.0f;
    for (int i = 0; i < 64; ++i) {
        sum += sig_value(&g_sig[ch], t - (double)i * 4e-6 - (double)rnd() * 4e-6);
    }
    float v = sum / 64.0f;
    if (v > FE_BUFFER_LIMIT_V) {
        v = FE_BUFFER_LIMIT_V;
    } else if (v < -FE_BUFFER_LIMIT_V) {
        v = -FE_BUFFER_LIMIT_V;
    }
    afe_range_t r = g_app.range[ch];
    float gain = (r == AFE_RANGE_10X) ? 53.56f : 9.803f;
    float v_pin = 0.978f - (v + FE_OFFSET_V(ch, r)) / gain;   /* the gain stage inverts */
    if (v_pin < 0.0f) {
        v_pin = 0.0f;
    } else if (v_pin > 3.0f) {
        v_pin = 3.0f;
    }
    float raw = v_pin / 3.000f * 4096.0f + 1.5f * gauss();
    return (uint16_t)(raw < 0.0f ? 0.0f : (raw > 4095.0f ? 4095.0f : raw));
}

/* ---- USB: bytes the device sends, as main.c writes them to stdout ------------------------------ */

#define USB_OUT_MAX (4u * 1024u * 1024u)   /* room for 2 s of streaming between page frames */
static uint8_t g_usb_out[USB_OUT_MAX];
static size_t g_usb_len;
static char g_usb_in[128];

static void usb_put(const void *data, size_t len) {
    if (g_usb_len + len > USB_OUT_MAX) {
        return;   /* host not reading: drop, as a full CDC buffer would */
    }
    memcpy(g_usb_out + g_usb_len, data, len);
    g_usb_len += len;
}

static void usb_puts_line(const char *s) {
    usb_put(s, strlen(s));
    usb_put("\n", 1u);
}

static void send_frames(size_t start, size_t count) {
    if (start >= g_n) {
        return;
    }
    if (start + count > g_n) {
        count = g_n - start;
    }
    for (uint8_t ch = 0u; ch < 2u; ++ch) {
        size_t len = usb_encode_wave_frame(ch, (ch ? g_ch_b : g_ch_a) + start, count, g_frame, sizeof g_frame);
        usb_put(g_frame, len);
    }
}

/* ---- USB streaming (follows main.c stream_start / stream_poll / stream_stop) -------------------- */

#define SIM_RING_WORDS 16384u          /* main.c's 64 KB DMA ring */
static stream_t g_stream;
static double g_stream_t0_ms;          /* when the stream's DMA started */
static uint64_t g_raw_consumed;        /* raw words taken from the ring */
static double g_usb_budget = 1.0e6;    /* bytes per second the simulated USB host accepts */
static double g_usb_allow;             /* bytes it would accept right now */
static double g_stream_last_ms;
static uint64_t g_stream_bytes_last;
static double g_stream_stat_ms;
static float g_sya, g_syb;             /* anti-alias filter state at the raw rate */

static void usb_put(const void *data, size_t len);

static void sim_stream_emit(const uint8_t *block, size_t len, void *ctx) {
    (void)ctx;
    usb_put(block, len);
    g_usb_allow -= (double)len;
}

static void sim_stream_start(double now_ms) {
    g_capturing = false;
    g_stream = g_app.stream_plan;
    g_stream_t0_ms = now_ms;
    g_raw_consumed = 0u;
    g_usb_allow = 0.0;
    g_stream_last_ms = g_stream_stat_ms = now_ms;
    g_stream_bytes_last = 0u;
    g_sya = g_syb = 0.0f;
    g_app.streaming = true;
    g_app.stream_lost = 0u;
    g_app.stream_bps = 0.0f;
}

static void sim_stream_stop(void) {
    char line[160];
    stream_flush(&g_stream, sim_stream_emit, NULL);
    snprintf(line, sizeof line, "STREAM END SAMPLES=%lu BLOCKS=%lu BYTES=%llu LOST=%lu LOSTSAMPLES=%lu",
             (unsigned long)g_stream.out_index, (unsigned long)g_stream.blocks, (unsigned long long)g_stream.bytes,
             (unsigned long)g_stream.lost_events, (unsigned long)g_stream.lost_samples);
    usb_put(line, strlen(line));
    usb_put("\n", 1u);
    g_app.streaming = false;
}

static void sim_stream_poll(double now_ms) {
    double dt = now_ms - g_stream_last_ms;
    g_stream_last_ms = now_ms;
    g_usb_allow += g_usb_budget * dt * 1e-3;
    double cap = g_usb_budget * dt * 1e-3 + 65536.0;   /* no credit saved up beyond the host's buffers */
    if (g_usb_allow > cap) {
        g_usb_allow = cap;
    }
    double raw_hz = (double)g_stream.raw_hz;
    uint64_t produced = (uint64_t)((now_ms - g_stream_t0_ms) * 1e-3 * raw_hz);
    uint64_t avail = produced - g_raw_consumed;
    /* The pen drains its ring continuously; the browser steps this model only every frame (or seldom, in
     * a background tab). So within one step, first send what the USB budget allows, and only then count
     * what no longer fits the ring as lost. A step longer than 2 s is a stalled page: skip it. */
    if (dt > 2000.0) {
        stream_skip_raw(&g_stream, (uint32_t)avail, sim_stream_emit, NULL);
        g_raw_consumed += avail;
        avail = 0u;
    }
    float alpha = 1.0f - expf(-2.0f * (float)M_PI * 31e6f / (float)raw_hz);
    double per_word = (double)((g_stream.chmask == 3u) ? 2u : 1u) / (double)g_stream.avg;
    while (avail > 0u && g_usb_allow > 0.0) {
        uint32_t n = avail > 1024u ? 1024u : (uint32_t)avail;
        uint32_t fit = (uint32_t)(g_usb_allow / per_word) + 1u;
        if (n > fit) {
            n = fit;
        }
        for (uint32_t i = 0u; i < n; ++i) {
            double t = g_stream_t0_ms * 1e-3 + (double)(g_raw_consumed + i) / raw_hz;
            g_words[i] = pio_word(adc_code(0, sig_value(&g_sig[0], t), &g_sya, alpha),
                                  adc_code(1, sig_value(&g_sig[1], t), &g_syb, alpha));
        }
        stream_push_words(&g_stream, g_words, n, sim_stream_emit, NULL);
        g_raw_consumed += n;
        avail -= n;
    }
    if (avail > SIM_RING_WORDS - 512u) {   /* the USB host fell behind: the ring lapped */
        uint64_t drop = avail - SIM_RING_WORDS / 2u;
        stream_skip_raw(&g_stream, (uint32_t)drop, sim_stream_emit, NULL);
        g_raw_consumed += drop;
    }
    if (now_ms - g_stream_stat_ms >= 500.0) {
        g_app.stream_bps = (float)((double)(g_stream.bytes - g_stream_bytes_last) * 1000.0 / (now_ms - g_stream_stat_ms));
        g_app.stream_lost = g_stream.lost_events;
        g_stream_bytes_last = g_stream.bytes;
        g_stream_stat_ms = now_ms;
    }
}

/* ---- exported API ------------------------------------------------------------------------------ */

static uint8_t g_buttons;
static double g_last_dc = -1e9, g_last_draw = -1e9;
static bool g_fresh;
static uint32_t g_flash_writes;
static char g_info[768];

EXPORT void sim_init(void) {
    app_init(&g_app);
    buttons_init(&g_btn);
    g_n = g_n_nib = 0u;
    g_usb_len = 0u;
    g_capturing = false;
    g_fresh = false;
    g_last_dc = g_last_draw = -1e9;
    capture_start(0.0);
}

EXPORT void sim_set_buttons(uint32_t mask) {
    g_buttons = (uint8_t)(mask & 0x0Fu);
}

EXPORT void sim_set_signal(int ch, int kind, float vpp, float offset, float freq, float duty, float noise) {
    if (ch < 0 || ch > 1) {
        return;
    }
    g_sig[ch].kind = (kind >= 0 && kind < SIG_COUNT) ? kind : SIG_GND;
    g_sig[ch].vpp = vpp;
    g_sig[ch].offset = offset;
    g_sig[ch].freq = freq > 0.0f ? freq : 1.0f;
    g_sig[ch].duty = duty < 0.0f ? 0.0f : (duty > 1.0f ? 1.0f : duty);
    g_sig[ch].noise = noise < 0.0f ? 0.0f : noise;
}

/* Bytes per second the simulated USB host takes from a stream (full-speed CDC: about 1 MB/s, to be
 * measured on a board). */
EXPORT void sim_set_usb_budget(double bytes_per_s) {
    g_usb_budget = bytes_per_s > 1000.0 ? bytes_per_s : 1000.0;
}

EXPORT void sim_set_pod(int kind, float freq) {
    g_pod_kind = kind;
    g_pod_freq = freq > 0.0f ? freq : 1.0f;
}

/* One pass of main.c's loop at time now_ms. Returns 1 when the framebuffer was redrawn. */
EXPORT int sim_step(double now_ms) {
    uint8_t ev[BTN_COUNT];
    size_t nev = buttons_update(&g_btn, g_buttons, (uint32_t)now_ms, ev, BTN_COUNT);
    for (size_t i = 0u; i < nev; ++i) {
        app_button_event(&g_app, (uint8_t)(ev[i] >> 4), (btn_event_t)(ev[i] & 0x0Fu));
    }
    if (now_ms - g_last_dc >= 100.0) {
        double t = now_ms * 1e-3;
        app_set_dc(&g_app, dc_raw(0, t), dc_raw(1, t), (uint16_t)(5.02f / 2.0f / 3.0f * 4096.0f));
        g_last_dc = now_ms;
    }
    if (g_app.stream_req) {
        g_app.stream_req = false;
        if (g_app.stream_want_hz > 0.0f) {
            if (g_app.streaming) {
                sim_stream_stop();
            }
            sim_stream_start(now_ms);
        } else if (g_app.streaming) {
            sim_stream_stop();
            capture_start(now_ms);
        }
    }
    if (g_app.streaming) {
        sim_stream_poll(now_ms);
        if (now_ms - g_last_draw >= 250.0) {
            app_render(&g_app, g_fb, g_ch_a, g_ch_b, g_n, g_nib, g_n_nib);
            g_last_draw = now_ms;
            return 1;
        }
        return 0;
    }
    if (g_capturing) {
        if (now_ms < g_cap_end_ms) {
            /* the real loop keeps reading buttons and USB while the DMA runs; redraw in HOLD-style 20 Hz */
            if (now_ms - g_last_draw >= 50.0) {
                app_render(&g_app, g_fb, g_ch_a, g_ch_b, g_n, g_nib, g_n_nib);
                g_last_draw = now_ms;
                return 1;
            }
            return 0;
        }
        capture_finish();
        g_capturing = false;
        g_fresh = true;
    }
    if (g_app.cal_changed) {
        ++g_flash_writes;   /* main.c writes the calibration sector here */
        g_app.cal_changed = false;
    }
    if (g_app.ask_pending) {
        char meas[320];
        char line[340];
        app_format_meas(&g_app, meas, sizeof meas);
        snprintf(line, sizeof line, "ASK %s", meas);
        usb_puts_line(line);
        send_frames(g_app.win_start, g_app.win_len);
        g_app.ask_pending = false;
    }
    if (g_app.capture_req) {
        send_frames(g_app.win_start, g_app.capture_req);
        g_app.capture_req = 0u;
    }
    int drawn = 0;
    if (g_fresh || now_ms - g_last_draw >= 50.0) {
        app_render(&g_app, g_fb, g_ch_a, g_ch_b, g_n, g_nib, g_n_nib);
        g_last_draw = now_ms;
        g_fresh = false;
        drawn = 1;
    }
    if (g_app.mode == APP_MODE_LOGIC) {
        g_app.cal_pending = false;
    }
    if (g_app.run || g_app.cal_pending) {
        capture_start(now_ms);
    }
    return drawn;
}

EXPORT uint16_t *sim_fb(void) {
    return g_fb;
}
EXPORT uint32_t sim_lcd_width(void) {
    return LCD_WIDTH;
}
EXPORT uint32_t sim_lcd_height(void) {
    return LCD_HEIGHT;
}

/* USB in: the host writes a command line into sim_usb_in_buf() and calls sim_usb_in_commit(). */
EXPORT char *sim_usb_in_buf(void) {
    return g_usb_in;
}
EXPORT void sim_usb_in_commit(void) {
    char reply[320];
    g_usb_in[sizeof g_usb_in - 1u] = '\0';
    app_usb_command(&g_app, g_usb_in, reply, sizeof reply);
    usb_puts_line(reply);
}
EXPORT uint8_t *sim_usb_out(void) {
    return g_usb_out;
}
EXPORT uint32_t sim_usb_out_len(void) {
    return (uint32_t)g_usb_len;
}
EXPORT void sim_usb_out_clear(void) {
    g_usb_len = 0u;
}

/* Live capture settings for the page's info panel, as JSON. */
EXPORT const char *sim_info(void) {
    static const char *const MODES[APP_MODE_COUNT] = {"CH1+2", "CH1", "CH2", "LOGIC", "METER"};
    size_t n = app_capture_samples(&g_app, CAPTURE_MAX);
    snprintf(g_info, sizeof g_info,
             "{\"mode\":\"%s\",\"tb_ns\":%lu,\"fs\":%.1f,\"clkdiv\":%lu,\"peak_m\":%lu,\"screen\":%lu,"
             "\"capture\":%lu,\"cap_ms\":%lu,\"range\":[%d,%d],\"auto\":%d,\"trig_level\":%u,\"trig_src\":%u,"
             "\"slope\":%d,\"triggered\":%d,\"run\":%d,\"streaming\":%d,\"stream_rate\":%.3f,\"stream_raw\":%.1f,"
             "\"stream_avg\":%lu,\"stream_bps\":%.0f,\"stream_lost\":%lu,\"usb_budget\":%.0f,\"flash_writes\":%lu,\"off1\":[%.4f,%.4f],\"off10\":[%.4f,%.4f]}",
             MODES[g_app.mode], (unsigned long)app_timebase_ns(&g_app), (double)app_sample_rate_hz(&g_app),
             (unsigned long)app_clkdiv(&g_app), (unsigned long)app_peak_factor(&g_app),
             (unsigned long)app_screen_samples(&g_app), (unsigned long)n, (unsigned long)g_cap_ms,
             (int)g_app.range[0], (int)g_app.range[1], (int)g_app.auto_range, (unsigned)g_app.trig_level,
             (unsigned)g_app.trig_src, (int)g_app.trig_slope, (int)g_app.triggered, (int)g_app.run,
             (int)g_app.streaming, (double)g_app.stream_plan.rate_hz, (double)g_app.stream_plan.raw_hz,
             (unsigned long)g_app.stream_plan.avg, (double)g_app.stream_bps, (unsigned long)g_app.stream_lost,
             g_usb_budget, (unsigned long)g_flash_writes, (double)g_app.cal[0].offset_1x_v, (double)g_app.cal[1].offset_1x_v,
             (double)g_app.cal[0].offset_10x_v, (double)g_app.cal[1].offset_10x_v);
    return g_info;
}
