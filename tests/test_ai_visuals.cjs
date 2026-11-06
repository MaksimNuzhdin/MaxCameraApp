const assert = require("node:assert/strict");
const V = require("../frontend/ai-visuals.js");
const obj = { feature: 2, class_id: 1, text: "bicycle", score: 850000, track_id: 7, x: 80, y: 60, width: 120, height: 100 };
const data = { state: "fresh", max_age_us: 15e6, result: { sequence: 1, capture_sequence: 23, age_us: 1e6,
  width: 640, height: 480, pixel_format: 0x02180014, config_generation: 4, objects: [obj] } };
const session = { enabled: true, running: true, connected: true, visible: true, applying: false,
  size: { w: 640, h: 480 }, generation: 4, pixelFormat: 0x02180014, route: true };
assert.equal(V.scene(data, 0, session).active, true);
assert.match(V.scene(data, 0, session).message, /not frame-synchronized/);
for (const change of [{ enabled: false }, { running: false }, { connected: false }, { visible: false }, { applying: true },
  { generation: 6 }, { pixelFormat: 1 }, { route: false }, { size: { w: 320, h: 240 } }, { size: null }]) {
  assert.equal(V.scene(data, 0, { ...session, ...change }).active, false, JSON.stringify(change));
}
assert.equal(V.scene(data, 14001, session).active, false, "local age must expire annotations without another response");
for (const state of ["stale", "error", "waiting", "disabled", "unavailable", "stopped"]) {
  assert.equal(V.scene({ ...data, state }, 0, session).active, false, state);
}
assert.equal(V.scene({ ...data, result: { ...data.result, objects: [] } }, 0, session).active, true, "empty success stays distinct");
assert.match(V.label({ ...obj, feature: 1, class_id: 0, text: "unknown" }, V.defaults), /Unknown face/);
assert.match(V.label({ ...obj, feature: 1, class_id: 1, text: "Demo identity" }, V.defaults), /Demo identity/);
assert.match(V.label({ ...obj, feature: 1, class_id: 1 }, { scores: true }), /cosine similarity/);
assert.match(V.label({ ...obj, feature: 3 }, { scores: true }), /marker/);
assert.equal(V.label(obj, { labels: false, trackIds: true }), "Track 7");
const firstLabel = V.labelRect(obj, 240, 21, data.result, []);
const nextLabel = V.labelRect({ ...obj, x: 100, y: 65 }, 200, 21, data.result, [firstLabel]);
assert.ok(nextLabel.y >= firstLabel.y + firstLabel.height || nextLabel.y + nextLabel.height <= firstLabel.y, "nearby labels should avoid overlap");
const edgeLabel = V.labelRect({ ...obj, x: 620, y: 0 }, 300, 21, data.result, []);
assert.ok(edgeLabel.x >= 0 && edgeLabel.x + edgeLabel.width <= 640 && edgeLabel.y >= 0, "edge labels stay inside the source image");

const tracks = new V.Trails();
tracks.add(data.result, 10000, 15e6);
tracks.add(data.result, 12000, 15e6);
assert.equal(tracks.points(obj, 12000, 15e6).length, 1, "repeated polls must not invent trail points");
tracks.add({ ...data.result, sequence: 2, objects: [{ ...obj, x: 100 }] }, 12000, 15e6);
assert.equal(tracks.points(obj, 12000, 15e6).length, 2);
assert.equal(tracks.points({ ...obj, feature: 0 }, 12000, 15e6).length, 0, "track namespaces must not collide");
assert.equal(tracks.points(obj, 30000, 15e6).length, 0, "old points expire");
tracks.add({ ...data.result, sequence: 3, config_generation: 6 }, 13000, 15e6);
assert.equal(tracks.points(obj, 13000, 15e6).length, 1, "config changes reset trails");
tracks.add({ ...data.result, sequence: 4, config_generation: 6, objects: [] }, 14000, 15e6);
assert.equal(tracks.points(obj, 14000, 15e6).length, 0, "absent detections break trails");

// Minimal SVG DOM records actual renderer output, including safe text nodes.
class Node {
  constructor(tag) { this.tag = tag; this.attrs = {}; this.children = []; this.ownerDocument = { createElementNS: (_, name) => new Node(name) }; }
  setAttribute(key, value) { this.attrs[key] = String(value); }
  toggleAttribute(key, value) { if (value) this.attrs[key] = ""; else delete this.attrs[key]; }
  append(...nodes) { this.children.push(...nodes); }
  replaceChildren(...nodes) { this.children = nodes; }
}
const svg = new Node("svg");
const model = V.scene(data, 0, session);
V.render(svg, model, { ...V.defaults, style: "rectangle", centers: true, fill: true }, .5, tracks, 10000);
assert.equal(svg.attrs.viewBox, "0 0 640 480", "preview downscaling must not change source coordinates");
const box = svg.children[0].children.find((node) => node.attrs.class === "ai-box");
assert.equal(box.attrs.x, "80");
assert.equal(box.attrs.width, "120");
assert.equal(box.attrs["vector-effect"], "non-scaling-stroke");
assert.equal(svg.children[0].children.find((node) => node.tag === "text").attrs["font-size"], "24", "labels compensate for display scale");
const unsafe = '<img src=x onerror=alert(1)>';
V.render(svg, { ...model, result: { ...model.result, objects: [{ ...obj, text: unsafe }] } }, V.defaults, 1, tracks, 10000);
assert.equal(svg.children[0].children.find((node) => node.tag === "text").textContent, unsafe, "labels stay literal text");
V.render(svg, { ...model, result: { ...model.result, objects: [{ ...obj, x: 600 }] } }, V.defaults, 1, tracks, 10000);
assert.equal(svg.children.length, 0, "out-of-bounds annotations must not draw");
V.render(svg, { active: false }, V.defaults, 1, tracks, 10000);
assert.equal(svg.children.length, 0);
assert.ok("hidden" in svg.attrs, "stop/error/stale removes every prior mark");
console.log("PASS AI visuals: source mapping, freshness, unavailable/stop, safe labels, independent toggles and observed trails");
