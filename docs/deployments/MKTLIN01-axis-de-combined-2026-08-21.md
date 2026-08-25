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
- deployable source/documentation commit: `0f19476`;
- source archive: `/tmp/mctivity-0f19476.tar.gz`;
- source archive SHA-256: `f787e864ca6c136882167c66709095aeca1a597ead35378cdc9e45b73116d038`;
- CYMG documentation commit: `a26fe06` (local-only);
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
