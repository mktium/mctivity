# MKTLIN01 HMI marker and anti-sway save repair — 2026-09-22

## Scope

This deployment fixes two HMI issues in the single-axis D Uservo profile:

1. the current-position marker applied the reversed encoder direction twice;
2. the anti-sway period had no explicit save action, so the visible default
   could remain only a browser value while the backend still had no period.

The repair adds a visible `保存` button, preserves calibrated endpoints during
period saves, reports whether the period is unconfigured or ready to enable,
and keeps the anti-sway switch separately controlled. No motiond source,
EtherCAT PDO, profile, axis environment, or drive command was changed.

## Source and tests

- Branch: `feature/v1.4.1-axis-d-uservo-anti-sway`
- Commit: `c16ec15` (`Fix linear marker direction and save anti-sway period`)
- Pushed to `origin/feature/v1.4.1-axis-d-uservo-anti-sway`.
- HMI test files were run in separate Python processes; all passed.
- Python compilation, embedded JavaScript syntax check, and `git diff --check`
  passed.

## Target and backup

- Host: `mktlin01` (`192.168.1.201`)
- Active release: `/opt/mctivity-releases/v1.4.1-axis-d-uservo-anti-sway-68ca401`
- Active link: `/opt/mctivity`
- Backup directory:
  `/var/backups/mctivity/hmi-marker-anti-sway-20260922T041658Z`
- HMI source before:
  `cea20ae64d7a3e37d8378aaa2d1ba858d3515231d2c623d093927976a80d6562`
- HMI source after:
  `ba64e818b68c40937a6dad1efad4a887151891e22755fda61a71bc11527ac742`
- State file before/after:
  `215fa5d260b98b4aa8c7ab729bb0dc5d6e9ad39d72c3acfad0507232f869e4c8`

## Controlled deployment result

- Replaced only `/opt/mctivity/mctivity_hmi/mctivity_hmi.py`.
- Restarted only `mctivity-hmi.service`.
- HMI service: `active/running`, restart count `0` after deployment.
- `mctivity-motiond.service`: PID remained `736`, restart count remained `0`;
  it was not restarted.
- D status: `enabled=false`, `servo_request=false`, `moving=false`, `cw=0`,
  no drive fault, OP/WC healthy, and `communication_timing_fault=false` at
  the post-deployment check.
- Current UI state remains calibrated with `calibration_state=both_valid` and
  `anti_sway_enabled=false`; `sway_period_ms` is still unset until the
  operator presses the new `保存` button.
- The served page was verified to contain the direction fix, the travel marker
  reverse-order handling, and the `travelSaveSwayPeriod` control.

No enable, mode switch, calibration, stop, or motion command was sent.

