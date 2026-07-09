#!/usr/bin/env bash
set -euo pipefail

URL="${MCTIVITY_VERIFY_URL:-http://127.0.0.1:2015}"

echo "== services =="
systemctl --no-pager --full status ethercat.service mctivity-motiond.service mctivity-hmi.service mctivity-kiosk.service || true

echo "== listeners =="
ss -ltnp | grep -E '(:2015|:10001|:22)' || true

echo "== hmi api =="
curl -fsS "${URL}/api/status?device=mctivity" >/dev/null && echo "mctivity status ok"
curl -fsS "${URL}/api/status?device=fv3" >/dev/null && echo "fv3 status ok"
curl -fsS "${URL}/api/capabilities" >/dev/null && echo "capabilities ok"
curl -fsS "${URL}/api/health/modular" >/dev/null && echo "modular health ok"

echo "== display =="
for status in /sys/class/drm/card*-*/status; do
  [ -r "$status" ] && printf '%s=%s\n' "$status" "$(cat "$status")"
done
if command -v xrandr >/dev/null 2>&1; then
  DISPLAY="${MCTIVITY_KIOSK_DISPLAY:-:0}" xrandr --query 2>/dev/null || true
fi

echo "== touch input =="
grep -A5 -B1 -E 'Touch|G2Touch|touch' /proc/bus/input/devices || true
if command -v xinput >/dev/null 2>&1; then
  DISPLAY="${MCTIVITY_KIOSK_DISPLAY:-:0}" xinput list 2>/dev/null || true
fi

echo "== browser processes =="
pgrep -a -f 'chromium|chrome|Xorg|openbox|unclutter' || true
