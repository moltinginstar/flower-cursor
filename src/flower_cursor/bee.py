"""One bounded idle visitor, with no terminal or caret ownership."""

from __future__ import annotations

import math
from dataclasses import dataclass

from .lifecycle import CursorCell

IDLE_DELAY = 10.0
APPROACH_TIME = 3.8
REST_TIME = 6.0
DEPARTURE_TIME = 1.8
COOLDOWN = 35.0


def smooth(value: float) -> float:
    value = max(0.0, min(1.0, value))
    return value * value * (3 - 2 * value)


@dataclass(frozen=True, slots=True)
class BeePose:
    x: float
    y: float
    scale: float
    bank: float
    folded: float
    wing_phase: float


class IdleBee:
    def __init__(self) -> None:
        self.cell: CursorCell | None = None
        self.target: CursorCell | None = None
        self.idle = 0.0
        self.cooldown = 0.0
        self.age = 0.0
        self.departure: float | None = None
        self.departure_folded = 0.0
        self.x, self.y = 0.83, 0.27
        self.vx = self.vy = 0.0
        self.head = (0.503, 0.119)
        self.phase = 0.0
        self.generation = 0

    @property
    def visible(self) -> bool:
        return self.cell is not None

    @property
    def folded(self) -> float:
        if self.departure is not None:
            return self.departure_folded * (1 - smooth(self.departure / 0.35))
        return smooth((self.age - 2.5) / 1.3)

    def leave(self) -> None:
        if self.visible and self.departure is None:
            self.departure_folded = self.folded
            self.departure = 0.0

    def move_to(self, cell: CursorCell) -> None:
        if cell == self.target:
            return
        self.target = cell
        self.idle = 0.0
        self.leave()

    def reset(self) -> None:
        self.cell = self.target = None
        self.idle = 0.0
        self.cooldown = COOLDOWN
        self.departure = None
        self.vx = self.vy = 0.0

    def update(self, duration: float, head: tuple[float, float] | None = None) -> None:
        if not math.isfinite(duration) or duration < 0:
            raise ValueError("duration must be finite and non-negative")
        if duration > 2:
            # A suspended application must not replay a stale visitor on resume.
            target = self.target
            self.reset()
            self.target = target
            return
        steps = max(1, math.ceil(duration * 120))
        dt = duration / steps
        for _ in range(steps):
            self._step(dt, head)

    def _step(self, dt: float, head: tuple[float, float] | None) -> None:
        self.cooldown = max(0.0, self.cooldown - dt)
        if self.target is not None:
            self.idle += dt
        if not self.visible:
            if self.target is None or self.idle + 1e-9 < IDLE_DELAY or self.cooldown > 0:
                return
            self.cell = self.target
            self.generation += 1
            self.age = self.phase = 0.0
            self.departure = None
            self.x, self.y = 0.83, 0.27
            self.vx = self.vy = 0.0
        self.age += dt
        self.phase += dt * 11.3
        if self.departure is None:
            if head is not None:
                self.head = head
            if self.age >= APPROACH_TIME + REST_TIME:
                self.leave()
        if self.departure is not None:
            self.departure += dt
            if self.departure >= DEPARTURE_TIME:
                self.cell = None
                self.cooldown = COOLDOWN
                return
            goal = (0.85, 0.29)
            rate = 3.8
        else:
            approach = smooth(self.age / 2.2)
            settle = smooth((self.age - 2.2) / 1.6)
            hover = 1 - settle
            goal = (
                0.83 * (1 - approach)
                + (self.head[0] + 0.018 + 0.025 * hover * math.sin(self.age * 4.1)) * approach,
                0.27 * (1 - approach)
                + (self.head[1] - 0.012 - 0.014 * hover + 0.002 * hover * math.sin(self.age * 5.3))
                * approach,
            )
            rate = 12.0 + 38.0 * settle
        for axis, velocity, destination in (("x", "vx", goal[0]), ("y", "vy", goal[1])):
            position = getattr(self, axis)
            speed = getattr(self, velocity)
            offset = position - destination
            c = speed + rate * offset
            decay = math.exp(-rate * dt)
            setattr(self, axis, destination + (offset + c * dt) * decay)
            setattr(self, velocity, (speed - rate * c * dt) * decay)

    @property
    def pose(self) -> BeePose:
        scale = smooth(self.age / 0.65)
        if self.departure is not None:
            scale *= 1 - smooth(self.departure / DEPARTURE_TIME)
        return BeePose(
            self.x, self.y, scale, max(-18, min(18, -self.vy * 70)), self.folded, self.phase
        )
