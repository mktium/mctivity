#!/usr/bin/env bash
set -euo pipefail

URL="${MCTIVITY_KIOSK_URL:-http://127.0.0.1:2015/}"
ROTATE="${MCTIVITY_KIOSK_ROTATE:-normal}"
HIDE_CURSOR="${MCTIVITY_KIOSK_HIDE_CURSOR:-1}"
OUTPUT="${MCTIVITY_KIOSK_OUTPUT:-auto}"
OUTPUT_PREFERENCE="${MCTIVITY_KIOSK_OUTPUT_PREFERENCE:-HDMI-1,HDMI-A-1,DP-1,DP-2,VGA-1,eDP-1}"
DISABLE_OTHER_OUTPUTS="${MCTIVITY_KIOSK_DISABLE_OTHER_OUTPUTS:-1}"
MAP_TOUCH="${MCTIVITY_KIOSK_MAP_TOUCH:-1}"
TOUCH_NAME="${MCTIVITY_KIOSK_TOUCH_NAME:-G2Touch}"
PROFILE_DIR="${MCTIVITY_KIOSK_PROFILE_DIR:-${XDG_STATE_HOME:-$HOME/.local/state}/mctivity/chromium-kiosk}"

find_chromium() {
  for cmd in chromium chromium-browser google-chrome google-chrome-stable; do
    if command -v "$cmd" >/dev/null 2>&1; then
      command -v "$cmd"
      return 0
    fi
  done
  return 1
}

is_connected_output() {
  output="$1"
  xrandr --query | awk -v output="$output" '$1 == output && $2 == "connected" { found = 1 } END { exit found ? 0 : 1 }'
}

choose_output() {
  if ! command -v xrandr >/dev/null 2>&1; then
    return 1
  fi

  if [ "$OUTPUT" != "auto" ]; then
    if is_connected_output "$OUTPUT"; then
      printf '%s\n' "$OUTPUT"
      return 0
    fi
    echo "configured kiosk output is not connected: $OUTPUT" >&2
  fi

  old_ifs="$IFS"
  IFS=","
  for candidate in $OUTPUT_PREFERENCE; do
    IFS="$old_ifs"
    if [ -n "$candidate" ] && is_connected_output "$candidate"; then
      printf '%s\n' "$candidate"
      return 0
    fi
    IFS=","
  done
  IFS="$old_ifs"

  xrandr --query | awk '/ connected/{ print $1; exit }'
}

configure_display() {
  if ! command -v xrandr >/dev/null 2>&1; then
    return 0
  fi

  kiosk_output="$(choose_output || true)"
  if [ -z "$kiosk_output" ]; then
    echo "no connected display output found for kiosk" >&2
    return 0
  fi

  echo "using kiosk output: $kiosk_output"
  if [ "$DISABLE_OTHER_OUTPUTS" = "1" ]; then
    args=(--output "$kiosk_output" --auto --primary --rotate "$ROTATE")
    while IFS= read -r connected_output; do
      if [ "$connected_output" != "$kiosk_output" ]; then
        args+=(--output "$connected_output" --off)
      fi
    done < <(xrandr --query | awk '/ connected/{ print $1 }')
    xrandr "${args[@]}" || true
  else
    xrandr --output "$kiosk_output" --auto --primary --rotate "$ROTATE" || true
  fi

  export MCTIVITY_KIOSK_ACTIVE_OUTPUT="$kiosk_output"
}

configure_touch() {
  if [ "$MAP_TOUCH" != "1" ] || [ -z "${MCTIVITY_KIOSK_ACTIVE_OUTPUT:-}" ]; then
    return 0
  fi
  if ! command -v xinput >/dev/null 2>&1; then
    return 0
  fi

  xinput list | awk -v pat="$TOUCH_NAME" 'index($0, pat) {
    if (match($0, /id=[0-9]+/)) {
      print substr($0, RSTART + 3, RLENGTH - 3)
    }
  }' | while IFS= read -r input_id; do
    if [ -n "$input_id" ]; then
      echo "mapping touch input $input_id to ${MCTIVITY_KIOSK_ACTIVE_OUTPUT}"
      xinput map-to-output "$input_id" "$MCTIVITY_KIOSK_ACTIVE_OUTPUT" || true
    fi
  done
}

CHROMIUM="$(find_chromium || true)"
if [ -z "$CHROMIUM" ]; then
  echo "chromium is not installed" >&2
  exit 127
fi

xset s off -dpms s noblank >/dev/null 2>&1 || true

configure_display
configure_touch

if command -v openbox >/dev/null 2>&1; then
  openbox >/dev/null 2>&1 &
fi

if [ "$HIDE_CURSOR" = "1" ] && command -v unclutter >/dev/null 2>&1; then
  unclutter -idle 0.5 -root >/dev/null 2>&1 &
fi

mkdir -p "$PROFILE_DIR"

exec "$CHROMIUM" \
  --kiosk "$URL" \
  --no-first-run \
  --no-default-browser-check \
  --disable-restore-session-state \
  --disable-session-crashed-bubble \
  --disable-infobars \
  --disable-features=Translate,AutofillServerCommunication \
  --overscroll-history-navigation=0 \
  --check-for-update-interval=31536000 \
  --user-data-dir="$PROFILE_DIR"
