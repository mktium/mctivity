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
