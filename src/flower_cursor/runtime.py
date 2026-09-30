"""The visual a renderer draws, and how fast the animation runs.

What is left here after the flat-atlas runtime was retired: the dataclass every
compositor and renderer passes around, and the speed the layered runtime plays
its poses at.
"""

from __future__ import annotations

from dataclasses import dataclass

from .lifecycle import CursorCell, FlowerPose
from .pose_library import PoseAsset

DEFAULT_ANIMATION_SPEED = 2.0


@dataclass(frozen=True, slots=True)
class FlowerVisual:
  identifier: int
  cell: CursorCell
  pose: FlowerPose
  asset: PoseAsset
  samples: tuple[tuple[PoseAsset, float], ...] = ()
  # Set when several visuals are known to composite to the same frame, so the
  # renderer can draw one and place it many times.
  appearance: object | None = None
