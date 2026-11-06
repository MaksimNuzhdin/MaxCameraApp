# Development and validation

## Runtime prerequisites

Use Linux x86-64 with `/usr/bin/python3` 3.10+ (verified on 3.10), GCC, bubblewrap, gzip and pip for that same interpreter. Runtime launchers explicitly use `/usr/bin/python3`; installing wheels with a different active venv/Conda Python can create incompatible native modules.

`requirements.txt` pins the three direct Python dependencies observed in the validated application. `requirements.lock.txt` pins the resolved transitive versions used for the clean-install check. This is a version lock, not a fully hashed or offline wheel distribution. The source release does not vendor third-party wheels or compiled libraries.

```sh
./deploy/bootstrap-local.sh
```

The script installs into ignored `pydeps/`, builds `backend/libgvsp_rx.so`, copies the local environment example only when needed, and verifies the pinned XML. It requires network access to package registries. It does not use sudo, install services or access the camera.

For a clean dependency refresh, use a fresh checkout rather than mixing old `pydeps` wheels. Modern distributions with externally managed Python may require an isolated supported packaging arrangement; do not blindly use `--break-system-packages` or disable the application's sandbox.

## Build and offline checks

Node.js 18+ is recommended for developer JavaScript tests and is not needed to run the app.

```sh
gcc -O2 -std=c11 -Wall -Wextra -Werror -shared -fPIC \
  backend/gvsp_rx.c -o backend/libgvsp_rx.so
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=pydeps:backend \
  /usr/bin/python3 -B -m unittest discover -s tests -v
node --check frontend/app.js
node --check frontend/ai.js
node --check frontend/ai-visuals.js
node tests/test_ai_ui.cjs
node tests/test_ai_visuals.cjs
/usr/bin/python3 deploy/update-camera-source.py
bash -n deploy/*.sh
```

Backend tests use the real XML with a fake wire peer. They cover coherent latching/release, read/release failures, worker/availability states, stale generations/geometry, feature masks, repeated writes and stop/release behavior. JavaScript tests cover semantic states, score meaning, mapping, independent effects, literal labels and bounded non-predicted trails.

Precompressed frontend `.gz` files must match source exactly. The launcher refreshes them deterministically with `gzip -9 -k -f -n`. Rebuild after frontend changes before publication; verify decompressed bytes match originals.

## Synthetic browser fixture

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=pydeps:backend \
  /usr/bin/python3 -B tests/preview_ai_visuals.py
```

Open `http://127.0.0.1:8082/` and use the disposable fixture sign-in. It serves the actual production UI, synthetic frames and a **Synthetic test** scene selector. Scene modes include fresh, stale, empty, unavailable, error, mismatched-source and literal-HTML-label responses. Fixture routes are registered only by this launcher and are not present in the production app.

The fixture is loopback-only and does not need the real camera, models or a gallery. Ctrl+C stops it. Never present its fabricated results as detector-quality evidence. Public documentation screenshots must retain an explicit synthetic caption and contain no real scenes or personal data.

## Local preview and hardware checks

Configure a local camera MAC/IP in `deploy/maxcamera.env`, then use bounded discovery before launching. The development preview runs separately from installed service state:

```sh
./deploy/preview-local.sh
```

Open `http://localhost:8081/`. Keep the launcher alive and stop it with Ctrl+C. Do not run simultaneous controllers. The following helper refuses an already-active stream:

```sh
PYTHONPATH=pydeps /usr/bin/python3 tests/check_http_ai.py
# Only with an idle, authorized camera:
PYTHONPATH=pydeps /usr/bin/python3 tests/check_http_ai.py --live
```

The optional live run lasts 18 seconds, uses volatile AI state and tries cleanup in `finally`. It is not an endurance/accuracy qualification. `check_bridge.py` needs the web gateway disconnected because bridge IPC accepts one client; do not run it against an active user's gateway.

## Updating the camera contract

The release pin is in `backend/data/upstream.json`. Normal verification changes no files or camera settings. `--fetch` retrieves that exact revision and replaces XML only after both hashes pass. To support a new release, review its XML and actual camera-side AI contract, update provenance deliberately, then rerun protocol/UI/hardware checks. Never guess register addresses or assume feature-mask equality means loaded models are accurate.

## Documentation

The Russian user guide is maintained in Markdown and a PDF build script under `docs/`. Its build dependencies are development-only and are not app runtime requirements. Regenerate the PDF, render every page, inspect legibility/layout and verify screenshots/captions before publishing. Do not commit QA render pages, cache files or private local paths.

Font notices for the UI and embedded PDF fonts are preserved in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
