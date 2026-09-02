import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load_manifest(profile, profile_path=None, aux_encoder=True):
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT / "mctivity_hmi")
    env["MCTIVITY_PROFILE"] = profile
    env["MCTIVITY_AUX_ENCODER_ENABLED"] = "1" if aux_encoder else "0"
    if profile_path:
        env["MCTIVITY_PROFILE_PATH"] = str(profile_path)
    script = (
        "import json, mctivity_hmi; "
        "print(json.dumps(mctivity_hmi.capability_manifest(), sort_keys=True))"
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=ROOT,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


class ProfileAssemblyTests(unittest.TestCase):
    def test_packaged_profiles_assemble_without_warnings(self):
        expected_features = {
            "minimal": {"position"},
            "standard": {"position", "incremental", "multi_point", "homing", "velocity"},
            "full": {
                "position",
                "anti_sway_position",
                "incremental",
                "multi_point",
                "homing",
                "velocity",
                "torque",
                "gear_cam",
            },
        }
        for profile, features in expected_features.items():
            with self.subTest(profile=profile):
                manifest = load_manifest(profile)
                self.assertEqual([], manifest["warnings"])
                self.assertEqual(features, set(manifest["enabled_feature_keys"]))
                self.assertIn("axis.control.zero", manifest["capabilities"])
                self.assertIn("axis.control.fault.reset", manifest["capabilities"])
                self.assertNotIn("axis.control.servo_diagnostic.view", manifest["capabilities"])
                self.assertNotIn("feature-hmi-servo-diagnostic", manifest["active_features"])

    def test_dependency_failure_removes_partial_anti_sway_assembly(self):
        profile = {
            "profile": "invalid-anti-sway",
            "domains": ["axis_control_domain"],
            "modules": [
                "axis-feedback-panel",
                "axis-control-panel",
                "feature-logic-single-point",
                "feature-hmi-single-point",
                "feature-logic-anti-sway-position",
                "feature-hmi-anti-sway-position",
                "ui-state-persist",
            ],
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "profile.json"
            path.write_text(json.dumps(profile), encoding="utf-8")
            manifest = load_manifest("invalid-anti-sway", path)

        self.assertNotIn("anti_sway_position", manifest["enabled_feature_keys"])
        self.assertNotIn("feature-logic-anti-sway-position", manifest["active_features"])
        self.assertNotIn("feature-hmi-anti-sway-position", manifest["active_features"])
        self.assertTrue(
            any(
                warning.startswith(
                    "module_missing_requirement:feature-logic-anti-sway-position:feature-logic-dual-axis-fv3"
                )
                for warning in manifest["warnings"]
            )
        )


if __name__ == "__main__":
    unittest.main()
