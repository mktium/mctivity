# MKTLIN01 Axis D native PV profile deployment — 2026-08-20

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

The release was first installed without restarting motiond because the physical EtherCAT link was down at the deployment gate. After the link returned, the new motiond was restarted once with inhibit still set. No enable or motion request was sent. The restart reproduced the known drive communication fault `0x8100`, so acceptance is stopped and no reset is attempted.

- implementation commits: `22f8887fb52dfc4405ddd1ff9890942b0ba4a3e0`, `e596331ce521699c6224f814d40c6ac51155deda`
- final documentation commit: pending
- active release symlink: `/opt/mctivity-releases/v1.4.1-axis-d-pv-e596331`
- source archive SHA-256: `17fa7b40ec3527fe2cedb60d3d96a3d8130e5e3e47f225689b1644abebd395b4`
- target-built motiond SHA-256: `661f9fec271d5159e2530d2c76698db4b47f63ec379fb2519a3d7b1d248bfcf1`
- backup directory: `/var/backups/mctivity/pre-axis-d-pv-20260820T011432Z`
- target EtherLab `-Werror` build and release preflight: pass
- `systemd-analyze verify` for EtherCAT/motiond/HMI: pass
- HMI listener `127.0.0.1:2015`: pass
- effective HMI profile/topology/Axis D/velocity capability and route: pass
- profile values `37000`/`166500` cnt/s and `370333` cnt/s²: pass
- `enabled=false`, `servo_request=false`, `moving=false`, `cw=0`, deadline miss/skip zero: pass on the inhibited pre-existing process
- OP and WC complete: pass after link restoration (`OP`, WC `3/3`)
- `fault=true`, backend `err=0`; SDO `0x603F=0x8100` (`Communication_DS_301`): acceptance blocked
- new motiond launcher activation: pass; systemd `ExecStart` uses the launcher and target binary hash matches
- post-restart state: `enabled=false`, `servo_request=false`, `moving=false`, `cw=0`, inhibit true; no control request was sent

The HMI GET-only profile/capability/status check passed, but the `0x8100` fault means the no-motion acceptance is incomplete. Keep commissioning inhibit set, do not issue `fault_reset`, enable, mode, jog, stop, or phase-search commands, and preserve the target journal/SDO evidence for the restart-transition investigation.

## Read-only fault evidence

At 09:26:40 CST, the old motiond released EtherCAT and the new process requested the master. The kernel then reported PREOP, a domain WC transition, an unmatched datagram, and OP roughly one second later. The drive's read-only communication timeout object `0x36B5` is `100 ms` (`0x0064`). This restart/OP gap therefore exceeds the drive's configured communication-loss tolerance. After the restart, the link remained UP with zero new lost frames, OP/WC stayed healthy, and realtime deadline miss/skip counters stayed zero, while `0x603F` remained `0x8100` and statusword `0x0218` retained the fault bit. Objects `0x3008`, `0x3622`, `0x3657`, and `0x3638` all read zero; no phase-search acknowledgement was sent.

This evidence supports a restart-transition investigation. It does not authorize changing `0x36B5`, issuing `fault_reset`, or enabling the drive. A future fix must either remove the EtherCAT communication gap or be explicitly reviewed against the vendor's timeout behavior before any motion test.

## Known risk

Drive fault `0x8100` (`Communication_DS_301`) has occurred during earlier restart/shutdown switching. It remains a known transition risk and is never concealed by automatic fault reset. If it recurs, acceptance stops with inhibit active and the journal, EtherCAT state, and read-only `0x603F` evidence are retained.
