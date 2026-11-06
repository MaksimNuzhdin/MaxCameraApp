# MaxCameraApp

**MaxCamera Control: GigE Vision camera control, live video and asynchronous AI annotations in your browser.**

[PDF user guide](docs/MaxCameraApp_User_Guide.pdf) · [Editable user guide](docs/MaxCameraApp_User_Guide.md) · [Documentation index](docs/INDEX.md)

![MaxCameraApp with AI annotations](docs/images/screenshot-ai-desktop.png)

*Actual application UI on a disposable synthetic fixture. The scene, identities, detections and AI results are fabricated for illustration. This is not real camera footage or evidence of model accuracy. The **Synthetic test** selector exists only in the fixture. Its default-password warning is not production deployment guidance.*

## Features

MaxCameraApp is a local Linux gateway between a browser and a DinxTech MaxCamera. The responsive interface, documentation and deployment materials are in English. No browser plug-in is needed.

| Area | Available controls |
|---|---|
| Live video | Start/stop, camera/browser FPS, loss counters, four preview-quality choices |
| Camera settings | Exposure, gain, white balance, format, ROI/scaling, ISP, sensor, presets and health |
| Navigation | Setting search, grouped tabs, processing chain and keyboard shortcuts |
| Viewing | Fit, 1:1, zoom/pan, grid, histogram, fullscreen, PNG/JPEG snapshot |
| MaxAI | Eight feature groups, supported settings, worker states and coherent result snapshots |
| Visual effects | Boxes, labels, scores/similarity, track IDs, center marks, tint and observed trails |
| Access | Local authentication, bubblewrap/seccomp process separation, loopback by default |
| Narrow screens | Settings move below the image; long toolbars and processing strips scroll horizontally |

## Quick start

### 1. Prepare the Linux host

The current backend requires **Linux x86-64**, system **/usr/bin/python3 3.10+** (verified on 3.10), GCC, bubblewrap, gzip and pip for that same interpreter. Node.js is needed only for developer JavaScript tests. UI checks used Chromium at desktop/mobile widths; a full browser matrix is not qualified.

Example system packages for Ubuntu/Debian, installed by the operator:

```sh
sudo apt install git python3 python3-pip gcc bubblewrap gzip
```

### 2. Obtain and build the project

```sh
git clone https://github.com/MaksimNuzhdin/MaxCameraApp.git ~/MaxCameraApp
cd ~/MaxCameraApp
./deploy/bootstrap-local.sh
```

Bootstrap downloads version-pinned packages from PyPI into local `pydeps/`, builds the C receiver, creates the local configuration example if absent, and verifies the pinned XML. It does not start services, change the firewall/router/account, or contact the camera.

### 3. Configure your camera

Edit `deploy/maxcamera.env` and set the verified **MAXCAMERA_MAC**. Then perform bounded read-only discovery:

```sh
/usr/bin/python3 deploy/discover-camera.py
```

Put the returned address in **MAXCAMERA_IP**. Keep **MAXCAMERA_LISTEN=127.0.0.1** for local-only access. The `.example` deliberately contains documentation-only addresses, not a real camera target. DHCP can change an address; do not copy one from an old report.

### 4. Open the application

```sh
./deploy/preview-local.sh
```

Leave the terminal open and visit **http://localhost:8081/**. Initial sign-in for a new preview state is **admin / admin**. Change it under **Account** before granting access. The installed service and separate preview use different local state.

Press **Start stream**. For a color preview, try **Format → Full-field color preview**. Finish with **Stop stream** to release the camera. Ctrl+C stops only this foreground preview's components.

## AI: from enablement to annotations

1. Start streaming. Select a small required feature set in the **AI** tab.
2. Press **Apply selection**. Availability does not itself establish model accuracy or a qualified gallery.
3. Check worker state, error code, inference latency, configuration generation and result age.
4. Under **Visual overlays**, turn on **Enable AI overlays** and choose effects.
5. The toolbar's **AI overlay** button toggles browser annotations only. It does not start inference.

| Group | Purpose and limits |
|---|---|
| Face detection | YuNet face boxes |
| Gallery recognition | SFace comparison; unknown faces receive no invented identity; an empty gallery returns unknown |
| Objects | YOLOX COCO object classes |
| Plate candidates / OCR | Plate candidates and text; applicability and accuracy are data-dependent and unqualified |
| Motion regions | Differences between sampled frames; lighting and camera movement can trigger detections |
| Pedestrians | HOG/SVM, separate from the COCO person detector |
| QR decoding | Decoded text bounded by the camera contract |
| Tracking | Requires a detector; trails join observed points without prediction |

### AI is not synchronized to the displayed frame

AI results arrive asynchronously. **not frame-synchronized** means a box is not a guaranteed annotation of the currently displayed video frame. Capture age includes inference and keeps advancing between responses.

Annotations disappear on stale/error/unavailable/disabled states, connection loss, stop, or source-format/geometry/configuration mismatch. The current freshness ceiling is **15 seconds**. AI enablement clears when control is released and is not restored automatically after stop.

**Scores are not universal confidence percentages.** The UI distinguishes model scores, cosine similarity and operational markers. This publication does not qualify recognition accuracy, real plates, enrolled galleries, endurance or physical MicroSD boot.

Downloaded **Snapshot** images contain no browser AI annotations, grid or histogram. Visual effects do not change camera registers, saved presets or the source video.

## Mobile layout

<img src="docs/images/screenshot-ai-mobile.png" width="390" alt="Synthetic MaxCameraApp demonstration at a 390 by 844 mobile viewport">

*The same synthetic fixture at 390 × 844. No real scenes or personal data appear in these screenshots. They illustrate responsive layout, not AI quality.*

## Documentation

| Document | Contents |
|---|---|
| [Complete PDF guide](docs/MaxCameraApp_User_Guide.pdf) | Step-by-step workflows, all tabs, AI/effects, accounts, operations and troubleshooting |
| [Editable guide](docs/MaxCameraApp_User_Guide.md) | Maintained English source |
| [Architecture and APIs](docs/ARCHITECTURE.md) | Process boundaries, camera lifecycle, routes and AI integrity |
| [Security](docs/SECURITY.md) | Threat model, privacy, authentication and protection limits |
| [Deployment](docs/DEPLOYMENT.md) | Configuration, systemd, preview, HTTPS and remote access |
| [Development](docs/DEVELOPMENT.md) | Build, dependencies, offline/live checks and synthetic fixture |
| [AI visual overlays](docs/AI_VISUALS.md) | Geometry, trails, states and earlier UI checks |
| [AI update report](docs/AI_UPDATE_REPORT.md) | Pinned upstream and earlier bounded hardware checks |
| [Publication verification](docs/PUBLICATION_VERIFICATION.md) | Passed/not-run checks, contents and exclusions |
| [Third-party notices](docs/THIRD_PARTY_NOTICES.md) | UI and embedded PDF font licenses |

## Developer checks

After bootstrap and a Node.js installation (18+ recommended):

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=pydeps:backend \
  /usr/bin/python3 -B -m unittest discover -s tests -v
node tests/test_ai_ui.cjs
node tests/test_ai_visuals.cjs
/usr/bin/python3 deploy/update-camera-source.py
bash -n deploy/*.sh
```

The release passed 22 backend tests, both JavaScript suites, parse/syntax checks, XML/assets/source hashes, a clean dependency installation, and C receiver build/load. Screenshots illustrate the interface and do not replace hardware tests.

`tests/preview_ai_visuals.py` provides a loopback-only synthetic fixture without a camera on port 8082. `tests/check_http_ai.py` uses an already-running preview; `--live` adds a bounded 18-second camera run. **Never run live checks against another user's active stream.** Publication work deliberately left the active real stream untouched. No CI workflow is configured in this initial release.

## Installed user services

Templates under `deploy/systemd/` assume **~/MaxCameraApp**. For a different checkout location, change `EnvironmentFile` and `ExecStart` before manual installation. Installed services use port 8080; the separate development preview uses 8081. See [deployment instructions](docs/DEPLOYMENT.md).

Do not use process-name-wide kills to restart the app; they can stop another preview. Target specific systemd units or press Ctrl+C in your own preview terminal. Only one application can control the physical camera at a time.

## Publication contents and exclusions

Git contains source, tests, documentation, XML/provenance, version-locked dependencies, configuration examples and two synthetic screenshots. `SOURCE_MANIFEST.json` records SHA-256 values for unchanged backend/frontend/test files.

**Excluded:** `run/`, authentication/session state, local IP/MAC configuration, real camera captures, enrolled identities, model weights, `pydeps/`, `vendor/`, `.so`, `.pyc` and caches. Local setup generates these files outside version control.

## Security and rights

Access is local-only by default. Three processes are separated with bubblewrap/seccomp, but sandboxing does not replace updates or guarantee protection from every bug. Signed-in users share one account, stream and set of camera settings. A password change invalidates session tokens but does not immediately close already-open WebSockets; forcing all viewers to disconnect requires an agreed restart of the web service.

Never expose **admin/admin**, or send passwords/video through untrusted networks over plain HTTP. Tunnel providers can see TLS-terminated traffic. Public-access helpers change reachability and some system settings; they are not required for quick start and never run automatically.

The reviewed contract is pinned to [MaxCamera 07dfb588](https://github.com/MaksimNuzhdin/MaxCamera/commit/07dfb588b9576f827968c47c5798d9164561346d), firmware **0.3.16-ai-linux-dev**. Firmware, the camera-side AI runtime, models, galleries and calibration are prepared separately through upstream procedures.

This publication does not select a new project-wide license. Existing per-file and upstream rights remain applicable. Archivo retains its [SIL Open Font License](frontend/fonts/OFL.txt); separately downloaded Python packages retain their own licenses. [Third-party notices](docs/THIRD_PARTY_NOTICES.md) also cover fonts embedded in the PDF.
