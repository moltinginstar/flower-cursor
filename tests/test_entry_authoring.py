import math
import runpy
from pathlib import Path

import pytest

from flower_cursor.idle_motion import IDLE_ANGULAR_SPEED
from flower_cursor.lifecycle import CursorCell, FlowerInstance
from flower_cursor.shedding import SheddingFlower


@pytest.fixture(scope="module")
def authored():
  script = Path(__file__).resolve().parents[1] / "blender" / "entry_poses.py"
  return dict(runpy.run_path(str(script))["growth_idle_poses"]())


def test_leaf_growth_never_overshoots_idle_size(authored):
  growth = [p.leaf_unfurl * p.stem for n, p in authored.items() if n.startswith("grow_")]
  idle = [p.leaf_unfurl * p.stem for n, p in authored.items() if n.startswith("idle_")]
  assert len(growth) == 36
  assert len(idle) == 24
  assert growth == sorted(growth)
  assert max(growth) <= min(idle) + 1e-12
  assert min(idle) == pytest.approx(1)
  assert max(idle) - min(idle) < 1e-12
  assert authored["grow_35"].bloom > 0.995


def test_growth_uses_actual_runtime_motion(authored):
  flower = SheddingFlower(FlowerInstance.bud(1, CursorCell(0, 0)), wall_time_idle=True)
  previous = 0
  for name, pose in authored.items():
    if not name.startswith("grow_"):
      continue
    time = pose.idle_phase / IDLE_ANGULAR_SPEED
    flower.update(2 * (time - previous), wall_duration=time - previous)
    actual = flower.pose
    for channel in ("stem", "bloom", "petal_open", "head_lean", "maturity"):
      assert getattr(pose, channel) == pytest.approx(getattr(actual, channel), abs=1e-10)
    assert pose.leaf_unfurl == pytest.approx(flower.flower.pose.leaf_unfurl)
    previous = time


def test_idle_keeps_motion_without_size_pulse(authored):
  idle = [p for n, p in authored.items() if n.startswith("idle_")]
  assert [p.idle_phase for p in idle] == pytest.approx([i * math.tau / 24 for i in range(24)])
  assert max(p.head_lean for p in idle) - min(p.head_lean for p in idle) > 0.15
  assert max(p.petal_open for p in idle) - min(p.petal_open for p in idle) > 0.01
  assert max(abs(idle[(i + 1) % 24].head_lean - p.head_lean) for i, p in enumerate(idle)) < 0.04
