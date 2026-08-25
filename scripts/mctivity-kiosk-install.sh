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
if [ "$PROFILE" = "axis-d-uservo" ] || [ "$PROFILE" = "axis-d-uservo-pv" ] || [ "$PROFILE" = "axis-de-uservo-pv" ] || [ "$PROFILE" = "axis-de-uservo-gear" ] || [ "$PROFILE" = "axis-de-uservo-combined" ]; then
  TOPOLOGY="${MCTIVITY_TOPOLOGY:-$PROFILE}"
  if [ "$PROFILE" = "axis-de-uservo-combined" ]; then
    TOPOLOGY="axis-de-uservo-gear"
  fi
  COMMISSIONING_INHIBIT="${MCTIVITY_COMMISSIONING_INHIBIT:-1}"
  REQUIRE_REALTIME="${MCTIVITY_REQUIRE_REALTIME:-1}"
else
  TOPOLOGY="${MCTIVITY_TOPOLOGY:-legacy-dual}"
  COMMISSIONING_INHIBIT="${MCTIVITY_COMMISSIONING_INHIBIT:-0}"
  REQUIRE_REALTIME="${MCTIVITY_REQUIRE_REALTIME:-0}"
fi
RT_CPU="${MCTIVITY_RT_CPU:-}"
WEB_HOST="${MCTIVITY_WEB_HOST:-127.0.0.1}"
WEB_PORT="${MCTIVITY_WEB_PORT:-2015}"
ENABLE_POWEROFF="${MCTIVITY_ENABLE_POWEROFF:-0}"
SYSTEMCTL_PATH="${MCTIVITY_SYSTEMCTL_PATH:-/usr/bin/systemctl}"
POWEROFF_COMMAND="${MCTIVITY_SYSTEM_POWEROFF_COMMAND:-/usr/bin/sudo -n ${SYSTEMCTL_PATH} --no-block start mctivity-poweroff.service}"
POWEROFF_TIMEOUT="${MCTIVITY_SYSTEM_POWEROFF_COMMAND_TIMEOUT_SEC:-5}"
ENABLE_MOTIOND_RESTART="${MCTIVITY_ENABLE_MOTIOND_RESTART:-0}"
MOTIOND_RESTART_COMMAND="${MCTIVITY_SYSTEM_MOTIOND_RESTART_COMMAND:-/usr/bin/sudo -n ${SYSTEMCTL_PATH} restart mctivity-motiond.service}"
MOTIOND_RESTART_TIMEOUT="${MCTIVITY_SYSTEM_MOTIOND_RESTART_COMMAND_TIMEOUT_SEC:-10}"
POWEROFF_STOP_UNITS="${MCTIVITY_POWEROFF_STOP_UNITS:-mctivity-kiosk.service mctivity-hmi.service mctivity-motiond.service ethercat.service}"
POWEROFF_STOP_TIMEOUT="${MCTIVITY_POWEROFF_STOP_TIMEOUT_SEC:-45}"
POWEROFF_FINAL_COMMAND="${MCTIVITY_POWEROFF_FINAL_COMMAND:-${SYSTEMCTL_PATH} poweroff}"
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
BACKUP_ROOT="${MCTIVITY_BACKUP_ROOT:-/var/backups/mctivity}"

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

backup_dir="${BACKUP_ROOT}/kiosk-install-$(date -u +%Y%m%dT%H%M%SZ)-$$"
install -d -m 0755 "${backup_dir}"
: >"${backup_dir}/manifest.tsv"
for path in \
  /etc/mctivity/axis.env \
  /etc/mctivity/hmi.env \
  /etc/mctivity/poweroff.env \
  /etc/mctivity/kiosk.env \
  /etc/systemd/system/mctivity-motiond.service \
  /etc/systemd/system/mctivity-hmi.service \
  /etc/systemd/system/mctivity-kiosk.service \
  /etc/systemd/system/mctivity-poweroff.service \
  /etc/sudoers.d/mctivity-motiond-restart \
  /etc/systemd/system/mctivity-motiond.service.d/10-axis-d-realtime.conf; do
  if [ -e "$path" ]; then
    saved="$(printf '%s' "$path" | tr '/' '_')"
    cp -a "$path" "${backup_dir}/${saved}"
    printf 'present\t%s\t%s\n' "$path" "$saved" >>"${backup_dir}/manifest.tsv"
  else
    printf 'absent\t%s\t-\n' "$path" >>"${backup_dir}/manifest.tsv"
  fi
done
readlink -f "$ROOT" >"${backup_dir}/active-release.txt" 2>/dev/null || true
cat >"${backup_dir}/rollback.sh" <<'EOF'
#!/usr/bin/env sh
set -eu
backup_dir="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
tab="$(printf '\t')"
while IFS="$tab" read -r state target saved; do
  if [ "$state" = present ]; then
    cp -a "${backup_dir}/${saved}" "$target"
  else
    rm -f "$target"
  fi
done <"${backup_dir}/manifest.tsv"
systemctl daemon-reload
echo "configuration restored; repoint the active release and restart only after the inhibited read-only gate is ready"
EOF
chmod 0755 "${backup_dir}/rollback.sh"

{
  printf 'MCTIVITY_TOPOLOGY=%s\n' "$TOPOLOGY"
  printf 'MCTIVITY_PROFILE=%s\n' "$PROFILE"
  printf 'MCTIVITY_COMMISSIONING_INHIBIT=%s\n' "$COMMISSIONING_INHIBIT"
  printf 'MCTIVITY_REQUIRE_REALTIME=%s\n' "$REQUIRE_REALTIME"
} >/etc/mctivity/axis.env
chmod 0644 /etc/mctivity/axis.env

{
  printf 'MCTIVITY_PROFILE=%s\n' "$PROFILE"
  printf 'MCTIVITY_WEB_HOST=%s\n' "$WEB_HOST"
  printf 'MCTIVITY_WEB_PORT=%s\n' "$WEB_PORT"
  printf 'MCTIVITY_UI_STATE_PATH=/var/lib/mctivity/mctivity_hmi_state.json\n'
  printf 'MCTIVITY_SYSTEM_POWEROFF_ENABLED=%s\n' "$ENABLE_POWEROFF"
  printf 'MCTIVITY_SYSTEM_POWEROFF_COMMAND="%s"\n' "$POWEROFF_COMMAND"
  printf 'MCTIVITY_SYSTEM_POWEROFF_COMMAND_TIMEOUT_SEC=%s\n' "$POWEROFF_TIMEOUT"
  printf 'MCTIVITY_SYSTEM_MOTIOND_RESTART_ENABLED=%s\n' "$ENABLE_MOTIOND_RESTART"
  printf 'MCTIVITY_SYSTEM_MOTIOND_RESTART_COMMAND="%s"\n' "$MOTIOND_RESTART_COMMAND"
  printf 'MCTIVITY_SYSTEM_MOTIOND_RESTART_COMMAND_TIMEOUT_SEC=%s\n' "$MOTIOND_RESTART_TIMEOUT"
} >/etc/mctivity/hmi.env
chmod 0644 /etc/mctivity/hmi.env

{
  printf 'MCTIVITY_SYSTEMCTL_PATH=%s\n' "$SYSTEMCTL_PATH"
  printf 'MCTIVITY_POWEROFF_STOP_UNITS="%s"\n' "$POWEROFF_STOP_UNITS"
  printf 'MCTIVITY_POWEROFF_STOP_TIMEOUT_SEC=%s\n' "$POWEROFF_STOP_TIMEOUT"
  printf 'MCTIVITY_POWEROFF_FINAL_COMMAND="%s"\n' "$POWEROFF_FINAL_COMMAND"
} >/etc/mctivity/poweroff.env
chmod 0644 /etc/mctivity/poweroff.env

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
rewrite_unit "${ROOT}/systemd/mctivity-poweroff.service" /etc/systemd/system/mctivity-poweroff.service

motiond_dropin_dir=/etc/systemd/system/mctivity-motiond.service.d
motiond_dropin="${motiond_dropin_dir}/10-axis-d-realtime.conf"
if [ "$PROFILE" = "axis-d-uservo" ] || [ "$PROFILE" = "axis-d-uservo-pv" ] || [ "$PROFILE" = "axis-de-uservo-pv" ] || [ "$PROFILE" = "axis-de-uservo-gear" ] || [ "$PROFILE" = "axis-de-uservo-combined" ]; then
  install -d -m 0755 "$motiond_dropin_dir"
  if [ -n "$RT_CPU" ] && ! printf '%s' "$RT_CPU" | grep -Eq '^[0-9]+$'; then
    echo "invalid MCTIVITY_RT_CPU: $RT_CPU" >&2
    exit 1
  fi
  realtime_tmp="$(mktemp)"
  cp "${ROOT}/systemd/mctivity-motiond-axis-d-realtime.conf" "$realtime_tmp"
  if [ -n "$RT_CPU" ]; then
    printf 'CPUAffinity=%s\n' "$RT_CPU" >>"$realtime_tmp"
  fi
  install -m 0644 "$realtime_tmp" "$motiond_dropin"
  rm -f "$realtime_tmp"
else
  rm -f "$motiond_dropin"
fi

chmod 0755 \
  "${ROOT}/scripts/mctivity-kiosk-start.sh" \
  "${ROOT}/scripts/mctivity-kiosk-session.sh" \
  "${ROOT}/scripts/mctivity-axis-d-verify.sh" \
  "${ROOT}/scripts/mctivity-axis-d-pv-verify.sh" \
  "${ROOT}/scripts/mctivity-axis-d-stability.py" \
  "${ROOT}/scripts/mctivity-kiosk-verify.sh" \
  "${ROOT}/scripts/mctivity-motiond-launch.py" \
  "${ROOT}/scripts/mctivity-poweroff.sh"

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
  {
    printf '%s ALL=(root) NOPASSWD: %s --no-block start mctivity-poweroff.service\n' "$SERVICE_USER" "$SYSTEMCTL_PATH"
  } >"$sudoers_tmp"
  /usr/sbin/visudo -cf "$sudoers_tmp"
  install -m 0440 "$sudoers_tmp" /etc/sudoers.d/mctivity-poweroff
  rm -f "$sudoers_tmp"
else
  rm -f /etc/sudoers.d/mctivity-poweroff
fi

if [ "$ENABLE_MOTIOND_RESTART" = "1" ]; then
  if [ ! -x /usr/bin/sudo ]; then
    echo "sudo is required for motiond restart support" >&2
    exit 1
  fi
  if [ ! -x "$SYSTEMCTL_PATH" ]; then
    echo "systemctl not found or not executable: $SYSTEMCTL_PATH" >&2
    exit 1
  fi
  if [ ! -x /usr/sbin/visudo ]; then
    echo "visudo is required for motiond restart sudoers validation" >&2
    exit 1
  fi
  sudoers_tmp="$(mktemp)"
  printf '%s ALL=(root) NOPASSWD: %s restart mctivity-motiond.service\n' "$SERVICE_USER" "$SYSTEMCTL_PATH" >"$sudoers_tmp"
  /usr/sbin/visudo -cf "$sudoers_tmp"
  install -m 0440 "$sudoers_tmp" /etc/sudoers.d/mctivity-motiond-restart
  rm -f "$sudoers_tmp"
else
  rm -f /etc/sudoers.d/mctivity-motiond-restart
fi

systemctl daemon-reload
systemctl enable mctivity-motiond.service mctivity-hmi.service mctivity-kiosk.service

echo "mctivity kiosk install complete"
echo "backup: ${backup_dir}"
echo "rollback config: ${backup_dir}/rollback.sh; active release before this installer is recorded in active-release.txt"
