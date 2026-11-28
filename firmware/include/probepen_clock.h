#ifndef PROBEPEN_CLOCK_H
#define PROBEPEN_CLOCK_H

/* The system clock the firmware is built for (CMake PROBEPEN_SYS_MHZ, or -DPROBEPEN_SYS_MHZ=...).
 *
 *   150 MHz  the RP2354B's rating: the ADC runs at up to 75 MS/s (two PIO cycles per sample).
 *   250 MHz  overclocked: the core at 250 MHz (core voltage raised to 1.20 V at boot) and the AD9288 at up
 *            to 125 MS/s, beyond its 80 MS/s rating. Decoding gets 5/3 the CPU time per second.
 *
 * Every rate follows from APP_SYS_HZ: ADC and pod rates are APP_ADC_MAX_HZ / an integer. The simulator is
 * built for both (sim/site/probepen.wasm and probepen-250.wasm). Neither has run on a board yet. */

#ifndef PROBEPEN_SYS_MHZ
#define PROBEPEN_SYS_MHZ 150
#endif

#define APP_SYS_HZ      ((float)PROBEPEN_SYS_MHZ * 1e6f)
#define APP_ADC_MAX_HZ  (APP_SYS_HZ / 2.0f)   /* two PIO cycles per sample at divider 1 */

#endif /* PROBEPEN_CLOCK_H */
