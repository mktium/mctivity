# MKTLIN01 single-axis UF Uservo anti-sway baseline — 2026-09-17

## Scope

This deployment starts the single-axis anti-sway branch for the replacement
UF-48V03AEDR-P drive with the original motor. It uses the existing validated
single-axis `axis-d-uservo` CSP profile as the EtherCAT baseline. It does not
enable or tune an anti-sway algorithm; the required pendulum/load parameters
are not yet specified.

The vendor Uservo-Flex documentation identifies UF-48V03AEDR-P as an
`UF-48VxxAEDx` EtherCAT drive. The vendor XML page lists
`XActant-E-XML-6120R.xml` for that family, firmware V6.1.20 and backward
compatible versions. Its identity matches the existing profile:

- vendor `0x00666999`;
- product `0x00004806`;
- revision `0x00000001`;
- one Uservo slave at physical position 0;
- CSP RxPDO `0x1600` / TxPDO `0x1A00`.

## Build and deployment

- branch: `feature/v1.4.1-axis-d-uservo-anti-sway`;
- source commit: `c3b0729` (pushed to `mktium/mctivity`);
- source archive: `/tmp/mctivity-c3b0729.tar.gz`;
- source archive SHA-256:
  `958d86ffd0e619a2bf4badd006519b3f74d358324cbce411e4942f08096c1c38`;
- release: `/opt/mctivity-releases/v1.4.1-axis-d-uservo-anti-sway-c3b0729`;
- pre-deploy backup:
  `/var/backups/mctivity/pre-axis-d-uservo-anti-sway-c3b0729-20260917T072320Z`;
- profile-switch backup:
  `/var/backups/mctivity/profile-20260917T072321Z-20039`;
- target build used real `/opt/etherlab` and `gcc -O2 -Wall -Wextra -Werror`;
- target motiond SHA-256:
  `fa9a17db7b92c8cb29e3b6a06f9abb8a49880ae637595ef840dbcb0336c31db9`.

The target configuration is now `axis-d-uservo` with
`MCTIVITY_COMMISSIONING_INHIBIT=1`. The previous combined release was not
deleted and remains available through the recorded backup link.

## Read-only acceptance

After the restart transition, ten one-second status samples remained healthy:

- one Uservo slave detected at position 0, state OP;
- Domain0 size `22`, working counter `3/3`;
- Axis D `al_state=8`, `operational=true`, `wc_complete=true`;
- `enabled=false`, `servo_request=false`, `moving=false`, `fault=false`;
- controlword `0`, commanded mode `0`, target equal to actual position;
- realtime deadline misses and skipped periods `0`;
- communication timing fault `false`;
- motiond, HMI, and kiosk services active.

No reset, mode change, enable, stop, gear command, or motion command was sent.
The first motion test and anti-sway tuning remain separate operator-approved
steps.

## HMI inhibit synchronization follow-up

The first single-axis profile switch exposed a configuration inconsistency:
motiond correctly read `MCTIVITY_COMMISSIONING_INHIBIT=1` from `axis.env`, but
an old `MCTIVITY_COMMISSIONING_INHIBIT=0` remained in `hmi.env`. The profile
switch script now updates both files from the selected Uservo profile, so HMI
capabilities cannot advertise an enabled control path while motiond is
inhibited.

- fix commit: `6c372dc` (pushed to `mktium/mctivity`);
- source archive: `/tmp/mctivity-6c372dc.tar.gz`;
- source archive SHA-256:
  `f5ce21b3e26fb7b6b865891054569311357e8f23bc619089447c75d1baaf533a`;
- active release: `/opt/mctivity-releases/v1.4.1-axis-d-uservo-anti-sway-6c372dc`;
- pre-deploy backup:
  `/var/backups/mctivity/pre-axis-d-uservo-anti-sway-6c372dc-20260917T072726Z`;
- profile-switch backup:
  `/var/backups/mctivity/profile-20260917T072726Z-21545`;
- target motiond SHA-256 remains:
  `fa9a17db7b92c8cb29e3b6a06f9abb8a49880ae637595ef840dbcb0336c31db9`.

Post-fix read-only status reports both axis.env and hmi.env inhibit values as
`1`, HMI capability `commissioning_inhibit=true`, D OP/WC `3/3`, disabled,
stationary, and controlword `0`. No control or motion command was sent.
