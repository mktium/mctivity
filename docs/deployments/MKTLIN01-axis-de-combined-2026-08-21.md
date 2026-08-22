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
- deployable source/documentation commit: `34c6fff`;
- source archive: `/tmp/mctivity-34c6fff.tar.gz`;
- source archive SHA-256: `b12f7bafbf8623c4bac4d9a3d110f03dd36245b7cbde5cf4fcf83df9be83670f`;
- CYMG documentation commit: `a26fe06` (local-only);
- release preflight: passed;
- profile/PDO contract validation: passed;
- HMI, launcher, Python, JavaScript, shell, JSON and C unit checks: passed;
- local real-EtherLab motiond build: not available because this workstation has no `/opt/etherlab`.

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
