# AI visual overlays

The optional browser annotation layer consumes the existing authenticated
`/api/ai/results` response and pinned MaxCamera contract. It changes no camera
registers, backend API, model, authentication, sandbox, or installed service.
The interface remains English.

## Controls

Open **AI → Visual overlays**. Enable the master switch, then select independent
effects: corner/rectangle boxes, object/face names, model scores or similarity,
track IDs, center marks, translucent region tint, and observed track trails.
The toolbar's **AI overlay** button toggles the whole layer. Inference still
needs to be enabled using the existing camera controls.

Geometry uses the captured source image's viewBox, inside the same transformed
frame as video. Fit, 1:1, zoom, pan and reduced preview resolution therefore share
one coordinate transform. Stroke widths and label size compensate for display
scale. Labels use literal text nodes and stay within the source image; a greedy
placement pass avoids label overlap when possible. Scores preserve their
model/cosine/operational-marker meaning. Unknown faces are never assigned names.

## Asynchronous results

The badge explicitly states snapshot age and **not frame-synchronized**.
There is no inference-to-GVSP-frame identity mapping in the upstream contract.
Visuals are opt-in and hidden for stale/error/unavailable/disabled results,
connection loss, source/configuration mismatch, changing settings and stop.
Capture age advances without waiting for another network response. The existing
15-second maximum age and conservative HTTP round-trip accounting apply.

Trails are bounded to 12 observations per currently visible track and the same
15-second capture window. Their namespace includes feature/class/track ID and
source/configuration. Equal request sequences add no points, and missing tracks,
stale data, configuration changes, stop or disabling trails clear their history.
Lines join sampled observations without interpolation or prediction.

Overlay polling continues while another settings tab is selected, but only
while the page is visible and streaming. Browser effects do not start inference,
claim camera control, persist labels, modify saved presets, or alter downloaded
camera snapshots. Visual preferences last for the current page session.

## Validation

Run the existing backend suite and both JavaScript checks:

```bash
PYTHONPATH=pydeps:backend python3 -m unittest discover -s tests -v
node tests/test_ai_ui.cjs
node tests/test_ai_visuals.cjs
```

The overlay tests cover mapping, scale, clipped/overlapping labels, literal
untrusted text, freshness, stopped/unavailable/error states, source/configuration
changes, independent effects, deduplicated observations and expired track history.

For visual checks without camera traffic:

```bash
PYTHONPATH=pydeps:backend python3 tests/preview_ai_visuals.py
```

This disposable fixture listens only on `127.0.0.1:8082`, uses the product's
sign-in/API/stream UI, and generates an explicitly labelled synthetic image.
Its test-scene selector supplies fresh, stale, empty, unavailable, error,
mismatched-source and literal-HTML-label responses. Fixture routes are registered
only by this test launcher and are absent from the production application.
Ctrl+C stops it. No real gallery, camera frames, or model weights are required.

The actual app still opens by running `./deploy/preview-local.sh` from the project
directory in your own terminal, keeping it open, and visiting
<http://localhost:8081/>. Preview availability after the launcher exits is not assumed.

Validation on 6 November 2026 passed: the 22 existing backend tests, both
JavaScript suites and script parse checks. Browser inspection on the synthetic
fixture confirmed five annotated detections, independent box/label toggles,
rectangle/corner styles, displayed cosine similarity and track IDs, identical
video/overlay bounds at Fit and 1:1, shared pan transforms, and polling outside
the AI tab. Stale, unavailable, error and source-mismatch cases removed all
marks; empty results reported zero; literal HTML remained text. Master-off and
stream stop cleared annotations, and stop hid the age badge. There were no
browser console errors. At 390×844, page width was 390 and the panel content
matched its 380 px viewport; image and overlay bounds matched exactly.

No new live hardware check was run for this browser-only change. The synthetic
fixture was stopped after verification; neither it nor a production preview is
promised to remain running. Installed services and persistent camera settings
were not changed, and captured scenes/model weights were not added to source.
