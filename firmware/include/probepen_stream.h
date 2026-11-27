#ifndef PROBEPEN_STREAM_H
#define PROBEPEN_STREAM_H

/* Continuous USB streaming of the scope channels (USB command STREAM).
 *
 * The AD9288 runs free at 75 MHz / clkdiv into a DMA ring. The main loop takes the raw PIO words as they
 * arrive, flips them to codes that rise with the tip voltage, averages `avg` of them per output sample
 * (slow rates: more resolution, less noise), and packs 8-bit samples into blocks:
 *
 *   A5 5A flags chmask len_lo len_hi idx0 idx1 idx2 idx3 payload[len] crc_lo crc_hi
 *
 *   flags   bit0: samples were lost just before this block (the USB host did not keep up)
 *           bit1 / bit2: CH1 / CH2 on the +-26 V range (else +-5 V)
 *   chmask  1 = CH1, 2 = CH2, 3 = both, interleaved CH1, CH2, CH1, CH2 ...
 *   idx     index of the block's first sample since the stream started (gaps show what was lost)
 *   crc     CRC-16/CCITT (0xFFFF start) over everything before it
 *
 * Plain C, no hardware: src/main.c feeds it from the DMA ring, sim/sim.c from simulated signals. */

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#define STREAM_MAGIC0        0xA5u
#define STREAM_MAGIC1        0x5Au
#define STREAM_HDR_BYTES     10u
#define STREAM_PAYLOAD_MAX   1024u
#define STREAM_BLOCK_MAX     (STREAM_HDR_BYTES + STREAM_PAYLOAD_MAX + 2u)
#define STREAM_CLKDIV_MAX    65535u     /* PIO integer divider: 75 MHz / 65535 = 1144 S/s raw, slowest */
#define STREAM_FLAG_LOST     0x01u
#define STREAM_FLAG_CH1_10X  0x02u
#define STREAM_FLAG_CH2_10X  0x04u

typedef void (*stream_emit_fn)(const uint8_t *block, size_t len, void *ctx);

typedef struct {
    /* plan */
    uint8_t chmask;          /* 1, 2 or 3 */
    uint32_t clkdiv;         /* PIO divider: raw rate = adc_max_hz / clkdiv */
    uint32_t avg;            /* raw samples per output sample */
    float raw_hz;
    float rate_hz;           /* output samples per second, per channel */
    uint8_t range_flags;     /* STREAM_FLAG_CH1_10X / _CH2_10X */
    /* state */
    uint32_t acc[2];
    uint32_t nacc;
    uint32_t out_index;      /* output samples produced or skipped since the start */
    uint32_t block_first;    /* out_index of the first sample in the block being filled */
    bool lost;               /* set by stream_skip_raw(), carried by the next block */
    uint8_t block[STREAM_BLOCK_MAX];
    size_t fill;             /* payload bytes in block */
    /* statistics */
    uint32_t blocks;
    uint32_t lost_events;
    uint32_t lost_samples;
    uint64_t bytes;
} stream_t;

/* Picks clkdiv and avg for want_hz output samples per second (per channel). False for want_hz <= 0,
 * above adc_max_hz, or a bad channel mask. */
bool stream_plan(stream_t *s, float want_hz, uint8_t chmask, float adc_max_hz, uint8_t range_flags);
/* Raw AD9288 PIO words in (channel A bit-reversed in bits 0..7, channel B in bits 16..23, both inverted
 * by the front end). Full blocks go to emit. */
void stream_push_words(stream_t *s, const uint32_t *words, size_t n, stream_emit_fn emit, void *ctx);
/* Codes already rising with the tip voltage (deep record): ch1 and/or ch2 as the plan's chmask says,
 * n samples each. The plan must have avg 1. */
void stream_push_codes(stream_t *s, const uint8_t *ch1, const uint8_t *ch2, size_t n, stream_emit_fn emit, void *ctx);
/* n raw words were dropped (DMA ring overrun): the partial block goes out first, and the next block
 * carries STREAM_FLAG_LOST and a jump in idx. */
void stream_skip_raw(stream_t *s, uint32_t n, stream_emit_fn emit, void *ctx);
/* Sends the partial block, if any (at stop). */
void stream_flush(stream_t *s, stream_emit_fn emit, void *ctx);
/* Payload bytes per second on USB for the plan (without the 12 bytes of framing per block). */
float stream_bytes_per_s(const stream_t *s);

#ifdef __cplusplus
}
#endif

#endif /* PROBEPEN_STREAM_H */
