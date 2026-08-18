# Assembly Quickstart

This guide is for users who download the repo and want to assemble only the modules they need.

## The Simple Model

You mainly need to understand three things:

1. `profile`
   decides what to load
2. `module`
   declares what each part provides
3. `feature registry`
   defines which commands and modes belong to which feature

## The Three Places to Touch

### 1. Choose a profile

Files:

- `profiles/minimal.json`
- `profiles/standard.json`
- `profiles/full.json`

For most users, start from `standard` or `full`.

### 2. Add or remove module ids

Edit only the `modules` array in the chosen profile.

Example:

```json
{
  "profile": "custom",
  "domains": ["axis_control_domain"],
  "modules": [
    "axis-feedback-panel",
    "axis-control-panel",
    "feature-logic-single-point",
    "feature-hmi-single-point",
    "feature-logic-incremental",
    "feature-hmi-incremental",
    "ui-state-persist"
  ]
}
```

### 3. Do not edit the feature registry unless needed

File:

- `mctivity_hmi/feature_registry.json`

Only touch this when:

- creating a new feature
- changing command ownership
- changing mode ownership

For normal assembly, editing the profile is enough.

## Recommended Ready-Made Patterns

### Minimal

- feedback
- control panel
- single-point
- UI state persistence

Use:

- `profiles/minimal.json`

### Standard

- minimal +
- incremental
- jog
- point
- homing
- velocity
- UI state persistence

Use:

- `profiles/standard.json`

### Full

- standard +
- torque
- electronic gear
- dual-axis FV3 support

Use:

- `profiles/full.json`

### Uservo Axis D

- one DS1-E4806N slave at EtherCAT physical position 0
- primary HMI axis is labelled Axis D
- no FV3 or legacy MCTIVITY slave is requested
- commissioning inhibit is enabled by the supplied environment template

Use:

- `profiles/axis-d-uservo.json`
- `config/axis-d-uservo.env`

## How to Start

Before using the systemd examples, create the runtime user/group that the sample units expect:

```bash
sudo groupadd --system mctivity || true
sudo useradd --system --gid mctivity --home-dir /var/lib/mctivity --create-home mctivity || true
```

### Manual

```bash
cd /opt/mctivity/mctivity_hmi
MCTIVITY_PROFILE=full python3 mctivity_hmi.py
```

The HMI listens on `127.0.0.1` by default. For controlled LAN access only:

```bash
cd /opt/mctivity/mctivity_hmi
MCTIVITY_PROFILE=full MCTIVITY_WEB_HOST=0.0.0.0 MCTIVITY_ALLOWED_HOSTS=mctivity.local,axis-hmi.local MCTIVITY_API_TOKEN=change-me python3 mctivity_hmi.py
```

To require a token on `POST /api/command` and `GET/POST /api/ui_state`:

```bash
cd /opt/mctivity/mctivity_hmi
MCTIVITY_PROFILE=full MCTIVITY_API_TOKEN=change-me python3 mctivity_hmi.py
```

When token auth is enabled in browser HMI, enter the same token in the top-bar API Token field. The token is kept in `sessionStorage`.

### IPC Profile Switch

```bash
cd /opt/mctivity
sudo ./scripts/mctivity-set-profile.sh full
./scripts/mctivity-modular-verify.sh
```

`mctivity-modular-verify.sh` expects the HMI service to be running. Set `BASE_URL` if the HMI is listening on a non-default port.

## How to Verify

Before packaging or publishing:

```bash
./scripts/mctivity-release-preflight.sh
```

```bash
curl -s http://127.0.0.1:2015/api/capabilities
```

If token auth is enabled:

```bash
curl -s -H 'X-MCTIVITY-Token: change-me' http://127.0.0.1:2015/api/ui_state
```

Look at:

- `profile`
- `active_features`
- `enabled_feature_keys`
- `feature_assembly`
- `warnings`

Healthy modular state:

- `warnings` should ideally be empty
- `feature_assembly.loaded` should contain the features you expect

## Very Simple Rule for New Users

If you are only assembling existing functions:

- edit `profiles/*.json`
- do not edit Python files
- do not edit `feature_registry.json` first
- keep the default local-only HMI unless you intentionally need LAN access
