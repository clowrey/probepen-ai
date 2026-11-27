#include "probepen_stream.h"

#include <math.h>
#include <string.h>

#include "probepen_usb.h"

static uint8_t rbit8(uint8_t b) {
    b = (uint8_t)((b & 0xF0u) >> 4 | (b & 0x0Fu) << 4);
    b = (uint8_t)((b & 0xCCu) >> 2 | (b & 0x33u) << 2);
    b = (uint8_t)((b & 0xAAu) >> 1 | (b & 0x55u) << 1);
    return b;
}

bool stream_plan(stream_t *s, float want_hz, uint8_t chmask, float adc_max_hz, uint8_t range_flags) {
    if (!s || !(want_hz > 0.0f) || want_hz > adc_max_hz || chmask < 1u || chmask > 3u) {
        return false;
    }
    memset(s, 0, sizeof *s);
    /* average just enough raw samples that the divider fits in 16 bits */
    double k_total = (double)adc_max_hz / (double)want_hz;   /* raw clocks per output sample */
    uint32_t avg = (uint32_t)ceil(k_total / (double)STREAM_CLKDIV_MAX);
    if (avg < 1u) {
        avg = 1u;
    }
    double k = floor(k_total / (double)avg + 0.5);
    if (k < 1.0) {
        k = 1.0;
    } else if (k > (double)STREAM_CLKDIV_MAX) {
        k = (double)STREAM_CLKDIV_MAX;
    }
    s->chmask = chmask;
    s->clkdiv = (uint32_t)k;
    s->avg = avg;
    s->raw_hz = adc_max_hz / (float)s->clkdiv;
    s->rate_hz = s->raw_hz / (float)avg;
    s->range_flags = (uint8_t)(range_flags & (STREAM_FLAG_CH1_10X | STREAM_FLAG_CH2_10X));
    return true;
}

float stream_bytes_per_s(const stream_t *s) {
    if (!s) {
        return 0.0f;
    }
    return s->rate_hz * (float)((s->chmask == 3u) ? 2u : 1u);
}

static void emit_block(stream_t *s, stream_emit_fn emit, void *ctx) {
    uint32_t first = s->block_first;
    uint8_t *b = s->block;
    b[0] = STREAM_MAGIC0;
    b[1] = STREAM_MAGIC1;
    b[2] = (uint8_t)(s->range_flags | (s->lost ? STREAM_FLAG_LOST : 0u));
    b[3] = s->chmask;
    b[4] = (uint8_t)s->fill;
    b[5] = (uint8_t)(s->fill >> 8);
    b[6] = (uint8_t)first;
    b[7] = (uint8_t)(first >> 8);
    b[8] = (uint8_t)(first >> 16);
    b[9] = (uint8_t)(first >> 24);
    size_t n = STREAM_HDR_BYTES + s->fill;
    uint16_t crc = usb_crc16_ccitt(b, n);
    b[n] = (uint8_t)crc;
    b[n + 1u] = (uint8_t)(crc >> 8);
    if (emit) {
        emit(b, n + 2u, ctx);
    }
    s->bytes += n + 2u;
    ++s->blocks;
    s->lost = false;
    s->fill = 0u;
}

void stream_push_words(stream_t *s, const uint32_t *words, size_t n, stream_emit_fn emit, void *ctx) {
    if (!s || !words || s->avg == 0u) {
        return;
    }
    size_t nch = (s->chmask == 3u) ? 2u : 1u;
    /* a block holds whole samples of every channel */
    size_t cap = STREAM_PAYLOAD_MAX - (STREAM_PAYLOAD_MAX % nch);
    for (size_t i = 0u; i < n; ++i) {
        uint32_t w = words[i];
        s->acc[0] += 255u - rbit8((uint8_t)w);                 /* the gain stage inverts */
        s->acc[1] += 255u - ((w >> 16) & 0xFFu);
        if (++s->nacc < s->avg) {
            continue;
        }
        uint32_t half = s->avg / 2u;
        if (s->fill == 0u) {
            s->block_first = s->out_index;
        }
        if (s->chmask & 1u) {
            s->block[STREAM_HDR_BYTES + s->fill++] = (uint8_t)((s->acc[0] + half) / s->avg);
        }
        if (s->chmask & 2u) {
            s->block[STREAM_HDR_BYTES + s->fill++] = (uint8_t)((s->acc[1] + half) / s->avg);
        }
        s->acc[0] = s->acc[1] = 0u;
        s->nacc = 0u;
        ++s->out_index;
        if (s->fill >= cap) {
            emit_block(s, emit, ctx);
        }
    }
}

void stream_push_codes(stream_t *s, const uint8_t *ch1, const uint8_t *ch2, size_t n, stream_emit_fn emit, void *ctx) {
    if (!s) {
        return;
    }
    size_t nch = (s->chmask == 3u) ? 2u : 1u;
    size_t cap = STREAM_PAYLOAD_MAX - (STREAM_PAYLOAD_MAX % nch);
    for (size_t i = 0u; i < n; ++i) {
        if (s->fill == 0u) {
            s->block_first = s->out_index;
        }
        if ((s->chmask & 1u) && ch1) {
            s->block[STREAM_HDR_BYTES + s->fill++] = ch1[i];
        }
        if ((s->chmask & 2u) && ch2) {
            s->block[STREAM_HDR_BYTES + s->fill++] = ch2[i];
        }
        ++s->out_index;
        if (s->fill >= cap) {
            emit_block(s, emit, ctx);
        }
    }
}

void stream_skip_raw(stream_t *s, uint32_t n, stream_emit_fn emit, void *ctx) {
    if (!s || n == 0u || s->avg == 0u) {
        return;
    }
    stream_flush(s, emit, ctx);
    /* drop the partial average too: the next sample starts clean after the gap */
    uint32_t skipped = (n + s->nacc) / s->avg;
    s->acc[0] = s->acc[1] = 0u;
    s->nacc = 0u;
    s->out_index += skipped;
    s->lost_samples += skipped;
    ++s->lost_events;
    s->lost = true;
}

void stream_flush(stream_t *s, stream_emit_fn emit, void *ctx) {
    if (s && s->fill > 0u) {
        emit_block(s, emit, ctx);
    }
}
