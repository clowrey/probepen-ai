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
#include "ad9288_capture.pio.h"
#include "pod_capture.pio.h"
#endif

#define SCOPE_SAMPS   4096u          /* one 32-bit PIO word per sample (both channels) */
#define SCOPE_RATE_HZ 40000000.0f    /* 150 MHz / (2 cycles x 1.875) */
#define SCOPE_CLKDIV  1.875f

static uint32_t g_pio_words[SCOPE_SAMPS];
static uint16_t g_ch_a[SCOPE_SAMPS];
static uint16_t g_ch_b[SCOPE_SAMPS];
static uint16_t g_lcd_fb[LCD_PIXELS];
static afe_cal_t g_cal;

static void hal_init_peripherals(void) {
#ifdef PICO_RP2350
    stdio_init_all();
    /* AD9288 S1/S2 = 1/1: both channels running, outputs aligned */
    gpio_init(PIN_ADC_S1);
    gpio_set_dir(PIN_ADC_S1, GPIO_OUT);
    gpio_put(PIN_ADC_S1, 1);
    gpio_init(PIN_ADC_S2);
    gpio_set_dir(PIN_ADC_S2, GPIO_OUT);
    gpio_put(PIN_ADC_S2, 1);

    /* Gain switches: high = low-gain (+/-26 V) range, the safe default */
    gpio_init(PIN_CH1_GAIN_LO);
    gpio_set_dir(PIN_CH1_GAIN_LO, GPIO_OUT);
    gpio_put(PIN_CH1_GAIN_LO, 1);
    gpio_init(PIN_CH2_GAIN_LO);
    gpio_set_dir(PIN_CH2_GAIN_LO, GPIO_OUT);
    gpio_put(PIN_CH2_GAIN_LO, 1);

    static const uint8_t buttons[] = {PIN_BTN_ASK_AI_N, PIN_BTN_MODE_N, PIN_BTN_UP_N, PIN_BTN_DOWN_N};
    for (unsigned i = 0; i < sizeof buttons; ++i) {
        gpio_init(buttons[i]);
        gpio_set_dir(buttons[i], GPIO_IN);
        gpio_pull_up(buttons[i]);
    }

    gpio_init(PIN_LCD_BLK_GATE_N);
    gpio_set_dir(PIN_LCD_BLK_GATE_N, GPIO_OUT);
    gpio_put(PIN_LCD_BLK_GATE_N, 0);

    adc_init();
    adc_gpio_init(PIN_CH1_DC_SENSE);
    adc_gpio_init(PIN_CH2_DC_SENSE);
    adc_gpio_init(PIN_VBUS_SENSE);

    spi_init(spi1, 24000000u);
    gpio_set_function(PIN_LCD_SCK, GPIO_FUNC_SPI);
    gpio_set_function(PIN_LCD_MOSI, GPIO_FUNC_SPI);
#else
    (void)PIN_LCD_SCK;
    (void)PIN_LCD_MOSI;
    (void)PIN_ADC_CLK;
    (void)PIN_ADC_S1;
    (void)PIN_ADC_S2;
    (void)PIN_POD_CH0;
    (void)PIN_LCD_CS_N;
    (void)PIN_LCD_DC;
    (void)PIN_LCD_RST_N;
    (void)PIN_LCD_BLK_GATE_N;
    (void)PIN_BTN_ASK_AI_N;
    (void)PIN_BTN_MODE_N;
    (void)PIN_CH1_GAIN_LO;
    (void)PIN_CH2_GAIN_LO;
    (void)PIN_CH1_DC_SENSE;
    (void)PIN_CH2_DC_SENSE;
    (void)PIN_VBUS_SENSE;
#endif
}

#ifdef PICO_RP2350
/* One block capture: PIO0 clocks the AD9288 and pushes one word per sample, DMA moves SCOPE_SAMPS words. */
static void scope_capture_block(void) {
    PIO pio = pio0;
    uint sm = 0;
    static int offset = -1;
    if (offset < 0) {
        offset = pio_add_program(pio, &ad9288_capture_program);
        ad9288_capture_program_init(pio, sm, (uint)offset, SCOPE_CLKDIV);
    }
    int ch = dma_claim_unused_channel(true);
    dma_channel_config c = dma_channel_get_default_config((uint)ch);
    channel_config_set_transfer_data_size(&c, DMA_SIZE_32);
    channel_config_set_read_increment(&c, false);
    channel_config_set_write_increment(&c, true);
    channel_config_set_dreq(&c, pio_get_dreq(pio, sm, false));
    pio_sm_clear_fifos(pio, sm);
    pio_sm_restart(pio, sm);
    dma_channel_configure((uint)ch, &c, g_pio_words, &pio->rxf[sm], SCOPE_SAMPS, true);
    pio_sm_set_enabled(pio, sm, true);
    dma_channel_wait_for_finish_blocking((uint)ch);
    pio_sm_set_enabled(pio, sm, false);
    dma_channel_unclaim((uint)ch);
}
#endif

int main(void) {
    hal_init_peripherals();
    afe_cal_init_default(&g_cal);
    lcd_fb_clear(g_lcd_fb, lcd_rgb565(12u, 16u, 24u));
    lcd_fb_draw_text(g_lcd_fb, 4, 4, "AD9288", lcd_rgb565(0u, 255u, 140u), lcd_rgb565(12u, 16u, 24u));

    afe_range_t range = AFE_RANGE_1X;
    scope_meas_t meas;
    uint8_t ymin[LCD_WIDTH];
    uint8_t ymax[LCD_WIDTH];

#ifdef PICO_RP2350
    scope_capture_block();
#endif
    size_t n_samp = scope_unpack_ad9288(g_pio_words, SCOPE_SAMPS, g_ch_a, g_ch_b, SCOPE_SAMPS);
    scope_measure_buffer(g_ch_a, n_samp, SCOPE_RATE_HZ, 1.0f / 256.0f, 128.0f, TRIG_RISING, &meas);
    scope_decimate_min_max(g_ch_a, n_samp, ymin, ymax, LCD_WIDTH, 60u);
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
