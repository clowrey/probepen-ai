#ifndef PROBEPEN_AFE_H
#define PROBEPEN_AFE_H

#include <stdbool.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

typedef enum {
    AFE_RANGE_1X  = 0,
    AFE_RANGE_10X = 1
} afe_range_t;

typedef struct {
    float vref_v;          /* REF3030 nominal reference (3.000 V) */
    float mid_bias_1x_v;   /* AFE mid-scale bias in 1x mode (REF_MID, 0.978 V) */
    float mid_bias_10x_v;  /* AFE mid-scale bias in 10x mode (REF_MID, 0.978 V) */
    float gain_1x;         /* Calibrated 1x V/V factor (nominal 5.000 / 2.000 scale) */
    float offset_1x_v;     /* Calibrated 1x zero offset (V) */
    float gain_10x;        /* Calibrated 10x V/V factor (compensates 100 ppm divider) */
    float offset_10x_v;    /* Calibrated 10x zero offset (V) */
} afe_cal_t;

void afe_cal_init_default(afe_cal_t *cal);
float afe_code_to_volts(const afe_cal_t *cal, afe_range_t range, uint16_t adc_code);
uint16_t afe_volts_to_code(const afe_cal_t *cal, afe_range_t range, float volts);
bool afe_calibrate_two_point(
    afe_cal_t *cal,
    afe_range_t range,
    uint16_t code_lo,
    float v_lo_known,
    uint16_t code_hi,
    float v_hi_known
);
float afe_dc_sense_to_volts(const afe_cal_t *cal, afe_range_t range, uint16_t rp_adc12);
afe_range_t afe_autorange_select(afe_range_t current, float abs_volts, bool otr_flag);

#ifdef __cplusplus
}
#endif

#endif /* PROBEPEN_AFE_H */
