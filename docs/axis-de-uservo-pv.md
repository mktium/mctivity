# Axis D/E dual Uservo native PV profile

`axis-de-uservo-pv` assembles two identical XActant Uservo / DS1-E4806N-4I
drives as native CiA 402 PV axes. The fixed EtherCAT wiring order is:

| Physical position | Transport key | Logical axis |
| ---: | --- | --- |
| 0 | `mctivity` | D |
| 1 | `mctivity_e` | E |

Both drives report vendor `0x00666999`, product `0x00004806`, revision `1`,
serial `1`, hardware `0.7`, firmware `5.11`, and station alias `1`. The reported
serial and alias are therefore not unique identities. Position 0 is the
upstream drive connected directly to the master NIC; position 1 is the
downstream end drive. Label both drives, both EtherCAT cable ends, and both
motor cable sets with P0/D and P1/E. Swapping identical drives cannot be
detected by the current profile.

The profile uses `axis_instances` to instantiate
`modules/axis/device/uservo/pv/module.json` twice. Instance records may only
select logical axis, transport key, and physical position. Motor, encoder, PDO,
cycle, and motion values remain in the single module template:

- 10000 counts/revolution (2500-line differential ABZ encoder with x4 count);
- native PV mode code 3;
- RxPDO `0x1601`: `6040`, `6060`, `60FF`, `60FE:01`;
- TxPDO `0x1A01`: `6041`, `6061`, `606C`, `60FD`;
- 1 ms cycle with D/position 0 as the sole DC reference;
- default 222 rpm (37000 counts/s), maximum 999 rpm (166500 counts/s);
- acceleration, deceleration, and stop deceleration 2222 rpm/s
  (370333 counts/s2).

The launcher resolves and validates two separate D/E environment parameter
groups. Motiond keeps independent PDO offsets, state, target velocity, command
routing, fault state, and stop state for each axis. The communication timing
guard is shared deliberately. While both axes are stopped and disabled, a
transient non-OP/incomplete-WC sample disarms the enable gate; the gate rearms
only after 1000 consecutive healthy 1 ms cycles and does not require a daemon
restart. If either axis has an active enable request, enabled feedback, motion,
or a synchronized group session, the same loss immediately clears both target
velocities and latches the fail-closed communication fault for both axes.

The dual topology additionally provides an atomic PV speed group:
`sync_enable`, `sync_disable`, `sync_jog_velocity`, and `sync_stop`. A group
command has no single-axis device key. Motiond validates both axes first, then
updates both runtime slots together so the next combined-domain PDO frame
carries both controlwords or both target velocities. The HMI exposes this as an
explicit D+E synchronization switch; while it is on, the normal enable, large
start/stop, direction buttons, stop button, and live RPM slider all use group
commands. One-axis faults, communication loss, or loss of enabled state after
the group is armed clears and disables both axes before PDO output and latches
the group safety interlock. `sync_disable` is required to clear that latch.
This is same-cycle PV speed start/stop, not encoder phase or position locking.

During commissioning, use `config/axis-de-uservo-pv.env` and keep
`MCTIVITY_COMMISSIONING_INHIBIT=1`. Inhibit forces both controlwords, mode
commands, and target velocities to zero. Deployment verification is read-only:
do not send `set_mode`, `enable`, `jog_velocity`, `stop`, `fault_reset`, SDO
downloads, alias writes, state requests, or rescan requests.

Commissioning inhibit permits only the CiA 402 fault-reset controlword pulse
`0x0080`; mode, target velocity, and every enabling controlword remain forced
to zero. Communication or synchronized-group safety latches also block the
reset pulse. The HMI refreshes the selected axis after a reset request and
reports whether the pulse failed, was accepted but left the fault active, or
cleared the fault. It also refreshes commissioning-inhibit state from live axis
status, displays command rejection reasons, and serves control pages and JSON
with `Cache-Control: no-store` so a workstation cannot retain a stale
commissioning gate after the server configuration changes.

MKTLIN01's reproducible HMI settings are in
`config/axis-de-uservo-pv-hmi.env`. The server binds `0.0.0.0:2015` so the
local kiosk and `http://192.168.1.201:2015` both work, while the HTTP Host
allowlist admits only the default loopback names plus `192.168.1.201`. This is
not client authentication: without `MCTIVITY_API_TOKEN`, every device on the
trusted LAN that can reach the page can also submit control requests. Add a
token before extending access beyond the controlled machine network.

The first observed two-drive baseline before dual-profile deployment was:

- both drives PREOP with link up and zero lost frames;
- position 0 already mapped to PV `0x1601/0x1A01`, but faulted `0x8100` after
  the previous single-axis motiond stopped;
- position 1 still mapped to `0x1600/0x1A00`, mode display 1, and error 0;
- both target velocities and actual velocities were zero.

The `0x8100` transition fault is a known acceptance blocker and must not be
hidden or automatically reset. After inhibited activation, verify two slaves
OP, combined WC complete, D/E topology and routing, fault state, independent
SDO `0x603F`, disabled/request/moving false, controlword and target velocity
zero, and deadline miss/skip zero. Actual enable or motion requires a separate
operator-approved plan.
