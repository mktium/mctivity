#!/usr/bin/env python3
"""Pure single-axis endpoint-contact calibration state machine.

This module intentionally has no EtherCAT, socket, or subprocess code.  It
defines the deterministic decision layer that a future motiond adapter can
drive with cyclic samples.  A caller must still enforce the commissioning
inhibit and the physical authorization boundary before sending any command.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum
from statistics import median
from typing import Iterable


class CalibrationState(str, Enum):
    IDLE = "idle"
    ARMED = "armed"
    APPROACHING_LEFT = "approaching_left"
    APPROACHING_RIGHT = "approaching_right"
    CONTACT_DETECTED = "contact_detected"
    STOP_REQUESTED = "stop_requested"
    COMPLETE = "complete"
    FAILED = "failed"
    CANCELLED = "cancelled"


class CalibrationDirection(str, Enum):
    LEFT = "left"
    RIGHT = "right"

    @property
    def sign(self) -> int:
        return -1 if self is CalibrationDirection.LEFT else 1


@dataclass(frozen=True)
class CalibrationConfig:
    approach_speed_counts_s: int = 500
    max_duration_ms: int = 30_000
    current_delta_threshold: int = 100
    current_absolute_limit: int = 1_000
    contact_hold_ms: int = 40
    min_position_progress_counts: int = 2
    rollback_counts: int = 200

    def __post_init__(self) -> None:
        if self.approach_speed_counts_s <= 0:
            raise ValueError("approach_speed_counts must be positive")
        if self.max_duration_ms <= 0 or self.contact_hold_ms <= 0:
            raise ValueError("calibration timeouts must be positive")
        if self.current_delta_threshold <= 0 or self.current_absolute_limit <= 0:
            raise ValueError("current thresholds must be positive")
        if self.min_position_progress_counts < 0 or self.rollback_counts < 0:
            raise ValueError("position limits must be non-negative")


@dataclass(frozen=True)
class CalibrationSample:
    now_ms: int
    position_counts: int
    velocity_counts_s: int
    current_feedback: int | None
    operational: bool
    wc_complete: bool
    fault: bool
    enabled: bool


@dataclass(frozen=True)
class CalibrationDecision:
    state: CalibrationState
    command_velocity_counts_s: int = 0
    contact_position_counts: int | None = None
    failure_reason: str | None = None
    contact_current: int | None = None


@dataclass(frozen=True)
class CalibrationResult:
    state: CalibrationState = CalibrationState.IDLE
    direction: CalibrationDirection | None = None
    baseline_current: int | None = None
    contact_position_counts: int | None = None
    contact_current: int | None = None
    failure_reason: str | None = None
    started_at_ms: int | None = None


def estimate_baseline(samples: Iterable[int], *, max_samples: int = 32) -> int | None:
    """Return a robust baseline from the most recent current samples."""

    values = [int(value) for value in samples if value is not None]
    if not values:
        return None
    return int(round(median(values[-max_samples:])))


class EndpointCalibration:
    """Cyclic decision state for one endpoint, with fail-closed behavior."""

    def __init__(self, config: CalibrationConfig | None = None) -> None:
        self.config = config or CalibrationConfig()
        self.result = CalibrationResult()
        self._contact_since_ms: int | None = None
        self._last_position: int | None = None

    @property
    def active(self) -> bool:
        return self.result.state in {
            CalibrationState.ARMED,
            CalibrationState.APPROACHING_LEFT,
            CalibrationState.APPROACHING_RIGHT,
            CalibrationState.CONTACT_DETECTED,
            CalibrationState.STOP_REQUESTED,
        }

    def arm(
        self,
        direction: CalibrationDirection | str,
        sample: CalibrationSample,
        *,
        commissioning_inhibit: bool,
        baseline_current: int | None,
    ) -> CalibrationDecision:
        """Arm only; the caller decides when an authorized motion command may run."""

        if commissioning_inhibit:
            return self._fail("commissioning_inhibit")
        if not sample.operational or not sample.wc_complete:
            return self._fail("communication_not_ready")
        if sample.fault:
            return self._fail("drive_fault")
        if not sample.enabled:
            return self._fail("drive_not_enabled")
        try:
            chosen = CalibrationDirection(direction)
        except ValueError:
            return self._fail("invalid_direction")
        self.result = CalibrationResult(
            state=CalibrationState.ARMED,
            direction=chosen,
            baseline_current=baseline_current,
            started_at_ms=sample.now_ms,
        )
        self._contact_since_ms = None
        self._last_position = sample.position_counts
        return CalibrationDecision(CalibrationState.ARMED)

    def approach(self, sample: CalibrationSample) -> CalibrationDecision:
        """Advance one sample and return the velocity request for the adapter."""

        if not self.active or self.result.direction is None:
            return CalibrationDecision(self.result.state, failure_reason="not_active")
        if not sample.operational or not sample.wc_complete:
            return self._fail("communication_not_ready")
        if sample.fault:
            return self._fail("drive_fault")
        if not sample.enabled:
            return self._fail("drive_disabled")
        assert self.result.started_at_ms is not None
        if sample.now_ms - self.result.started_at_ms > self.config.max_duration_ms:
            return self._fail("calibration_timeout")
        if self.result.state is CalibrationState.ARMED:
            self.result = replace(
                self.result,
                state=(
                    CalibrationState.APPROACHING_LEFT
                    if self.result.direction is CalibrationDirection.LEFT
                    else CalibrationState.APPROACHING_RIGHT
                ),
            )
        if self.result.state in {
            CalibrationState.CONTACT_DETECTED,
            CalibrationState.STOP_REQUESTED,
        }:
            return CalibrationDecision(
                self.result.state,
                contact_position_counts=self.result.contact_position_counts,
                contact_current=self.result.contact_current,
            )

        if self._contact_detected(sample):
            self.result = replace(
                self.result,
                state=CalibrationState.CONTACT_DETECTED,
                contact_position_counts=sample.position_counts,
                contact_current=sample.current_feedback,
            )
            return CalibrationDecision(
                CalibrationState.CONTACT_DETECTED,
                contact_position_counts=sample.position_counts,
                contact_current=sample.current_feedback,
            )
        self._last_position = sample.position_counts
        return CalibrationDecision(
            self.result.state,
            command_velocity_counts_s=self.result.direction.sign * self.config.approach_speed_counts_s,
        )

    def request_stop(self) -> CalibrationDecision:
        if not self.active:
            return CalibrationDecision(self.result.state)
        self.result = replace(self.result, state=CalibrationState.STOP_REQUESTED)
        return CalibrationDecision(
            CalibrationState.STOP_REQUESTED,
            contact_position_counts=self.result.contact_position_counts,
            contact_current=self.result.contact_current,
        )

    def complete(self) -> CalibrationDecision:
        if self.result.state not in {CalibrationState.CONTACT_DETECTED, CalibrationState.STOP_REQUESTED}:
            return CalibrationDecision(self.result.state, failure_reason="contact_not_confirmed")
        self.result = replace(self.result, state=CalibrationState.COMPLETE)
        return CalibrationDecision(
            CalibrationState.COMPLETE,
            contact_position_counts=self.result.contact_position_counts,
            contact_current=self.result.contact_current,
        )

    def cancel(self) -> CalibrationDecision:
        self.result = replace(self.result, state=CalibrationState.CANCELLED)
        self._contact_since_ms = None
        return CalibrationDecision(CalibrationState.CANCELLED)

    def _contact_detected(self, sample: CalibrationSample) -> bool:
        baseline = self.result.baseline_current
        current = sample.current_feedback
        if baseline is None or current is None:
            return False
        current_spike = current >= baseline + self.config.current_delta_threshold
        absolute_limit = current >= self.config.current_absolute_limit
        no_progress = (
            self._last_position is not None
            and abs(sample.position_counts - self._last_position) < self.config.min_position_progress_counts
        )
        if not (current_spike or absolute_limit) or not no_progress:
            self._contact_since_ms = None
            return False
        if self._contact_since_ms is None:
            self._contact_since_ms = sample.now_ms
        return sample.now_ms - self._contact_since_ms >= self.config.contact_hold_ms

    def _fail(self, reason: str) -> CalibrationDecision:
        self.result = replace(self.result, state=CalibrationState.FAILED, failure_reason=reason)
        self._contact_since_ms = None
        return CalibrationDecision(CalibrationState.FAILED, failure_reason=reason)
