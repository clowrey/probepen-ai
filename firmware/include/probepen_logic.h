#ifndef PROBEPEN_LOGIC_H
#define PROBEPEN_LOGIC_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

typedef enum {
    LOGIC_LOW    = 0,
    LOGIC_HIGH   = 1,
    LOGIC_FLOAT  = 2,
    LOGIC_PULSE  = 3
} logic_state_t;

typedef enum {
    LOGIC_FAMILY_TTL    = 0,  /* 1.40 V threshold */
    LOGIC_FAMILY_CMOS33 = 1,  /* 1.65 V threshold */
    LOGIC_FAMILY_CMOS18 = 2   /* 0.90 V threshold */
} logic_family_t;

uint16_t logic_thr_mv_to_pwm_level(uint32_t thr_mv, uint16_t pwm_wrap, float vdd_mv);
uint32_t logic_family_default_mv(logic_family_t family);
logic_state_t logic_classify_sample(bool comp_high, bool tip_float, uint32_t edge_count);
size_t logic_unpack_pod_nibbles(const uint32_t *words, size_t n_words, uint8_t *out_nibbles, size_t max_samples);
uint32_t logic_count_pod_edges(const uint8_t *nibbles, size_t count, uint8_t channel_idx);

#ifdef __cplusplus
}
#endif

#endif /* PROBEPEN_LOGIC_H */
