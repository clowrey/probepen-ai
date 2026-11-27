#include "probepen_calstore.h"

#include <math.h>
#include <string.h>

#include "probepen_usb.h"

#define N_PER_CH 7u    /* floats per channel */
#define HDR      8u    /* magic (4), version (2), float count (2) */

static void put32(uint8_t *p, uint32_t v) {
    p[0] = (uint8_t)v;
    p[1] = (uint8_t)(v >> 8);
    p[2] = (uint8_t)(v >> 16);
    p[3] = (uint8_t)(v >> 24);
}

static uint32_t get32(const uint8_t *p) {
    return (uint32_t)p[0] | ((uint32_t)p[1] << 8) | ((uint32_t)p[2] << 16) | ((uint32_t)p[3] << 24);
}

static void to_floats(const afe_cal_t *cal, float f[N_PER_CH]) {
    f[0] = cal->vref_v;
    f[1] = cal->mid_bias_1x_v;
    f[2] = cal->mid_bias_10x_v;
    f[3] = cal->gain_1x;
    f[4] = cal->offset_1x_v;
    f[5] = cal->gain_10x;
    f[6] = cal->offset_10x_v;
}

/* false for values far from the design (a corrupted or foreign record) */
static bool from_floats(const float f[N_PER_CH], afe_cal_t *cal) {
    for (size_t i = 0u; i < N_PER_CH; ++i) {
        if (!isfinite(f[i])) {
            return false;
        }
    }
    if (f[3] < 5.0f || f[3] > 20.0f || f[5] < 25.0f || f[5] > 110.0f || fabsf(f[4]) > 1.0f || fabsf(f[6]) > 5.0f) {
        return false;
    }
    cal->vref_v = f[0];
    cal->mid_bias_1x_v = f[1];
    cal->mid_bias_10x_v = f[2];
    cal->gain_1x = f[3];
    cal->offset_1x_v = f[4];
    cal->gain_10x = f[5];
    cal->offset_10x_v = f[6];
    return true;
}

size_t calstore_pack(const afe_cal_t cal[2], uint8_t out[CALSTORE_BYTES]) {
    if (!cal || !out) {
        return 0u;
    }
    memset(out, 0xFF, CALSTORE_BYTES);
    put32(out, CALSTORE_MAGIC);
    out[4] = (uint8_t)CALSTORE_VERSION;
    out[5] = 0u;
    out[6] = (uint8_t)(2u * N_PER_CH);
    out[7] = 0u;
    for (size_t c = 0u; c < 2u; ++c) {
        float f[N_PER_CH];
        to_floats(&cal[c], f);
        for (size_t i = 0u; i < N_PER_CH; ++i) {
            uint32_t bits;
            memcpy(&bits, &f[i], sizeof bits);
            put32(out + HDR + 4u * (c * N_PER_CH + i), bits);
        }
    }
    size_t n = HDR + 4u * 2u * N_PER_CH;
    uint16_t crc = usb_crc16_ccitt(out, n);
    out[n] = (uint8_t)crc;
    out[n + 1u] = (uint8_t)(crc >> 8);
    return n + 2u;
}

bool calstore_unpack(const uint8_t in[CALSTORE_BYTES], afe_cal_t cal[2]) {
    if (!in || !cal || get32(in) != CALSTORE_MAGIC) {
        return false;
    }
    /* version 1: one set for both channels; version 2: CH1 then CH2 */
    size_t sets = (in[4] == 1u) ? 1u : (in[4] == 2u) ? 2u : 0u;
    if (sets == 0u || in[6] != sets * N_PER_CH) {
        return false;
    }
    size_t n = HDR + 4u * sets * N_PER_CH;
    uint16_t crc = (uint16_t)(in[n] | (in[n + 1u] << 8));
    if (crc != usb_crc16_ccitt(in, n)) {
        return false;
    }
    afe_cal_t got[2];
    for (size_t c = 0u; c < sets; ++c) {
        float f[N_PER_CH];
        for (size_t i = 0u; i < N_PER_CH; ++i) {
            uint32_t bits = get32(in + HDR + 4u * (c * N_PER_CH + i));
            memcpy(&f[i], &bits, sizeof bits);
        }
        if (!from_floats(f, &got[c])) {
            return false;
        }
    }
    if (sets == 1u) {
        got[1] = got[0];
    }
    cal[0] = got[0];
    cal[1] = got[1];
    return true;
}
