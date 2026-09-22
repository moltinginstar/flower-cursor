"""One bounded screen-space idle visitor, with no terminal or caret ownership."""

from __future__ import annotations

import json
import math
import random
from dataclasses import dataclass
from pathlib import Path

from .easing import clamp01, require_duration, smoothstep
from .geometry import ViewportGeometry
from .lifecycle import CursorCell

APPROACH_TIME = 3.8
REST_TIME = 6.0
DEPARTURE_TIME = 1.8
# How long the caret stays still before a visit, on average. One number rather
# than an idle threshold plus a cooldown: both answered the same question -
# when may a bee appear - and a single wait that restarts whenever the caret
# moves or the bee leaves says it once. The clock runs only while nothing
# moves, so a bee means the screen was actually left alone.
DEFAULT_INTERVAL = 90.0
# Drawn fresh for every wait, because a visitor that arrives on the same
# schedule every time reads as a timer rather than as something alive.
INTERVAL_JITTER = (0.67, 1.33)
HOVER_TIME = 2.45
LANDING_TIME = 2.8
TOUCHDOWN_TIME = 3.45
REST_CONTACT = tuple(
    json.loads((Path(__file__).parent / "assets/bee/manifest.json").read_text())["contacts"]["0"][
        "anchor"
    ]
)


@dataclass(frozen=True, slots=True)
class BeePose:
    x: float
    y: float
    size: tuple[int, int]
    bank: float
    folded: float
    wing_phase: float
    heading: float = 0.0


@dataclass(frozen=True, slots=True)
class _FlightLeg:
    """Quintic Hermite segment, matching incoming derivatives and ending at zero acceleration."""

    duration: float
    x: tuple[float, ...]
    y: tuple[float, ...]

    @staticmethod
    def coefficients(
        start: float,
        velocity: float,
        acceleration: float,
        end: float,
        end_velocity: float,
        duration: float,
    ) -> tuple[float, ...]:
        v = velocity * duration
        a = acceleration * duration * duration / 2
        distance = end - start - v - a
        dv = end_velocity * duration - v - 2 * a
        da = -2 * a
        return (
            start,
            v,
            a,
            10 * distance - 4 * dv + da / 2,
            -15 * distance + 7 * dv - da,
            6 * distance - 3 * dv + da / 2,
        )

    def sample(self, time: float) -> tuple[float, float, float, float, float, float]:
        t = clamp01(time / self.duration)
        values = []
        for c in (self.x, self.y):
            position = sum(value * t**i for i, value in enumerate(c))
            velocity = sum(i * c[i] * t ** (i - 1) for i in range(1, 6)) / self.duration
            acceleration = (
                sum(i * (i - 1) * c[i] * t ** (i - 2) for i in range(2, 6)) / self.duration**2
            )
            values.append((position, velocity, acceleration))
        return (
            values[0][0],
            values[1][0],
            values[0][1],
            values[1][1],
            values[0][2],
            values[1][2],
        )


class IdleBee:
    def __init__(
        self, rng: random.Random | None = None, *, interval: float = DEFAULT_INTERVAL
    ) -> None:
        self.rng = rng if rng is not None else random.Random()
        # The wait draws from a stream of its own, forked from the visit stream
        # without consuming it, so how often a bee comes never changes the
        # route it flies for a given seed.
        self._waits = random.Random()
        self._waits.setstate(self.rng.getstate())
        self.interval = interval
        self.hover_time = HOVER_TIME
        self.landing_time = LANDING_TIME
        self.touchdown_time = TOUCHDOWN_TIME
        self.approach_time = APPROACH_TIME
        self.rest_time = REST_TIME
        self.departure_time = DEPARTURE_TIME
        self.landing_offset = (0.0, 0.0)
        self._landing_support_bounds: tuple[tuple[float, float], tuple[float, float]] | None = None
        self._source = (0, 0.5)
        self._exit: tuple[int, float] | None = None
        self._bend = 0.0
        self._midpoint_time = 1.15
        self._hover_offset = (18.0, -22.0)
        self._heading = 0.0
        self.cell: CursorCell | None = None
        self.target: CursorCell | None = None
        self.viewport: ViewportGeometry | None = None
        self.size = (50, 45)
        self._next_size = self.size
        self.idle = 0.0
        self.wait = 0.0
        self.age = 0.0
        self.departure: float | None = None
        self.departure_folded = 0.0
        self.x = self.y = self.vx = self.vy = 0.0
        self.head = (0.0, 0.0)
        self._entrance = (0.0, 0.0)
        self.ax = self.ay = 0.0
        self._legs: list[_FlightLeg] = []
        self._leg_time = 0.0
        self._planned_head = self.head
        self._retarget_age = 0.0
        self._geometry_changed = False
        self.phase = 0.0
        self.generation = 0
        self._sample_wait()

    def _sample_wait(self) -> None:
        """Start the wait over, with a length this visit does not share."""
        self.idle = 0.0
        self.wait = self.interval * self._waits.uniform(*INTERVAL_JITTER)

    @property
    def visible(self) -> bool:
        return self.cell is not None

    @property
    def radius(self) -> float:
        return math.hypot(*self.size) / 2 + 3

    @property
    def folded(self) -> float:
        if self.departure is not None:
            return self.departure_folded * (1 - smoothstep(self.departure / 0.22))
        if self.age < self.touchdown_time:
            return 0.35 * smoothstep(
                (self.age - self.landing_time) / (self.touchdown_time - self.landing_time)
            )
        return 0.35 + 0.65 * smoothstep(
            (self.age - self.touchdown_time) / (self.approach_time - self.touchdown_time)
        )

    @property
    def landing(self) -> tuple[float, float]:
        return (
            self.head[0] + self.landing_offset[0] + (0.5 - REST_CONTACT[0]) * self.size[0],
            self.head[1] + self.landing_offset[1] + (0.5 - REST_CONTACT[1]) * self.size[1],
        )

    @property
    def landing_support_bounds(
        self,
    ) -> tuple[tuple[float, float], tuple[float, float]] | None:
        return self._landing_support_bounds

    def set_landing_support_bounds(
        self, bounds: tuple[tuple[float, float], tuple[float, float]] | None
    ) -> None:
        self._landing_support_bounds = bounds

    def set_geometry(self, viewport: ViewportGeometry | None, size: tuple[int, int]) -> None:
        if viewport is None and self.visible:
            target = self.target
            self.reset()
            self.target = target
        self._geometry_changed |= viewport != self.viewport
        self.viewport = viewport
        self._next_size = size

    def leave(self) -> None:
        if self.visible and self.departure is None:
            self.departure_folded = self.folded
            self.departure = 0.0
            self._exit = (self.rng.randrange(4), self.rng.uniform(0.12, 0.88))
            self.departure_time = self.rng.uniform(1.6, 2.3)
            self._plan_departure()

    def move_to(self, cell: CursorCell) -> None:
        if cell == self.target:
            return
        self.target = cell
        self._sample_wait()
        self.leave()

    def reset(self) -> None:
        self.cell = self.target = None
        self._sample_wait()
        self.departure = None
        self.vx = self.vy = self.ax = self.ay = 0.0
        self._legs.clear()
        self._leg_time = 0.0

    def update(self, duration: float, head: tuple[float, float] | None = None) -> None:
        require_duration(duration)
        if duration > 2:
            target = self.target
            self.reset()
            self.target = target
            return
        steps = max(1, math.ceil(duration * 120))
        dt = duration / steps
        for _ in range(steps):
            self._step(dt, head)

    def _step(self, dt: float, head: tuple[float, float] | None) -> None:
        if self.target is not None:
            self.idle += dt
        viewport = self.viewport
        if viewport is None:
            return
        if not self.visible:
            if self.target is None or head is None or self.idle + 1e-9 < self.wait:
                return
            self.cell = self.target
            self.generation += 1
            self.age = self.phase = 0.0
            self.departure = None
            self.size = self._next_size
            self._sample_visit()
            self.x, self.y = self._edge_point(self._source)
            self._entrance = self.x, self.y
            self.vx = self.vy = self.ax = self.ay = 0.0
            self.head = head
            self._plan_approach()
            _, _, vx, vy, _, _ = self._legs[0].sample(0.001)
            self._heading = math.degrees(math.atan2(-vy, -vx))
            self._geometry_changed = False
            return
        if self.departure is None:
            if head is not None:
                self.head = head
            settled = self.age >= self.approach_time
            changed = math.dist(self.head, self._planned_head) > (0.03 if settled else 0.25)
            interval = 0.04 if settled else 0.12
            if self._geometry_changed or (changed and self.age - self._retarget_age >= interval):
                self._plan_approach()
            if self.age + dt >= self.approach_time + self.rest_time:
                resting = max(0.0, self.approach_time + self.rest_time - self.age)
                self._fly(resting)
                self.age += resting
                self.phase += resting * 11.3
                dt -= resting
                self.leave()
        elif self._geometry_changed:
            self._plan_departure()
        self._geometry_changed = False
        self.age += dt
        self.phase += dt * 11.3
        if self.departure is not None:
            self.departure += dt
        self._fly(dt)
        if (
            dt > 0
            and self.departure is not None
            and self._exit is not None
            and self._outside_edges[self._exit[0]]
        ):
            self.cell = None
            self._sample_wait()

    @property
    def _outside_edges(self) -> tuple[bool, bool, bool, bool]:
        viewport = self.viewport
        if viewport is None:
            return False, False, False, False
        return (
            self.x + self.radius < 0,
            self.x - self.radius > viewport.width,
            self.y + self.radius < 0,
            self.y - self.radius > viewport.height,
        )

    @property
    def offscreen(self) -> bool:
        return any(self._outside_edges)

    def _sample_visit(self) -> None:
        timing = self.rng.uniform(0.92, 1.12)
        self.hover_time = HOVER_TIME * timing
        self.landing_time = LANDING_TIME * timing
        self.touchdown_time = TOUCHDOWN_TIME * timing
        self.approach_time = APPROACH_TIME * timing
        self.rest_time = self.rng.uniform(4.8, 7.2)
        self._source = (self.rng.randrange(4), self.rng.uniform(0.12, 0.88))
        self._exit = None
        self._bend = self.rng.uniform(-0.2, 0.2)
        self._midpoint_time = self.rng.uniform(1.0, 1.3) * timing
        self._hover_offset = (self.rng.uniform(18, 26), self.rng.uniform(-28, -20))
        angle = self.rng.uniform(-math.pi, math.pi)
        distance = math.sqrt(self.rng.random())
        offset = (
            math.cos(angle) * distance * self.size[0] / 50,
            math.sin(angle) * distance * 0.4 * self.size[1] / 45,
        )
        if self._landing_support_bounds is not None:
            offset = tuple(
                max(lower, min(upper, value))
                for value, (lower, upper) in zip(offset, self._landing_support_bounds)
            )
        self.landing_offset = offset

    def _edge_point(self, edge: tuple[int, float]) -> tuple[float, float]:
        viewport = self.viewport
        assert viewport is not None
        side, fraction = edge
        margin = self.radius + 4
        return (
            (-margin, viewport.height * fraction),
            (viewport.width + margin, viewport.height * fraction),
            (viewport.width * fraction, -margin),
            (viewport.width * fraction, viewport.height + margin),
        )[side]

    def _plan(self, knots: list[tuple[float, tuple[float, float], tuple[float, float]]]) -> None:
        self._legs.clear()
        self._leg_time = 0.0
        position = (self.x, self.y)
        velocity = (self.vx, self.vy)
        acceleration = (self.ax, self.ay)
        for duration, end, end_velocity in knots:
            self._legs.append(
                _FlightLeg(
                    duration,
                    _FlightLeg.coefficients(
                        position[0], velocity[0], acceleration[0], end[0], end_velocity[0], duration
                    ),
                    _FlightLeg.coefficients(
                        position[1], velocity[1], acceleration[1], end[1], end_velocity[1], duration
                    ),
                )
            )
            position, velocity, acceleration = end, end_velocity, (0.0, 0.0)

    def _plan_approach(self) -> None:
        self._planned_head = self.head
        self._retarget_age = self.age
        scale = self.size[0] / 50
        x, y = self.landing
        viewport = self.viewport
        assert viewport is not None
        hover = (
            max(
                x + 8 * scale,
                min(viewport.width + self.radius / 4, x + self._hover_offset[0] * scale),
            ),
            max(-self.radius / 4, y + self._hover_offset[1] * scale),
        )
        start_x, start_y = self._entrance
        margin = min(self.radius + 12 * scale, viewport.width / 4, viewport.height / 4)
        above = start_y < hover[1]
        bend = (70 + abs(self._bend) * 250) * scale
        midpoint = (
            max(
                margin,
                min(
                    max(viewport.width - margin, hover[0] + 8 * scale),
                    max((start_x + hover[0]) / 2, hover[0] + bend),
                ),
            ),
            max(
                max(-self.radius / 2, min(margin, hover[1] - 18 * scale)),
                min(viewport.height - margin, hover[1] + (-bend if above else bend)),
            ),
        )
        incoming = math.dist(self._entrance, midpoint)
        outgoing = math.dist(midpoint, hover)
        tangent = (
            (midpoint[0] - start_x) / max(1.0, incoming)
            + (hover[0] - midpoint[0]) / max(1.0, outgoing),
            (midpoint[1] - start_y) / max(1.0, incoming)
            + (hover[1] - midpoint[1]) / max(1.0, outgoing),
        )
        length = max(1e-6, math.hypot(*tangent))
        speed = min(
            (incoming + outgoing) / self.hover_time,
            1.25
            * min(
                incoming / self._midpoint_time, outgoing / (self.hover_time - self._midpoint_time)
            ),
        )
        middle_velocity = (tangent[0] / length * speed, tangent[1] / length * speed)
        landing_lead = min(14 * scale, (hover[0] - x) * 0.65)
        alignment_lead = min(4 * scale, landing_lead * 0.4)
        drift = min(
            12 * scale, (hover[0] - x - landing_lead) / (self.landing_time - self.hover_time)
        )
        waypoints = [
            (
                self._midpoint_time,
                midpoint,
                middle_velocity,
            ),
            (self.hover_time, hover, (-drift, 0.0)),
            (
                self.landing_time,
                (x + landing_lead, max(hover[1], y - 18 * scale)),
                (-drift, 4 * scale),
            ),
            (
                self.touchdown_time - 0.26,
                (x + alignment_lead, y),
                (-alignment_lead * 5, 0.0),
            ),
            (self.touchdown_time, (x, y), (0.0, 0.0)),
        ]
        knots = []
        time = self.age
        for end_time, position, velocity in waypoints:
            if end_time > self.age + 0.18:
                knots.append((end_time - time, position, velocity))
                time = end_time
        if not knots:
            duration = max(0.12, min(1.2, math.dist((self.x, self.y), (x, y)) / 350))
            knots.append((duration, (x, y), (0.0, 0.0)))
        self._plan(knots)

    def _plan_departure(self) -> None:
        viewport = self.viewport
        if viewport is None:
            return
        scale = self.size[0] / 50
        assert self._exit is not None
        remaining = max(1.4, self.departure_time - (self.departure or 0))
        end = self._edge_point(self._exit)
        speed = math.hypot(self.vx, self.vy)
        if self.departure == 0 and self.departure_folded > 0.2 and speed < 5:
            duration = 0.45
            position = (self.x - 12 * scale, self.y - 28 * scale)
            velocity = (-35 * scale, -55 * scale)
        else:
            duration = min(0.35, max(0.08, speed * 1.5 / max(1.0, math.hypot(self.ax, self.ay))))
            heading = math.radians(self._heading)
            direction = (
                (self.vx / speed, self.vy / speed)
                if speed > 1
                else (-math.cos(heading), -math.sin(heading))
            )
            end_speed = min(120 * scale, max(35 * scale, speed))
            velocity = (direction[0] * end_speed, direction[1] * end_speed)
            position = (
                self.x + (self.vx + velocity[0]) * duration / 2 + self.ax * duration**2 / 12,
                self.y + (self.vy + velocity[1]) * duration / 2 + self.ay * duration**2 / 12,
            )
        knots = [(duration, position, velocity)]
        remaining -= duration
        dx, dy = end[0] - position[0], end[1] - position[1]
        distance = max(1.0, math.hypot(dx, dy))
        direction = (dx / distance, dy / distance)
        speed = math.hypot(*velocity)
        forward = (velocity[0] / speed, velocity[1] / speed)
        turn = math.atan2(
            forward[0] * direction[1] - forward[1] * direction[0],
            forward[0] * direction[0] + forward[1] * direction[1],
        )
        if abs(turn) > 0.35:
            duration = min(remaining - 0.4, 0.25 + abs(turn) * 0.16)
            side = math.copysign(1, turn)
            angle = abs(turn)
            radius = (speed + 90 * scale) * duration / (2 * angle)
            position = (
                position[0]
                + radius
                * (math.sin(angle) * forward[0] - side * (1 - math.cos(angle)) * forward[1]),
                position[1]
                + radius
                * (math.sin(angle) * forward[1] + side * (1 - math.cos(angle)) * forward[0]),
            )
            knots.append(
                (duration, position, (direction[0] * 90 * scale, direction[1] * 90 * scale))
            )
            remaining -= duration
        speed = max(120 * scale, math.dist(position, end) / remaining * 0.8)
        side = self._exit[0]
        outward = ((-speed, 0.0), (speed, 0.0), (0.0, -speed), (0.0, speed))[side]
        knots.append((remaining, end, outward))
        self._plan(knots)

    def _fly(self, dt: float) -> None:
        while self._legs and dt > 0:
            leg = self._legs[0]
            step = min(dt, leg.duration - self._leg_time)
            self._leg_time += step
            self.x, self.y, self.vx, self.vy, self.ax, self.ay = leg.sample(self._leg_time)
            self._orient(step)
            dt -= step
            if self._leg_time + 1e-12 >= leg.duration:
                self._legs.pop(0)
                self._leg_time = 0.0
            else:
                break
        if not self._legs and dt > 0:
            self.x += self.vx * dt
            self.y += self.vy * dt

    def _orient(self, dt: float) -> None:
        if math.hypot(self.vx, self.vy) < 1e-6:
            return
        heading = math.degrees(math.atan2(-self.vy, -self.vx))
        if self.departure is None and self.age >= self.touchdown_time:
            heading = 0.0
        turn = (heading - self._heading + 180) % 360 - 180
        self._heading += max(-540 * dt, min(540 * dt, turn))

    @property
    def pose(self) -> BeePose:
        speed = max(60 * self.size[0] / 50, math.hypot(self.vx, self.vy))
        tilt = self.vy / speed * (1 - self.folded)
        return BeePose(
            self.x,
            self.y,
            self.size,
            7 * tilt,
            self.folded,
            self.phase,
            self._heading,
        )


class NoBee(IdleBee):
    """A flower nothing visits: the disabled bee, and every secondary cursor's."""

    def _step(self, dt: float, head: tuple[float, float] | None) -> None:
        return
