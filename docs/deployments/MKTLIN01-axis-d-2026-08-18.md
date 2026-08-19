# MKTLIN01 Axis D Deployment - 2026-08-18

## Scope and Safety State

This deployment adds the DS1-E4806N-4I/Uservo Axis D profile and hardens the
1 ms EtherCAT loop. All live validation was performed with:

```text
MCTIVITY_TOPOLOGY=axis-d-uservo
MCTIVITY_PROFILE=axis-d-uservo
MCTIVITY_COMMISSIONING_INHIBIT=1
MCTIVITY_REQUIRE_REALTIME=1
```

No enable, position, velocity, torque, homing, or motion command was sent.
The drive remained disabled with controlword `0` and the target position
pinned to the actual position.

## Repository Release

- repository: `mktium/mctivity`
- branch: `feature/v1.4.1-axis-d-uservo`
- implementation: `8c753ec` (`Harden Axis D EtherCAT realtime loop`)
- reset-on-fork follow-up: `f940475`
- deployed fix: `46b636a` (`Fix reset-on-fork policy detection on Linux`)
- active archival release: `/opt/mctivity-releases/v1.4.1-axis-d-rt-c537f82`
  (same tested binary as `46b636a`, plus the deployment record)
- active symlink: `/opt/mctivity`
- pre-release backup: `/opt/mctivity-backups/pre-axis-d-rt-20260818T1527`
- pre-native-driver backup: `/opt/mctivity-backups/pre-ec-r8169-20260818T1552`

The target verified archive SHA-256 before extraction:

```text
305082e9e7ac548e74d303eb25f9a85302061848790a5e588c23ef4cad584fc4
```

The target release preflight passed. `mctivity_motiond` also compiled on the
target against `/opt/etherlab` with `-O2 -Wall -Wextra -Werror`. Its SHA-256 is:

```text
b75740f5473e8c29704db7ecfe7d9e2c09419f3357b1d71016326863f70c095f
```

The first realtime start correctly failed closed because Linux returned
`SCHED_FIFO | SCHED_RESET_ON_FORK` from `sched_getscheduler()`. On this glibc
version `SCHED_RESET_ON_FORK` is an enum, not a preprocessor macro, so the
first guard did not compile into the binary. Commit `46b636a` masks the Linux
ABI bit unconditionally. The changed target binary hash and the subsequent
FIFO/memory-lock checks prove the fix is active. No EtherCAT or motor action
occurred during that failed start.

## Application Realtime Hardening

The deployed loop now:

- skips expired deadlines instead of sending catch-up bursts;
- uses the actual scheduled deadline for DC application time;
- refreshes DC application time during the bounded shutdown sequence;
- limits TCP accepts, reads, bytes, and commands per PDO cycle;
- fails closed if nonblocking sockets, `mlockall`, or FIFO scheduling fail;
- reports deadline, runtime, WC, memory-lock, and scheduling telemetry;
- requires 1000 consecutive OP/WC-complete cycles before arming;
- latches a communication timing fault on OP/WC/deadline loss after arming;
- clears servo and motion requests and holds `cw=0`, mode `0`, and
  target=actual while inhibited or timing-faulted.

MKTLIN01 runs the process with FIFO priority 70, locked memory, and CPU 2
affinity. CPU affinity alone did not resolve the original WKC problem; it is
retained as conservative host-specific isolation from the NIC IRQ on CPU 1.

## WKC Root Cause and A/B Evidence

Before the native-driver change, MKTLIN01 used `ec_generic` over the Linux
`r8169` driver. The drive was already faulted with `0x603f=0x8100` before this
deployment, and the pre-change kernel log contained 21,151 EtherCAT
SKIPPED/UNMATCHED/WC-related records since 13:00.

The official XActant fault table identifies `0x8100` as
`Communication_DS_301`: PDO communication was lost after OP. The configured
timeout read back as 100 ms at object `0x36B5`.

No-motion A/B tests separated the causes:

1. The hardened loop on `ec_generic+r8169` still changed WC from 3/3 to 0/3
   and logged UNMATCHED/SKIPPED frames while application deadline miss and
   skipped-period counters remained zero.
2. Pinning motiond to CPU 2 did not stop the changes.
3. Temporarily raising `EtherCAT-OP` to FIFO 60 did not stop the changes.
4. Disabling EEE, GRO, checksumming, and VLAN offload did not stop the
   changes; those settings were restored.
5. NIC alignment/error counters did not increase when the WKC changes
   occurred, so historical physical receive errors did not explain the
   continuous event stream.
6. Replacing the generic path with the EtherLab native `ec_r8169` driver
   stopped the WKC changes under the same 1 ms PDO/DC configuration and full
   kiosk load.

The evidence therefore attributes the continuous WKC wave to the generic
Realtek packet path on this host, not to the official Uservo PDO map and not
to a missed application deadline. CPU/load tuning could change frequency but
was not sufficient; the native EtherCAT NIC driver was the effective fix.

## Native EtherCAT Driver

The existing `/usr/local/src/ethercat` source tree includes the 6.12 native
Realtek driver and matches the installed EtherLab 1.6.9 master. A clean copy
was configured with `--enable-r8169 --with-r8169-kernel=6.12` and compiled
against `6.12.74+deb13+1-rt-amd64` before any runtime change.

Installed module:

```text
/usr/lib/modules/6.12.74+deb13+1-rt-amd64/ethercat/devices/ec_r8169.ko
SHA-256 90e222cba0d02fcfede140751b9f0c9210d5ee700668fd73ebb54a3f6c1b7645
```

Persistent `/etc/sysconfig/ethercat` values:

```text
MASTER0_DEVICE="00:e0:67:1d:9b:c5"
DEVICE_MODULES="r8169"
UPDOWN_INTERFACES=""
```

Runtime verification showed `ec_r8169` bound directly to PCI device
`10ec:8168` rev 07, with `ec_generic` and the standard `r8169` module absent.
EtherCAT reported Link UP, one Uservo slave, OP, WC 3/3, and Lost frames 0.

The native module is built for this exact kernel. A kernel upgrade must not be
activated until `ec_r8169.ko` has been rebuilt and verified for the new kernel.
If it is missing, keep commissioning inhibit enabled and do not fall back to
`ec_generic` for motion.

## No-Motion Acceptance

- Axis D verifier: passed with EtherCAT, motiond, HMI, and kiosk active.
- Backend: `axis-d-uservo`, 10000 counts/rev, inhibit true.
- Realtime: FIFO 70, memory locked, application deadline miss/skip `0/0`.
- Safety state: enabled false, servo request false, moving false, `cw=0`.
- Feedback: position stayed at raw count 1611.
- Native-driver 60-second comparison: WC 3/3 throughout, no kernel events.
- Eight-client status/inhibited-set-mode load for more than five minutes:
  Lost frames 0 and no SKIPPED, UNMATCHED, or WC0 events.
- Full kiosk/HMI stability observation ran for 21 minutes 20 seconds before
  the user approved shortening the planned 30-minute wait. During the actual
  window, WC stayed 3/3, Lost frames stayed 0, the kernel added no SKIPPED,
  UNMATCHED, or WC0 event, application deadline miss/skip stayed `0/0`, and
  raw position stayed 1611.

After the native link was stable and the timing guard was armed, one fault
reset was sent while inhibit remained active. The only permitted pulse was
`cw=0x0080`; after it completed, status returned `cw=0`, enabled false,
position 1611, and SDO `0x603f=0x0000`.

## Rollback

1. Keep `MCTIVITY_COMMISSIONING_INHIBIT=1` and stop motiond and EtherCAT.
2. Restore `/etc/sysconfig/ethercat` and the previous EtherCAT modules from
   `/opt/mctivity-backups/pre-ec-r8169-20260818T1552`.
3. Run `depmod -a`, start EtherCAT, and verify the intended driver before
   starting motiond.
4. To roll back the application, restore the active release and configuration
   from `/opt/mctivity-backups/pre-axis-d-rt-20260818T1527`.
5. Do not remove inhibit after a rollback; the generic driver is a known WKC
   instability on this host.

## Remaining Motion Gate

This deployment authorizes only EtherCAT OP, feedback observation, and
inhibited diagnostics. Before any enable or movement, obtain explicit user
confirmation and complete the onsite E-stop/STO, mechanical clearance,
direction, soft-limit, current-limit, and first-small-move checklist.

## 2026-08-19 First-enable incident

After the overnight power-off, the no-motion checks passed and the user
confirmed the environment was clear. Commissioning inhibit was removed for a
staged first-motion test. A restart-related `0x8100` fault was reset, the
disabled position was set as software zero, and only an `enable` request was
sent. The planned +100-count position command was never sent.

During enable, the raw feedback changed by about 949 counts (about 34 degrees
at 10000 counts/revolution) and the motor made a loud noise. The user removed
drive power. The application configuration was immediately restored to
`MCTIVITY_COMMISSIONING_INHIBIT=1`, and `mctivity-motiond.service` was stopped.

The command trace shows that the daemon continuously pinned target position
to current feedback during its enable-settling window; it did not issue the
planned relative move. XActant's official MotorHost documentation states that
incremental encoders perform electrical-angle search on the first enable after
every power-on, and describes strong-pull phase search as actively pulling the
rotor through an angle. The saved MotorHost commissioning screen used the
strong-pull mode. This is the leading explanation for the observed first-enable
movement, but the loud sound is not accepted as a successful phase search.
Encoder resolution/direction, motor phase relationship, identification result,
search current, automatic-search setting, and return-after-search setting must
be checked before another EtherCAT enable.

Corrective action adds a session-scoped phase-search confirmation gate. Axis D
ordinary enable is rejected until the operator separately confirms that the
current power cycle's phase search has completed. The latch resets on daemon
start, EtherCAT communication loss,
timing fault, or drive fault. Deployment and verification of this change remain
strictly no-motion with commissioning inhibit enabled.

### Phase-search gate deployment

- source commit: `3dab970` (`Gate Axis D enable on phase-search confirmation`)
- pushed repository/branch: `mktium/mctivity`,
  `feature/v1.4.1-axis-d-uservo`
- staged target release: `/opt/mctivity-releases/v1.4.1-axis-d-phase-3dab970`
- pre-deployment backup:
  `/opt/mctivity-backups/pre-phase-gate-20260819T103753`
- source archive SHA-256:
  `6f98c0f9e8674faf1057b6754c785e7cd26ddacc00ec60027f6b4c05e045af73`
- target-built `mctivity_motiond` SHA-256:
  `9558f1769b9ef2c0072c96a581269e59769b8ef66089e6adac589a26f0cea423`

The release preflight and complete target Linux/EtherLab build passed with
`-O2 -Wall -Wextra -Werror`. The installer was run with the host's existing
`iiru:iiru` service identity, Axis D profile, realtime requirement, CPU 2, and
`MCTIVITY_COMMISSIONING_INHIBIT=1`. The first installer invocation used its
nonexistent default `mctivity` service user and stopped before writing service
configuration; rerunning with the existing service identity completed.

`mctivity-motiond.service` intentionally remained inactive after deployment so
it could not compete with the user's independent MotorHost test. No runtime
phase-gate acceptance and no motion test are claimed at this point. Runtime
verification must wait until MotorHost is disconnected and control is returned
to EtherCAT; it must begin with inhibit enabled and must not send enable.
