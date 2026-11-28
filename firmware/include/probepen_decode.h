#ifndef PROBEPEN_DECODE_H
#define PROBEPEN_DECODE_H

/* Bus decoding on the pen (USB command DECODE): UART and CAN / CAN FD from the scope channels.
 *
 * The ADC runs at 75 MHz / an integer, at least 8 samples per bit of the fastest phase (4 when that would
 * pass 18.75 MS/s). Each sample is compared with two thresholds that follow the signal's own high and low
 * levels, so any logic level works without settings. Only the edges cost real work: the time between two
 * edges is a run of equal bits, and its length in bit times gives the bits. Runs start at edges, so the
 * bit clock re-locks on every edge, as a UART or CAN receiver does.
 *
 *   source  CH1, CH2, or CH1 - CH2 (differential, as a CAN transceiver sees CAN_H - CAN_L)
 *   idle    the level of the first long run (10 bit times) is the bus idle level: high for a TTL UART, low
 *           for CAN_H and CH1 - CH2, high for CAN_L. Logical 1 = idle level (UART mark, CAN recessive).
 *   UART    8N1, LSB first. Bytes go out as one record per line ('\n'), per 64 bytes, or when the line
 *           goes idle; FE counts stop bits that were not at the idle level.
 *   CAN     classic CAN 2.0 and CAN FD, base and extended IDs: bit destuffing, BRS (data phase at the data
 *           rate, switched at the BRS bit's sample point), the stuff count, and the CRC (15, 17 or 21 bits)
 *           checked. One record per frame, after the bus goes idle (so the ACK slot is known).
 *
 * Live (sample rate up to DEC_LIVE_MAX_HZ): the ADC runs into the stream ring and every sample is decoded,
 * without gaps. Burst (faster): records of DEC_BURST_BYTES are taken at full rate, decoded, and taken again;
 * frames that fall between two bursts are missed, and the reply says what share of the time is covered.
 *
 * Plain C, no hardware: src/main.c feeds it from the DMA ring or the burst buffer, sim/sim.c from simulated
 * signals, tests/test_decode.py from synthetic ones. */

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "probepen_clock.h"

#ifdef __cplusplus
extern "C" {
#endif

#define DEC_PROTO_UART 1u
#define DEC_PROTO_CAN  2u
#define DEC_SRC_CH1    1u
#define DEC_SRC_CH2    2u
#define DEC_SRC_DIFF   3u             /* CH1 - CH2 */
#define DEC_MAX_BYTES  64u
#define DEC_LIVE_MAX_HZ (10e6f * APP_SYS_HZ / 150e6f)   /* estimate: 10 MS/s on one core at 150 MHz; to be measured */
#define DEC_BURST_BYTES (320u * 1024u)   /* below the LCD framebuffer, so the screen can be drawn between bursts */
#define DEC_CYCLES_PER_SAMPLE 10.0f   /* estimate, for the burst coverage figure */
#define DEC_SAMPLE_POINT 0.8f         /* CAN bit-rate switch point within the bit */
#define DEC_LINE_MAX   (128u + 2u * DEC_MAX_BYTES)   /* one USB line, with its HEX field */
#define DEC_TEXT_MAX   (16u + DEC_MAX_BYTES)

enum {
    DEC_ERR_NONE = 0,
    DEC_ERR_STUFF,      /* six equal bits, or a wrong fixed stuff bit (CAN FD) */
    DEC_ERR_CRC,
    DEC_ERR_STUFFCOUNT, /* CAN FD stuff count or its parity does not match */
    DEC_ERR_FORM,       /* the bus went idle inside a frame */
};

typedef struct {
    uint8_t proto;
    uint8_t err;            /* DEC_ERR_* */
    uint64_t t_sample;      /* first sample of the record (start bit, SOF) since the decode started */
    uint8_t len;
    uint8_t data[DEC_MAX_BYTES];
    uint16_t framing_errors;   /* UART */
    /* CAN */
    uint32_t id;
    bool ext, fd, brs, esi, rtr, ack;
    uint8_t dlc;
} dec_record_t;

typedef void (*dec_emit_fn)(const dec_record_t *rec, void *ctx);

typedef struct {
    /* plan */
    uint8_t proto, src;
    float rate;             /* UART baud, CAN arbitration bit rate */
    float data_rate;        /* CAN FD data phase (= rate when there is none) */
    uint32_t clkdiv;
    float fs;               /* samples per second */
    bool burst;
    float spb_arb, spb_data;
    uint32_t burst_n;       /* samples per burst (per channel) */
    float coverage;         /* share of the time decoded, 1 for live */
    /* levels: envelopes in 1/256 codes */
    int32_t env_hi, env_lo, leak;
    uint8_t th_hi, th_lo;
    bool levels_ok;
    uint8_t level;
    int8_t idle_level;      /* -1 until a long run shows it */
    /* runs */
    uint64_t pos;           /* samples seen (and skipped) since the start */
    uint64_t run_start;
    bool run_flushed;       /* this run was already handled as idle */
    bool run_from_edge;     /* this run began at an edge (not at the start or after a gap) */
    uint32_t idle_samples;
    /* bit engine */
    float spb;              /* samples per bit now */
    double bit_pos;         /* sample position of the bit being handed over */
    uint8_t state;
    /* UART */
    uint8_t u_nb, u_byte;
    /* CAN */
    uint16_t c_nb;          /* destuffed bits so far, SOF = 0 */
    uint8_t c_prev, c_same;
    uint16_t c_stuff;       /* dynamic stuff bits */
    uint16_t c_fdf_at;      /* index of the FDF / r0 bit */
    uint16_t c_data_at, c_data_end;
    uint8_t c_r12;          /* RTR (base) or SRR (extended) */
    bool c_dlc_done, c_crc_stage, c_fix_expect;
    uint8_t c_crc_len, c_crc_nb, c_fix_nb;
    uint32_t c_crc_rx, c_sc_rx;
    uint16_t crc15;
    uint32_t crc17, crc21, c_crc_calc;
    uint8_t c_after;        /* bits seen after the CRC (ACK search) */
    bool c_skip_stuff;      /* classic: one more stuff bit after the CRC */
    dec_record_t rec;
    /* statistics */
    uint32_t records, errors, lost_events;
    uint64_t lost_samples;
    uint32_t edges;
    uint32_t min_run;       /* shortest run seen (3 samples or more): one bit at the bus's fastest rate */
    bool listen;            /* only measure (edges, min_run): nothing is emitted (the pen's UART auto-baud) */
} dec_t;

/* The bit rate the edges show (sample rate / shortest run), 0 before any: when nothing decodes, this tells
 * whether the rate is set wrong. */
float dec_seen_rate(const dec_t *d);
/* Do the edges agree with the rate set? DEC_RATE_OK (or too few edges to say), DEC_RATE_TOO_FAST (runs of 4
 * samples or less: the bus is faster than this sample rate resolves), DEC_RATE_SLOWER / DEC_RATE_FASTER (the
 * shortest run is more than 15 % off one bit of the fastest phase). */
enum { DEC_RATE_OK = 0, DEC_RATE_TOO_FAST, DEC_RATE_SLOWER, DEC_RATE_FASTER };
int dec_rate_check(const dec_t *d);
/* The statistics (records, errors, losses, samples, edges, shortest run) from the running decoder into the
 * app's copy of the plan, for the screen; the plan itself is left alone (it may already be the next one). */
void dec_copy_stats(dec_t *dst, const dec_t *src);

/* Plans a decode: clkdiv, sample rate, live or burst. False for a bad protocol, source or rate. */
bool dec_plan(dec_t *d, uint8_t proto, uint8_t src, float rate, float data_rate, float adc_max_hz);
/* Back to the start of a planned decode (levels unknown, waiting for the bus to go idle). */
void dec_reset(dec_t *d);
/* Codes that rise with the tip voltage (or CH1 - CH2 + 128), in time order. */
void dec_push_codes(dec_t *d, const uint8_t *codes, size_t n, dec_emit_fn emit, void *ctx);
/* Raw AD9288 PIO words (as the stream ring holds them): the plan's source is taken out of each. */
void dec_push_words(dec_t *d, const uint32_t *words, size_t n, dec_emit_fn emit, void *ctx);
/* n samples were not seen (lost = a ring overrun, else the planned time between two bursts): the frame in
 * progress is dropped and decoding waits for the bus to go idle again. */
void dec_skip(dec_t *d, uint64_t n, bool lost, dec_emit_fn emit, void *ctx);
/* Sends the UART bytes still held (at stop). */
void dec_flush(dec_t *d, dec_emit_fn emit, void *ctx);

/* "DEC UART T=.. BYTES=.. FE=.. HEX=..." or "DEC CAN T=.. ID=.. EXT= FD= BRS= ESI= DLC= LEN= ACK= CRC=OK|ERR
 * [ERR=..] HEX=..". Returns the length (no newline). */
size_t dec_format_line(const dec_t *d, const dec_record_t *r, char *out, size_t max_len);
/* What the pen's screen shows: UART bytes as text ('.' for other bytes, \n kept), CAN "123 Hello..". */
size_t dec_format_text(const dec_record_t *r, char *out, size_t max_len);
/* Bytes for a CAN DLC (classic: at most 8). */
uint8_t dec_can_dlc_len(uint8_t dlc, bool fd);

/* CRC steps on one bit, exposed for the simulator's CAN frame generator and the tests. */
uint16_t dec_crc15_bit(uint16_t crc, uint8_t bit);
uint32_t dec_crc17_bit(uint32_t crc, uint8_t bit);
uint32_t dec_crc21_bit(uint32_t crc, uint8_t bit);
/* 3-bit Gray code of the stuff count (mod 8) and its even parity bit, as the 4 bits sent (MSB first). */
uint8_t dec_stuff_count_bits(uint16_t stuff_bits);

#ifdef __cplusplus
}
#endif

#endif /* PROBEPEN_DECODE_H */
