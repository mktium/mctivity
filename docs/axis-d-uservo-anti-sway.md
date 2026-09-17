# Single-axis Uservo anti-sway baseline

## Scope

Branch `feature/v1.4.1-axis-d-uservo-anti-sway` starts from the accepted
`axis-de-uservo-combined` release but uses the existing single-axis Uservo
profile for the current machine state: one UF-48V03AEDR-P EtherCAT drive and
the original motor. The runtime identity is Axis D at physical EtherCAT
position 0 (`mctivity`).

This stage establishes a safe, single-axis EtherCAT baseline. It does not claim
that an anti-sway controller has been tuned or enabled. The existing CSP
position path remains the baseline for later anti-sway command generation; the
native PV profile remains available when the application needs velocity mode.

## Vendor compatibility basis

The vendor's Uservo-Flex documentation identifies UF-48V03AEDR-P as an
`UF-48VxxAEDx` EtherCAT drive. The vendor XML page states that
`XActant-E-XML-6120R.xml` applies to `UF-48VxxAEDx` and corresponds to firmware
V6.1.20 (backward compatible). The XML identity is:

- vendor ID `0x00666999`;
- product code `0x00004806`;
- revision `0x00000001`;
- 1 ms EtherCAT cycle;
- CSP RxPDO `0x1600` and TxPDO `0x1A00` for the single-axis position path.

The identity and PDO contract match `profiles/axis-d-uservo.json`. This does
not replace checking the replacement drive's motor, encoder, current, limits,
and drive-side parameters.

## Anti-sway boundary

Anti-sway is an application-level trajectory/command-shaping function, not an
EtherCAT identity change. It must not be invented from the drive model alone.
Before implementing it, record the payload mechanics and tuning values:

- pendulum/load length or measured oscillation period;
- direction and sign convention;
- allowable travel and speed/acceleration/deceleration;
- whether the controller receives position, velocity, or operator jog commands;
- damping or residual-sway acceptance criterion.

Until those values are confirmed, the branch exposes the proven single-axis
Uservo transport and keeps anti-sway output disabled. No enable, mode change,
reset, or motion command is part of the baseline deployment.

## No-motion acceptance

The target must use `axis-d-uservo` with:

- `MCTIVITY_COMMISSIONING_INHIBIT=1`;
- exactly one Uservo slave at physical position 0;
- slave OP and Domain0 WC `3/3`;
- `enabled=false`, `servo_request=false`, `moving=false`, and `cw=0`;
- mode request and position target held at zero/actual position;
- no drive fault and no communication timing fault.

The single-axis verification scripts are read-only and send no reset, mode,
enable, stop, or motion command. First motion and anti-sway tuning require a
separate operator-approved plan.
