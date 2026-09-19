import pytest

from flower_cursor.lifecycle import CursorCell, FlowerLifecycle

A = CursorCell(4, 8)
B = CursorCell(4, 20)
C = CursorCell(12, 3)


def test_new_flower_sprouts_stem_before_bloom() -> None:
    lifecycle = FlowerLifecycle()
    lifecycle.move_to(A)

    lifecycle.update(0.05)
    active = lifecycle.active
    assert active is not None
    pose = active.pose

    assert pose.stem > pose.bloom > 0
    assert pose.vitality == pytest.approx(1.0)


def test_withering_starts_from_the_interrupted_pose() -> None:
    lifecycle = FlowerLifecycle(retain_departures=True)
    flower = lifecycle.move_to(A)
    lifecycle.update(0.18)
    interrupted = flower.pose

    lifecycle.move_to(B)

    assert flower.active is False
    assert flower.pose == interrupted
    lifecycle.update(0.02)
    assert 0 < flower.pose.bloom < 1
    assert 0 < flower.pose.stem < 1
    assert abs(flower.pose.bloom - interrupted.bloom) < 0.1
    lifecycle.update(0.12)
    assert flower.pose.bloom < interrupted.bloom
    assert flower.pose.stem < interrupted.stem


def test_returning_to_a_withering_cell_reverses_the_same_flower() -> None:
    lifecycle = FlowerLifecycle(retain_departures=True)
    flower = lifecycle.move_to(A)
    lifecycle.update(0.2)
    lifecycle.move_to(B)
    lifecycle.update(0.05)
    withering_pose = flower.pose

    returned = lifecycle.move_to(A)

    assert returned is flower
    assert returned.active is True
    assert returned.pose == withering_pose
    lifecycle.update(0.02)
    assert abs(returned.pose.bloom - withering_pose.bloom) < 0.1
    lifecycle.update(0.2)
    assert returned.pose.bloom > withering_pose.bloom


def test_rapid_moves_keep_multiple_independent_flowers() -> None:
    lifecycle = FlowerLifecycle(retain_departures=True)
    lifecycle.move_to(A)
    lifecycle.update(0.08)
    lifecycle.move_to(B)
    lifecycle.update(0.08)
    lifecycle.move_to(C)

    assert [flower.cell for flower in lifecycle.flowers] == [A, B, C]
    active = lifecycle.active
    assert active is not None
    assert active.cell == C

    lifecycle.update(3.0)
    assert [flower.cell for flower in lifecycle.flowers] == [C]


def test_cursor_move_can_remove_departing_flower_immediately() -> None:
    lifecycle = FlowerLifecycle(retain_departures=False)
    lifecycle.move_to(A)
    lifecycle.update(0.2)

    lifecycle.move_to(B)

    assert [flower.cell for flower in lifecycle.flowers] == [B]


def test_negative_time_is_rejected() -> None:
    lifecycle = FlowerLifecycle()
    lifecycle.move_to(A)

    with pytest.raises(ValueError, match="non-negative"):
        lifecycle.update(-0.01)
