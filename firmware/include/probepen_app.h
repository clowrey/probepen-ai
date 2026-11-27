#ifndef PROBEPEN_APP_H
#define PROBEPEN_APP_H

/* Instrument state, button handling, USB commands and screen drawing. Nothing here touches hardware,
 * so the whole user interface runs (and is tested) on the host; src/main.c feeds it captures. */

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "probepen_afe.h"
#include "probepen_scope.h"

#ifdef __cplusplus
extern "C" {
#endif

#define APP_SYS_HZ        150000000.0f   /* RP2354B default system clock */
#define APP_ADC_MAX_HZ    75000000.0f    /* two PIO cycles per sample at clkdiv 1 (AD9288-80) */
#define APP_FW_VERSION    "0.3"

typedef enum {
    APP_MODE_DUAL = 0,   /* both scope channels */
    APP_MODE_CH1,
    APP_MODE_CH2,
    APP_MODE_LOGIC,      /* 4-channel logic pod */
    APP_MODE_METER,      /* DC volts from the MCU ADC */
    APP_MODE_COUNT
} app_mode_t;

enum { BTN_ASK = 0, BTN_MODE = 1, BTN_UP = 2, BTN_DOWN = 3, BTN_COUNT = 4 };

typedef enum {
    BTN_EV_NONE = 0,
    BTN_EV_SHORT = 1,    /* released before the long-press time */
    BTN_EV_LONG = 2,     /* held for BTN_LONG_MS */
    BTN_EV_REPEAT = 3    /* still held, every BTN_REPEAT_MS after the long press */
} btn_event_t;

#define BTN_DEBOUNCE_MS 20u
#define BTN_LONG_MS     600u
#define BTN_REPEAT_MS   150u

typedef struct {
    uint8_t raw;
    uint8_t stable;
    uint32_t t_change[BTN_COUNT];
    uint32_t t_press[BTN_COUNT];
    uint32_t t_repeat[BTN_COUNT];
    bool long_fired[BTN_COUNT];
} buttons_t;

void buttons_init(buttons_t *b);
/* pressed_mask: bit n set while button n is down. Writes (button << 4 | event) codes; returns how many. */
size_t buttons_update(buttons_t *b, uint8_t pressed_mask, uint32_t now_ms, uint8_t *events, size_t max_events);

typedef struct {
    app_mode_t mode;
    uint8_t tb;                 /* timebase index, see app_timebase_ns() */
    bool run;                   /* false = hold the last capture */
    bool auto_range;
    afe_range_t range[2];
    uint16_t trig_level;        /* 8-bit ADC code */
    uint8_t trig_src;           /* 0 = CH1, 1 = CH2 */
    scope_trig_slope_t trig_slope;
    uint32_t logic_thr_mv;
    afe_cal_t cal;

    /* results of the last capture */
    scope_meas_t meas[2];
    bool triggered;
    size_t win_start;           /* first sample shown */
    size_t win_len;             /* samples across the screen */
    float sample_rate_hz;
    float dc_v[2];
    float vbus_v;
    uint32_t pod_edges[4];
    uint8_t pod_level[4];
    float pod_freq_hz[4];

    /* requests for the main loop */
    bool ask_pending;           /* ASK held: send a snapshot to the host */
    bool cal_pending;           /* CAL: zero the offsets on the next capture (inputs shorted) */
    bool cal_changed;           /* calibration updated: the main loop stores it in flash */
    uint16_t capture_req;       /* CAPTURE n: send n samples per channel after the next capture */
} app_t;

void app_init(app_t *app);

size_t app_timebase_count(void);
uint32_t app_timebase_ns(const app_t *app);           /* time per division */
uint32_t app_clkdiv(const app_t *app);                 /* integer PIO divider for the scope / pod clock */
float app_sample_rate_hz(const app_t *app);           /* = APP_ADC_MAX_HZ / clkdiv */
size_t app_screen_samples(const app_t *app);          /* samples across the 10 divisions */
size_t app_capture_samples(const app_t *app, size_t max_samples);

void app_button_event(app_t *app, uint8_t button, btn_event_t ev);
void app_process_scope(app_t *app, const uint16_t *ch_a, const uint16_t *ch_b, size_t n);
void app_process_logic(app_t *app, const uint8_t *nibbles, size_t n);
/* Raw 12-bit MCU ADC readings of CH1_DC_SENSE, CH2_DC_SENSE and VBUS_SENSE (3.000 V reference). */
void app_set_dc(app_t *app, uint16_t raw_ch1, uint16_t raw_ch2, uint16_t raw_vbus);

/* One text command line in, one reply line out (no newline). Returns the reply length. */
size_t app_usb_command(app_t *app, const char *line, char *reply, size_t max_len);
/* "CH1 VPP=... F=...;CH2 ..." summary of the last capture. */
size_t app_format_meas(const app_t *app, char *out, size_t max_len);
/* Engineering notation with 3 significant digits: 0.0123 V -> "12.3mV". */
size_t app_format_eng(float value, const char *unit, char *out, size_t max_len);

/* Draws the whole screen for the current mode into an LCD_WIDTH x LCD_HEIGHT RGB565 framebuffer.
 * ch_a / ch_b / n: the last scope capture; nibbles / n_nib: the last pod capture (either may be NULL). */
void app_render(const app_t *app, uint16_t *fb, const uint16_t *ch_a, const uint16_t *ch_b, size_t n,
                const uint8_t *nibbles, size_t n_nib);

#ifdef __cplusplus
}
#endif

#endif /* PROBEPEN_APP_H */
