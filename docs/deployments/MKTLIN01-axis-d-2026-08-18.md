# MKTLIN01 Axis D Deployment - 2026-08-18

## Release

- implementation base commit: `dbb3a94` (`Add Uservo axis D commissioning profile`)
- deployed release commit: `80a40de` (`Persist MKTLIN01 EtherCAT interface isolation`)
- branch: `feature/v1.4.1-axis-d-uservo`
- target: `MKTLIN01`
- target release path: `/opt/mctivity-releases/v1.4.1-axis-d`
- active path: `/opt/mctivity`
- pre-deployment backup: `/opt/mctivity-backups/pre-axis-d-20260818T130934`
- rollback directory retained on target: `/opt/mctivity.rollback.20260818T130934`

The Git archive SHA-256 matched before extraction:

```text
9f980911d95edcf5834acf90ebbcbefd80af715ec0b8af3669fd3a70b9b0d53a
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
- two final samples ten seconds apart both reported `pos_raw=1612`
- the Axis D verifier used a non-energizing `set_mode(position)` request and confirmed it was rejected with `commissioning_inhibit`
- `scripts/mctivity-axis-d-verify.sh` completed successfully against the deployed release
- all four services were active: EtherCAT, motion daemon, HMI, and kiosk

The deliberate application restart latched drive error `0x8100` in SDO `0x603f`. The official XActant fault table identifies it as `Communication_DS_301`: after the slave enters OP, loss of PDO communication for the configured timeout raises the alarm. The default timeout is 100 ms and object `0x36B5` configures it. The original 300-cycle shutdown used a stale DC application time and exceeded this timeout; that shutdown behavior is a defect, not evidence that the official PDO map is wrong.

The target network configuration already declared `enp3s0` as an EtherCAT-only interface, but the kernel still had IPv6 autoconfiguration enabled and assigned a link-local address. `/etc/sysctl.d/90-mctivity-ethercat-enp3s0.conf` now persistently disables IPv6 on that interface only; Wi-Fi and Tailscale are unchanged. After applying it and issuing the safe fault reset, `0x603f` remained `0x0000` during the initial observation window and the position remained unchanged.

Kernel logs still showed frequent domain working-counter changes, even though each reported sample returned to `3/3`; earlier logs also contained skipped/unmatched EtherCAT datagrams. The IPv6 correction improved the observed drive-fault behavior but does not resolve the EtherCAT timing instability. Treat this as a blocking commissioning risk: investigate it before removing inhibit or attempting the first motion.

A controlled no-motion comparison isolated a strong load correlation: with the local Chromium kiosk stopped and only `motiond` plus the HMI server running, the working-counter changes almost disappeared during a 20-second sample; restarting the kiosk immediately restored changes every second. A temporary CPU-affinity/real-time-priority experiment reduced some bursts but did not eliminate them, so it was reverted rather than left as an undocumented runtime dependency. This host currently uses the generic EtherCAT module over the Linux `r8169` driver. The next investigation should prioritize a native EtherCAT NIC driver or a better-supported dedicated NIC, then repeat the loaded kiosk timing test.

No enable or motion command was sent during this deployment.

## Remaining Gate

This deployment authorizes EtherCAT OP and feedback observation only. Do not remove `MCTIVITY_COMMISSIONING_INHIBIT=1` until the separate onsite motion-readiness review covers E-stop/STO, drive-side limits, mechanical clearance, direction, and the first small move.

The default Uservo TxPDO does not expose `0x603f`, so a statusword fault can be detected but the detailed drive error code remains unavailable in the HMI.
