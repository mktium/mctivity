#!/usr/bin/env python3
"""Pure input-shaping helpers for the single-axis anti-sway prototype.

This module only plans delayed relative target increments. It does not talk
to motiond, enable a drive, change a mode, or issue a motion command.
"""

from __future__ import annotations

from dataclasses import dataclass
import math


class AntiSwayConfigError(ValueError):
    """Raised when an input-shaper parameter is unsafe or unusable."""


@dataclass(frozen=True)
class ShaperImpulse:
    delay_ms: int
    delta_counts: int
    amplitude: float


def _validate_period(period_ms: int) -> int:
    if isinstance(period_ms, bool):
        raise AntiSwayConfigError("period_ms must be an integer")
    try:
        period = int(period_ms)
    except (TypeError, ValueError) as exc:
        raise AntiSwayConfigError("period_ms must be an integer") from exc
    if period < 10 or period > 120000:
        raise AntiSwayConfigError("period_ms is out of range")
    return period


def _validate_damping(damping_ratio: float) -> float:
    try:
        damping = float(damping_ratio)
    except (TypeError, ValueError) as exc:
        raise AntiSwayConfigError("damping_ratio must be a number") from exc
    if not math.isfinite(damping) or damping < 0.0 or damping >= 1.0:
        raise AntiSwayConfigError("damping_ratio must be in [0, 1)")
    return damping


def build_zvd_impulses(period_ms: int, damping_ratio: float = 0.05) -> tuple[tuple[int, float], ...]:
    """Build a three-impulse ZVD shaper using the measured damped period."""

    period = _validate_period(period_ms)
    damping = _validate_damping(damping_ratio)
    k = 1.0 if damping == 0.0 else math.exp(-damping * math.pi / math.sqrt(1.0 - damping * damping))
    denominator = (1.0 + k) ** 2
    amplitudes = (k * k / denominator, 2.0 * k / denominator, 1.0 / denominator)
    return ((0, amplitudes[0]), (period // 2, amplitudes[1]), (period, amplitudes[2]))


def plan_zvd_move(
    target_delta_counts: int,
    period_ms: int,
    damping_ratio: float = 0.05,
) -> tuple[ShaperImpulse, ...]:
    """Return integer increments whose sum exactly equals the requested move."""

    if isinstance(target_delta_counts, bool):
        raise AntiSwayConfigError("target_delta_counts must be an integer")
    try:
        delta = int(target_delta_counts)
    except (TypeError, ValueError) as exc:
        raise AntiSwayConfigError("target_delta_counts must be an integer") from exc
    impulses = build_zvd_impulses(period_ms, damping_ratio)
    raw_counts = [delta * amplitude for _, amplitude in impulses]
    rounded = [int(round(value)) for value in raw_counts]
    rounded[-1] = delta - sum(rounded[:-1])
    return tuple(
        ShaperImpulse(delay_ms=delay, delta_counts=counts, amplitude=amplitude)
        for (delay, amplitude), counts in zip(impulses, rounded)
    )


def shaped_target_counts(start_counts: int, impulses: tuple[ShaperImpulse, ...]) -> tuple[tuple[int, int], ...]:
    """Convert impulses to delayed absolute targets for a future adapter."""

    position = int(start_counts)
    targets = []
    for impulse in impulses:
        position += impulse.delta_counts
        targets.append((impulse.delay_ms, position))
    return tuple(targets)
