"""AI view: the computer looks at what the pen's tips touch and picks what the pen should show.

The pen in AI mode (or `AI ASK`) sends a snapshot: an `AIASK <measurements>` line and both channels' samples.
The host then takes a few more captures at other timebases (SURVEY_TB), and this module:

  1. measures each capture (features): logic levels or analog, edges, the shortest pulse (one bit), the idle
     level, the period and how regular it is, duty;
  2. makes a first guess by rules (rules_plan): a UART or CAN bus to decode, a PWM for the meter's timing page,
     a waveform for the scope, a DC level for the meter;
  3. asks a language model (Gemini or Claude) to check that guess against the numbers and a sketch of the
     waveforms, and to word a title and a few lines for the pen's screen (ask_model);
  4. turns the plan into the pen's USB commands (plan_to_commands): `AI CLEAR`, `AI TITLE`, `AI LINE`, the
     settings (`GAIN`, `TB`, `TRIG`, `DECODE`), and `AI VIEW n [page]`.

Without a key (or when the model fails) the rules' plan is used, so the mode works offline too. The key is
read at run time (load_key): from GEMINI_API_KEY / ANTHROPIC_API_KEY, or from a JSON file holding
{"keys": {"gemini": ..., "anthropic": ...}}. It is never written anywhere and never sent to a browser.
"""
from __future__ import annotations

import json
import math
import os
import urllib.error
import urllib.request

LSB = {"1X": 9.803 * 1.024 / 256, "10X": 53.56 * 1.024 / 256}   # tip volts per code
TB_NS = [100, 200, 500, 1e3, 2e3, 5e3, 1e4, 2e4, 5e4, 1e5, 2e5, 5e5, 1e6, 2e6, 5e6, 1e7, 2e7, 5e7]
SURVEY_TB = [2, 6, 10, 14]   # 500 ns, 10 us, 200 us and 5 ms per division
SURVEY_DEEP = (65536, 25e6)  # then a deep record of both channels: 2.6 ms at 25 MS/s (a CAN frame or two,
                             # a UART line; 12 samples per 2 Mbit/s bit)


def deep_snapshot(plan: dict, ch1: list[int], ch2: list[int]) -> dict:
    """A deep record (the DEEP reply's plan and both channels' codes) as a snapshot for analyse()."""
    rng = {c: ("10X" if float(plan.get(f"LSB{c}", 0)) > 0.1 else "1X") for c in (1, 2)}
    return {"meas": {"FS": float(plan["RATE"]), "TB": 0, "CH1": {"RANGE": rng[1]}, "CH2": {"RANGE": rng[2]}},
            "ch1": list(ch1), "ch2": list(ch2)}
STD_BAUD = [300, 1200, 2400, 4800, 9600, 14400, 19200, 38400, 57600, 76800, 115200, 230400, 250000, 460800,
            500000, 921600, 1000000, 1500000, 2000000]
CAN_RATES = [125e3, 250e3, 500e3, 1e6, 2e6, 4e6, 5e6, 8e6]
VIEWS = {"scope_dual": (0, 0), "scope_ch1": (1, 0), "scope_ch2": (2, 0), "logic": (3, 0), "meter_volts": (4, 0),
         "meter_timing": (4, 1), "decode_uart": (5, 0), "decode_can": (5, 0)}
DEFAULT_MODELS = {"gemini": "gemini-flash-latest", "anthropic": "claude-sonnet-5-5"}


# ---- measuring ---------------------------------------------------------------------------------------

def parse_meas(line: str) -> dict:
    """`CH1 VPP=.. F=..;CH2 ..;FS=..;TB=..` (MEAS?, ASK, AIASK) as a dict."""
    if line.startswith(("AIASK ", "ASK ")):
        line = line.split(" ", 1)[1]
    out: dict = {}
    for part in line.split(";"):
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


def channel_features(codes: list[int], fs: float, rng: str = "1X") -> dict:
    """Levels, edges and timing of one channel's capture (codes rise with the tip voltage)."""
    lsb = LSB.get(rng, LSB["1X"])
    n = len(codes)
    if n < 4 or fs <= 0:
        return {"samples": n, "kind": "none"}
    lo, hi = min(codes), max(codes)
    span = hi - lo
    mean = sum(codes) / n
    rms = math.sqrt(max(0.0, sum((c - mean) ** 2 for c in codes) / n))
    f = {
        "samples": n, "seconds": n / fs, "vmin": round((lo - 128) * lsb, 3), "vmax": round((hi - 128) * lsb, 3),
        "vmean": round((mean - 128) * lsb, 3), "vrms_ac": round(rms * lsb, 3),
    }
    if span < 8:
        f["kind"] = "flat"
        return f
    near = sum(1 for c in codes if c < lo + span / 4 or c > hi - span / 4) / n
    f["kind"] = "two-level" if near > 0.9 and span >= 12 else "analog"
    mid, hy = (lo + hi) / 2, max(1.0, span / 8)
    level = 1 if codes[0] > mid else 0
    start, runs, rises = 0, [], []
    for i, c in enumerate(codes):
        if (level and c < mid - hy) or (not level and c > mid + hy):
            runs.append((level, i - start, start == 0))
            level ^= 1
            start = i
            if level:
                rises.append(i)
    runs.append((level, n - start, True))
    whole = [(lv, ln) for lv, ln, partial in runs if not partial]
    f["edges"] = len(runs) - 1
    f["high_fraction"] = round(sum(1 for c in codes if c > mid) / n, 3)
    if whole:
        f["shortest_pulse_s"] = min(ln for _, ln in whole) / fs
        longest = max(runs, key=lambda r: r[1])
        f["longest_run_s"] = longest[1] / fs
        f["idle_level"] = "high" if longest[0] else "low"
    if len(rises) >= 3:
        gaps = [b - a for a, b in zip(rises, rises[1:])]
        m = sum(gaps) / len(gaps)
        sd = math.sqrt(sum((g - m) ** 2 for g in gaps) / len(gaps))
        f["period_s"] = m / fs
        f["period_regularity"] = round(1 - sd / m, 3) if m else 0.0   # 1 = perfectly regular
    return f


def nearest(value: float, table: list[float], tol: float = 0.08) -> float | None:
    best = min(table, key=lambda t: abs(value / t - 1))
    return best if abs(value / best - 1) <= tol else None


def sketch(codes: list[int], rng: str, points: int = 48) -> list[list[float]]:
    """The waveform as [min, max] volts per slot, for the model to see its shape."""
    lsb = LSB.get(rng, LSB["1X"])
    n = len(codes)
    out = []
    for k in range(min(points, n)):
        part = codes[k * n // points:max(k * n // points + 1, (k + 1) * n // points)]
        out.append([round((min(part) - 128) * lsb, 2), round((max(part) - 128) * lsb, 2)])
    return out


def analyse(snapshots: list[dict]) -> list[dict]:
    """Snapshots {"meas": line or dict, "ch1": codes, "ch2": codes} -> per snapshot the timebase, the rate and
    both channels' features."""
    out = []
    for s in snapshots:
        meas = s["meas"] if isinstance(s["meas"], dict) else parse_meas(s["meas"])
        fs = float(meas.get("FS", 0) or 0)
        entry = {"tb_ns_per_div": meas.get("TB"), "sample_rate": fs, "channels": {}}
        for name in ("CH1", "CH2"):
            rng = (meas.get(name) or {}).get("RANGE", "1X")
            codes = s.get(name.lower()) or []
            feat = channel_features(codes, fs, rng)
            feat["range"] = "+-26V" if rng == "10X" else "+-5V"
            feat["meter"] = {k: (meas.get(name) or {}).get(k) for k in ("VPP", "F", "VRMS", "DUTY", "DC")}
            entry["channels"][name] = feat
            entry.setdefault("sketch", {})[name] = sketch(codes, rng) if codes else []
        out.append(entry)
    return out


# ---- a first guess by rules -------------------------------------------------------------------------

def _best(analysis: list[dict], ch: str, key: str, pick=min):
    """A time (or its regularity) from the captures that resolve it: 8 samples or more per pulse or period,
    and enough edges to judge. A slow capture cannot time a fast pulse."""
    base = "period_s" if key == "period_regularity" else key
    good = [a for a in analysis if a["channels"][ch].get(base) and a["channels"][ch].get("edges", 0) >= 6
            and a["channels"][ch][base] * a["sample_rate"] >= 8]
    if key == "period_regularity":   # from the capture with the most edges
        good = [a for a in good if a["channels"][ch]["edges"] >= 16]
        return max(good, key=lambda a: a["channels"][ch]["edges"])["channels"][ch][key] if good else None
    if key == "shortest_pulse_s" and good:   # among the captures that agree, the finest-sampled one
        m = min(a["channels"][ch][key] for a in good)
        near = [a for a in good if a["channels"][ch][key] <= 1.15 * m]
        return max(near, key=lambda a: a["sample_rate"])["channels"][ch][key]
    vals = [a["channels"][ch][key] for a in good] or [a["channels"][ch][key] for a in analysis if a["channels"][ch].get(key)]
    return pick(vals) if vals else None


def _kind(analysis: list[dict], ch: str) -> str:
    kinds = [a["channels"][ch].get("kind") for a in analysis]
    for k in ("two-level", "analog"):
        if k in kinds:
            return k
    return "flat"


def tb_for_period(period: float, periods_on_screen: float = 4.0) -> int:
    """The timebase showing about `periods_on_screen` periods across the 10 divisions."""
    want_ns = period * periods_on_screen / 10 * 1e9
    return min(range(len(TB_NS)), key=lambda i: abs(math.log(TB_NS[i] / want_ns)))


def rules_plan(analysis: list[dict]) -> dict:
    k1, k2 = _kind(analysis, "CH1"), _kind(analysis, "CH2")
    plan: dict = {"range": "auto", "lines": []}
    # CAN: both tips two-level, opposite swings around 2.5 V (CAN_H up, CAN_L down when dominant)
    if k1 == "two-level" and k2 == "two-level":
        a = max(analysis, key=lambda x: x["channels"]["CH1"].get("edges", 0))
        c1, c2 = a["channels"]["CH1"], a["channels"]["CH2"]
        if 1.0 < c1["vmean"] < 4.0 and 1.0 < c2["vmean"] < 4.0 and c1.get("idle_level") != c2.get("idle_level"):
            bit = _best(analysis, "CH1", "shortest_pulse_s") or 2e-6
            data = nearest(1 / bit, CAN_RATES, 0.15) or round(1 / bit)
            arb = data if data <= 1e6 else 500e3
            plan.update(view="decode_can", decode_source=3, decode_rate=arb, decode_data_rate=data,
                        title=f"CAN{' FD' if data > arb else ''} bus, {arb / 1e3:g}k" + (f"/{data / 1e6:g}M" if data > arb else ""),
                        reason="Two tips swing opposite ways around 2.5 V: CAN_H and CAN_L.")
            plan["lines"] = ["CAN_H on CH1, CAN_L on CH2.", f"Shortest bit {bit * 1e6:.2f} us.",
                             "Decoding CH1 - CH2."]
            return plan
    for ch, kind, src in (("CH1", k1, 1), ("CH2", k2, 2)):
        if kind != "two-level":
            continue
        reg = _best(analysis, ch, "period_regularity")
        reg = 1.0 if reg is None else reg   # too few edges to say: not data
        bit = _best(analysis, ch, "shortest_pulse_s")
        idle = [a["channels"][ch].get("idle_level") for a in analysis if a["channels"][ch].get("idle_level")]
        if reg < 0.9 and bit:   # irregular edges: data
            baud = nearest(1 / bit, STD_BAUD) or round(1 / bit)
            plan.update(view="decode_uart", decode_source=src, decode_rate=baud,
                        title=f"UART {baud:g} baud on {ch}",
                        reason=f"{ch} has two levels with irregular edges; its shortest pulse is one bit.")
            plan["lines"] = [f"Serial data, idles {idle[0] if idle else 'high'}.", f"One bit: {bit * 1e6:.2f} us.",
                             "Decoding as 8N1 text."]
            return plan
        period = _best(analysis, ch, "period_s")
        if period:
            plan.update(view="meter_timing", title=f"Pulses on {ch}, {1 / period:.4g} Hz",
                        reason=f"{ch} switches between two levels at a steady rate: a clock or PWM.")
            plan["lines"] = ["A steady pulse train.", "Frequency, duty and widths."]
            return plan
    analog = [ch for ch, k in (("CH1", k1), ("CH2", k2)) if k == "analog"]
    if analog:
        ch = analog[0]
        period = _best(analysis, ch, "period_s", max)
        view = "scope_dual" if len(analog) == 2 else ("scope_ch1" if ch == "CH1" else "scope_ch2")
        plan.update(view=view, timebase_index=tb_for_period(period) if period else 6,
                    trigger_volts=0.0, title=f"Waveform on {' and '.join(analog)}",
                    reason="An analog signal: the scope shows its shape.")
        if period:
            plan["lines"] = [f"Period {period * 1e3:.4g} ms ({1 / period:.4g} Hz)."]
        return plan
    plan.update(view="meter_volts", title="Steady levels",
                reason="Neither tip moves much: the meter shows the DC levels.")
    plan["lines"] = ["No signal moving.", "DC levels on the meter."]
    return plan


# ---- asking a model ----------------------------------------------------------------------------------

SYSTEM = """You set up ProbePen-AI, a pen-shaped two-channel oscilloscope with a 240x135 screen, for whoever is
holding it. You get measurements of what its two probe tips touch, taken at several timebases, and a guess
made by simple rules. Decide the single most useful thing to show, and explain it briefly on the pen's
screen.

The pen: 2 channels, 8-bit, up to 75 MS/s (125 MS/s overclocked), ranges +-5 V (39 mV steps) and +-26 V
(214 mV steps). Views:
- scope_dual, scope_ch1, scope_ch2: waveforms; pick timebase_index (0..17 = 100 ns, 200 ns, 500 ns, 1 us,
  2 us, 5 us, 10 us, 20 us, 50 us, 100 us, 200 us, 500 us, 1 ms, 2 ms, 5 ms, 10 ms, 20 ms, 50 ms per
  division, 10 divisions) and trigger_volts.
- meter_volts: large DC level and AC RMS per channel. meter_timing: large frequency, duty, pulse widths.
- decode_uart: 8N1 text from one channel (decode_source 1 or 2, decode_rate = baud).
- decode_can: CAN / CAN FD from CH1 - CH2 (decode_source 3), decode_rate = arbitration bit rate,
  decode_data_rate = data phase rate (equal for classic CAN).
- logic: the 4-line logic pod (only if asked).

Give every setting the view needs: decode_source and decode_rate for decode_uart; decode_source,
decode_rate and decode_data_rate for decode_can; timebase_index and trigger_volts for the scope views.

Rules: trust the numbers. A shortest pulse that repeats is one bit. Snap bit rates to standard values
(9600, 19200, 38400, 57600, 115200, 230400, 460800, 921600, 1M baud; CAN 125k, 250k, 500k, 1M, data 2M,
5M, 8M). The title is at most 34 characters; each line at most 38 characters, plain ASCII, no more than
6 lines, saying what the signal is and anything notable (levels, rate, duty, noise, likely source).
Keep the rules' view unless the numbers clearly say otherwise. Reply with JSON only."""

SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "view": {"type": "STRING", "enum": list(VIEWS)},
        "timebase_index": {"type": "INTEGER"},
        "trigger_volts": {"type": "NUMBER"},
        "range": {"type": "STRING", "enum": ["auto", "5v", "26v"]},
        "decode_source": {"type": "INTEGER"},
        "decode_rate": {"type": "NUMBER"},
        "decode_data_rate": {"type": "NUMBER"},
        "title": {"type": "STRING"},
        "lines": {"type": "ARRAY", "items": {"type": "STRING"}},
        "reason": {"type": "STRING"},
    },
    "required": ["view", "title", "lines", "reason"],
}


def _json_schema(s: dict) -> dict:
    """The Gemini-style schema above in JSON Schema form (for Claude)."""
    out = {k: v for k, v in s.items() if k not in ("type", "properties", "items")}
    out["type"] = s["type"].lower()
    if "properties" in s:
        out["properties"] = {k: _json_schema(v) for k, v in s["properties"].items()}
    if "items" in s:
        out["items"] = _json_schema(s["items"])
    return out


def _post(url: str, headers: dict, body: dict, timeout: float = 90.0) -> dict:
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers={"content-type": "application/json", **headers})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")[:300]
        raise RuntimeError(f"HTTP {e.code}: {detail}") from None


def ask_model(provider: str, key: str, model: str, analysis: list[dict], guess: dict) -> dict:
    user = json.dumps({"captures": analysis, "rules_guess": guess}, separators=(",", ":"))
    if provider == "gemini":
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
        body = {
            "systemInstruction": {"parts": [{"text": SYSTEM}]},
            "contents": [{"role": "user", "parts": [{"text": user}]}],
            "generationConfig": {"responseMimeType": "application/json", "responseSchema": SCHEMA, "temperature": 0.2,
                                 "thinkingConfig": {"thinkingLevel": "low"}},   # an answer in seconds, not half a minute
        }
        try:
            r = _post(url, {"x-goog-api-key": key}, body)
        except RuntimeError as e:   # older models take no thinking level: ask again without it
            if "think" not in str(e).lower():
                raise
            body["generationConfig"].pop("thinkingConfig")
            r = _post(url, {"x-goog-api-key": key}, body)
        parts = r.get("candidates", [{}])[0].get("content", {}).get("parts", [])
        text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
    elif provider == "anthropic":
        body = {
            "model": model, "max_tokens": 1024, "system": SYSTEM,
            "messages": [{"role": "user", "content": user}],
            "tools": [{"name": "show", "description": "What the pen shows", "input_schema": _json_schema(SCHEMA)}],
            "tool_choice": {"type": "tool", "name": "show"},
        }
        r = _post("https://api.anthropic.com/v1/messages", {"x-api-key": key, "anthropic-version": "2023-06-01"}, body)
        for block in r.get("content", []):
            if block.get("type") == "tool_use":
                return block["input"]
        raise RuntimeError("Claude gave no plan")
    else:
        raise ValueError(f"unknown provider {provider}")
    return json.loads(text)


def load_key(provider: str, key_file: str | None = None) -> str:
    env = {"gemini": ("GEMINI_API_KEY", "GOOGLE_API_KEY"), "anthropic": ("ANTHROPIC_API_KEY",)}[provider]
    for name in env:
        if os.environ.get(name):
            return os.environ[name]
    if key_file and os.path.exists(key_file):
        with open(key_file, encoding="utf8") as f:
            return (json.load(f).get("keys") or {}).get(provider, "") or ""
    return ""


# ---- the plan as commands ----------------------------------------------------------------------------

def _clip(text: str, n: int) -> str:
    text = "".join(c if 32 <= ord(c) < 127 else "?" for c in str(text)).strip()
    return text[:n]


def plan_to_commands(plan: dict, meas: dict | None = None) -> list[str]:
    """USB command lines for the pen (each under 60 characters, its line buffer is 64)."""
    view = plan.get("view") if plan.get("view") in VIEWS else "scope_dual"
    mode, page = VIEWS[view]
    cmds = ["AI CLEAR", f"AI TITLE {_clip(plan.get('title', view), 38)}"]
    for line in (plan.get("lines") or [])[:6]:
        cmds.append(f"AI LINE {_clip(line, 40)}")
    rng = plan.get("range", "auto")
    cmds.append({"5v": "GAIN 1", "26v": "GAIN 10"}.get(rng, "GAIN 0"))
    if view.startswith("scope"):
        tb = int(plan.get("timebase_index", 6))
        cmds.append(f"TB {max(0, min(len(TB_NS) - 1, tb))}")
        ch = "CH2" if view == "scope_ch2" else "CH1"
        r = ((meas or {}).get(ch) or {}).get("RANGE", "1X") if rng == "auto" else ("10X" if rng == "26v" else "1X")
        code = round(128 + float(plan.get("trigger_volts", 0.0)) / LSB.get(r, LSB["1X"]))
        cmds.append(f"TRIG {max(0, min(255, code))}")
    if view == "decode_uart":
        src = int(plan.get("decode_source", 1))
        cmds.append(f"DECODE UART {src if src in (1, 2) else 1} {float(plan.get('decode_rate', 115200)):.0f}")
    elif view == "decode_can":
        src = int(plan.get("decode_source", 3))
        rate = float(plan.get("decode_rate", 500e3))
        data = max(rate, float(plan.get("decode_data_rate", rate) or rate))
        cmds.append(f"DECODE CAN {src if src in (1, 2, 3) else 3} {rate:.0f} {data:.0f}")
    cmds.append(f"AI VIEW {mode} {page}")
    return cmds


def ai_plan(snapshots: list[dict], provider: str = "gemini", key: str = "", model: str = "") -> dict:
    """The whole question: returns {"plan", "commands", "source", "note"}; the rules answer if the model can't."""
    analysis = analyse(snapshots)
    guess = rules_plan(analysis)
    meas = snapshots[0]["meas"] if isinstance(snapshots[0]["meas"], dict) else parse_meas(snapshots[0]["meas"])
    source, note, plan = "rules", "", guess
    if key and provider in ("gemini", "anthropic"):
        model = model or DEFAULT_MODELS[provider]
        try:
            got = ask_model(provider, key, model, analysis, guess)
            if got.get("view") in VIEWS:
                if got["view"] == guess.get("view"):   # settings the model left out: the rules measured them
                    for k in ("decode_source", "decode_rate", "decode_data_rate", "timebase_index", "trigger_volts"):
                        if got.get(k) in (None, 0) and k in guess:
                            got[k] = guess[k]
                plan, source = got, f"{provider}:{model}"
            else:
                note = f"the model's view {got.get('view')!r} is not one the pen has; using the rules"
        except Exception as e:   # no network, a refused key, a timeout: the rules still answer
            note = f"the model did not answer ({e}); using the rules"
    elif not key:
        note = "no API key: the rules' guess"
    return {"plan": plan, "commands": plan_to_commands(plan, meas), "source": source, "note": note,
            "analysis": analysis}
