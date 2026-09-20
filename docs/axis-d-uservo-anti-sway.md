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

The live target PDO inspection also confirms that the current single-axis
contract is exactly RxPDO `0x1600` (`6040/6060/607A/60FE:01`) and TxPDO `0x1A00`
(`6041/6061/6064/60FD`). There is no cyclic `0x6077`, `0x6078`, or `0x35F6`
current/torque feedback in this mapping. The application therefore does not
infer a mechanical endpoint from current and does not use the drive's native
Homing mode. The PDO contract is left unchanged.

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

## Linear travel and HMI phase B/G

The current implementation uses manual endpoint teaching. The operator moves
the enabled axis with the existing jog control, stops it, and presses `记录左端点`
or `记录右端点`. The HMI reads status only, persists the stopped encoder
position with calibration data version 1, validates left/right ordering and
the configured safety margin, and exposes `未标定` / `左端已记录` /
`两端已标定` states. No endpoint button sends an enable, mode, stop, or motion
command. After both endpoints are valid, position and jog commands receive the
safe bounds; out-of-range position targets are rejected and a bounded jog is
held at the safe edge. `set_zero` and torque control are blocked until the
operator clears the endpoint calibration. The anti-sway switch remains disabled
until a later, separately validated trajectory implementation is ready.

The HMI panel is deliberately compact for the fixed touch display. Its short
viewport layout keeps the position rail, endpoint/target metrics, and calibration
badge visible without relying on vertical scrolling; explanatory text and the
currently unavailable anti-sway switch collapse at viewport heights at or below
820 px.

The earlier current-spike/contact state machine remains as isolated regression
coverage only; it is not wired to the live Uservo command path and is not used
for this machine's endpoint teaching.

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
