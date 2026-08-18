# Release Notes

Full release status and safety notes are in [../RELEASE_NOTES.md](../RELEASE_NOTES.md).

## Version v1.4.1

Version `v1.4.1` adds the project-specific Uservo DS1 axis D topology, profile metadata, per-revolution scaling, and a motion-daemon commissioning inhibit. The legacy A/B topology remains the default.

`v1.2.0` is a preview/source release for controlled lab evaluation and integration testing. It is not a certified safety system and must not be used as the sole protection layer for machinery.

## Version v1.4.0

Version `v1.4.0` adds an optional local touchscreen kiosk layer for industrial-panel deployments.

This version:

- adds `ui-touchscreen` as an independent runtime module
- adds `mctivity-kiosk.service` for local fullscreen browser startup
- adds installation and verification scripts for kiosk deployments
- keeps motion daemon, Web HMI, and local kiosk startup as separate service responsibilities
- keeps the HMI local-only by default for touchscreen deployments
- adds a touch-safe system menu with guarded staged poweroff dry-run and long-press confirmation
- delegates real poweroff to `mctivity-poweroff.service`, outside the HMI dependency chain

## Version

Version `v1.2.0` marks the first modular baseline of `mctivity`.

This version:

- added a new user-visible mode: `incremental`
- completed the first usable modular assembly baseline
- kept current deployment and main usage model intact

## Highlights

- Profile/module/feature-registry modular assembly chain is now in place
- Incremental displacement is integrated as an independent feature
- HMI mode entry visibility now follows actual assembly state
- Torque mode is now represented in feature registry and dispatch like the other assembled modes
- Existing axis-control behavior is preserved as the baseline
- Public-release defaults are safer: local-only Web HMI, optional API token, bounded request size, and safe fallback behavior
- FV3 device access is controlled by the `axis.device.fv3.access` capability from the dual-axis module
- `/api/command` rejects unknown commands and forwards only whitelisted payload fields

## Functional Scope

Base axis modules:

- `axis-feedback-panel`
- `axis-control-panel`

Main assembled features in this version:

- `position`
- `incremental`
- `velocity`
- `gear_cam`

Other modes still present in profile/capability space:

- `jog`
- `point`
- `homing`
- `torque`

Current note:

- `torque` is registered in the modular feature path, while its HMI behavior in this release remains staged rather than full CST torque PDO control

## Main Changes

### 1. Modular Runtime Assembly

- `profiles/*.json`
- `modules/**/module.json`
- `mctivity_hmi/feature_registry.json`
- capability-gated `/api/command`
- assembly observability:
  - `/api/capabilities`
  - `/api/health/modular`

### 2. Feature Dispatch and Contracts

- `feature_dispatch.py`
- `feature_contract.py`
- `ProtocolAdapter`
- `FeatureContext`

### 3. Incremental Displacement

- new `incremental` mode
- embedded motion-curve editor in IPC HMI
- `move_curve_rel` path
- independent module/capability boundary:
  - `feature-logic-incremental`
  - `feature-hmi-incremental`
  - `axis.mode.incremental.execute`

### 4. HMI Assembly Awareness

- `mode_hmi_module_map`
- mode selector and right-side mode panels follow loaded HMI modules

### 5. Public Release Hardening

- default HMI host changed to `127.0.0.1`
- request Host allowlist defaults to `127.0.0.1`, `localhost`, and `::1`; additional LAN hosts require `MCTIVITY_ALLOWED_HOSTS`
- optional `MCTIVITY_API_TOKEN` support for command/state endpoints
- browser HMI API requests can include the token from the top-bar API Token field
- request payload size bound through `MCTIVITY_MAX_REQUEST_BYTES`
- command/state POST requests require `application/json` and reject foreign browser origins
- strict command and mode validation before motion transport
- payload field sanitization with stable command field order before forwarding to `motiond`
- strict integer validation and configurable motion safety limits before forwarding motion parameters
- `move_curve_rel` drops UI-only mode data and validates blend names before transport
- `motiond` now matches JSON keys strictly and rejects invalid numeric fields instead of treating ordinary strings as field names or zero values
- UI state persistence drops NaN/Infinity and emits standard JSON only
- UI state default path moved out of the code directory
- systemd examples include `StateDirectory` and basic hardening flags

### 6. Device Assembly Gate

- `feature-logic-dual-axis-fv3` provides `axis.device.fv3.access`
- profiles without that capability expose only Axis A
- unsupported or unloaded devices are rejected before command dispatch

## Known Limitations

- Browser hardening headers are not yet complete.
- Global software travel-envelope limits are not yet fully configurable.
- The C motion daemon does not yet use saturated arithmetic in every position/velocity accumulation path.
- `mctivity_ctl.py` bypasses HMI profile and API restrictions by design.
- The motion daemon assumes the configured EtherCAT slave topology.
- This release is not a certified functional safety component.
