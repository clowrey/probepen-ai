#ifndef PROBEPEN_DEEP_H
#define PROBEPEN_DEEP_H

/* Deep record (USB command DEEP): one long capture at up to 75 MS/s into most of the RAM.
 *
 * Two PIO state machines run in lockstep: one drives ADC_CLK and reads channel A's 8 bits, the other reads
 * channel B's 8 bits on the same cycle. Each packs 4 samples per 32-bit word into its own region of the
 * buffer, so a sample costs 1 byte (the normal capture spends a 32-bit word per sample pair). With one
 * channel, that channel gets the whole buffer.
 *
 * After the capture the bytes are turned into codes that rise with the tip voltage, the trigger is looked
 * for, and the record goes out over USB in the stream block format (probepen_stream.h):
 *   reply   OK N=.. CH=.. RATE=.. DIV=.. SECONDS=.. LSB1=.. ZERO1=.. LSB2=.. ZERO2=..
 *   then    DEEP DATA N=.. TRIG=<index of the trigger sample, -1 if none>
 *           blocks (first-sample index 0 .. N-1)
 *           DEEP END
 * Plain C, no hardware: src/main.c runs the PIO and DMA, sim/sim.c simulates them. */

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#define DEEP_BYTES (384u * 1024u)   /* also holds the stream ring and the scope capture ring */

/* Largest record per channel for a channel mask (1, 2 or 3), a multiple of 4. */
uint32_t deep_max_samples(uint8_t chmask);
/* Samples per channel (want_n 0 = the most that fits, else rounded down to a multiple of 4) and the PIO
 * divider for want_hz (75 MS/s / an integer). False for a bad mask, rate or count. */
bool deep_plan(uint32_t want_n, uint8_t chmask, float want_hz, float adc_max_hz, uint32_t *n_out,
               uint32_t *clkdiv_out, float *rate_out);
/* Raw PIO bytes to codes in place: channel A's bus is bit-reversed on the pins; both are inverted by the
 * front end. */
void deep_decode(uint8_t *buf, size_t n, bool bit_reversed);
/* First crossing of level (with hysteresis) after start; -1 when there is none. */
long deep_find_trigger(const uint8_t *codes, size_t n, size_t start, uint8_t level, uint8_t hysteresis, bool falling);

#ifdef __cplusplus
}
#endif

#endif /* PROBEPEN_DEEP_H */
