# Single-axis Uservo anti-sway baseline

## Scope

Branch `feature/v1.4.1-axis-d-uservo-anti-sway` starts from the accepted
`axis-de-uservo-combined` release but uses the existing single-axis Uservo
profile for the current machine state: one UF-48V03AEDR-P EtherCAT drive and
the original motor. The runtime identity is Axis D at physical EtherCAT
position 0 (`mctivity`). The active single-axis profile is
`axis-d-uservo-combined`: CSP remains the position path and native PV is added
for direct speed control.

This stage establishes a safe, single-axis EtherCAT baseline. It does not claim
that an anti-sway controller has been tuned or enabled. The existing CSP
position path remains the baseline for later anti-sway command generation; the
native PV target is available from the same HMI profile when the application
needs velocity mode.

## Vendor compatibility basis

The vendor's Uservo-Flex documentation identifies UF-48V03AEDR-P as an
`UF-48VxxAEDx` EtherCAT drive. The vendor XML page states that
`XActant-E-XML-6120R.xml` applies to `UF-48VxxAEDx` and corresponds to firmware
V6.1.20 (backward compatible). The XML identity is:

- vendor ID `0x00666999`;
- product code `0x00004806`;
- revision `0x00000001`;
- 1 ms EtherCAT cycle;
- mixed RxPDO `0x1600` and TxPDO `0x1A00` for the single-axis position/speed path.

The identity and mixed PDO contract match `profiles/axis-d-uservo-combined.json`. This does
not replace checking the replacement drive's motor, encoder, current, limits,
and drive-side parameters.

The mixed runtime contract is RxPDO `0x1600` (`6040/6060/607A/60FF/60FE:01`)
and TxPDO `0x1A00` (`6041/6061/6064/606C/60FD`). There is no cyclic `0x6077`,
`0x6078`, or `0x35F6` current/torque feedback in this mapping. The application therefore does not
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
position with calibration data version 1, applies the profile's mechanical
position direction before validating left/right ordering and the configured
safety margin, and exposes `未标定` / `左端已记录` /
`两端已标定` states. No endpoint button sends an enable, mode, stop, or motion
command. After both endpoints are valid, position and jog commands receive the
safe bounds; out-of-range position targets are rejected and a bounded jog is
held at the safe edge. `set_zero` and torque control are blocked until the
operator clears the endpoint calibration. The anti-sway switch remains disabled
until a later, separately validated trajectory implementation is ready.

For the current D Uservo assembly, `position_direction=-1`: physical left may
therefore have a larger raw encoder count than physical right. The stored
`left_limit_counts` and `right_limit_counts` remain drive-coordinate values so
the motion guard can pass correct raw bounds to motiond, while validation,
percentage display, and safe-margin calculation use the physical direction.
`清除标定` is a non-motion operation and remains clickable even if the HMI's
fast status cache is stale; the backend accepts it only when the axis is
stationary, disabled, and not requesting servo output.

The D axis drives the linear belt through a 40 mm/rev transmission. The HMI
therefore presents the absolute target, taught endpoints, current position, and
safe range as signed millimetres; encoder counts remain a secondary diagnostic
value. `rev` is retained only as a transmission concept (one motor revolution),
not as the operator's target-position unit. The position screen uses compact
horizontal speed and acceleration sliders so the travel card remains usable on
the fixed 976x731 touch display.

The persisted `travel` block is runtime-owned. Ordinary HMI profile saves
(speed, mode, slider, and transmission settings) are merged into the existing
device state and cannot remove recorded endpoints. This prevents a successful
endpoint record from appearing to work in the page and then disappearing after
the next periodic profile save.

The HMI panel is deliberately compact for the fixed touch display. Its short
viewport layout keeps the position rail, endpoint/target metrics, calibration
badge, sway-period field, and anti-sway switch visible without relying on
vertical scrolling; only the explanatory reason text collapses at viewport
heights at or below 820 px.

After endpoint calibration, the absolute-position target slider is narrowed to
the persisted software-safe range, with the installed `position_direction`
applied before the range is shown. Dragging the slider only changes and saves
the target value; the explicit `移动到目标` action is still required before a
position command can be sent. The backend repeats the native-count safety check,
so the browser range is a usability aid and not the safety boundary by itself.

The anti-sway command path is now implemented for this CSP profile as a
real-time ZVD input shaper inside `motiond`. A shaped absolute-position command
first generates the existing bounded S-curve, then feeds its nominal CSP target
through three positive impulses at `0`, `T/2`, and `T`, where `T` is the measured
damped sway period. The shaper is initialized at the current actual position,
keeps the target inside the software travel limits, and runs a bounded tail for
one period after the nominal move. The default period is `1150 ms` and the
default damping parameter is 50 permille; the period must be between 10 ms and
10000 ms. Native PV velocity control is unchanged and does not use this shaper.

The fixed HMI exposes the period and an explicit anti-sway switch only after
both endpoints are valid. Saving the period does not enable the feature; the
switch must be turned on separately. With the switch on, absolute and relative
position actions are converted to `move_shaped_abs`, while direct velocity/jog
actions remain native PV. The backend rechecks the persisted configuration,
travel bounds, and numeric limits, so a stale or malformed browser request is
rejected. The feature remains off by default and has not been motion-tested in
this deployment; first motion and period tuning still require a separate
operator-approved plan.

The HMI also persists the motiond software-zero raw count alongside the taught
endpoints. On a service restart it may restore that coordinate only when the
axis is healthy, stopped, and disabled; `restore_zero_raw` changes no raw
position target and sends no drive enable or mode command. This is required so
the saved travel limits continue to describe the same coordinate system after
motiond is restarted.

The earlier current-spike/contact state machine remains as isolated regression
coverage only; it is not wired to the live Uservo command path and is not used
for this machine's endpoint teaching.

## No-motion acceptance

The target must use `axis-d-uservo-combined` with:

- `MCTIVITY_COMMISSIONING_INHIBIT=1`;
- exactly one Uservo slave at physical position 0;
- slave OP and Domain0 WC `3/3`;
- `enabled=false`, `servo_request=false`, `moving=false`, and `cw=0`;
- mode request and position target held at zero/actual position;
- no drive fault and no communication timing fault.

The single-axis verification scripts are read-only and send no reset, mode,
enable, stop, or motion command. First motion and anti-sway tuning require a
separate operator-approved plan.
