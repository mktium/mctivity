# Assembly Quickstart

## Assembly Model

1. A file in `profiles/` selects module ids.
2. Each `modules/**/module.json` declares dependencies and capabilities.
3. `mctivity_hmi/feature_registry.json` assigns modes and commands to features.

When assembling existing functions, edit a profile. Change the feature registry only when adding a feature or changing command ownership.

## Ready-Made Profiles

### Minimal

- axis feedback and control panel
- single-point positioning
- UI state persistence

### Standard

- minimal profile
- incremental, jog, point, and multi-point positioning
- current-position and torque-obstruction homing
- velocity mode

### Full

- standard profile
- torque mode
- electronic gear
- Axis B FV3 support
- Axis C auxiliary encoder support
- full-path and endpoint anti-sway positioning

Keep each motion feature's `feature-logic-*` and `feature-hmi-*` modules together unless a manifest explicitly says otherwise. Missing dependencies remove the incomplete feature from the active assembly and add a capability warning.

## Manual Start

```bash
cd /opt/mctivity/mctivity_hmi
MCTIVITY_PROFILE=full python3 mctivity_hmi.py
```

The HMI listens on `127.0.0.1` by default. Controlled LAN access requires explicit host configuration and a randomly generated API token:

```bash
export MCTIVITY_API_TOKEN="$(python3 -c 'import secrets; print(secrets.token_hex(32))')"
MCTIVITY_PROFILE=full \
MCTIVITY_WEB_HOST=0.0.0.0 \
MCTIVITY_ALLOWED_HOSTS=mctivity.local,axis-hmi.local \
python3 mctivity_hmi.py
```

Enter the generated token in the HMI. Keep it in protected installation configuration for persistent service use, never in Git. Non-loopback startup rejects empty or short/repetitive tokens. This HTTP service is for isolated trusted networks, not internet exposure. See [Security and Operation](../SECURITY.md).

Real anti-sway motion is disabled by default:

```bash
MCTIVITY_ANTI_SWAY_EXECUTE_ENABLED=1 python3 mctivity_hmi.py
```

Set this only on a controlled machine after verifying the measured period, transmission direction, limits, emergency stop, and safe test envelope.

The auxiliary encoder is enabled by default in the supplied daemon. Disable it when the third EtherCAT slave is not installed:

```bash
MCTIVITY_AUX_ENCODER_ENABLED=0 ./mctivity_motiond
```

## Profile Switch

```bash
sudo ./scripts/mctivity-set-profile.sh full
./scripts/mctivity-modular-verify.sh
```

`mctivity-modular-verify.sh` expects a running HMI. Set `BASE_URL` when it is not on the default local port.

## Verify an Assembly

```bash
./scripts/mctivity-release-preflight.sh
curl -s http://127.0.0.1:2015/api/capabilities
```

Check `profile`, `active_features`, `enabled_feature_keys`, `feature_assembly`, and `warnings`. A healthy assembly has the expected loaded features and no unresolved dependency warnings.

## Rules of Thumb

- Use `minimal` for the smallest single-axis UI.
- Use `standard` for normal axis control without the extra devices/modes in `full`.
- Use `full` only when the configured EtherCAT topology and devices match the daemon.
- Do not expose the built-in HTTP server directly to the public internet.
- Do not treat software limits or UI confirmations as machine safety functions.
