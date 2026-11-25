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
    USB_CMD_CAPTURE = 8
} usb_cmd_kind_t;

typedef struct {
    usb_cmd_kind_t kind;
    int32_t arg_i;
    float arg_f;
    bool valid;
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
