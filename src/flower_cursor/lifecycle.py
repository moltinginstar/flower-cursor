"""Continuously interruptible flower lifecycle state."""

from __future__ import annotations

import math
from dataclasses import dataclass

_STEM_GROWTH_RESPONSE = 8.5
_BLOOM_GROWTH_RESPONSE = 8.0
_VITALITY_GROWTH_RESPONSE = 6.0
_STEM_WITHER_RESPONSE = 36.0
_BLOOM_WITHER_RESPONSE = 30.0
_VITALITY_WITHER_RESPONSE = 22.0


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


@dataclass(slots=True)
class _Spring:
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
    """A flower whose target can change without resetting its physical state."""

    identifier: int
    cell: CursorCell
    active: bool
    _stem: _Spring
    _bloom: _Spring
    _vitality: _Spring
    idle_phase: float = 0.0

    @classmethod
    def bud(cls, identifier: int, cell: CursorCell) -> FlowerInstance:
        return cls(
            identifier=identifier,
            cell=cell,
            active=True,
            _stem=_Spring(0.0, response=_STEM_GROWTH_RESPONSE),
            _bloom=_Spring(0.0, response=_BLOOM_GROWTH_RESPONSE),
            _vitality=_Spring(1.0, response=_VITALITY_GROWTH_RESPONSE),
        )

    @property
    def pose(self) -> FlowerPose:
        sway = math.sin(self.idle_phase) * 0.035 * self._vitality.value
        return FlowerPose(
            stem=self._stem.value,
            bloom=self._bloom.value,
            vitality=self._vitality.value,
            idle_phase=self.idle_phase,
            idle_sway=sway,
        )

    def begin_withering(self) -> None:
        self.active = False
        self._stem.response = _STEM_WITHER_RESPONSE
        self._bloom.response = _BLOOM_WITHER_RESPONSE
        self._vitality.response = _VITALITY_WITHER_RESPONSE

    def restore(self) -> None:
        self.active = True
        self._stem.response = _STEM_GROWTH_RESPONSE
        self._bloom.response = _BLOOM_GROWTH_RESPONSE
        self._vitality.response = _VITALITY_GROWTH_RESPONSE

    def update(self, duration: float) -> None:
        if duration < 0:
            raise ValueError("duration must be non-negative")
        target = 1.0 if self.active else 0.0
        self._stem.step(target, duration)
        self._bloom.step(target, duration)
        self._vitality.step(target, duration)
        self.idle_phase = (self.idle_phase + duration * 1.7) % math.tau

    @property
    def dead(self) -> bool:
        maximum_state = max(self._stem.value, self._bloom.value, self._vitality.value)
        return not self.active and maximum_state < 0.01


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
            next((flower for flower in self._flowers if flower.cell == cell), None)
            if self._retain_departures
            else None
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
        if duration < 0:
            raise ValueError("duration must be non-negative")
        for flower in self._flowers:
            flower.update(duration)
        self._flowers[:] = [flower for flower in self._flowers if not flower.dead]
        if self._active_identifier is not None and self.active is None:
            self._active_identifier = None
