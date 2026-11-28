#include "probepen_scope.h"
#include <math.h>
#include <stddef.h>

static uint8_t rbit8(uint8_t b) {
    b = (uint8_t)((b & 0xF0u) >> 4 | (b & 0x0Fu) << 4);
    b = (uint8_t)((b & 0xCCu) >> 2 | (b & 0x33u) << 2);
    b = (uint8_t)((b & 0xAAu) >> 1 | (b & 0x55u) << 1);
    return b;
}

size_t scope_unpack_ad9288(const uint32_t *words, size_t n_words, uint16_t *out_a, uint16_t *out_b, size_t max_samples) {
    if (!words || max_samples == 0u) {
        return 0u;
    }
    size_t n = (n_words < max_samples) ? n_words : max_samples;
    for (size_t i = 0u; i < n; ++i) {
        uint32_t w = words[i];
        if (out_a) {
            out_a[i] = rbit8((uint8_t)(w & 0xFFu));           /* GPIO16..23 = ADCA_D7..D0 */
        }
        if (out_b) {
            out_b[i] = (uint16_t)((w >> 16u) & 0xFFu);       /* GPIO32..39 = ADCB_D0..D7 */
        }
    }
    return n;
}

void scope_peak_group(const uint32_t *ring, size_t ring_len, size_t start, size_t m, uint16_t out_a[2],
                      uint16_t out_b[2]) {
    /* channel A: bit-reverse then invert; channel B: invert (both rise with the tip voltage afterwards) */
    static uint8_t lut_a[256];
    static bool lut_ready = false;
    if (!lut_ready) {
        for (unsigned i = 0u; i < 256u; ++i) {
            lut_a[i] = (uint8_t)(SCOPE_CODE_MAX - rbit8((uint8_t)i));
        }
        lut_ready = true;
    }
    uint8_t a_min = 255u, a_max = 0u, b_min = 255u, b_max = 0u;
    size_t a_imin = 0u, a_imax = 0u, b_imin = 0u, b_imax = 0u;
    for (size_t k = 0u; k < m; ++k) {
        uint32_t w = ring[(start + k) % ring_len];
        uint8_t a = lut_a[w & 0xFFu];
        uint8_t b = (uint8_t)(SCOPE_CODE_MAX - ((w >> 16u) & 0xFFu));
        if (a < a_min) { a_min = a; a_imin = k; }
        if (a > a_max) { a_max = a; a_imax = k; }
        if (b < b_min) { b_min = b; b_imin = k; }
        if (b > b_max) { b_max = b; b_imax = k; }
    }
    /* keep time order: the extreme that came first is output first */
    out_a[0] = a_imin <= a_imax ? a_min : a_max;
    out_a[1] = a_imin <= a_imax ? a_max : a_min;
    out_b[0] = b_imin <= b_imax ? b_min : b_max;
    out_b[1] = b_imin <= b_imax ? b_max : b_min;
}

void scope_invert_codes(uint16_t *codes, size_t n) {
    for (size_t i = 0u; codes && i < n; ++i) {
        codes[i] = (uint16_t)(SCOPE_CODE_MAX - (codes[i] & SCOPE_CODE_MAX));
    }
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
    if (hi_arm > SCOPE_CODE_MAX) {
        hi_arm = SCOPE_CODE_MAX;
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
    out_meas->vrms_ac = 0.0f;
    out_meas->t_high_s = 0.0f;
    out_meas->t_low_s = 0.0f;
    out_meas->duty = 0.0f;

    if (!samples || count == 0u) {
        return;
    }

    uint16_t smin = SCOPE_CODE_MAX;
    uint16_t smax = 0u;
    double sum = 0.0;
    for (size_t i = 0u; i < count; ++i) {
        uint16_t s = samples[i] & SCOPE_CODE_MAX;
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
            uint16_t s = samples[i] & SCOPE_CODE_MAX;
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

    /* Pulse widths: a Schmitt trigger centred on the midpoint, so equal rise and fall times cancel. Only
     * whole intervals between two transitions count. The AC RMS uses whole periods (first to last rising
     * transition) when there are two or more, else the whole record. */
    size_t rms_from = 0u, rms_to = count;
    if (sample_rate_hz > 0.0f && smax > (uint16_t)(smin + 6u)) {
        uint16_t h2 = (uint16_t)((smax - smin) / 32u + 1u);
        uint16_t up = (uint16_t)(mid_code + h2), dn = (uint16_t)(mid_code - h2);
        int lvl = -1;
        size_t last_t = 0u, first_rise = 0u, last_rise = 0u, rises = 0u;
        bool have_t = false;
        double sum_hi = 0.0, sum_lo = 0.0;
        size_t n_hi = 0u, n_lo = 0u;
        for (size_t i = 0u; i < count; ++i) {
            uint16_t s = samples[i] & SCOPE_CODE_MAX;
            int now = lvl;
            if (s >= up) {
                now = 1;
            } else if (s <= dn) {
                now = 0;
            }
            if (now != lvl) {
                if (lvl >= 0) {   /* a transition */
                    if (have_t) {
                        if (now == 0) {
                            sum_hi += (double)(i - last_t);
                            ++n_hi;
                        } else {
                            sum_lo += (double)(i - last_t);
                            ++n_lo;
                        }
                    }
                    if (now == 1) {
                        if (rises++ == 0u) {
                            first_rise = i;
                        }
                        last_rise = i;
                    }
                    have_t = true;
                    last_t = i;
                }
                lvl = now;
            }
        }
        if (n_hi > 0u && n_lo > 0u) {
            out_meas->t_high_s = (float)(sum_hi / (double)n_hi) / sample_rate_hz;
            out_meas->t_low_s = (float)(sum_lo / (double)n_lo) / sample_rate_hz;
            out_meas->duty = out_meas->t_high_s / (out_meas->t_high_s + out_meas->t_low_s);
        }
        if (rises >= 2u && last_rise > first_rise) {
            rms_from = first_rise;
            rms_to = last_rise;
        }
    }
    double s1 = 0.0, s2 = 0.0;
    for (size_t i = rms_from; i < rms_to; ++i) {
        double s = (double)(samples[i] & SCOPE_CODE_MAX);
        s1 += s;
        s2 += s * s;
    }
    double nn = (double)(rms_to - rms_from);
    double var = nn > 0.0 ? s2 / nn - (s1 / nn) * (s1 / nn) : 0.0;
    out_meas->vrms_ac = (float)sqrt(var > 0.0 ? var : 0.0) * code_scale;
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
        uint16_t smin = SCOPE_CODE_MAX;
        uint16_t smax = 0u;
        for (size_t i = i0; i < i1; ++i) {
            uint16_t s = samples[i] & SCOPE_CODE_MAX;
            if (s < smin) {
                smin = s;
            }
            if (s > smax) {
                smax = s;
            }
        }
        uint32_t y_hi = ((uint32_t)(SCOPE_CODE_MAX - smax) * max_y) / SCOPE_CODE_MAX;
        uint32_t y_lo = ((uint32_t)(SCOPE_CODE_MAX - smin) * max_y) / SCOPE_CODE_MAX;
        out_ymin[col] = (uint8_t)y_hi;
        out_ymax[col] = (uint8_t)y_lo;
    }
}
