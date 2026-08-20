#!/usr/bin/env python3
import os
import unittest
from pathlib import Path


os.environ["MCTIVITY_PROFILE"] = "axis-d-uservo-pv"
os.environ["MCTIVITY_COMMISSIONING_INHIBIT"] = "1"

import mctivity_hmi  # noqa: E402


class VelocityProfileHmiTests(unittest.TestCase):
    def test_rendered_axis_d_velocity_parameters(self):
        html = mctivity_hmi.HTML
        self.assertIn("const PRIMARY_AXIS_LABEL = 'D';", html)
        self.assertIn('id="velRpm" type="range" min="1" max="999" step="1" value="222"', html)
        self.assertIn('oninput="handleVelocitySliderInput()"', html)
        self.assertIn('id="cfgAccel" type="number" value="2222"', html)
        self.assertIn('加速度 <strong id="velocityAccelText">2222 rpm/s</strong>', html)
        self.assertIn('id="communicationAlarm"', html)
        self.assertIn("stopDecelRpmS:2222", html)
        self.assertIn("syncModePanels('velocity');", html)

    def test_velocity_start_precedes_position_fallback(self):
        source = Path(mctivity_hmi.__file__).read_text(encoding="utf-8")
        velocity_branch = source.index("modeSelect.value === 'velocity'")
        position_fallback = source.index("modeSelect.value !== 'position'", velocity_branch)
        self.assertLess(velocity_branch, position_fallback)
        self.assertIn("currentProfile().stopDecelRpmS", source)
        self.assertIn("const velocity = Number(velRpm.value);", source)
        self.assertNotIn("const velocity = PRIMARY_AXIS_DEFAULT_VELOCITY_RPM;", source)
        self.assertIn("function handleVelocitySliderInput()", source)
        self.assertIn("status.enabled && status.control_mode === 'velocity' && targetCps !== 0", source)
        self.assertIn("liveStatus.enabled && liveStatus.control_mode === 'velocity' && liveTargetCps !== 0", source)
        self.assertIn("const direction = liveTargetCps < 0 ? -1 : 1;", source)
        self.assertIn("rpmToCountsS(direction * requestedRpm)", source)
        self.assertIn("}, 150);", source)
        self.assertIn("function jogVelocity(v) { return api({cmd:'jog_velocity', velocity:rpmToCountsS(v)}); }", source)
        self.assertIn("const timingFault = Boolean(s.communication_timing_fault);", source)
        self.assertIn("timingFault ? text.timingFault", source)
        self.assertIn("const visibleWarningCount = warningList.length + (timingFault ? 1 : 0);", source)

    def test_deployment_verifier_has_no_control_requests(self):
        source = (Path(mctivity_hmi.__file__).parent.parent / "scripts" / "mctivity-kiosk-verify.sh").read_text(
            encoding="utf-8"
        )
        for command in ("set_mode", "confirm_phase_search_complete", "fault_reset", "reset_fault", "jog_velocity"):
            self.assertNotIn(command, source)
        self.assertNotIn("sock.sendall", source)
        self.assertNotIn("/api/command", source)


if __name__ == "__main__":
    unittest.main()
