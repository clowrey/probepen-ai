#include "probepen_deep.h"

#include <math.h>

uint32_t deep_max_samples(uint8_t chmask) {
    if (chmask < 1u || chmask > 3u) {
        return 0u;
    }
    uint32_t n = (chmask == 3u) ? DEEP_BYTES / 2u : DEEP_BYTES;
    return n & ~3u;
}

bool deep_plan(uint32_t want_n, uint8_t chmask, float want_hz, float adc_max_hz, uint32_t *n_out,
               uint32_t *clkdiv_out, float *rate_out) {
    uint32_t max = deep_max_samples(chmask);
    if (max == 0u || !(want_hz > 0.0f) || want_hz > adc_max_hz || want_n > max) {
        return false;
    }
    uint32_t n = want_n ? (want_n & ~3u) : max;
    if (n < 4u) {
        return false;
    }
    double k = floor((double)adc_max_hz / (double)want_hz + 0.5);
    if (k < 1.0) {
        k = 1.0;
    } else if (k > 65535.0) {
        k = 65535.0;
    }
    if (n_out) {
        *n_out = n;
    }
    if (clkdiv_out) {
        *clkdiv_out = (uint32_t)k;
    }
    if (rate_out) {
        *rate_out = adc_max_hz / (float)k;
    }
    return true;
}

void deep_decode(uint8_t *buf, size_t n, bool bit_reversed) {
    static uint8_t lut[2][256];
    static bool ready = false;
    if (!ready) {
        for (unsigned i = 0u; i < 256u; ++i) {
            uint8_t r = 0u;
            for (unsigned k = 0u; k < 8u; ++k) {
                r = (uint8_t)(r | (((i >> k) & 1u) << (7u - k)));
            }
            lut[0][i] = (uint8_t)(255u - i);
            lut[1][i] = (uint8_t)(255u - r);
        }
        ready = true;
    }
    const uint8_t *t = lut[bit_reversed ? 1 : 0];
    for (size_t i = 0u; buf && i < n; ++i) {
        buf[i] = t[buf[i]];
    }
}

long deep_find_trigger(const uint8_t *codes, size_t n, size_t start, uint8_t level, uint8_t hysteresis, bool falling) {
    if (!codes) {
        return -1;
    }
    int lo = (int)level - (int)hysteresis, hi = (int)level + (int)hysteresis;
    bool armed = false;
    for (size_t i = start; i < n; ++i) {
        int s = codes[i];
        if (!falling) {
            if (s <= lo) {
                armed = true;
            } else if (armed && s >= (int)level) {
                return (long)i;
            }
        } else {
            if (s >= hi) {
                armed = true;
            } else if (armed && s <= (int)level) {
                return (long)i;
            }
        }
    }
    return -1;
}
