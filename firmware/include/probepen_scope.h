#ifndef PROBEPEN_SCOPE_H
#define PROBEPEN_SCOPE_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

typedef enum {
    TRIG_RISING  = 0,
    TRIG_FALLING = 1
} scope_trig_slope_t;

typedef struct {
    float vmin;
    float vmax;
    float vpp;
    float vavg;
    float freq_hz;
    size_t trigger_idx;
    bool triggered;
} scope_meas_t;

#define SCOPE_CODE_MAX 255u   /* AD9288: 8-bit codes */

/* One PIO word per sample: channel A in bits 0..7 (bit-reversed), channel B in bits 16..23. */
size_t scope_unpack_ad9288(const uint32_t *words, size_t n_words, uint16_t *out_a, uint16_t *out_b, size_t max_samples);
/* code -> 255 - code. The front end's gain stage is inverting, so the main loop flips each capture once
 * after unpacking; everything after that sees codes that rise with the tip voltage. */
void scope_invert_codes(uint16_t *codes, size_t n);
/* Peak detect: reduces m raw PIO words (ring[start..start+m), wrapping at ring_len) to two samples per
 * channel, the minimum and the maximum in the order they occurred, already bit-reversed and inverted. */
void scope_peak_group(const uint32_t *ring, size_t ring_len, size_t start, size_t m, uint16_t out_a[2],
                      uint16_t out_b[2]);
size_t scope_find_trigger(
    const uint16_t *samples,
    size_t count,
    uint16_t level_code,
    uint16_t hysteresis,
    scope_trig_slope_t slope,
    size_t pre_trigger,
    bool *found_out
);
void scope_measure_buffer(
    const uint16_t *samples,
    size_t count,
    float sample_rate_hz,
    float code_scale,
    float zero_code,
    scope_trig_slope_t slope,
    scope_meas_t *out_meas
);
void scope_decimate_min_max(
    const uint16_t *samples,
    size_t count,
    uint8_t *out_ymin,
    uint8_t *out_ymax,
    size_t lcd_cols,
    uint8_t plot_height
);

#ifdef __cplusplus
}
#endif

#endif /* PROBEPEN_SCOPE_H */
