"""Host client for the ProbePen-AI USB serial port.

    python host/probepen.py PORT idn
    python host/probepen.py PORT meas
    python host/probepen.py PORT capture [N]      # prints CH1 and CH2 sample codes as CSV
    python host/probepen.py PORT ask [DIR]        # waits for ASK button presses; saves each snapshot as JSON in DIR
    python host/probepen.py PORT send "TB 5"      # any command line

PORT is e.g. /dev/ttyACM0 or COM7. Uses pyserial when it is installed; on Linux it also works without it.

Protocol (firmware/src/app.c, usb_proto.c): one ASCII command per line, one reply line. After
`CAPTURE n` (reply `OK n`) and after an `ASK ...` line, the device sends one binary frame per channel:
AA 55 seq count_lo count_hi samples (uint16 LE) crc_lo crc_hi, CRC-16/CCITT (0xFFFF start) over
everything before the CRC; seq 0 is CH1, seq 1 is CH2. Sample codes rise with the tip voltage.
"""
from __future__ import annotations

import os
import sys
import time


def crc16_ccitt(data: bytes) -> int:
    crc = 0xFFFF
    for b in data:
        crc ^= b << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return crc


def decode_frame(buf: bytes) -> tuple[int, list[int]]:
    """One complete frame -> (seq, samples). Raises ValueError on a bad header, length or CRC."""
    if len(buf) < 7 or buf[0] != 0xAA or buf[1] != 0x55:
        raise ValueError("bad frame header")
    count = buf[3] | (buf[4] << 8)
    if len(buf) != 7 + 2 * count:
        raise ValueError("bad frame length")
    if crc16_ccitt(buf[:5 + 2 * count]) != buf[5 + 2 * count] | (buf[6 + 2 * count] << 8):
        raise ValueError("bad frame CRC")
    return buf[2], [buf[5 + 2 * i] | (buf[6 + 2 * i] << 8) for i in range(count)]


def parse_meas(line: str) -> dict:
    """'CH1 VPP=1 ...;CH2 ...;FS=..;TB=..;TRIG=YES' -> {'CH1': {'VPP': 1.0, ...}, 'FS': ..., ...}"""
    out: dict = {}
    for part in line.strip().split(";"):
        words = part.split()
        if not words:
            continue
        if "=" in words[0]:
            k, v = words[0].split("=", 1)
            out[k] = _num(v)
            continue
        out[words[0]] = {k: _num(v) for k, v in (w.split("=", 1) for w in words[1:] if "=" in w)}
    return out


def _num(v: str):
    try:
        return float(v)
    except ValueError:
        return v


class _PosixPort:
    def __init__(self, path: str):
        import termios
        import tty
        self.fd = os.open(path, os.O_RDWR | os.O_NOCTTY)
        tty.setraw(self.fd)
        attrs = termios.tcgetattr(self.fd)
        attrs[6][termios.VMIN] = 0
        attrs[6][termios.VTIME] = 1    # 100 ms read timeout
        termios.tcsetattr(self.fd, termios.TCSANOW, attrs)

    def write(self, data: bytes) -> None:
        os.write(self.fd, data)

    def read(self, n: int) -> bytes:
        return os.read(self.fd, n)

    def close(self) -> None:
        os.close(self.fd)


def open_port(path: str):
    try:
        import serial  # pyserial
        return serial.Serial(path, 115200, timeout=0.1)
    except ImportError:
        if os.name != "posix":
            raise SystemExit("install pyserial to use a COM port")
        return _PosixPort(path)


class ProbePen:
    def __init__(self, port):
        self.port = port
        self.buf = b""

    def _fill(self, timeout: float) -> bool:
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            chunk = self.port.read(4096)
            if chunk:
                self.buf += chunk
                return True
        return False

    def readline(self, timeout: float = 2.0) -> str:
        end = time.monotonic() + timeout
        while b"\n" not in self.buf:
            if not self._fill(max(0.0, end - time.monotonic())):
                raise TimeoutError("no reply")
        line, self.buf = self.buf.split(b"\n", 1)
        return line.decode(errors="replace").strip()

    def read_frame(self, timeout: float = 5.0) -> tuple[int, list[int]]:
        end = time.monotonic() + timeout
        while True:
            start = self.buf.find(b"\xaa\x55")
            if start >= 0 and len(self.buf) >= start + 5:
                count = self.buf[start + 3] | (self.buf[start + 4] << 8)
                need = start + 7 + 2 * count
                if len(self.buf) >= need:
                    frame, self.buf = self.buf[start:need], self.buf[need:]
                    return decode_frame(frame)
            if not self._fill(max(0.0, end - time.monotonic())):
                raise TimeoutError("no frame")

    def command(self, line: str) -> str:
        self.port.write(line.encode() + b"\n")
        return self.readline()

    def capture(self, n: int = 512) -> tuple[list[int], list[int]]:
        reply = self.command(f"CAPTURE {n}")
        if not reply.startswith("OK"):
            raise RuntimeError(reply)
        frames = dict(self.read_frame() for _ in range(2))
        return frames[0], frames[1]

    def wait_ask(self):
        """Yields (measurements, ch1, ch2) each time the ASK button is held."""
        while True:
            try:
                line = self.readline(timeout=3600.0)
            except TimeoutError:
                continue
            if line.startswith("ASK "):
                meas = parse_meas(line[4:])
                frames = dict(self.read_frame() for _ in range(2))
                yield meas, frames[0], frames[1]


def snapshot(meas: dict, ch1: list[int], ch2: list[int]) -> dict:
    """A self-describing snapshot: measurements plus the screen's samples as codes and as volts."""
    fs = float(meas.get("FS", 0) or 0)
    out = {"measurements": meas, "sample_rate_hz": fs, "timebase_ns_per_div": meas.get("TB"), "channels": {}}
    for name, codes in (("CH1", ch1), ("CH2", ch2)):
        m = meas.get(name, {})
        # volts per code from the measured extremes (the device reports both in volts and the codes span them)
        lo, hi = (min(codes), max(codes)) if codes else (0, 0)
        vmin, vmax = m.get("VMIN", 0.0), m.get("VMAX", 0.0)
        scale = (vmax - vmin) / (hi - lo) if hi > lo else 0.0
        out["channels"][name] = {
            "range": m.get("RANGE"),
            "codes": codes,
            "volts": [round(vmin + (c - lo) * scale, 4) for c in codes] if scale else None,
        }
    return out


def save_snapshot(out_dir: str, meas: dict, ch1: list[int], ch2: list[int]) -> str:
    import json
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, time.strftime("ask-%Y%m%d-%H%M%S.json"))
    with open(path, "w", encoding="utf8") as f:
        json.dump(snapshot(meas, ch1, ch2), f, indent=1)
    return path


def main(argv: list[str]) -> None:
    if len(argv) < 3:
        raise SystemExit(__doc__)
    pen = ProbePen(open_port(argv[1]))
    cmd = argv[2].lower()
    if cmd == "idn":
        print(pen.command("*IDN?"))
    elif cmd == "meas":
        print(parse_meas(pen.command("MEAS?")))
    elif cmd == "capture":
        a, b = pen.capture(int(argv[3]) if len(argv) > 3 else 512)
        print("ch1,ch2")
        for x, y in zip(a, b):
            print(f"{x},{y}")
    elif cmd == "ask":
        out_dir = argv[3] if len(argv) > 3 else None
        for meas, a, b in pen.wait_ask():
            print(meas, f"{len(a)} samples per channel")
            if out_dir:
                print("saved", save_snapshot(out_dir, meas, a, b))
    elif cmd == "send":
        print(pen.command(" ".join(argv[3:])))
    else:
        raise SystemExit(__doc__)


if __name__ == "__main__":
    main(sys.argv)
