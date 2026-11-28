#ifndef PROBEPEN_USB_H
#define PROBEPEN_USB_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

typedef enum {
    USB_CMD_UNKNOWN = 0,
    USB_CMD_IDN     = 1,
    USB_CMD_MODE    = 2,
    USB_CMD_GAIN    = 3,
    USB_CMD_THR     = 4,
    USB_CMD_CAL     = 5,
    USB_CMD_MEAS    = 6,
    USB_CMD_TRIG    = 7,
    USB_CMD_CAPTURE = 8,
    USB_CMD_TB      = 9,    /* TB n: timebase index */
    USB_CMD_RUN     = 10,   /* RUN 0|1: hold / run */
    USB_CMD_STREAM  = 11,   /* STREAM rate_hz [chmask]: continuous 8-bit samples; STREAM 0 stops */
    USB_CMD_DEEP    = 12,   /* DEEP n [chmask] [rate_hz]: one long record (n 0 = as long as fits, rate 0 = the fastest) */
    USB_CMD_DECODE  = 13    /* DECODE UART src baud | DECODE CAN src rate [data_rate] | DECODE 0 (stop);
                               src 1 = CH1, 2 = CH2, 3 = CH1 - CH2. arg_i = protocol (0 = stop), arg_i2 = src,
                               arg_f = rate, arg_f2 = data rate */
} usb_cmd_kind_t;

typedef struct {
    usb_cmd_kind_t kind;
    int32_t arg_i;
    float arg_f;
    bool valid;
    int32_t arg_i2;
    float arg_f2;
} usb_cmd_t;

uint16_t usb_crc16_ccitt(const uint8_t *data, size_t len);
usb_cmd_t usb_parse_command(const char *line);
size_t usb_encode_wave_frame(
    uint8_t seq,
    const uint16_t *samples,
    size_t count,
    uint8_t *out_buf,
    size_t max_len
);
bool usb_decode_wave_frame(
    const uint8_t *frame,
    size_t frame_len,
    uint8_t *out_seq,
    uint16_t *out_samples,
    size_t max_samples,
    size_t *out_count
);

#ifdef __cplusplus
}
#endif

#endif /* PROBEPEN_USB_H */
