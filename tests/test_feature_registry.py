import json
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "mctivity_hmi"))

from feature_dispatch import dispatch_axis_command, feature_key_from_payload


class FeatureRegistryTests(unittest.TestCase):
    def test_base_zero_commands_use_default_transport(self):
        calls = []

        def transport(device, payload):
            calls.append((device, payload))
            return {"ok": True}

        self.assertEqual("default", feature_key_from_payload({"cmd": "set_zero"}))
        self.assertEqual("default", feature_key_from_payload({"cmd": "home"}))
        result = dispatch_axis_command("axis_a", {"cmd": "set_zero"}, transport, enabled_feature_keys=set())
        self.assertTrue(result["ok"])
        self.assertEqual([("axis_a", {"cmd": "set_zero"})], calls)

    def test_terminal_anti_sway_command_is_owned_by_feature(self):
        payload = {"cmd": "terminal_anti_sway_curve_abs"}
        self.assertEqual("anti_sway_position", feature_key_from_payload(payload))

        rejected = dispatch_axis_command("axis_a", payload, lambda *_: {"ok": True}, enabled_feature_keys=set())
        self.assertEqual("feature_not_loaded:anti_sway_position", rejected["error"])

        accepted = dispatch_axis_command(
            "axis_a",
            payload,
            lambda *_: {"ok": True},
            enabled_feature_keys={"anti_sway_position"},
        )
        self.assertTrue(accepted["ok"])

    def test_profiles_keep_anti_sway_with_independent_sensor_capability(self):
        standard = json.loads((ROOT / "profiles" / "standard.json").read_text(encoding="utf-8"))
        full = json.loads((ROOT / "profiles" / "full.json").read_text(encoding="utf-8"))

        self.assertNotIn("feature-logic-anti-sway-position", standard["modules"])
        self.assertNotIn("feature-hmi-anti-sway-position", standard["modules"])
        self.assertIn("feature-logic-anti-sway-position", full["modules"])
        self.assertIn("feature-logic-aux-encoder", full["modules"])


if __name__ == "__main__":
    unittest.main()
