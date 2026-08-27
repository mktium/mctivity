# Axis D/E Uservo combined velocity and electronic gear

`axis-de-uservo-combined` is the unified D/E HMI profile. It exposes native PV
velocity control and CSP position/electronic gear in the same mode selector.
The old standalone PV and CSP profiles remain unchanged.

## Runtime contract

- physical position 0 is logical D (`mctivity`);
- physical position 1 is logical E (`mctivity_e`);
- D is the default gear master and E is the default gear slave;
- RxPDO `0x1600`: `6040`, `6060`, `607A`, `60FF`, `60FE:01`;
- TxPDO `0x1A00`: `6041`, `6061`, `6064`, `606C`, `60FD`;
- cycle period is 1 ms and D is the DC reference;
- the default velocity remains 222 rpm, while the configured ceiling is 999 rpm
  / 166500 counts/s (matching the native PV profile).

In velocity mode the drive is set to CiA 402 PV (`0x6060=3`) and the requested
counts/s are written directly to the native `0x60FF` target-velocity PDO; live
velocity is read from `0x606C`. In position and gear modes the drive remains in
CSP (`0x6060=8`) and receives `0x607A` target position. The combined map keeps
each control/status object unique in the process image, so changing the HMI
mode does not require a live PDO remap.

The combined profile uses the existing Uservo PDO assignment objects
`0x1600`/`0x1A00`, extended at startup in Pre-Op with the additional native PV
fields. Because the exact target ESI is not installed on the controller,
deployment must verify that both slaves accept the map and that the domain WC
is complete before any motion test. A failed map/domain gate is a deployment
failure and must roll back the active release while retaining the failed
release for diagnosis.

## Mode selection and safety

Velocity and electronic gear are separate, mutually exclusive modes. A mode
change is rejected while the axis is moving, while a velocity jog is active,
or while a controlled stop is still active. During an active gear session, the
follower remains locked in `gear_cam`, while the master may select its normal
position mode and receive ordinary position commands. The gear slave remains
locked against ordinary axis commands until `gear_stop` completes. Gear
configuration keeps the real-peer-only D/E rule, 1–200 ratios, direction
selection, and the 200-count following-error threshold. In the combined
profile, following error is warning-only and is shown live in the HMI; it does
not stop the group. The standalone `axis-de-uservo-gear` profile retains its
original immediate 200-count trip. The follower target-step guard scales its
allowed counts by the number of elapsed 1 ms control cycles, so a short
scheduling/sample gap is not mistaken for an over-speed event;
communication/WC faults, target overflow, and target-speed violations still
fail closed independently.

The HMI profile exposes `axis.mode.velocity.execute` and
`axis.mode.gear_cam.execute` together. `sync_velocity_control` remains absent
because this profile controls each axis through the mixed PV/CSP map; the
electronic-gear group remains the only coupled D/E mode.

## Deployment boundary

This change is software-only in the current stage. The Uservo drive power is
disconnected, so no EtherCAT electrical/PDO/OP validation or motion test is
claimed. Deployment may restart services and verify profile/API assembly, but
must not send enable, mode, gear-start, stop, fault-reset, or motion commands.
The old CSP gear and native PV profiles remain rollback targets.

The realtime status separates host scheduler jitter from EtherCAT communication
health. A single or two-cycle host wake-up slip is counted but does not latch a
communication fault; three consecutive skipped 1 ms periods during active
Uservo control latch `rt_schedule_timing_fault` and fail closed. OP/WC/link
losses retain immediate fail-closed behavior. The status API exposes the
`rt_consecutive_schedule_misses` streak and `rt_schedule_timing_fault` flag.

## Restricted HMI motiond restart

The combined HMI can expose a `重启 motiond` action when
`MCTIVITY_SYSTEM_MOTIOND_RESTART_ENABLED=1`. The server accepts it only when
every assembled axis is disabled and stationary, has `servo_request=false`,
has no gear or sync session, and reports controlword `0`. A drive fault is not
reset or cleared by this action. The server first checks the exact configured
command with `sudo -n -l` and then runs only
`/usr/bin/systemctl restart mctivity-motiond.service` through a matching
`/etc/sudoers.d/mctivity-motiond-restart` rule. Any failed status, permission,
or safety check blocks the request.

The installer writes the restart variables and validates the sudoers file with
`visudo`. The button is a narrowly scoped service-recovery control; it does
not enable a drive, change mode, reset a fault, start gearing, or issue a
motion command.

The gear panel also provides an explicit `清除齿轮安全锁存` action. It refreshes
both D/E statuses, resolves the actual configured gear slave, and sends
`gear_stop` only when every assembled axis is disabled, stationary, and has
controlword `0`. It is separate from the large motion `启停` control, so a
latched gear fault cannot be accidentally retried as a gear start.
