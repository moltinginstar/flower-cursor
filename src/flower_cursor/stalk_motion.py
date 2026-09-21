"""Rooted, tip-first stalk wilting without changing lifecycle duration."""

from __future__ import annotations

import math

from .easing import smoothstep


def wilt_progress(vitality: float) -> float:
    if vitality >= 0.55:
        return 0.0
    if vitality <= 0.02:
        return 1.0
    return smoothstep(math.log(0.55 / vitality) / math.log(0.55 / 0.02))


def wilt_point(t: float, progress: float) -> tuple[float, float, float]:
    if t <= 0:
        return 0.0, 0.0, 0.0
    if progress <= 0:
        return 0.0, 0.0, t
    progress = min(1.0, progress)
    power = 3.0 - 1.5 * progress
    step = t / 24
    forward = height = 0.0
    for index in range(24):
        position = (index + 0.5) * step
        angle = math.pi * progress * position**power
        forward += math.sin(angle) * step
        height += math.cos(angle) * step
    settling = smoothstep((progress - 0.55) / 0.45)
    length = 1.0 - 0.88 * settling
    side = -0.22 * forward + 0.06 * t**2 * math.sin(math.tau * t) * math.sin(math.pi * progress)
    side = 0.09 * math.tanh(side / 0.09)
    return side * length, forward * length, height * length
