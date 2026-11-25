#include "probepen_scope.h"
#include <stddef.h>

size_t scope_unpack_pio30(const uint32_t *words, size_t n_words, uint16_t *out_samples, size_t max_samples) {
    if (!words || !out_samples || max_samples == 0u) {
        return 0u;
    }
    size_t out_idx = 0u;
    for (size_t i = 0u; i < n_words && out_idx < max_samples; ++i) {
        uint32_t w = words[i];
        out_samples[out_idx++] = (uint16_t)((w >> 20u) & 0x3FFu);
        if (out_idx < max_samples) {
            out_samples[out_idx++] = (uint16_t)((w >> 10u) & 0x3FFu);
        }
        if (out_idx < max_samples) {
            out_samples[out_idx++] = (uint16_t)(w & 0x3FFu);
        }
    }
    return out_idx;
}

size_t scope_find_trigger(
    const uint16_t *samples,
    size_t count,
    uint16_t level_code,
    uint16_t hysteresis,
    scope_trig_slope_t slope,
    size_t pre_trigger,
    bool *found_out
) {
    if (found_out) {
        *found_out = false;
    }
    if (!samples || count < 2u || pre_trigger >= count) {
        return 0u;
    }
    uint16_t lo_arm = (level_code > hysteresis) ? (uint16_t)(level_code - hysteresis) : 0u;
    uint16_t hi_arm = (uint16_t)(level_code + hysteresis);
    if (hi_arm > 1023u) {
        hi_arm = 1023u;
    }

    bool armed = false;
    size_t start = (pre_trigger > 0u) ? pre_trigger : 1u;
    for (size_t i = start; i < count; ++i) {
        uint16_t s = samples[i];
        if (slope == TRIG_RISING) {
            if (s <= lo_arm) {
                armed = true;
            } else if (armed && s >= level_code) {
                if (found_out) {
                    *found_out = true;
                }
                return i;
            }
        } else {
            if (s >= hi_arm) {
                armed = true;
            } else if (armed && s <= level_code) {
                if (found_out) {
                    *found_out = true;
                }
                return i;
            }
        }
    }
    return pre_trigger;
}

void scope_measure_buffer(
    const uint16_t *samples,
    size_t count,
    float sample_rate_hz,
    float code_scale,
    float zero_code,
    scope_trig_slope_t slope,
    scope_meas_t *out_meas
) {
    if (!out_meas) {
        return;
    }
    out_meas->vmin = 0.0f;
    out_meas->vmax = 0.0f;
    out_meas->vpp = 0.0f;
    out_meas->vavg = 0.0f;
    out_meas->freq_hz = 0.0f;
    out_meas->trigger_idx = 0u;
    out_meas->triggered = false;

    if (!samples || count == 0u) {
        return;
    }

    uint16_t smin = 1023u;
    uint16_t smax = 0u;
    double sum = 0.0;
    for (size_t i = 0u; i < count; ++i) {
        uint16_t s = samples[i] & 0x3FFu;
        if (s < smin) {
            smin = s;
        }
        if (s > smax) {
            smax = s;
        }
        sum += (double)s;
    }

    out_meas->vmin = ((float)smin - zero_code) * code_scale;
    out_meas->vmax = ((float)smax - zero_code) * code_scale;
    out_meas->vpp = out_meas->vmax - out_meas->vmin;
    out_meas->vavg = ((float)(sum / (double)count) - zero_code) * code_scale;

    uint16_t mid_code = (uint16_t)(((uint32_t)smin + (uint32_t)smax) / 2u);
    uint16_t hyst = (uint16_t)((smax - smin) / 16u);
    if (hyst < 2u) {
        hyst = 2u;
    }

    out_meas->trigger_idx = scope_find_trigger(
        samples, count, mid_code, hyst, slope, count / 8u, &out_meas->triggered
    );

    if (sample_rate_hz > 0.0f && smax > (uint16_t)(smin + 6u)) {
        uint16_t arm_lo = (mid_code > hyst) ? (uint16_t)(mid_code - hyst) : 0u;
        bool armed = false;
        size_t first_cross = 0u;
        size_t last_cross = 0u;
        size_t periods = 0u;
        for (size_t i = 0u; i < count; ++i) {
            uint16_t s = samples[i] & 0x3FFu;
            if (s <= arm_lo) {
                armed = true;
            } else if (armed && s >= mid_code) {
                armed = false;
                if (first_cross == 0u && periods == 0u) {
                    first_cross = i;
                } else {
                    last_cross = i;
                    ++periods;
                }
            }
        }
        if (periods > 0u && last_cross > first_cross) {
            float avg_period_samples = (float)(last_cross - first_cross) / (float)periods;
            out_meas->freq_hz = sample_rate_hz / avg_period_samples;
        }
    }
}

void scope_decimate_min_max(
    const uint16_t *samples,
    size_t count,
    uint8_t *out_ymin,
    uint8_t *out_ymax,
    size_t lcd_cols,
    uint8_t plot_height
) {
    if (!samples || count == 0u || !out_ymin || !out_ymax || lcd_cols == 0u || plot_height < 2u) {
        return;
    }
    uint8_t max_y = (uint8_t)(plot_height - 1u);
    for (size_t col = 0u; col < lcd_cols; ++col) {
        size_t i0 = (col * count) / lcd_cols;
        size_t i1 = ((col + 1u) * count) / lcd_cols;
        if (i1 <= i0) {
            i1 = i0 + 1u;
        }
        if (i1 > count) {
            i1 = count;
        }
        uint16_t smin = 1023u;
        uint16_t smax = 0u;
        for (size_t i = i0; i < i1; ++i) {
            uint16_t s = samples[i] & 0x3FFu;
            if (s < smin) {
                smin = s;
            }
            if (s > smax) {
                smax = s;
            }
        }
        uint32_t y_hi = ((uint32_t)(1023u - smax) * max_y) / 1023u;
        uint32_t y_lo = ((uint32_t)(1023u - smin) * max_y) / 1023u;
        out_ymin[col] = (uint8_t)y_hi;
        out_ymax[col] = (uint8_t)y_lo;
    }
}
