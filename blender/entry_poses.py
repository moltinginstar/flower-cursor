"""Coherent growth and idle poses shared by every flower species."""

from __future__ import annotations

import math
from dataclasses import replace

from flower_cursor.idle_motion import IDLE_ANGULAR_SPEED
from flower_cursor.lifecycle import CursorCell, FlowerInstance
from flower_cursor.runtime import DEFAULT_ANIMATION_SPEED
from flower_cursor.shedding import SheddingFlower


def growth_idle_poses():
  flower = SheddingFlower(FlowerInstance.bud(1, CursorCell(0, 0)), wall_time_idle=True)
  previous = 0.0
  times = [index / 48 for index in range(24)]
  times.extend(0.5 + index * 0.05 for index in range(12))
  for index, time in enumerate(times):
    flower.update(DEFAULT_ANIMATION_SPEED * (time - previous), wall_duration=time - previous)
    yield f"grow_{index:02d}", replace(flower.pose, leaf_unfurl=flower.flower.pose.leaf_unfurl)
    previous = time
  period = math.tau / IDLE_ANGULAR_SPEED
  mature = FlowerInstance.bud(1, CursorCell(0, 0))
  mature.update(8)
  flower = SheddingFlower(mature, wall_time_idle=True)
  flower.update(2 * DEFAULT_ANIMATION_SPEED * period, wall_duration=2 * period)
  for index in range(24):
    yield (
      f"idle_{index:02d}",
      replace(
        flower.pose,
        idle_phase=index * math.tau / 24,
        leaf_unfurl=flower.flower.pose.leaf_unfurl,
      ),
    )
    flower.update(DEFAULT_ANIMATION_SPEED * period / 24, wall_duration=period / 24)
