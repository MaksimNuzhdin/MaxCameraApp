"use strict";
/* MaxCamera Control. No framework and no third-party code, so the strict CSP stays simple. */

const $ = (id) => document.getElementById(id);
const el = (tag, attrs = {}, ...kids) => {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") node.className = v;
    else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
    else if (v === true) node.setAttribute(k, "");
    else if (v !== false && v != null) node.setAttribute(k, v);
  }
  for (const kid of kids) if (kid != null) node.append(kid);
  return node;
};

const state = {
  schema: null, values: {}, status: {}, stream: false, wantStream: false, waiting: null,
  tab: "exposure", ws: null, autoRestart: true, frameSize: null,
  zoom: { mode: "fit", scale: 1, x: 0, y: 0 }, histogram: false, frames: [], applying: 0, hiddenTimer: null,
  ai: null, aiReceived: 0, aiEpoch: 0, aiReading: false, aiApplying: false, aiLastRead: 0,
  aiVisuals: { ...MaxAIVisuals.defaults }, aiTrails: new MaxAIVisuals.Trails(), aiRenderKey: "",
};

/* ---------- API ---------- */
async function api(path, body) {
  const options = body === undefined ? {} : {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  };
  const response = await fetch(path, { credentials: "same-origin", ...options });
  let data = {};
  try { data = await response.json(); } catch { /* an empty or non-JSON body keeps the status check below */ }
  if (!response.ok) {
    if (data.code === "auth") { showLogin(); }
    const error = new Error(data.error || `Request failed (${response.status})`);
    error.code = data.code; error.status = response.status;
    throw error;
  }
  return data;
}

function toast(text, isError) {
  const node = el("div", { class: "toast" + (isError ? " error" : "") }, text);
  $("toasts").append(node);
  setTimeout(() => node.remove(), isError ? 7000 : 3500);
}

function confirmAction(text) {
  return new Promise((resolve) => {
    const dialog = $("confirm-dialog");
    $("confirm-text").textContent = text;
    const done = (answer) => { dialog.close(); $("confirm-yes").onclick = $("confirm-no").onclick = null; resolve(answer); };
    $("confirm-yes").onclick = () => done(true);
    $("confirm-no").onclick = () => done(false);
    dialog.showModal();
  });
}

/* ---------- Sign-in ---------- */
function buildMosaic() {
  const mosaic = $("mosaic");
  if (mosaic.childElementCount) return;
  const columns = Math.ceil(innerWidth / 48) + 1, rows = Math.ceil(innerHeight / 48) + 1;
  const tiles = [];
  for (let y = 0; y < rows; y++) for (let x = 0; x < columns; x++) {
    const tile = el("i");
    tile.style.setProperty("--tile", y % 2 ? (x % 2 ? "#ef6461" : "#57c98a") : (x % 2 ? "#57c98a" : "#5b9df5"));
    mosaic.append(tile);
    tiles.push({ tile, x: x / columns, y: y / rows });
  }
  const expose = () => {
    if (!$("login").hidden) {
      const reduce = matchMedia("(prefers-reduced-motion: reduce)").matches;
      const cx = .5 + Math.random() * .5, cy = Math.random();
      for (const t of tiles) {
        const d = Math.hypot(t.x - cx, t.y - cy);
        t.tile.style.setProperty("--level", (Math.max(.02, .3 - d * .42) * (reduce ? .5 : .7 + Math.random() * .6)).toFixed(3));
      }
      if (!reduce) setTimeout(expose, 3200);
    }
  };
  requestAnimationFrame(expose);
}

function showLogin() {
  clearAI();
  closeSocket();
  $("app").hidden = true;
  $("login").hidden = false;
  buildMosaic();
  $("login-pass").value = "";
  $("login-user").focus();
}

async function enterApp(me) {
  $("login").hidden = true;
  $("app").hidden = false;
  $("password-warning").hidden = !me.default_password;
  $("account-user").textContent = `Signed in as ${me.user}`;
  buildTabs();
  if (state.slowLink) {
    state.slowLink = false;
    $("preview-quality").value = "320,40";
    api("/api/preview", { max_width: 320, quality: 40, max_fps: 10 }).catch(() => {});
    toast("This connection is slow, so the preview starts at Minimal quality.");
  }
  try {
    state.schema = await api("/api/schema");
    renderPanel();
    await refreshValues();
  } catch (error) {
    if (error.status !== 401) toast(error.message, true);
  }
  openSocket();
}

$("login-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  $("login-error").textContent = "";
  try {
    const me = await api("/api/login", { username: $("login-user").value, password: $("login-pass").value });
    await enterApp(me);
  } catch (error) {
    $("login-error").textContent = error.message;
  }
});

/* ---------- Socket and video ---------- */
function openSocket() {
  closeSocket();
  const ws = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/api/ws`);
  ws.binaryType = "arraybuffer";
  state.ws = ws;
  ws.onmessage = (event) => (typeof event.data === "string" ? onEvent(JSON.parse(event.data)) : onFrame(event.data));
  ws.onclose = () => { if (state.ws === ws && !$("app").hidden) setTimeout(() => { if (state.ws === ws) openSocket(); }, 2000); };
}

function closeSocket() {
  const ws = state.ws;
  state.ws = null;
  if (ws) ws.close();
  updateAIOverlay();
}

function onEvent(event) {
  switch (event.event) {
    case "status":
      state.status = event;
      setStream(Boolean(event.stream));
      updateCameraState();
      if (event.stats) updateHud(event.stats);
      break;
    case "stream":
      if (event.running) { setStream(true); } else if (!event.paused) { setStream(false); state.status.camera = "free"; }
      if (event.error) toast(event.error, true);
      updateCameraState(event.paused ? "Applying setting" : null);
      break;
    case "values":
      mergeValues(event.values);
      break;
    case "decode_error":
      toast(event.message, true);
      break;
  }
}

const canvas = $("canvas");
const context2d = canvas.getContext("2d", { alpha: false });
let drawing = false;

async function onFrame(buffer) {
  const headLength = new DataView(buffer).getUint32(0);
  const header = JSON.parse(new TextDecoder().decode(new Uint8Array(buffer, 4, headLength)));
  const blob = new Blob([new Uint8Array(buffer, 4 + headLength)], { type: "image/jpeg" });
  if (drawing) { ack(); return; }
  drawing = true;
  try {
    const image = "createImageBitmap" in window ? await createImageBitmap(blob) : await loadImage(blob);
    if (!state.stream) return;
    if (canvas.width !== image.width || canvas.height !== image.height) {
      canvas.width = image.width;
      canvas.height = image.height;
    }
    context2d.drawImage(image, 0, 0);
    if (image.close) image.close();
    showFrameArea(true);
    if (!state.frameSize || state.frameSize.w !== header.src_w || state.frameSize.h !== header.src_h) {
      state.frameSize = { w: header.src_w, h: header.src_h };
      layoutFrame();
    }
    state.frames.push({ t: performance.now(), bytes: blob.size });
    if (header.stats) updateHud(header.stats);
    if (state.histogram) drawHistogram();
  } catch { /* a frame that fails to decode is skipped; the next one replaces it */ } finally {
    drawing = false;
    ack();
  }
}

function ack() {
  if (state.ws && state.ws.readyState === WebSocket.OPEN) state.ws.send("ack");
}

function loadImage(blob) {
  return new Promise((resolve, reject) => {
    const image = new Image();
    image.onload = () => { URL.revokeObjectURL(image.src); resolve(image); };
    image.onerror = reject;
    image.src = URL.createObjectURL(blob);
  });
}

function showFrameArea(visible) {
  $("empty").hidden = visible;
  $("frame").classList.toggle("invisible", !visible);
  $("hud").hidden = !visible;
  $("histogram").hidden = !(visible && state.histogram);
}

function updateHud(stats) {
  const now = performance.now();
  state.frames = state.frames.filter((f) => now - f.t < 2000);
  const shown = state.frames.length / 2;
  const mbit = state.frames.reduce((sum, f) => sum + f.bytes, 0) * 8 / 2e6;
  const hud = $("hud");
  hud.replaceChildren(
    el("span", {}, `${stats.width || "–"}×${stats.height || "–"}`),
    el("span", {}, `Camera ${stats.fps ?? "–"} fps`),
    el("span", { class: "optional" }, `On screen ${shown.toFixed(1)} fps`),
    el("span", {}, `${mbit.toFixed(1)} Mbit/s`),
  );
  if (stats.partial) hud.append(el("span", { class: "warn", title: "Frames with a few lost packets, filled from the previous frame" }, `${stats.partial} partial`));
  if (stats.dropped) hud.append(el("span", { class: "warn", title: "Frames discarded because too many packets were lost" }, `${stats.dropped} dropped`));
  if (stats.stalled) hud.append(el("span", { class: "warn" }, "No frames from the camera"));
}

function drawHistogram() {
  const small = drawHistogram.small || (drawHistogram.small = document.createElement("canvas"));
  small.width = 160; small.height = 120;
  const ctx = small.getContext("2d", { willReadFrequently: true });
  ctx.drawImage(canvas, 0, 0, 160, 120);
  const pixels = ctx.getImageData(0, 0, 160, 120).data;
  const channels = [new Uint32Array(256), new Uint32Array(256), new Uint32Array(256)];
  for (let i = 0; i < pixels.length; i += 4) { channels[0][pixels[i]]++; channels[1][pixels[i + 1]]++; channels[2][pixels[i + 2]]++; }
  const plot = $("histogram").getContext("2d");
  plot.clearRect(0, 0, 256, 96);
  plot.globalCompositeOperation = "lighter";
  const peak = Math.max(1, ...channels.map((c) => Math.max(...c.subarray(2, 254))));
  ["#ef6461", "#57c98a", "#5b9df5"].forEach((colour, index) => {
    const height = (x) => Math.min(94, Math.pow(channels[index][x] / peak, .6) * 94);
    plot.beginPath();
    plot.moveTo(0, 96);
    for (let x = 0; x < 256; x++) plot.lineTo(x, 96 - height(x));
    plot.lineTo(255, 96);
    plot.fillStyle = colour + "26";
    plot.fill();
    plot.beginPath();
    for (let x = 0; x < 256; x++) plot.lineTo(x, 96 - height(x));
    plot.strokeStyle = colour;
    plot.lineWidth = 1.2;
    plot.stroke();
  });
  plot.globalCompositeOperation = "source-over";
}

/* ---------- Stream control ---------- */
function setStream(running) {
  if (state.stream === running) { if (!running) updateStreamButtons(); return; }
  state.stream = running;
  clearAI();
  if (!running && state.values.MaxAIEnabledFeatures) state.values.MaxAIEnabledFeatures.value = 0;
  if (!running) {
    state.frameSize = null;
    showFrameArea(false);
    $("empty-title").textContent = "The camera is not connected";
    $("empty-note").textContent = "MaxCamera connects only while you stream or change a setting, so other applications can use the camera the rest of the time.";
  } else {
    if (state.waiting) { clearTimeout(state.waiting); state.waiting = null; }
    $("empty-title").textContent = "Waiting for the first frame";
    $("empty-note").textContent = "The camera is starting. Large images can take a moment.";
    $("empty-start").hidden = true;
  }
  updateStreamButtons();
  renderPanelValues();
}

function updateStreamButtons() {
  const toggle = $("stream-toggle");
  const waiting = Boolean(state.waiting);
  toggle.textContent = state.stream ? "Stop stream" : waiting ? "Cancel waiting" : "Start stream";
  toggle.classList.toggle("stop", state.stream || waiting);
  $("empty-start").hidden = state.stream || waiting;
  $("snapshot").disabled = !state.stream;
  $("chain").classList.toggle("live", state.stream);
}

async function startStream() {
  try {
    await api("/api/stream/start", {});
    setStream(true);
    refreshValues();
  } catch (error) {
    if (error.code === "busy") {
      $("empty-title").textContent = "Another application is using the camera";
      $("empty-note").textContent = "MaxCamera does not interrupt other users. It checks again every 5 seconds and starts as soon as the camera is free.";
      state.waiting = setTimeout(() => { state.waiting = null; startStream(); }, 5000);
      updateStreamButtons();
    } else {
      toast(error.message, true);
      updateStreamButtons();
    }
  }
}

async function stopStream() {
  try {
    await api("/api/stream/stop", {});
    setStream(false);
    state.status.camera = "free";
    updateCameraState();
  } catch (error) { toast(error.message, true); }
}

$("stream-toggle").addEventListener("click", () => {
  if (state.waiting) {
    clearTimeout(state.waiting); state.waiting = null;
    setStream(false);
    return;
  }
  (state.stream ? stopStream : startStream)();
});
$("empty-start").addEventListener("click", startStream);

document.addEventListener("visibilitychange", () => {
  if (document.hidden) {
    state.hiddenTimer = setTimeout(() => {
      if (state.stream) { stopStream(); toast("Stream stopped while this tab was hidden, to free the camera."); }
    }, 60000);
    if (state.ws?.readyState === WebSocket.OPEN) state.ws.send("video-off");
  } else {
    clearTimeout(state.hiddenTimer);
    if (state.ws?.readyState === WebSocket.OPEN) state.ws.send("video-on");
  }
});

function updateCameraState(override) {
  const node = $("camera-state");
  const camera = state.status.camera;
  const key = state.stream ? "streaming" : camera || "unknown";
  const labels = {
    streaming: "Streaming", free: "Camera free", controlled: "Applying settings",
    busy: "In use by another application", offline: "Camera offline", unknown: "Camera idle",
  };
  node.dataset.state = key;
  node.querySelector("span").textContent = override || labels[key] || "Camera idle";
}

/* ---------- Preview tools ---------- */
$("preview-quality").addEventListener("change", (event) => {
  const [width, quality] = event.target.value.split(",").map(Number);
  api("/api/preview", { max_width: width, quality, max_fps: width && width <= 320 ? 10 : 30 }).catch((error) => toast(error.message, true));
});
$("snapshot").addEventListener("click", async () => {
  const response = await fetch(`/api/snapshot?format=${$("snapshot-format").value}`, { credentials: "same-origin" });
  if (!response.ok) { toast("The snapshot failed. Check that the stream is running.", true); return; }
  const name = /filename="([^"]+)"/.exec(response.headers.get("Content-Disposition") || "")?.[1] || "maxcamera.png";
  const url = URL.createObjectURL(await response.blob());
  const link = el("a", { href: url, download: name });
  document.body.append(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 10000);
  toast(`Saved ${name}`);
});
$("auto-restart").addEventListener("change", (event) => { state.autoRestart = event.target.checked; renderPanelValues(); });
$("toggle-grid").addEventListener("click", (event) => {
  const on = event.currentTarget.getAttribute("aria-pressed") !== "true";
  event.currentTarget.setAttribute("aria-pressed", on);
  $("grid-overlay").hidden = !on;
});
$("toggle-histogram").addEventListener("click", (event) => {
  state.histogram = event.currentTarget.getAttribute("aria-pressed") !== "true";
  event.currentTarget.setAttribute("aria-pressed", state.histogram);
  $("histogram").hidden = !(state.histogram && state.stream);
});
$("fullscreen").addEventListener("click", () => {
  const target = $("viewport");
  if (document.fullscreenElement) document.exitFullscreen();
  else if (target.requestFullscreen) target.requestFullscreen();
  else if (target.webkitRequestFullscreen) target.webkitRequestFullscreen();
});

/* Zoom and pan act on the displayed image; the camera is not involved. */
function layoutFrame() {
  const view = $("viewport").getBoundingClientRect();
  const frame = $("frame");
  const size = state.frameSize || { w: canvas.width, h: canvas.height };
  const z = state.zoom;
  if (z.mode === "fit") {
    z.scale = Math.min(view.width / size.w, view.height / size.h) || 1;
    z.x = z.y = 0;
  }
  frame.style.width = `${size.w}px`;
  frame.style.height = `${size.h}px`;
  frame.style.transform = `translate(${z.x - size.w * z.scale / 2}px, ${z.y - size.h * z.scale / 2}px) scale(${z.scale})`;
  frame.classList.toggle("pixelated", z.scale >= 2);
  updateAIOverlay();
}
function setZoom(scale, anchorX = 0, anchorY = 0) {
  const z = state.zoom;
  const next = Math.max(.05, Math.min(16, scale));
  z.x = anchorX - (anchorX - z.x) * next / z.scale;
  z.y = anchorY - (anchorY - z.y) * next / z.scale;
  z.scale = next;
  z.mode = "free";
  layoutFrame();
}
$("zoom-fit").addEventListener("click", () => { state.zoom.mode = "fit"; layoutFrame(); });
$("zoom-one").addEventListener("click", () => { state.zoom.x = state.zoom.y = 0; setZoom(1); });
addEventListener("resize", layoutFrame);
document.addEventListener("fullscreenchange", layoutFrame);
{
  const view = $("viewport");
  const pointers = new Map();
  let pinch = 0;
  const anchor = (x, y) => {
    const r = view.getBoundingClientRect();
    return [x - r.left - r.width / 2, y - r.top - r.height / 2];
  };
  view.addEventListener("wheel", (event) => {
    if (!state.stream) return;
    event.preventDefault();
    setZoom(state.zoom.scale * Math.exp(-event.deltaY * .0015), ...anchor(event.clientX, event.clientY));
  }, { passive: false });
  view.addEventListener("pointerdown", (event) => {
    if (!state.stream || event.target.closest("button")) return;
    view.setPointerCapture(event.pointerId);
    pointers.set(event.pointerId, [event.clientX, event.clientY]);
    pinch = 0;
  });
  view.addEventListener("pointermove", (event) => {
    const last = pointers.get(event.pointerId);
    if (!last) return;
    pointers.set(event.pointerId, [event.clientX, event.clientY]);
    if (pointers.size === 1) {
      state.zoom.x += event.clientX - last[0];
      state.zoom.y += event.clientY - last[1];
      state.zoom.mode = "free";
      layoutFrame();
    } else if (pointers.size === 2) {
      const [a, b] = [...pointers.values()];
      const distance = Math.hypot(a[0] - b[0], a[1] - b[1]);
      if (pinch) setZoom(state.zoom.scale * distance / pinch, ...anchor((a[0] + b[0]) / 2, (a[1] + b[1]) / 2));
      pinch = distance;
    }
  });
  const end = (event) => { pointers.delete(event.pointerId); pinch = 0; };
  view.addEventListener("pointerup", end);
  view.addEventListener("pointercancel", end);
  view.addEventListener("dblclick", () => { state.zoom.mode = "fit"; layoutFrame(); });
}

/* ---------- Feature definitions ---------- */
const LABELS = {
  ExposureTime: "Exposure time", ExposureAuto: "Auto exposure", GainAuto: "Auto gain", GainFactor: "Gain",
  MaxAIAvailable: "Worker available", MaxAISupportedFeatures: "Loaded capability mask",
  MaxAIEnabledFeatures: "Enabled feature mask", MaxAIWorkerState: "Worker state", MaxAIErrorCode: "AI error code",
  MaxAIConfigGeneration: "Configuration generation", MaxAIInferenceTimeUs: "Last inference latency (µs)",
  MaxAIMinimumScore: "Minimum model score (millionths)", MaxAISamplePeriodUs: "Sampling interval (µs)",
  MaxAIFaceCosineThreshold: "Face cosine threshold (millionths)",
  Gain: "Gain (dB scale)", AutoExposureTarget: "Auto exposure target", AutoGainLimit: "Auto gain limit",
  ExposureLimit50Hz: "Exposure limit, 50 Hz lighting", ExposureLimit60Hz: "Exposure limit, 60 Hz lighting",
  SensorNightMode: "Night mode", SensorBanding: "Light flicker filter", AcquisitionFrameRateEnable: "Limit frame rate",
  AcquisitionFrameRate: "Frame rate limit", ResultingFrameRate: "Sensor frame rate",
  BalanceWhiteAuto: "Sensor auto white balance", WhiteBalanceRed: "Sensor red gain", WhiteBalanceGreen: "Sensor green gain",
  WhiteBalanceBlue: "Sensor blue gain", FpgaBalanceWhiteAuto: "FPGA auto white balance", FpgaBalanceRed: "FPGA red gain",
  FpgaBalanceGreen: "FPGA green gain", FpgaBalanceBlue: "FPGA blue gain", PixelFormat: "Pixel format",
  OffsetX: "Offset X", OffsetY: "Offset Y", ReverseX: "Mirror", ReverseY: "Flip", ImageCompressionMode: "Compression",
  SensorJpegQuality: "JPEG quantization (lower is better)", GevSCPD: "Packet delay (µs)",
  FpgaProcessingEnable: "FPGA processing", FpgaBadPixelCorrectionEnable: "Bad pixel correction", FpgaBlackLevel: "Black level",
  FpgaLensShadingEnable: "Lens shading correction", FpgaColorTransformationEnable: "Color matrix", FpgaGammaEnable: "Gamma curve",
  FpgaMedianEnable: "Median filter", FpgaBilateralEnable: "Bilateral filter", FpgaBilateralSigmaColor: "Range sigma",
  FpgaBilateralSigmaSpace: "Spatial sigma", FpgaSharpenStrength: "Sharpening", FpgaScalerEnable: "Scaler",
  FpgaScalerInterpolation: "Interpolation", FpgaRoiEnable: "Crop region (ROI)", FpgaInputWidth: "Input width", FpgaInputHeight: "Input height",
  FpgaRoiOffsetX: "ROI X", FpgaRoiOffsetY: "ROI Y", FpgaRoiWidth: "ROI width", FpgaRoiHeight: "ROI height",
  FpgaLocalContrastEnable: "Local contrast (CLAHE)", FpgaLocalContrastClipLimit: "Clip limit", FpgaAdaptiveToneEnable: "Adaptive tone",
  FpgaAdaptiveToneTarget: "Tone target", FpgaSceneAnalysisEnable: "Focus and motion analysis", FpgaDistortionEnable: "Distortion correction",
  FpgaYuvRange: "YUV range", TestPattern: "Test pattern", SensorSharpness: "Sensor sharpness", SensorDenoise: "Sensor denoise",
  SensorSharpnessAuto: "Auto sharpness", SensorDenoiseAuto: "Auto denoise", DeviceTemperature: "FPGA temperature",
  FanStartTemperature: "Fan start temperature", FanStopTemperature: "Fan stop temperature", FanDutyCycle: "Fan speed",
  UserSetSelector: "Preset slot", UserSetLoad: "Load preset", UserSetSave: "Save to preset", UserSetDefault: "Load at power-up",
  DeviceReset: "Reset camera", FpgaFocusMean: "Focus score", FpgaMotionMean: "Motion score", FpgaIntensityMean: "Mean brightness",
  FpgaClippedPixels: "Clipped pixels", FpgaDarkPixels: "Dark pixels", FpgaAppliedBalanceRed: "Applied red gain",
  FpgaAppliedBalanceGreen: "Applied green gain", FpgaAppliedBalanceBlue: "Applied blue gain", SensorAverageLuminance: "Sensor luminance",
};
const label = (name) => LABELS[name] || name.replace(/([a-z0-9])([A-Z])/g, "$1 $2").replace(/^Fpga /, "FPGA ");

/* A feature locked by its own auto mode cannot be helped by restarting the stream. */
const AUTO_OWNER = {
  ExposureTime: ["ExposureAuto", "Off"], GainFactor: ["GainAuto", "Off"], Gain: ["GainAuto", "Off"],
  WhiteBalanceRed: ["BalanceWhiteAuto", "Off"], WhiteBalanceGreen: ["BalanceWhiteAuto", "Off"],
  WhiteBalanceBlue: ["BalanceWhiteAuto", "Off"], BalanceRatio: ["BalanceWhiteAuto", "Off"],
};
const LOG_SLIDERS = new Set(["ExposureTime", "GainFactor", "AutoGainLimit", "ExposureLimit50Hz", "ExposureLimit60Hz"]);
const CHANNEL = { WhiteBalanceRed: "red", WhiteBalanceGreen: "green", WhiteBalanceBlue: "blue", FpgaBalanceRed: "red", FpgaBalanceGreen: "green", FpgaBalanceBlue: "blue", FpgaAppliedBalanceRed: "red" };

const TABS = [
  { id: "exposure", name: "Exposure", groups: [
    { title: "Exposure", features: ["ExposureAuto", "ExposureTime", "AutoExposureTarget", "ExposureLimit50Hz", "ExposureLimit60Hz"] },
    { title: "Gain", features: ["GainAuto", "GainFactor", "AutoGainLimit", "SensorNightMode"] },
    { title: "Frame rate", features: ["AcquisitionFrameRateEnable", "AcquisitionFrameRate", "ResultingFrameRate", "SensorBanding"] },
  ]},
  { id: "color", name: "Color", groups: [
    { title: "White balance on the sensor", note: "Works when FPGA processing is off. RAW output has no sensor white balance.", features: ["BalanceWhiteAuto", "WhiteBalanceRed", "WhiteBalanceGreen", "WhiteBalanceBlue"] },
    { id: "wb", title: "White balance in the FPGA", features: ["FpgaBalanceWhiteAuto", "FpgaBalanceRed", "FpgaBalanceGreen", "FpgaBalanceBlue"] },
    { id: "ccm", title: "Color matrix", custom: "matrix" },
    { id: "gamma", title: "Gamma curve", custom: "gamma" },
  ]},
  { id: "format", name: "Format", groups: [
    { title: "Start from a known setup", custom: "recipes" },
    { title: "Output image", features: ["PixelFormat", "Width", "Height", "OffsetX", "OffsetY", "ReverseX", "ReverseY", "PayloadSize"] },
    { title: "Compression", note: "JPEG requires 640×480 YUV422_8 with FPGA processing off. Geometry stays locked while JPEG is on.", features: ["ImageCompressionMode", "SensorJpegQuality"] },
    { id: "scaler", title: "FPGA scaler and crop", features: ["FpgaScalerEnable", "FpgaScalerInterpolation", "FpgaInputWidth", "FpgaInputHeight", "FpgaRoiEnable", "FpgaRoiOffsetX", "FpgaRoiOffsetY", "FpgaRoiWidth", "FpgaRoiHeight", "FpgaYuvRange"] },
    { title: "Network pacing", note: "Spaces out video packets. Raise it if the picture shows partial frames.", features: ["GevSCPD"] },
  ]},
  { id: "processing", name: "Processing", groups: [
    { id: "master", title: "FPGA pipeline", note: "Turns on the camera's RAW processing chain. Off uses the sensor's own image processor.", features: ["FpgaProcessingEnable", "FpgaProcessingVersion"] },
    { id: "black", title: "Black level", features: ["FpgaBlackLevel", "FpgaBlackLevelLiveActive"] },
    { id: "bpc", title: "Bad pixels", features: ["FpgaBadPixelCorrectionEnable"] },
    { id: "lsc", title: "Lens shading", note: "Needs a flat-field calibration to correct real optics. Edit gains in All settings.", features: ["FpgaLensShadingEnable"] },
    { id: "median", title: "Median filter", features: ["FpgaMedianEnable"] },
    { id: "bilateral", title: "Bilateral filter", features: ["FpgaBilateralEnable", "FpgaBilateralSigmaColor", "FpgaBilateralSigmaSpace"] },
    { id: "sharpen", title: "Sharpening", features: ["FpgaSharpenStrength"] },
    { id: "remap", title: "Distortion correction", note: "Requires a map uploaded for this lens. The map is uploaded with the project's desktop tool.", features: ["FpgaDistortionEnable", "FpgaDistortionMapState"] },
    { id: "clahe", title: "Local contrast", features: ["FpgaLocalContrastEnable", "FpgaLocalContrastClipLimit"] },
    { id: "tone", title: "Adaptive tone", features: ["FpgaAdaptiveToneEnable", "FpgaAdaptiveToneTarget", "FpgaAdaptiveToneAppliedGain", "FpgaAdaptiveTonePreviousMean"] },
    { id: "scene", title: "Focus and motion", features: ["FpgaSceneAnalysisEnable", "FpgaFocusMean", "FpgaMotionMean", "FpgaMotionValid", "FpgaIntensityMean", "FpgaClippedPixels", "FpgaDarkPixels"] },
  ]},
  { id: "sensor", name: "Sensor", groups: [
    { title: "Test pattern", note: "Turn off sensor auto white balance first (Color tab), or patterns come out black.", features: ["TestPattern", "SensorTestPatternStyle", "SensorTestRolling", "SensorTestTransparent", "SensorTestMonochrome"] },
    { title: "Sensor image processor", note: "Has no effect on RAW output.", features: ["SensorSharpnessAuto", "SensorSharpness", "SensorDenoiseAuto", "SensorDenoise"] },
    { title: "Effects", note: "Set the values, then press Apply while the stream is stopped.", features: ["EffectEnableBits", "EffectBrightness", "EffectContrast", "EffectHueCos", "EffectHueSin", "EffectU", "EffectV", "EffectYOffset", "EffectSigns", "EffectManualSaturation", "EffectUvThreshold1", "EffectUvThreshold2", "EffectApply"] },
    { title: "Overlay rectangle", features: ["OverlayEnable", "OverlayLeft", "OverlayRight", "OverlayTop", "OverlayBottom", "OverlayU", "OverlayV", "OverlayApply"] },
  ]},
  { id: "ai", name: "AI", groups: [
    { id: "ai-visuals", title: "Visual overlays", custom: "ai-visuals",
      note: "Optional annotations from the latest camera AI snapshot. They are delayed and not synchronized to the displayed video frame." },
    { id: "ai-runtime", title: "MaxAI · A53 analytics", custom: "ai",
      note: "Asynchronous camera inference. Video FPS is independent of AI latency. Start the stream, select a small feature set, then apply." },
    { title: "Inference settings", note: "Volatile settings. Sampling is an admission interval, not a result-rate guarantee. Scores are not calibrated probabilities; 450000 means cosine 0.45.",
      features: ["MaxAISamplePeriodUs", "MaxAIMinimumScore", "MaxAIFaceCosineThreshold"] },
  ]},
  { id: "presets", name: "Presets", groups: [
    { title: "Presets stored in the camera", custom: "presets" },
    { title: "Device", features: ["DeviceReset"] },
  ]},
  { id: "health", name: "Health", groups: [
    { title: "Device", custom: "device" },
    { title: "Temperature and fan", features: ["DeviceTemperature", "FanStartTemperature", "FanStopTemperature", "FanDutyCycle"] },
    { title: "Frames", readout: ["CapturedFrames", "TransmittedFrames", "DroppedFrames", "CsiErrorEvents", "CameraRecoveryCount"] },
    { title: "Network", readout: ["EthernetLinkUp", "EthernetReceivedPackets", "EthernetTransmittedPackets", "EthernetErrors", "EthernetReceiveDrops", "StreamSendRetries", "PacketResendRequests", "RetransmittedPackets", "PacketResendUnavailable"] },
    { title: "Sensor", readout: ["SensorInitialized", "SensorState", "SensorBusy", "SensorLastError", "SensorTransportError", "SensorAverageLuminance"] },
  ]},
  { id: "all", name: "All settings", custom: "all" },
];

/* Follows the project's qualified bring-up order: dependent stages off first, geometry
   next, complete tables, master enable last. Sensor white balance must stay off on the
   RAW route, because its estimator can settle at zero gain and black out the image. */
function gammaItems(gamma) {
  const items = [];
  for (let channel = 0; channel < 3; channel++) {
    items.push(["FpgaGammaChannel", channel]);
    for (let i = 0; i < 256; i++) items.push(["FpgaGammaIndex", i], ["FpgaGammaValue", Math.round(255 * Math.pow(i / 255, 1 / gamma))]);
  }
  return items;
}
function fpgaRecipe(width, height, roi) {
  return [
    ["FpgaProcessingEnable", 0], ["ImageCompressionMode", "Off"], ["AcquisitionFrameRateEnable", 0],
    ["FpgaAdaptiveToneEnable", 0], ["FpgaColorTransformationEnable", 0], ["FpgaGammaEnable", 0], ["FpgaBalanceWhiteAuto", "Off"],
    ["FpgaMedianEnable", 0], ["FpgaBilateralEnable", 0], ["FpgaSharpenStrength", 0], ["FpgaLensShadingEnable", 0],
    ["FpgaLocalContrastEnable", 0], ["FpgaDistortionEnable", 0], ["FpgaRoiEnable", 0], ["FpgaScalerEnable", 0], ["FpgaScalerInterpolation", "Area"],
    ["PixelFormat", "YUV422_8"], ["Width", width], ["Height", height], ["PixelFormat", "RGB8"],
    ["TestPattern", "Off"], ["BalanceWhiteAuto", "Off"], ["SensorNightMode", 0],
    ["FpgaBlackLevel", 0], ["FpgaBadPixelCorrectionEnable", 1], ["FpgaBalanceRed", 1], ["FpgaBalanceGreen", 1], ["FpgaBalanceBlue", 1],
    ["FpgaInputWidth", 2592], ["FpgaInputHeight", 1944],
    ["FpgaRoiOffsetX", roi ? roi[0] : 0], ["FpgaRoiOffsetY", roi ? roi[1] : 0], ["FpgaRoiWidth", roi ? roi[2] : 2592], ["FpgaRoiHeight", roi ? roi[3] : 1944],
    ...gammaItems(1.8),
    ...(roi ? [["FpgaRoiEnable", 1]] : []),
    ["FpgaScalerEnable", 1], ["FpgaScalerInterpolation", "Polyphase"], ["FpgaGammaEnable", 1],
    ["FpgaBalanceWhiteAuto", "Continuous"], ["FpgaSceneAnalysisEnable", 1], ["FpgaProcessingEnable", 1],
    ["ExposureAuto", "Continuous"], ["GainAuto", "Continuous"],
  ];
}
function nativeRecipe(width, height, extra = []) {
  return [
    ["FpgaProcessingEnable", 0], ["ImageCompressionMode", "Off"], ["TestPattern", "Off"],
    ["PixelFormat", "YUV422_8"], ["Width", width], ["Height", height],
    ["BalanceWhiteAuto", "Continuous"], ["ExposureAuto", "Continuous"], ["GainAuto", "Continuous"], ...extra,
  ];
}
const RECIPES = [
  { name: "Camera test bars", detail: "Sensor color bars at 640×480, to check the whole path. Auto white balance goes off, or the bars come out black.",
    items: nativeRecipe(640, 480, [["BalanceWhiteAuto", "Off"], ["WhiteBalanceRed", 1], ["WhiteBalanceGreen", 1], ["WhiteBalanceBlue", 1], ["TestPattern", "ColorBars"]]) },
  { name: "Full-field color preview", detail: "640×480 RGB from the whole sensor through the FPGA, with gamma and auto white balance. About 14 fps.", items: fpgaRecipe(640, 480) },
  { name: "Twofold digital zoom", detail: "Centered 640×480 crop enlarged to 1280×960 RGB.", items: fpgaRecipe(1280, 960, [976, 732, 640, 480]) },
  { name: "Native sensor video", detail: "640×480 YUV from the sensor's own processor. Fast and light on the network. About 30 fps.", items: nativeRecipe(640, 480) },
  { name: "Maximum resolution", detail: "2592×1944 YUV from the sensor. About 5 fps.", items: nativeRecipe(2592, 1944) },
];

const CHAIN = [
  { name: "Sensor", tab: "sensor", on: () => true },
  { name: "Black level", tab: "processing", group: "black", fpga: 1, on: (v) => v("FpgaBlackLevel") > 0 },
  { name: "Bad pixels", tab: "processing", group: "bpc", fpga: 1, on: (v) => v("FpgaBadPixelCorrectionEnable") },
  { name: "Lens shading", tab: "processing", group: "lsc", fpga: 1, on: (v) => v("FpgaLensShadingEnable") },
  { name: "White balance", tab: "color", group: "wb", fpga: 1, on: (v) => v("FpgaBalanceWhiteAuto") === "Continuous" },
  { name: "Color matrix", tab: "color", group: "ccm", fpga: 1, on: (v) => v("FpgaColorTransformationEnable") },
  { name: "Gamma", tab: "color", group: "gamma", fpga: 1, on: (v) => v("FpgaGammaEnable") },
  { name: "Median", tab: "processing", group: "median", fpga: 1, on: (v) => v("FpgaMedianEnable") },
  { name: "Bilateral", tab: "processing", group: "bilateral", fpga: 1, on: (v) => v("FpgaBilateralEnable") },
  { name: "Sharpen", tab: "processing", group: "sharpen", fpga: 1, on: (v) => v("FpgaSharpenStrength") > 0 },
  { name: "Scale", tab: "format", group: "scaler", fpga: 1, on: (v) => v("FpgaScalerEnable") },
  { name: "Distortion", tab: "processing", group: "remap", fpga: 1, on: (v) => v("FpgaDistortionEnable") },
  { name: "Contrast", tab: "processing", group: "clahe", fpga: 1, on: (v) => v("FpgaLocalContrastEnable") },
  { name: "Tone", tab: "processing", group: "tone", fpga: 1, on: (v) => v("FpgaAdaptiveToneEnable") },
  { name: "Output", tab: "format", on: () => true, text: (v) => v("PixelFormat") },
];

/* ---------- Values ---------- */
async function refreshValues() {
  try {
    mergeValues(await api("/api/values"));
    const status = await api("/api/status");
    state.status = status;
    setStream(Boolean(status.stream));
    updateCameraState();
  } catch (error) {
    if (error.code === "busy" || error.code === "offline") {
      state.status.camera = error.code;
      updateCameraState();
    } else if (error.status !== 401) {
      toast(error.message, true);
    }
  }
}

function mergeValues(values) {
  if (state.ai?.result && (values.MaxAIConfigGeneration?.value !== state.ai.result.config_generation ||
      !values.MaxAIAvailable?.value || !values.MaxAIEnabledFeatures?.value ||
      ["Width", "Height", "PixelFormat", "FpgaProcessingEnable", "ImageCompressionMode"].some((name) => values[name]?.value !== state.values[name]?.value))) clearAI();
  state.values = values;
  renderPanelValues();
  renderChain();
  renderTopReadings();
}

const val = (name) => state.values[name]?.value;

function format(value, info) {
  if (value == null) return "–";
  if (typeof value === "number") {
    if (info?.representation === "HexNumber") return "0x" + value.toString(16).toUpperCase();
    if (info?.kind === "float") {
      const abs = Math.abs(value);
      return value.toLocaleString("en-US", { maximumFractionDigits: abs >= 1000 ? 0 : abs >= 10 ? 1 : 3 });
    }
    return value.toLocaleString("en-US", { useGrouping: Math.abs(value) >= 100000 });
  }
  return String(value);
}

function renderTopReadings() {
  const box = $("top-readings");
  const temp = val("DeviceTemperature");
  box.replaceChildren();
  if (temp != null) box.append(el("span", {}, "FPGA ", el("b", {}, `${temp.toFixed(1)} °C`)));
  if (val("PixelFormat")) box.append(el("span", {}, el("b", {}, `${val("Width")}×${val("Height")} ${val("PixelFormat")}`)));
  if (val("DroppedFrames")) box.append(el("span", {}, "Camera dropped ", el("b", {}, String(val("DroppedFrames")))));
}

function renderChain() {
  const nav = $("chain");
  const read = (name) => val(name);
  const fpga = Boolean(read("FpgaProcessingEnable"));
  nav.classList.toggle("bypass", !fpga);
  if (nav.childElementCount !== CHAIN.length) {
    nav.replaceChildren(...CHAIN.map((stage) => el("button", {
      class: stage.fpga ? "fpga" : "",
      onclick: () => openTab(stage.tab, stage.group),
    }, stage.name)));
  }
  [...nav.children].forEach((button, index) => {
    const stage = CHAIN[index];
    const on = stage.on(read) && (!stage.fpga || fpga);
    button.classList.toggle("on", Boolean(on));
    button.classList.toggle("pass", !on && (!stage.fpga || fpga));
    button.textContent = stage.text ? (stage.text(read) || stage.name) : stage.name;
    button.title = stage.fpga && !fpga ? `${stage.name}: bypassed, FPGA processing is off` : `${stage.name}: ${on ? "active" : "not active"}`;
  });
}

/* ---------- Applying ---------- */
const writeQueue = { items: [], timer: null, running: false };

function queueWrite(name, value, delay = 180) {
  writeQueue.items = writeQueue.items.filter(([n]) => n !== name);
  writeQueue.items.push([name, value]);
  clearTimeout(writeQueue.timer);
  writeQueue.timer = setTimeout(flushWrites, delay);
}

async function flushWrites() {
  if (writeQueue.running || !writeQueue.items.length) return;
  const items = writeQueue.items;
  writeQueue.items = [];
  writeQueue.running = true;
  try { await applyItems(items); } finally {
    writeQueue.running = false;
    if (writeQueue.items.length) flushWrites();
  }
}

async function applyItems(items, successText) {
  if (items.some(([name]) => name.startsWith("MaxAI") || ["Width", "Height", "PixelFormat", "FpgaProcessingEnable"].includes(name))) clearAI();
  state.applying++;
  try {
    const result = await api("/api/apply", { items, restart: state.autoRestart });
    mergeValues(result.values);
    if (result.error) toast(result.error, true);
    else if (successText) toast(successText);
    return !result.error;
  } catch (error) {
    if (error.code === "busy") toast("Another application controls the camera. The change was not applied.", true);
    else if (error.status !== 401) toast(error.message, true);
    refreshValues();
    return false;
  } finally {
    state.applying--;
  }
}

/* ---------- Panel ---------- */
function buildTabs() {
  const nav = $("tabs");
  nav.replaceChildren(...TABS.map((tab) => el("button", {
    role: "tab", id: `tab-${tab.id}`, "aria-selected": tab.id === state.tab, onclick: () => openTab(tab.id),
  }, tab.name)));
}

function openTab(id, groupId, featureName) {
  state.tab = id;
  [...$("tabs").children].forEach((b) => b.setAttribute("aria-selected", b.id === `tab-${id}`));
  renderPanel();
  const target = groupId && $("panel").querySelector(`[data-group="${groupId}"]`)
    || featureName && $("panel").querySelector(`[data-feature="${featureName}"]`);
  if (target) {
    target.scrollIntoView({ block: "center" });
    target.classList.add("flash");
    setTimeout(() => target.classList.remove("flash"), 1500);
  } else {
    $("panel").scrollTop = 0;
  }
}

const bindings = new Map();

function renderPanel() {
  bindings.clear();
  const panel = $("panel");
  panel.replaceChildren();
  if (!state.schema) { panel.append(el("p", { class: "muted" }, "Loading settings…")); return; }
  const tab = TABS.find((t) => t.id === state.tab);
  if (tab.custom === "all") { renderAll(panel); renderPanelValues(); return; }
  for (const group of tab.groups) panel.append(renderGroup(group));
  renderPanelValues();
}

function renderGroup(group) {
  const section = el("section", { class: "group", "data-group": group.id || null }, el("h3", {}, group.title));
  if (group.note) section.append(el("p", { class: "note" }, group.note));
  if (group.custom === "recipes") renderRecipes(section);
  if (group.custom === "matrix") renderMatrix(section);
  if (group.custom === "gamma") renderGamma(section);
  if (group.custom === "presets") renderPresets(section);
  if (group.custom === "device") renderDevice(section);
  if (group.custom === "ai") renderAI(section);
  if (group.custom === "ai-visuals") renderAIVisuals(section);
  for (const name of group.features || []) section.append(renderField(name));
  if (group.readout) {
    const grid = el("div", { class: "stat-grid" });
    for (const name of group.readout) {
      const info = state.schema.features[name];
      if (!info) continue;
      const value = el("span", { class: "readout" });
      grid.append(el("span", {}, label(name)), value);
      bindings.set(`read:${name}`, (s) => {
        value.textContent = format(s?.value, info);
        value.classList.toggle("bad", /Error|Dropped|Drops|Csi/.test(name) && s?.value !== 0 && s?.value != null);
      });
    }
    section.append(grid);
  }
  return section;
}

function controlState(name, info, s) {
  /* Returns {enabled, hint, restart}. */
  if (!s) return { enabled: false, hint: "" };
  if (name.startsWith("MaxAI") && !state.stream) return { enabled: false, hint: "Start the stream to change AI" };
  if (name.startsWith("MaxAI") && state.aiApplying) return { enabled: false, hint: "Applying AI settings" };
  if (name.startsWith("MaxAI") && !val("MaxAIAvailable")) return { enabled: false, hint: "Worker unavailable" };
  if (!s.available) return { enabled: false, hint: "Not available in this setup" };
  const owner = AUTO_OWNER[name];
  if (owner && val(owner[0]) !== owner[1]) return { enabled: false, hint: `Controlled by ${label(owner[0]).toLowerCase()}` };
  if (s.locked) {
    if (state.stream && state.autoRestart) return { enabled: true, hint: "\u21bb", restart: true };
    if (state.stream) return { enabled: false, hint: "Stop the stream to change" };
    return { enabled: false, hint: "Locked" };
  }
  return { enabled: true, hint: "" };
}

function renderField(name) {
  const info = state.schema.features[name];
  if (!info) return document.createDocumentFragment();
  const writable = info.writable && info.access !== "RO";
  const field = el("div", { class: "field", "data-feature": name, title: info.description || "" });
  const nameNode = el("span", { class: "field-name" }, label(name));
  const hint = el("span", { class: "field-hint" });
  const setHint = (ctrl) => {
    hint.textContent = ctrl.hint;
    hint.classList.toggle("restart", Boolean(ctrl.restart));
    hint.title = ctrl.restart ? "Changing this pauses the stream briefly and resumes it" : "";
  };
  const head = el("div", { class: "field-head" }, nameNode, hint);

  if (info.kind === "command") {
    const button = el("button", { onclick: async () => {
      if (info.confirm && !(await confirmAction(info.confirm))) return;
      await applyItems([[name, 1]], `${label(name)} done`);
    } }, label(name));
    if (name === "DeviceReset") button.className = "danger";
    field.append(button, hint);
    bindings.set(name, (s) => {
      const ctrl = controlState(name, info, s);
      button.disabled = !ctrl.enabled;
      setHint(ctrl);
    });
    return field;
  }

  if (!writable || info.kind === "string") {
    field.classList.add("compact");
    const value = el("span", { class: "readout" });
    field.append(nameNode, value);
    bindings.set(name, (s) => {
      value.textContent = format(s?.value, info) + (info.unit && s?.value != null ? ` ${info.unit}` : "");
      field.classList.toggle("off", s ? !s.available : true);
    });
    return field;
  }

  if (info.kind === "bool") {
    field.classList.add("compact");
    const input = el("input", { type: "checkbox", "aria-label": label(name), onchange: (e) => queueWrite(name, e.target.checked ? 1 : 0, 0) });
    field.append(el("div", {}, nameNode, el("div", {}, hint)), el("label", { class: "switch" }, input, el("i")));
    bindings.set(name, (s) => {
      const ctrl = controlState(name, info, s);
      input.checked = Boolean(s?.value);
      input.disabled = !ctrl.enabled;
      field.classList.toggle("off", !ctrl.enabled);
      setHint(ctrl);
    });
    return field;
  }

  if (info.kind === "enum") {
    const visible = info.entries.filter((e) => !e.hidden);
    if (visible.length <= 4 && visible.every((e) => e.name.length < 14)) {
      const buttons = visible.map((entry) => el("button", { onclick: () => {
        if (info.confirm) { confirmAction(info.confirm).then((ok) => ok && queueWrite(name, entry.name, 0)); } else queueWrite(name, entry.name, 0);
      } }, entry.name));
      field.append(head, el("div", { class: "segments", role: "group", "aria-label": label(name) }, ...buttons));
      bindings.set(name, (s) => {
        const ctrl = controlState(name, info, s);
        buttons.forEach((b, i) => {
          b.classList.toggle("active", visible[i].name === s?.value);
          b.disabled = !ctrl.enabled || (s?.unavailable || []).includes(visible[i].name);
        });
        field.classList.toggle("off", !ctrl.enabled);
        setHint(ctrl);
      });
    } else {
      const select = el("select", { "aria-label": label(name), onchange: (e) => queueWrite(name, e.target.value, 0) },
        ...visible.map((entry) => el("option", { value: entry.name }, entry.name)));
      field.append(head, select);
      bindings.set(name, (s) => {
        const ctrl = controlState(name, info, s);
        const unavailable = new Set(s?.unavailable || []);
        for (const option of select.options) option.disabled = unavailable.has(option.value);
        if (s?.value != null && ![...select.options].some((o) => o.value === s.value)) {
          select.append(el("option", { value: s.value }, s.value));
        }
        if (document.activeElement !== select) select.value = s?.value ?? "";
        select.disabled = !ctrl.enabled;
        field.classList.toggle("off", !ctrl.enabled);
        setHint(ctrl);
      });
    }
    return field;
  }

  /* Numbers: a slider paired with an exact field. Huge ranges get only the field. */
  const number = el("input", { type: "number", "aria-label": label(name), step: "any" });
  const slider = el("input", { type: "range", min: 0, max: 1000, step: 1, tabindex: "-1", "aria-hidden": "true", class: CHANNEL[name] || null });
  const row = el("div", { class: "value-row" }, slider, number);
  if (info.unit) row.append(el("span", { class: "unit" }, info.unit));
  field.append(head, row);
  let range = { min: 0, max: 1, log: false, inc: 0 };
  let holding = false;
  let restartOnly = false;
  const toPos = (v) => {
    if (range.log) return 1000 * Math.log(Math.max(v, range.min) / range.min) / Math.log(range.max / range.min);
    return 1000 * (v - range.min) / ((range.max - range.min) || 1);
  };
  const fromPos = (p) => {
    let v = range.log ? range.min * Math.pow(range.max / range.min, p / 1000) : range.min + (range.max - range.min) * p / 1000;
    if (info.kind === "int") {
      const inc = range.inc || 1;
      v = Math.round((v - range.min) / inc) * inc + range.min;
    }
    return Math.max(range.min, Math.min(range.max, v));
  };
  const paint = () => slider.style.setProperty("--fill", `${slider.value / 10}%`);
  slider.addEventListener("input", () => {
    holding = true;
    const v = fromPos(Number(slider.value));
    number.value = info.kind === "int" ? v : Number(v.toFixed(3));
    paint();
    if (!restartOnly) queueWrite(name, v);
  });
  slider.addEventListener("change", () => { holding = false; queueWrite(name, Number(number.value), 0); });
  number.addEventListener("change", () => {
    const v = Number(number.value);
    if (Number.isFinite(v)) queueWrite(name, Math.max(range.min, Math.min(range.max, v)), 0);
  });
  bindings.set(name, (s) => {
    const ctrl = controlState(name, info, s);
    restartOnly = Boolean(ctrl.restart);
    const min = s?.min ?? 0, max = s?.max ?? 1;
    const hasRange = Number.isFinite(min) && Number.isFinite(max) && max > min && max - min <= 1e8;
    range = { min, max, inc: s?.inc || 0, log: LOG_SLIDERS.has(name) && min > 0 };
    slider.hidden = !hasRange;
    if (info.kind === "int" && s?.inc) number.step = s.inc;
    number.min = min; number.max = max;
    if (s?.value != null && !holding && document.activeElement !== number) {
      number.value = info.kind === "float" ? Number(Number(s.value).toFixed(3)) : s.value;
      slider.value = toPos(s.value);
      paint();
    }
    number.disabled = slider.disabled = !ctrl.enabled;
    field.classList.toggle("off", !ctrl.enabled);
    setHint(ctrl);
  });
  return field;
}

function renderPanelValues() {
  if (!state.schema) return;
  for (const [key, bind] of bindings) bind(state.values[key.replace(/^read:/, "")]);
  updateAI();
  updateAIOverlay();
}

/* ---------- Custom widgets ---------- */

function clearAI() {
  state.aiEpoch++;
  state.ai = null;
  state.aiReceived = 0;
  state.aiLastRead = 0;
  state.aiTrails.clear();
  state.aiRenderKey = "";
  updateAIOverlay();
}

function setAIVisuals(change) {
  Object.assign(state.aiVisuals, change);
  if (!state.aiVisuals.enabled || !state.aiVisuals.trails) state.aiTrails.clear();
  state.aiRenderKey = "";
  $("toggle-ai-overlay").setAttribute("aria-pressed", String(state.aiVisuals.enabled));
  renderPanelValues();
}

$("toggle-ai-overlay").addEventListener("click", () => setAIVisuals({ enabled: !state.aiVisuals.enabled }));

function renderAIVisuals(section) {
  const toggles = [
    ["enabled", "Enable AI overlays"], ["boxes", "Bounding boxes"], ["labels", "Object and face labels"],
    ["scores", "Scores / similarity"], ["trackIds", "Track IDs"], ["centers", "Center markers"],
    ["fill", "Region tint"], ["trails", "Track trails"],
  ];
  const list = el("div", { class: "ai-visual-options" });
  const controls = toggles.map(([key, name]) => {
    const input = el("input", { type: "checkbox", "aria-label": name,
      onchange: () => setAIVisuals({ [key]: input.checked }) });
    list.append(el("label", {}, input, el("span", {}, name)));
    return [key, input];
  });
  const style = el("select", { "aria-label": "AI box style", onchange: () => setAIVisuals({ style: style.value }) },
    el("option", { value: "corners" }, "Corner brackets"), el("option", { value: "rectangle" }, "Rectangle"));
  const legend = el("div", { class: "ai-legend", "aria-label": "AI feature colors" });
  for (const feature of MaxAI.features.slice(0, 7)) {
    const chip = el("span", {}, feature.name);
    chip.style.setProperty("--ai-color", MaxAIVisuals.colors[feature.id]);
    legend.append(chip);
  }
  section.append(list, el("label", { class: "ai-style-label" }, "Box style", style),
    el("p", { id: "ai-visuals-state", class: "note" }), legend,
    el("p", { class: "note" }, "View effects only; enable inference below. Names come from camera results; unknown faces stay unknown. Trails join observed positions of the same track, without motion prediction. Saved snapshots contain the camera image without these annotations."));
  bindings.set("ai-visuals-widget", () => {
    for (const [key, input] of controls) input.checked = state.aiVisuals[key];
    style.value = state.aiVisuals.style;
  });
}

function updateAIOverlay() {
  const svg = $("ai-overlay"), badge = $("ai-overlay-status");
  if (!svg || !badge) return;
  const now = performance.now();
  const pixelFormat = state.schema?.features.PixelFormat?.entries?.find((entry) => entry.name === val("PixelFormat"))?.value;
  const model = MaxAIVisuals.scene(state.ai, state.aiReceived ? now - state.aiReceived : 0, {
    enabled: state.aiVisuals.enabled, running: state.stream, connected: state.ws?.readyState === WebSocket.OPEN,
    visible: !document.hidden && !$("app").hidden, applying: state.applying > 0,
    size: state.frameSize, generation: val("MaxAIConfigGeneration"), pixelFormat,
    route: Boolean(val("FpgaProcessingEnable")) && ["RGB8", "BGR8"].includes(val("PixelFormat")) && val("ImageCompressionMode") === "Off",
  });
  badge.hidden = !state.aiVisuals.enabled || !state.stream || $("app").hidden;
  badge.dataset.state = model.state;
  badge.textContent = model.message;
  const note = $("ai-visuals-state");
  if (note) { note.textContent = model.message; note.dataset.state = model.state; }
  if (model.active && state.aiVisuals.trails) state.aiTrails.add(model.result, now, model.maxAgeUs);
  else if (!model.active) state.aiTrails.clear();
  const r = model.result;
  const key = model.active ? [state.aiEpoch, r.sequence, r.config_generation, r.width, r.height,
    state.zoom.scale, Math.floor(r.age_us / 1e6), JSON.stringify(state.aiVisuals)].join(":") : "hidden";
  if (key !== state.aiRenderKey) {
    MaxAIVisuals.render(svg, model, state.aiVisuals, state.zoom.scale, state.aiTrails, now);
    state.aiRenderKey = key;
  }
}

async function readAI() {
  if (state.aiReading || state.aiApplying || $("app").hidden) return;
  const epoch = state.aiEpoch;
  const started = performance.now();
  state.aiReading = true;
  state.aiLastRead = performance.now();
  updateAI();
  try {
    const data = await api("/api/ai/results", {});
    if (epoch !== state.aiEpoch) return;
    const previousSequence = state.ai?.result?.sequence;
    state.ai = data;
    // Include the entire HTTP round trip conservatively, including slow links.
    state.aiReceived = started;
    state.aiRepeated = data.result && data.result.sequence === previousSequence;
    const keys = { available: "MaxAIAvailable", supported: "MaxAISupportedFeatures", enabled: "MaxAIEnabledFeatures",
      worker: "MaxAIWorkerState", error_code: "MaxAIErrorCode", config_generation: "MaxAIConfigGeneration", inference_us: "MaxAIInferenceTimeUs" };
    for (const [key, name] of Object.entries(keys)) {
      if (key in data.diagnostics) state.values[name] = { ...state.values[name], value: key === "worker" ? MaxAI.workers[data.diagnostics[key]] : data.diagnostics[key], available: true };
    }
  } catch (error) {
    if (epoch === state.aiEpoch) { state.ai = { state: "error", message: error.message, result: null, diagnostics: {} }; state.aiReceived = performance.now(); }
  } finally {
    state.aiReading = false;
    renderPanelValues();
  }
}

function renderAI(section) {
  if (!state.schema.features.MaxAIAvailable) {
    section.append(el("p", { class: "note" }, "MaxAI is unavailable in this firmware's feature map."));
    return;
  }
  const status = el("div", { id: "ai-status", class: "ai-status", role: "status" });
  const diagnostics = el("div", { id: "ai-diagnostics", class: "stat-grid" });
  const route = el("p", { id: "ai-route", class: "note" });
  const choices = el("div", { class: "ai-features" });
  for (const feature of MaxAI.features) {
    choices.append(el("label", { class: "ai-feature", title: feature.note },
      el("input", { type: "checkbox", "data-ai-bit": feature.bit, "aria-label": feature.name }),
      el("span", {}, el("b", {}, feature.name), el("small", {}, feature.note)),
      el("span", { class: "ai-capability", "data-ai-capability": feature.bit })));
  }
  const apply = el("button", { id: "ai-apply", onclick: async () => {
    if (state.aiApplying) return;
    const mask = [...choices.querySelectorAll("input:checked")].reduce((sum, input) => sum | Number(input.dataset.aiBit), 0);
    const error = MaxAI.selectionError(mask, Number(val("MaxAISupportedFeatures") || 0));
    if (error) { toast(error, true); return; }
    state.aiApplying = true;
    renderPanelValues();
    try { await applyItems([["MaxAIEnabledFeatures", mask]], "AI selection applied for this stream session"); }
    finally { state.aiApplying = false; renderPanelValues(); await readAI(); }
  } }, "Apply selection");
  const off = el("button", { id: "ai-off", onclick: async () => {
    if (state.aiApplying) return;
    state.aiApplying = true;
    renderPanelValues();
    try { await applyItems([["MaxAIEnabledFeatures", 0]], "AI disabled"); }
    finally { state.aiApplying = false; renderPanelValues(); await readAI(); }
  } }, "Disable AI");
  const read = el("button", { id: "ai-read", onclick: readAI }, "Read AI state");
  const results = el("div", { id: "ai-results", class: "ai-results" });
  section.append(status, diagnostics, route, choices, el("div", { class: "ai-actions" }, apply, off, read),
    el("p", { class: "note" }, "Control release clears enablement. No automatic re-enable. Recognition needs a private gallery; no gallery or enrollment count is exposed. Models are separate, hash-verified downloads. Accuracy is unqualified."),
    el("h3", {}, "Result snapshot"), results);
  let lastMask;
  bindings.set("ai-widget", () => {
    const mask = val("MaxAIEnabledFeatures");
    const supported = Number(val("MaxAISupportedFeatures") || 0);
    const canChange = state.stream && Boolean(val("MaxAIAvailable")) && !state.aiApplying;
    for (const input of choices.querySelectorAll("input")) {
      const bit = Number(input.dataset.aiBit);
      if (mask !== lastMask) input.checked = Boolean(Number(mask) & bit);
      input.disabled = !canChange || !(supported & bit);
    }
    lastMask = mask;
    for (const badge of choices.querySelectorAll("[data-ai-capability]")) {
      badge.textContent = supported & Number(badge.dataset.aiCapability) ? "Loaded" : "Unavailable";
    }
  });
}

function updateAI() {
  if (state.tab !== "ai" || !$("ai-status")) return;
  const elapsed = state.aiReceived ? performance.now() - state.aiReceived : 0;
  // A manual diagnostic read while stopped still reports worker availability.
  const view = !state.stream && state.ai ? { ...state.ai, result: null } : MaxAI.view(state.ai, elapsed, state.stream);
  if (state.stream && state.ws?.readyState !== WebSocket.OPEN) {
    view.state = "stale";
    view.message = "Live connection interrupted. Camera ownership and snapshot freshness cannot be confirmed.";
    view.result = null;
  }
  const status = $("ai-status");
  status.dataset.state = view.state;
  status.textContent = `${view.state.toUpperCase()} · ${view.message}`;
  const diagnostics = $("ai-diagnostics");
  diagnostics.replaceChildren();
  const diag = state.ai?.diagnostics || {};
  const entry = (name, value) => diagnostics.append(el("span", {}, name), el("span", { class: "readout" }, String(value ?? "–")));
  entry("Worker", MaxAI.workers[diag.worker] ?? val("MaxAIWorkerState"));
  entry("Supported / enabled masks", `${diag.supported ?? val("MaxAISupportedFeatures") ?? "–"} / ${diag.enabled ?? val("MaxAIEnabledFeatures") ?? "–"}`);
  const code = diag.error_code ?? val("MaxAIErrorCode");
  entry("Worker error", code == null ? "–" : `${code} · ${MaxAI.errors[code] || "Unknown"}`);
  const latency = diag.inference_us ?? val("MaxAIInferenceTimeUs");
  entry("Last inference latency", latency ? `${(latency / 1e6).toFixed(3)} s` : "No measurement");
  entry("Configuration generation", diag.config_generation ?? val("MaxAIConfigGeneration"));
  const compatible = Boolean(val("FpgaProcessingEnable")) && ["RGB8", "BGR8"].includes(val("PixelFormat")) && val("ImageCompressionMode") === "Off";
  $("ai-route").textContent = compatible ? "Frame route: FPGA RGB8/BGR8. Results use captured output coordinates after ROI/scaling." :
    "No AI frame offers on this route. Use Format → Full-field color preview for FPGA RGB8/BGR8; unsupported formats can still stream normally.";
  $("ai-apply").disabled = !state.stream || !val("MaxAIAvailable") || state.aiApplying || writeQueue.running;
  $("ai-off").disabled = !state.stream || state.aiApplying || !val("MaxAIEnabledFeatures");
  $("ai-read").disabled = state.aiReading || state.aiApplying;
  $("ai-read").textContent = state.aiReading ? "Reading…" : "Read AI state";
  const results = $("ai-results");
  results.replaceChildren();
  const r = view.result;
  if (!r) { results.append(el("p", { class: "note" }, "No current snapshot. Valid empty results are reported explicitly.")); return; }
  const meta = el("div", { class: "stat-grid ai-meta" });
  const detail = (name, value) => meta.append(el("span", {}, name), el("span", { class: "readout" }, String(value)));
  detail("Request / capture sequence", `${r.sequence} / ${r.capture_sequence}${state.aiRepeated ? " · same result" : ""}`);
  detail("Capture age (includes inference)", `${(r.age_us / 1e6).toFixed(1)} s · limit 15 s`);
  detail("Source", `${r.width}×${r.height} · PFNC 0x${r.pixel_format.toString(16)}`);
  detail("Source configuration", r.config_generation);
  detail("Device timestamp (hex)", `0x${r.timestamp_high.toString(16)}${r.timestamp_low.toString(16).padStart(8, "0")}`);
  results.append(meta, el("p", { class: "note" }, "Device clock, not wall time. Request/capture sequences are not GVSP BlockIDs. Optional overlays show delayed snapshot positions, not frame-synchronized tracking."));
  if (!r.objects.length) results.append(el("p", { class: "note" }, "Valid empty result · 0 detections in this snapshot."));
  for (const [i, obj] of r.objects.entries()) {
    results.append(el("article", { class: "ai-object" },
      el("b", {}, `${i + 1}. ${MaxAI.features[obj.feature]?.name || "Unknown feature"} · class ${obj.class_id}`),
      el("p", { class: "ai-object-text" }, obj.text || "No text label"),
      el("p", {}, `${MaxAI.scoreText(obj)} · track ${obj.track_id || "none"}`),
      el("p", {}, `Box x ${obj.x}, y ${obj.y}, ${obj.width}×${obj.height} px`)));
  }
}

setInterval(() => {
  if ((state.tab !== "ai" && !state.aiVisuals.enabled) || $("app").hidden || document.hidden) return;
  if (state.tab === "ai") updateAI();
  if (state.stream && performance.now() - state.aiLastRead > 2000) readAI();
}, 1000);
setInterval(updateAIOverlay, 250);
function renderRecipes(section) {
  for (const recipe of RECIPES) {
    section.append(el("button", { class: "recipe", onclick: () => applyItems(recipe.items, `${recipe.name} applied`) },
      el("b", {}, recipe.name), el("span", {}, recipe.detail)));
  }
}

function renderMatrix(section) {
  section.append(renderField("FpgaColorTransformationEnable"));
  section.append(el("p", { class: "note" }, "Rows are output R, G, B. The last column is the offset. Values range from −8 to 8."));
  const cells = [];
  const grid = el("div", { class: "matrix" });
  const identity = [1, 0, 0, 0, 1, 0, 0, 0, 1];
  for (let row = 0; row < 3; row++) {
    for (let col = 0; col < 3; col++) {
      const input = el("input", { type: "number", step: "0.001", min: -8, max: 7.999, value: identity[row * 3 + col], "aria-label": `Matrix row ${row + 1} column ${col + 1}` });
      cells.push(input);
      grid.append(input);
    }
    grid.append(el("span", { class: "gap" }));
    const offset = el("input", { type: "number", step: "0.001", min: -8, max: 7.999, value: 0, "aria-label": `Offset ${"RGB"[row]}` });
    cells.push(offset);
    grid.append(offset);
  }
  const coeffs = () => [0, 1, 2, 4, 5, 6, 8, 9, 10].map((i) => cells[i]);
  const offsets = () => [3, 7, 11].map((i) => cells[i]);
  section.append(grid, el("div", { class: "actions" },
    el("button", { class: "ghost", onclick: async () => {
      for (let i = 0; i < 9; i++) {
        if (!(await applyItems([["FpgaColorTransformationSelector", i]]))) return;
        coeffs()[i].value = Number(Number(val("FpgaColorTransformationValue")).toFixed(4));
      }
      for (let i = 0; i < 3; i++) {
        if (!(await applyItems([["FpgaColorOffsetSelector", i]]))) return;
        offsets()[i].value = Number(Number(val("FpgaColorOffsetValue")).toFixed(4));
      }
      toast("Read the matrix from the camera");
    } }, "Read from camera"),
    el("button", { class: "ghost", onclick: () => {
      coeffs().forEach((c, i) => { c.value = identity[i]; });
      offsets().forEach((c) => { c.value = 0; });
    } }, "Reset to identity"),
    el("button", { onclick: () => {
      const items = [];
      coeffs().forEach((c, i) => items.push(["FpgaColorTransformationSelector", i], ["FpgaColorTransformationValue", Number(c.value)]));
      offsets().forEach((c, i) => items.push(["FpgaColorOffsetSelector", i], ["FpgaColorOffsetValue", Number(c.value)]));
      applyItems(items, "Matrix applied");
    } }, "Apply matrix")));
}

function renderGamma(section) {
  section.append(renderField("FpgaGammaEnable"));
  let gamma = 1.8;
  const line = document.createElementNS("http://www.w3.org/2000/svg", "polyline");
  const diagonal = document.createElementNS("http://www.w3.org/2000/svg", "line");
  Object.entries({ x1: 0, y1: 100, x2: 200, y2: 0 }).forEach(([k, v]) => diagonal.setAttribute(k, v));
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", "0 0 200 100");
  svg.setAttribute("class", "curve");
  svg.setAttribute("preserveAspectRatio", "none");
  svg.append(diagonal, line);
  const lut = () => Array.from({ length: 256 }, (_, i) => Math.round(255 * Math.pow(i / 255, 1 / gamma)));
  const draw = () => {
    line.setAttribute("points", lut().filter((_, i) => i % 4 === 0 || i === 255).map((v, i, a) => `${(i / (a.length - 1) * 200).toFixed(1)},${(100 - v / 255 * 100).toFixed(1)}`).join(" "));
  };
  const number = el("input", { type: "number", min: .2, max: 4, step: .05, value: gamma, "aria-label": "Gamma value" });
  const slider = el("input", { type: "range", min: .2, max: 4, step: .05, value: gamma, "aria-label": "Gamma value slider" });
  const paint = () => slider.style.setProperty("--fill", `${(gamma - .2) / 3.8 * 100}%`);
  slider.addEventListener("input", () => { gamma = Number(slider.value); number.value = gamma; paint(); draw(); });
  number.addEventListener("input", () => { gamma = Math.max(.2, Math.min(4, Number(number.value) || 1)); slider.value = gamma; paint(); draw(); });
  draw(); paint();
  const write = () => {
    const table = lut();
    const items = [];
    for (let channel = 0; channel < 3; channel++) {
      items.push(["FpgaGammaChannel", channel]);
      table.forEach((value, index) => items.push(["FpgaGammaIndex", index], ["FpgaGammaValue", value]));
    }
    items.push(["FpgaGammaEnable", 1]);
    toast("Writing the gamma table, this takes a few seconds…");
    applyItems(items, `Gamma ${gamma} applied`);
  };
  section.append(svg, el("div", { class: "value-row" }, slider, number), el("div", { class: "actions" }, el("button", { onclick: write }, "Apply curve")));
}

function renderPresets(section) {
  section.append(el("p", { class: "note" }, "Load applies a saved preset now. Save overwrites the slot in the camera's flash memory. The Default slot is read-only."));
  section.append(renderField("UserSetSelector"), renderField("UserSetLoad"), renderField("UserSetSave"), renderField("UserSetDefault"));
  const stored = el("p", { class: "note" });
  section.append(stored);
  bindings.set("read:UserSetSavedMask", (s) => {
    const mask = s?.value;
    if (mask == null) { stored.textContent = ""; return; }
    const names = [];
    for (let i = 1; i < 8; i++) if (mask & (1 << i)) names.push(`UserSet${i}`);
    stored.textContent = names.length ? `Saved slots: ${names.join(", ")}` : "No slots saved yet";
  });
}

function renderDevice(section) {
  const grid = el("div", { class: "stat-grid" });
  for (const name of ["DeviceVendorName", "DeviceModelName", "DeviceSerialNumber", "DeviceFirmwareVersion", "DeviceVersion", "DeviceUserID", "FpgaProcessingVersion", "GevCurrentIPAddress", "DeviceUptime"]) {
    const info = state.schema.features[name];
    if (!info) continue;
    const value = el("span", { class: "readout" });
    grid.append(el("span", {}, label(name).replace(/^Device /, "").replace(/^Gev Current /, "")), value);
    bindings.set(`read:${name}`, (s) => {
      let shown = s?.value;
      if (name === "GevCurrentIPAddress" && typeof shown === "number") shown = [24, 16, 8, 0].map((b) => (shown >>> b) & 255).join(".");
      value.textContent = name === "DeviceUptime" && shown != null ? `${Math.floor(shown / 3600)} h ${Math.floor(shown / 60) % 60} min` : format(shown, name === "GevCurrentIPAddress" ? null : info);
    });
  }
  section.append(grid, el("div", { class: "actions" },
    el("button", { class: "ghost", onclick: () => refreshValues().then(() => toast("Values updated")) }, "Read values now")));
}

function renderAll(panel) {
  const filter = el("input", { class: "filter", type: "search", placeholder: "Filter settings", "aria-label": "Filter settings" });
  const list = el("div");
  panel.append(filter, list);
  const build = () => {
    const needle = filter.value.trim().toLowerCase();
    for (const key of [...bindings.keys()]) bindings.delete(key);
    list.replaceChildren();
    for (const category of state.schema.categories) {
      const names = category.features.filter((n) => {
        if (state.schema.features[n]?.visibility === "Invisible") return false;
        return !needle || n.toLowerCase().includes(needle) || label(n).toLowerCase().includes(needle);
      });
      if (!names.length) continue;
      const details = el("details", { class: "category", open: Boolean(needle) || null },
        el("summary", {}, label(category.name), el("span", {}, String(names.length))));
      const fill = () => {
        if (details.dataset.filled) return;
        details.dataset.filled = "1";
        for (const name of names) {
          details.append(renderField(name));
          const description = state.schema.features[name]?.description;
          if (description) details.append(el("p", { class: "description" }, description));
        }
        renderPanelValues();
      };
      details.addEventListener("toggle", () => { if (details.open) fill(); });
      if (needle) fill();
      list.append(details);
    }
    if (!list.childElementCount) list.append(el("p", { class: "muted" }, "No settings match this filter."));
  };
  filter.addEventListener("input", build);
  build();
}

/* ---------- Search ---------- */
function findHome(name) {
  if (name.startsWith("MaxAI")) return { tab: "ai", group: "ai-runtime", title: "AI, MaxAI analytics" };
  for (const tab of TABS) {
    for (const group of tab.groups || []) {
      if ((group.features || []).includes(name)) return { tab: tab.id, title: `${tab.name}, ${group.title}` };
    }
  }
  if (name.startsWith("FpgaGamma")) return { tab: "color", group: "gamma", title: "Color, Gamma curve" };
  if (name.startsWith("FpgaColor")) return { tab: "color", group: "ccm", title: "Color, Color matrix" };
  if (name.startsWith("UserSet")) return { tab: "presets", title: "Presets" };
  return { tab: "all", title: "All settings" };
}
function openSearch() {
  if (!state.schema) return;
  const dialog = $("search-dialog");
  $("search-input").value = "";
  renderSearch();
  dialog.showModal();
  $("search-input").focus();
}
let searchIndex = 0;
function renderSearch() {
  const needle = $("search-input").value.trim().toLowerCase();
  const box = $("search-results");
  const score = (name) => {
    const a = label(name).toLowerCase(), b = name.toLowerCase();
    if (!needle) return 0;
    if (a.startsWith(needle) || b.startsWith(needle)) return 3;
    if (a.includes(needle) || b.includes(needle)) return 2;
    return (state.schema.features[name].description || "").toLowerCase().includes(needle) ? 1 : -1;
  };
  const found = Object.keys(state.schema.features)
    .map((name) => [score(name), name]).filter(([s]) => s >= 0).sort((a, b) => b[0] - a[0]).slice(0, 40).map(([, n]) => n);
  searchIndex = 0;
  box.replaceChildren(...found.map((name, i) => {
    const home = findHome(name);
    return el("button", { class: i === 0 ? "selected" : "", "data-name": name, onclick: () => {
      $("search-dialog").close();
      if (home.tab === "all") { state.tab = "all"; openTab("all"); const f = $("panel").querySelector(".filter"); f.value = name; f.dispatchEvent(new Event("input")); } else openTab(home.tab, home.group, name);
    } }, label(name), el("small", {}, home.title));
  }));
  if (!found.length) box.append(el("p", {}, "No settings match. Try another word."));
}
$("search-input").addEventListener("input", renderSearch);
$("search-input").addEventListener("keydown", (event) => {
  const options = [...$("search-results").querySelectorAll("button")];
  if (!options.length) return;
  if (event.key === "ArrowDown" || event.key === "ArrowUp") {
    event.preventDefault();
    options[searchIndex]?.classList.remove("selected");
    searchIndex = (searchIndex + (event.key === "ArrowDown" ? 1 : options.length - 1)) % options.length;
    options[searchIndex].classList.add("selected");
    options[searchIndex].scrollIntoView({ block: "nearest" });
  } else if (event.key === "Enter") {
    event.preventDefault();
    options[searchIndex]?.click();
  }
});
$("search-dialog").addEventListener("click", (event) => { if (event.target === event.currentTarget) event.currentTarget.close(); });
$("open-search").addEventListener("click", openSearch);
addEventListener("keydown", (event) => {
  if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") { event.preventDefault(); if (!$("app").hidden) openSearch(); }
  const typing = /INPUT|SELECT|TEXTAREA/.test(document.activeElement?.tagName || "");
  if (typing || $("app").hidden || document.querySelector("dialog[open]")) return;
  if (event.key === " ") { event.preventDefault(); $("stream-toggle").click(); }
  if (event.key.toLowerCase() === "f") $("fullscreen").click();
  if (event.key.toLowerCase() === "s" && state.stream) $("snapshot").click();
});

/* ---------- Account ---------- */
const openAccount = () => { $("pw-error").textContent = ""; $("account-dialog").showModal(); };
$("open-account").addEventListener("click", openAccount);
$("warning-change").addEventListener("click", openAccount);
$("warning-close").addEventListener("click", () => { $("password-warning").hidden = true; });
$("account-close").addEventListener("click", () => $("account-dialog").close());
$("sign-out").addEventListener("click", async () => {
  $("account-dialog").close();
  if (state.stream) await stopStream();
  await api("/api/logout", {}).catch(() => {});
  showLogin();
});
$("password-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    await api("/api/password", { current: $("pw-current").value, new: $("pw-new").value });
    $("account-dialog").close();
    $("pw-current").value = $("pw-new").value = "";
    $("password-warning").hidden = true;
    toast("Password changed");
  } catch (error) { $("pw-error").textContent = error.message; }
});

/* ---------- Start ---------- */
$("boot").remove();
$("frame").classList.add("invisible");
/* A script that took seconds to arrive means a slow link: start with a light preview. */
const scriptEntry = performance.getEntriesByType("resource").find((r) => r.name.endsWith("/app.js"));
state.slowLink = Boolean(scriptEntry && scriptEntry.duration > 2500);
api("/api/me").then(enterApp).catch(showLogin);
