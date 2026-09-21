# MKTLIN01 HMI travel-persistence repair — 2026-09-21

## Scope

This deployment fixes the single-axis D HMI state merge so ordinary profile
saves cannot erase runtime-owned travel calibration. Endpoint recording,
endpoint clearing, and stopped-axis zero reconciliation remain the only paths
that can replace calibration and software-zero fields.

No EtherCAT PDO, profile, axis environment, motiond binary, or drive command
was changed. No enable, mode-switch, calibration, stop, or motion command was
sent by the deployment.

## Source and tests

- Branch: `feature/v1.4.1-axis-d-uservo-anti-sway`
- Local commit: `728b2d4` (`Preserve travel calibration across HMI saves`)
- Push: not performed; the branch remains local.
- Changed files: `mctivity_hmi/mctivity_hmi.py`,
  `mctivity_hmi/test_dual_uservo_hmi.py`,
  `docs/axis-d-uservo-anti-sway.md`
- HMI test files were run in separate Python processes to isolate their
  profile-specific environment setup; all test files passed.
- Python compilation and `git diff --check` passed.

## Target and backup

- Host: `mktlin01` (`192.168.1.201`)
- Active release: `/opt/mctivity-releases/v1.4.1-axis-d-uservo-anti-sway-68ca401`
- Active link: `/opt/mctivity`
- Backup directory:
  `/var/backups/mctivity/hmi-travel-persist-20260921T081500Z`
- HMI source before: `7eb7ada5007c48d47a0d134a36b8381c4d7f65e9622a6a41b12cf8763b326111`
- HMI source after: `cea20ae64d7a3e37d8378aaa2d1ba858d3515231d2c623d093927976a80d6562`
- State file before/after:
  `d4aeca12773f0b1afefe6c808dc16af5d66291188f6aee9cd3433944802d7b21`

The state file was intentionally not edited. It still contains no endpoints,
so the HMI correctly reports `uncalibrated` until the operator records both
ends again.

## Controlled deployment result

- Replaced only `/opt/mctivity/mctivity_hmi/mctivity_hmi.py`.
- Restarted only `mctivity-hmi.service`.
- HMI service: `active/running`, restart count `0` after deployment.
- `/api/status?device=mctivity`: returned successfully; D was disabled,
  stationary, `cw=0`, target equaled actual position, and OP/WC were healthy.
- `/api/travel?device=mctivity`: returned successfully and reported the
  expected pre-existing `travel_not_calibrated` state.
- `mctivity-motiond.service`: PID remained `36951`, restart count remained `0`;
  it was not restarted.

## Separate observation

The post-deployment read-only status showed a latched
`communication_timing_fault` in motiond while the axis remained disabled and
stationary. The deployment did not reset or otherwise handle this fault. It
requires a separate operator-authorized motiond fault/restart investigation.

