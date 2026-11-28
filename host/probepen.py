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
    python host/probepen.py PORT deep [N] [CH] [RATE] [OUT]
                                                  # one long record (N 0 = as long as fits: 196 k samples per
                                                  # channel, 393 k for one) at RATE (default the fastest); CSV volts
    python host/probepen.py PORT decode uart SRC BAUD [SECONDS]
    python host/probepen.py PORT decode can SRC RATE [DATA_RATE] [SECONDS]
                                                  # the pen decodes the bus and sends text: SRC 1 = CH1, 2 = CH2,
                                                  # 3 = CH1 - CH2 (CAN_H - CAN_L); prints each line or frame
    python host/probepen.py PORT ai [gemini|anthropic|rules] [KEY_FILE]
                                                  # AI mode: answers the pen's AIASK snapshots (MODE to AI, or
                                                  # ASK in it): surveys 4 timebases, asks the model, sets the
                                                  # pen's view and explanation (host/ai_view.py)
    python host/probepen.py PORT watch [N] [COUNT] [OUT.jsonl]
                                                  # continuous captures, one after another: prints each one's
                                                  # measurements and appends {time, meas, ch1, ch2} to OUT

PORT is e.g. /dev/ttyACM0 or COM7. Uses pyserial when it is installed; on Linux it also works without it.

Protocol (firmware/src/app.c, usb_proto.c): one ASCII command per line, one reply line. After
`CAPTURE n` (reply `OK n`) and after an `ASK ...` line, the device sends one binary frame per channel:
AA 55 seq count_lo count_hi samples (uint16 LE) crc_lo crc_hi, CRC-16/CCITT (0xFFFF start) over
everything before the CRC; seq 0 is CH1, seq 1 is CH2. Sample codes rise with the tip voltage.
`STREAM rate [ch]` replies `OK RATE=.. RAW=.. DIV=.. AVG=.. CH=.. BPS=.. LSB1=.. ZERO1=.. LSB2=.. ZERO2=..`
and then sends blocks until `STREAM 0` (or a button), which ends with a `STREAM END ...` line:
A5 5A flags chmask len(u16) first_sample_index(u32) payload[len] crc(u16), 8-bit codes, CH1 and CH2
interleaved when both are on; flags bit0 = samples lost before this block (firmware/include/probepen_stream.h).
`DEEP n [ch] [rate]` replies `OK N=.. CH=.. RATE=.. DIV=.. SECONDS=.. LSB1=.. ZERO1=.. LSB2=.. ZERO2=..`, takes
the record, then sends `DEEP DATA N=.. TRIG=..`, the record as the same blocks, and `DEEP END`
(firmware/include/probepen_deep.h).
`DECODE UART src baud` / `DECODE CAN src rate [data_rate]` replies `OK PROTO=.. SRC=.. RATE=.. DATA=.. FS=.. DIV=..
SPB=.. MODE=LIVE|BURST COVER=.. BURSTN=..`, then sends one line per UART line or CAN frame until `DECODE 0` (or a
button), which ends with `DECODE END ...` (firmware/include/probepen_decode.h):
  DEC UART T=<s> BYTES=n FE=<framing errors> HEX=<bytes>
  DEC CAN T=<s> ID=<hex> EXT= FD= BRS= ESI= RTR= DLC= LEN= ACK= ERR=NONE|STUFF|CRC|SC|FORM HEX=<data>
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

    def deep(self, n: int = 0, chmask: int = 3, rate: float = 0.0, timeout: float = 600.0) -> dict:
        """One deep record: returns the plan, the trigger index and the samples in volts per channel."""
        reply = self.command(f"DEEP {n} {chmask} {rate:g}")
        if not reply.startswith("OK"):
            raise RuntimeError(reply)
        plan = parse_kv(reply)
        dec = StreamDecoder()
        dec.buf, self.buf = self.buf, b""
        payload = bytearray()
        info: dict = {}
        t0 = time.monotonic()
        done = False
        while not done and time.monotonic() - t0 < timeout:
            for ev in dec.feed(self.port.read(65536)):
                if ev[0] == "block":
                    payload += ev[4]
                elif ev[1].startswith("DEEP DATA"):
                    info = parse_kv(ev[1])
                elif ev[1].startswith("DEEP END"):
                    done = True
        if not done:
            raise TimeoutError("deep record did not finish")
        chans = [0, 1] if chmask == 3 else [chmask - 1]
        out = {"plan": plan, "trigger_index": int(info.get("TRIG", -1)), "transfer_s": round(time.monotonic() - t0, 3),
               "crc_errors": dec.crc_errors, "lost_samples": dec.lost_samples}
        for k, c in enumerate(chans):
            lsb, zero = plan[f"LSB{c + 1}"], plan[f"ZERO{c + 1}"]
            out[f"CH{c + 1}"] = [(b - zero) * lsb for b in payload[k::len(chans)]]
            out[f"codes{c + 1}"] = list(payload[k::len(chans)])
        return out

    def decode(self, proto: str, src: int, rate: float, data_rate: float = 0.0, seconds: float = 10.0):
        """Starts a decode and yields each decoded record (parse_decoded) for `seconds`, then stops it."""
        proto = proto.upper()
        extra = f" {data_rate:g}" if proto == "CAN" and data_rate else ""
        reply = self.command(f"DECODE {proto} {src} {rate:g}{extra}")
        if not reply.startswith("OK"):
            raise RuntimeError(reply)
        yield {"plan": parse_kv(reply)}
        t0 = time.monotonic()
        stopped = False
        while True:
            if not stopped and time.monotonic() - t0 >= seconds:
                self.port.write(b"DECODE 0\n")
                stopped = True
            try:
                line = self.readline(timeout=0.2)
            except TimeoutError:
                if stopped and time.monotonic() - t0 > seconds + 3.0:
                    return
                continue
            if line.startswith("DEC "):
                yield parse_decoded(line)
            elif line.startswith("DECODE END"):
                yield {"end": parse_kv(line)}
                return

    def watch(self, n: int = 1024):
        """Continuous captures: yields (measurements, ch1, ch2) for each capture, one straight after another."""
        while True:
            a, b = self.capture(n)
            yield parse_meas(self.command("MEAS?")), a, b

    def ai_serve(self, provider: str = "gemini", key: str = "", model: str = ""):
        """Answers the pen's AI questions until stopped: yields each result (ai_view.ai_plan)."""
        import ai_view
        while True:
            try:
                line = self.readline(timeout=3600.0)
            except TimeoutError:
                continue
            if not line.startswith("AIASK "):
                continue
            frames = dict(self.read_frame() for _ in range(2))
            snaps = [{"meas": line[6:], "ch1": frames[0], "ch2": frames[1]}]
            self.command("AI WAIT")
            for tb in ai_view.SURVEY_TB:
                self.command(f"TB {tb}")
                for _ in range(4):   # the capture in flight may still be at the old timebase
                    a, b = self.capture(512)
                    meas = self.command("MEAS?")
                    if parse_meas(meas).get("TB") == ai_view.TB_NS[tb]:
                        snaps.append({"meas": meas, "ch1": a, "ch2": b})
                        break
            n, rate = ai_view.SURVEY_DEEP
            rec = self.deep(n, 3, rate)
            snaps.append(ai_view.deep_snapshot(rec["plan"], rec["codes1"], rec["codes2"]))
            out = ai_view.ai_plan(snaps, provider, key, model)
            for c in out["commands"]:
                self.command(c)
            yield out

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


def parse_decoded(line: str) -> dict:
    """A `DEC UART ...` or `DEC CAN ...` line as a dict, with the payload as bytes and as text."""
    words = line.split()
    out: dict = {"proto": words[1]}
    for w in words[2:]:
        k, _, v = w.partition("=")
        out[k.lower()] = v
    data = bytes.fromhex(out.pop("hex", ""))
    out["data"] = data
    out["text"] = "".join(chr(c) if 32 <= c < 127 or c == 10 else ("" if c == 13 else ".") for c in data)
    out["t"] = float(out["t"])
    return out


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
    elif cmd == "deep":
        n = int(argv[3]) if len(argv) > 3 else 0
        chmask = int(argv[4]) if len(argv) > 4 else 3
        rate = float(argv[5]) if len(argv) > 5 else 0.0
        rec = pen.deep(n, chmask, rate)
        names = [k for k in ("CH1", "CH2") if k in rec]
        count = len(rec[names[0]])
        print(f"{count} samples per channel at {rec['plan']['RATE']:g} S/s, trigger at {rec['trigger_index']}, "
              f"sent in {rec['transfer_s']} s, {rec['crc_errors']} CRC errors")
        if len(argv) > 6:
            with open(argv[6], "w") as f:
                f.write("t_s," + ",".join(n.lower() + "_v" for n in names) + "\n")
                for i in range(count):
                    f.write(f"{i / rec['plan']['RATE']:.9e}," + ",".join(f"{rec[n][i]:.4f}" for n in names) + "\n")
    elif cmd == "decode":
        proto = argv[3].upper() if len(argv) > 3 else "UART"
        src = int(argv[4]) if len(argv) > 4 else 1
        rate = float(argv[5]) if len(argv) > 5 else (115200.0 if proto == "UART" else 500e3)
        data_rate = float(argv[6]) if proto == "CAN" and len(argv) > 6 else 0.0
        seconds = float(argv[7 if proto == "CAN" else 6]) if len(argv) > (7 if proto == "CAN" else 6) else 10.0
        for rec in pen.decode(proto, src, rate, data_rate, seconds):
            if "plan" in rec:
                p = rec["plan"]
                print(f"# {p['MODE'].lower()} at {p['FS']:g} S/s ({p['SPB']} samples per bit), "
                      f"{p['COVER'] * 100:.0f} % of the time seen")
            elif "end" in rec:
                print("#", rec["end"])
            elif rec["proto"] == "UART":
                fe = f"  [{rec['fe']} framing errors]" if rec["fe"] != "0" else ""
                print(f"{rec['t']:12.6f}  {rec['text'].rstrip(chr(10))}{fe}")
            else:
                status = "" if rec["err"] == "NONE" else f"  [{rec['err']} error]"
                kind = ("FD" if rec["fd"] == "1" else "CAN") + (" BRS" if rec["brs"] == "1" else "")
                print(f"{rec['t']:12.6f}  {rec['id']:>8s} {kind:6s} [{rec['len']}] {rec['text']}{status}")
    elif cmd == "ai":
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import ai_view
        provider = argv[3] if len(argv) > 3 else "gemini"
        key = "" if provider == "rules" else ai_view.load_key(provider, argv[4] if len(argv) > 4 else None)
        print(f"# AI mode: {provider if key else 'rules (no API key)'}; put the pen in AI mode (MODE), ASK to ask again")
        for out in pen.ai_serve(provider, key):
            p = out["plan"]
            print(f"{time.strftime('%H:%M:%S')}  {out['source']}: {p.get('view')} - {p.get('title')}")
            for line in p.get("lines", []):
                print("           ", line)
            if out["note"]:
                print("           (" + out["note"] + ")")
    elif cmd == "watch":
        import json
        n = int(argv[3]) if len(argv) > 3 else 1024
        count = int(argv[4]) if len(argv) > 4 else 0
        out = open(argv[5], "a", encoding="utf8") if len(argv) > 5 else None
        for k, (meas, a, b) in enumerate(pen.watch(n), 1):
            ch = [meas.get(c, {}) for c in ("CH1", "CH2")]
            print(f"{k:5d}  " + "  ".join(f"{c}: {m.get('VPP', 0):.3f} Vpp {m.get('F', 0):.6g} Hz RMS {m.get('VRMS', 0):.3f}"
                                         for c, m in zip(("CH1", "CH2"), ch)))
            if out:
                out.write(json.dumps({"time": time.time(), "meas": meas, "ch1": a, "ch2": b}) + "\n")
            if count and k >= count:
                break
    elif cmd == "send":
        print(pen.command(" ".join(argv[3:])))
    else:
        raise SystemExit(__doc__)


if __name__ == "__main__":
    main(sys.argv)
