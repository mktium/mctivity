# MKTLIN01 travel calibration safety-layer deployment

Date: 2026-09-17 (Asia/Shanghai)  
Branch: `feature/v1.4.1-axis-d-uservo-anti-sway`  
Source commit: `aa14e92965f033199b5de4b8ece895cc65c82566`

## Scope

This release adds the pure C endpoint-calibration decision layer and its
`-Wall -Wextra -Werror` unit test. It is deliberately fail-closed: if the
runtime cannot supply validated cyclic current feedback, endpoint calibration
is rejected with `MCTIVITY_TRAVEL_FAIL_MISSING_CURRENT_FEEDBACK`. The live
motiond command path is not wired to this state machine yet.

The target's current Uservo PDO contract was read-only verified as TxPDO
`0x1A00 = 6041/6061/6064/60FD`; it has no cyclic current or torque feedback.
No PDO remap, SDO write, mode change, enable, or motion command was sent.

## Artifact and target

- Source archive: `/tmp/mctivity-aa14e92.tar.gz`
- Source archive SHA-256: `ce807228e24b7da6e17c36e3edcc5bed3a71f6ee2d25668d85bcf0ca820c0c23`
- Target release: `/opt/mctivity-releases/v1.4.1-axis-d-uservo-anti-sway-aa14e92`
- Backup: `/var/backups/mctivity/pre-axis-d-uservo-anti-sway-aa14e92-20260917T101417Z`
- Active link after deployment: `/opt/mctivity` -> the target release above
- Remote push: not performed

Only `mctivity-hmi.service` and `mctivity-kiosk.service` were restarted.
`mctivity-motiond.service` was not restarted.

## Read-only acceptance

- profile `axis-d-uservo`, logical axis D, physical position 0;
- commissioning inhibit remains `1` in both axis and HMI environments;
- no physical enable, homing, calibration, or motion operation was requested;
- the HMI remains in the uncalibrated/read-only state;
- previous release links and configuration backups were retained.

The next software step is to select and validate the vendor-native homing/stall
current path or an explicit PDO remap. That decision must be completed before
any physical endpoint-calibration run.
