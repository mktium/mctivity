#!/usr/bin/env python3
import unittest

from travel_calibration import (
    CalibrationConfig,
    CalibrationDirection,
    CalibrationSample,
    CalibrationState,
    EndpointCalibration,
    estimate_baseline,
)


def sample(
    now_ms,
    position_counts=0,
    velocity_counts_s=0,
    current_feedback=10,
    *,
    operational=True,
    wc_complete=True,
    fault=False,
    enabled=True,
):
    return CalibrationSample(
        now_ms=now_ms,
        position_counts=position_counts,
        velocity_counts_s=velocity_counts_s,
        current_feedback=current_feedback,
        operational=operational,
        wc_complete=wc_complete,
        fault=fault,
        enabled=enabled,
    )


class TravelCalibrationTests(unittest.TestCase):
    def test_baseline_uses_recent_median(self):
        self.assertEqual(estimate_baseline([10, 11, 10, 80, 9]), 10)
        self.assertIsNone(estimate_baseline([]))

    def test_inhibit_fails_before_arm(self):
        flow = EndpointCalibration()
        decision = flow.arm(
            CalibrationDirection.LEFT,
            sample(0),
            commissioning_inhibit=True,
            baseline_current=10,
        )
        self.assertEqual(decision.state, CalibrationState.FAILED)
        self.assertEqual(decision.failure_reason, "commissioning_inhibit")

    def test_arm_requires_enabled_healthy_axis(self):
        flow = EndpointCalibration()
        decision = flow.arm(
            "right",
            sample(0, enabled=False),
            commissioning_inhibit=False,
            baseline_current=10,
        )
        self.assertEqual(decision.failure_reason, "drive_not_enabled")

    def test_left_and_right_velocity_direction(self):
        for direction, expected in (("left", -500), ("right", 500)):
            flow = EndpointCalibration(CalibrationConfig(approach_speed_counts_s=500))
            self.assertEqual(
                flow.arm(direction, sample(0), commissioning_inhibit=False, baseline_current=10).state,
                CalibrationState.ARMED,
            )
            self.assertEqual(flow.approach(sample(1, position_counts=1)).command_velocity_counts_s, expected)

    def test_contact_requires_current_and_no_progress_hold(self):
        flow = EndpointCalibration(
            CalibrationConfig(
                current_delta_threshold=20,
                current_absolute_limit=1000,
                contact_hold_ms=40,
                min_position_progress_counts=2,
            )
        )
        flow.arm("right", sample(0), commissioning_inhibit=False, baseline_current=10)
        self.assertEqual(flow.approach(sample(1, position_counts=1, current_feedback=40)).state, CalibrationState.APPROACHING_RIGHT)
        self.assertEqual(flow.approach(sample(20, position_counts=1, current_feedback=40)).state, CalibrationState.APPROACHING_RIGHT)
        decision = flow.approach(sample(45, position_counts=1, current_feedback=40))
        self.assertEqual(decision.state, CalibrationState.CONTACT_DETECTED)
        self.assertEqual(decision.contact_position_counts, 1)
        self.assertEqual(flow.complete().state, CalibrationState.COMPLETE)

    def test_current_spike_without_stall_does_not_trigger(self):
        flow = EndpointCalibration(CalibrationConfig(current_delta_threshold=20, contact_hold_ms=10))
        flow.arm("right", sample(0), commissioning_inhibit=False, baseline_current=10)
        decision = flow.approach(sample(20, position_counts=100, current_feedback=80))
        self.assertEqual(decision.state, CalibrationState.APPROACHING_RIGHT)

    def test_timeout_and_fault_fail_closed(self):
        flow = EndpointCalibration(CalibrationConfig(max_duration_ms=10))
        flow.arm("left", sample(0), commissioning_inhibit=False, baseline_current=10)
        timeout = flow.approach(sample(11, position_counts=-1))
        self.assertEqual(timeout.failure_reason, "calibration_timeout")
        faulted = EndpointCalibration()
        faulted.arm("left", sample(0), commissioning_inhibit=False, baseline_current=10)
        decision = faulted.approach(sample(1, fault=True))
        self.assertEqual(decision.failure_reason, "drive_fault")

    def test_cancel_and_stop_are_explicit(self):
        flow = EndpointCalibration()
        flow.arm("left", sample(0), commissioning_inhibit=False, baseline_current=10)
        self.assertEqual(flow.request_stop().state, CalibrationState.STOP_REQUESTED)
        self.assertEqual(flow.cancel().state, CalibrationState.CANCELLED)


if __name__ == "__main__":
    unittest.main()
