#include "probepen_lcd.h"

/* Compact 5x7 ASCII glyph rows for core UI digits, units, and labels. */
static void glyph_rows_5x7(char ch, uint8_t rows[7]) {
    for (int i = 0; i < 7; ++i) {
        rows[i] = 0u;
    }
    switch (ch) {
    case '0': rows[0]=0x0E; rows[1]=0x11; rows[2]=0x13; rows[3]=0x15; rows[4]=0x19; rows[5]=0x11; rows[6]=0x0E; break;
    case '1': rows[0]=0x04; rows[1]=0x0C; rows[2]=0x04; rows[3]=0x04; rows[4]=0x04; rows[5]=0x04; rows[6]=0x0E; break;
    case '2': rows[0]=0x0E; rows[1]=0x11; rows[2]=0x01; rows[3]=0x02; rows[4]=0x04; rows[5]=0x08; rows[6]=0x1F; break;
    case '3': rows[0]=0x1F; rows[1]=0x02; rows[2]=0x04; rows[3]=0x02; rows[4]=0x01; rows[5]=0x11; rows[6]=0x0E; break;
    case '4': rows[0]=0x02; rows[1]=0x06; rows[2]=0x0A; rows[3]=0x12; rows[4]=0x1F; rows[5]=0x02; rows[6]=0x02; break;
    case '5': rows[0]=0x1F; rows[1]=0x10; rows[2]=0x1E; rows[3]=0x01; rows[4]=0x01; rows[5]=0x11; rows[6]=0x0E; break;
    case '6': rows[0]=0x06; rows[1]=0x08; rows[2]=0x10; rows[3]=0x1E; rows[4]=0x11; rows[5]=0x11; rows[6]=0x0E; break;
    case '7': rows[0]=0x1F; rows[1]=0x01; rows[2]=0x02; rows[3]=0x04; rows[4]=0x08; rows[5]=0x08; rows[6]=0x08; break;
    case '8': rows[0]=0x0E; rows[1]=0x11; rows[2]=0x11; rows[3]=0x0E; rows[4]=0x11; rows[5]=0x11; rows[6]=0x0E; break;
    case '9': rows[0]=0x0E; rows[1]=0x11; rows[2]=0x11; rows[3]=0x0F; rows[4]=0x01; rows[5]=0x02; rows[6]=0x0C; break;
    case 'V': rows[0]=0x11; rows[1]=0x11; rows[2]=0x11; rows[3]=0x11; rows[4]=0x11; rows[5]=0x0A; rows[6]=0x04; break;
    case 'A': rows[0]=0x0E; rows[1]=0x11; rows[2]=0x11; rows[3]=0x1F; rows[4]=0x11; rows[5]=0x11; rows[6]=0x11; break;
    case 'H': rows[0]=0x11; rows[1]=0x11; rows[2]=0x11; rows[3]=0x1F; rows[4]=0x11; rows[5]=0x11; rows[6]=0x11; break;
    case 'L': rows[0]=0x10; rows[1]=0x10; rows[2]=0x10; rows[3]=0x10; rows[4]=0x10; rows[5]=0x10; rows[6]=0x1F; break;
    case 'P': rows[0]=0x1E; rows[1]=0x11; rows[2]=0x11; rows[3]=0x1E; rows[4]=0x10; rows[5]=0x10; rows[6]=0x10; break;
    case 'S': rows[0]=0x0F; rows[1]=0x10; rows[2]=0x10; rows[3]=0x0E; rows[4]=0x01; rows[5]=0x01; rows[6]=0x1E; break;
    case 'D': rows[0]=0x1E; rows[1]=0x11; rows[2]=0x11; rows[3]=0x11; rows[4]=0x11; rows[5]=0x11; rows[6]=0x1E; break;
    case 'C': rows[0]=0x0E; rows[1]=0x11; rows[2]=0x10; rows[3]=0x10; rows[4]=0x10; rows[5]=0x11; rows[6]=0x0E; break;
    case '.': rows[5]=0x0C; rows[6]=0x0C; break;
    case '-': rows[3]=0x1F; break;
    case '+': rows[1]=0x04; rows[2]=0x04; rows[3]=0x1F; rows[4]=0x04; rows[5]=0x04; break;
    case ' ': break;
    default:  rows[0]=0x1F; rows[3]=0x1F; rows[6]=0x1F; break;
    }
}

static const lcd_init_cmd_t ST7735_INIT_SEQ[] = {
    {0x01u, 0u, {0u}, 120u},                              /* SWRESET */
    {0x11u, 0u, {0u}, 120u},                              /* SLPOUT */
    {0x3Au, 1u, {0x05u}, 0u},                             /* COLMOD 16-bit RGB565 */
    {0x36u, 1u, {0x78u}, 0u},                             /* MADCTL landscape BGR for FP-096H09A */
    {0x21u, 0u, {0u}, 0u},                                /* INVON IPS polarity */
    {0x2Au, 4u, {0x00u, 0x01u, 0x00u, 0xA0u}, 0u},        /* CASET 1..160 */
    {0x2Bu, 4u, {0x00u, 0x1Au, 0x00u, 0x69u}, 0u},        /* RASET 26..105 (80 px) */
    {0x29u, 0u, {0u}, 20u},                               /* DISPON */
};

uint16_t lcd_rgb565(uint8_t r, uint8_t g, uint8_t b) {
    uint16_t r5 = (uint16_t)((r >> 3u) & 0x1Fu);
    uint16_t g6 = (uint16_t)((g >> 2u) & 0x3Fu);
    uint16_t b5 = (uint16_t)((b >> 3u) & 0x1Fu);
    return (uint16_t)((r5 << 11u) | (g6 << 5u) | b5);
}

const lcd_init_cmd_t *lcd_st7735_init_seq(size_t *out_len) {
    if (out_len) {
        *out_len = sizeof(ST7735_INIT_SEQ) / sizeof(ST7735_INIT_SEQ[0]);
    }
    return ST7735_INIT_SEQ;
}

void lcd_fb_clear(uint16_t *fb, uint16_t color) {
    if (!fb) {
        return;
    }
    for (size_t i = 0u; i < LCD_PIXELS; ++i) {
        fb[i] = color;
    }
}

void lcd_fb_pixel(uint16_t *fb, int x, int y, uint16_t color) {
    if (!fb || x < 0 || y < 0 || x >= (int)LCD_WIDTH || y >= (int)LCD_HEIGHT) {
        return;
    }
    fb[(size_t)y * LCD_WIDTH + (size_t)x] = color;
}

void lcd_fb_vline(uint16_t *fb, int x, int y0, int y1, uint16_t color) {
    if (!fb || x < 0 || x >= (int)LCD_WIDTH) {
        return;
    }
    if (y0 > y1) {
        int tmp = y0;
        y0 = y1;
        y1 = tmp;
    }
    if (y1 < 0 || y0 >= (int)LCD_HEIGHT) {
        return;
    }
    if (y0 < 0) {
        y0 = 0;
    }
    if (y1 >= (int)LCD_HEIGHT) {
        y1 = (int)LCD_HEIGHT - 1;
    }
    for (int y = y0; y <= y1; ++y) {
        fb[(size_t)y * LCD_WIDTH + (size_t)x] = color;
    }
}

void lcd_fb_rect(uint16_t *fb, int x, int y, int w, int h, uint16_t color) {
    if (!fb || w <= 0 || h <= 0) {
        return;
    }
    for (int col = x; col < x + w; ++col) {
        lcd_fb_vline(fb, col, y, y + h - 1, color);
    }
}

void lcd_fb_draw_char(uint16_t *fb, int x, int y, char ch, uint16_t fg, uint16_t bg) {
    if (!fb) {
        return;
    }
    uint8_t rows[7];
    glyph_rows_5x7(ch, rows);
    for (int r = 0; r < 7; ++r) {
        for (int c = 0; c < 5; ++c) {
            bool on = ((rows[r] >> (4 - c)) & 1u) != 0u;
            lcd_fb_pixel(fb, x + c, y + r, on ? fg : bg);
        }
    }
}

void lcd_fb_draw_text(uint16_t *fb, int x, int y, const char *str, uint16_t fg, uint16_t bg) {
    if (!fb || !str) {
        return;
    }
    int cx = x;
    while (*str != '\0') {
        lcd_fb_draw_char(fb, cx, y, *str, fg, bg);
        cx += 6;
        ++str;
    }
}
