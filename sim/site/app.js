/* ProbePen-AI simulator page: runs probepen.wasm (the firmware UI plus sim/sim.c), shows its framebuffer
 * on the pen and enlarged, feeds it buttons and probe signals, and plays the host app over its USB
 * protocol (one text command per line, binary wave frames: AA 55 seq count samples crc). */
"use strict";

const W = 240, H = 135;
const MODES = ["CH1+2", "CH1", "CH2", "LOGIC", "METER", "DECODE"];
const TB_NS = [100, 200, 500, 1e3, 2e3, 5e3, 1e4, 2e4, 5e4, 1e5, 2e5, 5e5, 1e6, 2e6, 5e6, 1e7, 2e7, 5e7];
const GAIN = [9.803, 53.56];                      // tip volts per ADC volt, +-5 V and +-26 V ranges
const LSB = GAIN.map((g) => (g * 1.024) / 256);   // volts per code
const SIG_KINDS = [
  ["Grounded (0 V)", 0], ["DC", 1], ["Sine", 2], ["Square", 3], ["Triangle", 4], ["Sawtooth", 5],
  ["Pulse", 6], ["UART 8N1 text", 7], ["AM sine", 8], ["CAN FD, CAN_H wire", 9], ["CAN FD, CAN_L wire", 10],
];
const BTN = ["ASK", "MODE", "UP", "DOWN"];
const KEYS = { a: 0, m: 1, ArrowUp: 2, ArrowDown: 3 };

const $ = (id) => document.getElementById(id);
/* the firmware build: 150 MHz (stock, ADC to 75 MS/s) or 250 MHz (overclocked, ADC to 125 MS/s), from ?mhz= */
const MHZ = new URLSearchParams(location.search).get("mhz") === "250" ? 250 : 150;
const ADC_MAX = (MHZ * 1e6) / 2;
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
  const resp = await fetch(MHZ === 150 ? "probepen.wasm" : `probepen-${MHZ}.wasm`);
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
  { kind: 2, vpp: 3, offset: 0, freq: 50e3, duty: 0.5, noise: 0.01, rate2: 2e6 },
  { kind: 3, vpp: 3.3, offset: 1.65, freq: 25e3, duty: 0.5, noise: 0.005, rate2: 2e6 },
];
const pod = { kind: 0, freq: 1e6 };
const PRESETS = [
  {
    name: "Sine + clock", note: "CH1: 3 Vpp 50 kHz sine. CH2: 3.3 V 25 kHz clock. Both channels at 5 MS/s, 10 µs/div.",
    sig: [{ kind: 2, vpp: 3, offset: 0, freq: 50e3, noise: 0.01 }, { kind: 3, vpp: 3.3, offset: 1.65, freq: 25e3, noise: 0.005 }],
    cmds: ["MODE 0", "GAIN 0", "TB 6", "TRIG 140", "RUN 1"],
  },
  {
    name: "UART 115200", note: "CH1: 3.3 V serial line sending \"Hello world 0000\\r\\n\" (the number counts up) at 115200 baud, 8N1. Press Start under Decode: the pen decodes every byte live at 926 kS/s and prints the text, on its screen and below.",
    sig: [{ kind: 7, vpp: 3.3, offset: 0, freq: 115200, noise: 0.01 }, { kind: 0 }],
    cmds: ["MODE 1", "GAIN 1", "TB 8", "TRIG 150", "RUN 1"],
    dec: { proto: "UART", src: 1, rate: "115200" },
  },
  {
    name: "UART 1 Mbaud", note: "CH1: the same text at 1 Mbaud. Decoding needs 8 samples per bit, 8.3 MS/s: still live, every byte.",
    sig: [{ kind: 7, vpp: 3.3, offset: 0, freq: 1e6, noise: 0.01 }, { kind: 0 }],
    cmds: ["MODE 1", "GAIN 1", "TB 5", "TRIG 150", "RUN 1"],
    dec: { proto: "UART", src: 1, rate: "1M" },
  },
  {
    name: "12 V PWM", note: "CH1: 12 V, 20 kHz, 30 % duty motor PWM. Auto-range moves CH1 to the ±26 V range (214 mV per step). Press MODE until METER: DC level, AC RMS, and the pulse widths (+W 15 µs, −W 35 µs, duty 30 %).",
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
    name: "12 V rail (meter)", note: "CH1: 12 V supply with 0.2 V of 100 Hz ripple, CH2: 3.3 V. METER mode shows each channel's DC level from the 12-bit DC-sense ADC, and from the scope capture its AC RMS (the ripple: about 71 mV), peak to peak, frequency and pulse widths. UP lengthens the window: a whole 100 Hz period needs 20 ms/div or more.",
    sig: [{ kind: 2, vpp: 0.2, offset: 12, freq: 100, noise: 0.02 }, { kind: 1, offset: 3.3, noise: 0.005 }],
    cmds: ["MODE 4", "GAIN 0", "TB 15", "RUN 1"],
  },
  {
    name: "CAN FD 500k / 2M", note: "CH1 = CAN_H, CH2 = CAN_L: a CAN FD frame every 1 ms, ID 123, \"Hello world 0000\" (16 bytes), arbitration at 500 kbit/s and the data phase at 2 Mbit/s, fully encoded (stuffing, CRC-17). Decode CH1 − CH2: 18.75 MS/s is beyond live decoding, so the pen takes bursts of 164 k samples (8.7 ms) and decodes each; about 44 % of frames are caught.",
    sig: [{ kind: 9, freq: 500e3, rate2: 2e6, noise: 0.02 }, { kind: 10, freq: 500e3, rate2: 2e6, noise: 0.02 }],
    cmds: ["MODE 0", "GAIN 1", "TB 4", "TRIG 204", "RUN 1"],
    dec: { proto: "CAN", src: 3, rate: "500k", data: "2M" },
  },
  {
    name: "CAN FD 1M / 5M", note: "The same frames with arbitration at 1 Mbit/s and data at 5 Mbit/s. Decoding samples at 25 MS/s (5 per data bit) in bursts.",
    sig: [{ kind: 9, freq: 1e6, rate2: 5e6, noise: 0.02 }, { kind: 10, freq: 1e6, rate2: 5e6, noise: 0.02 }],
    cmds: ["MODE 0", "GAIN 1", "TB 3", "TRIG 204", "RUN 1"],
    dec: { proto: "CAN", src: 3, rate: "1M", data: "5M" },
  },
  {
    name: "CAN FD 500k, no BRS", note: "CAN FD frames without bit-rate switching: the whole frame at 500 kbit/s. 4.2 MS/s is decoded live: every frame.",
    sig: [{ kind: 9, freq: 500e3, rate2: 500e3, noise: 0.02 }, { kind: 10, freq: 500e3, rate2: 500e3, noise: 0.02 }],
    cmds: ["MODE 0", "GAIN 1", "TB 5", "TRIG 204", "RUN 1"],
    dec: { proto: "CAN", src: 3, rate: "500k", data: "500k" },
  },
  {
    name: "SPI on the pod", note: "Logic pod: CS, SCK, MOSI and MISO of a 1 MHz SPI bus. LOGIC mode samples all four lines at the scope's rate.",
    pod: { kind: 1, freq: 1e6 }, cmds: ["MODE 3", "TB 4", "RUN 1"],
  },
  {
    name: "Tips grounded (for CAL)", note: "Both tips on ground. This simulated unit reads about +47 mV on CH1 and −31 mV on CH2. Press CAL in the host app: the pen measures each channel's offset, stores the corrections in flash and applies them on both ranges.",
    sig: [{ kind: 0 }, { kind: 0 }], cmds: ["MODE 0", "GAIN 1", "TB 6", "RUN 1"],
  },
];

function applySignals() {
  if (!ex) return;
  sig.forEach((s, ch) => {
    ex.sim_set_signal(ch, s.kind, s.vpp, s.offset, s.freq, s.duty, s.noise);
    ex.sim_set_bitrate(ch, s.rate2 || 0);
  });
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
      <label data-row="rate2">Data rate<input data-k="rate2" inputmode="decimal"></label>
      <label data-row="duty">Duty %<input data-k="duty" inputmode="decimal"></label>
      <label data-row="noise">Noise rms<input data-k="noise" inputmode="decimal"></label>`;
    box.appendChild(fs);
    const field = (k) => fs.querySelector(`[data-k="${k}"]`);
    const refresh = () => {
      field("kind").value = s.kind;
      field("vpp").value = `${+s.vpp.toPrecision(4)} V`;
      field("offset").value = `${+s.offset.toPrecision(4)} V`;
      field("freq").value = s.kind === 7 ? `${eng(s.freq, "").trim()} baud` : s.kind >= 9 ? eng(s.freq, "bit/s") : eng(s.freq, "Hz");
      field("duty").value = `${+(s.duty * 100).toPrecision(3)}`;
      field("noise").value = eng(s.noise, "V");
      field("rate2").value = eng(s.rate2 || 4 * s.freq, "bit/s");
      const k = s.kind;
      const can = k >= 9;
      const show = { vpp: k >= 2 && !can, offset: k >= 1 && !can, freq: k >= 2, rate2: can, duty: k === 6, noise: k >= 1 };
      for (const r of Object.keys(show)) fs.querySelector(`[data-row="${r}"]`).style.display = show[r] ? "" : "none";
      fs.querySelector('[data-l="vpp"]').textContent = k === 6 || k === 7 ? "High − low" : "Peak to peak";
      fs.querySelector('[data-l="offset"]').textContent = k === 1 ? "Level" : k === 6 || k === 7 ? "Low level" : "Offset";
      fs.querySelector('[data-l="freq"]').textContent = k === 7 ? "Baud" : can ? "Arbitration" : "Frequency";
    };
    s.refresh = refresh;
    refresh();
    fs.addEventListener("change", (e) => {
      const k = e.target.dataset.k;
      if (!k) return;
      if (k === "kind") {
        const was = s.kind;
        s.kind = +e.target.value;
        if (s.kind !== was) Object.assign(s, KIND_DEFAULTS[s.kind] || {});   /* a sine's 50 kHz is no baud rate */
      } else {
        let v = parseSI(e.target.value);
        if (k === "duty") v /= 100;
        if (isFinite(v)) s[k] = k === "freq" ? Math.max(0.01, v) : k === "rate2" ? Math.max(s.freq, v) : v;
      }
      refresh();
      applySignals();
      markPreset(-1);
      decodeFollowSignal(ch);
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
  (p.sig || []).forEach((s, ch) => Object.assign(sig[ch], { vpp: 0, offset: 0, freq: 1e3, duty: 0.5, noise: 0, rate2: 0 }, s));
  if (p.dec) setDecodeControls(p.dec);
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
  decodeStats(s);
  if (s.decoding) {
    const src = ["", "CH1", "CH2", "CH1 − CH2"][s.dec_src];
    const proto = s.dec_proto === 1 ? `UART 8N1, ${eng(s.dec_rate, "").trim()} baud`
      : `CAN${s.dec_data > s.dec_rate ? " FD" : ""}, ${eng(s.dec_rate, "bit/s")}${s.dec_data > s.dec_rate ? ` / ${eng(s.dec_data, "bit/s")} data` : ""}`;
    $("now").innerHTML = [
      ["Mode", `Decoding ${proto} from ${src}`],
      ["Samples", `${eng(s.dec_fs, "S/s")} (${eng(s.adc_max, "Hz")} ÷ ${s.dec_div}), ${s.dec_spb.toFixed(1)} per ${s.dec_proto === 2 && s.dec_data > s.dec_rate ? "data " : ""}bit`],
      ["How", s.dec_burst
        ? `bursts: beyond the ${eng(s.dec_live_max, "S/s")} one core can decode as it comes (estimate at ${MHZ} MHz), so the pen takes ${s.dec_burst_n.toLocaleString()} samples (${eng(s.dec_burst_n / s.dec_fs, "s")}), decodes them, and takes the next: about <b>${Math.round(s.dec_cover * 100)} %</b> of the time is seen`
        : `live: every sample, without gaps (up to ${eng(s.dec_live_max, "S/s")} at ${MHZ} MHz, an estimate)`],
      ["Edges", !s.dec_edges ? "none yet" : s.dec_min_run <= 4 ? `${s.dec_edges.toLocaleString()}, some faster than this sample rate resolves`
        : `${s.dec_edges.toLocaleString()}, the shortest about one bit at ${eng(s.dec_seen, "bit/s")}`],
      ["Decoded", `${s.dec_records} ${s.dec_proto === 1 ? "lines" : "frames"}, <span class="${s.dec_errors ? "no" : "yes"}">${s.dec_errors} with errors</span>, ${s.dec_lost} gaps`],
      ["Simulation", s.dec_speed < 0.98 ? `the browser makes ${eng(s.dec_speed * s.dec_fs, "S/s")}, so the simulated bus runs at ${Math.round(s.dec_speed * 100)} % of real time` : "real time"],
      ["Ranges", `CH1 ${rng(s.range[0])}, CH2 ${rng(s.range[1])}, fixed while decoding`],
    ].map(([k, v]) => `<dt>${k}</dt><dd>${v}</dd>`).join("");
    syncHostControls(s);
    return;
  }
  if (s.streaming) {
    const nch = st.mask === 3 ? 2 : 1;
    $("now").innerHTML = [
      ["Mode", "USB stream"],
      ["Samples", `${eng(s.stream_rate, "S/s")} per channel, ${nch === 2 ? "CH1 and CH2" : `CH${st.mask}`}, 8 bits`],
      ["ADC", `${eng(s.stream_raw, "S/s")} (${eng(ADC_MAX, "Hz")} ÷ ${Math.round(ADC_MAX / s.stream_raw)})${s.stream_avg > 1 ? `, average of ${s.stream_avg} per sample` : ""}`],
      ["Needs", `${eng(s.stream_rate * nch, "B/s")} of samples plus 12 bytes per 1 KB block`],
      ["USB sends", `${eng(s.stream_bps, "B/s")} (link set to ${eng(s.usb_budget, "B/s")})`],
      ["Losses", `<span class="${s.stream_lost ? "no" : "yes"}">${s.stream_lost} gap${s.stream_lost === 1 ? "" : "s"}</span>`],
      ["Ranges", `CH1 ${rng(s.range[0])}, CH2 ${rng(s.range[1])}, fixed while streaming`],
    ].map(([k, v]) => `<dt>${k}</dt><dd>${v}</dd>`).join("");
    syncHostControls(s);
    return;
  }
  const rows = [
    ["Mode", `${s.mode}${s.run ? "" : " (held)"}`],
    ["Timebase", s.mode === "METER" ? "–" : `${tbLabel(s.tb_ns)}, ${eng(s.tb_ns * 1e-8, "s")} across the screen`],
    ["Clock", `RP2354B at ${MHZ} MHz${MHZ > 150 ? " (overclocked)" : ""}: ADC up to ${eng(s.adc_max, "S/s")}${s.adc_max > 80e6 ? ", beyond the AD9288-80's 80 MS/s rating" : ""}`],
    ["Sample rate", s.mode === "METER" ? `DC: DC-sense ADC, 10 readings/s. AC RMS and pulse widths: ${eng(s.fs, "S/s")} scope capture` : `${eng(s.fs, "S/s")} (${eng(s.adc_max, "Hz")} ÷ ${s.clkdiv})${scope ? ", both channels" : ", 4 pod lines"}`],
    ["Samples on screen", s.mode === "METER" ? "–" : `${s.screen}`],
    ["Record", s.mode === "METER" ? "–" : `${s.capture} samples per ${scope ? "channel" : "line"}, ${eng(s.capture / s.fs, "s")} of signal`],
    ["Peak detect", s.peak_m ? `on: ADC at ${eng(s.adc_max / 30, "S/s")}, min and max of every ${s.peak_m} samples (catches pulses ≥ ${eng(30 / s.adc_max, "s")})` : "off (every sample kept)"],
    ["Ranges", `CH1 ${rng(s.range[0])}, CH2 ${rng(s.range[1])}${s.auto ? ", auto" : ", fixed"}`],
    ["Trigger", scope ? `CH${s.trig_src + 1} ${s.slope ? "falling" : "rising"}, level ${s.trig_level} = ${eng(trigV, "V")}, ` +
      `<span class="${s.triggered ? "yes" : "no"}">${s.triggered ? "found" : "not found (free-running)"}</span>` : "–"],
    ["Calibration", `zero corrections CH1 ${eng(s.off1[0], "V")}, CH2 ${eng(s.off1[1], "V")} (±5 V range); ` +
      `${s.flash_writes} flash write${s.flash_writes === 1 ? "" : "s"}`],
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
        if (m.block) {
          onBlock(m);
        } else if (m.line !== undefined) {
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
    if (rx[0] === 0xa5) {
      if (rx.length < 10) return;
      if (rx[1] !== 0x5a) { rx = rx.slice(1); continue; }
      const len = rx[4] | (rx[5] << 8);
      const total = 12 + len;
      if (rx.length < total) return;
      const b = rx.slice(0, total);
      rx = rx.slice(total);
      if (crc16(b, total - 2) !== (b[total - 2] | (b[total - 1] << 8))) { st.crc++; continue; }
      const first = (b[6] | (b[7] << 8) | (b[8] << 16) | (b[9] << 24)) >>> 0;
      inbox.push({ block: true, flags: b[2], chmask: b[3], first, payload: b.subarray(10, 10 + len), total });
      continue;
    }
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
  if (line.startsWith("DEC ")) {        /* unsolicited: one decoded line or frame */
    if (!dc.on) {                        /* started on the pen (DECODE mode) */
      dc.on = true;
      $("dcStart").textContent = "Stop";
      $("termWrap").hidden = false;
    }
    onDecoded(line);
    return;
  }
  logLine("rx", `← ${line}`);
  if (line.startsWith("DECODE END")) {   /* the pen may start again at once (UP / DOWN, auto-baud) */
    dc.on = false;
    $("dcStart").textContent = "Start";
    return;
  }
  if (line.startsWith("DEEP DATA")) {    /* unsolicited: the record follows as blocks */
    const kv = {};
    line.split(/\s+/).slice(2).forEach((w) => { const [k, v] = w.split("="); kv[k] = +v; });
    dp.trig = kv.TRIG;
    dp.receiving = true;
    return;
  }
  if (line.startsWith("DEEP END")) {
    dp.receiving = false;
    deepDone();
    return;
  }
  if (line.startsWith("STREAM END")) {   /* unsolicited, after the stream's last block */
    st.on = false;
    $("sStart").textContent = "Start";
    return;
  }
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

/* ---------- host app: streaming ---------- */
const CAP = 262144;   // samples kept per channel for the chart and CSV
const st = {
  on: false, plan: null, mask: 3, a: new Uint8Array(CAP), b: new Uint8Array(CAP), w: 0, n: 0,
  next: null, lost: 0, lostFlags: 0, crc: 0, samples: 0, bytes: 0, blocks: 0,
  t0: 0, lastT: 0, lastBytes: 0, lastSamples: 0, bps: 0, sps: 0,
};
function onBlock(m) {
  if (dp.receiving) {
    deepBlock(m);
    return;
  }
  const nch = m.chmask === 3 ? 2 : 1;
  const ns = m.payload.length / nch;
  if (st.next !== null && m.first > st.next) st.lost += m.first - st.next;
  st.next = m.first + ns;
  st.lostFlags += m.flags & 1;
  st.bytes += m.total;
  st.blocks++;
  st.samples += ns;
  for (let i = 0; i < ns; i++) {
    if (m.chmask === 3) {
      st.a[st.w] = m.payload[2 * i];
      st.b[st.w] = m.payload[2 * i + 1];
    } else if (m.chmask === 1) {
      st.a[st.w] = m.payload[i];
    } else {
      st.b[st.w] = m.payload[i];
    }
    st.w = (st.w + 1) % CAP;
  }
  st.n = Math.min(CAP, st.n + ns);
}
function streamStart() {
  if (st.on) {
    send("STREAM 0");
    return;
  }
  const rate = parseSI($("sRate").value);
  const ch = +$("sCh").value;
  if (!(rate > 0)) {
    note("stream rate: a number such as 100k or 1M (samples per second per channel)");
    return;
  }
  ex.sim_set_usb_budget(+$("sBudget").value);
  send(`STREAM ${rate} ${ch}`, (reply) => {
    if (!reply.startsWith("OK")) {
      note("the pen refused that stream rate (above 75 MS/s?)");
      return;
    }
    const kv = {};
    reply.split(/\s+/).slice(1).forEach((w) => { const [k, v] = w.split("="); kv[k] = +v; });
    Object.assign(st, {
      on: true, plan: kv, mask: ch, w: 0, n: 0, next: null, lost: 0, lostFlags: 0, crc: 0, samples: 0, bytes: 0,
      blocks: 0, t0: performance.now(), lastT: performance.now(), lastBytes: 0, lastSamples: 0, bps: 0, sps: 0,
    });
    $("sStart").textContent = "Stop";
    $("sCsv").disabled = false;
    $("plotEmpty").style.display = "none";
    const need = kv.BPS;
    note(`streaming ${eng(kv.RATE, "S/s")} per channel (ADC ${eng(kv.RAW, "S/s")}, average of ${kv.AVG}): ` +
      `${eng(need, "B/s")} of samples for a ${eng(+$("sBudget").value, "B/s")} link` +
      (need > +$("sBudget").value ? " — more than the link carries, so samples will be lost" : ""));
  });
}
function streamStats(t) {
  if (!st.on || t - st.lastT < 400) return;
  const dt = (t - st.lastT) / 1000;
  st.bps = (st.bytes - st.lastBytes) / dt;
  st.sps = (st.samples - st.lastSamples) / dt;
  st.lastT = t;
  st.lastBytes = st.bytes;
  st.lastSamples = st.samples;
  const lostPct = st.samples + st.lost ? (100 * st.lost) / (st.samples + st.lost) : 0;
  $("sStats").innerHTML = `${eng(st.bps, "B/s")} on USB · ${eng(st.sps, "S/s")} per channel arriving · ` +
    `${st.samples.toLocaleString()} samples · ` +
    `<span class="${st.lost ? "bad" : "good"}">${st.lost ? `${st.lost.toLocaleString()} lost (${lostPct.toFixed(1)} %)` : "none lost"}</span>` +
    (st.crc ? ` · <span class="bad">${st.crc} CRC errors</span>` : "");
}
function streamVolts(code, ch) {
  const lsb = st.plan ? st.plan[ch ? "LSB2" : "LSB1"] : LSB[0];
  const zero = st.plan ? st.plan[ch ? "ZERO2" : "ZERO1"] : 128;
  return (code - zero) * lsb;
}
let lastStreamDraw = 0;
function drawStream(t) {
  if (!st.on || t - lastStreamDraw < 66 || !st.n) return;
  lastStreamDraw = t;
  const cv = $("plot");
  const dpr = window.devicePixelRatio || 1;
  const w = cv.clientWidth, h = cv.clientHeight;
  cv.width = Math.round(w * dpr);
  cv.height = Math.round(h * dpr);
  const g = cv.getContext("2d");
  g.setTransform(dpr, 0, 0, dpr, 0, 0);
  g.fillStyle = "#07090d";
  g.fillRect(0, 0, w, h);
  const rate = st.plan.RATE;
  const win = Math.max(2, Math.min(st.n, Math.round(rate * +$("sWin").value)));   // the chosen window
  const L = 52, R = 6, T = 8, B = 22, pw = w - L - R, ph = h - T - B;
  const r1 = st.plan.LSB1 > 0.1 ? 27.5 : 5.1, r2 = st.plan.LSB2 > 0.1 ? 27.5 : 5.1;
  const span = Math.max(st.mask & 1 ? r1 : 0, st.mask & 2 ? r2 : 0);
  g.strokeStyle = "#1c2330";
  for (let i = 0; i <= 10; i++) { const x = L + (pw * i) / 10; g.beginPath(); g.moveTo(x, T); g.lineTo(x, T + ph); g.stroke(); }
  for (let j = 0; j <= 8; j++) { const y = T + (ph * j) / 8; g.beginPath(); g.moveTo(L, y); g.lineTo(L + pw, y); g.stroke(); }
  const y = (v) => T + ph / 2 - (v / span) * (ph / 2);
  const cols = Math.max(1, Math.floor(pw));
  const draw = (buf, ch, col) => {
    g.strokeStyle = col;
    g.lineWidth = 1.4;
    g.globalAlpha = 0.85;
    g.beginPath();
    if (win <= cols) {           // fewer samples than pixels: a line through them
      for (let i = 0; i < win; i++) {
        const x = L + (pw * i) / Math.max(1, win - 1);
        const yy = y(streamVolts(buf[(st.w - win + i + CAP) % CAP], ch));
        i ? g.lineTo(x, yy) : g.moveTo(x, yy);
      }
    } else {                     // more: each pixel column shows the min-max of its samples
      let prevLo = null;
      for (let c = 0; c < cols; c++) {
        const i0 = Math.floor((c * win) / cols), i1 = Math.max(i0 + 1, Math.floor(((c + 1) * win) / cols));
        let lo = 255, hi = 0;
        for (let i = i0; i < i1; i++) {
          const v = buf[(st.w - win + i + CAP) % CAP];
          if (v < lo) lo = v;
          if (v > hi) hi = v;
        }
        if (prevLo !== null) { hi = Math.max(hi, prevLo); lo = Math.min(lo, prevLo); }   // join neighbouring columns
        const x = L + c + 0.5;
        g.moveTo(x, y(streamVolts(hi, ch)));
        g.lineTo(x, y(streamVolts(lo, ch)) + 0.5);
        prevLo = buf[(st.w - win + i1 - 1 + CAP) % CAP];
      }
    }
    g.stroke();
    g.globalAlpha = 1;
  };
  if (st.mask & 2) draw(st.b, 1, "#3fc8e6");
  if (st.mask & 1) draw(st.a, 0, "#ffd21f");
  g.fillStyle = "#8b93a3";
  g.font = "11px system-ui, sans-serif";
  g.textAlign = "right";
  for (let j = 0; j <= 4; j++) { const v = span - (j * span) / 2; g.fillText(eng(v, "V", 2), L - 6, y(v) + 4); }
  g.textAlign = "center";
  const dur = win / rate;
  for (let i = 0; i <= 10; i += 2) g.fillText(`−${eng(dur * (1 - i / 10), "s", 3)}`, L + (pw * i) / 10, h - 6);
}
function streamCsv() {
  const n = st.n, rate = st.plan ? st.plan.RATE : 1;
  let out = "t_s" + (st.mask & 1 ? ",ch1_v" : "") + (st.mask & 2 ? ",ch2_v" : "") + "\n";
  for (let i = 0; i < n; i++) {
    const k = (st.w - n + i + CAP) % CAP;
    out += (i / rate).toExponential(6);
    if (st.mask & 1) out += "," + streamVolts(st.a[k], 0).toFixed(4);
    if (st.mask & 2) out += "," + streamVolts(st.b[k], 1).toFixed(4);
    out += "\n";
  }
  save(new Blob([out], { type: "text/csv" }), "probepen-stream.csv");
}

/* ---------- host app: deep record ---------- */
const dp = { plan: null, mask: 3, a: null, b: null, got: 0, trig: -1, receiving: false, t0: 0, bytes: 0 };
function deepStart() {
  if (st.on) {
    note("stop the stream first");
    return;
  }
  const ch = +$("dCh").value, rate = +$("dRate").value;
  send(`DEEP 0 ${ch} ${rate}`, (reply) => {
    if (!reply.startsWith("OK")) {
      note("the pen refused the deep record");
      return;
    }
    const kv = {};
    reply.split(/\s+/).slice(1).forEach((w) => { const [k, v] = w.split("="); kv[k] = +v; });
    Object.assign(dp, { plan: kv, mask: ch, a: new Uint8Array(kv.N), b: new Uint8Array(kv.N), got: 0, trig: -1,
      t0: performance.now(), bytes: 0 });
    $("dStats").textContent = `recording ${kv.N.toLocaleString()} samples per channel (${eng(kv.SECONDS, "s")} of signal)…`;
  });
}
function deepBlock(m) {
  const nch = m.chmask === 3 ? 2 : 1;
  const ns = m.payload.length / nch;
  for (let i = 0; i < ns; i++) {
    const k = m.first + i;
    if (m.chmask === 3) {
      dp.a[k] = m.payload[2 * i];
      dp.b[k] = m.payload[2 * i + 1];
    } else if (m.chmask === 1) {
      dp.a[k] = m.payload[i];
    } else {
      dp.b[k] = m.payload[i];
    }
  }
  dp.got += ns;
  dp.bytes += m.total;
}
function deepDone() {
  const n = dp.plan.N, nch = dp.mask === 3 ? 2 : 1;
  const usbS = dp.bytes / (+$("sBudget").value || 1e6);   // what a real full-speed link would take
  $("dStats").innerHTML = `${dp.got.toLocaleString()} of ${n.toLocaleString()} samples per channel · ` +
    `${eng(n / dp.plan.RATE, "s")} at ${eng(dp.plan.RATE, "S/s")} · ${eng(dp.bytes, "B")} over USB ` +
    `(about ${eng(usbS, "s")} on a ${eng(+$("sBudget").value, "B/s")} link) · ` +
    (dp.trig >= 0 ? `trigger at sample ${dp.trig.toLocaleString()}` : `<span class="bad">no trigger in the record</span>`);
  $("dCsv").disabled = false;
  $("plotEmpty").style.display = "none";
  note(`deep record: ${n.toLocaleString()} samples per channel, ${nch * n} bytes of samples`);
  drawDeep();
}
function deepVolts(code, ch) {
  return (code - dp.plan[ch ? "ZERO2" : "ZERO1"]) * dp.plan[ch ? "LSB2" : "LSB1"];
}
function drawDeep() {
  if (!dp.plan || dp.got === 0) return;
  const n = dp.plan.N;
  let i0 = 0, i1 = n;
  if ($("dView").value === "trig" && dp.trig >= 0) {
    const half = 1500;
    i0 = Math.max(0, dp.trig - half / 2);
    i1 = Math.min(n, i0 + half * 1.5);
  }
  const cv = $("plot");
  const dpr = window.devicePixelRatio || 1;
  const w = cv.clientWidth, h = cv.clientHeight;
  cv.width = Math.round(w * dpr);
  cv.height = Math.round(h * dpr);
  const g = cv.getContext("2d");
  g.setTransform(dpr, 0, 0, dpr, 0, 0);
  g.fillStyle = "#07090d";
  g.fillRect(0, 0, w, h);
  const L = 52, R = 6, T = 8, B = 22, pw = w - L - R, ph = h - T - B;
  const span = (dp.mask & 1 && dp.plan.LSB1 > 0.1) || (dp.mask & 2 && dp.plan.LSB2 > 0.1) ? 27.5 : 5.1;
  g.strokeStyle = "#1c2330";
  for (let i = 0; i <= 10; i++) { const x = L + (pw * i) / 10; g.beginPath(); g.moveTo(x, T); g.lineTo(x, T + ph); g.stroke(); }
  for (let j = 0; j <= 8; j++) { const y = T + (ph * j) / 8; g.beginPath(); g.moveTo(L, y); g.lineTo(L + pw, y); g.stroke(); }
  const y = (v) => T + ph / 2 - (v / span) * (ph / 2);
  const m = i1 - i0, cols = Math.floor(pw);
  const draw = (buf, ch, col) => {
    g.strokeStyle = col;
    g.lineWidth = 1.4;
    g.globalAlpha = 0.85;
    g.beginPath();
    if (m <= cols) {
      for (let i = 0; i < m; i++) {
        const x = L + (pw * i) / Math.max(1, m - 1), yy = y(deepVolts(buf[i0 + i], ch));
        i ? g.lineTo(x, yy) : g.moveTo(x, yy);
      }
    } else {
      for (let c = 0; c < cols; c++) {
        const a0 = i0 + Math.floor((c * m) / cols), a1 = Math.max(a0 + 1, i0 + Math.floor(((c + 1) * m) / cols));
        let lo = 255, hi = 0;
        for (let i = a0; i < a1; i++) { const v = buf[i]; if (v < lo) lo = v; if (v > hi) hi = v; }
        g.moveTo(L + c + 0.5, y(deepVolts(hi, ch)));
        g.lineTo(L + c + 0.5, y(deepVolts(lo, ch)) + 0.5);
      }
    }
    g.stroke();
    g.globalAlpha = 1;
  };
  if (dp.mask & 2) draw(dp.b, 1, "#3fc8e6");
  if (dp.mask & 1) draw(dp.a, 0, "#ffd21f");
  if (dp.trig >= i0 && dp.trig < i1) {
    const x = L + (pw * (dp.trig - i0)) / Math.max(1, m - 1);
    g.strokeStyle = "#ff7800";
    g.setLineDash([4, 4]);
    g.beginPath(); g.moveTo(x, T); g.lineTo(x, T + ph); g.stroke();
    g.setLineDash([]);
  }
  g.fillStyle = "#8b93a3";
  g.font = "11px system-ui, sans-serif";
  g.textAlign = "right";
  for (let j = 0; j <= 4; j++) { const v = span - (j * span) / 2; g.fillText(eng(v, "V", 2), L - 6, y(v) + 4); }
  g.textAlign = "center";
  const ref = dp.trig >= 0 ? dp.trig : 0;
  for (let i = 0; i <= 10; i += 2) {
    const k = i0 + (m * i) / 10;
    g.fillText(eng((k - ref) / dp.plan.RATE, "s", 3), L + (pw * i) / 10, h - 6);
  }
}
function deepCsv() {
  if (!dp.plan) return;
  const n = dp.plan.N;
  let out = "t_s" + (dp.mask & 1 ? ",ch1_v" : "") + (dp.mask & 2 ? ",ch2_v" : "") + "\n";
  const ref = dp.trig >= 0 ? dp.trig : 0;
  for (let i = 0; i < n; i++) {
    out += ((i - ref) / dp.plan.RATE).toExponential(6);
    if (dp.mask & 1) out += "," + deepVolts(dp.a[i], 0).toFixed(4);
    if (dp.mask & 2) out += "," + deepVolts(dp.b[i], 1).toFixed(4);
    out += "\n";
  }
  save(new Blob([out], { type: "text/csv" }), "probepen-deep.csv");
}

/* ---------- host app: bus decoding ---------- */
const dc = { on: false, lines: 0, frames: 0, errors: 0, bytes: 0, t0: 0, uartOpen: null };
/* what a signal kind starts with when it is picked by hand */
const KIND_DEFAULTS = {
  7: { vpp: 3.3, offset: 0, freq: 115200, noise: 0.01 },
  9: { freq: 500e3, rate2: 2e6, noise: 0.02 },
  10: { freq: 500e3, rate2: 2e6, noise: 0.02 },
};
const siText = (v) => eng(v, "", 6).replace(/\s/g, "");   /* 115200 -> "115.2k", exact */
/* The Decode bar follows a UART or CAN signal on a tip: its bus, channel and bit rates (not while decoding). */
function decodeFollowSignal(ch) {
  if (dc.on) return;
  const s = sig[ch], o = sig[1 - ch];
  if (s.kind === 7) {
    setDecodeControls({ proto: "UART", src: ch + 1, rate: siText(s.freq) });
  } else if (s.kind >= 9) {
    const pair = o.kind >= 9 && o.kind !== s.kind;   /* CAN_H and CAN_L on the two tips: decode CH1 - CH2 */
    setDecodeControls({ proto: "CAN", src: pair ? 3 : ch + 1, rate: siText(s.freq), data: siText(s.rate2 || 4 * s.freq) });
  }
}
function setDecodeControls(d) {
  $("dcProto").value = d.proto;
  $("dcSrc").value = String(d.src);
  $("dcRate").value = d.rate;
  if (d.data) $("dcData").value = d.data;
  decodeFields();
}
function decodeFields() {
  const can = $("dcProto").value === "CAN";
  $("dcRateL").textContent = can ? "Bit rate" : "Baud";
  $("dcDataL").style.display = can ? "" : "none";
}
function decodeStart() {
  if (dc.on) {
    send("DECODE 0");
    return;
  }
  if (st.on) {
    note("stop the stream first");
    return;
  }
  const proto = $("dcProto").value, src = $("dcSrc").value;
  const rate = parseSI($("dcRate").value), data = proto === "CAN" ? parseSI($("dcData").value) : 0;
  if (!(rate > 0) || (proto === "CAN" && !(data >= rate))) {
    note("decode: a bit rate such as 115200 or 500k, and a CAN data rate at least as fast");
    return;
  }
  send(`DECODE ${proto} ${src} ${rate}${proto === "CAN" ? ` ${data}` : ""}`, (reply) => {
    if (!reply.startsWith("OK")) {
      note("the pen refused that decode (bit rate too fast for the ADC, or a stream running)");
      return;
    }
    const kv = {};
    reply.split(/\s+/).slice(1).forEach((w) => { const [k, v] = w.split("="); kv[k] = isNaN(+v) ? v : +v; });
    Object.assign(dc, { on: true, t0: performance.now(), lines: 0, frames: 0, errors: 0, bytes: 0 });
    termAdd(`<span class="t">— ${esc(reply.slice(3))}</span>`);
    $("dcStart").textContent = "Stop";
    $("termWrap").hidden = false;
    $("termSub").textContent = `${proto} from ${["", "CH1", "CH2", "CH1 − CH2"][+src]}, ${eng(kv.FS, "S/s")}, ` +
      (kv.MODE === "LIVE" ? "live: every bit" : `bursts: about ${Math.round(kv.COVER * 100)} % of the time`);
    note(`decoding at ${eng(kv.FS, "S/s")} (${kv.SPB} samples per bit), ${kv.MODE === "LIVE" ? "live" : `in bursts of ${kv.BURSTN.toLocaleString()} samples`}`);
  });
}
function termAdd(html) {
  const t = $("term");
  const stick = t.scrollTop + t.clientHeight >= t.scrollHeight - 4;
  const div = document.createElement("div");
  div.innerHTML = html;
  t.appendChild(div);
  while (t.childElementCount > 600) t.firstChild.remove();
  if (stick) t.scrollTop = t.scrollHeight;
}
const esc = (s) => s.replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" })[c]);
function hexText(hex) {
  let s = "";
  for (let i = 0; i + 1 < hex.length; i += 2) {
    const c = parseInt(hex.slice(i, i + 2), 16);
    s += c === 10 ? "\n" : c === 13 ? "" : c >= 32 && c < 127 ? String.fromCharCode(c) : "·";
  }
  return s;
}
function onDecoded(line) {
  const kv = {};
  line.split(/\s+/).slice(2).forEach((w) => { const i = w.indexOf("="); kv[w.slice(0, i)] = w.slice(i + 1); });
  const t = `<span class="t">${(+kv.T).toFixed(6)}</span> `;
  if (line.startsWith("DEC UART")) {
    dc.lines++;
    dc.bytes += +kv.BYTES;
    if (+kv.FE) dc.errors++;
    const text = esc(hexText(kv.HEX || "").replace(/\n$/, ""));
    termAdd(`${t}${text}${+kv.FE ? ` <span class="err">${kv.FE} framing error${+kv.FE > 1 ? "s" : ""}</span>` : ""}`);
  } else {
    dc.frames++;
    const ok = kv.ERR === "NONE";
    if (!ok) dc.errors++;
    const kind = `${kv.FD === "1" ? "FD" : "CAN"}${kv.BRS === "1" ? " BRS" : ""}${kv.EXT === "1" ? " EXT" : ""}${kv.RTR === "1" ? " RTR" : ""}`;
    const body = ok ? `[${kv.LEN}] ${esc(hexText(kv.HEX || ""))}` : "";
    termAdd(`${t}<span class="id">${kv.ID}</span> ${kind} ${body} ` +
      (ok ? `<span class="ok">CRC ok</span>${kv.ACK === "1" ? "" : " no ACK"}` : `<span class="err">${kv.ERR} error</span>`));
  }
}
function decodeStats(s) {
  if (!s.decoding && !dc.on) return;
  const n = dc.frames || dc.lines;
  $("dcStats").innerHTML = `${n.toLocaleString()} ${dc.frames ? "frames" : "lines"}` +
    (dc.bytes ? ` · ${dc.bytes.toLocaleString()} bytes` : "") +
    ` · <span class="${dc.errors ? "bad" : "good"}">${dc.errors ? `${dc.errors} with errors` : "no errors"}</span>` +
    (s.dec_burst ? ` · bursts, ${Math.round(s.dec_cover * 100)} % seen` : " · live") +
    (n >= 5 && dc.errors >= n
      ? ` · <span class="bad">every ${dc.frames ? "frame" : "line"} has errors: check the ${dc.frames ? "bit rates and the source" : "baud rate and the source"}</span>` : "") +
    (s.dec_check === 1 ? ` · <span class="bad">the edges are faster than ${eng(s.dec_fs / 4, "bit/s")}: the rate is set too low</span>`
      : s.dec_check >= 2 ? ` · <span class="bad">the edges show about ${eng(s.dec_seen, "bit/s")}, not ${eng(s.dec_proto === 2 ? s.dec_data : s.dec_rate, "bit/s")}: is the rate right?</span>` : "") +
    (s.decoding && !s.dec_edges && s.dec_seconds > 0.5 ? ` · <span class="bad">no edges on the source</span>` : "");
}

/* ---------- host app: continuous capture and its history ---------- */
const HIST_MAX = 200;
const hist = [];
let histFollow = true;
let capT = [];
function addHistory(cap) {
  hist.push({ ...cap, t: performance.now() });
  if (hist.length > HIST_MAX) hist.shift();
  capT.push(performance.now());
  capT = capT.filter((x) => performance.now() - x < 2000);
  const r = $("hHistPos");
  r.max = String(hist.length - 1);
  $("hHist").hidden = false;
  if (histFollow) r.value = r.max;
  showHistory();
}
function showHistory() {
  if (!hist.length) return;
  const i = +$("hHistPos").value;
  const cap = hist[Math.min(i, hist.length - 1)];
  lastCap = cap;
  drawPlot($("plot"), cap, true);
  showMeas(cap.meas);
  const ago = (performance.now() - cap.t) / 1000;
  const rate = capT.length > 1 ? (capT.length - 1) / ((capT[capT.length - 1] - capT[0]) / 1000) : 0;
  $("hHistOut").textContent = `${i + 1} of ${hist.length}` + (histFollow ? " (latest)" : `, ${ago.toFixed(1)} s ago`) +
    ($("hLive").checked && rate ? ` · ${rate.toFixed(1)} captures/s` : "");
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
  $("hCap").addEventListener("click", () => capBusy || capture());
  $("hLive").addEventListener("change", (e) => {
    histFollow = true;
    if (e.target.checked && !capBusy) capture();
  });
  $("hHistPos").addEventListener("input", (e) => {
    histFollow = +e.target.value >= hist.length - 1;
    showHistory();
  });
  $("hHistLatest").addEventListener("click", () => {
    histFollow = true;
    $("hHistPos").value = $("hHistPos").max;
    showHistory();
  });
  $("dcProto").addEventListener("change", () => {
    $("dcRate").value = $("dcProto").value === "CAN" ? "500k" : "115200";
    decodeFields();
  });
  $("dcStart").addEventListener("click", decodeStart);
  $("dcClear").addEventListener("click", () => {
    $("term").innerHTML = "";
    Object.assign(dc, { lines: 0, frames: 0, errors: 0, bytes: 0 });
  });
  decodeFields();
  $("clk").value = String(MHZ);
  $("clk").addEventListener("change", (e) => {
    const u = new URL(location.href);
    if (e.target.value === "150") u.searchParams.delete("mhz");
    else u.searchParams.set("mhz", e.target.value);
    location.href = u.toString();
  });
  $("dRate").innerHTML = [1, 2, 3, 8, 75].map((k) => `<option value="${ADC_MAX / k}">${eng(ADC_MAX / k, "S/s")}</option>`).join("");
  $("hCsv").addEventListener("click", downloadCsv);
  $("sStart").addEventListener("click", streamStart);
  $("dGo").addEventListener("click", deepStart);
  $("dCsv").addEventListener("click", deepCsv);
  $("dView").addEventListener("change", drawDeep);
  $("sCsv").addEventListener("click", streamCsv);
  $("sBudget").addEventListener("change", (e) => ex && ex.sim_set_usb_budget(+e.target.value));
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
  capBusy = true;
  send(`CAPTURE ${n}`, (reply) => {
    if (!reply.startsWith("OK")) {
      capBusy = false;
      $("hLive").checked = false;
      note("CAPTURE needs a scope mode (CH1+2, CH1 or CH2)");
      return;
    }
    frameSink = {
      frames: [],
      done: (fr) => send("MEAS?", (l) => {
        capBusy = false;
        addHistory({ a: fr[0], b: fr[1], meas: parseMeas(l) });
        $("plotEmpty").style.display = "none";
        $("hCsv").disabled = false;
        if ($("hLive").checked && !dc.on && !st.on) setTimeout(capture, 0);   /* the next one, straight away */
      }),
    };
  });
}
let capBusy = false;
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
  streamStats(t);
  drawStream(t);
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
