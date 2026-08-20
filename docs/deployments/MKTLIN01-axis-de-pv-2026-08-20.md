# MKTLIN01 Axis D/E dual Uservo PV - 2026-08-20

## Hardware discovery baseline

Two identical Uservo / DS1-E4806N-4I slaves were discovered in fixed ring
positions 0 and 1. Both report vendor `0x00666999`, product `0x00004806`,
revision `1`, serial `1`, hardware `0.7`, firmware `5.11`, encoder resolution
`10000/1`, and gear ratio `1/1`. Because alias and serial are identical, the
deployment maps the upstream drive to D and the downstream drive to E by
absolute ring position (`alias=0`, position 0/1).

Before implementation, motiond was stopped and
`MCTIVITY_COMMISSIONING_INHIBIT=1` was restored. Both drives returned to PREOP.
Position 0 then showed the known restart/stop transition fault `0x8100`;
position 1 remained error-free. No reset, mode change, SDO download, enable, or
motion command was issued.

## Profile and parameter source

`profiles/axis-de-uservo-pv.json` instantiates the existing
`axis-device-uservo-pv` module twice. D and E inherit the same confirmed motor,
encoder, and native-PV values from the one module manifest; only logical axis,
transport key, and physical position are instance data. Runtime values are
resolved separately for D and E, even though the confirmed hardware values are
currently equal.

## Deployment result

- implementation commit: `92607f0aef0d33fa24aae57fb51b28ca30fd27f2`;
- pushed branch: `mktium/mctivity` `feature/v1.4.1-axis-d-uservo`;
- release: `/opt/mctivity-releases/v1.4.1-axis-de-pv-92607f0`;
- source archive SHA-256:
  `5be52d5b3dfba9dc0e17f8ab68fe5377c2248113f2c715abcf213c16b6101d54`;
- target-built motiond SHA-256:
  `3641d3e4a65c3611aeed55db83a6e20a30dc4eafc94832bbd89f1606d5a910df`;
- pre-switch backup:
  `/var/backups/mctivity/pre-axis-de-pv-20260820T100318-10017`.

The target compiled with the real `/opt/etherlab` headers and library using
`-Wall -Wextra -Werror`. Release preflight, profile/JSON validation, legacy
profile regression, Python and rendered JavaScript syntax, HMI tests, shell
syntax, C tests, and systemd unit verification passed. Both `axis.env` and
`hmi.env` now select `axis-de-uservo-pv`; commissioning inhibit and the real-time
requirement remain `1`.

On first activation, D entered OP immediately while E needed approximately six
seconds to complete DC synchronization. During that interval the combined
domain stayed at WC `3/6` and the timing guard remained unarmed. E then entered
OP and the stable combined WC became `6/6`. Both axes report the correct logical
labels, independent transport routes, 10000 counts/revolution, native velocity
capability, and zero command/target state. Both are disabled with
`servo_request=false`, `moving=false`, `cw=0`; deadline miss and skipped-period
counts are zero. The 10001 motion socket and 2015 HMI listener are local-only.
HMI and kiosk are active, Xorg/Chromium are running, and HDMI-1 outputs
1920x1080.

The HMI was subsequently opened to the controlled LAN at
`http://192.168.1.201:2015`. The previous failure was caused by
`MCTIVITY_WEB_HOST=127.0.0.1`, not by routing or the firewall. The deployed HMI
now binds `0.0.0.0:2015` and allows Host `192.168.1.201`; the firewall input
policy is accept. A request from the commissioning workstation returned HTTP
200 and the active `axis-de-uservo-pv` capability manifest. The previous
`hmi.env` is backed up under
`/var/backups/mctivity/hmi-lan-20260820T101400`. No motiond restart or motor
command was involved. LAN command access currently has no API token and must
therefore remain on the trusted machine network.

The final read-only verifier deliberately did not pass: D retained the known
`0x8100` transition fault (`6041=0x1218`, status `fault=true`), while E was
fault-free. No automatic reset was issued. EtherCAT SDO upload cannot be
performed while motiond owns the active master; the typed pre-activation read
is therefore the independent `0x603F` evidence. The active statusword is the
runtime fault evidence. This remaining fault must be reset and rechecked by the
operator before any enable plan.

Rollback is recoverable: while inhibited, stop kiosk, HMI, and motiond; restore
`/etc/mctivity`, installed units/drop-ins, and the old symlink target recorded
in the backup directory; run daemon-reload and unit verification; then start
the prior services and repeat read-only checks. Neither release is deleted.
Motion testing was not performed and still requires a separate operator-approved
plan.

## Atomic synchronized-velocity and LAN follow-up

Commit `9302dfbd60c76e3ffe3c138903b34f0179d329f6` adds the D+E atomic
velocity group, fixes inhibited fault-reset controlword delivery, improves HMI
reset feedback, and records the MKTLIN01 LAN HMI environment. The source archive
SHA-256 is
`9e9b3584586625cc7f8cc7c86c373908ba72c5bdf57121cf0f83d6c256aa2d0e`.
Target release `/opt/mctivity-releases/v1.4.1-axis-de-sync-9302dfb` was
compiled with the real EtherLab headers under `-Werror`; its motiond SHA-256 is
`ab17ceaebb1d8cdf3d9bd5a00ee8149b7e94251287efdbe3c345eb043b01a6b2`.
The pre-switch backup is
`/var/backups/mctivity/pre-axis-de-sync-20260820T102954-18624`.

The synchronized group uses device-free `sync_enable`, `sync_disable`,
`sync_jog_velocity`, and `sync_stop` commands. All D/E preconditions are checked
before either runtime slot changes, and both controlwords/targets are emitted in
the next combined-domain frame. Once the group has reached enabled state, loss
of either axis, WC/OP, enabled state, or fault-free state disables and clears
both axes before output and latches a group safety interlock. No synchronized
command was exercised during deployment.

The first dual inhibited implementation suppressed `0x0080` together with all
other controlwords, which explained why the user's HMI fault-reset click had no
effect. The corrected writer permits only `0x0080` while inhibited; mode,
target velocity, and enabling controlwords remain zero. After the new release
restart, both drives showed the known transition fault. The operator reset D
and E once each through the HMI. Read-only status then confirmed both
`fault=false`, OP, combined WC `6/6`, disabled, `servo_request=false`,
`moving=false`, `cw=0`, target velocity zero, deadline miss/skip `0/0`, and all
group session/motion/safety-latch flags false. The GET/status-only kiosk verifier
passed completely. No enable, mode selection, velocity, stop, or motion command
was sent during acceptance.
