#!/usr/bin/env python3
import os
import unittest
from unittest import mock
from pathlib import Path


os.environ["MCTIVITY_PROFILE"] = "axis-d-uservo"
os.environ["MCTIVITY_COMMISSIONING_INHIBIT"] = "1"

import mctivity_hmi  # noqa: E402


class SingleAxisTravelHmiTests(unittest.TestCase):
    def test_capability_manifest_exposes_manual_endpoint_recording(self):
        manifest = mctivity_hmi.capability_manifest()
        self.assertEqual(
            manifest["linear_travel_control"],
            {
                "available": True,
                "device": "mctivity",
                "logical_axis": "D",
                "counts_per_rev": 10000,
                "position_direction": -1,
                "calibration_actions_available": True,
                "calibration_actions_reason": "manual_endpoint_recording",
                "calibration_engine": "manual_endpoint_recording_v1",
                "calibration_runtime_connected": True,
                "native_homing_required": False,
                "anti_sway_shaper": "zvd",
            },
        )

    def test_travel_status_is_read_only_and_defaults_to_uncalibrated(self):
        with mock.patch.object(
            mctivity_hmi,
            "motiond_command",
            return_value={
                "ok": True,
                "status": {
                    "device": "mctivity",
                    "pos": 120,
                    "target": 120,
                    "commissioning_inhibit": True,
                    "operational": True,
                    "wc_complete": True,
                    "fault": False,
                    "enabled": False,
                    "servo_request": False,
                    "moving": False,
                },
            },
        ) as command:
            result = mctivity_hmi.travel_status("mctivity")
        self.assertTrue(result["ok"])
        self.assertFalse(result["travel"]["endpoints_valid"])
        self.assertEqual(result["travel"]["calibration_state"], "uncalibrated")
        self.assertTrue(result["travel"]["calibration_actions_available"])
        self.assertEqual(result["travel"]["calibration_engine"], "manual_endpoint_recording_v1")
        self.assertTrue(result["travel"]["calibration_engine_available"])
        command.assert_called_once_with({"cmd": "status", "device": "mctivity"})

    def test_travel_status_rejects_other_device(self):
        result = mctivity_hmi.travel_status("mctivity_e")
        self.assertEqual(result, {"ok": False, "error": "linear_travel_unavailable", "device": "mctivity_e"})

    def test_ui_state_normalizes_travel_without_command_fields(self):
        normalized = mctivity_hmi._normalize_ui_device_state(
            {
                "travel": {
                    "left_limit_counts": 5000,
                    "right_limit_counts": -1000,
                    "safety_margin_counts": 100,
                    "calibration_state": "both_valid",
                    "anti_sway_enabled": True,
                    "sway_period_ms": 900,
                    "unexpected_command": "enable",
                }
            }
        )
        self.assertEqual(normalized["travel"]["left_limit_counts"], 5000)
        self.assertTrue(normalized["travel"]["anti_sway_enabled"])
        self.assertNotIn("unexpected_command", normalized["travel"])

    def test_motion_guard_rejects_outside_target_and_bounds_jog(self):
        state = {
            "devices": {
                "mctivity": {
                    "travel": {
                        "left_limit_counts": 5000,
                        "right_limit_counts": -1000,
                        "safety_margin_counts": 100,
                        "calibration_data_version": 1,
                        "calibration_state": "both_valid",
                    }
                }
            }
        }
        with mock.patch.object(mctivity_hmi, "load_ui_state", return_value=state), mock.patch.object(
            mctivity_hmi,
            "motiond_command",
            return_value={"ok": True, "status": {"pos": 0}},
        ):
            self.assertEqual(
                mctivity_hmi._travel_command_guard({"cmd": "move_abs", "pos": -901}, "mctivity"),
                "target_outside_safe_travel",
            )
            clean = {"cmd": "jog_velocity", "velocity": 100}
            self.assertIsNone(mctivity_hmi._travel_command_guard(clean, "mctivity"))
            self.assertEqual(clean["min_pos"], -900)
            self.assertEqual(clean["max_pos"], 4900)

    def test_rendered_ui_contains_read_only_linear_travel_panel(self):
        html = mctivity_hmi.HTML
        self.assertIn('id="linearTravelCard"', html)
        self.assertIn("行程与防摇", html)
        self.assertIn("/api/travel?device=", html)
        self.assertIn("记录左端点", html)
        self.assertIn("记录右端点", html)
        self.assertIn("/api/travel/record", html)
        self.assertIn("clearButton.disabled = false", html)
        self.assertIn("clear_calibration_guard", Path(mctivity_hmi.__file__).read_text(encoding="utf-8"))
        self.assertIn("statusAllowsRecording", html)
        self.assertIn("data.recording_available || statusAllowsRecording", html)
        self.assertIn("@media (max-height: 820px)", html)
        self.assertIn("overflow:hidden", html)
        self.assertNotIn("__LINEAR_TRAVEL_AVAILABLE__", html)


if __name__ == "__main__":
    unittest.main()
