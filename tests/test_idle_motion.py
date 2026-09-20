import math
from dataclasses import replace

import pytest

from flower_cursor.idle_motion import IDLE_ANGULAR_SPEED, idle_pose
from flower_cursor.lifecycle import CursorCell, FlowerInstance


def mature_pose():
    flower = FlowerInstance.bud(1, CursorCell(0, 0))
    flower.update(8.0)
    flower.idle_phase = 0.0
    return flower.pose


def test_idle_period_is_independent_wall_time_and_twice_current_default_period():
    pose = mature_pose()
    period = math.tau / IDLE_ANGULAR_SPEED
    assert period == pytest.approx(2 * math.tau / (1.7 * 2.0))
    assert idle_pose(pose, 0.0) == idle_pose(pose, period)


def test_idle_is_slightly_more_visible_with_distinct_leaf_and_petal_motion():
    pose = mature_pose()
    samples = [idle_pose(pose, i * math.tau / (120 * IDLE_ANGULAR_SPEED)) for i in range(120)]
    assert 0.1025 < max(p.idle_sway for p in samples) < 0.11875
    for index, sample in enumerate(samples):
        phase = index * math.tau / 120
        previous_sway = (0.082 * math.sin(phase) + 0.012 * math.sin(2 * phase)) * pose.vitality
        assert sample.idle_sway == pytest.approx(previous_sway * 1.25)
    assert min(p.leaf_unfurl for p in samples) < 0.95
    assert min(p.petal_open for p in samples) < 0.97
    assert all(p.stem == pose.stem and p.vitality == pose.vitality for p in samples)
    first = idle_pose(pose, 0.0)
    half = idle_pose(pose, math.pi / IDLE_ANGULAR_SPEED)
    assert first.petal_open > half.petal_open
    assert first.leaf_unfurl > half.leaf_unfurl


def test_extra_sway_grows_gradually_with_maturity_and_weakens_with_vitality():
    pose = mature_pose()
    phase = IDLE_ANGULAR_SPEED
    baseline = 0.082 * math.sin(phase) + 0.012 * math.sin(2 * phase)
    young = idle_pose(replace(pose, bloom=0.5), 1.0)
    fading = idle_pose(replace(pose, vitality=0.5), 1.0)
    assert young.idle_sway == pytest.approx(baseline * 1.015625)
    assert fading.idle_sway == pytest.approx(baseline * 0.5 * 1.125)


def test_idle_motion_does_not_change_a_closed_bud():
    pose = FlowerInstance.bud(1, CursorCell(0, 0)).pose
    result = idle_pose(pose, 1.0)
    assert result.petal_open == pose.petal_open
    assert result.leaf_unfurl == pose.leaf_unfurl
    assert result.stem == pose.stem
    phase = IDLE_ANGULAR_SPEED
    assert result.idle_sway == pytest.approx(0.082 * math.sin(phase) + 0.012 * math.sin(2 * phase))
