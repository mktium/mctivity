# MKTLIN01 single-axis anti-sway software deployment

Date: 2026-09-17 (Asia/Shanghai)  
Branch: `feature/v1.4.1-axis-d-uservo-anti-sway`  
Source commit: `847c86e61aa371b54cd07d83d2e01befa338b25e`

## Scope

This release adds the pure endpoint-contact calibration decision model and
shrinks the linear-travel HMI panel for fixed touch displays. The model is
offline-only: it has no EtherCAT, socket, SDO, enable, mode, or motion side
effects. Endpoint calibration remains unavailable to the runtime until a
separate motiond integration and physical authorization phase.

The HMI change is compact at viewport heights at or below 820 px and does not
rely on vertical scrolling. The commissioning inhibit remains enabled.

## Artifact and target

- Source archive: `/tmp/mctivity-847c86e.tar.gz`
- Source archive SHA-256: `8bfcdb691a77b5ac80492b7ae045d395d6b232d66859abf3fc76e4030ad48fa7`
- Target release: `/opt/mctivity-releases/v1.4.1-axis-d-uservo-anti-sway-847c86e`
- Backup: `/var/backups/mctivity/pre-axis-d-uservo-anti-sway-847c86e-20260917T092910Z`
- Active link after deployment: `/opt/mctivity` -> the target release above
- Remote push: not performed; the branch has no valid upstream remote

Only `mctivity-hmi.service` and `mctivity-kiosk.service` were restarted.
`mctivity-motiond.service` was not restarted and no EtherCAT control command was
sent.

## Read-only acceptance

Verified on MKTLIN01 after the HMI restart:

- profile `axis-d-uservo`, logical axis D, physical position 0;
- EtherCAT slave OP, Domain WC `3/3`, `wc_complete=true`;
- `MCTIVITY_COMMISSIONING_INHIBIT=1` in both axis and HMI environments;
- `enabled=false`, `servo_request=false`, `moving=false`, `fault=false`;
- control word `0`, target held at actual position `0`, gear/session inactive;
- `calibration_actions_available=false`, reason `runtime_not_connected`;
- endpoint state `uncalibrated`, anti-sway disabled and not ready.

The daemon's lifetime scheduling counters reported `deadline_miss_count=2` and
`rt_skipped_periods=3`, while `rt_schedule_timing_fault=false` and
`communication_timing_fault=false`. These counters predate this HMI-only
deployment and were not reset or acted on during acceptance.

## Rollback

Keep commissioning inhibit set. The previous release and configuration backup
are retained at the paths recorded above. Rollback consists of restoring the
previous `/opt/mctivity` link and restarting only the services appropriate to
the restored release; do not delete either release.
