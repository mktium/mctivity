# MKTLIN01 D/E Uservo electronic gear — 2026-08-21

## Release and target build

- implementation commit: `cdaa2ca` (`feature/v1.4.1-axis-d-uservo`), pushed to `mktium/mctivity`;
- source archive: `v1.4.1-axis-de-gear-cdaa2ca.tar.gz`;
- source archive SHA-256: `7dfd93128d0848046ec55d91fb418083f6b8167bbb633f26af5475d8869e2c2a`;
- target build flags: `-O2 -Wall -Wextra -Werror`;
- target EtherLab prefix: `/opt/etherlab`;
- target-built motiond SHA-256: `bce8e50b011d447d9e50457568a0e3ca85ff22ff537995b316d35878cf5ef5ed`;
- prepared target release: `/opt/mctivity-releases/v1.4.1-axis-de-gear-cdaa2ca`.

MKTLIN01 release preflight, JSON/profile validation, HMI tests, legacy A/B and
D/E PV regression tests, shell checks, C unit tests, and the real EtherLab
compile all passed. No enable, mode, gear-start, motion, stop, or fault-reset
command was sent.

## No-motion deployment attempt

The pre-switch backup was created at:

`/var/backups/mctivity/pre-axis-de-gear-20260821T150100`

It contains `/etc/mctivity`, motiond/HMI units and realtime drop-ins, the old
active-release target, and the old motiond hash. The old release was retained.
The new link was switched atomically and both required services started with:

```text
MCTIVITY_TOPOLOGY=axis-de-uservo-gear
MCTIVITY_PROFILE=axis-de-uservo-gear
MCTIVITY_COMMISSIONING_INHIBIT=1
MCTIVITY_REQUIRE_REALTIME=1
```

The HMI reported the expected D/E mapping, CSP mode 8, RxPDO `0x1600`, TxPDO
`0x1A00`, default D-master/E-slave 1:1 gear capability, and zero control
outputs. The daemon and HMI were active.

The 60-second acceptance gate failed immediately for an external fieldbus
condition. Read-only EtherLab evidence was:

```text
Master0: Active yes
Slaves: 0
Main link: DOWN
Tx frames: 0
Rx frames: 0
```

Both axes therefore remained non-OP with incomplete WC. This did not meet the
acceptance contract, so the release was rolled back without deleting it. The
old PV release is active again, both services are active, and the restored
configuration was safety-overridden to keep `MCTIVITY_COMMISSIONING_INHIBIT=1`.
The rollback reason is recorded in the backup's `rollback-reason.txt`.

The new CSP gear profile is not accepted as deployed until the EtherCAT link
is restored and a separately authorized, still-inhibited no-motion gate can
complete. No drive fault was reset automatically; no motion acceptance was
performed.
