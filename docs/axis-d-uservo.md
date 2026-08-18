# Uservo DS1 Axis D

## Scope

This profile supports the CYMG20241203 DS1-E4806N drive as a single EtherCAT axis displayed as Axis D. It intentionally does not request the legacy MCTIVITY or FV3 slaves.

Live identity and scale captured on 2026-08-18:

- device name: `Uservo`
- vendor ID: `0x00666999`
- product code: `0x00004806`
- revision: `0x00000001`
- physical position: `0`
- encoder resolution: `10000` increments per motor revolution
- interpolation period: `1 ms`

Default RxPDO `0x1600`:

- `0x6040:00` controlword
- `0x6060:00` modes of operation
- `0x607a:00` target position
- `0x60fe:01` digital outputs

Default TxPDO `0x1a00`:

- `0x6041:00` statusword
- `0x6061:00` modes of operation display
- `0x6064:00` position actual value
- `0x60fd:00` digital inputs

## Configuration

Install `config/axis-d-uservo.env` as `/etc/mctivity/axis.env`. Both the HMI and motion-daemon systemd units load this file.

The first deployment must retain:

```text
MCTIVITY_COMMISSIONING_INHIBIT=1
```

With inhibit active:

- cyclic controlword is forced to `0`
- cyclic mode request is forced to `0`
- target position is initialized from actual position
- status, disable, stop, and the non-energizing CiA 402 fault-reset pulse remain available
- enable, mode changes, and all motion commands are rejected by `motiond`

The commissioning fault reset can write only controlword `0x0080`; it never enters the CiA 402 enable sequence. This is needed to clear a communication fault latched when the EtherCAT application is deliberately restarted. After a reset, verify the controlword returns to zero and the axis remains disabled before continuing.

The first profile intentionally omits velocity mode because the drive's default RxPDO has target position (`0x607a`) but not target velocity (`0x60ff`). Position-based modes use conservative UI defaults: 0.01 revolution relative move, a +/-1 revolution position range, 30 rpm default speed, and 222 rpm maximum speed. These values are preparation for a later onsite motion gate; inhibit remains authoritative until that separate gate is approved.

The default TxPDO does not contain `0x603f` (error code). The statusword fault bit is available, but the HMI error-code field remains zero until an error-code PDO or SDO diagnostic path is added.

## First Deployment Gate

1. Back up `/opt/mctivity` and the installed service/config files.
2. Build the new binary against `/opt/etherlab` before replacing the active binary.
3. Install the exact pushed Git commit and keep commissioning inhibit enabled.
4. Restart `mctivity-motiond.service`, then `mctivity-hmi.service` and `mctivity-kiosk.service` if needed.
5. Confirm the EtherCAT slave reaches OP and the domain working counter is complete.
6. Confirm status reports `enabled=false`, `servo_request=false`, `cw=0`, and changing position feedback is plausible when the shaft is moved manually.
7. Confirm a non-energizing `set_mode` request returns `commissioning_inhibit`, then read status again and confirm the axis remains disabled with controlword zero.

Run `scripts/mctivity-axis-d-verify.sh` for this gate. The script pins the expected profile to `axis-d-uservo`, checks the backend's own topology/scale/inhibit fields, and uses a non-energizing mode-selection request to prove command rejection. It deliberately does not send an enable command.

On MKTLIN01, `enp3s0` is dedicated to EtherCAT. Install `config/90-mctivity-ethercat-enp3s0.conf` as `/etc/sysctl.d/90-mctivity-ethercat-enp3s0.conf` so the normal IPv6 stack does not configure or transmit on the fieldbus interface. Do not apply this host-specific file to an interface that is also used for normal networking.

This gate does not include motor enable or motion. Removing commissioning inhibit requires a separate onsite decision after the no-motion gate passes.

## Rollback

Stop the kiosk, HMI, and motion daemon; restore the timestamped `/opt/mctivity` backup and prior `/etc/mctivity/axis.env`/systemd configuration; reload systemd; then start the prior motion daemon and HMI. Verify the expected legacy topology before any enable request.
