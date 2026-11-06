"use strict";
/* Semantic IDs and units from upstream SW/ai/README.md; register addresses stay in XML. */
const MaxAI = (() => {
  const features = [
    ["Face detection", "YuNet face boxes"],
    ["Gallery recognition", "SFace comparison; empty gallery returns unknown"],
    ["Objects", "YOLOX, 80 COCO classes"],
    ["Plate candidates / OCR", "Country-dependent candidates; OCR accuracy unqualified"],
    ["Motion regions", "Sampled-frame difference; lighting and camera motion can trigger"],
    ["Pedestrians", "HOG/SVM; separate from COCO person detection"],
    ["QR decoding", "Decoded text limited to 31 UTF-8 bytes"],
    ["Tracking", "Requires a detector; expires after three sampled frames"],
  ].map(([name, note], id) => ({ id, bit: 1 << id, name, note }));
  const workers = ["Unavailable", "Idle", "Ready", "Copying"];
  const errors = ["None", "Worker exception", "Rejected result", "Cancelled / stale configuration"];
  function view(data, elapsedMs, running) {
    if (!running) return { state: "stopped", message: "Stream stopped. Camera released; AI enablement is cleared.", result: null };
    if (!data) return { state: "waiting", message: "Read AI state to check the worker and results.", result: null };
    const result = data.result && { ...data.result, age_us: data.result.age_us + Math.max(0, elapsedMs) * 1000 };
    if (result && result.age_us > data.max_age_us) return { ...data, state: "stale", result,
      message: "Stale snapshot: capture age exceeds 15 s. It does not describe the live frame." };
    return { ...data, result };
  }
  function selectionError(mask, supported) {
    if (!Number.isInteger(mask) || mask < 0 || mask > 255) return "Invalid feature selection.";
    if (mask & ~supported) return "This selection contains an unsupported feature.";
    if (mask === 128) return "Tracking requires at least one detector.";
    return "";
  }
  function scoreText(obj) {
    const score = (obj.score / 1e6).toFixed(3);
    if ([3, 4, 6].includes(obj.feature)) return `${score} marker`;
    return `${score} ${obj.feature === 1 && obj.class_id === 1 ? "cosine similarity" : "model score"}`;
  }
  return { features, workers, errors, view, selectionError, scoreText };
})();
if (typeof module !== "undefined") module.exports = MaxAI;
