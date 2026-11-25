#include "probepen_afe.h"
#include <math.h>

/* MS9280 10-bit nominal range: 1.000 Vp-p centered around 1.000 V (codes 0..1023 -> 0.5..1.5 V). */
#define ADC_COUNTS   1024.0f
#define ADC_MID_CODE 512.0f
#define ADC_SPAN_V   1.000f

void afe_cal_init_default(afe_cal_t *cal) {
    if (!cal) {
        return;
    }
    cal->vref_v = 3.000f;
    cal->mid_bias_1x_v = 1.000f;
    cal->mid_bias_10x_v = 1.000f;
    cal->gain_1x = 5.000f;    /* 1x divider: +-2.5 V input -> +-0.5 V at ADC */
    cal->offset_1x_v = 0.0f;
    cal->gain_10x = 50.000f;  /* 10x attenuator: +-25 V input -> +-0.5 V at ADC */
    cal->offset_10x_v = 0.0f;
}

float afe_code_to_volts(const afe_cal_t *cal, afe_range_t range, uint16_t adc_code) {
    if (!cal) {
        return 0.0f;
    }
    uint16_t clamped = (adc_code > 1023u) ? 1023u : adc_code;
    float v_adc_delta = ((float)clamped - ADC_MID_CODE) * (ADC_SPAN_V / ADC_COUNTS);
    if (range == AFE_RANGE_10X) {
        return v_adc_delta * cal->gain_10x + cal->offset_10x_v;
    }
    return v_adc_delta * cal->gain_1x + cal->offset_1x_v;
}

uint16_t afe_volts_to_code(const afe_cal_t *cal, afe_range_t range, float volts) {
    if (!cal) {
        return (uint16_t)ADC_MID_CODE;
    }
    float gain = (range == AFE_RANGE_10X) ? cal->gain_10x : cal->gain_1x;
    float offset = (range == AFE_RANGE_10X) ? cal->offset_10x_v : cal->offset_1x_v;
    if (fabsf(gain) < 1e-6f) {
        return (uint16_t)ADC_MID_CODE;
    }
    float v_adc_delta = (volts - offset) / gain;
    float fcode = ADC_MID_CODE + v_adc_delta * (ADC_COUNTS / ADC_SPAN_V);
    if (fcode <= 0.0f) {
        return 0u;
    }
    if (fcode >= 1023.0f) {
        return 1023u;
    }
    return (uint16_t)lroundf(fcode);
}

bool afe_calibrate_two_point(
    afe_cal_t *cal,
    afe_range_t range,
    uint16_t code_lo,
    float v_lo_known,
    uint16_t code_hi,
    float v_hi_known
) {
    if (!cal || code_hi <= code_lo) {
        return false;
    }
    float dv_adc = ((float)code_hi - (float)code_lo) * (ADC_SPAN_V / ADC_COUNTS);
    if (dv_adc < 1e-4f) {
        return false;
    }
    float gain = (v_hi_known - v_lo_known) / dv_adc;
    float v_adc_lo = ((float)code_lo - ADC_MID_CODE) * (ADC_SPAN_V / ADC_COUNTS);
    float offset = v_lo_known - gain * v_adc_lo;
    if (range == AFE_RANGE_10X) {
        cal->gain_10x = gain;
        cal->offset_10x_v = offset;
    } else {
        cal->gain_1x = gain;
        cal->offset_1x_v = offset;
    }
    return true;
}

float afe_dc_sense_to_volts(const afe_cal_t *cal, afe_range_t range, uint16_t rp_adc12) {
    if (!cal) {
        return 0.0f;
    }
    uint16_t clamped = (rp_adc12 > 4095u) ? 4095u : rp_adc12;
    float v_pin = ((float)clamped / 4096.0f) * cal->vref_v;
    if (range == AFE_RANGE_10X) {
        return (v_pin - cal->mid_bias_10x_v) * cal->gain_10x + cal->offset_10x_v;
    }
    return (v_pin - cal->mid_bias_1x_v) * cal->gain_1x + cal->offset_1x_v;
}

afe_range_t afe_autorange_select(afe_range_t current, float abs_volts, bool otr_flag) {
    if (otr_flag || abs_volts > 2.25f) {
        return AFE_RANGE_10X;
    }
    if (current == AFE_RANGE_10X && abs_volts < 1.80f) {
        return AFE_RANGE_1X;
    }
    return current;
}
