# mctivity v1.4.1 Release Notes

## v1.4.1 Uservo Axis D

`mctivity v1.4.1` adds a selectable single-slave topology for the Uservo DS1-E4806N drive used by project CYMG20241203.

- adds the `axis-device-uservo` module and `axis-d-uservo` profile
- implements a dedicated Uservo Axis D CSP path rather than treating the legacy Axis A timing as proven for this slave
- matches live EtherCAT identity `0x00666999:0x00004806`
- matches the drive's default `0x1600` and `0x1a00` PDO assignment
- uses `10000` counts per motor revolution and a 1 ms cycle
- limits the initial UI envelope to +/-1 revolution, defaults to 0.01 revolution at 30 rpm, and caps the profile UI at 222 rpm
- omits velocity/CSV mode because the selected default RxPDO has no `0x60ff` target-velocity entry
- leaves the legacy MCTIVITY/FV3 topology unchanged unless explicitly selected
- enforces `MCTIVITY_COMMISSIONING_INHIBIT=1` in the motion daemon during first deployment
- permits only a non-energizing `0x0080` fault-reset pulse under inhibit so a restart-latched communication fault can be cleared without entering the enable sequence
- reports the actual backend topology, scale, and inhibit state for drift-resistant verification
- pins identity, PDO, DC, cycle, watchdog, and supported-mode claims to the official `XActant-E-XML-6120R.xml` ESI and DS1 product documentation
- replaces catch-up bursts and counter-derived DC time with a no-catch-up deadline scheduler and explicit realtime/WC telemetry
- bounds command-socket work inside each 1 ms cycle and refreshes DC application time during the shortened safe shutdown sequence
- requires locked memory and `SCHED_FIFO` for Axis D and latches a safe hold after any post-arm timing or communication failure
- removes the non-vendor session-level phase-search acknowledgement from Uservo enable and D/E electronic-gear start; drive-side electrical-angle alignment remains controlled by the saved drive parameters

The first deployment gate permits EtherCAT OP, working-counter validation, and position feedback only. The verification script uses a non-energizing mode-selection request to confirm the low-level commissioning lock. It does not send enable and does not authorize motor enable or motion.

## v1.4.0 Touchscreen Kiosk

`mctivity v1.4.0` adds an optional local touchscreen kiosk layer on top of the v1.2.0 modular baseline.

This version:

- adds the `ui-touchscreen` runtime module
- adds `mctivity-kiosk.service` for local fullscreen browser startup
- adds kiosk install and verification scripts
- keeps motion control and Web HMI services separate from local display startup
- uses local-only HMI binding by default for touchscreen deployments
- adds a touch-safe system menu with guarded, dry-run-verifiable staged poweroff support
- delegates real poweroff to a dedicated `mctivity-poweroff.service` so shutdown continues after HMI/kiosk stop

## v1.2.0 Release Notes

## Release Status

`mctivity v1.2.0` is a source release and preview release for evaluation, integration testing, and controlled lab use.

This version includes the modular HMI, profile-based capability gating, dual-axis MCTIVITY/FV3 support, EtherCAT motion daemon integration, systemd service files, and release preflight checks.

This release is not certified as a functional safety system and must not be used as the only protection layer for real machinery.

## Safety Notice

`mctivity` controls motion hardware. Incorrect configuration, software defects, network exposure, invalid motion parameters, or EtherCAT/drive misconfiguration may cause unexpected machine movement.

Before using this software with real hardware, users must ensure:

- physical emergency stop is installed and tested
- hardware limit switches are installed and tested
- drive-side torque, velocity, acceleration, position, and following-error limits are configured
- the machine is tested first without load or with the mechanical system safely isolated
- operators understand the difference between HMI-level restrictions and drive-level safety limits
- the system is used only on a trusted machine and trusted network

The software must not be treated as a replacement for drive safety functions, hardware interlocks, emergency stop circuits, or machinery risk assessment.

## Important Deployment Notes

### Web HMI Exposure

The Web HMI listens on `127.0.0.1` by default. Do not expose it directly to the public internet.

For controlled LAN access, set an API token and explicitly allow expected hostnames:

```bash
MCTIVITY_WEB_HOST=0.0.0.0
MCTIVITY_ALLOWED_HOSTS=mctivity.local,axis-hmi.local
MCTIVITY_API_TOKEN=<strong-token>
```

For shared or untrusted networks, place the service behind an authenticated reverse proxy or VPN.

### Browser Security

The HMI rejects foreign browser origins for command/state POST requests and restricts Host headers by default.

This version does not yet provide a complete hardened browser header set such as `Content-Security-Policy` or `X-Frame-Options`. Use the HMI only from trusted local workstations, and do not embed it into third-party dashboards in operational environments.

### Motion Range Limits

The HMI API validates command names, device access, JSON content type, numeric format, velocity, acceleration, move time, torque, and gear ratio limits.

This version does not yet provide a complete global machine-travel envelope for all position-related fields, such as:

- `pos`
- `delta`
- `target_delta_counts`
- `min_pos`
- `max_pos`

Users must configure safe travel and force limits at the drive, controller, and mechanical levels. Do not rely only on the HMI frontend for travel limitation.

### Low-Level Motion Daemon Access

`mctivity_ctl.py` is a local low-level daemon client. It communicates directly with `mctivity_motiond` and bypasses:

- HMI API token checks
- profile capability gating
- frontend command limits
- HMI command sanitization

Use `mctivity_ctl.py` only for local diagnostics, commissioning, and trusted maintenance workflows. Public or remote API access should go through the HMI service.

### EtherCAT Topology

The v1.2.0 motion daemon is designed around the current dual-slave EtherCAT topology:

```text
Slave 0: MCTIVITY axis
Slave 1: FV3 axis
```

The HMI profile system can hide or disable FV3 access at the UI/API layer, but the underlying motion daemon still expects the configured EtherCAT topology unless modified.

Users with a single-axis setup should verify daemon behavior in their own EtherCAT environment before deployment.

## Recommended Operating Modes

For evaluation:

```bash
MCTIVITY_PROFILE=standard
MCTIVITY_WEB_HOST=127.0.0.1
python3 mctivity_hmi/mctivity_hmi.py
```

For full dual-axis local testing:

```bash
MCTIVITY_PROFILE=full
MCTIVITY_WEB_HOST=127.0.0.1
python3 mctivity_hmi/mctivity_hmi.py
```

For controlled LAN testing:

```bash
MCTIVITY_PROFILE=full
MCTIVITY_WEB_HOST=0.0.0.0
MCTIVITY_ALLOWED_HOSTS=mctivity.local,axis-hmi.local
MCTIVITY_API_TOKEN=<strong-token>
python3 mctivity_hmi/mctivity_hmi.py
```

## Preflight Checks

Before publishing or deploying, run:

```bash
./scripts/mctivity-release-preflight.sh
```

On an EtherCAT development machine, also build the motion daemon:

```bash
cd mctivity_pdo_monitor
make clean
make
```

The C build requires the IgH EtherCAT Master headers and libraries, including `ecrt.h`.

## Known Limitations

- Browser hardening headers are not yet complete.
- Global software travel-envelope limits are not yet fully configurable.
- The C motion daemon does not yet use saturated arithmetic in every position/velocity accumulation path.
- `mctivity_ctl.py` bypasses HMI profile and API restrictions by design.
- The motion daemon assumes the configured EtherCAT slave topology.
- This release is not a certified functional safety component.
