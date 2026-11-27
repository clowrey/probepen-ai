#include "probepen_usb.h"
#include <stdlib.h>

static char ascii_upper(char c) {
    if (c >= 'a' && c <= 'z') {
        return (char)(c - ('a' - 'A'));
    }
    return c;
}

static bool match_prefix_ci(const char *s, const char *prefix, const char **rest) {
    while (*prefix != '\0') {
        if (ascii_upper(*s) != *prefix) {
            return false;
        }
        ++s;
        ++prefix;
    }
    if (*s != '\0' && *s != ' ' && *s != '\t' && *s != '\r' && *s != '\n') {
        return false;
    }
    while (*s == ' ' || *s == '\t') {
        ++s;
    }
    if (rest) {
        *rest = s;
    }
    return true;
}

uint16_t usb_crc16_ccitt(const uint8_t *data, size_t len) {
    uint16_t crc = 0xFFFFu;
    if (!data) {
        return crc;
    }
    for (size_t i = 0u; i < len; ++i) {
        crc ^= (uint16_t)((uint16_t)data[i] << 8u);
        for (int b = 0; b < 8; ++b) {
            if ((crc & 0x8000u) != 0u) {
                crc = (uint16_t)((crc << 1u) ^ 0x1021u);
            } else {
                crc <<= 1u;
            }
        }
    }
    return crc;
}

usb_cmd_t usb_parse_command(const char *line) {
    usb_cmd_t cmd = {USB_CMD_UNKNOWN, 0, 0.0f, false};
    if (!line) {
        return cmd;
    }
    while (*line == ' ' || *line == '\t') {
        ++line;
    }
    const char *arg = NULL;
    if (match_prefix_ci(line, "*IDN?", &arg) || match_prefix_ci(line, "IDN?", &arg)) {
        cmd.kind = USB_CMD_IDN;
        cmd.valid = true;
    } else if (match_prefix_ci(line, "MODE", &arg) && *arg != '\0') {
        cmd.kind = USB_CMD_MODE;
        cmd.arg_i = (int32_t)strtol(arg, NULL, 10);
        cmd.valid = true;
    } else if (match_prefix_ci(line, "GAIN", &arg) && *arg != '\0') {
        cmd.kind = USB_CMD_GAIN;
        cmd.arg_i = (int32_t)strtol(arg, NULL, 10);
        cmd.valid = (cmd.arg_i == 0 || cmd.arg_i == 1 || cmd.arg_i == 10);   /* 0 = auto range */
    } else if (match_prefix_ci(line, "THR", &arg) && *arg != '\0') {
        cmd.kind = USB_CMD_THR;
        cmd.arg_f = strtof(arg, NULL);
        cmd.arg_i = (int32_t)cmd.arg_f;
        cmd.valid = (cmd.arg_f >= 0.0f && cmd.arg_f <= 3000.0f);
    } else if (match_prefix_ci(line, "CAL", &arg)) {
        cmd.kind = USB_CMD_CAL;
        cmd.valid = true;
    } else if (match_prefix_ci(line, "MEAS?", &arg)) {
        cmd.kind = USB_CMD_MEAS;
        cmd.valid = true;
    } else if (match_prefix_ci(line, "TRIG", &arg) && *arg != '\0') {
        cmd.kind = USB_CMD_TRIG;
        cmd.arg_i = (int32_t)strtol(arg, NULL, 10);
        cmd.valid = (cmd.arg_i >= 0 && cmd.arg_i <= 255);   /* AD9288 8-bit trigger code */
    } else if (match_prefix_ci(line, "CAPTURE", &arg)) {
        cmd.kind = USB_CMD_CAPTURE;
        cmd.arg_i = (*arg != '\0') ? (int32_t)strtol(arg, NULL, 10) : 256;
        cmd.valid = (cmd.arg_i > 0 && cmd.arg_i <= 4096);
    } else if (match_prefix_ci(line, "TB", &arg) && *arg != '\0') {
        cmd.kind = USB_CMD_TB;
        cmd.arg_i = (int32_t)strtol(arg, NULL, 10);
        cmd.valid = (cmd.arg_i >= 0);
    } else if (match_prefix_ci(line, "STREAM", &arg) && *arg != '\0') {
        char *end = NULL;
        cmd.kind = USB_CMD_STREAM;
        cmd.arg_f = strtof(arg, &end);              /* output samples per second per channel, 0 = stop */
        cmd.arg_i = 3;                              /* channel mask: both */
        if (end && *end != '\0') {
            cmd.arg_i = (int32_t)strtol(end, NULL, 10);
        }
        cmd.valid = end != arg && cmd.arg_f >= 0.0f && cmd.arg_i >= 1 && cmd.arg_i <= 3;
    } else if (match_prefix_ci(line, "RUN", &arg) && *arg != '\0') {
        cmd.kind = USB_CMD_RUN;
        cmd.arg_i = (int32_t)strtol(arg, NULL, 10);
        cmd.valid = (cmd.arg_i == 0 || cmd.arg_i == 1);
    }
    return cmd;
}

size_t usb_encode_wave_frame(
    uint8_t seq,
    const uint16_t *samples,
    size_t count,
    uint8_t *out_buf,
    size_t max_len
) {
    /* Layout: [0]=0xAA, [1]=0x55, [2]=seq, [3..4]=count LE, [5..]=samples LE16, [end-2..end-1]=CRC16 LE */
    size_t needed = 7u + count * 2u;
    if (!samples || !out_buf || max_len < needed || count > 65535u) {
        return 0u;
    }
    out_buf[0] = 0xAAu;
    out_buf[1] = 0x55u;
    out_buf[2] = seq;
    out_buf[3] = (uint8_t)(count & 0xFFu);
    out_buf[4] = (uint8_t)((count >> 8u) & 0xFFu);
    for (size_t i = 0u; i < count; ++i) {
        uint16_t s = samples[i] & 0x3FFu;
        out_buf[5u + i * 2u] = (uint8_t)(s & 0xFFu);
        out_buf[6u + i * 2u] = (uint8_t)((s >> 8u) & 0xFFu);
    }
    uint16_t crc = usb_crc16_ccitt(out_buf, 5u + count * 2u);
    out_buf[5u + count * 2u] = (uint8_t)(crc & 0xFFu);
    out_buf[6u + count * 2u] = (uint8_t)((crc >> 8u) & 0xFFu);
    return needed;
}

bool usb_decode_wave_frame(
    const uint8_t *frame,
    size_t frame_len,
    uint8_t *out_seq,
    uint16_t *out_samples,
    size_t max_samples,
    size_t *out_count
) {
    if (!frame || frame_len < 7u || frame[0] != 0xAAu || frame[1] != 0x55u) {
        return false;
    }
    size_t count = (size_t)frame[3] | ((size_t)frame[4] << 8u);
    if (frame_len != 7u + count * 2u || count > max_samples) {
        return false;
    }
    uint16_t expected = usb_crc16_ccitt(frame, 5u + count * 2u);
    uint16_t got = (uint16_t)frame[5u + count * 2u] | ((uint16_t)frame[6u + count * 2u] << 8u);
    if (expected != got) {
        return false;
    }
    if (out_seq) {
        *out_seq = frame[2];
    }
    if (out_count) {
        *out_count = count;
    }
    if (out_samples) {
        for (size_t i = 0u; i < count; ++i) {
            out_samples[i] = (uint16_t)frame[5u + i * 2u] | ((uint16_t)frame[6u + i * 2u] << 8u);
        }
    }
    return true;
}
