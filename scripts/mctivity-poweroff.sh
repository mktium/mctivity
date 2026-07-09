#!/usr/bin/env bash
set -euo pipefail

SYSTEMCTL="${MCTIVITY_SYSTEMCTL_PATH:-/usr/bin/systemctl}"
STOP_UNITS="${MCTIVITY_POWEROFF_STOP_UNITS:-mctivity-kiosk.service mctivity-hmi.service mctivity-motiond.service ethercat.service}"
STOP_TIMEOUT_SEC="${MCTIVITY_POWEROFF_STOP_TIMEOUT_SEC:-45}"
FINAL_COMMAND="${MCTIVITY_POWEROFF_FINAL_COMMAND:-${SYSTEMCTL} poweroff}"

log() {
  printf '[mctivity-poweroff] %s\n' "$*"
}

stop_unit() {
  unit="$1"
  state="$("$SYSTEMCTL" is-active "$unit" 2>/dev/null || true)"
  if [ "$state" = "inactive" ] || [ "$state" = "unknown" ]; then
    log "$unit already $state"
    return 0
  fi

  log "stopping $unit"
  if command -v timeout >/dev/null 2>&1; then
    if timeout "${STOP_TIMEOUT_SEC}s" "$SYSTEMCTL" stop "$unit"; then
      return 0
    fi
  elif "$SYSTEMCTL" stop "$unit"; then
    return 0
  fi

  log "stop $unit did not complete cleanly; killing remaining unit processes"
  "$SYSTEMCTL" kill "$unit" >/dev/null 2>&1 || true
  "$SYSTEMCTL" reset-failed "$unit" >/dev/null 2>&1 || true
}

for unit in $STOP_UNITS; do
  stop_unit "$unit"
done

log "requesting system poweroff"
# FINAL_COMMAND is an administrator-controlled env value written by the installer.
# shellcheck disable=SC2086
exec $FINAL_COMMAND
