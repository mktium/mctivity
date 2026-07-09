#!/usr/bin/env bash
set -euo pipefail

if [ "${EUID:-$(id -u)}" -ne 0 ]; then
  echo "run as root" >&2
  exit 1
fi

ROOT="${MCTIVITY_ROOT:-/opt/mctivity}"
SERVICE_USER="${MCTIVITY_SERVICE_USER:-mctivity}"
SERVICE_GROUP="${MCTIVITY_SERVICE_GROUP:-$SERVICE_USER}"
INSTALL_PACKAGES="${MCTIVITY_INSTALL_PACKAGES:-0}"
PROFILE="${MCTIVITY_PROFILE:-full}"
WEB_HOST="${MCTIVITY_WEB_HOST:-127.0.0.1}"
WEB_PORT="${MCTIVITY_WEB_PORT:-2015}"
ENABLE_POWEROFF="${MCTIVITY_ENABLE_POWEROFF:-0}"
SYSTEMCTL_PATH="${MCTIVITY_SYSTEMCTL_PATH:-/usr/bin/systemctl}"
POWEROFF_COMMAND="${MCTIVITY_SYSTEM_POWEROFF_COMMAND:-/usr/bin/sudo -n ${SYSTEMCTL_PATH} poweroff}"
POWEROFF_CHECK_COMMAND="${MCTIVITY_SYSTEM_POWEROFF_CHECK_COMMAND:-/usr/bin/sudo -n -l ${SYSTEMCTL_PATH} poweroff}"
KIOSK_URL="${MCTIVITY_KIOSK_URL:-http://127.0.0.1:2015/}"
KIOSK_DISPLAY="${MCTIVITY_KIOSK_DISPLAY:-:0}"
KIOSK_VT="${MCTIVITY_KIOSK_VT:-7}"
KIOSK_ROTATE="${MCTIVITY_KIOSK_ROTATE:-normal}"
KIOSK_HIDE_CURSOR="${MCTIVITY_KIOSK_HIDE_CURSOR:-1}"
KIOSK_OUTPUT="${MCTIVITY_KIOSK_OUTPUT:-auto}"
KIOSK_OUTPUT_PREFERENCE="${MCTIVITY_KIOSK_OUTPUT_PREFERENCE:-HDMI-1,HDMI-A-1,DP-1,DP-2,VGA-1,eDP-1}"
KIOSK_DISABLE_OTHER_OUTPUTS="${MCTIVITY_KIOSK_DISABLE_OTHER_OUTPUTS:-1}"
KIOSK_MAP_TOUCH="${MCTIVITY_KIOSK_MAP_TOUCH:-1}"
KIOSK_TOUCH_NAME="${MCTIVITY_KIOSK_TOUCH_NAME:-G2Touch}"
KIOSK_SCALE_FACTOR="${MCTIVITY_KIOSK_SCALE_FACTOR:-1.5}"

packages=(
  chromium
  xserver-xorg-core
  xserver-xorg-legacy
  xserver-xorg-input-libinput
  xinit
  openbox
  unclutter
  x11-xserver-utils
  xinput
  libinput-tools
)

if [ "$INSTALL_PACKAGES" = "1" ]; then
  apt-get update
  DEBIAN_FRONTEND=noninteractive apt-get install -y "${packages[@]}"
fi

if ! id "$SERVICE_USER" >/dev/null 2>&1; then
  echo "service user does not exist: $SERVICE_USER" >&2
  exit 1
fi

install -d -m 0755 /etc/mctivity

{
  printf 'MCTIVITY_PROFILE=%s\n' "$PROFILE"
  printf 'MCTIVITY_WEB_HOST=%s\n' "$WEB_HOST"
  printf 'MCTIVITY_WEB_PORT=%s\n' "$WEB_PORT"
  printf 'MCTIVITY_UI_STATE_PATH=/var/lib/mctivity/mctivity_hmi_state.json\n'
  printf 'MCTIVITY_SYSTEM_POWEROFF_ENABLED=%s\n' "$ENABLE_POWEROFF"
  printf 'MCTIVITY_SYSTEM_POWEROFF_COMMAND="%s"\n' "$POWEROFF_COMMAND"
  printf 'MCTIVITY_SYSTEM_POWEROFF_CHECK_COMMAND="%s"\n' "$POWEROFF_CHECK_COMMAND"
} >/etc/mctivity/hmi.env
chmod 0644 /etc/mctivity/hmi.env

{
  printf 'MCTIVITY_ROOT=%s\n' "$ROOT"
  printf 'MCTIVITY_KIOSK_URL=%s\n' "$KIOSK_URL"
  printf 'MCTIVITY_KIOSK_DISPLAY=%s\n' "$KIOSK_DISPLAY"
  printf 'MCTIVITY_KIOSK_VT=%s\n' "$KIOSK_VT"
  printf 'MCTIVITY_KIOSK_ROTATE=%s\n' "$KIOSK_ROTATE"
  printf 'MCTIVITY_KIOSK_HIDE_CURSOR=%s\n' "$KIOSK_HIDE_CURSOR"
  printf 'MCTIVITY_KIOSK_OUTPUT=%s\n' "$KIOSK_OUTPUT"
  printf 'MCTIVITY_KIOSK_OUTPUT_PREFERENCE=%s\n' "$KIOSK_OUTPUT_PREFERENCE"
  printf 'MCTIVITY_KIOSK_DISABLE_OTHER_OUTPUTS=%s\n' "$KIOSK_DISABLE_OTHER_OUTPUTS"
  printf 'MCTIVITY_KIOSK_MAP_TOUCH=%s\n' "$KIOSK_MAP_TOUCH"
  printf 'MCTIVITY_KIOSK_TOUCH_NAME=%s\n' "$KIOSK_TOUCH_NAME"
  printf 'MCTIVITY_KIOSK_SCALE_FACTOR=%s\n' "$KIOSK_SCALE_FACTOR"
} >/etc/mctivity/kiosk.env
chmod 0644 /etc/mctivity/kiosk.env

rewrite_unit() {
  src="$1"
  dest="$2"
  tmp="$(mktemp)"
  sed \
    -e "s|^User=.*|User=${SERVICE_USER}|" \
    -e "s|^Group=.*|Group=${SERVICE_GROUP}|" \
    -e "s|/opt/mctivity|${ROOT}|g" \
    "$src" >"$tmp"
  install -m 0644 "$tmp" "$dest"
  rm -f "$tmp"
}

rewrite_unit "${ROOT}/systemd/mctivity-motiond.service" /etc/systemd/system/mctivity-motiond.service
rewrite_unit "${ROOT}/systemd/mctivity-hmi.service" /etc/systemd/system/mctivity-hmi.service
rewrite_unit "${ROOT}/systemd/mctivity-kiosk.service" /etc/systemd/system/mctivity-kiosk.service

chmod 0755 \
  "${ROOT}/scripts/mctivity-kiosk-start.sh" \
  "${ROOT}/scripts/mctivity-kiosk-session.sh" \
  "${ROOT}/scripts/mctivity-kiosk-verify.sh"

if [ "$ENABLE_POWEROFF" = "1" ]; then
  if [ ! -x /usr/bin/sudo ]; then
    echo "sudo is required for poweroff support" >&2
    exit 1
  fi
  if [ ! -x "$SYSTEMCTL_PATH" ]; then
    echo "systemctl not found or not executable: $SYSTEMCTL_PATH" >&2
    exit 1
  fi
  if [ ! -x /usr/sbin/visudo ]; then
    echo "visudo is required for poweroff sudoers validation" >&2
    exit 1
  fi
  sudoers_tmp="$(mktemp)"
  printf '%s ALL=(root) NOPASSWD: %s poweroff\n' "$SERVICE_USER" "$SYSTEMCTL_PATH" >"$sudoers_tmp"
  /usr/sbin/visudo -cf "$sudoers_tmp"
  install -m 0440 "$sudoers_tmp" /etc/sudoers.d/mctivity-poweroff
  rm -f "$sudoers_tmp"
else
  rm -f /etc/sudoers.d/mctivity-poweroff
fi

systemctl daemon-reload
systemctl enable mctivity-motiond.service mctivity-hmi.service mctivity-kiosk.service

echo "mctivity kiosk install complete"
