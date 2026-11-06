# Deployment and operations

## 1. Local-only configuration

Run the bootstrap in a fresh checkout. Copy/configure `deploy/maxcamera.env` locally; it is ignored by Git. The example uses documentation-only addresses and a zero MAC, deliberately not a real target.

- `MAXCAMERA_IP`: verified current camera address, not a permanent DHCP assumption.
- `MAXCAMERA_MAC`: verified camera MAC; discovery and the bridge check it.
- `MAXCAMERA_LISTEN=127.0.0.1`: local-only HTTP edge.

After setting the MAC, `python3 deploy/discover-camera.py` broadcasts for two seconds and returns one matching camera or fails. It does not change camera/network configuration. Update IP manually with the verified result. The running confined bridge cannot redirect itself; restart it after an intentional target change.

## 2. Development preview

Run `./deploy/preview-local.sh` and leave the terminal open. It starts three sandboxed components, uses separate `run/ai-preview/` state, and binds port 8081 to loopback. Ctrl+C cleans up its own components. Existing installed service state is not reused.

A new preview state starts with `admin` / `admin`. Change the password before granting access. If connection is refused, confirm the launcher is alive and read `run/ai-preview/log/`. Do not erase another installation's auth state to fix the preview.

## 3. Optional systemd user services

The supplied templates assume the checkout is **~/MaxCameraApp** using `%h/MaxCameraApp`. A different location needs both `EnvironmentFile` and `ExecStart` updated in each `.service` file. Configure/build the checkout before installation.

The following are manual operator steps, not part of bootstrap:

```sh
mkdir -p ~/.config/systemd/user
cp deploy/systemd/maxcamera-control* ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now maxcamera-control.target \
  maxcamera-control-bridge.service \
  maxcamera-control-web.service \
  maxcamera-control-edge.service
```

Installed services listen on port 8080. Do not run the preview and installed app against the same camera at once. Inspect/restart specific services rather than using process-name-wide kills:

```sh
systemctl --user status maxcamera-control-bridge maxcamera-control-web maxcamera-control-edge
systemctl --user restart maxcamera-control-bridge maxcamera-control-web maxcamera-control-edge
journalctl --user -u maxcamera-control-bridge -u maxcamera-control-web -u maxcamera-control-edge -f
```

Explicitly stop streaming before planned service restarts. User services can end at logout unless login linger is intentionally enabled. Linger is persistent behavior and is not required for foreground local testing.

## 4. Remote access: review first

There is no need to open GigE camera ports publicly. The bridge remains a local gateway to a fixed camera. Public exposure of the web app is a separate decision. Use a strong changed password, HTTPS, maintained software and an access-controlled endpoint. A default account must never be exposed.

### HTTPS tunnels

`deploy/public-tunnel.sh` uses SSH without offering personal keys and forwards to `run/ipc/web.sock`. It stores tunnel host-key state under ignored runtime data and reconnects while running. `deploy/public-tunnel-cloudflare.sh` downloads the official x86-64 Cloudflare client on first use and forwards to the local TCP edge at `http://127.0.0.1:8080`. Thus the normal installed web/edge must be running for Cloudflare. These helpers do not target the separate preview on 8081.

The tunnel provider terminates TLS and can see what it carries, including sign-in and video. The clients are outside the app's own sandbox. Free URLs and service limits can change. Stop the helper to close that tunnel; also check for other independently running exposure paths.

### Plain HTTP/public-IP helper

`deploy/enable-public-ip.sh` changes the UFW firewall, enables login linger, changes listen binding to all interfaces and restarts the edge. The router still requires a forwarding rule and genuine public reachability; carrier-grade NAT can prevent it. Plain HTTP exposes sign-in/video to networks on the path. Prefer a properly secured HTTPS arrangement.

`deploy/disable-public-ip.sh` rebinds loopback and removes the UFW port allowance. It does **not** remove a router forwarding rule or undo login linger. Review and reverse those separately if no longer wanted. `deploy/router-forward.py add/remove` alters UPnP rules; its `check` mode reads router status. Never run network-changing helpers just to troubleshoot an offline camera.

## 5. Data, backups and recovery

`run/` holds local sockets, logs and state. Installed authentication and preview authentication are separate. Back up account state only in a private, access-controlled location. Never commit it. A password recovery reset returns an account to a public known default; do it only locally with public access closed, then set a new password before reopening access.

Camera presets live on the camera, not in Git. **Save to preset**, power-up preset and reset can affect other controllers and should be deliberate. This release does not bundle firmware, model weights, enrolled identities or calibration maps. Follow the upstream camera project's procedures for those.
