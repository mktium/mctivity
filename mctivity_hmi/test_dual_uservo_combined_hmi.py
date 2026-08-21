#!/usr/bin/env python3
import os
import unittest


os.environ["MCTIVITY_PROFILE"] = "axis-de-uservo-combined"
os.environ["MCTIVITY_COMMISSIONING_INHIBIT"] = "1"

import mctivity_hmi  # noqa: E402


class DualUservoCombinedHmiTests(unittest.TestCase):
    def test_manifest_exposes_velocity_and_electronic_gear_together(self):
        manifest = mctivity_hmi.capability_manifest()
        self.assertEqual(manifest["profile"], "axis-de-uservo-combined")
        self.assertEqual(manifest["device_order"], ["mctivity", "mctivity_e"])
        self.assertTrue(manifest["electronic_gear_control"]["available"])
        self.assertIn("axis.mode.velocity.execute", manifest["capabilities"])
        self.assertIn("axis.mode.gear_cam.execute", manifest["capabilities"])
        self.assertFalse(manifest["sync_velocity_control"]["available"])
        self.assertEqual(manifest["active_features"].count("feature-hmi-velocity"), 1)
        self.assertEqual(manifest["active_features"].count("feature-hmi-electronic-gear"), 1)

    def test_rendered_mode_selector_keeps_both_options_assembled(self):
        html = mctivity_hmi.HTML
        self.assertIn('value="velocity"', html)
        self.assertIn('value="gear_cam"', html)
        self.assertIn("modeRequiredHmiModule", html)
        self.assertIn("jog_velocity", html)
        self.assertIn("gearPayload('gear_start')", html)


if __name__ == "__main__":
    unittest.main()
