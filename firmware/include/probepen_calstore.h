#ifndef PROBEPEN_CALSTORE_H
#define PROBEPEN_CALSTORE_H

/* Calibration record kept in the last 4 KB flash sector: magic, version, the afe_cal_t floats, CRC-16.
 * Packing and checking are plain C (host-tested); src/main.c does the flash erase / program. */

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "probepen_afe.h"

#ifdef __cplusplus
extern "C" {
#endif

#define CALSTORE_MAGIC   0x50504341u   /* "ACPP" little-endian */
#define CALSTORE_VERSION 1u
#define CALSTORE_BYTES   256u          /* one flash page */

/* Writes a CALSTORE_BYTES record (rest padded with 0xFF); returns the bytes used. */
size_t calstore_pack(const afe_cal_t *cal, uint8_t out[CALSTORE_BYTES]);
/* True and fills *cal only for a record with the right magic, version and CRC; erased flash returns false. */
bool calstore_unpack(const uint8_t in[CALSTORE_BYTES], afe_cal_t *cal);

#ifdef __cplusplus
}
#endif

#endif /* PROBEPEN_CALSTORE_H */
