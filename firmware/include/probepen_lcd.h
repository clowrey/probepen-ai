#ifndef PROBEPEN_LCD_H
#define PROBEPEN_LCD_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#define LCD_WIDTH  160u
#define LCD_HEIGHT 80u
#define LCD_PIXELS (LCD_WIDTH * LCD_HEIGHT)

typedef struct {
    uint8_t cmd;
    uint8_t len;
    uint8_t data[16];
    uint16_t delay_ms;
} lcd_init_cmd_t;

uint16_t lcd_rgb565(uint8_t r, uint8_t g, uint8_t b);
const lcd_init_cmd_t *lcd_st7735_init_seq(size_t *out_len);
void lcd_fb_clear(uint16_t *fb, uint16_t color);
void lcd_fb_pixel(uint16_t *fb, int x, int y, uint16_t color);
void lcd_fb_vline(uint16_t *fb, int x, int y0, int y1, uint16_t color);
void lcd_fb_rect(uint16_t *fb, int x, int y, int w, int h, uint16_t color);
void lcd_fb_draw_char(uint16_t *fb, int x, int y, char ch, uint16_t fg, uint16_t bg);
void lcd_fb_draw_text(uint16_t *fb, int x, int y, const char *str, uint16_t fg, uint16_t bg);

#ifdef __cplusplus
}
#endif

#endif /* PROBEPEN_LCD_H */
