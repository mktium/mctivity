#!/usr/bin/env bash
set -euo pipefail

ROOT="${MCTIVITY_ROOT:-/opt/mctivity}"
DISPLAY_NAME="${MCTIVITY_KIOSK_DISPLAY:-:0}"
VT="${MCTIVITY_KIOSK_VT:-7}"
SESSION_SCRIPT="${ROOT}/scripts/mctivity-kiosk-session.sh"

if ! command -v startx >/dev/null 2>&1; then
  echo "startx is not installed; install xinit before starting kiosk" >&2
  exit 127
fi

if [ ! -x "$SESSION_SCRIPT" ]; then
  echo "kiosk session script is not executable: $SESSION_SCRIPT" >&2
  exit 126
fi

export MCTIVITY_KIOSK_URL="${MCTIVITY_KIOSK_URL:-http://127.0.0.1:2015/}"
export MCTIVITY_KIOSK_HIDE_CURSOR="${MCTIVITY_KIOSK_HIDE_CURSOR:-1}"
export MCTIVITY_KIOSK_ROTATE="${MCTIVITY_KIOSK_ROTATE:-normal}"

exec startx "$SESSION_SCRIPT" -- "$DISPLAY_NAME" "vt${VT}" -keeptty -nolisten tcp -s 0 -dpms
