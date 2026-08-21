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

## Retry after EtherCAT link restoration

After the fieldbus link was restored, a read-only check confirmed two OP
Uservo slaves, Link UP, WC `6/6`, an armed timing gate, and the old PV release
still inhibited and stationary. The prepared gear release was then activated
again with the following retry backup:

`/var/backups/mctivity/pre-axis-de-gear-retry-20260821T152300`

The gear HMI/API profile and D/E routing were correct. During the inhibited
warmup, D remained fault-free but E reported a drive fault. Independent
read-only evidence was:

```text
P0/D 0x603F = 0x0000
P1/E 0x603F = 0x8100
```

At the stop point both axes were still disabled and stationary with
`servo_request=false`, `moving=false`, `gear_running=false`, `cw=0`, and each
target equal to actual position. The gear session and safety latch were false;
no fault reset, enable, mode, gear-start, stop, or motion command was sent.
The 60-second no-motion gate is paused, and the E fault is left untouched for
separate operator disposition.

## No-motion gate completion after E fault reset

The operator subsequently reset E. Read-only verification then showed both
drive fault registers clear (`0x603F=0x0000`), and both API statuses reported
`fault=false`. With the release still inhibited, the gate was rerun and passed
60/60 one-second samples. During the gate:

- both Uservo slaves stayed OP with combined WC `6/6`;
- `commissioning_inhibit=true`, timing guard armed, and communication fault
  false;
- D/E stayed disabled with `servo_request=false`, `moving=false`, and
  `gear_running=false`;
- gear session and safety latch stayed false, and `cw=0`;
- each target remained equal to its actual position;
- realtime deadline misses and skipped periods remained `0/0`.

The gear release is now accepted for the inhibited no-motion deployment phase
and remains active at `/opt/mctivity-releases/v1.4.1-axis-de-gear-cdaa2ca`.
The direct SDO read attempted after the gate returned EtherLab I/O errors while
motiond owned the active master; the immediately preceding post-reset SDO
reads and the continuous API status gate were clean. No enable, mode, gear
start, stop, or motion command was issued. First motion acceptance remains a
separate plan and still requires explicit confirmation.

## Superseded phase-search gate

The first gear implementation carried a session-level
`phase_search_confirmed` acknowledgement gate. After reviewing the official
MotorHost manual, that gate was identified as an application safety policy,
not a requirement to manually confirm phase search before every ordinary
enable. The follow-up change removes the software acknowledgement from
ordinary enable and `gear_start`; drive-side electrical-angle alignment, if
enabled by the saved drive parameters, remains entirely drive-controlled.

## Follow-up deployment: remove non-vendor acknowledgement gate

Source commit `fdad9ae` was pushed to
`mktium/mctivity:feature/v1.4.1-axis-d-uservo`. The immutable source archive
used for the target build was
`v1.4.1-axis-de-gear-no-phase-fdad9ae.tar.gz`, SHA-256
`46b3a2610fc0996c765b2aba4b827ecfb8e847a9532f80018a463e454ebaa0cc`.
MKTLIN01 compiled the source against its real `/opt/etherlab` with
`-O2 -Wall -Wextra -Werror`; the deployed `mctivity_motiond` SHA-256 is
`0205ff41b1399b2006020113668dce62358139b5a635e27a67dc302e31974eb0`.

The pre-switch backup is
`/var/backups/mctivity/pre-axis-de-gear-no-phase-20260821T082407Z` and the
new release is `/opt/mctivity-releases/v1.4.1-axis-de-gear-no-phase-fdad9ae`.
The active profile is `axis-de-uservo-gear`; both services are active and
`MCTIVITY_COMMISSIONING_INHIBIT=1` is enforced in the runtime environment.

After startup, the real EtherCAT master reported Link UP, two Uservo slaves in
OP, and combined WC `6/6`. Read-only API status confirmed that the removed
phase-search fields are absent. D/E remained disabled and stationary with
`servo_request=false`, `moving=false`, `cw=0`, and each target equal to actual;
the gear session and safety latch stayed false. D nevertheless reported a
drive fault during the restart transition while E remained fault-free. No
fault reset, enable, mode, gear-start, stop, or motion command was sent.
The 60-second no-motion acceptance is paused on that fault and must not be
claimed complete until the operator handles it separately. The release remains
active but inhibited; the fault is intentionally left for separate disposition.

## Post-reset no-motion acceptance completion

The operator cleared the remaining D drive fault. A read-only EtherCAT check
then reported Link UP, Master0 in Operation, and both Uservo slaves in OP.
Both API endpoints reported `fault=false`, `operational=1`, and complete WC
(`wc=6`, `wc_complete=true`).

With `/opt/mctivity-releases/v1.4.1-axis-de-gear-no-phase-fdad9ae` still
active and `MCTIVITY_COMMISSIONING_INHIBIT=1`, a 60-second read-only gate was
run using 60 one-second samples for both D (`mctivity`) and E (`mctivity_e`).
All 60 samples passed:

- D/E stayed disabled with `enabled=false`, `servo_request=false`,
  `moving=false`, and `gear_running=false`;
- `cw=0`, and each target remained pinned to its actual position (D `1`, E
  `0` counts for the baseline and final sample);
- no gear session or safety latch became active;
- the timing guard remained armed, `communication_timing_fault=false`, and
  realtime deadline misses/skipped periods remained `0/0`;
- no position change, mode command, enable request, gear command, stop
  command, or motion command was observed or sent.

The inhibited no-motion deployment phase is therefore accepted. The release,
backup, and source/build hashes remain the ones recorded above. First enable
and first-motion acceptance remain a separate, explicitly authorized phase;
this record does not authorize either action.

## Commissioning inhibit removal requested by operator

The operator subsequently requested removal of the commissioning inhibit. The
current configuration was backed up at
`/var/backups/mctivity/pre-inhibit-remove-20260821T164735`, including both
axis/HMI environment files and the two systemd unit files. The active release
was not changed.

`MCTIVITY_COMMISSIONING_INHIBIT` was changed from `1` to `0` in both
`/etc/mctivity/axis.env` and `/etc/mctivity/hmi.env`. `mctivity-motiond` and
`mctivity-hmi` were restarted and both returned `active`. No enable, mode,
gear-start, gear-stop, stop, or motion command was sent.

After the restart, EtherCAT remained Link UP with two Uservo slaves in OP and
combined WC `6/6`. Both axes remained disabled and stationary with
`servo_request=false`, `moving=false`, `gear_running=false`, `cw=0`, and each
target equal to actual; the gear session and safety latch were clear and
timing counters remained healthy. However, both D and E reported a drive fault
after the restart transition. Per the fault policy, no automatic reset was
performed and the next read-only acceptance is paused until the operator
resets both faults and they are independently confirmed clear.
