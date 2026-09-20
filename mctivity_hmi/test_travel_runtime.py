#!/usr/bin/env python3
import unittest

from travel_runtime import (
    TravelConfigError,
    build_travel_guard,
    clear_calibration_guard,
    normalize_travel_config,
    travel_ui_model,
    ui_target_bounds,
    endpoint_recording_guard,
    record_manual_endpoint,
    validate_target_counts,
)


class TravelRuntimeTests(unittest.TestCase):
    def valid_config(self, **overrides):
        raw = {
            "left_limit_counts": -1000,
            "right_limit_counts": 5000,
            "safety_margin_counts": 100,
            "calibration_state": "both_valid",
        }
        raw.update(overrides)
        return normalize_travel_config(raw, 10000)

    def test_encoder_scale_and_safe_bounds(self):
        config = self.valid_config()
        self.assertEqual(config.counts_per_rev, 10000)
        self.assertEqual(config.safe_left_counts, -900)
        self.assertEqual(config.safe_right_counts, 4900)
        self.assertTrue(config.endpoints_valid)

    def test_uncalibrated_config_rejects_target_without_clamping(self):
        config = normalize_travel_config({}, 10000)
        self.assertEqual(validate_target_counts(10, config), (False, "travel_not_calibrated"))

    def test_outside_target_is_rejected(self):
        config = self.valid_config()
        self.assertEqual(validate_target_counts(-901, config), (False, "target_outside_safe_travel"))
        self.assertEqual(validate_target_counts(4901, config), (False, "target_outside_safe_travel"))
        self.assertEqual(validate_target_counts(0, config), (True, None))

    def test_ui_target_bounds_follow_reversed_mechanical_direction(self):
        reversed_config = normalize_travel_config(
            {
                "left_limit_counts": 1000,
                "right_limit_counts": -5000,
                "safety_margin_counts": 100,
                "calibration_state": "both_valid",
            },
            10000,
            position_direction=-1,
        )
        self.assertEqual(ui_target_bounds(reversed_config), (-900, 4900))
        model = travel_ui_model(
            {"pos": 0, "target": 0},
            {
                "left_limit_counts": 1000,
                "right_limit_counts": -5000,
                "safety_margin_counts": 100,
                "calibration_state": "both_valid",
            },
            10000,
            position_direction=-1,
        )
        self.assertEqual(model["guard"]["ui_target_bounds"], (-900, 4900))

    def test_invalid_endpoints_and_margin_fail_closed(self):
        with self.assertRaises(TravelConfigError):
            self.valid_config(left_limit_counts=10, right_limit_counts=10)
        with self.assertRaises(TravelConfigError):
            self.valid_config(safety_margin_counts=3000)

    def test_anti_sway_requires_period_and_valid_endpoints(self):
        with self.assertRaises(TravelConfigError):
            self.valid_config(anti_sway_enabled=True)
        config = self.valid_config(anti_sway_enabled=True, sway_period_ms=900)
        self.assertTrue(config.anti_sway_ready)
        configured_but_disabled = self.valid_config(anti_sway_enabled=False, sway_period_ms=900)
        self.assertTrue(configured_but_disabled.anti_sway_ready)
        uncalibrated = normalize_travel_config(
            {"anti_sway_enabled": False, "sway_period_ms": 900}, 10000
        )
        self.assertFalse(uncalibrated.anti_sway_ready)

    def test_guard_lists_all_no_motion_reasons(self):
        config = normalize_travel_config({}, 10000)
        guard = build_travel_guard(
            {
                "pos": 0,
                "target": 0,
                "commissioning_inhibit": True,
                "operational": False,
                "wc_complete": False,
                "fault": True,
            },
            config,
        )
        self.assertFalse(guard["ready"])
        self.assertEqual(
            guard["reasons"],
            ["commissioning_inhibit", "not_operational", "wc_incomplete", "drive_fault", "travel_not_calibrated"],
        )

    def test_ui_model_advertises_manual_recording_without_drive_motion(self):
        model = travel_ui_model({"pos": 0, "target": 0}, {}, 10000)
        self.assertTrue(model["available"])
        self.assertFalse(model["endpoints_valid"])
        self.assertTrue(model["calibration_actions_available"])
        self.assertEqual(model["calibration_actions_reason"], "manual_endpoint_recording")
        self.assertEqual(model["calibration_engine"], "manual_endpoint_recording_v1")

    def test_manual_recording_preserves_first_endpoint_and_validates_order(self):
        raw = {}
        raw = record_manual_endpoint(raw, "left", -1000, 10000)
        self.assertEqual(raw["calibration_state"], "left_valid")
        raw = record_manual_endpoint(raw, "right", 5000, 10000)
        self.assertEqual(raw["calibration_state"], "both_valid")
        self.assertEqual(raw["calibration_data_version"], 1)
        self.assertEqual(normalize_travel_config(raw, 10000).safe_left_counts, -900)
        with self.assertRaises(TravelConfigError):
            record_manual_endpoint(record_manual_endpoint({}, "right", 10, 10000), "left", 20, 10000)

    def test_manual_recording_follows_physical_direction_when_encoder_is_reversed(self):
        raw = record_manual_endpoint({}, "left", 1000, 10000, position_direction=-1)
        self.assertEqual(raw["calibration_state"], "left_valid")
        raw = record_manual_endpoint(raw, "right", -5000, 10000, position_direction=-1)
        config = normalize_travel_config(raw, 10000, position_direction=-1)
        self.assertTrue(config.endpoints_valid)
        self.assertEqual(config.safe_left_counts, 900)
        self.assertEqual(config.safe_right_counts, -4900)
        self.assertEqual(validate_target_counts(0, config), (True, None))
        self.assertEqual(validate_target_counts(1001, config), (False, "target_outside_safe_travel"))

    def test_clear_calibration_guard_requires_disabled_stopped_axis(self):
        ok, reason = clear_calibration_guard({"moving": False, "enabled": False, "servo_request": False})
        self.assertTrue(ok)
        self.assertIsNone(reason)
        ok, reason = clear_calibration_guard({"moving": False, "enabled": True, "servo_request": True})
        self.assertFalse(ok)
        self.assertEqual(reason, "clear_requires_disabled_stopped_axis")

    def test_recording_guard_requires_stopped_enabled_healthy_axis(self):
        ok, reason = endpoint_recording_guard({"operational": True, "wc_complete": True, "enabled": True, "servo_request": True, "moving": False, "fault": False, "pos": 0})
        self.assertTrue(ok)
        self.assertIsNone(reason)
        ok, reason = endpoint_recording_guard({"operational": True, "wc_complete": True, "enabled": True, "servo_request": True, "moving": True, "fault": False, "pos": 0})
        self.assertFalse(ok)
        self.assertEqual(reason, "axis_must_be_stopped")


if __name__ == "__main__":
    unittest.main()
