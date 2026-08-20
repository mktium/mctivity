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
status_e_json=""
if CAPABILITIES_JSON="$capabilities" python3 -c 'import json, os, sys; sys.exit(0 if json.loads(os.environ["CAPABILITIES_JSON"]).get("profile") == "axis-de-uservo-pv" else 1)'; then
  status_e_json="$(curl -fsS "${URL}/api/status?device=mctivity_e")"
  echo "mctivity_e status ok"
fi
if CAPABILITIES_JSON="$capabilities" python3 -c 'import json, os, sys; sys.exit(0 if "axis.device.fv3.access" in json.loads(os.environ["CAPABILITIES_JSON"]).get("capabilities", []) else 1)'; then
  curl -fsS "${URL}/api/status?device=fv3" >/dev/null && echo "fv3 status ok"
fi
curl -fsS "${URL}/api/health/modular" >/dev/null && echo "modular health ok"

CAPABILITIES_JSON="$capabilities" STATUS_JSON="$status_json" STATUS_E_JSON="$status_e_json" EXPECTED_PROFILE="$EXPECTED_PROFILE" python3 <<'PY'
import json
import os

capabilities = json.loads(os.environ["CAPABILITIES_JSON"])
status_response = json.loads(os.environ["STATUS_JSON"])
expected_profile = os.environ.get("EXPECTED_PROFILE", "").strip()
profile = capabilities.get("profile")

if expected_profile and profile != expected_profile:
    raise SystemExit(f"profile mismatch: expected {expected_profile}, got {profile}")

if profile in {"axis-d-uservo", "axis-d-uservo-pv", "axis-de-uservo-pv"}:
    expected_topology = profile
    assert capabilities.get("primary_axis_label") == "D", capabilities
    assert capabilities.get("counts_per_rev") == 10000, capabilities
    assert capabilities.get("commissioning_inhibit") is True, capabilities
    axis_devices = capabilities.get("axis_devices") or []
    expected_instances = ([('D', 'mctivity', 0), ('E', 'mctivity_e', 1)]
                          if profile == "axis-de-uservo-pv" else
                          [('D', 'mctivity', 0)])
    actual_instances = [
        (item.get("logical_axis"), item.get("transport_device"), item.get("physical_position"))
        for item in axis_devices
    ]
    assert actual_instances == expected_instances, capabilities
    assert all(item.get("topology") == expected_topology for item in axis_devices), capabilities

    statuses = [status_response.get("status") or {}]
    if profile == "axis-de-uservo-pv":
        statuses.append((json.loads(os.environ["STATUS_E_JSON"]) or {}).get("status") or {})
    for expected_device, status in zip(expected_instances, statuses):
        assert status.get("device") == expected_device[1], status
        assert status.get("topology") == expected_topology, status
        assert status.get("counts_per_rev") == 10000, status
        assert status.get("commissioning_inhibit") is True, status
        assert status.get("phase_search_confirmation_required") is False, status
        assert status.get("operational") == 1, status
        assert status.get("wc_complete") is True, status
        assert status.get("enabled") is False, status
        assert status.get("servo_request") is False, status
        assert status.get("moving") is False, status
        assert status.get("fault") is False, status
        assert status.get("err") == 0, status
        assert status.get("cw") == 0, status
        assert status.get("rt_memory_locked") is True, status
        assert status.get("rt_scheduler_policy") == 1, status
        assert int(status.get("rt_scheduler_priority", 0)) > 0, status
        assert status.get("timing_guard_armed") is True, status
        assert status.get("communication_timing_fault") is False, status
        assert status.get("rt_deadline_miss_count") == 0, status
        assert status.get("rt_skipped_periods") == 0, status
    if profile in {"axis-d-uservo-pv", "axis-de-uservo-pv"}:
        assert all(status.get("control_mode") == "velocity" for status in statuses), statuses
        modules = set(capabilities.get("active_features") or [])
        caps = set(capabilities.get("capabilities") or [])
        assert {"feature-logic-velocity", "feature-hmi-velocity"} <= modules, capabilities
        assert "axis.mode.velocity.execute" in caps, capabilities
        assert capabilities.get("mode_capability_map", {}).get("velocity") == "axis.mode.velocity.execute", capabilities
        assert capabilities.get("mode_hmi_module_map", {}).get("velocity") == "feature-hmi-velocity", capabilities
        for device in axis_devices:
            assert device.get("default_velocity_counts_s") == 37000, device
            assert device.get("max_velocity_counts_s") == 166500, device
            assert device.get("stop_decel_counts_s2") == 370333, device
    print("Uservo axis read-only no-motion state ok")
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
