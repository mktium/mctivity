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
        self.assertIn('"stop_decel_rpm_s":2222', html)
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
        self.assertIn("rpmToCountsS(direction * requestedRpm, device)", source)
        self.assertIn("}, 150);", source)
        self.assertIn("if (syncVelocityEnabled) return syncJogVelocity(v);", source)
        self.assertIn("return api({cmd:'jog_velocity', velocity:rpmToCountsS(v, activeDevice)});", source)
        self.assertIn("const timingFault = Boolean(s.communication_timing_fault);", source)
        self.assertIn("timingFault ? text.timingFault", source)
        self.assertIn("const visibleWarningCount = warningList.length + (timingFault ? 1 : 0);", source)

    def test_deployment_verifier_has_no_control_requests(self):
        source = (Path(mctivity_hmi.__file__).parent.parent / "scripts" / "mctivity-kiosk-verify.sh").read_text(
            encoding="utf-8"
        )
        for control_transport in (
            "sock.sendall",
            "socket.create_connection",
            "/api/command",
            "curl -X POST",
            "curl --request POST",
        ):
            self.assertNotIn(control_transport, source)

    def test_fault_reset_refreshes_status_and_surfaces_outcome(self):
        source = Path(mctivity_hmi.__file__).read_text(encoding="utf-8")
        start = source.index("async function resetFault()")
        end = source.index("function stopMotion()", start)
        reset_source = source[start:end]
        self.assertIn("apiForDevice(device, {cmd:'fault_reset'})", reset_source)
        self.assertIn("await refreshDeviceStatus(device)", reset_source)
        self.assertIn("故障复位失败：", reset_source)
        self.assertIn("复位脉冲已发送，故障仍存在", reset_source)
        self.assertIn("故障已复位", reset_source)
        self.assertNotIn("console.error", reset_source)
        self.assertLess(
            reset_source.index("apiForDevice(device, {cmd:'fault_reset'})"),
            reset_source.index("await refreshDeviceStatus(device)"),
        )


if __name__ == "__main__":
    unittest.main()
