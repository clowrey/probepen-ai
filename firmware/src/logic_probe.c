#include "probepen_logic.h"
#include <math.h>

uint16_t logic_thr_mv_to_pwm_level(uint32_t thr_mv, uint16_t pwm_wrap, float vdd_mv) {
    if (pwm_wrap == 0u || vdd_mv <= 1.0f) {
        return 0u;
    }
    if ((float)thr_mv >= vdd_mv) {
        return pwm_wrap;
    }
    float frac = (float)thr_mv / vdd_mv;
    if (frac <= 0.0f) {
        return 0u;
    }
    long level = lroundf(frac * (float)pwm_wrap);
    if (level < 0L) {
        return 0u;
    }
    if (level > (long)pwm_wrap) {
        return pwm_wrap;
    }
    return (uint16_t)level;
}

uint32_t logic_family_default_mv(logic_family_t family) {
    switch (family) {
    case LOGIC_FAMILY_TTL:
        return 1400u;
    case LOGIC_FAMILY_CMOS18:
        return 900u;
    case LOGIC_FAMILY_CMOS33:
    default:
        return 1650u;
    }
}

logic_state_t logic_classify_sample(bool comp_high, bool tip_float, uint32_t edge_count) {
    if (edge_count >= 2u) {
        return LOGIC_PULSE;
    }
    if (tip_float) {
        return LOGIC_FLOAT;
    }
    return comp_high ? LOGIC_HIGH : LOGIC_LOW;
}

size_t logic_unpack_pod_nibbles(const uint32_t *words, size_t n_words, uint8_t *out_nibbles, size_t max_samples) {
    if (!words || !out_nibbles || max_samples == 0u) {
        return 0u;
    }
    size_t out_idx = 0u;
    for (size_t i = 0u; i < n_words && out_idx < max_samples; ++i) {
        uint32_t w = words[i];
        for (size_t n = 0u; n < 8u && out_idx < max_samples; ++n) {
            out_nibbles[out_idx++] = (uint8_t)((w >> (n * 4u)) & 0x0Fu);
        }
    }
    return out_idx;
}

uint32_t logic_count_pod_edges(const uint8_t *nibbles, size_t count, uint8_t channel_idx) {
    if (!nibbles || count < 2u || channel_idx > 3u) {
        return 0u;
    }
    uint8_t mask = (uint8_t)(1u << channel_idx);
    uint8_t prev = (nibbles[0] & mask);
    uint32_t edges = 0u;
    for (size_t i = 1u; i < count; ++i) {
        uint8_t cur = (nibbles[i] & mask);
        if (cur != prev) {
            ++edges;
            prev = cur;
        }
    }
    return edges;
}
