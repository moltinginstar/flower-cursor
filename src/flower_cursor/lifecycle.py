"""Continuously interruptible flower lifecycle state."""

from __future__ import annotations

import math
from dataclasses import dataclass

from .easing import clamp01, require_duration, smoothstep

_STEM_GROWTH_RESPONSE = 8.5
_BLOOM_GROWTH_RESPONSE = 5.5
_VITALITY_GROWTH_RESPONSE = 6.0
_LEAF_GROWTH_RESPONSE = 8.0
_STEM_WITHER_RESPONSE = 4.0
_BLOOM_WITHER_RESPONSE = 8.0
_VITALITY_WITHER_RESPONSE = 6.0
_LEAF_WITHER_RESPONSE = 8.0


@dataclass(frozen=True, slots=True)
class CursorCell:
  """A terminal cell in row/column coordinates."""

  row: int
  column: int


@dataclass(frozen=True, slots=True)
class FlowerPose:
  """Renderable state for one flower at one instant."""

  stem: float
  bloom: float
  vitality: float
  idle_phase: float
  idle_sway: float
  bud_size: float = 0.0
  leaf_unfurl: float = 0.0
  petal_open: float = 0.0
  head_lean: float = 0.0
  stem_bend: float = 0.0
  maturity: float = 0.0

  @property
  def opacity(self) -> float:
    return 1.0

  @property
  def collapse(self) -> float:
    return smoothstep((0.50 - self.vitality) / 0.46)

  @property
  def petal_curl(self) -> float:
    return smoothstep((0.86 - self.vitality) / 0.76)

  @property
  def retraction(self) -> float:
    return smoothstep((0.038 - self.vitality) / 0.018)


@dataclass(slots=True)
class Spring:
  value: float
  response: float
  damping: float = 1.0
  velocity: float = 0.0

  def step(self, target: float, duration: float) -> None:
    remaining = duration
    step_size = 1 / 120
    while remaining > 0:
      dt = min(remaining, step_size)
      acceleration = (target - self.value) * self.response**2
      acceleration -= 2 * self.damping * self.response * self.velocity
      self.velocity += acceleration * dt
      self.value += self.velocity * dt
      if self.value < 0:
        self.value = 0
        self.velocity = max(0.0, self.velocity)
      elif self.value > 1:
        self.value = 1
        self.velocity = min(0.0, self.velocity)
      remaining -= dt


@dataclass(slots=True)
class FlowerInstance:
  """A flower whose targets can change without resetting its physical state."""

  identifier: int
  cell: CursorCell
  active: bool
  _stem: Spring
  _bloom: Spring
  _vitality: Spring
  _leaf: Spring
  _maturity: Spring
  idle_phase: float = 0.0

  @classmethod
  def bud(cls, identifier: int, cell: CursorCell) -> FlowerInstance:
    return cls(
      identifier=identifier,
      cell=cell,
      active=True,
      _stem=Spring(0.0, response=_STEM_GROWTH_RESPONSE),
      _bloom=Spring(0.0, response=_BLOOM_GROWTH_RESPONSE),
      _vitality=Spring(1.0, response=_VITALITY_GROWTH_RESPONSE),
      _leaf=Spring(0.0, response=_LEAF_GROWTH_RESPONSE),
      _maturity=Spring(0.0, response=8.0),
    )

  @property
  def pose(self) -> FlowerPose:
    stem = clamp01(self._stem.value)
    bloom = clamp01(self._bloom.value)
    vitality = clamp01(self._vitality.value)
    wither_amount = 1.0 - vitality
    late_wither = smoothstep((wither_amount - 0.30) / 0.65)
    sway = math.sin(self.idle_phase) * 0.065 * vitality
    breathing = 0.012 * (1.0 - math.cos(self.idle_phase)) * bloom**4 * vitality
    head_lean = sway + 0.02 * wither_amount + 0.16 * late_wither
    stem_bend = 0.025 * wither_amount + 0.80 * late_wither
    return FlowerPose(
      stem=stem,
      bloom=bloom,
      vitality=vitality,
      idle_phase=self.idle_phase,
      idle_sway=sway,
      bud_size=clamp01(0.18 + 0.78 * stem - 0.50 * bloom),
      leaf_unfurl=clamp01(self._leaf.value),
      petal_open=clamp01(bloom - breathing),
      head_lean=head_lean,
      stem_bend=stem_bend,
      maturity=clamp01(self._maturity.value),
    )

  def begin_withering(self) -> None:
    self.active = False
    self._stem.response = _STEM_WITHER_RESPONSE
    self._bloom.response = _BLOOM_WITHER_RESPONSE
    self._vitality.response = _VITALITY_WITHER_RESPONSE
    self._leaf.response = _LEAF_WITHER_RESPONSE

  def restore(self) -> None:
    self.active = True
    self._stem.response = _STEM_GROWTH_RESPONSE
    self._bloom.response = _BLOOM_GROWTH_RESPONSE
    self._vitality.response = _VITALITY_GROWTH_RESPONSE
    self._leaf.response = _LEAF_GROWTH_RESPONSE

  def update(self, duration: float) -> None:
    require_duration(duration)
    if self.active:
      self._stem.step(1.0, duration)
      bloom_target = smoothstep((self._stem.value - 0.82) / 0.18)
      self._bloom.step(bloom_target, duration)
      self._vitality.step(1.0, duration)
      leaf_target = smoothstep((self._stem.value - 0.36) / 0.54)
      self._maturity.step(max(self._maturity.value, self._bloom.value), duration)
    else:
      self._bloom.step(0.0, duration)
      self._vitality.step(0.0, duration)
      self._stem.step(self._stem.value, duration)
      self._maturity.step(self._maturity.value, duration)
      leaf_target = self._leaf.value
    self._leaf.step(clamp01(leaf_target), duration)
    self.idle_phase = (self.idle_phase + duration * 1.7) % math.tau

  @property
  def dead(self) -> bool:
    return not self.active and self._vitality.value < 0.020


class FlowerLifecycle:
  """Manage active and recently departed flowers independently."""

  def __init__(self, *, retain_departures: bool = True) -> None:
    self._flowers: list[FlowerInstance] = []
    self._next_identifier = 1
    self._active_identifier: int | None = None
    self._retain_departures = retain_departures

  @property
  def flowers(self) -> tuple[FlowerInstance, ...]:
    return tuple(self._flowers)

  @property
  def active(self) -> FlowerInstance | None:
    return next(
      (flower for flower in self._flowers if flower.identifier == self._active_identifier),
      None,
    )

  def reset(self) -> None:
    self._flowers.clear()
    self._active_identifier = None

  def move_to(self, cell: CursorCell) -> FlowerInstance:
    """Move the logical cursor immediately and retarget the visible flowers."""
    current = self.active
    if current is not None and current.cell == cell:
      return current
    if current is not None:
      if self._retain_departures:
        current.begin_withering()
      else:
        self._flowers.remove(current)

    returning = (
      next((flower for flower in self._flowers if flower.cell == cell), None) if self._retain_departures else None
    )
    if returning is None:
      returning = FlowerInstance.bud(self._next_identifier, cell)
      self._next_identifier += 1
      self._flowers.append(returning)
    else:
      returning.restore()
    self._active_identifier = returning.identifier
    return returning

  def update(self, duration: float) -> None:
    require_duration(duration)
    for flower in self._flowers:
      flower.update(duration)
    self._flowers[:] = [flower for flower in self._flowers if not flower.dead]
    if self._active_identifier is not None and self.active is None:
      self._active_identifier = None
