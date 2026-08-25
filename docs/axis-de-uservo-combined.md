# Axis D/E Uservo combined velocity and electronic gear

`axis-de-uservo-combined` is the unified D/E HMI profile. It exposes both
velocity control and electronic gear in the same mode selector while keeping
the previously verified CSP PDO contract from `axis-de-uservo-gear`.

## Runtime contract

- physical position 0 is logical D (`mctivity`);
- physical position 1 is logical E (`mctivity_e`);
- D is the default gear master and E is the default gear slave;
- RxPDO `0x1600`: `6040`, `6060`, `607A`, `60FE:01`;
- TxPDO `0x1A00`: `6041`, `6061`, `6064`, `60FD`;
- cycle period is 1 ms and D is the DC reference;
- the configured velocity ceiling remains 222 rpm / 37000 counts/s.

The existing `axis-de-uservo-pv` profile is unchanged and remains available as
the native PV velocity fallback. The combined profile does not assume a new
or unverified drive PDO mapping: its velocity mode converts the requested
counts/s into bounded CSP target-position increments. The drive therefore
stays in CSP mode (`0x6060=8`) in both position/gear and velocity modes.

## Mode selection and safety

Velocity and electronic gear are separate, mutually exclusive modes. A mode
change is rejected while the axis is moving, while a velocity jog is active,
or while a gear session is active. The gear slave remains locked against
ordinary axis commands until `gear_stop` completes. Gear configuration keeps
the real-peer-only D/E rule, 1–200 ratios, direction selection, and the
200-count following-error limit.

The HMI profile exposes `axis.mode.velocity.execute` and
`axis.mode.gear_cam.execute` together. `sync_velocity_control` remains absent
because the old atomic dual-axis PV group is intentionally not mixed into the
CSP combined profile.

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
