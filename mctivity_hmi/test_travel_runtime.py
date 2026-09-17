#!/usr/bin/env python3
import unittest

from travel_runtime import (
    TravelConfigError,
    build_travel_guard,
    normalize_travel_config,
    travel_ui_model,
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

    def test_ui_model_does_not_advertise_actions_before_runtime_exists(self):
        model = travel_ui_model({"pos": 0, "target": 0}, {}, 10000)
        self.assertTrue(model["available"])
        self.assertFalse(model["endpoints_valid"])
        self.assertFalse(model["calibration_actions_available"])
        self.assertEqual(model["calibration_actions_reason"], "runtime_not_connected")


if __name__ == "__main__":
    unittest.main()
