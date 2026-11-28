#include "probepen_decode.h"

#include <math.h>
#include <stdio.h>
#include <string.h>

enum {
    ST_WAIT_IDLE = 0,   /* levels or bus position unknown: wait for a long idle run */
    ST_IDLE,            /* UART: waiting for a start bit; CAN: waiting for SOF */
    ST_UART_DATA,
    ST_UART_STOP,
    ST_CAN_FRAME,       /* SOF .. data (and the classic CRC), dynamic stuffing */
    ST_CAN_FIXED,       /* CAN FD stuff count and CRC, fixed stuff bits */
    ST_CAN_AFTER,       /* CRC done: CRC delimiter, ACK, EOF until the bus is idle */
};

#define MIN_SPAN_CODES 12   /* above the noise of a quiet line; a 3.3 V UART on the +-26 V range is 15 */
#define CHUNK 256u

/* ---- CRCs (shift register as in ISO 11898-1: the register is the CRC sequence) ------------------ */

uint16_t dec_crc15_bit(uint16_t crc, uint8_t bit) {
    uint16_t nxt = (uint16_t)((bit & 1u) ^ ((crc >> 14) & 1u));
    crc = (uint16_t)((crc << 1) & 0x7FFFu);
    return nxt ? (uint16_t)(crc ^ 0x4599u) : crc;
}

uint32_t dec_crc17_bit(uint32_t crc, uint8_t bit) {
    uint32_t nxt = (bit & 1u) ^ ((crc >> 16) & 1u);
    crc = (crc << 1) & 0x1FFFFu;
    return nxt ? crc ^ 0x1685Bu : crc;
}

uint32_t dec_crc21_bit(uint32_t crc, uint8_t bit) {
    uint32_t nxt = (bit & 1u) ^ ((crc >> 20) & 1u);
    crc = (crc << 1) & 0x1FFFFFu;
    return nxt ? crc ^ 0x102899u : crc;
}

uint8_t dec_stuff_count_bits(uint16_t stuff_bits) {
    uint8_t c = (uint8_t)(stuff_bits & 7u);
    uint8_t g = (uint8_t)(c ^ (c >> 1));
    uint8_t parity = (uint8_t)(((g >> 2) ^ (g >> 1) ^ g) & 1u);   /* makes the count of ones even */
    return (uint8_t)((g << 1) | parity);
}

uint8_t dec_can_dlc_len(uint8_t dlc, bool fd) {
    static const uint8_t FD_LEN[16] = {0, 1, 2, 3, 4, 5, 6, 7, 8, 12, 16, 20, 24, 32, 48, 64};
    dlc &= 15u;
    if (!fd) {
        return dlc > 8u ? 8u : dlc;
    }
    return FD_LEN[dlc];
}

/* ---- plan ---------------------------------------------------------------------------------------- */

void dec_reset(dec_t *d) {
    if (!d) {
        return;
    }
    d->env_hi = -1;
    d->env_lo = 0;
    d->th_hi = 255u;
    d->th_lo = 0u;
    d->levels_ok = false;
    d->level = 0u;
    d->idle_level = (int8_t)(d->proto == DEC_PROTO_UART ? 1 : -1);   /* a TTL UART idles high */
    d->pos = 0u;
    d->run_start = 0u;
    d->run_flushed = false;
    d->spb = d->spb_arb;
    d->bit_pos = 0.0;
    d->state = ST_WAIT_IDLE;
    memset(&d->rec, 0, sizeof d->rec);
    d->records = d->errors = d->lost_events = 0u;
    d->lost_samples = 0u;
}

bool dec_plan(dec_t *d, uint8_t proto, uint8_t src, float rate, float data_rate, float adc_max_hz) {
    if (!d || (proto != DEC_PROTO_UART && proto != DEC_PROTO_CAN) || src < DEC_SRC_CH1 || src > DEC_SRC_DIFF ||
        !(rate > 0.0f) || !(adc_max_hz > 0.0f)) {
        return false;
    }
    if (proto == DEC_PROTO_UART || !(data_rate > 0.0f)) {
        data_rate = rate;
    }
    if (data_rate < rate) {
        return false;
    }
    memset(d, 0, sizeof *d);
    float fast = data_rate;
    double k8 = floor((double)adc_max_hz / (8.0 * (double)fast));
    double k;
    if (k8 >= 4.0) {
        k = k8;
    } else {
        double k4 = floor((double)adc_max_hz / (4.0 * (double)fast));   /* at least 4 samples per bit */
        if (k4 < 1.0) {
            return false;
        }
        k = k4 < 4.0 ? k4 : 4.0;
    }
    if (k > 65535.0) {
        k = 65535.0;   /* the PIO's integer divider: 1144 S/s, still 10 samples per bit at 110 baud */
    }
    d->proto = proto;
    d->src = src;
    d->rate = rate;
    d->data_rate = data_rate;
    d->clkdiv = (uint32_t)k;
    d->fs = adc_max_hz / (float)d->clkdiv;
    d->spb_arb = d->fs / rate;
    d->spb_data = d->fs / data_rate;
    d->burst = d->fs > DEC_LIVE_MAX_HZ;
    d->burst_n = (DEC_BURST_BYTES / (src == DEC_SRC_DIFF ? 2u : 1u)) & ~3u;
    d->coverage = d->burst ? 1.0f / (1.0f + d->fs * DEC_CYCLES_PER_SAMPLE / APP_SYS_HZ) : 1.0f;
    /* envelopes relax by about 2 codes per millisecond, in 1/256 code per CHUNK samples */
    double leak = 131072000.0 / (double)d->fs;
    d->leak = (int32_t)(leak < 1.0 ? 1.0 : leak);
    d->idle_samples = (uint32_t)(10.0f * d->spb_arb + 0.5f);
    dec_reset(d);
    return true;
}

/* ---- records ------------------------------------------------------------------------------------- */

static void emit_rec(dec_t *d, dec_emit_fn emit, void *ctx) {
    d->rec.proto = d->proto;
    if (d->rec.err != DEC_ERR_NONE || d->rec.framing_errors) {
        ++d->errors;
    }
    ++d->records;
    if (emit) {
        emit(&d->rec, ctx);
    }
    memset(&d->rec, 0, sizeof d->rec);
}

static void can_fail(dec_t *d, uint8_t err, dec_emit_fn emit, void *ctx) {
    d->rec.err = err;
    emit_rec(d, emit, ctx);
    d->state = ST_WAIT_IDLE;
    d->spb = d->spb_arb;
}

/* ---- UART ---------------------------------------------------------------------------------------- */

static void uart_bit(dec_t *d, uint8_t bit, dec_emit_fn emit, void *ctx) {
    switch (d->state) {
    case ST_IDLE:
        if (!bit) {
            if (d->rec.len == 0u) {
                d->rec.t_sample = (uint64_t)(d->bit_pos + 0.5);
            }
            d->u_nb = 0u;
            d->u_byte = 0u;
            d->state = ST_UART_DATA;
        }
        break;
    case ST_UART_DATA:
        d->u_byte = (uint8_t)(d->u_byte | (bit << d->u_nb));
        if (++d->u_nb == 8u) {
            d->state = ST_UART_STOP;
        }
        break;
    case ST_UART_STOP:
        if (!bit) {
            ++d->rec.framing_errors;
        }
        d->rec.data[d->rec.len++] = d->u_byte;
        d->state = ST_IDLE;
        if (d->u_byte == '\n' || d->rec.len >= DEC_MAX_BYTES) {
            emit_rec(d, emit, ctx);
        }
        break;
    default:
        break;
    }
}

/* ---- CAN ----------------------------------------------------------------------------------------- */

static void can_finish(dec_t *d, dec_emit_fn emit, void *ctx) {
    if (d->rec.fd) {
        if (d->c_sc_rx != dec_stuff_count_bits(d->c_stuff)) {
            can_fail(d, DEC_ERR_STUFFCOUNT, emit, ctx);
            return;
        }
        uint32_t mask = d->c_crc_len == 17u ? 0x1FFFFu : 0x1FFFFFu;
        if ((d->c_crc_calc & mask) != (d->c_crc_rx & mask)) {
            can_fail(d, DEC_ERR_CRC, emit, ctx);
            return;
        }
    } else if ((d->c_crc_calc & 0x7FFFu) != (d->c_crc_rx & 0x7FFFu)) {
        can_fail(d, DEC_ERR_CRC, emit, ctx);
        return;
    }
    d->state = ST_CAN_AFTER;   /* the CRC delimiter comes next; the rate switches back at its sample point */
    d->c_after = 0u;
}

/* one destuffed bit of the frame, SOF = bit 0 */
static void can_field(dec_t *d, uint8_t bit, dec_emit_fn emit, void *ctx) {
    dec_record_t *r = &d->rec;
    uint16_t nb = d->c_nb++;
    if (d->c_crc_stage) {   /* classic CRC sequence, still dynamically stuffed */
        d->c_crc_rx = (d->c_crc_rx << 1) | bit;
        if (++d->c_crc_nb == 15u) {
            d->c_skip_stuff = d->c_same == 5u;
            can_finish(d, emit, ctx);
        }
        return;
    }
    d->crc15 = dec_crc15_bit(d->crc15, bit);
    if (nb == 0u) {
        return;   /* SOF */
    }
    if (nb <= 11u) {
        r->id = (r->id << 1) | bit;
    } else if (nb == 12u) {
        d->c_r12 = bit;
    } else if (nb == 13u) {
        r->ext = bit != 0u;
        d->c_fdf_at = r->ext ? 33u : 14u;
        r->rtr = !r->ext && d->c_r12;
    } else if (r->ext && nb <= 31u) {
        r->id = (r->id << 1) | bit;
    } else if (r->ext && nb == 32u) {
        r->rtr = bit != 0u;
    } else if (nb == d->c_fdf_at) {
        r->fd = bit != 0u;
        if (r->fd) {
            r->rtr = false;   /* RRS: CAN FD has no remote frames */
        }
    } else if (!d->c_dlc_done) {
        uint16_t rel = (uint16_t)(nb - d->c_fdf_at);
        /* FD: FDF res BRS ESI DLC; classic base: r0 (at the FDF place) DLC; classic extended: r1 r0 DLC */
        uint16_t dlc_at = r->fd ? 4u : (r->ext ? 2u : 1u);
        if (r->fd && rel == 2u) {
            r->brs = bit != 0u;
            if (r->brs) {
                d->spb = d->spb_data;   /* the data phase starts at this bit's sample point */
            }
        } else if (r->fd && rel == 3u) {
            r->esi = bit != 0u;
        } else if (rel >= dlc_at) {
            r->dlc = (uint8_t)((r->dlc << 1) | bit);
            if (rel == dlc_at + 3u) {
                d->c_dlc_done = true;
                r->len = r->rtr ? 0u : dec_can_dlc_len(r->dlc, r->fd);
                d->c_data_at = (uint16_t)(nb + 1u);
                d->c_data_end = (uint16_t)(d->c_data_at + 8u * r->len);
            }
        }
    } else if (nb < d->c_data_end) {
        uint16_t k = (uint16_t)((nb - d->c_data_at) / 8u);
        r->data[k] = (uint8_t)((r->data[k] << 1) | bit);
    }
    if (d->c_dlc_done && d->c_nb == d->c_data_end) {   /* the data field is complete */
        if (r->fd) {
            d->c_crc_len = r->len <= 16u ? 17u : 21u;
            d->c_crc_calc = d->c_crc_len == 17u ? d->crc17 : d->crc21;
            d->state = ST_CAN_FIXED;
            d->c_fix_expect = true;
            d->c_fix_nb = 0u;
            d->c_sc_rx = 0u;
            d->c_crc_rx = 0u;
        } else {
            d->c_crc_calc = d->crc15;
            d->c_crc_stage = true;
            d->c_crc_nb = 0u;
            d->c_crc_rx = 0u;
        }
    }
}

static void can_start(dec_t *d) {
    memset(&d->rec, 0, sizeof d->rec);
    d->rec.t_sample = (uint64_t)(d->bit_pos + 0.5);
    d->c_nb = 0u;
    d->c_prev = 0u;
    d->c_same = 1u;
    d->c_stuff = 0u;
    d->c_dlc_done = d->c_crc_stage = d->c_skip_stuff = false;
    d->c_r12 = 0u;
    d->crc15 = 0u;
    d->crc17 = dec_crc17_bit(1u << 16, 0u);   /* the SOF bit, on the ISO start value */
    d->crc21 = dec_crc21_bit(1u << 20, 0u);
    d->spb = d->spb_arb;
    d->state = ST_CAN_FRAME;
}

static void can_bit(dec_t *d, uint8_t bit, dec_emit_fn emit, void *ctx) {
    switch (d->state) {
    case ST_IDLE:
        if (!bit) {
            can_start(d);
            can_field(d, 0u, emit, ctx);
        }
        break;
    case ST_CAN_FRAME:
        if (d->c_same == 5u) {   /* a dynamic stuff bit: the opposite of the five before */
            if (bit == d->c_prev) {
                can_fail(d, DEC_ERR_STUFF, emit, ctx);
                return;
            }
            ++d->c_stuff;
            d->c_prev = bit;
            d->c_same = 1u;
            if (!d->c_crc_stage) {
                d->crc17 = dec_crc17_bit(d->crc17, bit);   /* the FD CRC covers the stuff bits too */
                d->crc21 = dec_crc21_bit(d->crc21, bit);
            }
            return;
        }
        d->c_same = (uint8_t)(bit == d->c_prev ? d->c_same + 1u : 1u);
        d->c_prev = bit;
        if (!d->c_crc_stage) {
            d->crc17 = dec_crc17_bit(d->crc17, bit);
            d->crc21 = dec_crc21_bit(d->crc21, bit);
        }
        can_field(d, bit, emit, ctx);
        break;
    case ST_CAN_FIXED:
        if (d->c_fix_expect) {   /* fixed stuff bit: the opposite of the bit before, not in the CRC */
            if (bit == d->c_prev) {
                can_fail(d, DEC_ERR_STUFF, emit, ctx);
                return;
            }
            d->c_prev = bit;
            d->c_fix_expect = false;
            return;
        }
        d->c_prev = bit;
        if (d->c_fix_nb < 4u) {
            d->c_sc_rx = (d->c_sc_rx << 1) | bit;
            d->c_crc_calc = d->c_crc_len == 17u ? dec_crc17_bit(d->c_crc_calc, bit) : dec_crc21_bit(d->c_crc_calc, bit);
        } else {
            d->c_crc_rx = (d->c_crc_rx << 1) | bit;
        }
        ++d->c_fix_nb;
        if (d->c_fix_nb == 4u + d->c_crc_len) {
            can_finish(d, emit, ctx);
        } else if ((d->c_fix_nb & 3u) == 0u) {
            d->c_fix_expect = true;
        }
        break;
    case ST_CAN_AFTER:
        if (d->c_skip_stuff) {
            d->c_skip_stuff = false;
        } else {
            if (d->c_after == 0u) {
                d->spb = d->spb_arb;   /* CRC delimiter */
            } else if (!bit && d->c_after < 3u) {
                d->rec.ack = true;     /* the ACK slot */
            }
            ++d->c_after;
        }
        break;
    default:
        break;
    }
}

/* ---- runs ---------------------------------------------------------------------------------------- */

static void bus_idle(dec_t *d, dec_emit_fn emit, void *ctx) {
    if (d->proto == DEC_PROTO_UART) {
        if (d->state == ST_UART_DATA || d->state == ST_UART_STOP) {
            ++d->rec.framing_errors;   /* a byte cut short */
        }
        if (d->rec.len) {
            emit_rec(d, emit, ctx);
        }
    } else if (d->state == ST_CAN_AFTER) {
        emit_rec(d, emit, ctx);
    } else if (d->state == ST_CAN_FRAME || d->state == ST_CAN_FIXED) {
        can_fail(d, DEC_ERR_FORM, emit, ctx);
    }
    d->spb = d->spb_arb;
    d->state = ST_IDLE;
}

static void feed_bits(dec_t *d, uint8_t bit, uint32_t len, uint32_t max_bits, dec_emit_fn emit, void *ctx) {
    double rem = (double)len;
    double p = (double)d->run_start;
    for (uint32_t k = 0u; k < max_bits && rem > 0.5 * (double)d->spb; ++k) {
        float L = d->spb;
        d->bit_pos = p;
        if (d->proto == DEC_PROTO_UART) {
            uart_bit(d, bit, emit, ctx);
        } else {
            can_bit(d, bit, emit, ctx);
        }
        if (d->spb != L) {   /* the bit rate changed at this bit's sample point */
            L = DEC_SAMPLE_POINT * L + (1.0f - DEC_SAMPLE_POINT) * d->spb;
        }
        rem -= (double)L;
        p += (double)L;
        if (d->state == ST_WAIT_IDLE) {
            break;
        }
    }
}

/* a run of len samples at level, starting at d->run_start */
static void handle_run(dec_t *d, uint8_t level, uint32_t len, dec_emit_fn emit, void *ctx) {
    bool long_run = len >= d->idle_samples;
    if (long_run) {
        if (d->proto == DEC_PROTO_CAN && d->idle_level < 0) {
            d->idle_level = (int8_t)level;
        } else if (d->proto == DEC_PROTO_UART && len >= 2u * d->idle_samples) {
            d->idle_level = (int8_t)level;   /* a long rest shows which way the line idles */
        }
    }
    if (d->idle_level < 0) {
        return;
    }
    uint8_t bit = level == (uint8_t)d->idle_level ? 1u : 0u;
    if (long_run && bit) {
        if (d->state != ST_WAIT_IDLE && d->state != ST_IDLE) {
            feed_bits(d, 1u, len, 12u, emit, ctx);   /* stop bit, CRC delimiter, ACK delimiter */
        }
        bus_idle(d, emit, ctx);
        return;
    }
    if (d->state == ST_WAIT_IDLE) {
        return;
    }
    feed_bits(d, bit, len, 0xFFFFu, emit, ctx);
}

void dec_push_codes(dec_t *d, const uint8_t *codes, size_t n, dec_emit_fn emit, void *ctx) {
    if (!d || !codes || !d->fs) {
        return;
    }
    while (n > 0u) {
        size_t m = n > CHUNK ? CHUNK : n;
        uint8_t lo = 255u, hi = 0u;
        for (size_t i = 0u; i < m; ++i) {
            lo = codes[i] < lo ? codes[i] : lo;
            hi = codes[i] > hi ? codes[i] : hi;
        }
        if (d->env_hi < 0) {
            d->env_hi = (int32_t)hi << 8;
            d->env_lo = (int32_t)lo << 8;
        } else {
            int32_t leak = (int32_t)(((int64_t)d->leak * (int64_t)m) / (int64_t)CHUNK);
            int32_t h = d->env_hi - leak, l = d->env_lo + leak;
            d->env_hi = ((int32_t)hi << 8) > h ? ((int32_t)hi << 8) : h;
            d->env_lo = ((int32_t)lo << 8) < l ? ((int32_t)lo << 8) : l;
        }
        int32_t span = (d->env_hi - d->env_lo) >> 8;
        if (span >= MIN_SPAN_CODES) {   /* else keep the last thresholds (a quiet bus) */
            int32_t mid = (d->env_hi + d->env_lo) >> 9;
            int32_t hy = span / 8 > 1 ? span / 8 : 1;
            d->th_hi = (uint8_t)(mid + hy > 255 ? 255 : mid + hy);
            d->th_lo = (uint8_t)(mid - hy < 0 ? 0 : mid - hy);
            if (!d->levels_ok) {   /* the line stayed within a few codes until now: one run since the start */
                d->levels_ok = true;
                d->level = codes[0] > mid ? 1u : 0u;
                d->run_flushed = false;
            }
        }
        if (d->levels_ok) {
            uint8_t level = d->level, th_hi = d->th_hi, th_lo = d->th_lo;
            for (size_t i = 0u; i < m; ++i) {
                uint8_t s = codes[i];
                if (level ? (s < th_lo) : (s > th_hi)) {
                    uint64_t at = d->pos + i;
                    if (!d->run_flushed) {
                        handle_run(d, level, (uint32_t)(at - d->run_start), emit, ctx);
                    }
                    level ^= 1u;
                    d->run_start = at;
                    d->run_flushed = false;
                }
            }
            d->level = level;
        }
        d->pos += m;
        if (d->levels_ok && !d->run_flushed && d->pos - d->run_start >= d->idle_samples) {
            handle_run(d, d->level, (uint32_t)(d->pos - d->run_start), emit, ctx);   /* a long run: bus idle */
            d->run_flushed = true;
        }
        codes += m;
        n -= m;
    }
}

static uint8_t rbit8(uint8_t b) {
    b = (uint8_t)((b & 0xF0u) >> 4 | (b & 0x0Fu) << 4);
    b = (uint8_t)((b & 0xCCu) >> 2 | (b & 0x33u) << 2);
    b = (uint8_t)((b & 0xAAu) >> 1 | (b & 0x55u) << 1);
    return b;
}

void dec_push_words(dec_t *d, const uint32_t *words, size_t n, dec_emit_fn emit, void *ctx) {
    static uint8_t lut_a[256];   /* channel A: bit-reversed on the pins, inverted by the front end */
    static bool lut_ready = false;
    uint8_t buf[CHUNK];
    if (!d || !words) {
        return;
    }
    if (!lut_ready) {
        for (unsigned i = 0u; i < 256u; ++i) {
            lut_a[i] = (uint8_t)(255u - rbit8((uint8_t)i));
        }
        lut_ready = true;
    }
    while (n > 0u) {
        size_t m = n > CHUNK ? CHUNK : n;
        for (size_t i = 0u; i < m; ++i) {
            uint32_t w = words[i];
            uint8_t a = lut_a[w & 0xFFu];
            uint8_t b = (uint8_t)(255u - ((w >> 16) & 0xFFu));
            if (d->src == DEC_SRC_CH1) {
                buf[i] = a;
            } else if (d->src == DEC_SRC_CH2) {
                buf[i] = b;
            } else {
                int v = 128 + (int)a - (int)b;
                buf[i] = (uint8_t)(v < 0 ? 0 : (v > 255 ? 255 : v));
            }
        }
        dec_push_codes(d, buf, m, emit, ctx);
        words += m;
        n -= m;
    }
}

void dec_skip(dec_t *d, uint64_t n, bool lost, dec_emit_fn emit, void *ctx) {
    if (!d) {
        return;
    }
    if (d->proto == DEC_PROTO_UART && d->rec.len) {
        emit_rec(d, emit, ctx);
    } else if (d->state == ST_CAN_AFTER) {
        emit_rec(d, emit, ctx);   /* complete; only the ACK slot may be unseen */
    }
    memset(&d->rec, 0, sizeof d->rec);
    d->pos += n;
    d->run_start = d->pos;
    d->run_flushed = false;
    d->spb = d->spb_arb;
    d->state = ST_WAIT_IDLE;
    if (lost && n) {
        ++d->lost_events;
        d->lost_samples += n;
    }
}

void dec_flush(dec_t *d, dec_emit_fn emit, void *ctx) {
    if (!d) {
        return;
    }
    if ((d->proto == DEC_PROTO_UART && d->rec.len) || d->state == ST_CAN_AFTER) {
        emit_rec(d, emit, ctx);
        if (d->proto == DEC_PROTO_CAN) {
            d->state = ST_IDLE;
        }
    }
}

/* ---- text ---------------------------------------------------------------------------------------- */

static const char *err_name(uint8_t e) {
    static const char *const N[] = {"NONE", "STUFF", "CRC", "SC", "FORM"};
    return e < sizeof N / sizeof N[0] ? N[e] : "?";
}

static size_t put_hex(char *out, size_t pos, size_t max_len, const uint8_t *data, uint8_t len) {
    static const char HX[] = "0123456789ABCDEF";
    for (uint8_t i = 0u; i < len && pos + 2u < max_len; ++i) {
        out[pos++] = HX[data[i] >> 4];
        out[pos++] = HX[data[i] & 15u];
    }
    out[pos < max_len ? pos : max_len - 1u] = '\0';
    return pos;
}

size_t dec_format_line(const dec_t *d, const dec_record_t *r, char *out, size_t max_len) {
    if (!d || !r || !out || max_len == 0u) {
        return 0u;
    }
    double t = d->fs > 0.0f ? (double)r->t_sample / (double)d->fs : 0.0;
    int w;
    if (r->proto == DEC_PROTO_UART) {
        w = snprintf(out, max_len, "DEC UART T=%.6f BYTES=%u FE=%u HEX=", t, (unsigned)r->len,
                     (unsigned)r->framing_errors);
    } else {
        w = snprintf(out, max_len, r->ext ? "DEC CAN T=%.6f ID=%08lX EXT=%d FD=%d BRS=%d ESI=%d RTR=%d DLC=%u LEN=%u ACK=%d ERR=%s HEX="
                                           : "DEC CAN T=%.6f ID=%03lX EXT=%d FD=%d BRS=%d ESI=%d RTR=%d DLC=%u LEN=%u ACK=%d ERR=%s HEX=",
                     t, (unsigned long)r->id, (int)r->ext, (int)r->fd, (int)r->brs, (int)r->esi, (int)r->rtr,
                     (unsigned)r->dlc, (unsigned)r->len, (int)r->ack, err_name(r->err));
    }
    if (w < 0) {
        out[0] = '\0';
        return 0u;
    }
    size_t pos = (size_t)w < max_len ? (size_t)w : max_len - 1u;
    pos = put_hex(out, pos, max_len, r->data, r->err == DEC_ERR_NONE || r->proto == DEC_PROTO_UART ? r->len : 0u);
    return pos < max_len ? pos : max_len - 1u;
}

size_t dec_format_text(const dec_record_t *r, char *out, size_t max_len) {
    if (!r || !out || max_len == 0u) {
        return 0u;
    }
    size_t pos = 0u;
    if (r->proto == DEC_PROTO_CAN) {
        int w = snprintf(out, max_len, r->ext ? "%08lX " : "%03lX ", (unsigned long)r->id);
        pos = w > 0 ? (size_t)w : 0u;
        if (r->err != DEC_ERR_NONE) {
            w = snprintf(out + pos, max_len - pos, "ERR %s\n", err_name(r->err));
            pos += w > 0 ? (size_t)w : 0u;
            return pos < max_len ? pos : max_len - 1u;
        }
        if (r->rtr) {
            w = snprintf(out + pos, max_len - pos, "RTR");
            pos += w > 0 ? (size_t)w : 0u;
        }
    }
    for (uint8_t i = 0u; i < r->len && pos + 1u < max_len; ++i) {
        uint8_t c = r->data[i];
        if (c == '\r') {
            continue;
        }
        out[pos++] = (c == '\n' || (c >= 0x20u && c < 0x7Fu)) ? (char)c : '.';
    }
    if (r->proto == DEC_PROTO_CAN && pos + 1u < max_len) {
        out[pos++] = '\n';
    }
    out[pos < max_len ? pos : max_len - 1u] = '\0';
    return pos;
}
