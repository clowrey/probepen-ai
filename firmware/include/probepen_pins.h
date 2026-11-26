#ifndef PROBEPEN_PINS_H
#define PROBEPEN_PINS_H

/* Exact 1:1 pin mapping with hw/design.py GPIO dictionary for RP2354B (QFN-80).
 * AD9288 Channel A (D0A..D7A -> GPIO0..7) and Channel B (D0B..D7B -> GPIO8..15)
 * sit in one contiguous 16-bit PIO0 window for simultaneous 75-100 MSPS DMA capture.
 */

#define PIN_ADCA_D0        0u
#define PIN_ADCA_D1        1u
#define PIN_ADCA_D2        2u
#define PIN_ADCA_D3        3u
#define PIN_ADCA_D4        4u
#define PIN_ADCA_D5        5u
#define PIN_ADCA_D6        6u
#define PIN_ADCA_D7        7u

#define PIN_ADCB_D0        8u
#define PIN_ADCB_D1        9u
#define PIN_ADCB_D2        10u
#define PIN_ADCB_D3        11u
#define PIN_ADCB_D4        12u
#define PIN_ADCB_D5        13u
#define PIN_ADCB_D6        14u
#define PIN_ADCB_D7        15u

#define PIN_ADC_CLK        16u
#define PIN_CH1_GAIN_LO    18u
#define PIN_CH2_GAIN_LO    19u
#define PIN_BTN_MODE_N     20u
#define PIN_BTN_UP_N       21u
#define PIN_BTN_DOWN_N     22u
#define PIN_BTN_ASK_AI_N   23u

#define PIN_LCD_DC         24u
#define PIN_LCD_CS_N       25u
#define PIN_LCD_SCK        26u
#define PIN_LCD_MOSI       27u
#define PIN_LCD_RST_N      28u
#define PIN_LCD_BLK_GATE_N 29u
#define PIN_ADC_S1         31u

#define PIN_ADC_S2         32u
#define PIN_POD_CH0        36u
#define PIN_POD_CH1        37u
#define PIN_POD_CH2        38u
#define PIN_POD_CH3        39u

#define PIN_CH1_DC_SENSE   40u
#define PIN_VBUS_SENSE     42u
#define PIN_CH2_DC_SENSE   43u

#endif /* PROBEPEN_PINS_H */
