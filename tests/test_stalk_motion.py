import math
from itertools import pairwise

import pytest

from flower_cursor.stalk_motion import wilt_point, wilt_progress


def test_root_and_healthy_shape_are_unchanged():
  for progress in (0.0, 0.2, 0.5, 0.8, 1.0):
    assert wilt_point(0.0, progress) == (0.0, 0.0, 0.0)
  for t in (0.0, 0.25, 0.5, 0.75, 1.0):
    assert wilt_point(t, 0.0) == (0.0, 0.0, t)
  assert wilt_progress(1.0) == wilt_progress(0.55) == 0.0
  assert wilt_progress(0.02) == wilt_progress(0.0) == 1.0


def test_upper_stem_yields_before_lower_stem():
  for progress in (0.2, 0.4, 0.6):
    lower = wilt_point(0.25, progress)[2] / 0.25
    upper = wilt_point(1.0, progress)[2]
    assert upper < lower
    side, forward, _ = wilt_point(0.001, progress)
    assert abs(side) / 0.001 < 0.0001
    assert forward / 0.001 < 0.0001


def test_stalk_settles_near_root_without_sideways_plop():
  side, forward, height = wilt_point(1.0, 1.0)
  assert abs(side) < 0.03
  assert 0 <= forward < 0.1
  assert 0 < height < 0.1
  for step in range(21):
    progress = step / 20
    for point in range(21):
      side, forward, height = wilt_point(point / 20, progress)
      assert abs(side) < 0.1
      assert height >= 0
      assert all(math.isfinite(v) for v in (side, forward, height))


@pytest.mark.parametrize("t", [0.1, 0.25, 0.5, 0.75, 1.0])
def test_sag_is_continuous_and_never_rebounds_upward(t):
  heights = [wilt_point(t, i / 100)[2] for i in range(101)]
  assert heights == sorted(heights, reverse=True)
  assert max(a - b for a, b in pairwise(heights)) < 0.04
