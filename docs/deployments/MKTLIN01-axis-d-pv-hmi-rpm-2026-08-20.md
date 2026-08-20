# MKTLIN01 Axis D PV HMI RPM units — 2026-08-20

This change keeps `/etc/mctivity/hmi.env` on `axis-d-uservo-pv` and changes only
the Axis D HMI presentation:

- velocity slider and configuration are in rpm (default 222, maximum 999, step 1);
- the HMI converts rpm to the native PV `0x60ff` counts/s value at the API
  boundary (`222 rpm -> 37000 cnt/s`);
- live velocity feedback is displayed in rpm;
- profile acceleration is displayed as 2222 rpm/s and is read-only. Native PV
  acceleration is object `0x6083`, configured at motiond startup, so changing it
  requires editing the module profile and a controlled no-motion restart.
- a latched `communication_timing_fault` is shown as a red alarm with
  “motiond restart required”; the HMI does not auto-reset it.

No enable, mode, jog, stop, fault reset, or other motion command is part of this
change. Deployment must retain `MCTIVITY_COMMISSIONING_INHIBIT=1` and use the
existing GET-only verification. The known restart-transition `0x8100` risk and
the timing-fault latch procedure remain unchanged.
