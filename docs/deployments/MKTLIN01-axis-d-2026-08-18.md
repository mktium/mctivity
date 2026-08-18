# MKTLIN01 Axis D Deployment - 2026-08-18

## Release

- implementation commit: `dbb3a94` (`Add Uservo axis D commissioning profile`)
- branch: `feature/v1.4.1-axis-d-uservo`
- target: `MKTLIN01`
- target release path: `/opt/mctivity-releases/v1.4.1-axis-d`
- active path: `/opt/mctivity`
- pre-deployment backup: `/opt/mctivity-backups/pre-axis-d-20260818T130934`
- rollback directory retained on target: `/opt/mctivity.rollback.20260818T130934`

The Git archive SHA-256 matched before extraction:

```text
3c69e359492e4c7712a79cb80cc2430e455ae390a44317f0d9f06bb4a54f2bc9
```

The target ran the repository release preflight and rebuilt `mctivity_motiond` against `/opt/etherlab`. The resulting binary resolved `libethercat.so.1` from `/opt/etherlab/lib`.

## Installed Configuration

`/etc/mctivity/axis.env`:

```text
MCTIVITY_TOPOLOGY=axis-d-uservo
MCTIVITY_PROFILE=axis-d-uservo
MCTIVITY_COMMISSIONING_INHIBIT=1
```

Environment-file drop-ins were installed for `mctivity-motiond.service` and `mctivity-hmi.service`. The existing target service user and the existing EtherCAT dependency drop-in were retained.

## No-Motion Acceptance Evidence

- `ethercat slaves`: `0  1:0  OP  +  Uservo`
- EtherCAT link: up
- lost frames: `0`
- backend topology: `axis-d-uservo`
- backend counts/rev: `10000`
- backend commissioning inhibit: `true`
- domain working counter: complete (`wc=3`)
- statusword fault: false
- controlword: `0`
- enabled: false
- servo request: false
- moving: false
- target position followed actual position
- two samples three seconds apart both reported `pos_raw=1612`
- the Axis D verifier used a non-energizing `set_mode(position)` request and confirmed it was rejected with `commissioning_inhibit`
- all four services were active: EtherCAT, motion daemon, HMI, and kiosk

The deliberate application restart latched drive error `0x8100` in SDO `0x603f`. This is a communication-class fault. The release therefore retains a commissioning-safe fault reset that can pulse only controlword `0x0080`; it cannot enter the enable sequence. Final acceptance requires the fault bit to be clear after this reset while inhibit remains active.

No enable or motion command was sent during this deployment.

## Remaining Gate

This deployment authorizes EtherCAT OP and feedback observation only. Do not remove `MCTIVITY_COMMISSIONING_INHIBIT=1` until the separate onsite motion-readiness review covers E-stop/STO, drive-side limits, mechanical clearance, direction, and the first small move.

The default Uservo TxPDO does not expose `0x603f`, so a statusword fault can be detected but the detailed drive error code remains unavailable in the HMI.
