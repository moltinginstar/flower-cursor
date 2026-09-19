"""Event-driven flower cursor runtime independent of Kitty observation."""

from __future__ import annotations

from dataclasses import dataclass

from .lifecycle import CursorCell, FlowerLifecycle, FlowerPose
from .pose_library import PoseAsset, PoseLibrary


@dataclass(frozen=True, slots=True)
class FlowerVisual:
    identifier: int
    cell: CursorCell
    pose: FlowerPose
    asset: PoseAsset


class FlowerCursorRuntime:
    """Turn explicit cursor events and elapsed time into render-ready visuals."""

    def __init__(
        self,
        pose_library: PoseLibrary | None = None,
        *,
        retain_departures: bool = True,
    ) -> None:
        self.lifecycle = FlowerLifecycle(retain_departures=retain_departures)
        self.pose_library = pose_library or PoseLibrary.default()

    @property
    def cursor(self) -> CursorCell | None:
        active = self.lifecycle.active
        return active.cell if active is not None else None

    def move_to(self, cell: CursorCell) -> None:
        self.lifecycle.move_to(cell)

    def update(self, duration: float) -> None:
        self.lifecycle.update(duration)

    def visuals(self) -> tuple[FlowerVisual, ...]:
        return tuple(
            FlowerVisual(
                identifier=flower.identifier,
                cell=flower.cell,
                pose=flower.pose,
                asset=self.pose_library.select(flower.pose),
            )
            for flower in self.lifecycle.flowers
        )
