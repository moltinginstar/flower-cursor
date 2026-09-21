"""Experimental shedding state for the Blender exit/recovery study."""

from __future__ import annotations

import math
from dataclasses import dataclass, replace

from .easing import require_duration, smoothstep
from .idle_motion import idle_pose
from .lifecycle import FlowerInstance, FlowerPose, Spring

_SHED_ORDER = (1, 4, 0, 5, 2, 3)
_FLIGHT_LIFETIME = 1.6


@dataclass(slots=True)
class PetalFlight:
    identifier: int
    index: int
    pose: FlowerPose
    previous_pose: FlowerPose
    growth: float
    sample_duration: float
    age: float = 0.0


def petal_decay(age: float) -> float:
    return smoothstep((age - 0.24) / 1.16)


def petal_displacement(
    index: int, age: float, velocity: tuple[float, float, float]
) -> tuple[float, float, float]:
    drag_time = -math.expm1(-5.0 * age) / 5.0
    spread = 1.1 * math.sin(index * 2.3 + 0.4)
    spread *= 1.0 - math.exp(-6.0 * age) * (1.0 + 6.0 * age)
    flutter = 0.06 * (math.sin(9.0 * age + index) - math.sin(index))
    flutter *= -math.expm1(-4.0 * age)
    return (
        velocity[0] * drag_time + 0.866 * spread + flutter,
        velocity[1] * drag_time + 0.5 * spread,
        velocity[2] * drag_time - 1.4 * age * age,
    )


class SheddingFlower:
    def __init__(
        self,
        flower: FlowerInstance,
        *,
        wall_time_idle: bool = False,
        group_count: int = 6,
        shedding_order: tuple[int, ...] | None = None,
    ) -> None:
        if group_count < 1:
            raise ValueError("group_count must be positive")
        if shedding_order is None:
            shedding_order = _SHED_ORDER if group_count == 6 else tuple(range(group_count))
        if tuple(sorted(shedding_order)) != tuple(range(group_count)):
            raise ValueError("shedding_order must be a permutation of all groups")
        self.flower = flower
        self._wall_time_idle = wall_time_idle
        self._idle_time = 0.0
        self.shedding_order = shedding_order
        # Density-aware staggering keeps dense blooms inside the existing exit window;
        # the lifecycle and stalk timing remain unchanged.
        self._detach_interval = 0.05 if group_count <= 18 else 0.85 / (group_count - 1)
        self.petals = tuple(Spring(1.0, response=9.0) for _ in range(group_count))
        self.flights: list[PetalFlight] = []
        self.head = Spring(1.0, response=18.0)
        self._opening = Spring(flower.pose.petal_open, response=12.0)
        self._lean = Spring(0.5 + flower.pose.head_lean / 2, response=12.0, damping=0.7)
        self._exit_age = 0.0
        self._shed: set[int] = set()
        self._next_flight = 1

    @property
    def _base_pose(self) -> FlowerPose:
        return (
            idle_pose(self.flower.pose, self._idle_time)
            if self._wall_time_idle
            else self.flower.pose
        )

    @property
    def pose(self) -> FlowerPose:
        return replace(
            self._base_pose,
            petal_open=self._opening.value,
            head_lean=(self._lean.value - 0.5) * 2,
        )

    @property
    def finished(self) -> bool:
        return self.flower.dead and not self.flights

    def begin_withering(self) -> None:
        if self.flower.active:
            self._exit_age = 0.0
            self._shed.clear()
            self.flower.begin_withering()

    def restore(self) -> None:
        self.flower.restore()

    def update(self, duration: float, *, wall_duration: float | None = None) -> None:
        require_duration(duration)
        wall_duration = duration if wall_duration is None else wall_duration
        require_duration(wall_duration, "wall duration")
        remaining = duration
        while remaining > 1e-12:
            dt = min(remaining, 1 / 120)
            previous = self.pose
            self.flower.update(dt)
            self._idle_time += dt * wall_duration / duration
            pose = self._base_pose
            opening = pose.petal_open
            if not self.flower.active:
                opening = max(opening, pose.maturity * 0.75)
            self._opening.step(opening, dt)
            sway = 0.12 * (1.0 - pose.vitality) * math.sin(pose.idle_phase * 5.0)
            self._lean.step(0.5 + (pose.head_lean + sway) / 2, dt)
            for flight in self.flights:
                flight.age += dt
            self.flights[:] = [f for f in self.flights if f.age < _FLIGHT_LIFETIME]
            if self.flower.active:
                for petal in self.petals:
                    petal.step(1.0, dt)
            else:
                for petal in self.petals:
                    petal.step(petal.value, dt)
                self._exit_age += dt
                for order, index in enumerate(self.shedding_order):
                    if index in self._shed or self._exit_age < 0.12 + order * self._detach_interval:
                        continue
                    self._shed.add(index)
                    petal = self.petals[index]
                    if petal.value > 0.001:
                        self.flights.append(
                            PetalFlight(
                                self._next_flight, index, self.pose, previous, petal.value, dt
                            )
                        )
                        self._next_flight += 1
                    petal.value = 0.0
                    petal.velocity = 0.0
            self.head.step(sum(p.value for p in self.petals) / len(self.petals), dt)
            remaining -= dt
