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
- source archive: `/tmp/mctivity-c6575a5.tar.gz`;
- source archive SHA-256: `2b162674be84b4d638a55d86f381353c5c04ac18d0b607ce5bd8b7dfe041b41c`;
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

## Target status

Read-only `GET /api/capabilities` at `192.168.1.201:2015` still reports the
previous `axis-de-uservo-gear` release and no velocity feature. The target was
not changed in this stage.

Direct SSH attempts to `192.168.1.201` using the available local identities
were rejected with `Permission denied (publickey,password)`. Therefore the
following steps remain pending and are intentionally not claimed:

1. copy the source archive to MKTLIN01;
2. compile `mctivity_motiond` against the target's real `/opt/etherlab` using
   `-O2 -Wall -Wextra -Werror`;
3. record the target binary SHA-256;
4. back up `/etc/mctivity`, systemd units and the active release link;
5. install and select `axis-de-uservo-combined`;
6. restart only the required services with no control commands;
7. perform the software/profile read-only gate while the drive power remains disconnected.

No HMI or motion API control request was sent during this stage. Once target
SSH authorization is restored, deployment must preserve the operator's current
commissioning-inhibit setting and must still wait for explicit authorization
before any enable or motion operation.
