#ifndef PROBEPEN_CALSTORE_H
#define PROBEPEN_CALSTORE_H

/* Calibration record kept in a 4 KB flash sector: magic, version, float count, the afe_cal_t floats of
 * CH1 then CH2, CRC-16. Version 1 records (one set for both channels) still load, into both channels.
 * Packing and checking are plain C (host-tested); src/main.c does the flash erase / program. */

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "probepen_afe.h"

#ifdef __cplusplus
extern "C" {
#endif

#define CALSTORE_MAGIC   0x50504341u   /* "ACPP" little-endian */
#define CALSTORE_VERSION 2u
#define CALSTORE_BYTES   256u          /* one flash page */

/* Writes a CALSTORE_BYTES record of both channels (rest padded with 0xFF); returns the bytes used. */
size_t calstore_pack(const afe_cal_t cal[2], uint8_t out[CALSTORE_BYTES]);
/* True and fills cal[0..1] only for a record with the right magic, version and CRC whose values are near
 * the design; erased flash returns false. */
bool calstore_unpack(const uint8_t in[CALSTORE_BYTES], afe_cal_t cal[2]);

#ifdef __cplusplus
}
#endif

#endif /* PROBEPEN_CALSTORE_H */
