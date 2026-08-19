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
- commit: `ab61cba` (`Add vendor-supported Axis D PV profile`)
- target release: `/opt/mctivity-releases/v1.4.1-axis-d-pv-ab61cba`
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

Because the drive fault bit is currently set, this is a communication/profile
deployment only, not a motion-readiness approval. Do not issue a fault reset or
remove inhibit without a separate operator decision.

## Next gate

After the existing fault is diagnosed/cleared under the approved no-motion
procedure, run `scripts/mctivity-axis-d-pv-verify.sh` only after explicitly
switching to the PV profile while retaining inhibit. A real PV velocity test
(including 222 rpm) remains blocked until the user confirms the test setup,
mechanical clearance, E-stop/STO, direction, and first-speed limit.
