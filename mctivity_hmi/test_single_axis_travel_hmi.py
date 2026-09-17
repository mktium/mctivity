#!/usr/bin/env python3
import os
import unittest
from unittest import mock


os.environ["MCTIVITY_PROFILE"] = "axis-d-uservo"
os.environ["MCTIVITY_COMMISSIONING_INHIBIT"] = "1"

import mctivity_hmi  # noqa: E402


class SingleAxisTravelHmiTests(unittest.TestCase):
    def test_capability_manifest_exposes_read_only_travel_design(self):
        manifest = mctivity_hmi.capability_manifest()
        self.assertEqual(
            manifest["linear_travel_control"],
            {
                "available": True,
                "device": "mctivity",
                "logical_axis": "D",
                "counts_per_rev": 10000,
                "calibration_actions_available": False,
                "calibration_actions_reason": "runtime_not_connected",
                "calibration_engine": "endpoint_contact_decision_v1",
                "calibration_runtime_connected": False,
                "native_homing_candidate": {
                    "validated": False,
                    "mode_code": 6,
                    "method_object": "0x6098",
                    "stall_current_object": "0x3637",
                    "timeout_object": "0x3643",
                    "controlword_start_bit": 4,
                    "statusword_attained_bit": 12,
                    "statusword_error_bit": 13,
                },
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
                },
            },
        ) as command:
            result = mctivity_hmi.travel_status("mctivity")
        self.assertTrue(result["ok"])
        self.assertFalse(result["travel"]["endpoints_valid"])
        self.assertEqual(result["travel"]["calibration_state"], "uncalibrated")
        self.assertFalse(result["travel"]["calibration_actions_available"])
        self.assertEqual(result["travel"]["calibration_engine"], "endpoint_contact_decision_v1")
        self.assertFalse(result["travel"]["calibration_engine_available"])
        command.assert_called_once_with({"cmd": "status", "device": "mctivity"})

    def test_travel_status_rejects_other_device(self):
        result = mctivity_hmi.travel_status("mctivity_e")
        self.assertEqual(result, {"ok": False, "error": "linear_travel_unavailable", "device": "mctivity_e"})

    def test_ui_state_normalizes_travel_without_action_fields(self):
        normalized = mctivity_hmi._normalize_ui_device_state(
            {
                "travel": {
                    "left_limit_counts": -1000,
                    "right_limit_counts": 5000,
                    "safety_margin_counts": 100,
                    "calibration_state": "both_valid",
                    "anti_sway_enabled": True,
                    "sway_period_ms": 900,
                    "unexpected_command": "enable",
                }
            }
        )
        self.assertEqual(normalized["travel"]["left_limit_counts"], -1000)
        self.assertTrue(normalized["travel"]["anti_sway_enabled"])
        self.assertNotIn("unexpected_command", normalized["travel"])

    def test_rendered_ui_contains_read_only_linear_travel_panel(self):
        html = mctivity_hmi.HTML
        self.assertIn('id="linearTravelCard"', html)
        self.assertIn("行程与防摇", html)
        self.assertIn("/api/travel?device=", html)
        self.assertIn("端点尚未有效；当前页面只读显示", html)
        self.assertIn("@media (max-height: 820px)", html)
        self.assertIn("overflow:hidden", html)
        self.assertNotIn("__LINEAR_TRAVEL_AVAILABLE__", html)


if __name__ == "__main__":
    unittest.main()
