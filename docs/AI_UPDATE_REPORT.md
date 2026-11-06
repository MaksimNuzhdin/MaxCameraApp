# MaxCamera AI GUI update — 6 November 2026

## Upstream reference

Repository: <https://github.com/MaksimNuzhdin/MaxCamera>

Published revision: `07dfb588b9576f827968c47c5798d9164561346d`.
GitHub's commit endpoint confirmed this revision. The app previously consumed a
camera-downloaded XML file rather than a source checkout. Its reference is now
explicitly pinned in `backend/data/upstream.json`; `backend/data/camera.xml`
matches the published `SW/gige/MaxCamera.xml` byte for byte:

- Git blob: `e4f1d8397e67d119ce6806aa231bbb198abc8247`
- SHA-256: `125090243233f8abb8873ebe4167616f7463cb415022058e09a96fba98eb1b9f`

The reviewed `SW/gige/GigeAI.c` and `SW/ai/README.md` were also matched to their
published Git blobs. Their source reference hashes are recorded in the pin.
The source update preserves every existing categorized feature and adds the
40 upstream MaxAI features. Snapshot internals are intentionally private to the
bridge transaction rather than exposed as independent GUI writes.

## Changes

- Added an English, responsive AI tab for face detection/gallery recognition,
  objects, plate candidates/OCR, motion, pedestrians, QR and tracking. It exposes
  live capabilities, worker/error state, configuration generation, inference
  latency, feature enablement and the three supported inference settings.
- Added authenticated, same-origin `POST /api/ai/results`, backed by XML names,
  never guessed register addresses. It holds the existing bridge state lock
  through latch, metadata, selector/object reads and release. Release is attempted
  even after a failed latch/read. Read failures do not return successful results.
- Results include source geometry/PFNC/configuration, request/capture sequences,
  device timestamps, age, UTF-8 labels, scores, classes, boxes and tracks. Up to
  32 objects are validated. Text is rendered as text and is not logged or stored.
- Explicit unavailable, stopped, disabled, waiting, error, valid-empty and stale
  states. Age includes inference and advances locally, conservatively including
  the HTTP round trip. A 15-second app freshness limit and source/configuration
  checks prevent old results being presented as current. Results are separate
  from video; no unverified alignment to GVSP frames is implied.
- AI settings require an owned running stream. Unsupported masks and tracking
  alone are rejected. Equal-value retries do not bump the firmware configuration
  or invalidate an inference. Stop/release clears enablement; the GUI discards
  late responses and never automatically re-enables AI.
- Added pinned source verification/refresh and bounded read-only discovery
  helpers, a separate loopback-only preview using the existing three sandboxes,
  tests, privacy exclusions and user documentation. Precompressed assets match
  the final source files.

## Verification

| Check | Result |
| --- | --- |
| Published commit/XML/contract identity | Passed via GitHub connector and local Git-blob/SHA-256 checks |
| Existing feature compatibility | Passed: no categorized feature removed |
| `PYTHONPATH=pydeps:backend python3 -m unittest discover -s tests -v` | Passed: 22 tests |
| JavaScript parse and `tests/test_ai_ui.cjs` | Passed in the bundled Node runtime; no `node` binary is on the shell PATH |
| Python compile and shell syntax | Passed |
| Final HTTP integration | Passed: unauthenticated 401, cross-origin 403, protected transport and internal AI writes denied, stopped AI writes rejected |
| Bounded face check through the GUI | Passed: valid empty snapshot, about 0.311 s live inference latency, clean stop/release |
| Final 18-second mask-255 HTTP run | Passed: 260 video frames, 14.29 FPS, zero partial/dropped frames, one distinct valid result with zero objects |
| Repeated start/apply/stop and disable | Passed: equal-mask retry preserved generation; disable returned disabled; repeated stop remained released |
| Final release state | Passed: stream off, ownership released, enabled mask 0, stream destination/port 0, original packet delay restored |
| Desktop and 390×844 GUI inspection | Passed; diagnostic-column wrapping fixed. At 390 px, page width was 390 and panel content width equaled its 380 px viewport |
| Active bridge/web/edge protection | Passed: `NoNewPrivs=1`, `Seccomp=2`; web retained a separate network namespace |
| New source downloader `--fetch` | Not run; initial XML was fetched through the GitHub connector and both local checksums pass |
| Direct `check_bridge.py` live helper | Not run, to avoid displacing the connected web gateway; HTTP live check used instead |
| Optical accuracy, enrolled identities, real plates, endurance, physical MicroSD boot | Not run / unqualified |

No checks remain failed. Worker code 3 (cancelled/stale configuration) occurred
between configuration sessions; the all-feature check also observed fresh and
aged/stale snapshots as expected. An empty result demonstrates protocol
handling, not detector accuracy. The unavailable/error/nonempty/UTF-8/selector
and read/release-failure cases are covered with a fake wire peer against the
real XML rather than by disrupting the camera worker.

The camera was rediscovered using bounded broadcast discovery at the then-current DHCP address,
firmware `0.3.16-ai-linux-dev`. The app target address
was updated accordingly; future runs must rediscover if DHCP changes.

## Open the app

The isolated local preview was used for verification. It is a temporary
foreground process, not an installed service; do not assume it remains available
after the launching terminal ends. The launcher cleans up its
child components on exit, and bubblewrap also uses `--die-with-parent`.

To open the updated app, run these commands in your own terminal and leave it open:

```bash
cd $MAXCAMERA_ROOT
./deploy/preview-local.sh
```

Then open <http://localhost:8081/>. The separate preview state initially uses
`admin` / `admin`. Open **AI**, start a stream, select supported features, and
apply. Ctrl+C stops the preview. If the browser reports connection refused,
check that the launcher is still running and inspect `run/ai-preview/log/`.

Stop streaming before using another controller. The preview keeps its own
ignored state/IPC under `run/ai-preview/`; the installed service's authentication
and state were not changed. No router/firewall, public exposure, persistent
camera settings, firmware, models, or galleries were changed. No scene images,
face presets, private credentials, or model weights were added to source. Scene-free GUI
screenshots are outside the project. No publication, merge or deployment was
performed.

## Remaining limits

AI is experimental and asynchronous. Upstream measured about 0.31 s for faces,
8 s for objects and 9.6 s for the whole set on one A53; these are not video frame
rates or guarantees. Gallery contents/counts are not exposed by the contract;
an empty gallery yields unknown. Model availability is inferred only from the
worker heartbeat and supported mask. Optical accuracy, physical SD boot,
all-mode/coherency and endurance qualification remain outside this GUI update.
