# Axis D/E Uservo CSP electronic gear

`axis-de-uservo-gear` is the position-synchronous D/E Uservo profile. It is
independent from the accepted `axis-de-uservo-pv` velocity group: the PV
profile and its commands remain unchanged.

## Fixed topology and PDO contract

| EtherCAT position | Logical axis | Transport key | Role at default configuration |
| ---: | --- | --- | --- |
| 0 | D | `mctivity` | master |
| 1 | E | `mctivity_e` | slave |

Both axes use the Uservo CSP contract at a 1 ms combined-domain cycle. D is
the DC reference.

- RxPDO `0x1600`: `6040:00/16`, `6060:00/8`, `607A:00/32`, `60FE:01/32`
- TxPDO `0x1A00`: `6041:00/16`, `6061:00/8`, `6064:00/32`, `60FD:00/32`
- mode of operation: CSP (`0x6060=8`)
- encoder scale: 10000 counts/revolution
- configured speed ceiling: 222 rpm

The position mapping is deliberately positional because the two installed
drives have identical identity fields. Swapping the physical drives is not
detectable by this profile.

## Gear session contract

The HMI and low-level API reuse `gear_config`, `gear_start`, and `gear_stop`.
For this profile, `gear_config` accepts only the real D/E peer, rejects
self-reference and virtual masters, and accepts a master/slave ratio from 1
to 200 plus same/reverse direction. The default is D master, E slave, same
direction, 1:1.

`gear_start` is a two-axis gate. It requires both drives to be OP with a
complete working counter, fault-free, enabled, settled, communication timing
healthy, and phase-search confirmation present. The slave then enters the
gear session; ordinary slave position commands are rejected while the
session is active. The master remains available for normal position commands.

The slave target is calculated from the captured engagement positions:

```
slave_target = slave_origin + direction *
               (master_cumulative_displacement * slave_ratio / master_ratio)
```

The helper in `mctivity_pdo_monitor/electronic_gear.h` uses signed 64-bit
intermediates, an int32 wrap-aware master delta, integer remainder retention,
and an explicit int32 target-range check. This prevents ratio truncation and
long-run drift while preserving the instantaneous position offset at engage.

`gear_stop` first clears the master motion target and holds both axes at their
actual positions before disengaging the session. A safety latch is cleared
only after both axes are disabled, stationary, healthy, and communicating.

## Fail-closed behavior

The group following-error limit is 200 counts (about 7.2 degrees at 10000
counts/revolution). Target overflow, follower speed above the configured
ceiling, following error above the limit, a fault, loss of OP/WC, timing loss,
or loss of either enable request clears both motion requests and latches the
gear safety stop. The output path then forces controlword and mode to zero and
pins each target to its actual position.

`MCTIVITY_COMMISSIONING_INHIBIT=1` is mandatory for the current phase. Under
inhibit, HMI and motiond reject mode changes, enables, gear starts, and motion
commands. Status and the narrow pre-existing fault-reset path remain available
but are not used by deployment. No motion acceptance is included in this
release.

## Read-only deployment gate

Before any future motion plan, verify for at least 60 seconds:

- positions 0/1 are D/E, both slaves are OP, and the combined WC is complete;
- commissioning inhibit is true;
- both axes are disabled with `servo_request=false`, `moving=false`, and
  `gear_running=false`;
- controlword and mode outputs are zero and each target equals actual position;
- gear session, safety latch, and phase-search confirmation are clear;
- realtime deadline misses and skipped periods remain zero.

Any driver fault is recorded and left in place; it is never automatically
reset. Any service, PDO, OP/WC, or zero-output failure stops the gate and uses
the timestamped backup to restore the previous release and configuration.
