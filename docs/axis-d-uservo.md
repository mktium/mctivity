# Uservo DS1 Axis D

## Scope

This profile supports the CYMG20241203 DS1-E4806N drive as a single EtherCAT axis displayed as Axis D. It intentionally does not request the legacy MCTIVITY or FV3 slaves.

Identity, timing, and PDO data were checked against the official `XActant-E-XML-6120R.xml` ESI as well as the live slave on 2026-08-18:

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
MCTIVITY_REQUIRE_REALTIME=1
```

With inhibit active:

- cyclic controlword is forced to `0`
- cyclic mode request is forced to `0`
- target position is initialized from actual position
- status, disable, stop, and the non-energizing CiA 402 fault-reset pulse remain available
- enable, mode changes, and all motion commands are rejected by `motiond`

The implementation retains a narrowly scoped commissioning fault-reset path, but it is not part of ordinary deployment or no-motion acceptance. Do not use it merely to make a failed timing test pass. The corrected shutdown path reduces its contribution to a planned restart from 300 stale-time cycles to 20 fresh-time cycles. The complete stop/re-exec/master-activation gap must still be measured against the drive's default 100 ms PDO-loss timeout.

The first profile intentionally omits velocity mode because the drive's default RxPDO has target position (`0x607a`) but not target velocity (`0x60ff`). Position-based modes use conservative UI defaults: 0.01 revolution relative move, a +/-1 revolution position range, 30 rpm default speed, and 222 rpm maximum speed. These values are preparation for a later onsite motion gate; inhibit remains authoritative until that separate gate is approved.

The default TxPDO does not contain `0x603f` (error code). The statusword fault bit is available, but the HMI error-code field remains zero until an error-code PDO or SDO diagnostic path is added.

## Incremental-encoder phase-search gate

The motor uses an incremental ABZ encoder. XActant's MotorHost manual states
that every incremental encoder needs an electrical-angle search before the
first enable after each power-on. The configured strong-pull search can move
the shaft; enable must therefore never be treated as a non-moving operation.

Axis D now starts every `motiond` communication session with
`phase_search_confirmed=false`. A normal `enable` request is rejected with
`phase_search_confirmation_required` until an operator has separately
completed the current power cycle's phase-search procedure and sent the
non-energizing command:

```json
{"cmd":"confirm_phase_search_complete","device":"mctivity"}
```

The confirmation command is accepted only while the drive is disabled,
stationary, fault-free, OP/WC-complete, and protected by the armed realtime
timing guard. It does not write a controlword or enable the drive. The latch is
cleared on process start, EtherCAT OP/WC loss, communication-timing fault, or
drive fault. Disable/re-enable within the same healthy communication session
does not clear it.

This latch is an operator safety acknowledgement, not proof that the drive's
electrical-angle identification succeeded. Before acknowledging it, use
MotorHost or a future read-only SDO diagnostic path to verify the current
power cycle's identification result and review `0x3622` (automatic phase
search), `0x3657` (return after search), `0x3638` (search mode), and `0x3008`
(identification state). Motion permission remains a separate user decision.

## First Deployment Gate

1. Back up `/opt/mctivity` and the installed service/config files.
2. Build the new binary against `/opt/etherlab` before replacing the active binary.
3. Install the exact pushed Git commit and keep commissioning inhibit enabled.
4. Restart `mctivity-motiond.service`, then `mctivity-hmi.service` and `mctivity-kiosk.service` if needed.
5. Confirm the EtherCAT slave reaches OP and the domain working counter is complete.
6. Confirm status reports `enabled=false`, `servo_request=false`, `cw=0`, and changing position feedback is plausible when the shaft is moved manually.
7. Confirm a non-energizing `set_mode` request returns `commissioning_inhibit`, then read status again and confirm the axis remains disabled with controlword zero.
8. Confirm status reports `phase_search_confirmation_required=true` and `phase_search_confirmed=false`.

Run `scripts/mctivity-axis-d-verify.sh` for this gate. The script pins the expected profile to `axis-d-uservo`, checks the backend's own topology/scale/inhibit fields, and uses a non-energizing mode-selection request to prove command rejection. It deliberately does not send an enable command.

Then run `scripts/mctivity-axis-d-stability.py --duration 1800 --max-position-span 0` under normal kiosk load. The stability gate requires no position change and no increase in deadline misses, skipped periods, WC transitions, or incomplete-WC cycles. See [Axis D EtherCAT Realtime Contract](axis-d-ethercat-realtime.md) for the official-source baseline and complete no-motion gate.

On MKTLIN01, `enp3s0` is dedicated to EtherCAT. Install `config/90-mctivity-ethercat-enp3s0.conf` as `/etc/sysctl.d/90-mctivity-ethercat-enp3s0.conf` so the normal IPv6 stack does not configure or transmit on the fieldbus interface. Do not apply this host-specific file to an interface that is also used for normal networking.

This gate does not include motor enable or motion. Removing commissioning inhibit requires a separate onsite decision after the no-motion gate passes.

## Rollback

Stop the kiosk, HMI, and motion daemon; restore the timestamped `/opt/mctivity` backup and prior `/etc/mctivity/axis.env`/systemd configuration; reload systemd; then start the prior motion daemon and HMI. Verify the expected legacy topology before any enable request.
