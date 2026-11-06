"use strict";
/* Browser-only effects from the validated MaxAI snapshot. No camera writes. */
const MaxAIVisuals = (() => {
  const contract = typeof module !== "undefined" && module.exports ? require("./ai.js") : MaxAI;
  const defaults = { enabled: false, boxes: true, labels: true, scores: false, trackIds: false,
    centers: false, fill: false, trails: false, style: "corners" };
  const colors = ["#53ddd4", "#77baff", "#f5b83d", "#ffaf79", "#c2a2ff", "#8dd5f4", "#f6a7dd", "#d6dce0"];

  function scene(data, elapsedMs, session) {
    const hidden = (message, state = "waiting") => ({ active: false, message, state, result: null });
    if (!session.enabled) return hidden("AI overlays off", "off");
    if (!session.running) return hidden("Start the stream and enable AI", "stopped");
    if (!session.visible || !session.connected) return hidden("AI overlays hidden · live connection unavailable", "stale");
    if (session.applying) return hidden("AI overlays hidden · settings changing");
    const view = contract.view(data, elapsedMs, true);
    if (view.state !== "fresh" || !view.result) return hidden(`AI overlays hidden · ${view.message || view.state}`, view.state);
    const r = view.result;
    if (!session.size) return hidden("AI overlays waiting for a video frame");
    if (r.width !== session.size.w || r.height !== session.size.h ||
        r.config_generation !== session.generation || r.pixel_format !== session.pixelFormat || !session.route) {
      return hidden("AI overlays hidden · source/configuration mismatch", "stale");
    }
    if (!Number.isFinite(r.age_us) || !Number.isFinite(view.max_age_us) || r.age_us < 0 || r.age_us > view.max_age_us) {
      return hidden("AI overlays hidden · stale capture", "stale");
    }
    return { active: true, state: "fresh", result: r, maxAgeUs: view.max_age_us,
      message: `AI snapshot · ${(r.age_us / 1e6).toFixed(1)} s old · ${r.objects.length} detections · not frame-synchronized` };
  }

  function label(obj, options) {
    const parts = [];
    if (options.labels) {
      const text = String(obj.text || "").replace(/[\u0000-\u001f\u007f]/g, " ");
      if (obj.feature === 1 && obj.class_id === 0) parts.push("Unknown face");
      else if (obj.feature <= 1) parts.push(text && text !== "unknown" ? `Face · ${text}` : "Face");
      else parts.push(text || `${contract.features[obj.feature]?.name || "Object"} · class ${obj.class_id}`);
    }
    if (options.scores) parts.push(contract.scoreText(obj));
    if (options.trackIds && obj.track_id) parts.push(`Track ${obj.track_id}`);
    return parts.join(" · ");
  }

  const trackKey = (obj) => `${obj.feature}:${obj.class_id}:${obj.track_id}`;
  function labelRect(obj, width, height, bounds, occupied) {
    width = Math.min(width, bounds.width);
    height = Math.min(height, bounds.height);
    const x = Math.max(0, Math.min(obj.x, bounds.width - width));
    const preferred = obj.y >= height ? obj.y - height : obj.y;
    const candidates = [preferred, obj.y + obj.height + 2, obj.y];
    for (let offset = height + 2; offset < bounds.height; offset += height + 2) candidates.push(preferred - offset, preferred + offset);
    const fits = (y) => y >= 0 && y + height <= bounds.height && !occupied.some((r) =>
      x < r.x + r.width + 2 && x + width + 2 > r.x && y < r.y + r.height + 2 && y + height + 2 > r.y);
    const y = candidates.find(fits) ?? Math.max(0, Math.min(preferred, bounds.height - height));
    return { x, y, width, height };
  }
  class Trails {
    constructor() { this.clear(); }
    clear() { this.context = ""; this.sequence = null; this.tracks = new Map(); }
    add(result, now, maxAgeUs) {
      const context = [result.config_generation, result.width, result.height, result.pixel_format].join(":");
      if (context !== this.context || (this.sequence !== null && result.sequence < this.sequence)) this.clear();
      this.context = context;
      if (result.sequence === this.sequence) return; // Polls are not additional observations.
      this.sequence = result.sequence;
      const current = new Set();
      for (const obj of result.objects) {
        if (!obj.track_id) continue;
        const key = trackKey(obj);
        current.add(key);
        const points = this.tracks.get(key) || [];
        points.push({ x: obj.x + obj.width / 2, y: obj.y + obj.height / 2, at: now - result.age_us / 1000 });
        this.tracks.set(key, points.filter((point) => now - point.at <= maxAgeUs / 1000).slice(-12));
      }
      // Only currently reported tracks are drawn. Never bridge missing detections.
      for (const key of this.tracks.keys()) if (!current.has(key)) this.tracks.delete(key);
    }
    points(obj, now, maxAgeUs) {
      return (this.tracks.get(trackKey(obj)) || []).filter((point) => now - point.at <= maxAgeUs / 1000);
    }
  }

  function render(svg, model, options, scale, trails, now) {
    svg.replaceChildren();
    svg.toggleAttribute("hidden", !model.active);
    if (!model.active) return;
    const r = model.result;
    svg.setAttribute("viewBox", `0 0 ${r.width} ${r.height}`);
    const make = (tag, attrs = {}, text) => {
      const node = svg.ownerDocument.createElementNS("http://www.w3.org/2000/svg", tag);
      for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, value);
      if (text != null) node.textContent = text;
      return node;
    };
    const unit = 1 / Math.max(.05, scale || 1);
    const occupied = [];
    for (const obj of r.objects) {
      // Defend the renderer too: source coordinates must fit the captured image.
      if (![obj.x, obj.y, obj.width, obj.height].every(Number.isFinite) || obj.width <= 0 || obj.height <= 0 ||
          obj.x < 0 || obj.y < 0 || obj.x + obj.width > r.width || obj.y + obj.height > r.height) continue;
      const color = colors[obj.feature] || colors[7];
      const g = make("g", { class: "ai-mark", "data-feature": obj.feature });
      const shape = { stroke: color, "stroke-width": 2, "vector-effect": "non-scaling-stroke", fill: "none" };
      if (options.trails && obj.track_id) {
        const points = trails.points(obj, now, model.maxAgeUs);
        if (points.length > 1) g.append(make("polyline", { ...shape, class: "ai-trail", points: points.map((p) => `${p.x},${p.y}`).join(" "), "stroke-dasharray": "4 4", opacity: .65 }));
      }
      if (options.fill) g.append(make("rect", { x: obj.x, y: obj.y, width: obj.width, height: obj.height, fill: color, "fill-opacity": .14 }));
      if (options.boxes) {
        if (options.style === "rectangle") g.append(make("rect", { ...shape, class: "ai-box", x: obj.x, y: obj.y, width: obj.width, height: obj.height, rx: 2 * unit }));
        else {
          const x = obj.x, y = obj.y, right = x + obj.width, bottom = y + obj.height;
          const c = Math.min(obj.width / 3, obj.height / 3, 16 * unit);
          g.append(make("path", { ...shape, class: "ai-box", d: `M${x},${y+c}V${y}H${x+c} M${right-c},${y}H${right}V${y+c} M${right},${bottom-c}V${bottom}H${right-c} M${x+c},${bottom}H${x}V${bottom-c}` }));
        }
      }
      if (options.centers) {
        const cx = obj.x + obj.width / 2, cy = obj.y + obj.height / 2, arm = 5 * unit;
        g.append(make("path", { ...shape, class: "ai-center", "stroke-width": 1, d: `M${cx-arm},${cy}H${cx+arm} M${cx},${cy-arm}V${cy+arm}` }));
      }
      const fullLabel = label(obj, options);
      if (fullLabel) {
        const font = 12 * unit, pad = 4 * unit, height = 21 * unit;
        const maxChars = Math.max(1, Math.floor((r.width - 2 * pad) / (font * .63)));
        const characters = Array.from(fullLabel);
        const text = characters.length > maxChars ? characters.slice(0, Math.max(0, maxChars - 1)).join("") + "…" : fullLabel;
        const width = Math.min(r.width, Array.from(text).length * font * .63 + 2 * pad);
        const rect = labelRect(obj, width, height, r, occupied);
        occupied.push(rect);
        const { x, y } = rect;
        if (y > obj.y + obj.height + height || y + height < obj.y - height) {
          g.append(make("line", { ...shape, x1: obj.x, y1: obj.y, x2: x + pad, y2: y + height / 2, opacity: .5, "stroke-width": 1 }));
        }
        g.append(make("rect", { ...rect, fill: "#071218", "fill-opacity": .9, rx: 3 * unit }));
        const t = make("text", { class: "ai-label", x: x + pad, y: y + 14 * unit, fill: color, "font-size": font }, text);
        g.append(t, make("title", {}, fullLabel));
      }
      svg.append(g);
    }
  }
  return { defaults, colors, scene, label, labelRect, Trails, render };
})();
if (typeof module !== "undefined") module.exports = MaxAIVisuals;
