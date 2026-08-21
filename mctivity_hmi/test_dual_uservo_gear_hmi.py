#!/usr/bin/env python3
import os
import unittest


os.environ["MCTIVITY_PROFILE"] = "axis-de-uservo-gear"
os.environ["MCTIVITY_COMMISSIONING_INHIBIT"] = "1"

import mctivity_hmi  # noqa: E402


class DualUservoGearHmiTests(unittest.TestCase):
    def test_manifest_exposes_csp_gear_without_pv_sync(self):
        manifest = mctivity_hmi.capability_manifest()
        self.assertEqual(manifest["profile"], "axis-de-uservo-gear")
        self.assertEqual(manifest["device_order"], ["mctivity", "mctivity_e"])
        self.assertEqual(manifest["electronic_gear_control"]["default_master"], "mctivity")
        self.assertEqual(manifest["electronic_gear_control"]["default_slave"], "mctivity_e")
        self.assertTrue(manifest["electronic_gear_control"]["available"])
        self.assertFalse(manifest["sync_velocity_control"]["available"])
        self.assertIn("axis.mode.gear_cam.execute", manifest["capabilities"])
        for device in manifest["axis_devices"]:
            self.assertEqual(device["ethercat_mode"], "csp")
            self.assertEqual(device["ethercat_mode_code"], 8)
            self.assertEqual(device["rxpdo_profile"], "0x1600")
            self.assertEqual(device["txpdo_profile"], "0x1A00")

    def test_gear_master_is_real_peer_and_direction_is_normalized(self):
        self.assertEqual(
            mctivity_hmi._sanitize_command_payload(
                {"cmd": "gear_config", "master": "mctivity", "master_ratio": 1, "slave_ratio": 1, "direction": "reverse"},
                "mctivity_e",
            ),
            {"cmd": "gear_config", "device": "mctivity_e", "master": "mctivity", "master_ratio": 1, "slave_ratio": 1, "direction": -1},
        )
        self.assertIsNone(
            mctivity_hmi._sanitize_command_payload(
                {"cmd": "gear_config", "master": "virtual", "master_ratio": 1, "slave_ratio": 1},
                "mctivity_e",
            )
        )
        self.assertIsNone(
            mctivity_hmi._sanitize_command_payload(
                {"cmd": "gear_config", "master": "mctivity_e", "master_ratio": 1, "slave_ratio": 1},
                "mctivity_e",
            )
        )
        self.assertIsNone(
            mctivity_hmi._sanitize_command_payload(
                {"cmd": "gear_config", "master": "mctivity", "master_ratio": 1, "slave_ratio": 1, "direction": 0},
                "mctivity_e",
            )
        )

    def test_rendered_gear_ui_has_dynamic_peer_and_inhibit_guard(self):
        html = mctivity_hmi.HTML
        self.assertIn('id="gearDirectionSelect"', html)
        self.assertIn("gearMasterSelect.replaceChildren()", html)
        self.assertIn("if (capabilityState.commissioningInhibit)", html)
        self.assertIn("gear_group_safety_latched", html)
        self.assertIn("gear_position_error", html)
        self.assertNotIn('value="virtual"', html)


if __name__ == "__main__":
    unittest.main()
