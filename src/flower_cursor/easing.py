"""Clamping, easing and argument checks shared by every animated model.

These are small enough to retype and were, repeatedly: the same smoothstep and
the same 0-to-1 clamp appeared in the flower lifecycle, the stalk, the petals,
the bee and the audio, and a constant tweaked in one of them changed only that
copy. They live here rather than in `lifecycle` so the bee and the buzz can use
them without importing the flower.
"""

from __future__ import annotations

import math


def clamp01(value: float) -> float:
  """*value* confined to the 0-to-1 range every animated quantity uses."""
  return max(0.0, min(1.0, value))


def smoothstep(value: float) -> float:
  """The classic 3t^2-2t^3 ease, clamped so the ends stay flat."""
  value = clamp01(value)
  return value * value * (3.0 - 2.0 * value)


def require_duration(value: float, label: str = "duration") -> float:
  """Reject the elapsed times that would corrupt an integration step.

  A NaN spreads silently through every spring it touches and a negative step
  runs the animation backwards, so both are refused at the boundary rather
  than debugged from the far side of a frame that looks wrong.
  """
  if not math.isfinite(value) or value < 0:
    raise ValueError(f"{label} must be finite and non-negative")
  return value
