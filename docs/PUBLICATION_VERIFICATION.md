# Publication verification · 6 November 2026

## Scope

This source release packages the current MaxCameraApp camera GUI and AI overlays for a standalone repository. The running original application, active stream, camera firmware, models, gallery, account state, router and firewall were not changed by publication work.

## Passed on the clean export

- Clean dependency download/install from official PyPI into a separate ignored `pydeps/`, using system Python 3.10 on Linux x86-64.
- Exact direct/transitive dependency versions recorded in `requirements.txt` and `requirements.lock.txt`.
- C receiver rebuilt with `-O2 -std=c11 -Wall -Wextra -Werror -shared -fPIC`; resulting library loaded successfully.
- NumPy/OpenCV/aiohttp imports with freshly installed wheels.
- All 22 backend unit tests using the published XML and fake wire peer, including unavailable/stale/error/repeated-operation/cleanup paths.
- Both JavaScript suites: AI semantics and visual overlays.
- JavaScript parse checks; Python compile/AST checks; shell syntax checks.
- Source-manifest hashes for unchanged backend/frontend/test files.
- All precompressed assets decompress to the corresponding final source and contain no timestamp/filename metadata.
- Pinned camera XML Git blob and SHA-256 matched upstream MaxCamera commit `07dfb588b9576f827968c47c5798d9164561346d`.
- Independent file/privacy review: no runtime/account files, captured scenes, model weights, gallery, local configuration, personal home paths or obvious secret patterns in the inspected export.
- Archivo font copyright/OFL notice included.
- Desktop/mobile screenshots inspected; synthetic scene and fabricated results explicitly disclosed. PNG ancillary metadata removed without changing pixels.

## HTTP/current hardware status

The original preview was reachable. Its unauthenticated and cross-origin requests were rejected, and protected transport/internal AI commands were denied. The integration helper then stopped at its **already-active stream** guard. The remaining idle/live stages were not rerun, so another user's active stream was not interrupted.

Earlier bounded hardware/UI verification is preserved in [AI update report](AI_UPDATE_REPORT.md) and [overlay report](AI_VISUALS.md). Historical runs do not imply a new full hardware qualification for this publication. Screenshot fixture output is not camera accuracy evidence.

## Deliberately not claimed

No new optical/recognition accuracy, real-gallery, plate/OCR quality, endurance, physical MicroSD-boot, browser-matrix or outside-network validation was performed. Runtime portability beyond the documented Linux x86-64/Python baseline remains unqualified. Model readiness is reported through the camera contract, not inferred from UI screenshots.

No CI workflow is configured in this initial source release. Local checks above are not GitHub CI. Dependency versions are pinned but wheel bytes are not hash-locked; future registry/platform availability is not guaranteed.

## Publication contents and exclusions

Included: application source, upstream XML/provenance, tests, portable example configuration/service templates, setup/build instructions, Russian guide/PDF source, documentation and synthetic UI screenshots.

Excluded: `run/`, auth/session records, `pydeps/`, `vendor/`, `.so`/`.pyc`/caches, local `deploy/maxcamera.env`, actual scene captures, enrolled identities, models and weights. The legacy process-name-wide restart helper was omitted; documented restarts target explicit systemd services or the owned foreground preview.

The publication does not select a new project-wide license. Existing per-file rights and the upstream camera project's rights remain applicable.
