#ifndef PROBEPEN_AFE_H
#define PROBEPEN_AFE_H

#include <stdbool.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

typedef enum {
    AFE_RANGE_1X  = 0,   /* high gain (CHx_GAIN_LO low): +-5 V */
    AFE_RANGE_10X = 1    /* low gain (CHx_GAIN_LO high): about +-26 V */
} afe_range_t;

typedef struct {
    float vref_v;          /* REF3030 reference, also the MCU ADC reference (3.000 V) */
    float mid_bias_1x_v;   /* REF_MID, the gain stage's pivot and the ADC's mid-scale (0.978 V) */
    float mid_bias_10x_v;  /* same node on the low-gain range */
    float gain_1x;         /* tip volts per ADC volt on the high-gain range (nominal 9.80) */
    float offset_1x_v;     /* zero offset (V) */
    float gain_10x;        /* tip volts per ADC volt on the low-gain range (nominal 53.6) */
    float offset_10x_v;    /* zero offset (V) */
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
