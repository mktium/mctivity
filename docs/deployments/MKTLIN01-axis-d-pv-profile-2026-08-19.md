# MKTLIN01 Axis D native PV profile deployment — 2026-08-19

## Scope and safety gate

This deployment parameterizes the DS1-E4806N-4I native EtherCAT PV path and enables the Axis D velocity HMI. It does not authorize motor motion. Throughout build, installation, and acceptance:

- `MCTIVITY_COMMISSIONING_INHIBIT=1`
- MotorHost USB and process remain disconnected
- no `enable`, `jog_velocity`, `set_mode`, `fault_reset`, phase-search acknowledgement, stop, or other control request is sent
- EtherCAT control is owned only by `mctivity-motiond`

## Parameter source

`modules/axis/device/uservo/pv/module.json` is canonical. The shared resolver supplies both HMI and the motiond launcher. With 10000 counts/rev it resolves:

| Parameter | Canonical value | Runtime value |
|---|---:|---:|
| target speed | 222 rpm | 37000 cnt/s |
| maximum speed | 999 rpm | 166500 cnt/s |
| acceleration | 2222 rpm/s | 370333 cnt/s² |
| deceleration | 2222 rpm/s | 370333 cnt/s² |
| stop deceleration | 2222 rpm/s | 370333 cnt/s² |
| HMI velocity step | 10 cnt/s | 10 cnt/s |

PV uses mode `3`, RxPDO `0x1601`, and TxPDO `0x1A01`. The HMI profile is explicitly `axis-d-uservo-pv`; the unit has no `full` default. The drive uses `0x6084` for both profile and stop deceleration, so those manifest values must remain equal.

## Backup and rollback

Before changing the active symlink, preserve the old release target, `/etc/mctivity`, installed motiond/HMI units and realtime drop-in, service snapshots, and old binary hash under a timestamped `/var/backups/mctivity` directory. The old release is retained.

Rollback keeps inhibit active: stop kiosk/HMI/motiond, restore the recorded environment/unit/drop-in files, atomically point `/opt/mctivity` to the recorded old release, reload and verify systemd, start the prior motiond and HMI serially, then repeat the read-only disabled-state gate. Rollback does not reset a drive fault.

## Deployment evidence

This section is completed from the target after the pushed implementation commit is built and the read-only acceptance finishes.

- implementation commit: pending
- final documentation commit: pending
- active release: pending
- source archive SHA-256: pending
- motiond SHA-256: pending
- backup directory: pending
- target EtherLab build: pending
- `systemd-analyze verify`: pending
- HMI listener `127.0.0.1:2015`: pending
- profile/topology/Axis D/velocity routing: pending
- OP and WC complete: pending
- `fault=false`, SDO `0x603F=0`: pending
- `enabled=false`, `servo_request=false`, `moving=false`, `cw=0`: pending
- deadline miss/skip zero: pending

## Known risk

Drive fault `0x8100` (`Communication_DS_301`) has occurred during earlier restart/shutdown switching. It remains a known transition risk and is never concealed by automatic fault reset. If it recurs, acceptance stops with inhibit active and the journal, EtherCAT state, and read-only `0x603F` evidence are retained.
