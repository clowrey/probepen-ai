#ifndef PROBEPEN_LCD_H
#define PROBEPEN_LCD_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/* Panel on the J4 pads (bottom side). Default: 1.14" 135x240 IPS (ST7789V), used in landscape.
 * Build with -DPROBEPEN_LCD_096 for the 0.96" 80x160 IPS (ST7735S) that fits the same pads.
 * The offsets place the visible area inside the controller's RAM; check them on the first board. */
#ifdef PROBEPEN_LCD_096
#define LCD_WIDTH    160u
#define LCD_HEIGHT   80u
#define LCD_X_OFFSET 1u
#define LCD_Y_OFFSET 26u
#else
#define LCD_WIDTH    240u
#define LCD_HEIGHT   135u
#define LCD_X_OFFSET 40u
#define LCD_Y_OFFSET 53u
#endif
#define LCD_PIXELS (LCD_WIDTH * LCD_HEIGHT)

#define LCD_GLYPH_W  6         /* 5x7 glyph plus one column of spacing */
#define LCD_GLYPH_H  8
#define LCD_MICRO    '\x7f'    /* the font draws a micro sign for code 0x7F */

typedef struct {
    uint8_t cmd;
    uint8_t len;
    uint8_t data[16];
    uint16_t delay_ms;
} lcd_init_cmd_t;

/* Byte-level access to the panel: the target wires these to SPI1 and the DC / CS pins,
 * host tests record the bytes. */
typedef struct {
    void (*write_cmd)(void *ctx, uint8_t cmd);
    void (*write_data)(void *ctx, const uint8_t *data, size_t len);
    void (*delay_ms)(void *ctx, uint32_t ms);
    void *ctx;
} lcd_bus_t;

uint16_t lcd_rgb565(uint8_t r, uint8_t g, uint8_t b);
const lcd_init_cmd_t *lcd_st7735_init_seq(size_t *out_len);
const lcd_init_cmd_t *lcd_st7789_init_seq(size_t *out_len);
const lcd_init_cmd_t *lcd_panel_init_seq(size_t *out_len);   /* the sequence for the panel built in */
void lcd_panel_init(const lcd_bus_t *bus);
/* Address window in framebuffer coordinates (inclusive), then RAMWR: the caller streams the pixels next. */
void lcd_panel_set_window(const lcd_bus_t *bus, uint16_t x0, uint16_t y0, uint16_t x1, uint16_t y1);

void lcd_fb_clear(uint16_t *fb, uint16_t color);
void lcd_fb_pixel(uint16_t *fb, int x, int y, uint16_t color);
void lcd_fb_vline(uint16_t *fb, int x, int y0, int y1, uint16_t color);
void lcd_fb_hline(uint16_t *fb, int x0, int x1, int y, uint16_t color);
void lcd_fb_rect(uint16_t *fb, int x, int y, int w, int h, uint16_t color);
void lcd_fb_draw_char(uint16_t *fb, int x, int y, char ch, uint16_t fg, uint16_t bg);
void lcd_fb_draw_text(uint16_t *fb, int x, int y, const char *str, uint16_t fg, uint16_t bg);
/* Scaled text with a transparent background; returns the x after the last glyph. */
int lcd_fb_draw_text_scaled(uint16_t *fb, int x, int y, const char *str, int scale, uint16_t fg);
int lcd_text_width(const char *str, int scale);

#ifdef __cplusplus
}
#endif

#endif /* PROBEPEN_LCD_H */
