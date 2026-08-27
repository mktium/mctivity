#!/usr/bin/env python3
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
SPEC = importlib.util.spec_from_file_location("motiond_launch", ROOT / "scripts" / "mctivity-motiond-launch.py")
LAUNCH = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(LAUNCH)


class MotiondLaunchTests(unittest.TestCase):
    def test_pv_environment_is_derived(self):
        runtime, device, env = LAUNCH.resolve_launch_environment(
            profile_name="axis-d-uservo-pv",
            environ={"MCTIVITY_COMMISSIONING_INHIBIT": "1"},
        )
        self.assertEqual(runtime["profile"], "axis-d-uservo-pv")
        self.assertEqual(device["default_velocity_counts_s"], 37000)
        self.assertEqual(env["MCTIVITY_PV_MAX_SPEED_RPM"], "999")
        self.assertEqual(env["MCTIVITY_TOPOLOGY"], "axis-d-uservo-pv")

    def test_legacy_profile_has_no_pv_environment(self):
        _, device, env = LAUNCH.resolve_launch_environment(profile_name="full", environ={})
        self.assertIsNone(device)
        self.assertEqual(env["MCTIVITY_TOPOLOGY"], "legacy-dual")
        self.assertFalse(any(key.startswith("MCTIVITY_PV_") for key in env))

    def test_dual_pv_environment_has_independent_d_e_parameters(self):
        runtime, device, env = LAUNCH.resolve_launch_environment(
            profile_name="axis-de-uservo-pv",
            environ={"MCTIVITY_COMMISSIONING_INHIBIT": "1"},
        )
        self.assertEqual(len(runtime["axis_devices"]), 2)
        self.assertEqual(device["logical_axis"], "D")
        self.assertEqual(env["MCTIVITY_TOPOLOGY"], "axis-de-uservo-pv")
        self.assertEqual(env["MCTIVITY_USERVO_AXIS_COUNT"], "2")
        for axis_name in ("D", "E"):
            prefix = f"MCTIVITY_AXIS_{axis_name}"
            self.assertEqual(env[f"{prefix}_COUNTS_PER_REV"], "10000")
            self.assertEqual(env[f"{prefix}_PV_TARGET_SPEED_RPM"], "222")
            self.assertEqual(env[f"{prefix}_PV_MAX_SPEED_RPM"], "999")
            self.assertEqual(env[f"{prefix}_PV_ACCEL_RPM_S"], "2222")
            self.assertEqual(env[f"{prefix}_PV_DECEL_RPM_S"], "2222")
            self.assertEqual(env[f"{prefix}_PV_STOP_DECEL_RPM_S"], "2222")

    def test_combined_profile_exports_pv_parameters_and_gear_limits(self):
        runtime, device, env = LAUNCH.resolve_launch_environment(
            profile_name="axis-de-uservo-combined",
            environ={"MCTIVITY_COMMISSIONING_INHIBIT": "1"},
        )
        self.assertEqual(runtime["profile"], "axis-de-uservo-combined")
        self.assertEqual(len(runtime["axis_devices"]), 2)
        self.assertEqual(device["logical_axis"], "D")
        self.assertEqual(env["MCTIVITY_TOPOLOGY"], "axis-de-uservo-gear")
        self.assertEqual(env["MCTIVITY_PROFILE"], "axis-de-uservo-combined")
        self.assertEqual(env["MCTIVITY_GEAR_FOLLOWING_ERROR_LIMIT_COUNTS"], "200")
        self.assertEqual(env["MCTIVITY_GEAR_MAX_RATIO"], "200")
        self.assertEqual(env["MCTIVITY_AXIS_D_MAX_SPEED_RPM"], "222")
        self.assertEqual(env["MCTIVITY_AXIS_E_MAX_SPEED_RPM"], "222")
        for axis_name in ("D", "E"):
            prefix = f"MCTIVITY_AXIS_{axis_name}"
            self.assertEqual(env[f"{prefix}_PV_TARGET_SPEED_RPM"], "222")
            self.assertEqual(env[f"{prefix}_PV_MAX_SPEED_RPM"], "222")
            self.assertEqual(env[f"{prefix}_PV_ACCEL_RPM_S"], "2222")
            self.assertEqual(env[f"{prefix}_PV_DECEL_RPM_S"], "2222")
            self.assertEqual(env[f"{prefix}_PV_STOP_DECEL_RPM_S"], "2222")

    def test_environment_topology_mismatch_is_rejected(self):
        with self.assertRaises(LAUNCH.ProfileRuntimeError):
            LAUNCH.resolve_launch_environment(
                profile_name="axis-d-uservo-pv",
                environ={"MCTIVITY_TOPOLOGY": "legacy-dual"},
            )

    def test_unknown_device_topology_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            modules = root / "modules"
            module_dir = modules / "axis" / "device" / "unknown"
            module_dir.mkdir(parents=True)
            device = {
                "topology": "unknown-axis",
                "counts_per_rev": 10000,
                "default_speed_rpm": 1,
                "max_speed_rpm": 2,
                "default_accel_rpm_s": 1,
                "max_accel_rpm_s": 2,
                "default_decel_rpm_s": 1,
                "max_decel_rpm_s": 2,
                "stop_decel_rpm_s": 1,
                "velocity_step_counts_s": 1,
            }
            (module_dir / "module.json").write_text(
                json.dumps({"id": "axis-device-unknown", "type": "axis_device", "device": device}),
                encoding="utf-8",
            )
            profile_path = root / "unknown.json"
            profile_path.write_text(
                json.dumps({"profile": "unknown", "modules": ["axis-device-unknown"]}),
                encoding="utf-8",
            )
            with self.assertRaises(LAUNCH.ProfileRuntimeError):
                LAUNCH.resolve_launch_environment(
                    profile_name="unknown",
                    profile_path=profile_path,
                    modules_root=modules,
                    environ={},
                )


if __name__ == "__main__":
    unittest.main()
