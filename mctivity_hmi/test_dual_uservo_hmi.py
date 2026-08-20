#!/usr/bin/env python3
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


os.environ["MCTIVITY_PROFILE"] = "axis-de-uservo-pv"
os.environ["MCTIVITY_COMMISSIONING_INHIBIT"] = "1"

import mctivity_hmi  # noqa: E402


ROOT = Path(__file__).resolve().parent.parent


class DualUservoHmiTests(unittest.TestCase):
    def test_capabilities_and_axis_configs_are_device_keyed(self):
        manifest = mctivity_hmi.capability_manifest()
        self.assertEqual(manifest["device_order"], ["mctivity", "mctivity_e"])
        self.assertEqual(manifest["device_axis_map"], {"mctivity": "D", "mctivity_e": "E"})
        self.assertNotIn("axis.device.fv3.access", manifest["capabilities"])
        self.assertEqual(set(mctivity_hmi._AXIS_DEVICE_BY_KEY), {"mctivity", "mctivity_e"})
        for key in manifest["device_order"]:
            with self.subTest(device=key):
                config = mctivity_hmi._HMI_AXIS_CONFIG_BY_DEVICE[key]
                self.assertEqual(config["counts_per_rev"], 10000)
                self.assertEqual(config["default_speed_rpm"], 222)
                self.assertEqual(config["max_speed_rpm"], 999)
                self.assertEqual(config["default_accel_rpm_s"], 2222)
                self.assertEqual(config["stop_decel_rpm_s"], 2222)

    def test_e_commands_keep_the_device_key(self):
        with mock.patch.object(mctivity_hmi, "motiond_command", return_value={"ok": True}) as command:
            result = mctivity_hmi._transport_command(
                "mctivity_e", {"cmd": "jog_velocity", "velocity": 37000}
            )
        self.assertEqual(result, {"ok": True})
        command.assert_called_once_with(
            {"cmd": "jog_velocity", "velocity": 37000, "device": "mctivity_e"}
        )
        self.assertEqual(mctivity_hmi._normalize_device("mctivity_e"), "mctivity_e")
        self.assertIsNone(mctivity_hmi._normalize_device("fv3"))

    def test_ui_state_round_trip_keeps_d_and_e_independent(self):
        with tempfile.TemporaryDirectory() as directory:
            state_path = str(Path(directory) / "state.json")
            with mock.patch.object(mctivity_hmi, "UI_STATE_PATH", state_path):
                mctivity_hmi.save_ui_state("mctivity", {"velRpm": 111, "mode": "velocity"})
                mctivity_hmi.save_ui_state("mctivity_e", {"velRpm": 333, "mode": "velocity"})
                state = mctivity_hmi.load_ui_state()
        self.assertEqual(state["devices"]["mctivity"]["velRpm"], 111)
        self.assertEqual(state["devices"]["mctivity_e"]["velRpm"], 333)

    def test_poweroff_status_gate_reads_both_uservo_axes(self):
        def status(payload):
            return {
                "ok": True,
                "status": {"device": payload["device"], "moving": False, "gear_running": False},
            }

        with mock.patch.object(mctivity_hmi, "motiond_command", side_effect=status) as command:
            machine, error = mctivity_hmi._read_poweroff_device_statuses()
        self.assertIsNone(error)
        self.assertEqual([item["device"] for item in machine["statuses"]], ["mctivity", "mctivity_e"])
        self.assertEqual(
            [call.args[0] for call in command.call_args_list],
            [
                {"cmd": "status", "device": "mctivity"},
                {"cmd": "status", "device": "mctivity_e"},
            ],
        )

    def test_rendered_ui_uses_dynamic_routes_and_separate_timers(self):
        html = mctivity_hmi.HTML
        self.assertIn('"mctivity":{"device_key":"mctivity","logical_axis":"D"', html)
        self.assertIn('"mctivity_e":{"device_key":"mctivity_e","logical_axis":"E"', html)
        self.assertIn("const velocitySliderCommandTimerByDevice = {};", html)
        self.assertIn("deviceProfiles[device] = newDeviceProfile(device);", html)
        self.assertIn("const reportedDevice = String(s && s.device || activeDevice).toLowerCase();", html)
        self.assertIn("body: JSON.stringify({device, state: currentProfile(device)})", html)
        self.assertIn("apiForDevice(device, {cmd:'jog_velocity'", html)

    def test_full_profile_keeps_legacy_devices(self):
        code = """
import json, sys
sys.path.insert(0, 'mctivity_hmi')
import mctivity_hmi as h
print(json.dumps({'order': h._HMI_DEVICE_ORDER, 'axes': h._AXIS_DEVICES}))
"""
        env = dict(os.environ, MCTIVITY_PROFILE="full", PYTHONDONTWRITEBYTECODE="1")
        output = subprocess.check_output(
            [sys.executable, "-c", code], cwd=ROOT, env=env, text=True
        )
        result = json.loads(output)
        self.assertEqual(result["order"], ["mctivity", "fv3"])
        self.assertEqual(result["axes"], [])


if __name__ == "__main__":
    unittest.main()
