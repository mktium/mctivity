#!/usr/bin/env bash
set -euo pipefail

URL="${MCTIVITY_VERIFY_URL:-http://127.0.0.1:2015}"
EXPECTED_PROFILE="${MCTIVITY_EXPECT_PROFILE:-}"

echo "== services =="
systemctl --no-pager --full status ethercat.service mctivity-motiond.service mctivity-hmi.service mctivity-kiosk.service || true

echo "== listeners =="
ss -ltnp | grep -E '(:2015|:10001|:22)' || true

echo "== hmi api =="
status_json="$(curl -fsS "${URL}/api/status?device=mctivity")"
echo "mctivity status ok"
capabilities="$(curl -fsS "${URL}/api/capabilities")"
echo "capabilities ok"
if CAPABILITIES_JSON="$capabilities" python3 -c 'import json, os, sys; sys.exit(0 if "axis.device.fv3.access" in json.loads(os.environ["CAPABILITIES_JSON"]).get("capabilities", []) else 1)'; then
  curl -fsS "${URL}/api/status?device=fv3" >/dev/null && echo "fv3 status ok"
fi
curl -fsS "${URL}/api/health/modular" >/dev/null && echo "modular health ok"

CAPABILITIES_JSON="$capabilities" STATUS_JSON="$status_json" EXPECTED_PROFILE="$EXPECTED_PROFILE" python3 <<'PY'
import json
import os
import socket

capabilities = json.loads(os.environ["CAPABILITIES_JSON"])
status_response = json.loads(os.environ["STATUS_JSON"])
expected_profile = os.environ.get("EXPECTED_PROFILE", "").strip()
profile = capabilities.get("profile")

if expected_profile and profile != expected_profile:
    raise SystemExit(f"profile mismatch: expected {expected_profile}, got {profile}")

if profile == "axis-d-uservo":
    assert capabilities.get("primary_axis_label") == "D", capabilities
    assert capabilities.get("counts_per_rev") == 10000, capabilities
    assert capabilities.get("commissioning_inhibit") is True, capabilities
    axis_devices = capabilities.get("axis_devices") or []
    assert axis_devices and axis_devices[0].get("topology") == "axis-d-uservo", capabilities

    status = status_response.get("status") or {}
    assert status.get("topology") == "axis-d-uservo", status
    assert status.get("counts_per_rev") == 10000, status
    assert status.get("commissioning_inhibit") is True, status
    assert status.get("phase_search_confirmation_required") is True, status
    assert status.get("phase_search_confirmed") is False, status
    assert status.get("operational") == 1, status
    assert status.get("wc_complete") is True, status
    assert status.get("enabled") is False, status
    assert status.get("servo_request") is False, status
    assert status.get("fault") is False, status
    assert status.get("cw") == 0, status
    assert status.get("rt_memory_locked") is True, status
    assert status.get("rt_scheduler_policy") == 1, status
    assert int(status.get("rt_scheduler_priority", 0)) > 0, status
    assert status.get("timing_guard_armed") is True, status
    assert status.get("communication_timing_fault") is False, status

    with socket.create_connection(("127.0.0.1", 10001), timeout=1.0) as sock:
        # A set-mode request must be rejected by the commissioning gate, but it
        # cannot energize the motor even if the gate is defective.
        sock.sendall(b'{"cmd":"set_mode","mode":"position","device":"mctivity"}\n')
        response = b""
        while not response.endswith(b"\n"):
            chunk = sock.recv(4096)
            if not chunk:
                break
            response += chunk
    reply = json.loads(response.decode("utf-8"))
    assert reply == {"ok": False, "error": "commissioning_inhibit"}, reply

    with socket.create_connection(("127.0.0.1", 10001), timeout=1.0) as sock:
        # The acknowledgement itself is non-energizing, but deployment with
        # inhibit active must not retain a phase-search confirmation either.
        sock.sendall(b'{"cmd":"confirm_phase_search_complete","device":"mctivity"}\n')
        response = b""
        while not response.endswith(b"\n"):
            chunk = sock.recv(4096)
            if not chunk:
                break
            response += chunk
    reply = json.loads(response.decode("utf-8"))
    assert reply == {"ok": False, "error": "commissioning_inhibit"}, reply

    with socket.create_connection(("127.0.0.1", 10001), timeout=1.0) as sock:
        sock.sendall(b'{"cmd":"status","device":"mctivity"}\n')
        response = b""
        while not response.endswith(b"\n"):
            chunk = sock.recv(4096)
            if not chunk:
                break
            response += chunk
    post_status = json.loads(response.decode("utf-8")).get("status") or {}
    assert post_status.get("phase_search_confirmation_required") is True, post_status
    assert post_status.get("phase_search_confirmed") is False, post_status
    assert post_status.get("enabled") is False, post_status
    assert post_status.get("servo_request") is False, post_status
    assert post_status.get("fault") is False, post_status
    assert post_status.get("cw") == 0, post_status
    assert post_status.get("communication_timing_fault") is False, post_status
    print("axis D no-motion gate ok")
PY

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
