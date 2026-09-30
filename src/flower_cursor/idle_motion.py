"""Experimental wall-time idle motion shared by authored study poses."""

from __future__ import annotations

import math
from dataclasses import replace

from .lifecycle import FlowerPose

IDLE_ANGULAR_SPEED = 1.7


def idle_pose(pose: FlowerPose, wall_time: float) -> FlowerPose:
  phase = (wall_time * IDLE_ANGULAR_SPEED) % math.tau
  strength = pose.bloom**4 * pose.vitality
  sway = (0.082 * math.sin(phase) + 0.012 * math.sin(2 * phase)) * pose.vitality
  sway *= 1.0 + 0.25 * strength
  breathing = 0.016 * (1.0 - math.cos(phase)) + 0.004 * (1.0 - math.cos(2 * phase))
  leaf_motion = 0.025 * (1.0 - math.cos(phase + 0.7))
  leaf_motion += 0.008 * (1.0 - math.cos(2 * phase - 0.4))
  return replace(
    pose,
    idle_phase=phase,
    idle_sway=sway,
    head_lean=pose.head_lean - pose.idle_sway + sway,
    petal_open=max(0.0, pose.bloom - breathing * strength),
    leaf_unfurl=pose.leaf_unfurl * (1.0 - leaf_motion * strength),
  )
