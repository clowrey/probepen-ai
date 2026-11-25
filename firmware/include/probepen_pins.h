#ifndef PROBEPEN_PINS_H
#define PROBEPEN_PINS_H

/* Exact 1:1 pin mapping with hw/design.py GPIO dictionary for RP2354B (QFN-80). */

#define PIN_ADC_D0         0u
#define PIN_ADC_D1         1u
#define PIN_ADC_D2         2u
#define PIN_ADC_D3         3u
#define PIN_ADC_D4         4u
#define PIN_ADC_D5         5u
#define PIN_ADC_D6         6u
#define PIN_ADC_D7         7u
#define PIN_ADC_D8         8u
#define PIN_ADC_D9         9u
#define PIN_ADC_CLK        10u
#define PIN_ADC_OTR        11u
#define PIN_ADC_STBY       12u

#define PIN_COMP_OUT       16u
#define PIN_POD_CH0        17u
#define PIN_POD_CH1        18u
#define PIN_POD_CH2        19u
#define PIN_POD_CH3        20u

#define PIN_LCD_CS_N       21u
#define PIN_LCD_SCK        22u
#define PIN_LCD_MOSI       23u
#define PIN_LCD_DC         24u
#define PIN_LCD_RST_N      25u
#define PIN_LCD_BLK_GATE_N 26u

#define PIN_LED_DATA       28u
#define PIN_BTN_ASK_AI_N   29u
#define PIN_BTN_MODE_N     30u
#define PIN_AFE_GAIN_10X   31u
#define PIN_COMP_THR_PWM   32u

#define PIN_AFE_DC_SENSE   40u
#define PIN_COMP_THR_ADC   41u
#define PIN_VBUS_SENSE     42u

#endif /* PROBEPEN_PINS_H */
