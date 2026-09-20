#!/usr/bin/env python3
"""Pure validation and presentation helpers for single-axis linear travel.

Endpoint calibration is operator-taught: the HMI records the actual encoder
position while the axis is stopped at the left or right mechanical end.  This
module deliberately has no transport or drive-writing code.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


INT32_MIN = -(2**31)
INT32_MAX = 2**31 - 1
CALIBRATION_STATES = {
    "uncalibrated",
    "left_valid",
    "right_valid",
    "both_valid",
    "running_left",
    "running_right",
    "failed",
}
SHAPER_TYPES = {"zvd"}
CALIBRATION_DATA_VERSION = 1


class TravelConfigError(ValueError):
    """Raised when a persisted or API-provided travel config is unsafe."""


@dataclass(frozen=True)
class TravelConfig:
    counts_per_rev: int
    position_direction: int = 1
    left_limit_counts: int | None = None
    right_limit_counts: int | None = None
    safety_margin_counts: int = 100
    calibration_data_version: int = 0
    calibration_state: str = "uncalibrated"
    anti_sway_enabled: bool = False
    sway_period_ms: int | None = None
    shaper: str = "zvd"
    residual_sway_limit_counts: int = 0

    @property
    def endpoints_valid(self) -> bool:
        return (
            self.calibration_state == "both_valid"
            and self.left_limit_counts is not None
            and self.right_limit_counts is not None
            and self.position_direction * self.left_limit_counts
            < self.position_direction * self.right_limit_counts
        )

    @property
    def safe_left_counts(self) -> int | None:
        if self.left_limit_counts is None:
            return None
        return self.left_limit_counts + self.position_direction * self.safety_margin_counts

    @property
    def safe_right_counts(self) -> int | None:
        if self.right_limit_counts is None:
            return None
        return self.right_limit_counts - self.position_direction * self.safety_margin_counts

    @property
    def anti_sway_ready(self) -> bool:
        return (
            self.anti_sway_enabled
            and self.endpoints_valid
            and self.sway_period_ms is not None
            and self.sway_period_ms > 0
            and self.shaper in SHAPER_TYPES
        )


def _int(value: Any, *, name: str, minimum: int = INT32_MIN, maximum: int = INT32_MAX) -> int:
    if isinstance(value, bool):
        raise TravelConfigError(f"{name} must be an integer")
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise TravelConfigError(f"{name} must be an integer") from exc
    if str(value).strip() != str(number) and not isinstance(value, int):
        raise TravelConfigError(f"{name} must be an integer")
    if number < minimum or number > maximum:
        raise TravelConfigError(f"{name} is out of range")
    return number


def _position_direction(value: Any) -> int:
    try:
        direction = int(value)
    except (TypeError, ValueError) as exc:
        raise TravelConfigError("position_direction must be -1 or 1") from exc
    if direction not in (-1, 1):
        raise TravelConfigError("position_direction must be -1 or 1")
    return direction


def normalize_travel_config(
    raw: Mapping[str, Any] | None,
    counts_per_rev: int,
    position_direction: int = 1,
) -> TravelConfig:
    """Normalize persisted travel UI state without making any drive request."""

    if not isinstance(raw, Mapping):
        raw = {}
    cpr = _int(counts_per_rev, name="counts_per_rev", minimum=1, maximum=INT32_MAX)
    direction = _position_direction(position_direction)
    state = str(raw.get("calibration_state", "uncalibrated")).strip().lower()
    if state not in CALIBRATION_STATES:
        raise TravelConfigError("invalid calibration_state")

    def optional_count(key: str) -> int | None:
        value = raw.get(key)
        if value is None or value == "":
            return None
        return _int(value, name=key)

    left = optional_count("left_limit_counts")
    right = optional_count("right_limit_counts")
    margin = _int(raw.get("safety_margin_counts", 100), name="safety_margin_counts", minimum=0)
    if left is not None and right is not None and direction * left >= direction * right:
        raise TravelConfigError("left_limit_counts must be on the physical left of right_limit_counts")
    if left is not None and right is not None and direction * left + margin >= direction * right - margin:
        raise TravelConfigError("safety margin leaves no usable travel")
    if state == "both_valid" and (left is None or right is None):
        raise TravelConfigError("both_valid requires both endpoints")
    version_raw = raw.get("calibration_data_version")
    if version_raw in (None, ""):
        version = CALIBRATION_DATA_VERSION if left is not None or right is not None else 0
    else:
        version = _int(version_raw, name="calibration_data_version", minimum=0, maximum=CALIBRATION_DATA_VERSION)
    if (left is not None or right is not None) and version != CALIBRATION_DATA_VERSION:
        raise TravelConfigError("endpoint data version is not supported")

    period = raw.get("sway_period_ms")
    period_ms = None if period in (None, "") else _int(period, name="sway_period_ms", minimum=10, maximum=120000)
    shaper = str(raw.get("shaper", "zvd")).strip().lower()
    if shaper not in SHAPER_TYPES:
        raise TravelConfigError("unsupported anti-sway shaper")
    residual = _int(
        raw.get("residual_sway_limit_counts", 0),
        name="residual_sway_limit_counts",
        minimum=0,
        maximum=INT32_MAX,
    )
    anti_sway = raw.get("anti_sway_enabled", False)
    if not isinstance(anti_sway, bool):
        raise TravelConfigError("anti_sway_enabled must be boolean")
    if anti_sway and period_ms is None:
        raise TravelConfigError("anti-sway requires sway_period_ms")

    return TravelConfig(
        counts_per_rev=cpr,
        position_direction=direction,
        left_limit_counts=left,
        right_limit_counts=right,
        safety_margin_counts=margin,
        calibration_state=state,
        calibration_data_version=version,
        anti_sway_enabled=anti_sway,
        sway_period_ms=period_ms,
        shaper=shaper,
        residual_sway_limit_counts=residual,
    )


def validate_target_counts(target_counts: int, config: TravelConfig) -> tuple[bool, str | None]:
    """Reject an out-of-range target; never silently clamp it."""

    try:
        target = _int(target_counts, name="target_counts")
    except TravelConfigError as exc:
        return False, str(exc)
    if not config.endpoints_valid:
        return False, "travel_not_calibrated"
    safe_min = min(config.safe_left_counts, config.safe_right_counts)
    safe_max = max(config.safe_left_counts, config.safe_right_counts)
    if target < safe_min or target > safe_max:
        return False, "target_outside_safe_travel"
    return True, None


def ui_target_bounds(config: TravelConfig) -> tuple[int, int] | None:
    """Return the safe target range in the HMI mechanical-position sign."""

    if not config.endpoints_valid:
        return None
    safe_left = config.safe_left_counts
    safe_right = config.safe_right_counts
    if safe_left is None or safe_right is None:
        return None
    logical_left = config.position_direction * safe_left
    logical_right = config.position_direction * safe_right
    return min(logical_left, logical_right), max(logical_left, logical_right)


def build_travel_guard(status: Mapping[str, Any] | None, config: TravelConfig) -> dict[str, Any]:
    """Build a read-only HMI guard summary from status and travel config."""

    status = status if isinstance(status, Mapping) else {}
    position = status.get("pos")
    target = status.get("target")
    reasons: list[str] = []
    if status.get("commissioning_inhibit"):
        reasons.append("commissioning_inhibit")
    if not status.get("operational", False):
        reasons.append("not_operational")
    if not status.get("wc_complete", False):
        reasons.append("wc_incomplete")
    if status.get("fault"):
        reasons.append("drive_fault")
    if not config.endpoints_valid:
        reasons.append("travel_not_calibrated")
    if position is None:
        reasons.append("position_unavailable")

    position_counts = None if position is None else int(position)
    target_counts = None if target is None else int(target)
    percent = None
    if config.safe_left_counts is not None and config.safe_right_counts is not None and position_counts is not None:
        span = config.safe_right_counts - config.safe_left_counts
        if span != 0:
            percent = max(0.0, min(100.0, (position_counts - config.safe_left_counts) * 100.0 / span))

    safe_min = None
    safe_max = None
    if config.safe_left_counts is not None and config.safe_right_counts is not None:
        safe_min = min(config.safe_left_counts, config.safe_right_counts)
        safe_max = max(config.safe_left_counts, config.safe_right_counts)

    return {
        "ready": not reasons,
        "reasons": reasons,
        "position_counts": position_counts,
        "target_counts": target_counts,
        "left_limit_counts": config.left_limit_counts,
        "right_limit_counts": config.right_limit_counts,
        "safe_left_counts": config.safe_left_counts,
        "safe_right_counts": config.safe_right_counts,
        "safe_min_counts": safe_min,
        "safe_max_counts": safe_max,
        "ui_target_bounds": ui_target_bounds(config),
        "position_percent": percent,
        "anti_sway_enabled": config.anti_sway_enabled,
        "anti_sway_ready": config.anti_sway_ready,
    }


def endpoint_recording_guard(status: Mapping[str, Any] | None) -> tuple[bool, str | None]:
    """Allow a record operation only at a healthy, stopped, enabled axis."""

    status = status if isinstance(status, Mapping) else {}
    if status.get("fault"):
        return False, "drive_fault"
    if not status.get("operational", False):
        return False, "not_operational"
    if not status.get("wc_complete", False):
        return False, "wc_incomplete"
    if not status.get("enabled", False) or not status.get("servo_request", False):
        return False, "axis_must_be_enabled"
    if status.get("moving"):
        return False, "axis_must_be_stopped"
    if status.get("pos") is None:
        return False, "position_unavailable"
    return True, None


def clear_calibration_guard(status: Mapping[str, Any] | None) -> tuple[bool, str | None]:
    """Allow clearing taught limits only while the axis is stopped and disabled."""

    status = status if isinstance(status, Mapping) else {}
    if status.get("moving") or status.get("enabled") or status.get("servo_request"):
        return False, "clear_requires_disabled_stopped_axis"
    return True, None


def record_manual_endpoint(
    raw: Mapping[str, Any] | None,
    side: str,
    position_counts: Any,
    counts_per_rev: int,
    position_direction: int = 1,
) -> dict[str, Any]:
    """Return a new persisted config after recording one stopped endpoint."""

    side_name = str(side or "").strip().lower()
    if side_name not in {"left", "right"}:
        raise TravelConfigError("endpoint side must be left or right")
    config = normalize_travel_config(raw, counts_per_rev, position_direction)
    position = _int(position_counts, name="position_counts")
    values = {
        "left_limit_counts": config.left_limit_counts,
        "right_limit_counts": config.right_limit_counts,
        "safety_margin_counts": config.safety_margin_counts,
        "calibration_data_version": CALIBRATION_DATA_VERSION,
        "anti_sway_enabled": config.anti_sway_enabled,
        "sway_period_ms": config.sway_period_ms,
        "shaper": config.shaper,
        "residual_sway_limit_counts": config.residual_sway_limit_counts,
    }
    values[f"{side_name}_limit_counts"] = position
    left = values["left_limit_counts"]
    right = values["right_limit_counts"]
    values["calibration_state"] = "both_valid" if left is not None and right is not None else f"{side_name}_valid"
    normalize_travel_config(values, counts_per_rev, position_direction)
    return values


def travel_ui_model(
    status: Mapping[str, Any] | None,
    raw_config: Mapping[str, Any] | None,
    counts_per_rev: int,
    position_direction: int = 1,
) -> dict[str, Any]:
    """Return JSON-ready UI data for a single linear axis."""

    config = normalize_travel_config(raw_config, counts_per_rev, position_direction)
    guard = build_travel_guard(status, config)
    return {
        "available": True,
        "counts_per_rev": config.counts_per_rev,
        "calibration_state": config.calibration_state,
        "calibration_data_version": config.calibration_data_version,
        "endpoints_valid": config.endpoints_valid,
        "calibration_actions_available": True,
        "calibration_actions_reason": "manual_endpoint_recording",
        "calibration_engine": "manual_endpoint_recording_v1",
        "calibration_engine_available": True,
        "recorded_positions": {
            "left": config.left_limit_counts,
            "right": config.right_limit_counts,
        },
        "anti_sway": {
            "enabled": config.anti_sway_enabled,
            "ready": config.anti_sway_ready,
            "shaper": config.shaper,
            "sway_period_ms": config.sway_period_ms,
            "residual_sway_limit_counts": config.residual_sway_limit_counts,
        },
        "guard": guard,
    }
