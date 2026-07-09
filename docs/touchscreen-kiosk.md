# Touchscreen Kiosk

`mctivity v1.4` adds an optional local touchscreen kiosk layer. The kiosk layer is separate from the motion daemon and Web HMI: it only starts a local browser on the machine display.

## Module

The module id is:

```text
ui-touchscreen
```

It declares:

```text
ui.touchscreen.kiosk
```

For MKTLIN01 the module is loaded through the `full` profile.

## Services

The touchscreen deployment uses four services:

- `ethercat.service`
- `mctivity-motiond.service`
- `mctivity-hmi.service`
- `mctivity-kiosk.service`

The kiosk service starts Xorg and Chromium in kiosk mode at:

```text
http://127.0.0.1:2015/
```

## Configuration

The installer writes:

```text
/etc/mctivity/hmi.env
/etc/mctivity/kiosk.env
```

Important defaults:

```text
MCTIVITY_PROFILE=full
MCTIVITY_WEB_HOST=127.0.0.1
MCTIVITY_WEB_PORT=2015
MCTIVITY_SYSTEM_POWEROFF_ENABLED=0
MCTIVITY_SYSTEM_POWEROFF_COMMAND="/usr/bin/sudo -n /usr/bin/systemctl poweroff"
MCTIVITY_SYSTEM_POWEROFF_PRE_COMMANDS="/usr/bin/sudo -n /usr/bin/systemctl stop mctivity-motiond.service;;/usr/bin/sudo -n /usr/bin/systemctl stop ethercat.service;;/usr/bin/sudo -n /usr/bin/systemctl stop mctivity-kiosk.service"
MCTIVITY_SYSTEM_POWEROFF_COMMAND_TIMEOUT_SEC=15
MCTIVITY_KIOSK_URL=http://127.0.0.1:2015/
MCTIVITY_KIOSK_DISPLAY=:0
MCTIVITY_KIOSK_VT=7
MCTIVITY_KIOSK_ROTATE=normal
MCTIVITY_KIOSK_HIDE_CURSOR=1
MCTIVITY_KIOSK_OUTPUT=auto
MCTIVITY_KIOSK_OUTPUT_PREFERENCE=HDMI-1,HDMI-A-1,DP-1,DP-2,VGA-1,eDP-1
MCTIVITY_KIOSK_DISABLE_OTHER_OUTPUTS=1
MCTIVITY_KIOSK_MAP_TOUCH=1
MCTIVITY_KIOSK_TOUCH_NAME=G2Touch
MCTIVITY_KIOSK_SCALE_FACTOR=1.5
```

`MCTIVITY_KIOSK_OUTPUT=auto` chooses the first connected output from
`MCTIVITY_KIOSK_OUTPUT_PREFERENCE`. The default preference puts HDMI before
embedded outputs so an attached touchscreen panel is used as the kiosk display.
When `MCTIVITY_KIOSK_DISABLE_OTHER_OUTPUTS=1`, other connected X outputs are
turned off for the kiosk session and the touch device is mapped to the active
kiosk output.

`MCTIVITY_KIOSK_SCALE_FACTOR=1.5` starts Chromium at 150% device scale for
touchscreen use.

## System Menu And Poweroff

The top-left system menu contains language controls and an optional poweroff
action. Poweroff is disabled unless the installer is run with:

```bash
MCTIVITY_ENABLE_POWEROFF=1
```

When enabled, the installer writes a minimal sudoers rule:

```text
iiru ALL=(root) NOPASSWD: /usr/bin/systemctl stop mctivity-motiond.service
iiru ALL=(root) NOPASSWD: /usr/bin/systemctl stop ethercat.service
iiru ALL=(root) NOPASSWD: /usr/bin/systemctl stop mctivity-kiosk.service
iiru ALL=(root) NOPASSWD: /usr/bin/systemctl poweroff
```

The HMI service sets `NoNewPrivileges=false` so the constrained sudoers command
can elevate. The sudoers rule still limits the HMI user to the explicit service
stop commands and `systemctl poweroff`.

The HMI exposes:

```text
POST /api/system/poweroff
```

The request body must include:

```json
{"confirm":"poweroff"}
```

For a no-shutdown permission and machine-state check:

```json
{"confirm":"poweroff","dry_run":true}
```

The server blocks poweroff if either axis reports `moving=true` or
`gear_running=true`, or if device status cannot be read. The touchscreen UI
requires opening the poweroff dialog and holding the red button for 2 seconds
before the real poweroff request is sent.

On a real poweroff request, HMI runs staged shutdown commands before poweroff:

```text
systemctl stop mctivity-motiond.service
systemctl stop ethercat.service
systemctl stop mctivity-kiosk.service
systemctl poweroff
```

Dry-run verifies axis state and sudo permission for the staged commands, but it
does not stop any service and does not power off the machine.

With the local-only HMI binding, remote browser access should use an SSH tunnel:

```bash
ssh -L 2015:127.0.0.1:2015 mctivity-host
```

## Install

On the target machine, after copying the release to `/opt/mctivity`, run as root:

```bash
MCTIVITY_SERVICE_USER=iiru \
MCTIVITY_SERVICE_GROUP=iiru \
MCTIVITY_INSTALL_PACKAGES=1 \
MCTIVITY_ENABLE_POWEROFF=1 \
/opt/mctivity/scripts/mctivity-kiosk-install.sh
```

Then restart the services:

```bash
systemctl restart mctivity-motiond.service mctivity-hmi.service mctivity-kiosk.service
```

## Verify

```bash
/opt/mctivity/scripts/mctivity-kiosk-verify.sh
```
