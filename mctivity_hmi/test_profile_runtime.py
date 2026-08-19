#!/usr/bin/env python3
import unittest
from pathlib import Path

from profile_runtime import build_module_runtime, rpm_s_to_counts_s2, rpm_to_counts_s


ROOT = Path(__file__).resolve().parent.parent


class ProfileRuntimeTest(unittest.TestCase):
    def runtime(self, name):
        return build_module_runtime(
            ROOT / "profiles" / f"{name}.json",
            ROOT / "modules",
            strict=True,
        )

    def test_pv_values_are_derived_from_rpm_and_counts_per_rev(self):
        runtime = self.runtime("axis-d-uservo-pv")
        self.assertEqual(len(runtime["axis_devices"]), 1)
        device = runtime["axis_devices"][0]
        counts_per_rev = device["counts_per_rev"]
        self.assertEqual(
            device["default_velocity_counts_s"],
            rpm_to_counts_s(device["default_speed_rpm"], counts_per_rev),
        )
        self.assertEqual(
            device["max_velocity_counts_s"],
            rpm_to_counts_s(device["max_speed_rpm"], counts_per_rev),
        )
        self.assertEqual(
            device["default_accel_counts_s2"],
            rpm_s_to_counts_s2(device["default_accel_rpm_s"], counts_per_rev),
        )
        self.assertEqual(device["default_decel_rpm_s"], device["stop_decel_rpm_s"])

    def test_position_uservo_stays_without_velocity_feature(self):
        runtime = self.runtime("axis-d-uservo")
        self.assertNotIn("feature-logic-velocity", runtime["active_features"])
        self.assertNotIn("feature-hmi-velocity", runtime["active_features"])
        self.assertEqual(runtime["axis_devices"][0]["topology"], "axis-d-uservo")

    def test_legacy_profiles_have_no_axis_device_parameters(self):
        for name in ("minimal", "standard", "full"):
            with self.subTest(profile=name):
                runtime = self.runtime(name)
                self.assertEqual(runtime["profile"], name)
                self.assertEqual(runtime["axis_devices"], [])


if __name__ == "__main__":
    unittest.main()
