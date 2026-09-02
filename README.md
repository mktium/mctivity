# mctivity v1.4.0-preview.3

> Preview/source release for controlled laboratory evaluation and integration testing. This software is not a certified safety system and must not be the sole protection layer for machinery.

`mctivity` is a modular EtherCAT motion-control stack with a Python Web HMI, a C motion daemon, profile-based feature assembly, and local command-line tooling.

## Features

- Axis A and Axis B servo feedback and control
- Axis C auxiliary encoder feedback
- single-point absolute positioning
- incremental motion-curve execution
- multi-point table positioning with repeat count and controlled stop
- current-position and torque-obstruction homing
- velocity, torque, and electronic-gear modes
- full-path and endpoint anti-sway positioning
- basic fault state and raw error-code display
- transmission direction, unit conversion, soft limits, and UI-state persistence

The feature set is assembled through:

```text
profile -> module manifests -> capabilities -> feature registry -> dispatch
```

See [Architecture](docs/architecture.md) for module boundaries and [Assembly Quickstart](docs/assembly-quickstart.md) for configuration.

## Platform

- Linux with systemd
- Python 3
- IgH EtherCAT Master headers and runtime
- a browser for the Web HMI

The supplied motion daemon is configured for the current EtherCAT topology:

```text
Slave 0: primary servo axis
Slave 1: secondary servo axis
Slave 2: SICK AFM60A auxiliary encoder (optional)
```

Set `MCTIVITY_AUX_ENCODER_ENABLED=0` when the auxiliary encoder is not present. Other devices or slave orders require source-level PDO/topology adaptation.

## Build

```bash
cd mctivity_pdo_monitor
make
```

The build requires `ecrt.h` and `libethercat` from the IgH EtherCAT Master environment.

The default build produces the monitor and motion daemon only. Standalone drive-enable and movement tests require `make motion-test-tools` and an explicit `--confirm-motion` first argument. These tools bypass HMI protections; read [Security and Operation](SECURITY.md) before using them.

## Run Locally

The HMI binds to `127.0.0.1` by default:

```bash
cd mctivity_hmi
MCTIVITY_PROFILE=standard python3 mctivity_hmi.py
```

Use the full profile when Axis B, electronic gearing, torque mode, or the auxiliary encoder is required:

```bash
cd mctivity_hmi
MCTIVITY_PROFILE=full python3 mctivity_hmi.py
```

Real anti-sway motion is disabled by default. Enable it only in a controlled test environment:

```bash
MCTIVITY_PROFILE=full \
MCTIVITY_ANTI_SWAY_EXECUTE_ENABLED=1 \
python3 mctivity_hmi.py
```

## Controlled LAN Access

Do not expose the built-in HMI directly to the public internet. For a controlled local network, explicitly set the bind address, allowed hostnames, and an API token:

```bash
export MCTIVITY_API_TOKEN="$(python3 -c 'import secrets; print(secrets.token_hex(32))')"
MCTIVITY_WEB_HOST=0.0.0.0 \
MCTIVITY_ALLOWED_HOSTS=mctivity.local,axis-hmi.local \
python3 mctivity_hmi.py
```

Non-loopback binding is rejected without a sufficiently long, varied token (at least 32 characters). Use a random generator, not a memorable phrase. The example generates a fresh token each time; persist installation tokens only in protected site configuration outside this repository and enter the same token in the HMI.

The token protects `GET /api/status`, `POST /api/command`, and `GET/POST /api/ui_state`. The HMI token field stores it in browser `sessionStorage`. Page assets, capabilities, and modular health remain public. The built-in HTTP server does not encrypt tokens; restrict access to an isolated trusted network. See [Security and Operation](SECURITY.md).

## systemd Installation

The supplied units use a dedicated `mctivity` user and `/var/lib/mctivity` for runtime state:

```bash
sudo groupadd --system mctivity || true
sudo useradd --system --gid mctivity --home-dir /var/lib/mctivity --create-home mctivity || true
sudo mkdir -p /opt/mctivity
sudo cp -a . /opt/mctivity
sudo cp systemd/99-ethercat-mctivity.rules /etc/udev/rules.d/
sudo cp systemd/mctivity-motiond.service /etc/systemd/system/
sudo cp systemd/mctivity-hmi.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now mctivity-motiond.service mctivity-hmi.service
```

Use a systemd drop-in for site-specific profile, network, topology, token, and anti-sway settings. Do not commit site secrets or runtime state into the repository.

## Verify

```bash
./scripts/mctivity-release-preflight.sh
./scripts/mctivity-modular-verify.sh
```

On the target EtherCAT development machine, also run:

```bash
cd mctivity_pdo_monitor
make clean
make
```

## Safety

- Install and test emergency stop, hardware limits, drive limits, and mechanical protection.
- Verify transmission direction and travel limits before enabling motion.
- Torque-obstruction homing intentionally searches for a mechanical obstruction; use low speed and independently limited torque.
- Anti-sway control in this release is open loop. The auxiliary encoder is used for monitoring, period calibration, phase gating, and evaluation, not real-time trajectory correction.
- `mctivity_ctl.py` talks directly to `motiond` and bypasses HMI authentication, capability gates, and parameter sanitization.

Read [Release Notes](RELEASE_NOTES.md) before connecting real hardware.

## Version History

- `v1.2.0`: first public modular assembly baseline
- `v1.3.0-preview.1`: multi-point positioning integration baseline
- `v1.4.0-preview.1`: auxiliary encoder, Axis C, homing, and anti-sway positioning
- `v1.4.0-preview.2`: endpoint anti-sway timing, motion confirmation, and expanded motion limits
- `v1.4.0-preview.3`: parameter persistence and direction fixes; endpoint anti-sway restored and verified as period-matched `N x T` deceleration timing

## Documents

1. [Release Notes](RELEASE_NOTES.md)
2. [Architecture](docs/architecture.md)
3. [Assembly Quickstart](docs/assembly-quickstart.md)
4. [Version Record](docs/version-record-v1.4.0-preview.3.md)
5. [Security and Operation](SECURITY.md)
6. [Third-Party and Rights Notices](NOTICE.md)

## License and Publication Status

Copyright (c) 2026 上海诣儒信息科技有限公司 for the project-developed software and company-created architecture diagrams.

The existing project license is [GNU GPL version 3](LICENSE); this review does not replace it. Third-party material retains its own rights and is not automatically covered by the project's license. This release does not include vendor-specific fault diagnosis, diagnostic data packages, or detail dialogs. Basic fault flags, raw error codes, and manual fault reset remain available. See [Notices](NOTICE.md) and the [Release Guide](docs/release.md).

Logo ownership: 上海诣儒信息科技有限公司. The project owner confirms that the logo has a trademark registration certificate. The software license does not grant trademark rights; see [Notices](NOTICE.md).
