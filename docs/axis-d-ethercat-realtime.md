# Axis D EtherCAT Realtime Contract

## Official source baseline

The Axis D implementation is pinned to the XActant customer help center rather than to legacy axis assumptions:

- [EtherCAT settings](https://xingdongyuan.feishu.cn/wiki/WSSxw0hKmiEWd9kAoiAcySkNnog)
- [CiA 402 topic](https://xingdongyuan.feishu.cn/wiki/Csg6wk6LaiWmMjkAfwTcNsyDnJe)
- [official EtherCAT XML downloads](https://xingdongyuan.feishu.cn/wiki/NmgEwu13uioULQkZZKAcs1srn5c)
- [error-code quick lookup](https://xingdongyuan.feishu.cn/wiki/XH6rwdeGZioGd3kndDLcFrz5nFc)

The applicable low-voltage ESI is `XActant-E-XML-6120R.xml` for `DS1-E48xxx-4I`, firmware baseline V6.1.20 Release. Its `Uservo` device declares vendor `0x00666999`, product `0x00004806`, revision `0x00000001`, DC assign-activate `0x0300`, and a 1 ms default interpolation period. The drive documentation permits DC periods from 125 microseconds through 8 milliseconds.

The selected default PDOs are exact ESI maps:

```text
RxPDO 0x1600: 6040:00/16, 6060:00/8, 607A:00/32, 60FE:01/32
TxPDO 0x1A00: 6041:00/16, 6061:00/8, 6064:00/32, 60FD:00/32
```

SM2 output watchdog behavior remains enabled as declared by the ESI. The implementation must not disable the watchdog to hide host timing defects. The DS1 product-level mode list is PP, PV, PT, HM, and CSP; this profile uses CSP (`0x6060=8`) only after the separate motion gate. It does not claim CSV or CST support from the generic CiA 402 mode list.

The separate native PV profile uses the same DC period and watchdog contract with the ESI's alternative maps:

```text
RxPDO 0x1601: 6040:00/16, 6060:00/8, 60FF:00/32, 60FE:01/32
TxPDO 0x1A01: 6041:00/16, 6061:00/8, 606C:00/32, 60FD:00/32
```

PV is CiA 402 mode code `3`; target velocity is `0x60FF`, actual velocity is `0x606C`, and profile acceleration/deceleration are the drive objects `0x6083`/`0x6084`. The implementation does not claim CSV (`9`) or infer a velocity map from the old CSP profile.

Official fault `0x8100` is `Communication_DS_301`: after the slave enters OP, loss of PDO communication for the configured timeout raises a communication alarm. The default is 100 ms and the timeout is configured through object `0x36B5`. It is distinct from EtherCAT AL code `0x001B` (Sync Manager watchdog).

## Host realtime invariants

Axis D is allowed to enter its cyclic path only when:

- `/etc/mctivity/axis.env` contains `MCTIVITY_COMMISSIONING_INHIBIT=1`
- `MCTIVITY_REQUIRE_REALTIME=1`
- the process has locked current and future memory
- the process is actually running under `SCHED_FIFO` with a positive priority

The Axis D scheduler never sends catch-up cycles. If a deadline has expired, every expired period is counted and skipped, and only the next future deadline is used. DC application time comes from that scheduled deadline, not from an ordinary loop counter. Shutdown frames also receive a fresh application time; the old 300-cycle stale-time shutdown is not used.

The command socket has fixed per-cycle budgets: at most one newly accepted connection, one read per client, and two commands total. A nonblocking setup failure closes the client instead of allowing the realtime loop to block.

After 1000 consecutive OP/WC-complete cycles, the timing guard arms. Any later missed deadline, non-OP state, or incomplete working counter latches `communication_timing_fault`, clears motion and servo requests, writes controlword and mode zero, and pins target position to actual position. The latch cannot be cleared through a motion or fault-reset command; the daemon must be restarted under a verified stable and inhibited configuration.

## No-motion acceptance

Every test in this section is read-only at the drive command layer. Do not send enable, drive fault reset, target position, target velocity, or target torque.

1. Run `scripts/mctivity-axis-d-verify.sh` after the timing guard has armed.
2. Record read-only SDO `0x603F`, AL state, WC, link/lost-frame counters, scheduler policy, priority, and `VmLck`.
3. Run `scripts/mctivity-axis-d-stability.py --duration 1800 --max-position-span 0` with kiosk and HMI at normal load.
4. Compare kernel EtherCAT logs across the same interval and require no new `TIMED OUT`, `skipped`, `unmatched`, or WC-change event.
5. Require no changes to deadline-miss, skipped-period, WC-change, or incomplete-WC counters during the window.

The gate passes only with continuous OP, complete WC, drive error zero, `enabled=false`, `servo_request=false`, `moving=false`, `cw=0`, inhibit true, and no communication-timing latch. Any failure keeps inhibit in place and blocks motion testing.

CPU and IRQ affinity are host-specific. They may be added through `MCTIVITY_RT_CPU` only after the target CPU/IRQ topology is measured. Do not hard-code MKTLIN01 affinity into the generic unit. If the generic `r8169` EtherCAT path still produces any WC transition after scheduler hardening, use a supported dedicated NIC/native EtherCAT driver before considering motion.
