# mctivity v1.4.0-preview.3 Release Notes

## Release Status

`v1.4.0-preview.3` is a source/preview release for controlled laboratory evaluation, integration testing, and module-development work.

It is not stable, production-ready, safety-certified, or suitable for unattended machine operation.

## Highlights Since v1.2.0

- multi-point positioning with editable point table, repeat count, progress display, and controlled stop
- Axis C auxiliary encoder feedback and use as an electronic-gear master
- current-position homing and torque-obstruction homing with configurable backoff
- anti-sway positioning as an independent HMI/logic module pair
- full-path ZVD input shaping and endpoint period-matched positioning
- transmission-direction and unit-consistency fixes across position, homing, and gearing
- stronger UI-state persistence for anti-sway settings and measured period
- touch-oriented HMI updates, motion confirmation dialogs, and responsive feedback layout

## Endpoint Anti-Sway Behavior

Endpoint anti-sway remains an open-loop, period-matched motion profile. Given distance `D`, velocity limit `vmax`, acceleration limit `a`, and measured natural period `T`, it chooses an integer period count `N` and starts deceleration at `N x T`.

Full-path anti-sway is a separate strategy using a three-impulse ZVD shaper. The two strategies are not interchangeable.

The auxiliary encoder provides swing-angle monitoring, period calibration, start-phase gating, and evaluation. It does not perform real-time closed-loop trajectory correction in this release.

## Modular Assembly

New or expanded modules include:

- `feature-logic-multi-point` + `feature-hmi-multi-point`
- `feature-logic-homing` + `feature-hmi-homing`
- `feature-logic-anti-sway-position` + `feature-hmi-anti-sway-position`
- `feature-logic-aux-encoder`

Existing functions continue to use the same profile/module/capability/feature-registry assembly chain introduced in `v1.2.0`.

## Safety Notice

Incorrect configuration, software defects, invalid parameters, network exposure, EtherCAT faults, or drive misconfiguration may cause unexpected motion.

Before enabling motion:

- install and test emergency stop and hardware limit circuits
- configure drive-side velocity, torque, following-error, and travel limits
- verify transmission direction, unit conversion, and software limits
- isolate the mechanism or begin with reduced speed, acceleration, torque, and travel
- keep an operator at the machine during preview testing

The software must not replace drive safety functions, mechanical protection, safety relays, risk assessment, or site operating procedures.

### Homing

Torque-obstruction homing intentionally moves toward a physical obstruction. It may bypass the established software coordinate envelope while searching because the operation defines that coordinate system. Use low speed, limited torque, independent travel protection, and a safe timeout.

### Anti-Sway

Real anti-sway execution is disabled unless `MCTIVITY_ANTI_SWAY_EXECUTE_ENABLED=1` is set. A measured period should be recalibrated whenever rope/rod geometry, payload, center of gravity, or suspension conditions change.

## Network and API Notes

- the HMI binds to `127.0.0.1` by default
- LAN use requires an explicit bind address, allowed hosts, and a randomly generated `MCTIVITY_API_TOKEN`
- non-loopback startup rejects missing or short/repetitive tokens; status, commands, and UI state require the configured token
- foreign browser origins and unsupported JSON payloads are rejected
- the built-in HMI does not provide a complete hardened browser security-header set
- do not expose the built-in server directly to the public internet

`mctivity_ctl.py` connects directly to `motiond` and bypasses HMI authentication, capability gating, and parameter sanitization. Keep it local and trusted.

Standalone enable/movement test tools are excluded from the default build and require an explicit `--confirm-motion` first argument. This is an accidental-use guard, not a machine safety interlock.

## Local Publication Review: 2026-09-02

- replaced private-value release checks with generic, redacted source/history scanning
- added HTTP authentication/configuration tests and a hardware-free C guard test
- documented external dependencies, redistribution boundaries, and installation security
- preserved the existing GPLv3 license; vendor-specific diagnostic packages are not included in this release
- preserved basic raw fault status and manual reset; simulated status cannot dispatch control commands
- corrected narrow-screen feedback/control overlap
- no trajectory-formula, homing-algorithm, or PDO changes in this review

This review is local only: no deployment, service restart, or motion test was performed. Earlier controller build and motion results below apply to previous snapshots, not to these new packaging/security changes. Full native tool compilation and installation testing remain pending on an EtherCAT development target. Publication still requires review of the exact source archive, incoming history, and included third-party attribution; excluded diagnostic content is not part of this release.

### Execution and Cancellation Follow-up

- The HMI and anti-sway feature handler both enforce the execution flag for direct full-path/endpoint curve requests and non-dry preparation requests. Preview and dry run remain available; the adapter defaults to execution disabled.
- Generic stop cancels a running multi-point job. Row dispatch and stop commands use per-axis ordering; other axis jobs remain independent.
- Row timeout, failed status, or execution exceptions trigger a controlled stop attempt and feedback confirmation. Original execution errors and stop errors are retained separately.
- An unconfirmed stop blocks table replacement, clear, restart, enable, reset, and other motion on that axis. Explicit stop retry and disable remain available. Stop request acceptance is not standstill confirmation.
- Changing modes while a multi-point task is active requests cancellation and rejects the mode change until cancellation has finished; repeat the mode selection after standstill is confirmed.
- Mock pages do not read or write live UI configuration. Delayed saves are blocked, and entering/leaving preview reloads the document without carrying preview settings into normal operation.

The multi-point stop state is process-local. Do not restart the HMI to bypass an unconfirmed stop; verify the mechanism and independent protection at the machine. Communication loss can prevent software stopping, so hardware emergency stop and independent limits remain necessary.

### Feedback Validity and Axis Response Follow-up

- Stop confirmation now requires valid EtherCAT working-counter and slave-operational flags, an advancing uint32 cycle counter, and two stationary samples. Missing, malformed, invalid, delayed, frozen, or rolled-back feedback preserves the unconfirmed-stop guard. Row completion uses the same feedback validity checks.
- Multi-point status, stop, table-write, and run responses update only the axis that initiated the request. Per-axis sequencing prevents late responses from overwriting newer state; polling does not race pending table mutations.
- Startup captures the axis and table values before waiting. Switching axes, including away and back, cancels remaining unsent startup steps; a late response cannot start the newly selected axis. Table-edit completion and errors also stay with their original axis.
- New memory and browser regressions cover invalid-feedback recovery, delayed axis responses, same-axis response ordering, and the visible stop-retry control.

These corrections remain local and hardware-free. No trajectory formula, PDO daemon, or homing implementation was changed. The feedback contract and its process-local limits are documented in SECURITY.md.

## EtherCAT Topology

The supplied daemon is configured for:

```text
Slave 0: primary servo axis
Slave 1: secondary servo axis
Slave 2: SICK AFM60A auxiliary encoder
```

The auxiliary encoder can be disabled with `MCTIVITY_AUX_ENCODER_ENABLED=0`. The two servo slave definitions remain required by the supplied daemon. Different devices, PDO layouts, or slave ordering require adapter/topology changes.

## Validation Record

- release preflight and JSON parsing
- Python syntax checks
- JavaScript syntax checks for the embedded HMI and curve editor
- shell syntax checks
- profile/module dependency and capability verification
- automated multi-point concurrency and command-routing regression tests
- automated assembly and HTTP smoke tests for `minimal`, `standard`, and `full`
- basic raw fault display and manual reset regression tests
- direct anti-sway execution-gate, cancellation/error recovery, and mock-configuration isolation regressions
- hardware-free browser smoke tests at desktop and mobile viewports
- GitHub Actions pull-request preflight is configured; no new remote run is claimed here

Historical checks on earlier snapshots, not repeated for this publication change:

- native IgH EtherCAT C build on the laboratory controller
- controlled real-hardware checks for existing axis modes, homing, auxiliary feedback, electronic gearing, and anti-sway motion

The final laboratory anti-sway configuration used a `500 mm` rope and a measured period near `1.41965 s`. These are validation values, not packaged defaults.

## Known Limitations

- preview-quality browser and operational hardening
- no certified functional-safety behavior
- no universal EtherCAT device discovery or PDO adapter configuration
- no complete global software travel envelope across every command path
- some low-level position/velocity accumulation paths are not fully saturated
- open-loop anti-sway only; no real-time swing-angle feedback correction
- raw fault flags and codes only; no vendor-specific diagnosis or troubleshooting guidance
- runtime UI state is site-specific and intentionally excluded from the repository

## Preflight

```bash
./scripts/mctivity-release-preflight.sh
```

On an IgH EtherCAT development target:

```bash
cd mctivity_pdo_monitor
make clean
make
```
