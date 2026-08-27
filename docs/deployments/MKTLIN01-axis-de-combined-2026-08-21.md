# MKTLIN01 D/E combined velocity and electronic gear — 2026-08-21

## Scope

This stage adds one HMI/profile choice that exposes both D/E velocity control
and electronic gearing. It is software-only. The Uservo drive power is
disconnected, so this record does not claim EtherCAT electrical validation,
PDO activation, OP/WC validation, enable, mode switching, gear start, stop,
fault reset, or motion testing.

## Source and software verification

- repository: `mktium/mctivity`;
- branch: `feature/v1.4.1-axis-d-uservo`;
- implementation commit: `c6575a5` (`Add combined D/E Uservo velocity and gear profile`);
- realtime scheduler-fault fix commit: `0f19476` (`Separate scheduler jitter from EtherCAT faults`);
- deployed source commit: `0f19476`;
- latest deployment-documentation commit: `a8d940c`;
- source archive: `/tmp/mctivity-0f19476.tar.gz`;
- source archive SHA-256: `f787e864ca6c136882167c66709095aeca1a597ead35378cdc9e45b73116d038`;
- CYMG documentation commits: `a26fe06`, `26b69f1`, `3f602ba` (local-only);
- release preflight: passed;
- profile/PDO contract validation: passed;
- HMI, launcher, Python, JavaScript, shell, JSON and C unit checks: passed;
- local real-EtherLab motiond build: not available because this workstation has no `/opt/etherlab`;
- MKTLIN01 real-EtherLab build: passed with `-O2 -Wall -Wextra -Werror`.

The new `axis-de-uservo-combined` profile retains the verified CSP map:

- RxPDO `0x1600`: `6040/6060/607A/60FE:01`;
- TxPDO `0x1A00`: `6041/6061/6064/60FD`;
- D = physical position 0, E = physical position 1;
- velocity mode uses bounded software position increments over CSP `0x607A`;
- native `axis-de-uservo-pv` and CSP `axis-de-uservo-gear` profiles remain rollback targets.

## Target deployment

SSH access to MKTLIN01 was restored. The source archive was already transferred
and the target rebuilt `mctivity_motiond` against the real `/opt/etherlab` with
`-O2 -Wall -Wextra -Werror`.

- release: `/opt/mctivity-releases/v1.4.1-axis-de-combined-34c6fff`;
- target motiond SHA-256: `4465795e9866e85ffddd024b761771f775c7085418ae89ff023cdfabfd6cf230`;
- pre-deploy backup: `/var/backups/mctivity/pre-axis-de-combined-34c6fff-20260822T015100Z`;
- active link: `/opt/mctivity` resolves to the combined release;
- `/etc/mctivity/axis.env`: `MCTIVITY_PROFILE=axis-de-uservo-combined`,
  `MCTIVITY_COMMISSIONING_INHIBIT=0` (the operator's current setting was preserved);
- motiond and HMI restarted successfully and are `active`;
- read-only capabilities gate passed: D=P0, E=P1, velocity and electronic-gear
  features present, gear control available, and velocity execute capability present.

No enable, mode, gear start/stop, fault reset, or motion command was sent. The
drive-power/electrical and PDO/OP motion validation remains intentionally out of
scope; first enable and first motion require a separate explicit authorization.

## 2026-08-25 scheduler-jitter fix deployment

The combined release was rebuilt and redeployed after a field stop showed that
one host scheduler slip was being mislabeled as an EtherCAT communication fault.
The fix counts host deadline skips separately: one or two consecutive skipped
1 ms periods do not latch; three consecutive skipped periods while control is
active latch `rt_schedule_timing_fault`. Real OP/WC/link failures remain
immediate fail-closed conditions.

- release: `/opt/mctivity-releases/v1.4.1-axis-de-combined-0f19476`;
- target motiond SHA-256: `54004b47d544878ee57548e8135be953647819b2f87a5d1135986157a71f8019`;
- pre-deploy backup: `/var/backups/mctivity/pre-axis-de-combined-0f19476-20260825T085610Z`;
- motiond and HMI restarted successfully and are `active`;
- combined capability gate passed; `MCTIVITY_COMMISSIONING_INHIBIT=0` was preserved;
- read-only post-deploy state: both axes OP/WC `6/6`, disabled, stationary,
  `cw=0`, position targets equal actual positions, and both timing-fault flags
  false immediately after restart.

The subsequent read-only check found D/E drive fault bits set after the service
restart (`sw=0x0218` and `sw=0x1218` respectively), while OP/WC remained healthy.
No automatic fault reset was attempted. Therefore motion regression and the
60-second no-motion acceptance gate are paused pending separate operator fault
handling; no enable, mode, gear, stop, reset, or motion command was sent.

## 2026-08-25 — restricted HMI motiond restart upgrade

The HMI was upgraded with a `重启 motiond` action for service recovery. The
action is server-gated on both assembled axes being disabled and stationary,
`servo_request=false`, no gear/sync session, and controlword `0`. It uses the
exact systemd command through a dedicated sudoers rule for the HMI service user;
it cannot execute arbitrary root commands and does not reset drive faults.

The implementation was tested offline, including blocked enabled/moving/gear
states, non-zero controlword, exact permission dry-run, safe execution path,
and disabled-feature behavior.

The target build and staged deployment were completed:

- source commit: `d380071` (pushed to `mktium/mctivity`);
- source archive: `/tmp/mctivity-d380071.tar.gz`;
- source archive SHA-256: `22f0dc344de3ae6c4b7ede94c13524ab11af0be03df00c17378240c001463541`;
- target build: `/opt/mctivity-releases/v1.4.1-axis-de-combined-d380071`;
- target-built motiond SHA-256: `54004b47d544878ee57548e8135be953647819b2f87a5d1135986157a71f8019`;
- pre-deploy backup: `/var/backups/mctivity/pre-axis-de-combined-d380071-20260825T093201Z`;
- real `/opt/etherlab` build with `-O2 -Wall -Wextra -Werror`: passed;
- exact `iiru` sudoers rule and `visudo` validation: passed;
- HMI restart: passed; motiond PID remained `14417`, so motiond was not restarted.

The read-only OP/WC gate initially passed, but during the 60-second check both
axes changed to `al_state=0`, `operational=0`, `wc=0`, and
`wc_complete=false`. The check was stopped immediately. Per the rollback rule,
the active link and HMI configuration were restored to
`v1.4.1-axis-de-combined-0f19476`; the new release was retained and not
deleted. No enable, mode, gear, reset, stop, or motion command was sent. The
EtherCAT service remained active, but the PDO/OP condition is currently not
acceptable for commissioning and requires separate field investigation.

## 2026-08-27 — native PV velocity in the combined profile

The combined profile was updated so its HMI velocity mode uses the Uservo
native PV command path while position and electronic gear remain CSP. The
standalone `axis-de-uservo-pv` and `axis-de-uservo-gear` profiles were not
changed.

- implementation commits: `8236845`, `1f3e906` (pushed to `mktium/mctivity`);
- final source archive: `/tmp/mctivity-1f3e906.tar.gz`;
- source archive SHA-256: `a6a84d672ab629437a9e69d635e34651450805712f4909fc75e41bdf9926b3db`;
- release: `/opt/mctivity-releases/v1.4.1-axis-de-combined-1f3e906`;
- target-built motiond SHA-256: `58c486d50019deefc2412e36774278c72633b60b562722317565fbd2e0497592`;
- build: real `/opt/etherlab`, `gcc -O2 -Wall -Wextra -Werror` passed;
- pre-deploy backup: `/var/backups/mctivity/axis-de-combined-20260827T120000Z-1f3e906`;
- active link: `/opt/mctivity` resolves to the new release;
- HMI and motiond profiles: `axis-de-uservo-combined`, topology
  `axis-de-uservo-gear`, `MCTIVITY_COMMISSIONING_INHIBIT=1`;
- HMI was restarted after synchronizing its inhibit value to `1`; motiond was
  not restarted again for that configuration-only change.

The target accepted the combined PDO assignment on both Uservo slaves:

- RxPDO `0x1600`: `6040/6060/607A/60FF/60FE:01`;
- TxPDO `0x1A00`: `6041/6061/6064/606C/60FD`;
- Domain0 size `60`, working counter `6/6`, both slaves OP, D is the DC
  reference.

The read-only post-deploy HMI gate confirmed D/E mapping, velocity and gear
capabilities, `commanded_mode=0`, `cw=0`, disabled, no servo request, no
motion, no gear session, and target position equal to actual position. Both
axes nevertheless reported drive status word `0x0218` (`fault=true`) while
OP/WC remained healthy. No fault reset was attempted, so the 60-second
no-motion acceptance and any motion test are paused for separate operator
fault handling. No enable, mode, gear, stop, reset, or motion command was
sent.

## 2026-08-27 — operator-requested inhibit removal

The operator requested removal of the commissioning inhibit for the next
manual drive test. The current `/etc/mctivity/axis.env` and `hmi.env` were
backed up at
`/var/backups/mctivity/axis-de-combined-inhibit-off-20260827T121500Z` and both
were changed to `MCTIVITY_COMMISSIONING_INHIBIT=0`. motiond and HMI were
restarted; no enable, mode, gear, reset, stop, or motion command was sent.

After the restart, the read-only status showed both slaves OP with WC `6/6`,
but both drives reported `sw=0x0218` and `fault=true`; `cw=0`,
`commanded_mode=0`, `enabled=false`, `servo_request=false`, and `moving=false`.
The current “velocity cannot run” report is therefore a drive-fault gate, not
the commissioning inhibit. No automatic fault reset was performed.

## 2026-08-27 — restore restricted HMI motiond restart action

The operator requested that the previously implemented HMI service-recovery
action be made available again. The implementation was already present in the
active source release; this deployment restored its target configuration and
the matching least-privilege sudoers rule. It does not reset a drive fault and
does not enable, change mode, start gearing, stop, or move an axis.

- active release remained `/opt/mctivity-releases/v1.4.1-axis-de-combined-1f3e906`;
- `/etc/mctivity/hmi.env` now contains
  `MCTIVITY_SYSTEM_MOTIOND_RESTART_ENABLED=1`, the exact restart command, and
  a 10-second timeout;
- `/etc/sudoers.d/mctivity-motiond-restart` was restored for `iiru` and passed
  `/usr/sbin/visudo -cf`;
- pre-change backup: `/var/backups/mctivity/motiond-restart-enable-20260827T072643Z`;
- only `mctivity-hmi.service` was restarted; `mctivity-motiond.service` was
  not restarted by deployment; all three services (`motiond`, `hmi`, and
  `ethercat`) were active afterward.

The read-only capability check reports
`motiond_restart_control.available=true`. The HMI `dry_run` check passed with
both D/E disabled, stationary, `servo_request=false`, `gear_running=false`,
and controlword `0`; the permission check also passed. No actual restart or
drive control command was issued.

## 2026-08-27 — explicit gear safety-latch clear control

The HMI was upgraded with a separate `清除齿轮安全锁存` action after the first
field gear attempt left the D/E group latched on follower position error. The
button is separate from the large motion `启停` control, refreshes both axis
statuses, targets the actual configured gear slave, and refuses to send
`gear_stop` unless D/E are both disabled, stationary, and have controlword `0`.
The existing backend `gear_stop` safety behavior remains unchanged.

- source commit: `eec32b9` (pushed to `mktium/mctivity`);
- source archive: `/tmp/mctivity-eec32b9.tar.gz`;
- source archive SHA-256: `d3297fc0cad9fa18fee24a04bb4351c44b436cf14e3bdfcf5480baec0d598ad5`;
- release: `/opt/mctivity-releases/v1.4.1-axis-de-combined-eec32b9`;
- target build: real `/opt/etherlab`, `gcc -O2 -Wall -Wextra -Werror` passed;
- target motiond SHA-256: `58c486d50019deefc2412e36774278c72633b60b562722317565fbd2e0497592`;
- pre-deploy backup: `/var/backups/mctivity/pre-axis-de-gear-latch-clear-eec32b9-20260827T082010Z`;
- only `mctivity-hmi.service` was restarted; motiond was not restarted;
- all three services remained active and the new HMI asset contained
  `clearGearSafetyLatch`.

The post-deploy read-only state remained D/E OP with WC `6/6`, disabled,
stationary, and controlword `0`. The pre-existing
`gear_group_safety_latched=true` state was intentionally not cleared by
deployment; the new button was not invoked. No drive control or motion command
was issued.

## 2026-08-27 — allow D master position control during active gear

The operator reported `combined_mode_change_requires_stop` when starting
normal position control from the D page after selecting electronic gear on E.
The root cause was a backend guard that rejected every non-`gear_cam` mode
change during an active gear session, including the master's required
`set_mode position`. The guard now locks only the follower; the stationary
master may select its normal position mode. Motion-in-progress, jog, and
controlled-stop checks remain enforced.

- source commit: `c77b7d2` (pushed to `mktium/mctivity`);
- source archive SHA-256:
  `6a58aa8e998e255db22594fdf6d982000e688f9db19b4921eff069fde6f2596b`;
- target release: `/opt/mctivity-releases/v1.4.1-axis-de-master-c77b7d2`;
- target-built motiond SHA-256:
  `59ce489a0ea36a09b42058d47635c08914e5c53ab9fc701474d3fe161a19b02d`;
- pre-deploy backup:
  `/var/backups/mctivity/pre-v1.4.1-axis-de-master-c77b7d2-20260827T092913Z`;
- target build used real `/opt/etherlab` and
  `gcc -O2 -Wall -Wextra -Werror`; target pure C tests passed;
- active link resolves to the new release and motiond/HMI are active.

The post-restart read-only gate still showed D/E OP with WC `6/6`, zero
deadline misses, zero communication-timing latch, disabled, stationary, and
`cw=0`. However, D reported drive status `0x1218` and E `0x0218`
(`fault=true`). This is the known restart-transition drive-fault risk; no
fault reset was attempted, and the 60-second no-motion acceptance and all
motion validation remain paused for separate operator fault handling.

## 2026-08-27 — elapsed-cycle gear speed guard fix deployment and rollback

Commit `8edac79` changed the follower target-step check to scale its allowed
step by the number of elapsed control cycles. Local release preflight, C unit
tests, and the target build against real `/opt/etherlab` with
`-O2 -Wall -Wextra -Werror` passed. The target-only pure C tests also passed.

- source archive: `/tmp/v1.4.1-axis-de-gear-step-8edac79.tar.gz`;
- source archive SHA-256:
  `7fbb1b361a458ff09d103bb78d14ef5d86f11b405e1e88fa0093c62def6ef260`;
- attempted release: `/opt/mctivity-releases/v1.4.1-axis-de-gear-step-8edac79`;
- target motiond SHA-256:
  `c0c012e6975dbfe52d6ad5f331e03648c3ee86be55ab85a90630f90b62270de3`;
- pre-deploy backup:
  `/var/backups/mctivity/pre-v1.4.1-axis-de-gear-step-8edac79-20260827T095312Z`.

The attempted motiond restart did not pass the no-motion EtherCAT gate: E
entered SAFEOP, the domain temporarily reported WC `3/6`, and the kernel
reported AL `0x001A` synchronization error. The new release was not deleted;
the active link was rolled back to
`/opt/mctivity-releases/v1.4.1-axis-de-master-c77b7d2`. After rollback both
services and EtherCAT were active and WC returned to `6/6`, but D/E retained
drive fault status `0x0218`; no fault reset was attempted. No enable, mode,
gear, stop, reset, or motion command was sent. The new gear-speed fix remains
unaccepted in the field until the restart/ EtherCAT transition fault is
handled separately.
