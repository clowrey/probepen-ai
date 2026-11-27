/* ProbePen-AI simulator page: runs probepen.wasm (the firmware UI plus sim/sim.c), shows its framebuffer
 * on the pen and enlarged, feeds it buttons and probe signals, and plays the host app over its USB
 * protocol (one text command per line, binary wave frames: AA 55 seq count samples crc). */
"use strict";

const W = 240, H = 135;
const MODES = ["CH1+2", "CH1", "CH2", "LOGIC", "METER"];
const TB_NS = [100, 200, 500, 1e3, 2e3, 5e3, 1e4, 2e4, 5e4, 1e5, 2e5, 5e5, 1e6, 2e6, 5e6, 1e7, 2e7, 5e7];
const GAIN = [9.803, 53.56];                      // tip volts per ADC volt, +-5 V and +-26 V ranges
const LSB = GAIN.map((g) => (g * 1.024) / 256);   // volts per code
const SIG_KINDS = [
  ["Grounded (0 V)", 0], ["DC", 1], ["Sine", 2], ["Square", 3], ["Triangle", 4], ["Sawtooth", 5],
  ["Pulse", 6], ["UART 8N1 text", 7], ["AM sine", 8],
];
const BTN = ["ASK", "MODE", "UP", "DOWN"];
const KEYS = { a: 0, m: 1, ArrowUp: 2, ArrowDown: 3 };

const $ = (id) => document.getElementById(id);
let ex = null;            // wasm exports
let buttons = 0;          // bitmask from the side buttons and the keyboard
const held = new Map();   // pointerId -> button

/* ---------- number formatting ---------- */
function eng(v, unit, digits = 3) {
  if (!isFinite(v)) return "–";
  const a = Math.abs(v);
  const P = [[1e9, "G"], [1e6, "M"], [1e3, "k"], [1, ""], [1e-3, "m"], [1e-6, "µ"], [1e-9, "n"]];
  if (a === 0) return `0 ${unit}`;
  for (const [s, p] of P) {
    if (a >= s * 0.9995) return `${+(v / s).toPrecision(digits)} ${p}${unit}`;
  }
  return `${+(v / 1e-9).toPrecision(digits)} n${unit}`;
}
function parseSI(s) {
  const m = String(s).trim().match(/^([-+]?[\d.]+(?:e[-+]?\d+)?)\s*([GMkmuµn]?)/i);
  if (!m) return NaN;
  const mul = { G: 1e9, M: 1e6, k: 1e3, K: 1e3, m: 1e-3, u: 1e-6, "µ": 1e-6, n: 1e-9 }[m[2]] || 1;
  return parseFloat(m[1]) * mul;
}
const tbLabel = (ns) => eng(ns * 1e-9, "s") + "/div";

/* ---------- wasm ---------- */
async function loadWasm() {
  const stub = new Proxy({}, { get: () => () => 0 });   // WASI calls the firmware never makes
  const imports = { wasi_snapshot_preview1: stub, env: stub };
  const resp = await fetch("probepen.wasm");
  let inst;
  if (WebAssembly.instantiateStreaming) {
    try {
      inst = (await WebAssembly.instantiateStreaming(resp.clone(), imports)).instance;
    } catch (e) {
      inst = null;
    }
  }
  if (!inst) inst = (await WebAssembly.instantiate(await resp.arrayBuffer(), imports)).instance;
  ex = inst.exports;
  if (ex._initialize) ex._initialize();
  ex.sim_init();
}
function cString(ptr) {
  const m = new Uint8Array(ex.memory.buffer);
  let e = ptr;
  while (m[e]) e++;
  return new TextDecoder().decode(m.subarray(ptr, e));
}
const info = () => JSON.parse(cString(ex.sim_info()));

/* ---------- screen ---------- */
const big = $("lcdBig").getContext("2d");
const small = $("lcdSmall").getContext("2d");
const img = big.createImageData(W, H);
function blit() {
  const fb = new Uint16Array(ex.memory.buffer, ex.sim_fb(), W * H);
  const d = img.data;
  for (let i = 0, j = 0; i < W * H; i++, j += 4) {
    const v = fb[i];
    const r = (v >> 11) & 31, g = (v >> 5) & 63, b = v & 31;
    d[j] = (r << 3) | (r >> 2);
    d[j + 1] = (g << 2) | (g >> 4);
    d[j + 2] = (b << 3) | (b >> 2);
    d[j + 3] = 255;
  }
  big.putImageData(img, 0, 0);
  small.putImageData(img, 0, 0);
}

/* ---------- the pen picture: LCD window and side buttons ---------- */
async function placePen() {
  const geo = await (await fetch("pen.json")).json();
  const pen = $("pen"), c = $("lcdSmall");
  const place = () => {
    const im = $("penImg");
    const w = im.clientWidth, h = im.clientHeight, top = im.offsetTop;
    c.style.left = `${geo.lcd.x * w}px`;
    c.style.top = `${top + geo.lcd.y * h}px`;
    c.style.width = `${geo.lcd.w * w}px`;
    c.style.height = `${geo.lcd.h * h}px`;
  };
  geo.buttons.forEach((b, i) => {
    const el = document.createElement("button");
    el.className = "side-btn";
    el.type = "button";
    el.innerHTML = `<span class="l">${b.name}</span><span class="s">${["A", "M", "▲", "▼"][i]}</span>`;
    el.setAttribute("aria-label", b.name);
    el.style.left = `${b.x * 100}%`;
    el.dataset.btn = i;
    pen.appendChild(el);
    bindHold(el, i);
  });
  $("penImg").complete ? place() : $("penImg").addEventListener("load", place);
  new ResizeObserver(place).observe($("penImg"));
}
function setBtn(i, down) {
  buttons = down ? buttons | (1 << i) : buttons & ~(1 << i);
  document.querySelectorAll(`[data-btn="${i}"]`).forEach((el) => el.classList.toggle("down", down));
}
function bindHold(el, i) {
  el.addEventListener("pointerdown", (e) => {
    e.preventDefault();
    el.setPointerCapture(e.pointerId);
    held.set(e.pointerId, i);
    setBtn(i, true);
  });
  const up = (e) => {
    if (held.has(e.pointerId)) {
      setBtn(held.get(e.pointerId), false);
      held.delete(e.pointerId);
    }
  };
  el.addEventListener("pointerup", up);
  el.addEventListener("pointercancel", up);
  el.addEventListener("contextmenu", (e) => e.preventDefault());
}
function buildPad() {
  const pad = $("btnpad");
  BTN.forEach((name, i) => {
    const el = document.createElement("button");
    el.className = "pad-btn";
    el.type = "button";
    el.textContent = name;
    el.dataset.btn = i;
    pad.appendChild(el);
    bindHold(el, i);
  });
  const typing = (e) => /INPUT|SELECT|TEXTAREA/.test(e.target.tagName);
  window.addEventListener("keydown", (e) => {
    const k = KEYS[e.key] ?? KEYS[e.key.toLowerCase?.()];
    if (k === undefined || typing(e) || e.ctrlKey || e.metaKey || e.altKey) return;
    e.preventDefault();
    if (!e.repeat) setBtn(k, true);
  });
  window.addEventListener("keyup", (e) => {
    const k = KEYS[e.key] ?? KEYS[e.key.toLowerCase?.()];
    if (k !== undefined) setBtn(k, false);
  });
  window.addEventListener("blur", () => BTN.forEach((_, i) => setBtn(i, false)));
}

/* ---------- probe inputs ---------- */
const sig = [
  { kind: 2, vpp: 3, offset: 0, freq: 50e3, duty: 0.5, noise: 0.01 },
  { kind: 3, vpp: 3.3, offset: 1.65, freq: 25e3, duty: 0.5, noise: 0.005 },
];
const pod = { kind: 0, freq: 1e6 };
const PRESETS = [
  {
    name: "Sine + clock", note: "CH1: 3 Vpp 50 kHz sine. CH2: 3.3 V 25 kHz clock. Both channels at 5 MS/s, 10 µs/div.",
    sig: [{ kind: 2, vpp: 3, offset: 0, freq: 50e3, noise: 0.01 }, { kind: 3, vpp: 3.3, offset: 1.65, freq: 25e3, noise: 0.005 }],
    cmds: ["MODE 0", "GAIN 0", "TB 6", "TRIG 140", "RUN 1"],
  },
  {
    name: "UART 115200", note: "CH1: 3.3 V serial line sending \"ProbePen-AI\\r\\n\" at 115200 baud, 8N1. Falling-edge trigger on the start bit (hold MODE to change slope).",
    sig: [{ kind: 7, vpp: 3.3, offset: 0, freq: 115200, noise: 0.01 }, { kind: 0 }],
    cmds: ["MODE 1", "GAIN 1", "TB 8", "TRIG 150", "RUN 1"],
  },
  {
    name: "12 V PWM", note: "CH1: 12 V, 20 kHz, 30 % duty motor PWM. Auto-range moves CH1 to the ±26 V range (214 mV per step).",
    sig: [{ kind: 6, vpp: 12, offset: 0, freq: 20e3, duty: 0.3, noise: 0.03 }, { kind: 0 }],
    cmds: ["MODE 1", "GAIN 0", "TB 7", "TRIG 150", "RUN 1"],
  },
  {
    name: "Fast edges", note: "CH1: 5 MHz square at 100 ns/div. 75 MS/s gives 13.3 ns per sample; the 31 MHz anti-alias filter rounds the edges.",
    sig: [{ kind: 3, vpp: 3.3, offset: 1.65, freq: 5e6, noise: 0.005 }, { kind: 2, vpp: 2, offset: 0, freq: 2e6, noise: 0.005 }],
    cmds: ["MODE 0", "GAIN 1", "TB 0", "TRIG 150", "RUN 1"],
  },
  {
    name: "Glitch at 2 ms/div", note: "CH1: a 1 µs pulse every 1 ms. At 2 ms/div the screen holds only 24 kS/s, but peak detect samples at 2.5 MS/s and keeps each group's min and max, so every pulse shows.",
    sig: [{ kind: 6, vpp: 3.3, offset: 0, freq: 1e3, duty: 0.001, noise: 0.01 }, { kind: 2, vpp: 2, offset: 0, freq: 200, noise: 0.01 }],
    cmds: ["MODE 0", "GAIN 1", "TB 13", "TRIG 150", "RUN 1"],
  },
  {
    name: "Over-range", note: "CH1: a ±35 V sine, beyond the ±26 V limit of the input buffer: the tops clip flat. Keep real inputs inside ±26 V.",
    sig: [{ kind: 2, vpp: 70, offset: 0, freq: 1e3, noise: 0.05 }, { kind: 0 }],
    cmds: ["MODE 1", "GAIN 0", "TB 11", "TRIG 128", "RUN 1"],
  },
  {
    name: "12 V rail (meter)", note: "CH1: 12 V supply with 0.2 V of 100 Hz ripple, CH2: 3.3 V. METER mode reads the DC-sense ADC (12-bit) and VBUS.",
    sig: [{ kind: 2, vpp: 0.2, offset: 12, freq: 100, noise: 0.02 }, { kind: 1, offset: 3.3, noise: 0.005 }],
    cmds: ["MODE 4", "GAIN 0", "RUN 1"],
  },
  {
    name: "SPI on the pod", note: "Logic pod: CS, SCK, MOSI and MISO of a 1 MHz SPI bus. LOGIC mode samples all four lines at the scope's rate.",
    pod: { kind: 1, freq: 1e6 }, cmds: ["MODE 3", "TB 4", "RUN 1"],
  },
  {
    name: "Tips grounded (for CAL)", note: "Both tips on ground. This simulated unit reads about +47 mV on CH1 and −31 mV on CH2. Press CAL in the host app: the pen measures the offset, stores the correction in flash and applies it on both ranges. The firmware keeps one correction for both channels, so it removes their average (+8 mV).",
    sig: [{ kind: 0 }, { kind: 0 }], cmds: ["MODE 0", "GAIN 1", "TB 6", "RUN 1"],
  },
];

function applySignals() {
  if (!ex) return;
  sig.forEach((s, ch) => ex.sim_set_signal(ch, s.kind, s.vpp, s.offset, s.freq, s.duty, s.noise));
  ex.sim_set_pod(pod.kind, pod.freq);
}
function buildChannels() {
  const box = $("chans");
  box.innerHTML = "";
  sig.forEach((s, ch) => {
    const fs = document.createElement("fieldset");
    const col = ch ? "var(--ch2)" : "var(--ch1)";
    fs.innerHTML = `<legend><span class="sw" style="background:${col}"></span>CH${ch + 1} tip</legend>
      <label>Signal <select data-k="kind">${SIG_KINDS.map(([n, v]) => `<option value="${v}">${n}</option>`).join("")}</select></label>
      <label data-row="vpp"><span data-l="vpp">Peak to peak</span><input data-k="vpp" inputmode="decimal"></label>
      <label data-row="offset"><span data-l="offset">Offset</span><input data-k="offset" inputmode="decimal"></label>
      <label data-row="freq"><span data-l="freq">Frequency</span><input data-k="freq" inputmode="decimal"></label>
      <label data-row="duty">Duty %<input data-k="duty" inputmode="decimal"></label>
      <label data-row="noise">Noise rms<input data-k="noise" inputmode="decimal"></label>`;
    box.appendChild(fs);
    const field = (k) => fs.querySelector(`[data-k="${k}"]`);
    const refresh = () => {
      field("kind").value = s.kind;
      field("vpp").value = `${+s.vpp.toPrecision(4)} V`;
      field("offset").value = `${+s.offset.toPrecision(4)} V`;
      field("freq").value = s.kind === 7 ? `${eng(s.freq, "").trim()} baud` : eng(s.freq, "Hz");
      field("duty").value = `${+(s.duty * 100).toPrecision(3)}`;
      field("noise").value = eng(s.noise, "V");
      const k = s.kind;
      const show = { vpp: k >= 2, offset: k >= 1, freq: k >= 2, duty: k === 6, noise: k >= 1 };
      for (const r of Object.keys(show)) fs.querySelector(`[data-row="${r}"]`).style.display = show[r] ? "" : "none";
      fs.querySelector('[data-l="vpp"]').textContent = k === 6 || k === 7 ? "High − low" : "Peak to peak";
      fs.querySelector('[data-l="offset"]').textContent = k === 1 ? "Level" : k === 6 || k === 7 ? "Low level" : "Offset";
      fs.querySelector('[data-l="freq"]').textContent = k === 7 ? "Baud" : "Frequency";
    };
    s.refresh = refresh;
    refresh();
    fs.addEventListener("change", (e) => {
      const k = e.target.dataset.k;
      if (!k) return;
      if (k === "kind") s.kind = +e.target.value;
      else {
        let v = parseSI(e.target.value);
        if (k === "duty") v /= 100;
        if (isFinite(v)) s[k] = k === "freq" ? Math.max(0.01, v) : v;
      }
      refresh();
      applySignals();
      markPreset(-1);
    });
  });
  $("podKind").addEventListener("change", (e) => { pod.kind = +e.target.value; applySignals(); markPreset(-1); });
  $("podFreq").addEventListener("change", (e) => {
    const v = parseSI(e.target.value);
    if (v > 0) pod.freq = v;
    e.target.value = eng(pod.freq, "Hz");
    applySignals();
  });
}
function markPreset(i) {
  document.querySelectorAll(".presets button").forEach((b, j) => b.classList.toggle("on", i === j));
  $("presetNote").textContent = i >= 0 ? PRESETS[i].note : "Custom signals.";
}
function buildPresets() {
  const box = $("presets");
  PRESETS.forEach((p, i) => {
    const b = document.createElement("button");
    b.type = "button";
    b.textContent = p.name;
    b.addEventListener("click", () => applyPreset(i));
    box.appendChild(b);
  });
}
function applyPreset(i) {
  const p = PRESETS[i];
  (p.sig || []).forEach((s, ch) => Object.assign(sig[ch], { vpp: 0, offset: 0, freq: 1e3, duty: 0.5, noise: 0 }, s));
  if (p.pod) {
    Object.assign(pod, p.pod);
    $("podKind").value = pod.kind;
    $("podFreq").value = eng(pod.freq, "Hz");
  }
  sig.forEach((s) => s.refresh && s.refresh());
  applySignals();
  markPreset(i);
  note(`preset "${p.name}"`);
  (p.cmds || []).forEach((c) => send(c));
}

/* ---------- live status ---------- */
function updateNow() {
  const s = info();
  const scope = s.mode !== "LOGIC" && s.mode !== "METER";
  const rng = (r) => (r ? "±26 V (214 mV/step)" : "±5 V (39 mV/step)");
  const trigV = (s.trig_level - 128) * LSB[s.range[s.trig_src]];
  const rows = [
    ["Mode", `${s.mode}${s.run ? "" : " (held)"}`],
    ["Timebase", s.mode === "METER" ? "–" : `${tbLabel(s.tb_ns)}, ${eng(s.tb_ns * 1e-8, "s")} across the screen`],
    ["Sample rate", s.mode === "METER" ? "DC-sense ADC, 10 readings/s" : `${eng(s.fs, "S/s")} (75 MHz ÷ ${s.clkdiv})${scope ? ", both channels" : ", 4 pod lines"}`],
    ["Samples on screen", s.mode === "METER" ? "–" : `${s.screen}`],
    ["Record", s.mode === "METER" ? "–" : `${s.capture} samples per ${scope ? "channel" : "line"}, ${eng(s.capture / s.fs, "s")} of signal`],
    ["Peak detect", s.peak_m ? `on: ADC at 2.5 MS/s, min and max of every ${s.peak_m} samples (catches pulses ≥ 0.4 µs)` : "off (every sample kept)"],
    ["Ranges", `CH1 ${rng(s.range[0])}, CH2 ${rng(s.range[1])}${s.auto ? ", auto" : ", fixed"}`],
    ["Trigger", scope ? `CH${s.trig_src + 1} ${s.slope ? "falling" : "rising"}, level ${s.trig_level} = ${eng(trigV, "V")}, ` +
      `<span class="${s.triggered ? "yes" : "no"}">${s.triggered ? "found" : "not found (free-running)"}</span>` : "–"],
    ["Calibration", `zero offsets ${eng(s.off1, "V")} (±5 V), ${eng(s.off10, "V")} (±26 V); ${s.flash_writes} flash write${s.flash_writes === 1 ? "" : "s"}`],
  ];
  $("now").innerHTML = rows.map(([k, v]) => `<dt>${k}</dt><dd>${v}</dd>`).join("");
  syncHostControls(s);
}

/* ---------- host app: USB link ---------- */
let rx = new Uint8Array(0);
const pending = [];          // commands waiting for their reply line
let frameSink = null;        // where the next frames go: {want, frames, done}
let lastCap = null;          // {a, b, meas}
let lastMeas = null;

function logLine(cls, text) {
  const c = $("console");
  const div = document.createElement("div");
  div.className = cls;
  div.textContent = text;
  c.appendChild(div);
  while (c.childElementCount > 400) c.firstChild.remove();
  c.scrollTop = c.scrollHeight;
}
const note = (t) => logLine("note", `# ${t}`);

function send(line, onReply) {
  if (!ex) return;
  logLine("tx", `→ ${line}`);
  pending.push({ line, onReply });
  const bytes = new TextEncoder().encode(line.slice(0, 126));
  const m = new Uint8Array(ex.memory.buffer, ex.sim_usb_in_buf(), 128);
  m.set(bytes);
  m[bytes.length] = 0;
  ex.sim_usb_in_commit();
  pump();
}
function crc16(buf, n) {
  let crc = 0xffff;
  for (let i = 0; i < n; i++) {
    crc ^= buf[i] << 8;
    for (let k = 0; k < 8; k++) crc = crc & 0x8000 ? ((crc << 1) ^ 0x1021) & 0xffff : (crc << 1) & 0xffff;
  }
  return crc;
}
let pumping = false;
const inbox = [];   // parsed messages, handled after parsing so a handler may send() again
function pump() {
  if (pumping) return;
  pumping = true;
  try {
    for (;;) {
      const n = ex.sim_usb_out_len();
      if (n) {
        const chunk = new Uint8Array(ex.memory.buffer, ex.sim_usb_out(), n).slice();
        ex.sim_usb_out_clear();
        const merged = new Uint8Array(rx.length + chunk.length);
        merged.set(rx);
        merged.set(chunk, rx.length);
        rx = merged;
      }
      parse();
      if (!inbox.length) break;
      while (inbox.length) {
        const m = inbox.shift();
        if (m.line !== undefined) {
          onLine(m.line);
        } else {
          logLine("bin", `← frame CH${m.seq + 1}: ${m.samples.length} samples, ${m.total} bytes, CRC ${m.ok ? "ok" : "BAD"}`);
          if (m.ok) onFrame(m.seq, m.samples);
        }
      }
    }
  } finally {
    pumping = false;
  }
}
function parse() {
  for (;;) {
    if (!rx.length) return;
    if (rx[0] === 0xaa) {
      if (rx.length < 5) return;
      const count = rx[3] | (rx[4] << 8);
      const total = 7 + 2 * count;
      if (rx.length < total) return;
      const f = rx.slice(0, total);
      rx = rx.slice(total);
      const ok = crc16(f, total - 2) === (f[total - 2] | (f[total - 1] << 8));
      const samples = new Uint16Array(count);
      for (let i = 0; i < count; i++) samples[i] = f[5 + 2 * i] | (f[6 + 2 * i] << 8);
      inbox.push({ seq: f[2], samples, ok, total });
    } else {
      const i = rx.indexOf(10);
      if (i < 0) return;
      const line = new TextDecoder().decode(rx.subarray(0, i));
      rx = rx.slice(i + 1);
      inbox.push({ line });
    }
  }
}
function parseMeas(line) {
  const out = {};
  for (const part of line.trim().split(";")) {
    const w = part.trim().split(/\s+/);
    if (!w[0]) continue;
    if (w[0].includes("=")) {
      const [k, v] = w[0].split("=");
      out[k] = isNaN(+v) ? v : +v;
      continue;
    }
    out[w[0]] = {};
    for (const kv of w.slice(1)) {
      const [k, v] = kv.split("=");
      out[w[0]][k] = isNaN(+v) ? v : +v;
    }
  }
  return out;
}
function onLine(line) {
  logLine("rx", `← ${line}`);
  if (line.startsWith("ASK ")) {
    const meas = parseMeas(line.slice(4));
    frameSink = { frames: [], done: (fr) => addSnapshot(meas, fr) };
    return;
  }
  const p = pending.shift();
  if (p && p.onReply) p.onReply(line);
}
function onFrame(seq, samples) {
  if (!frameSink) return;
  frameSink.frames[seq] = samples;
  if (frameSink.frames[0] && frameSink.frames[1]) {
    const s = frameSink;
    frameSink = null;
    s.done(s.frames);
  }
}

/* ---------- host app: controls ---------- */
let syncing = false;
function buildHost() {
  $("hMode").innerHTML = MODES.map((m, i) => `<option value="${i}">${m}</option>`).join("");
  $("hTb").innerHTML = TB_NS.map((ns, i) => `<option value="${i}">${tbLabel(ns)}</option>`).join("");
  $("hMode").addEventListener("change", (e) => send(`MODE ${e.target.value}`));
  $("hTb").addEventListener("change", (e) => send(`TB ${e.target.value}`));
  $("hGain").addEventListener("change", (e) => send(`GAIN ${e.target.value}`));
  const trig = $("hTrig");
  trig.addEventListener("input", () => showTrig(+trig.value));
  trig.addEventListener("change", () => send(`TRIG ${trig.value}`));
  $("hRun").addEventListener("click", () => send(`RUN ${$("hRun").dataset.run === "1" ? 0 : 1}`));
  $("hCal").addEventListener("click", () => {
    note("CAL zeroes both ranges from the next capture: the tips must be on ground (preset \"Tips grounded\")");
    send("CAL");
  });
  $("hMeas").addEventListener("click", () => send("MEAS?", (l) => showMeas(parseMeas(l))));
  $("hCap").addEventListener("click", capture);
  $("hCsv").addEventListener("click", downloadCsv);
  $("cmdForm").addEventListener("submit", (e) => {
    e.preventDefault();
    const v = $("cmdLine").value.trim();
    if (v) send(v);
    $("cmdLine").value = "";
  });
  window.addEventListener("resize", () => lastCap && drawPlot($("plot"), lastCap, true));
}
let lastInfo = null;
function showTrig(code) {
  const r = lastInfo ? lastInfo.range[lastInfo.trig_src] : 0;
  $("hTrigOut").textContent = `${code} = ${eng((code - 128) * LSB[r], "V")}`;
}
function syncHostControls(s) {
  lastInfo = s;
  if (syncing) return;
  const act = document.activeElement;
  if (act !== $("hMode")) $("hMode").value = MODES.indexOf(s.mode);
  if (act !== $("hTb")) $("hTb").value = TB_NS.indexOf(s.tb_ns);
  if (act !== $("hGain")) $("hGain").value = s.auto ? "0" : s.range[0] ? "10" : "1";
  if (act !== $("hTrig")) {
    $("hTrig").value = s.trig_level;
    showTrig(s.trig_level);
  }
  $("hRun").textContent = s.run ? "Hold" : "Run";
  $("hRun").dataset.run = s.run ? "1" : "0";
}
function capture() {
  const n = Math.max(1, Math.min(4096, parseInt($("hCapN").value, 10) || 1024));
  $("hCapN").value = n;
  send(`CAPTURE ${n}`, (reply) => {
    if (!reply.startsWith("OK")) {
      note("CAPTURE needs a scope mode (CH1+2, CH1 or CH2)");
      return;
    }
    frameSink = {
      frames: [],
      done: (fr) => send("MEAS?", (l) => {
        const meas = parseMeas(l);
        lastCap = { a: fr[0], b: fr[1], meas };
        showMeas(meas);
        drawPlot($("plot"), lastCap, true);
        $("plotEmpty").style.display = "none";
        $("hCsv").disabled = false;
      }),
    };
  });
}
function showMeas(m) {
  lastMeas = m;
  const ch = (k, cls) => {
    const c = m[k];
    if (!c) return "";
    return `<span class="${cls}"><b class="${cls}">${k}</b> ${eng(c.VPP, "V")} p-p, ${eng(c.VMIN, "V")} to ${eng(c.VMAX, "V")}, ` +
      `mean ${eng(c.VAVG, "V")}, ${c.F > 0 ? eng(c.F, "Hz") : "no frequency"}, range ${c.RANGE === "10X" ? "±26 V" : "±5 V"}, DC ${eng(c.DC, "V")}</span>`;
  };
  $("meas").innerHTML = ch("CH1", "c1") + ch("CH2", "c2") +
    `<span>${eng(m.FS, "S/s")}, ${tbLabel(m.TB)}, trigger ${m.TRIG === "YES" ? "found" : "not found"}${m.OVR === "YES" ? ", OVERRUN" : ""}</span>`;
}
const codeToV = (code, range) => (code - 128) * LSB[range === "10X" ? 1 : 0];

function drawPlot(cv, cap, axes) {
  const dpr = window.devicePixelRatio || 1;
  const w = cv.clientWidth, h = cv.clientHeight;
  cv.width = Math.round(w * dpr);
  cv.height = Math.round(h * dpr);
  const g = cv.getContext("2d");
  g.setTransform(dpr, 0, 0, dpr, 0, 0);
  g.fillStyle = "#07090d";
  g.fillRect(0, 0, w, h);
  const r1 = cap.meas.CH1 ? cap.meas.CH1.RANGE : "1X", r2 = cap.meas.CH2 ? cap.meas.CH2.RANGE : "1X";
  const span = r1 === "10X" || r2 === "10X" ? 27.5 : 5.1;
  const L = axes ? 52 : 4, R = 6, T = 8, B = axes ? 22 : 4;
  const pw = w - L - R, ph = h - T - B;
  const n = cap.a.length;
  g.strokeStyle = "#1c2330";
  g.lineWidth = 1;
  for (let i = 0; i <= 10; i++) {
    const x = L + (pw * i) / 10;
    g.beginPath(); g.moveTo(x, T); g.lineTo(x, T + ph); g.stroke();
  }
  for (let j = 0; j <= 8; j++) {
    const y = T + (ph * j) / 8;
    g.beginPath(); g.moveTo(L, y); g.lineTo(L + pw, y); g.stroke();
  }
  g.strokeStyle = "#33405a";
  g.beginPath(); g.moveTo(L, T + ph / 2); g.lineTo(L + pw, T + ph / 2); g.stroke();
  const y = (v) => T + ph / 2 - (v / span) * (ph / 2);
  const trace = (arr, range, col) => {
    g.strokeStyle = col;
    g.lineWidth = 1.4;
    g.beginPath();
    for (let i = 0; i < n; i++) {
      const x = L + (pw * i) / Math.max(1, n - 1);
      const yy = y(codeToV(arr[i], range));
      i ? g.lineTo(x, yy) : g.moveTo(x, yy);
    }
    g.stroke();
  };
  trace(cap.a, r1, "#ffd21f");
  trace(cap.b, r2, "#3fc8e6");
  if (axes) {
    g.fillStyle = "#8b93a3";
    g.font = "11px system-ui, sans-serif";
    g.textAlign = "right";
    for (let j = 0; j <= 4; j++) {
      const v = span - (j * span) / 2;
      g.fillText(eng(v, "V", 2), L - 6, y(v) + 4);
    }
    g.textAlign = "center";
    const dur = n / (cap.meas.FS || 1);
    for (let i = 0; i <= 10; i += 2) g.fillText(eng((dur * i) / 10, "s", 3), L + (pw * i) / 10, h - 6);
  }
}
function downloadCsv() {
  if (!lastCap) return;
  const { a, b, meas } = lastCap;
  const fs = meas.FS || 1;
  const r1 = meas.CH1 ? meas.CH1.RANGE : "1X", r2 = meas.CH2 ? meas.CH2.RANGE : "1X";
  let s = "t_s,ch1_code,ch1_v,ch2_code,ch2_v\n";
  for (let i = 0; i < a.length; i++) {
    s += `${(i / fs).toExponential(6)},${a[i]},${codeToV(a[i], r1).toFixed(4)},${b[i]},${codeToV(b[i], r2).toFixed(4)}\n`;
  }
  save(new Blob([s], { type: "text/csv" }), "probepen-capture.csv");
}
function save(blob, name) {
  const u = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = u;
  a.download = name;
  a.click();
  setTimeout(() => URL.revokeObjectURL(u), 1000);
}
function addSnapshot(meas, fr) {
  const box = $("snaps");
  box.querySelector(".snaps-empty")?.remove();
  const el = document.createElement("div");
  el.className = "snap";
  const t = new Date().toLocaleTimeString();
  const c1 = meas.CH1 || {}, c2 = meas.CH2 || {};
  el.innerHTML = `<div class="row"><b>${t}</b><span>${eng(meas.FS, "S/s")}, ${tbLabel(meas.TB)}</span></div>
    <canvas></canvas>
    <div>CH1 ${eng(c1.VPP, "V")} p-p ${c1.F > 0 ? eng(c1.F, "Hz") : ""} · CH2 ${eng(c2.VPP, "V")} p-p ${c2.F > 0 ? eng(c2.F, "Hz") : ""}</div>
    <div class="row"><span class="sub">${fr[0].length} samples per channel</span><button type="button">JSON</button></div>`;
  box.prepend(el);
  const cap = { a: fr[0], b: fr[1], meas };
  requestAnimationFrame(() => drawPlot(el.querySelector("canvas"), cap, false));
  el.querySelector("button").addEventListener("click", () => save(new Blob([JSON.stringify({
    time: new Date().toISOString(), meas, ch1: Array.from(fr[0]), ch2: Array.from(fr[1]),
  })], { type: "application/json" }), "probepen-snapshot.json"));
  note("snapshot received: the real host app would pass it to an AI model with the question");
}

/* ---------- main loop ---------- */
let lastInfoT = 0;
function frame(t) {
  ex.sim_set_buttons(buttons);
  if (ex.sim_step(t)) blit();
  pump();
  if (t - lastInfoT > 200) {
    updateNow();
    lastInfoT = t;
  }
  requestAnimationFrame(frame);
}

async function main() {
  buildPad();
  buildPresets();
  buildChannels();
  buildHost();
  $("podFreq").value = eng(pod.freq, "Hz");
  $("snaps").innerHTML = '<p class="snaps-empty">None yet.</p>';
  placePen();
  try {
    await loadWasm();
  } catch (e) {
    $("status").textContent = `Could not load the firmware: ${e.message}`;
    $("status").className = "status err";
    return;
  }
  applySignals();
  markPreset(0);
  $("status").textContent = "Firmware running";
  $("status").className = "status ok";
  send("*IDN?", (l) => {
    $("hostPort").textContent = `connected: ${l}`;
  });
  requestAnimationFrame(frame);
}
main();
