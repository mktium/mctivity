# Version Record: v1.4.0-preview.3

Date: 2026-09-02
Status: source/preview release candidate for controlled laboratory use

## Version Line

- `v1.2.0`: first public modular baseline
- `v1.3.0-preview.1`: multi-point integration baseline
- `v1.4.0-preview.1`: auxiliary encoder, Axis C, homing, and anti-sway development baseline
- `v1.4.0-preview.2`: endpoint anti-sway timing and expanded motion-control baseline
- `v1.4.0-preview.3`: consolidated auxiliary encoder, homing, and anti-sway preview

The version number is unchanged during preparation of this source release. Runtime files and settings on the controller have not been replaced.

## Included Functions

- Axis A/B servo feedback and control
- Axis C auxiliary encoder feedback
- single-point, incremental, jog, point, and multi-point positioning
- velocity, torque, and electronic-gear modes where selected by profile
- current-position and torque-obstruction homing with configurable backoff
- full-path and endpoint anti-sway positioning
- basic fault flags, hexadecimal raw error codes, and manual fault reset

Vendor-specific fault diagnosis, mapping datasets, detail dialogs, and manual-derived troubleshooting text are not included. The generic command-error and motion-confirmation dialogs remain.

## Anti-Sway Baseline

- full-path mode uses a three-impulse ZVD input shaper
- endpoint mode uses a period-matched trapezoid and begins deceleration at an integer multiple `N x T` of the measured natural period
- the auxiliary encoder provides angle display, period calibration, start-phase gating, and evaluation, not real-time trajectory correction
- measured period and UI settings persist across mode changes and service restarts

Earlier laboratory validation used a `500 mm` rope and a measured natural period near `1.41965 s`. These values describe one setup, not application defaults.

## Consolidated Fixes

- direction and unit conversion are applied to positioning, limits, homing, and gearing
- single-point targets persist across enable transitions; position display uses the current coordinate source after homing
- obstruction homing assigns the end coordinate before reporting controlled backoff
- anti-sway target dragging uses the target marker; target/period state persists across mode switches
- multi-point start, stop, clear, and restart are isolated by a per-run identifier
- base zero commands remain available without the optional homing module
- incomplete module dependency chains are omitted rather than left partially enabled
- anti-sway is assembled only in the full profile with independent feedback-axis capabilities

## Publication Preparation

- non-loopback HMI access requires a random token; status, commands, and UI state use authorization
- standalone enable/movement tools require an opt-in build and explicit pre-hardware confirmation
- release checks scan current source, filenames, and intervening Git snapshots without embedding private values
- excluded data-package paths are rejected by the release checker, including in intermediate commits
- browser mock status contains only generic samples and rejects control commands before network dispatch
- narrow-screen feedback and control panels no longer overlap the raw fault/reset row
- both standard and full profiles omit the excluded display module, without removing basic fault-reset capability
- the motion daemon, homing and anti-sway algorithms, protocol dispatch, and GPL license are unchanged by the display-module removal

## Ownership

On 2026-09-02 the project owner confirmed that the program code copyright and logo belong to 上海诣儒信息科技有限公司, that the logo has a trademark registration certificate, and that the company created the architecture diagrams in-house. README and NOTICE record that confirmation without claiming an independent certificate inspection. No certificate or registration number is packaged. Third-party license terms remain separate.

## Validation Scope

Current release checks cover Python/JavaScript/JSON/shell syntax, all three profile assemblies, command ownership, multi-point concurrency, HTTP authentication, removed asset routes, raw fault display, explicit manual reset, encoder read-only behavior, and read-only mock behavior.

The source release preflight is local and hardware-free. Browser checks use isolated simulated backends; no real drive is contacted. Reproducible test commands are in the release guide.

On 2026-09-02 the local preflight passed all 26 Python tests and the Node.js raw-fault/reset/mock regression. The isolated Chrome browser smoke test passed at 1920x1080 and 390x844, with no script errors, failed resource requests, or unexpected device commands. These results cover the release checks described above, not all physical-machine behavior.

Historical checks on earlier snapshots include controller-side native IgH compilation and supervised motion tests of existing modes, homing, encoder feedback, electronic gearing, and anti-sway. They are not evidence of new testing after this publication change.

Native builds of the modified standalone test tools and motion-enabled regression of the multi-point concurrency changes remain outstanding. Report these as untested in the laboratory demo, not as passed.

## Remaining Publication Review

Review the exact commit and archive, third-party attribution, and any other public branches/tags/assets independently. Cleaning this candidate does not clear existing remote history. No upload, service restart, deployment, or motion operation is part of this preparation.

## Known Limits

- preview software, not production-ready or safety-certified
- fixed EtherCAT topology/PDO mapping and no universal discovery
- open-loop anti-sway, without real-time angle correction
- no complete global software travel envelope across every low-level command path
- runtime state, site settings, logs, credentials, and private restoration copies are excluded
