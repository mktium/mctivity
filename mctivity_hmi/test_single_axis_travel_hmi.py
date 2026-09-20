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
        self.assertNotIn("soft_zero_raw", normalized["travel"])
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

    def test_shaped_motion_requires_explicit_enabled_config_and_injects_persisted_period(self):
        state = {
            "devices": {
                "mctivity": {
                    "travel": {
                        "left_limit_counts": 5000,
                        "right_limit_counts": -1000,
                        "safety_margin_counts": 100,
                        "calibration_data_version": 1,
                        "calibration_state": "both_valid",
                        "anti_sway_enabled": False,
                        "sway_period_ms": 900,
                        "shaper": "zvd",
                    }
                }
            }
        }
        with mock.patch.object(mctivity_hmi, "load_ui_state", return_value=state), mock.patch.object(
            mctivity_hmi,
            "motiond_command",
            return_value={"ok": True, "status": {"pos": 0}},
        ):
            clean = {"cmd": "move_shaped_abs", "pos": 200, "speed_rpm": 30, "acceleration_rpm_s": 300}
            self.assertEqual(mctivity_hmi._travel_command_guard(clean, "mctivity"), "anti_sway_not_enabled")
            state["devices"]["mctivity"]["travel"]["anti_sway_enabled"] = True
            clean = {"cmd": "move_shaped_abs", "pos": 200, "speed_rpm": 30, "acceleration_rpm_s": 300}
            self.assertIsNone(mctivity_hmi._travel_command_guard(clean, "mctivity"))
            self.assertEqual(clean["shaper_period_ms"], 900)
            self.assertEqual(clean["shaper_damping_permille"], 50)

    def test_travel_zero_is_restored_only_when_axis_is_stopped_and_disabled(self):
        status = {
            "device": "mctivity",
            "pos": 238891,
            "target": 238891,
            "soft_zero_raw": 0,
            "commissioning_inhibit": True,
            "operational": True,
            "wc_complete": True,
            "fault": False,
            "enabled": False,
            "servo_request": False,
            "moving": False,
        }
        restored = dict(status, pos=-7337, target=-7337, soft_zero_raw=246228)
        with mock.patch.object(
            mctivity_hmi,
            "motiond_command",
            side_effect=[{"ok": True, "status": restored}],
        ) as command:
            result = mctivity_hmi._reconcile_travel_zero(
                "mctivity",
                status,
                {"calibration_state": "both_valid", "soft_zero_raw": 246228},
            )
        self.assertEqual(result["soft_zero_raw"], 246228)
        command.assert_called_once_with({"cmd": "restore_zero_raw", "device": "mctivity", "raw_zero": 246228})

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
        self.assertIn("move_shaped_abs", html)
        self.assertIn("updateTravelSwayConfig", html)
        self.assertIn("toggleTravelAntiSway", html)
        self.assertIn('class="travel-tuning"', html)
        self.assertIn('id="travelAntiSwayToggle"', html)
        self.assertIn('class="rate-sliders"', html)
        self.assertIn('id="axisMinPosition"', html)
        self.assertIn('id="targetPositionBig"', html)
        self.assertIn("const PRIMARY_AXIS_LINEAR_MM_PER_REV = 40.0;", html)
        self.assertIn("linearPositionText", html)
        self.assertIn("@media (max-height: 820px)", html)
        self.assertIn("overflow:hidden", html)
        self.assertNotIn("__LINEAR_TRAVEL_AVAILABLE__", html)


if __name__ == "__main__":
    unittest.main()
