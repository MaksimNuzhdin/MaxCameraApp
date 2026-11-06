# Architecture and API reference

## Scope

MaxCameraApp is a Linux camera gateway and a framework-free browser UI. It consumes the upstream MaxCamera GenApi XML; it does not build firmware or host the camera-side AI runtime. The current seccomp implementation supports x86-64 Linux only.

## Process boundary

```text
Browser → HTTP edge → Unix socket → Web gateway → Unix socket → Camera bridge → Camera
```

- **edge.py** opens one TCP listener before confinement and relays bytes to `web.sock`. Its filter allows accepting traffic but prevents it from creating an outbound IP socket.
- **web.py** serves static assets, sessions, JSON and WebSocket traffic. It runs in a separate network namespace and uses Unix sockets to reach the bridge.
- **bridge.py** controls the fixed configured camera through pre-opened UDP sockets. A MAC check precedes control. The browser never supplies arbitrary destination addresses or raw register addresses.
- **gvsp_rx.c** assembles GigE Vision stream packets into frames. `decode.py` converts supported pixel formats and creates browser JPEGs/snapshots.
- **genapi.py** evaluates the pinned camera XML. `policy.py` removes or restricts unsafe/internal feature writes.

The launcher exposes only selected `/usr`, application and per-component runtime directories inside bubblewrap. The C seccomp-BPF program is installed after startup sockets are opened, with no-new-privileges and thread synchronization.

## Camera control lifecycle

The bridge does not displace a different controller. Start waits for the camera if busy; cancel stops the wait. A short settings operation acquires a lease, applies permitted named features and releases it after inactivity. A running stream owns the lease and sends heartbeats. Stop clears transport destination/port, stops acquisition, restores the prior packet delay, unlocks transport parameters and releases control. Device heartbeat recovery also covers an unexpected controller crash; exact timing depends on camera configuration.

All signed-in browser clients share the same physical camera and stream. One person's changes affect everyone. The gateway stops acquisition after its no-viewer grace interval when all viewers disappear. Explicit **Stop stream** is the predictable way to release it.

## Main HTTP endpoints

All camera/account routes require sign-in. Mutating routes enforce same-origin requests. This list describes the application interface, not a public compatibility commitment.

| Route | Purpose |
|---|---|
| `POST /api/login` | Check the local account and open a session |
| `POST /api/logout` | Close the current session |
| `GET /api/schema` | Read allowed categorized camera features |
| `GET /api/status` | Read stream/control/counter status |
| `GET /api/values` | Read allowed feature values |
| `POST /api/apply` | Apply bounded named-feature writes through policy |
| `POST /api/stream/start` | Request streaming/control |
| `POST /api/stream/stop` | Stop and release control |
| `POST /api/ai/results` | Read one coherent, latched AI snapshot |
| `GET /api/snapshot?format=png` | Download an unannotated image; JPEG also supported |
| `GET /api/ws` | Telemetry and back-pressure-controlled preview frames |

Read handler registrations in `backend/web.py` before building a client. Responses contain error states; clients must not treat every reachable endpoint as success. Authentication secrets and result labels must not be logged in client diagnostics.

## AI data integrity

The app pins the XML and reviewed AI contract to `backend/data/upstream.json`. `deploy/update-camera-source.py` verifies both Git-blob identity and SHA-256. Its `--fetch` option retrieves only that exact pinned XML and verifies before replacing it.

`backend/ai.py` holds the bridge lock across latch, metadata, object selector reads and release. It attempts release even after a failed read. At most 32 objects are accepted. Invalid counts/geometry, contract mismatch, worker loss and release failures fail closed instead of reporting fresh results.

AI results include source geometry/pixel format/configuration, sequence identifiers, timing and object fields. A local 15-second capture-age ceiling and source/configuration checks prevent stale snapshots appearing current. The browser accounts conservatively for network time and keeps advancing age between responses.

There is no proven identity mapping between an AI snapshot and a displayed GVSP frame. Optional SVG annotations therefore state **not frame-synchronized**. Video and overlay share a transform for fit/zoom/pan, but geometric alignment does not imply temporal alignment.

## Browser rendering

`frontend/app.js` handles controls and transport; `ai.js` interprets feature bits and score semantics; `ai-visuals.js` draws safe literal labels and bounded observed trails. No external JavaScript or web font request is made. Visual preferences live for the page session. Snapshot downloads come from the camera image path and exclude SVG/grid/histogram annotations.

## Repository map

| Path | Role |
|---|---|
| `backend/` | Bridge, gateway, edge, decoder, XML evaluator, security policy |
| `backend/data/` | Pinned upstream XML and integrity/provenance record |
| `frontend/` | English responsive UI and local font |
| `deploy/` | Local sandbox launcher, configuration example, manual deployment tools |
| `tests/` | Offline AI contract/visual tests and bounded integration helpers |
| `docs/` | User guide, screenshots, architecture, security, operations and checks |
| `SOURCE_MANIFEST.json` | Hashes of unchanged source copied into the publication |

Generated dependencies, binary libraries, local configuration/state, captured images, models and face galleries stay outside Git.
