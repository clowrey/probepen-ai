#include "probepen_pins.h"
#include "probepen_afe.h"
#include "probepen_scope.h"
#include "probepen_logic.h"
#include "probepen_lcd.h"
#include "probepen_usb.h"

#ifdef PICO_RP2350
#include "pico/stdlib.h"
#include "hardware/adc.h"
#include "hardware/dma.h"
#include "hardware/pio.h"
#include "hardware/pwm.h"
#include "hardware/spi.h"
#include "ms9280_capture.pio.h"
#include "pod_capture.pio.h"
#endif

#define SCOPE_WORDS 256u
#define SCOPE_SAMPS (SCOPE_WORDS * 3u)

static uint32_t g_pio_words[SCOPE_WORDS];
static uint16_t g_samples[SCOPE_SAMPS];
static uint16_t g_lcd_fb[LCD_PIXELS];
static afe_cal_t g_cal;

static void hal_init_peripherals(void) {
#ifdef PICO_RP2350
    stdio_init_all();
    gpio_init(PIN_ADC_STBY);
    gpio_set_dir(PIN_ADC_STBY, GPIO_OUT);
    gpio_put(PIN_ADC_STBY, 0);

    gpio_init(PIN_AFE_GAIN_10X);
    gpio_set_dir(PIN_AFE_GAIN_10X, GPIO_OUT);
    gpio_put(PIN_AFE_GAIN_10X, 0);

    gpio_init(PIN_BTN_ASK_AI_N);
    gpio_set_dir(PIN_BTN_ASK_AI_N, GPIO_IN);
    gpio_pull_up(PIN_BTN_ASK_AI_N);

    gpio_init(PIN_BTN_MODE_N);
    gpio_set_dir(PIN_BTN_MODE_N, GPIO_IN);
    gpio_pull_up(PIN_BTN_MODE_N);

    gpio_init(PIN_LCD_BLK_GATE_N);
    gpio_set_dir(PIN_LCD_BLK_GATE_N, GPIO_OUT);
    gpio_put(PIN_LCD_BLK_GATE_N, 0);

    adc_init();
    adc_gpio_init(PIN_AFE_DC_SENSE);
    adc_gpio_init(PIN_COMP_THR_ADC);
    adc_gpio_init(PIN_VBUS_SENSE);

    gpio_set_function(PIN_COMP_THR_PWM, GPIO_FUNC_PWM);
    uint slice = pwm_gpio_to_slice_num(PIN_COMP_THR_PWM);
    pwm_set_wrap(slice, 1000u);
    pwm_set_gpio_level(PIN_COMP_THR_PWM, logic_thr_mv_to_pwm_level(1400u, 1000u, 3300.0f));
    pwm_set_enabled(slice, true);

    spi_init(spi0, 24000000u);
    gpio_set_function(PIN_LCD_SCK, GPIO_FUNC_SPI);
    gpio_set_function(PIN_LCD_MOSI, GPIO_FUNC_SPI);
#else
    (void)PIN_ADC_CLK;
    (void)PIN_ADC_OTR;
    (void)PIN_ADC_STBY;
    (void)PIN_POD_CH0;
    (void)PIN_LCD_CS_N;
    (void)PIN_LCD_DC;
    (void)PIN_LCD_RST_N;
    (void)PIN_LCD_BLK_GATE_N;
    (void)PIN_BTN_ASK_AI_N;
    (void)PIN_BTN_MODE_N;
    (void)PIN_AFE_GAIN_10X;
    (void)PIN_AFE_DC_SENSE;
    (void)PIN_VBUS_SENSE;
#endif
}

int main(void) {
    hal_init_peripherals();
    afe_cal_init_default(&g_cal);
    lcd_fb_clear(g_lcd_fb, lcd_rgb565(12u, 16u, 24u));
    lcd_fb_draw_text(g_lcd_fb, 4, 4, "ADC 10B", lcd_rgb565(0u, 255u, 140u), lcd_rgb565(12u, 16u, 24u));

    afe_range_t range = AFE_RANGE_1X;
    scope_meas_t meas;
    uint8_t ymin[LCD_WIDTH];
    uint8_t ymax[LCD_WIDTH];

    size_t n_samp = scope_unpack_pio30(g_pio_words, SCOPE_WORDS, g_samples, SCOPE_SAMPS);
    scope_measure_buffer(g_samples, n_samp, 35000000.0f, 5.0f / 1024.0f, 512.0f, TRIG_RISING, &meas);
    scope_decimate_min_max(g_samples, n_samp, ymin, ymax, LCD_WIDTH, 60u);
    range = afe_autorange_select(range, meas.vmax, false);
    (void)range;
    (void)logic_family_default_mv(LOGIC_FAMILY_TTL);
    (void)usb_parse_command("*IDN?");

#ifdef PICO_RP2350
    for (;;) {
        tight_loop_contents();
    }
#endif
    return 0;
}
