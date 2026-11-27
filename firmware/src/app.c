#include "probepen_app.h"

#include <math.h>
#include <stdio.h>
#include <string.h>

#include "probepen_lcd.h"
#include "probepen_logic.h"
#include "probepen_usb.h"

/* ---- timebase -------------------------------------------------------------------------------- */

static const uint32_t TIMEBASE_NS[] = {
    100u, 200u, 500u, 1000u, 2000u, 5000u, 10000u, 20000u, 50000u, 100000u, 200000u, 500000u,
    1000000u, 2000000u, 5000000u, 10000000u, 20000000u, 50000000u,
};
#define TB_COUNT (sizeof(TIMEBASE_NS) / sizeof(TIMEBASE_NS[0]))

static bool scope_mode(app_mode_t m);
#define TB_DEFAULT 6u            /* 10 us/div */
#define SCREEN_DIVS 10u

size_t app_timebase_count(void) {
    return TB_COUNT;
}

uint32_t app_timebase_ns(const app_t *app) {
    return TIMEBASE_NS[(app && app->tb < TB_COUNT) ? app->tb : TB_DEFAULT];
}

uint32_t app_clkdiv(const app_t *app) {
    /* aim for two samples per screen column, rounded down to an integer divider (no clock jitter) */
    double want_hz = (2.0 * LCD_WIDTH) / (SCREEN_DIVS * (double)app_timebase_ns(app) * 1e-9);
    double k = floor((double)APP_ADC_MAX_HZ / want_hz);
    if (k < 1.0) {
        k = 1.0;
    }
    /* slowest rate: a peak-detect group (2k/30 words) must fit in half the 4096-word DMA ring, and the
     * pod's 2k divider must stay within the PIO's 16-bit integer divider */
    if (k > (double)APP_CLKDIV_MAX) {
        k = (double)APP_CLKDIV_MAX;
    }
    uint32_t ki = (uint32_t)k;
    if (ki >= 2u * APP_PEAK_CLKDIV) {
        ki = (ki / APP_PEAK_CLKDIV) * APP_PEAK_CLKDIV;   /* a whole number of peak-detect groups */
    }
    return ki;
}

uint32_t app_peak_factor(const app_t *app) {
    uint32_t k = app_clkdiv(app);
    if (!app || !scope_mode(app->mode) || k < 2u * APP_PEAK_CLKDIV) {
        return 0u;
    }
    return 2u * k / APP_PEAK_CLKDIV;
}

float app_sample_rate_hz(const app_t *app) {
    return APP_ADC_MAX_HZ / (float)app_clkdiv(app);
}

size_t app_screen_samples(const app_t *app) {
    double s = (double)app_sample_rate_hz(app) * SCREEN_DIVS * (double)app_timebase_ns(app) * 1e-9;
    size_t n = (size_t)(s + 0.5);
    return n < 2u ? 2u : n;
}

size_t app_capture_samples(const app_t *app, size_t max_samples) {
    size_t n = 4u * app_screen_samples(app);
    if (n < 512u) {
        n = 512u;
    }
    return n > max_samples ? max_samples : n;
}

/* ---- buttons --------------------------------------------------------------------------------- */

void buttons_init(buttons_t *b) {
    if (b) {
        memset(b, 0, sizeof *b);
    }
}

size_t buttons_update(buttons_t *b, uint8_t pressed_mask, uint32_t now_ms, uint8_t *events, size_t max_events) {
    size_t n = 0u;
    if (!b) {
        return 0u;
    }
    for (uint8_t i = 0u; i < BTN_COUNT; ++i) {
        uint8_t bit = (uint8_t)(1u << i);
        if ((pressed_mask & bit) != (b->raw & bit)) {
            b->raw = (uint8_t)((b->raw & ~bit) | (pressed_mask & bit));
            b->t_change[i] = now_ms;
        }
        btn_event_t ev = BTN_EV_NONE;
        if ((b->raw & bit) != (b->stable & bit) && now_ms - b->t_change[i] >= BTN_DEBOUNCE_MS) {
            b->stable = (uint8_t)((b->stable & ~bit) | (b->raw & bit));
            if (b->stable & bit) {
                b->t_press[i] = now_ms;
                b->long_fired[i] = false;
            } else if (!b->long_fired[i]) {
                ev = BTN_EV_SHORT;
            }
        } else if (b->stable & bit) {
            if (!b->long_fired[i] && now_ms - b->t_press[i] >= BTN_LONG_MS) {
                b->long_fired[i] = true;
                b->t_repeat[i] = now_ms;
                ev = BTN_EV_LONG;
            } else if (b->long_fired[i] && now_ms - b->t_repeat[i] >= BTN_REPEAT_MS) {
                b->t_repeat[i] = now_ms;
                ev = BTN_EV_REPEAT;
            }
        }
        if (ev != BTN_EV_NONE && events && n < max_events) {
            events[n++] = (uint8_t)((i << 4) | (uint8_t)ev);
        }
    }
    return n;
}

/* ---- state ----------------------------------------------------------------------------------- */

void app_init(app_t *app) {
    if (!app) {
        return;
    }
    memset(app, 0, sizeof *app);
    app->mode = APP_MODE_DUAL;
    app->tb = TB_DEFAULT;
    app->run = true;
    app->auto_range = true;
    app->range[0] = AFE_RANGE_10X;   /* start on the +-25 V range until the first capture */
    app->range[1] = AFE_RANGE_10X;
    app->trig_level = 128u;
    app->trig_src = 0u;
    app->trig_slope = TRIG_RISING;
    app->logic_thr_mv = logic_family_default_mv(LOGIC_FAMILY_CMOS33);
    afe_cal_init_default(&app->cal[0]);
    afe_cal_init_default(&app->cal[1]);
    app->sample_rate_hz = app_sample_rate_hz(app);
    app->win_len = app_screen_samples(app);
}

static bool scope_mode(app_mode_t m) {
    return m == APP_MODE_DUAL || m == APP_MODE_CH1 || m == APP_MODE_CH2;
}

void app_button_event(app_t *app, uint8_t button, btn_event_t ev) {
    if (!app || ev == BTN_EV_NONE) {
        return;
    }
    switch (button) {
    case BTN_MODE:
        if (ev == BTN_EV_SHORT) {
            app->mode = (app_mode_t)((app->mode + 1) % APP_MODE_COUNT);
            if (app->mode == APP_MODE_CH2) {
                app->trig_src = 1u;
            } else if (app->mode == APP_MODE_CH1) {
                app->trig_src = 0u;
            }
        } else if (ev == BTN_EV_LONG && scope_mode(app->mode)) {
            /* cycle CH1 rising -> CH1 falling -> CH2 rising -> CH2 falling */
            if (app->trig_slope == TRIG_RISING) {
                app->trig_slope = TRIG_FALLING;
            } else {
                app->trig_slope = TRIG_RISING;
                app->trig_src = (uint8_t)(app->trig_src ^ 1u);
            }
        }
        break;
    case BTN_UP:
    case BTN_DOWN: {
        bool up = (button == BTN_UP);
        if (ev == BTN_EV_SHORT) {
            if (up && app->tb + 1u < TB_COUNT) {
                ++app->tb;
            } else if (!up && app->tb > 0u) {
                --app->tb;
            }
        } else if (scope_mode(app->mode)) {   /* held: move the trigger level */
            int lvl = (int)app->trig_level + (up ? 4 : -4);
            app->trig_level = (uint16_t)(lvl < 0 ? 0 : (lvl > (int)SCOPE_CODE_MAX ? (int)SCOPE_CODE_MAX : lvl));
        }
        break;
    }
    case BTN_ASK:
        if (ev == BTN_EV_SHORT) {
            app->run = !app->run;
        } else if (ev == BTN_EV_LONG && scope_mode(app->mode)) {   /* the snapshot carries scope samples */
            app->ask_pending = true;
        }
        break;
    default:
        break;
    }
}

/* ---- processing ------------------------------------------------------------------------------ */

static void range_scale(const app_t *app, int ch, afe_range_t r, float *code_scale, float *zero_code) {
    const afe_cal_t *cal = &app->cal[ch & 1];
    float gain = (r == AFE_RANGE_10X) ? cal->gain_10x : cal->gain_1x;
    float offs = (r == AFE_RANGE_10X) ? cal->offset_10x_v : cal->offset_1x_v;
    *code_scale = gain * 1.024f / 256.0f;        /* 1.024 V ADC span over 256 codes */
    *zero_code = 128.0f - offs / *code_scale;
}

void app_process_scope(app_t *app, const uint16_t *ch_a, const uint16_t *ch_b, size_t n) {
    if (!app) {
        return;
    }
    const uint16_t *ch[2] = {ch_a, ch_b};
    app->sample_rate_hz = app_sample_rate_hz(app);
    for (int c = 0; c < 2; ++c) {
        float scale, zero;
        range_scale(app, c, app->range[c], &scale, &zero);
        scope_measure_buffer(ch[c], ch[c] ? n : 0u, app->sample_rate_hz, scale, zero, app->trig_slope, &app->meas[c]);
    }
    if (app->cal_pending && ch_a && ch_b && n > 0u) {
        /* Inputs shorted: what each channel still reads is its own offset. It is converted to ADC codes (it
         * comes from the gain stage and the ADC, after the range switch) and applied to both of that
         * channel's ranges. */
        for (int c = 0; c < 2; ++c) {
            float scale, zero, s1, s10, z;
            range_scale(app, c, app->range[c], &scale, &zero);
            float d_code = app->meas[c].vavg / scale;
            range_scale(app, c, AFE_RANGE_1X, &s1, &z);
            range_scale(app, c, AFE_RANGE_10X, &s10, &z);
            app->cal[c].offset_1x_v -= d_code * s1;
            app->cal[c].offset_10x_v -= d_code * s10;
        }
        app->cal_pending = false;
        app->cal_changed = true;
        for (int c = 0; c < 2; ++c) {
            float scale, zero;
            range_scale(app, c, app->range[c], &scale, &zero);
            scope_measure_buffer(ch[c], n, app->sample_rate_hz, scale, zero, app->trig_slope, &app->meas[c]);
        }
    }
    if (app_peak_factor(app) > 0u) {
        /* Peak detect: when most min/max pairs span the full swing, the signal is faster than a slot and
         * the zero crossings of the envelope say nothing about its frequency. */
        for (int c = 0; c < 2; ++c) {
            if (!ch[c] || n < 4u) {
                continue;
            }
            uint16_t lo = SCOPE_CODE_MAX, hi = 0u;
            for (size_t i = 0u; i < n; ++i) {
                lo = ch[c][i] < lo ? ch[c][i] : lo;
                hi = ch[c][i] > hi ? ch[c][i] : hi;
            }
            size_t full = 0u;
            for (size_t i = 0u; i + 1u < n; i += 2u) {
                int d = (int)ch[c][i + 1u] - (int)ch[c][i];
                if ((d < 0 ? -d : d) * 2 > (int)(hi - lo)) {
                    ++full;
                }
            }
            if (full * 4u > n / 2u) {
                app->meas[c].freq_hz = 0.0f;
            }
        }
    }
    if (app->auto_range) {
        for (int c = 0; c < 2; ++c) {
            bool otr = false;
            for (size_t i = 0u; ch[c] && i < n; ++i) {
                if (ch[c][i] == 0u || ch[c][i] >= SCOPE_CODE_MAX) {
                    otr = true;
                    break;
                }
            }
            float peak = fmaxf(fabsf(app->meas[c].vmin), fabsf(app->meas[c].vmax));
            app->range[c] = afe_autorange_select(app->range[c], peak, otr);
        }
    }

    size_t span = app_screen_samples(app);
    size_t pre = span / 4u;
    const uint16_t *src = ch[app->trig_src & 1u];
    app->triggered = false;
    app->win_start = 0u;
    if (src && n > span) {
        bool found = false;
        size_t idx = scope_find_trigger(src, n - (span - pre), app->trig_level, 3u, app->trig_slope, pre, &found);
        if (found) {
            app->triggered = true;
            app->win_start = idx - pre;
        }
    }
    app->win_len = span < n ? span : n;
}

void app_process_logic(app_t *app, const uint8_t *nibbles, size_t n) {
    if (!app) {
        return;
    }
    app->sample_rate_hz = app_sample_rate_hz(app);
    for (uint8_t c = 0u; c < 4u; ++c) {
        app->pod_edges[c] = logic_count_pod_edges(nibbles, n, c);
        app->pod_level[c] = (nibbles && n > 0u) ? (uint8_t)((nibbles[n - 1u] >> c) & 1u) : 0u;
        app->pod_freq_hz[c] = 0.0f;
        /* frequency from the rising edges: (count - 1) periods between the first and the last one */
        size_t first = 0u, last = 0u, rises = 0u;
        for (size_t i = 1u; nibbles && i < n; ++i) {
            if (((nibbles[i] >> c) & 1u) && !((nibbles[i - 1u] >> c) & 1u)) {
                if (rises++ == 0u) {
                    first = i;
                }
                last = i;
            }
        }
        if (rises >= 2u && last > first) {
            app->pod_freq_hz[c] = (float)(rises - 1u) * app->sample_rate_hz / (float)(last - first);
        }
    }
    size_t span = app_screen_samples(app);
    app->win_start = 0u;
    app->win_len = span < n ? span : n;
    app->triggered = false;
}

void app_set_dc(app_t *app, uint16_t raw_ch1, uint16_t raw_ch2, uint16_t raw_vbus) {
    if (!app) {
        return;
    }
    app->dc_v[0] = afe_dc_sense_to_volts(&app->cal[0], app->range[0], raw_ch1);
    app->dc_v[1] = afe_dc_sense_to_volts(&app->cal[1], app->range[1], raw_ch2);
    app->vbus_v = (float)raw_vbus / 4096.0f * app->cal[0].vref_v * 2.0f;   /* R17/R18 100k:100k */
}

/* ---- text ------------------------------------------------------------------------------------ */

size_t app_format_eng(float value, const char *unit, char *out, size_t max_len) {
    static const struct { float lim; float mul; const char *pre; } P[] = {
        {1e-6f, 1e9f, "n"}, {1e-3f, 1e6f, "\x7f"}, {1.0f, 1e3f, "m"}, {1e3f, 1.0f, ""}, {1e6f, 1e-3f, "k"},
        {3.4e38f, 1e-6f, "M"},
    };
    if (!out || max_len == 0u) {
        return 0u;
    }
    if (!unit) {
        unit = "";
    }
    float a = fabsf(value);
    if (a < 1e-9f || !isfinite(value)) {
        return (size_t)snprintf(out, max_len, "0%s", unit);
    }
    size_t k = 0u;
    while (k + 1u < sizeof P / sizeof P[0] && a >= P[k].lim) {
        ++k;
    }
    float x = value * P[k].mul;
    float ax = fabsf(x);
    const char *fmt = (ax < 9.995f) ? "%.2f%s%s" : (ax < 99.95f) ? "%.1f%s%s" : "%.0f%s%s";
    return (size_t)snprintf(out, max_len, fmt, (double)x, P[k].pre, unit);
}

size_t app_format_meas(const app_t *app, char *out, size_t max_len) {
    if (!app || !out || max_len == 0u) {
        return 0u;
    }
    size_t len = 0u;
    out[0] = '\0';
    for (int c = 0; c < 2 && len < max_len; ++c) {
        const scope_meas_t *m = &app->meas[c];
        int w = snprintf(out + len, max_len - len, "%sCH%d VPP=%.3f VMIN=%.3f VMAX=%.3f VAVG=%.3f F=%.1f RANGE=%s DC=%.3f",
                         c ? ";" : "", c + 1, (double)m->vpp, (double)m->vmin, (double)m->vmax, (double)m->vavg,
                         (double)m->freq_hz, app->range[c] == AFE_RANGE_10X ? "10X" : "1X", (double)app->dc_v[c]);
        if (w < 0) {
            break;
        }
        len += (size_t)w;
    }
    if (len < max_len) {
        int w = snprintf(out + len, max_len - len, ";FS=%.0f;TB=%lu;TRIG=%s;OVR=%s", (double)app->sample_rate_hz,
                         (unsigned long)app_timebase_ns(app), app->triggered ? "YES" : "NO",
                         app->overrun ? "YES" : "NO");
        if (w > 0) {
            len += (size_t)w;
        }
    }
    return len < max_len ? len : max_len - 1u;
}

size_t app_usb_command(app_t *app, const char *line, char *reply, size_t max_len) {
    if (!app || !reply || max_len == 0u) {
        return 0u;
    }
    usb_cmd_t cmd = usb_parse_command(line);
    int w = -1;
    if (!cmd.valid) {
        w = snprintf(reply, max_len, "ERR");
    } else {
        switch (cmd.kind) {
        case USB_CMD_IDN:
            w = snprintf(reply, max_len, "ProbePen-AI,RP2354B+AD9288,%s", APP_FW_VERSION);
            break;
        case USB_CMD_MODE:
            if (cmd.arg_i >= 0 && cmd.arg_i < (int32_t)APP_MODE_COUNT) {
                app->mode = (app_mode_t)cmd.arg_i;
                w = snprintf(reply, max_len, "OK");
            } else {
                w = snprintf(reply, max_len, "ERR");
            }
            break;
        case USB_CMD_GAIN:
            if (cmd.arg_i == 0) {
                app->auto_range = true;
            } else {
                app->auto_range = false;
                app->range[0] = app->range[1] = (cmd.arg_i == 10) ? AFE_RANGE_10X : AFE_RANGE_1X;
            }
            w = snprintf(reply, max_len, "OK");
            break;
        case USB_CMD_THR:
            app->logic_thr_mv = (uint32_t)cmd.arg_i;
            w = snprintf(reply, max_len, "OK");
            break;
        case USB_CMD_CAL:      /* needs a scope capture: refused in LOGIC and METER */
            app->cal_pending = scope_mode(app->mode);
            w = snprintf(reply, max_len, app->cal_pending ? "OK" : "ERR");
            break;
        case USB_CMD_MEAS:
            return app_format_meas(app, reply, max_len);
        case USB_CMD_TRIG:
            app->trig_level = (uint16_t)cmd.arg_i;
            w = snprintf(reply, max_len, "OK");
            break;
        case USB_CMD_CAPTURE:
            if (scope_mode(app->mode)) {
                app->capture_req = (uint16_t)cmd.arg_i;
                w = snprintf(reply, max_len, "OK %ld", (long)cmd.arg_i);
            } else {
                w = snprintf(reply, max_len, "ERR");
            }
            break;
        case USB_CMD_TB:
            if (cmd.arg_i >= 0 && (size_t)cmd.arg_i < TB_COUNT) {
                app->tb = (uint8_t)cmd.arg_i;
                w = snprintf(reply, max_len, "OK %lu", (unsigned long)app_timebase_ns(app));
            } else {
                w = snprintf(reply, max_len, "ERR");
            }
            break;
        case USB_CMD_RUN:
            app->run = (cmd.arg_i != 0);
            w = snprintf(reply, max_len, "OK");
            break;
        default:
            w = snprintf(reply, max_len, "ERR");
            break;
        }
    }
    if (w < 0) {
        reply[0] = '\0';
        return 0u;
    }
    return (size_t)w < max_len ? (size_t)w : max_len - 1u;
}

/* ---- drawing --------------------------------------------------------------------------------- */

#define HDR_H   (LCD_GLYPH_H + 2)
#define FOOT_H  (2 * LCD_GLYPH_H + 3)
#define PLOT_Y0 HDR_H
#define PLOT_W  ((int)LCD_WIDTH)
#define PLOT_H  ((((int)LCD_HEIGHT - HDR_H - FOOT_H) / 10) * 10)
#define FOOT_Y  (PLOT_Y0 + PLOT_H + 2)

typedef struct {
    uint16_t bg, grid, axis, text, dim, ch[2], trig, hold, pod[4];
} palette_t;

static palette_t palette(void) {
    palette_t p;
    p.bg = lcd_rgb565(6, 8, 12);
    p.grid = lcd_rgb565(38, 44, 54);
    p.axis = lcd_rgb565(78, 88, 102);
    p.text = lcd_rgb565(225, 230, 235);
    p.dim = lcd_rgb565(130, 140, 152);
    p.ch[0] = lcd_rgb565(255, 214, 0);
    p.ch[1] = lcd_rgb565(0, 200, 255);
    p.trig = lcd_rgb565(255, 120, 0);
    p.hold = lcd_rgb565(255, 60, 60);
    p.pod[0] = lcd_rgb565(120, 255, 120);
    p.pod[1] = lcd_rgb565(255, 140, 220);
    p.pod[2] = lcd_rgb565(140, 170, 255);
    p.pod[3] = lcd_rgb565(255, 190, 110);
    return p;
}

static void draw_grid(uint16_t *fb, const palette_t *p) {
    for (int d = 0; d <= 10; ++d) {
        int x = (d * (PLOT_W - 1)) / 10;
        int y = PLOT_Y0 + (d * (PLOT_H - 1)) / 10;
        for (int yy = PLOT_Y0; yy < PLOT_Y0 + PLOT_H; yy += 2) {
            lcd_fb_pixel(fb, x, yy, d == 5 ? p->axis : p->grid);
        }
        for (int xx = 0; xx < PLOT_W; xx += 2) {
            lcd_fb_pixel(fb, xx, y, d == 5 ? p->axis : p->grid);
        }
    }
}

static void draw_trace(uint16_t *fb, const uint16_t *s, size_t n, uint16_t color) {
    uint8_t ymin[LCD_WIDTH];
    uint8_t ymax[LCD_WIDTH];
    if (!s || n == 0u) {
        return;
    }
    scope_decimate_min_max(s, n, ymin, ymax, (size_t)PLOT_W, (uint8_t)PLOT_H);
    for (int x = 0; x < PLOT_W; ++x) {
        int top = ymin[x], bot = ymax[x];
        if (x > 0) {
            if (top > ymax[x - 1]) {
                top = ymax[x - 1];
            }
            if (bot < ymin[x - 1]) {
                bot = ymin[x - 1];
            }
        }
        lcd_fb_vline(fb, x, PLOT_Y0 + top, PLOT_Y0 + bot, color);
    }
}

static int code_to_y(uint16_t code) {
    return PLOT_Y0 + (int)(((uint32_t)(SCOPE_CODE_MAX - (code > SCOPE_CODE_MAX ? SCOPE_CODE_MAX : code)) * (uint32_t)(PLOT_H - 1)) / SCOPE_CODE_MAX);
}

static void fmt_timebase(uint32_t ns, char *out, size_t len) {
    char v[16];
    app_format_eng((float)ns * 1e-9f, "s", v, sizeof v);
    snprintf(out, len, "%s/div", v);
}

static float volts_per_div(const app_t *app, int ch) {
    float gain = (app->range[ch] == AFE_RANGE_10X) ? app->cal[ch & 1].gain_10x : app->cal[ch & 1].gain_1x;
    return gain * 1.024f / 10.0f;   /* 1.024 V ADC span over 10 divisions: 1.0 V/div or 5.5 V/div */
}

static void draw_header(const app_t *app, uint16_t *fb, const palette_t *p) {
    static const char *const NAMES[APP_MODE_COUNT] = {"CH1+2", "CH1", "CH2", "LOGIC", "METER"};
    char buf[48];
    lcd_fb_rect(fb, 0, 0, (int)LCD_WIDTH, HDR_H - 1, lcd_rgb565(22, 26, 34));
    int x = lcd_fb_draw_text_scaled(fb, 2, 1, NAMES[app->mode], 1, p->text) + 6;
    if (app->mode != APP_MODE_METER) {
        fmt_timebase(app_timebase_ns(app), buf, sizeof buf);
        x = lcd_fb_draw_text_scaled(fb, x, 1, buf, 1, p->dim) + 6;
    }
    if (scope_mode(app->mode)) {
        char v[16];
        float scale, zero;
        range_scale(app, app->trig_src & 1u, app->range[app->trig_src & 1u], &scale, &zero);
        app_format_eng(((float)app->trig_level - zero) * scale, "V", v, sizeof v);
        snprintf(buf, sizeof buf, "T%u%c%s", (unsigned)(app->trig_src + 1u), app->trig_slope == TRIG_RISING ? '/' : '\\', v);
        lcd_fb_draw_text_scaled(fb, x, 1, buf, 1, app->triggered ? p->trig : p->dim);
    }
    const char *st = app->overrun ? "OVR" : (app->run ? "RUN" : "HOLD");
    lcd_fb_draw_text_scaled(fb, (int)LCD_WIDTH - lcd_text_width(st, 1) - 1, 1, st, 1,
                            (app->run && !app->overrun) ? p->pod[0] : p->hold);
}

static void draw_channel_line(const app_t *app, uint16_t *fb, const palette_t *p, int ch, int y, bool detail) {
    char vdiv[16], a[16], b[16], buf[64];
    const scope_meas_t *m = &app->meas[ch];
    app_format_eng(volts_per_div(app, ch), "V", vdiv, sizeof vdiv);
    app_format_eng(m->vpp, "V", a, sizeof a);
    if (detail) {
        app_format_eng(m->vavg, "V", b, sizeof b);
        snprintf(buf, sizeof buf, "%d %s/d %s Vpp avg %s", ch + 1, vdiv, a, b);
    } else {
        app_format_eng(m->freq_hz, "Hz", b, sizeof b);
        snprintf(buf, sizeof buf, "%d %s/d %s Vpp %s", ch + 1, vdiv, a, m->freq_hz > 0.0f ? b : "--");
    }
    lcd_fb_draw_text_scaled(fb, 2, y, buf, 1, p->ch[ch]);
}

static void render_scope(const app_t *app, uint16_t *fb, const palette_t *p, const uint16_t *ch_a,
                         const uint16_t *ch_b, size_t n) {
    draw_grid(fb, p);
    size_t start = app->win_start < n ? app->win_start : 0u;
    size_t len = app->win_len;
    if (start + len > n) {
        len = n - start;
    }
    if (app->mode != APP_MODE_CH2 && ch_a) {
        draw_trace(fb, ch_a + start, len, p->ch[0]);
    }
    if (app->mode != APP_MODE_CH1 && ch_b) {
        draw_trace(fb, ch_b + start, len, p->ch[1]);
    }
    /* trigger level tick at the left edge, trigger position tick at the top */
    int ty = code_to_y(app->trig_level);
    lcd_fb_hline(fb, 0, 4, ty, p->trig);
    lcd_fb_vline(fb, PLOT_W / 4, PLOT_Y0, PLOT_Y0 + 3, p->trig);

    if (app->mode == APP_MODE_DUAL) {
        draw_channel_line(app, fb, p, 0, FOOT_Y, false);
        draw_channel_line(app, fb, p, 1, FOOT_Y + LCD_GLYPH_H + 1, false);
    } else {
        int ch = app->mode == APP_MODE_CH2 ? 1 : 0;
        char a[16], b[16], buf[64];
        draw_channel_line(app, fb, p, ch, FOOT_Y, true);
        app_format_eng(app->meas[ch].freq_hz, "Hz", a, sizeof a);
        app_format_eng(app->sample_rate_hz, "S/s", b, sizeof b);
        snprintf(buf, sizeof buf, "f %s  %s", app->meas[ch].freq_hz > 0.0f ? a : "--", b);
        lcd_fb_draw_text_scaled(fb, 2, FOOT_Y + LCD_GLYPH_H + 1, buf, 1, p->dim);
    }
}

static void render_logic(const app_t *app, uint16_t *fb, const palette_t *p, const uint8_t *nib, size_t n) {
    int lane = PLOT_H / 4;
    size_t start = app->win_start < n ? app->win_start : 0u;
    size_t len = app->win_len;
    if (start + len > n) {
        len = n - start;
    }
    for (int c = 0; c < 4; ++c) {
        int y_hi = PLOT_Y0 + c * lane + 2;
        int y_lo = PLOT_Y0 + (c + 1) * lane - 3;
        char lbl[4] = {'P', (char)('0' + c), '\0', '\0'};
        lcd_fb_hline(fb, 0, PLOT_W - 1, PLOT_Y0 + (c + 1) * lane - 1, p->grid);
        if (nib && len > 0u) {
            int prev = -1;
            for (int x = 0; x < PLOT_W; ++x) {
                size_t i0 = start + ((size_t)x * len) / (size_t)PLOT_W;
                size_t i1 = start + ((size_t)(x + 1) * len) / (size_t)PLOT_W;
                if (i1 <= i0) {
                    i1 = i0 + 1u;
                }
                bool hi = false, lo = false;
                for (size_t i = i0; i < i1 && i < start + len; ++i) {
                    if ((nib[i] >> c) & 1u) {
                        hi = true;
                    } else {
                        lo = true;
                    }
                }
                int cur = hi && lo ? 2 : (hi ? 1 : 0);
                if (cur == 2 || (prev >= 0 && prev != 2 && prev != cur)) {
                    lcd_fb_vline(fb, x, y_hi, y_lo, p->pod[c]);
                } else {
                    lcd_fb_pixel(fb, x, cur ? y_hi : y_lo, p->pod[c]);
                }
                prev = cur;
            }
        }
        lcd_fb_rect(fb, 1, y_hi, lcd_text_width(lbl, 1) + 1, LCD_GLYPH_H, p->bg);
        lcd_fb_draw_text_scaled(fb, 2, y_hi + 1, lbl, 1, p->pod[c]);
    }
    for (int c = 0; c < 4; ++c) {
        char f[16], buf[32];
        if (app->pod_freq_hz[c] > 0.0f) {
            app_format_eng(app->pod_freq_hz[c], "Hz", f, sizeof f);
        } else {
            snprintf(f, sizeof f, "%s", app->pod_level[c] ? "HIGH" : "LOW");
        }
        snprintf(buf, sizeof buf, "P%d %s", c, f);
        int col = (int)LCD_WIDTH / 2;
        lcd_fb_draw_text_scaled(fb, 2 + (c % 2) * col, FOOT_Y + (c / 2) * (LCD_GLYPH_H + 1), buf, 1, p->pod[c]);
    }
}

static void render_meter(const app_t *app, uint16_t *fb, const palette_t *p) {
    int scale = LCD_WIDTH >= 200u ? 3 : 2;
    int row = (PLOT_H - 2) / 2;
    for (int c = 0; c < 2; ++c) {
        char v[24], lbl[8];
        int y = PLOT_Y0 + 2 + c * row;
        snprintf(lbl, sizeof lbl, "CH%d", c + 1);
        lcd_fb_draw_text_scaled(fb, 2, y, lbl, 1, p->dim);
        snprintf(v, sizeof v, "%+.3fV", (double)app->dc_v[c]);
        lcd_fb_draw_text_scaled(fb, (int)LCD_WIDTH - lcd_text_width(v, scale) - 2, y + LCD_GLYPH_H, v, scale, p->ch[c]);
    }
    char v[16], buf[48];
    app_format_eng(app->vbus_v, "V", v, sizeof v);
    snprintf(buf, sizeof buf, "VBUS %s  %s", v, app->auto_range ? "AUTO RANGE" : "FIXED RANGE");
    lcd_fb_draw_text_scaled(fb, 2, FOOT_Y + LCD_GLYPH_H + 1, buf, 1, p->dim);
}

void app_render(const app_t *app, uint16_t *fb, const uint16_t *ch_a, const uint16_t *ch_b, size_t n,
                const uint8_t *nibbles, size_t n_nib) {
    if (!app || !fb) {
        return;
    }
    palette_t p = palette();
    lcd_fb_clear(fb, p.bg);
    draw_header(app, fb, &p);
    switch (app->mode) {
    case APP_MODE_LOGIC:
        render_logic(app, fb, &p, nibbles, n_nib);
        break;
    case APP_MODE_METER:
        render_meter(app, fb, &p);
        break;
    default:
        render_scope(app, fb, &p, ch_a, ch_b, n);
        break;
    }
}
