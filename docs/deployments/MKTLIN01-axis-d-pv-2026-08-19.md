# MKTLIN01 Axis D PV Upgrade - 2026-08-19

## Scope and vendor basis

This release adds a separate native PV velocity profile for the Uservo
DS1-E4806N-4I. It is based on the official XActant ESI
`XActant-E-XML-6120R.xml` and CiA 402 PV documentation:

- RxPDO `0x1601`: `6040`, `6060`, `60FF`, `60FE:01`
- TxPDO `0x1A01`: `6041`, `6061`, `606C`, `60FD`
- PV mode: `0x6060=3`
- DC cycle: 1 ms; SM2 watchdog remains enabled

The existing `axis-d-uservo` position/CSP profile is unchanged. The new
profile is selected only by `MCTIVITY_PROFILE=axis-d-uservo-pv` and
`MCTIVITY_TOPOLOGY=axis-d-uservo-pv`.

## Repository release

- repository: `mktium/mctivity`
- branch: `feature/v1.4.1-axis-d-uservo`
- commit: `9334312` (`Document Axis D PV deployment gate`), including the PV implementation from `ab61cba`
- target release: `/opt/mctivity-releases/v1.4.1-axis-d-pv-9334312`
- active symlink after deployment: `/opt/mctivity` -> the target release above
- target-built `mctivity_motiond` SHA-256:
  `8f365d11ecbc0acd35f744a51940eae483efae9f631832adc1c7e6e1e2043e39`

The target binary was compiled on MKTLIN01 against its installed EtherLab
headers/libraries with `-O2 -Wall -Wextra -Werror`. The release archive itself
contains source/configuration; the target-built binary is installed separately
as part of deployment.

## Safety state and verification

Deployment retained the existing environment; it did not select the PV profile:

```text
MCTIVITY_TOPOLOGY=axis-d-uservo
MCTIVITY_PROFILE=axis-d-uservo
MCTIVITY_COMMISSIONING_INHIBIT=1
MCTIVITY_REQUIRE_REALTIME=1
```

EtherCAT was not restarted. `mctivity-motiond.service` was restarted once to
load the new binary, with inhibit active. No enable, mode-change, target
velocity, target position, fault-reset, or motion command was sent.

Post-deployment read-only status at 2026-08-19:

- EtherCAT OP: yes; WC `3`, complete: yes
- inhibit: true; enabled: false; servo request: false; moving: false; `cw=0`
- realtime: FIFO priority 70; memory locked; deadline misses/skips `0/0`
- timing guard: armed; communication-timing fault: false
- existing drive fault bit: true (`sw=0x0218`), not cleared by this deployment
- post-restart history counters: `wc_change_count=3`, `wc_incomplete_cycles=1095`; these are not a clean motion-readiness result

Because the drive fault bit is currently set, this is a communication/profile
deployment only, not a motion-readiness approval. Do not issue a fault reset or
remove inhibit without a separate operator decision.

## PV profile staged (2026-08-19 15:48 CST)

At the operator's request, `/etc/mctivity/axis.env` was switched to:

```text
MCTIVITY_TOPOLOGY=axis-d-uservo-pv
MCTIVITY_PROFILE=axis-d-uservo-pv
MCTIVITY_COMMISSIONING_INHIBIT=1
MCTIVITY_REQUIRE_REALTIME=1
```

The previous file was backed up as
`/etc/mctivity/axis.env.pre-pv-20260819T154836`. Only
`mctivity-motiond.service` was restarted; EtherCAT was not restarted. The
post-switch read-only status was OP/WC `3/3`, `control_mode=velocity`,
`enabled=false`, `servo_request=false`, `moving=false`, `cw=0`,
`velocity_actual_cps=0`, `fault=false`, and `communication_timing_fault=false`.
No velocity or enable command was sent. The PV TxPDO has no position actual
entry, so the backend's position field is intentionally not a motion verdict
in this profile.

## PV profile acceleration limits

The PV implementation now configures the drive SDOs at startup (still before
any enable and while inhibit is required):

```text
target speed default: 222 rpm = 37000 cnt/s (PDO 0x60FF)
max profile velocity: 999 rpm = 166500 cnt/s (0x607F)
profile acceleration: 2222 rpm/s = 370333 cnt/s² (0x6083)
profile deceleration: 2222 rpm/s = 370333 cnt/s² (0x6084)
```

The MotorHost USB connection is a separate service/configuration channel. It
may remain open for read-only observation, but it must not download parameters,
reset faults, change modes, enable, or command motion while EtherCAT is the
active cyclic controller. For an actual EtherCAT run, close/disconnect
MotorHost first so there is only one command source.

## Post-ramp read-only check

After the ramp-parameter release was activated, the target remained in OP with
domain WC `3/3`, `commissioning_inhibit=true`, `enabled=false`,
`servo_request=false`, `moving=false`, `cw=0`, and zero realtime deadline
misses/skips. A read-only SDO upload reported drive error code `0x8100` and
statusword `0x1218`; the daemon therefore kept the servo request cleared. No
fault reset, enable, velocity, or motion command was sent. This latched drive
fault must be diagnosed/cleared under the no-motion procedure before any
EtherCAT run.

The MotorHost UI's communication error is therefore not evidence that the PV
parameters failed to apply: EtherCAT is the active cyclic command owner, while
the drive also reports the latched `0x8100` fault. Use only one command source
at a time—stop/disconnect MotorHost before EtherCAT control, or stop the motion
daemon before returning control to MotorHost.

## No-motion fault reset result

After the operator confirmed a fault-only reset, the daemon issued its scoped
reset pulse while all motion gates remained closed. The subsequent read-only
check reported `0x603F=0x0000`, statusword `0x1250`, `cw=0`,
`enabled=false`, `servo_request=false`, `moving=false`, OP/WC `1/3`, and
realtime deadline miss/skip `0/0`. No mode, target, enable, or motion command
was sent. This clears the present latch but is not approval to remove inhibit
or start a velocity test.

## EtherCAT 222 rpm continuous run

After the operator authorized motion, the test sequence registered the
already-completed MotorHost phase search, selected PV (`0x6060=3`), waited for
the 300-cycle enable settle window, and wrote `0x60FF=37000 cnt/s` (222 rpm).
The first three-second run was stopped normally; actual velocity was
`36700..37400 cnt/s`, with no fault, no WC loss, and zero realtime deadline
miss/skip counters.

The operator then requested continuous operation. A second start left the
servo running at the same target. The first five-second observation reported
`36650..37350 cnt/s` (approximately 220..224 rpm), `fault=false`, OP/WC `1/3`,
`cw=0x000F`, `enabled=true`, and realtime deadline miss/skip `0/0`. No stop
command has been sent after this second start; the motor remains under the
EtherCAT PV controller until the operator requests a stop. Do not restore
commissioning inhibit or restart `mctivity-motiond.service` while this run is
active.

## Next gate

After the existing fault is diagnosed/cleared under the approved no-motion
procedure, run `scripts/mctivity-axis-d-pv-verify.sh` only after explicitly
switching to the PV profile while retaining inhibit. A real PV velocity test
(including 222 rpm) remains blocked until the user confirms the test setup,
mechanical clearance, E-stop/STO, direction, and first-speed limit.
