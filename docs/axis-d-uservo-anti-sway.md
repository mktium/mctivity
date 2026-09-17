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
current/torque feedback in this mapping. The new C decision layer therefore
fails closed with `MCTIVITY_TRAVEL_FAIL_MISSING_CURRENT_FEEDBACK` instead of
guessing at an SDO value or treating a position sample as current feedback.
The next runtime integration must use a verified vendor-native homing/stall
current path or an explicitly validated PDO remap; it must not silently change
the current PDO contract.

The profile now records the vendor-native homing candidate without enabling it:
mode `6`, homing method `0x6098`, stall-current setting `0x3637`, timeout
`0x3643`, controlword start bit 4, and statusword attained/error bits 12/13.
These are implementation metadata only; `validated=false` remains until the
manual semantics and a no-motion startup check are reviewed together.

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

The local phase B/G implementation adds a read-only linear-travel model and HMI
panel for the single-axis D profile. It displays current/target counts, endpoint
validity, safe travel range, calibration state, commissioning inhibit, and
anti-sway readiness. The endpoint decision layer is implemented as the pure
`endpoint_contact_decision_v1` state machine in
`mctivity_hmi/travel_calibration.py`: it requires a healthy enabled axis, a
current baseline, a sustained current rise, and a position-progress stall, with
timeout/fault/communication fail-closed paths. It has no EtherCAT or subprocess
side effects. Endpoint calibration actions are intentionally not wired to
motiond yet; the HMI reports `runtime_not_connected` and keeps the anti-sway
toggle disabled. Targets are rejected by the pure model until both endpoints
and the safety margin are valid. The read-only model uses the confirmed
baseline of `10000 counts/rev`, while endpoint values and sway period remain
unconfigured.

The HMI panel is deliberately compact for the fixed touch display. Its short
viewport layout keeps the position rail, endpoint/target metrics, and calibration
badge visible without relying on vertical scrolling; explanatory text and the
currently unavailable anti-sway switch collapse at viewport heights at or below
820 px.

The real-time-side pure state machine is in
`mctivity_pdo_monitor/travel_calibration.h` and is covered by
`test_travel_calibration.c`. It checks inhibit, OP/WC, fault, enabled state,
feedback availability, timeout, sustained current rise, and stalled position
progress. It is not yet wired to the live Uservo command path.

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
