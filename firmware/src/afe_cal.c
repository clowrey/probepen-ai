#include "probepen_afe.h"
#include <math.h>

/* AD9288 8-bit: 1.024 Vp-p single-ended span around REF_MID (0.978 V), codes 0..255, mid-scale 128.
 * Codes here are already flipped back (scope_invert_codes): the gain stage is inverting.
 *
 * Tip volts per ADC volt, from hw/design.py: divider 940k into 100k (to +3V0_REF) || 51k (to AGND),
 * ratio 0.03468; gain stage Rf / Rg = 1.5k / 510R = 2.941 (high gain, GAIN_LO low), or
 * (1.5k || (330R + 6R switch)) / 510R = 0.538 (low gain, GAIN_LO high).
 *   1X  (high gain): 1 / (2.941 * 0.03468) =  9.80 -> +-5.0 V full scale
 *   10X (low gain):  1 / (0.538 * 0.03468) = 53.56 -> +-27.4 V at the ADC (the input buffer limits it to about +-26 V) */
#define ADC_COUNTS   256.0f
#define ADC_MID_CODE 128.0f
#define ADC_MAX_CODE 255u
#define ADC_SPAN_V   1.024f

#define FS_1X_V      (9.803f * 0.512f)

void afe_cal_init_default(afe_cal_t *cal) {
    if (!cal) {
        return;
    }
    cal->vref_v = 3.000f;
    cal->mid_bias_1x_v = 0.978f;
    cal->mid_bias_10x_v = 0.978f;
    cal->gain_1x = 9.803f;
    cal->offset_1x_v = 0.0f;
    cal->gain_10x = 53.56f;
    cal->offset_10x_v = 0.0f;
}

float afe_code_to_volts(const afe_cal_t *cal, afe_range_t range, uint16_t adc_code) {
    if (!cal) {
        return 0.0f;
    }
    uint16_t clamped = (adc_code > ADC_MAX_CODE) ? ADC_MAX_CODE : adc_code;
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
    if (fcode >= (float)ADC_MAX_CODE) {
        return ADC_MAX_CODE;
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
    /* CHx_DC_SENSE is the gain stage's output (through 10k): it falls as the tip voltage rises */
    if (range == AFE_RANGE_10X) {
        return (cal->mid_bias_10x_v - v_pin) * cal->gain_10x + cal->offset_10x_v;
    }
    return (cal->mid_bias_1x_v - v_pin) * cal->gain_1x + cal->offset_1x_v;
}

afe_range_t afe_autorange_select(afe_range_t current, float abs_volts, bool otr_flag) {
    /* up at 90 % of the +-5 V range, back down below 70 % so it does not toggle */
    if (otr_flag || abs_volts > 0.9f * FS_1X_V) {
        return AFE_RANGE_10X;
    }
    if (current == AFE_RANGE_10X && abs_volts < 0.7f * FS_1X_V) {
        return AFE_RANGE_1X;
    }
    return current;
}
