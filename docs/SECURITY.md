# Security model and safe operation

## Trust and exposure

The default setup binds the development preview to `127.0.0.1:8081`. Installed user services use `MAXCAMERA_LISTEN` and port 8080; keep `127.0.0.1` unless you intentionally approve wider access. No installer automatically changes the firewall, router, login linger or public access.

A new isolated state starts with `admin` / `admin`. Change it in **Account** before giving anyone access or exposing a network endpoint. The application has one shared account, not per-user permissions. Anyone signed in can view the camera, change supported image settings, save camera presets and reset the camera.

## Defense boundaries

Three bubblewrap/seccomp-confined processes split untrusted HTTP handling, application logic and camera UDP traffic. The web process has no normal LAN/Internet interface. The bridge keeps pre-opened fixed-camera sockets, rejects arbitrary network/transport writes and refuses to displace another controller. Each process applies no-new-privileges and denies risky system calls after initialization.

Bridge policy protects stream destination/port, IP configuration, provisioning/factory operations, heartbeat/transport settings and internal AI latch/selector commands from browser writes. The browser's disabled buttons are not the security boundary; the backend rechecks policy.

This reduces attack surface. It is not a formal proof that malicious traffic cannot escape, and it depends on the kernel, bubblewrap, Python/native dependencies and application correctness. Keep them current. The current filter is x86-64-specific; do not remove confinement to make an unsupported platform start.

## Authentication and browser controls

- Passwords are salted scrypt hashes in ignored runtime state, with mode 600. Plaintext passwords are not committed.
- Session cookies are `HttpOnly` and `SameSite=Strict`; session lifetime is 12 hours.
- Same-origin checks protect API mutations; strict CSP limits scripts/assets to the app.
- Login backoff follows repeated failures and includes a global throttle. Behind some proxies multiple clients may share one perceived address.
- Changing the password invalidates other session tokens. Already-open WebSockets are not immediately reauthenticated or closed after a password change or token expiry. To force-disconnect all viewers, restart the web service locally in an agreed maintenance window. Never publish `run/`, cookie/session data, account hashes or tunnel credentials.

## HTTP and tunnels

Plain HTTP does not encrypt passwords or video. Use trusted local access or a correctly configured HTTPS reverse proxy/tunnel. A tunnel provider can see the traffic it terminates. The optional SSH and Cloudflare processes are not confined by this application's per-component sandbox.

Review [deployment](DEPLOYMENT.md) before any public-access helper. `enable-public-ip.sh` changes UFW, persistent login linger and listen binding. `disable-public-ip.sh` closes/rebinds HTTP but does **not** undo linger or remove router forwarding. UPnP `router-forward.py add/remove` changes router configuration. These are manual operator tools, not required for normal operation.

## Privacy of scenes and AI results

The scene, recognized identities, decoded QR strings and plate text may contain private data. Get appropriate consent and comply with local rules before filming, recognition or sharing. The app does not qualify recognition accuracy. Use results as engineering output, not as sole evidence for a consequential decision.

Browser overlays render labels as literal text. They do not enroll identities or store labels. The source release includes no real scenes, face gallery, weights, account files or private network configuration. Documentation screenshots are explicitly synthetic, with fabricated identities/detections/results and no camera footage.

## Reporting a problem

Do not post credentials, scene imagery, identity galleries or access tokens in a public issue. A minimal report should give app revision, platform, firmware revision, a sanitized description, relevant states and a reproducible synthetic case if possible. If an issue could expose users or bypass confinement, avoid publishing exploit details until a private contact path is agreed with the repository owner.

## Limits of verification

The release's offline tests exercise the reviewed AI protocol and rendering behavior; previous bounded hardware/UI checks are documented separately. They do not establish resistance to all attackers, sustained-load behavior, optical/recognition accuracy or compliance certification. See [publication verification](PUBLICATION_VERIFICATION.md).
