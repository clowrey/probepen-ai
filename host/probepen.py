"""Host client for the ProbePen-AI USB serial port.

    python host/probepen.py PORT idn
    python host/probepen.py PORT meas
    python host/probepen.py PORT capture [N]      # prints CH1 and CH2 sample codes as CSV
    python host/probepen.py PORT ask [DIR]        # waits for ASK button presses; saves each snapshot as JSON in DIR
    python host/probepen.py PORT send "TB 5"      # any command line
    python host/probepen.py PORT stream RATE [CH] [SECONDS] [OUT]
                                                  # continuous 8-bit samples: RATE per channel, CH 1, 2 or 3
                                                  # (both); prints the throughput and losses it measured and
                                                  # writes the samples to OUT (.csv volts, else raw .bin)

PORT is e.g. /dev/ttyACM0 or COM7. Uses pyserial when it is installed; on Linux it also works without it.

Protocol (firmware/src/app.c, usb_proto.c): one ASCII command per line, one reply line. After
`CAPTURE n` (reply `OK n`) and after an `ASK ...` line, the device sends one binary frame per channel:
AA 55 seq count_lo count_hi samples (uint16 LE) crc_lo crc_hi, CRC-16/CCITT (0xFFFF start) over
everything before the CRC; seq 0 is CH1, seq 1 is CH2. Sample codes rise with the tip voltage.
`STREAM rate [ch]` replies `OK RATE=.. RAW=.. DIV=.. AVG=.. CH=.. BPS=.. LSB1=.. ZERO1=.. LSB2=.. ZERO2=..`
and then sends blocks until `STREAM 0` (or a button), which ends with a `STREAM END ...` line:
A5 5A flags chmask len(u16) first_sample_index(u32) payload[len] crc(u16), 8-bit codes, CH1 and CH2
interleaved when both are on; flags bit0 = samples lost before this block (firmware/include/probepen_stream.h).
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


class StreamDecoder:
    """Splits the bytes of a running stream into text lines and checked sample blocks.

    feed() returns a list of ("line", text) and ("block", flags, chmask, first_index, payload) events.
    A block with a bad CRC is counted in crc_errors and the decoder resynchronises on the next A5 5A."""

    def __init__(self):
        self.buf = b""
        self.crc_errors = 0
        self.blocks = 0
        self.samples = 0
        self.lost_samples = 0
        self.lost_flags = 0
        self.next_index = None

    def feed(self, data: bytes) -> list:
        self.buf += data
        out = []
        while self.buf:
            if self.buf[0] == 0xA5:
                if len(self.buf) < 10:
                    break
                if self.buf[1] != 0x5A:
                    self.buf = self.buf[1:]
                    continue
                n = self.buf[4] | (self.buf[5] << 8)
                if n > 1024:
                    self.buf = self.buf[1:]
                    continue
                if len(self.buf) < 12 + n:
                    break
                blk, rest = self.buf[:12 + n], self.buf[12 + n:]
                if crc16_ccitt(blk[:10 + n]) != blk[10 + n] | (blk[11 + n] << 8):
                    self.crc_errors += 1
                    self.buf = self.buf[1:]
                    continue
                self.buf = rest
                flags, chmask = blk[2], blk[3]
                first = int.from_bytes(blk[6:10], "little")
                nch = 2 if chmask == 3 else 1
                if self.next_index is not None and first > self.next_index:
                    self.lost_samples += first - self.next_index
                self.next_index = first + n // nch
                self.blocks += 1
                self.samples += n // nch
                self.lost_flags += flags & 1
                out.append(("block", flags, chmask, first, blk[10:10 + n]))
            else:
                i = self.buf.find(b"\n")
                j = self.buf.find(b"\xa5\x5a")
                if i < 0 or (0 <= j < i):
                    if j > 0:          # stray bytes before a block: drop them
                        self.buf = self.buf[j:]
                        continue
                    break
                line, self.buf = self.buf[:i], self.buf[i + 1:]
                out.append(("line", line.decode(errors="replace").strip()))
        return out


def parse_kv(line: str) -> dict:
    """'OK RATE=1 DIV=2 ...' -> {'RATE': 1.0, 'DIV': 2.0, ...}"""
    return {k: _num(v) for k, v in (w.split("=", 1) for w in line.split() if "=" in w)}


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

    def stream(self, rate: float, chmask: int = 3, seconds: float = 5.0, out_path: str | None = None) -> dict:
        """Runs a stream for `seconds`, returns the measured figures; optionally writes the samples."""
        self.port.write(f"STREAM {rate:g} {chmask}\n".encode())
        reply = self.readline()
        if not reply.startswith("OK"):
            raise RuntimeError(reply)
        plan = parse_kv(reply)
        dec = StreamDecoder()
        dec.buf, self.buf = self.buf, b""
        nch = 2 if chmask == 3 else 1
        lsb = [plan.get("LSB1", 0.0), plan.get("LSB2", 0.0)]
        zero = [plan.get("ZERO1", 128.0), plan.get("ZERO2", 128.0)]
        f = None
        if out_path:
            f = open(out_path, "w" if out_path.endswith(".csv") else "wb")
            if out_path.endswith(".csv"):
                names = ["ch1_v", "ch2_v"] if chmask == 3 else [f"ch{chmask}_v"]
                f.write("index," + ",".join(names) + "\n")
        chans = [0, 1] if chmask == 3 else [chmask - 1]
        t0 = time.monotonic()
        total = 0
        end_line = ""

        def take(events):
            nonlocal end_line
            for ev in events:
                if ev[0] == "line":
                    if ev[1].startswith("STREAM END"):
                        end_line = ev[1]
                    continue
                _, flags, cm, first, payload = ev
                if f is None:
                    continue
                if out_path.endswith(".csv"):
                    for i in range(0, len(payload) - nch + 1, nch):
                        vals = [(payload[i + k] - zero[c]) * lsb[c] for k, c in enumerate(chans)]
                        f.write(f"{first + i // nch}," + ",".join(f"{v:.4f}" for v in vals) + "\n")
                else:
                    f.write(payload)

        while time.monotonic() - t0 < seconds:
            chunk = self.port.read(65536)
            total += len(chunk)
            take(dec.feed(chunk))
        elapsed = time.monotonic() - t0
        self.port.write(b"STREAM 0\n")
        end = time.monotonic() + 3.0
        while not end_line and time.monotonic() < end:
            take(dec.feed(self.port.read(65536)))
        if f:
            f.close()
        return {
            "requested_rate": rate, "rate": plan.get("RATE"), "raw_rate": plan.get("RAW"), "avg": plan.get("AVG"),
            "channels": nch, "seconds": round(elapsed, 3), "bytes_per_s": round(total / elapsed),
            "samples_per_s_per_channel": round(dec.samples / elapsed), "blocks": dec.blocks,
            "lost_samples": dec.lost_samples, "blocks_after_loss": dec.lost_flags, "crc_errors": dec.crc_errors,
            "device_end": end_line,
        }

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
    elif cmd == "stream":
        rate = float(argv[3]) if len(argv) > 3 else 100e3
        chmask = int(argv[4]) if len(argv) > 4 else 3
        seconds = float(argv[5]) if len(argv) > 5 else 5.0
        out = argv[6] if len(argv) > 6 else None
        for k, v in pen.stream(rate, chmask, seconds, out).items():
            print(f"{k:28s} {v}")
    elif cmd == "send":
        print(pen.command(" ".join(argv[3:])))
    else:
        raise SystemExit(__doc__)


if __name__ == "__main__":
    main(sys.argv)
