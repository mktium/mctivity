#!/usr/bin/env python3
import os
import unittest
from unittest import mock


os.environ["MCTIVITY_PROFILE"] = "axis-de-uservo-combined"
os.environ["MCTIVITY_COMMISSIONING_INHIBIT"] = "0"

import mctivity_hmi  # noqa: E402


def status(device, **overrides):
    value = {
        "device": device,
        "enabled": False,
        "servo_request": False,
        "moving": False,
        "gear_running": False,
        "cw": 0,
        "fault": False,
    }
    value.update(overrides)
    return {"ok": True, "status": value}


class MotiondRestartTests(unittest.TestCase):
    def setUp(self):
        self.enabled = mock.patch.object(mctivity_hmi, "SYSTEM_MOTIOND_RESTART_ENABLED", True)
        self.command = mock.patch.object(
            mctivity_hmi,
            "SYSTEM_MOTIOND_RESTART_COMMAND",
            "/usr/bin/sudo -n /usr/bin/systemctl restart mctivity-motiond.service",
        )
        self.enabled.start()
        self.command.start()
        self.addCleanup(self.enabled.stop)
        self.addCleanup(self.command.stop)

    def test_requires_both_axes_safe_and_controlword_zero(self):
        def reply(payload):
            return status(payload["device"], enabled=payload["device"] == "mctivity")

        with mock.patch.object(mctivity_hmi, "motiond_command", side_effect=reply):
            result, code = mctivity_hmi.motiond_restart_request({"confirm": "restart_motiond", "dry_run": True})
        self.assertEqual(code, 409)
        self.assertEqual(result["error"], "motiond_restart_blocked")
        self.assertIn("enabled", result["blocked"][0]["blocked_reasons"])

    def test_blocks_active_gear_session_and_nonzero_controlword(self):
        def reply(payload):
            if payload["device"] == "mctivity":
                return status(payload["device"], gear_running=True)
            return status(payload["device"], cw=1)

        with mock.patch.object(mctivity_hmi, "motiond_command", side_effect=reply):
            result, code = mctivity_hmi.motiond_restart_request({"confirm": "restart_motiond"})
        self.assertEqual(code, 409)
        self.assertEqual(result["error"], "motiond_restart_blocked")
        self.assertEqual(result["blocked"][0]["blocked_reasons"], ["gear_running"])
        self.assertEqual(result["blocked"][1]["blocked_reasons"], ["controlword"])

    def test_dry_run_checks_exact_permission_without_executing_restart(self):
        with mock.patch.object(
            mctivity_hmi, "motiond_command", side_effect=lambda payload: status(payload["device"])
        ), mock.patch.object(
            mctivity_hmi, "_run_poweroff_permission_checks", return_value=(True, [])
        ) as permission, mock.patch.object(mctivity_hmi, "_run_command") as run:
            result, code = mctivity_hmi.motiond_restart_request(
                {"confirm": "restart_motiond", "dry_run": True}
            )
        self.assertEqual(code, 200)
        self.assertTrue(result["ok"])
        self.assertTrue(result["dry_run"])
        self.assertEqual(
            result["trigger_command"],
            "/usr/bin/sudo -n /usr/bin/systemctl restart mctivity-motiond.service",
        )
        permission.assert_called_once_with(
            [[
                "/usr/bin/sudo",
                "-n",
                "/usr/bin/systemctl",
                "restart",
                "mctivity-motiond.service",
            ]]
        )
        run.assert_not_called()

    def test_executes_only_after_safe_gate(self):
        with mock.patch.object(
            mctivity_hmi, "motiond_command", side_effect=lambda payload: status(payload["device"])
        ), mock.patch.object(
            mctivity_hmi, "_run_poweroff_permission_checks", return_value=(True, [])
        ), mock.patch.object(
            mctivity_hmi, "_run_command", return_value=(True, "")
        ) as run:
            result, code = mctivity_hmi.motiond_restart_request({"confirm": "restart_motiond"})
        self.assertEqual(code, 200)
        self.assertEqual(result["status"], "motiond_restart_requested")
        run.assert_called_once()

    def test_disabled_feature_does_not_read_or_execute(self):
        with mock.patch.object(mctivity_hmi, "SYSTEM_MOTIOND_RESTART_ENABLED", False), mock.patch.object(
            mctivity_hmi, "_read_motiond_restart_device_statuses"
        ) as read:
            result, code = mctivity_hmi.motiond_restart_request(
                {"confirm": "restart_motiond", "dry_run": True}
            )
        self.assertEqual(code, 403)
        self.assertEqual(result["error"], "motiond_restart_disabled")
        read.assert_not_called()


if __name__ == "__main__":
    unittest.main()
