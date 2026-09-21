import pytest

from flower_cursor.lifecycle import CursorCell, FlowerLifecycle

A = CursorCell(4, 8)
B = CursorCell(4, 20)
C = CursorCell(12, 3)


def advance(lifecycle: FlowerLifecycle, duration: float) -> None:
    remaining = duration
    while remaining > 0:
        step = min(remaining, 1 / 60)
        lifecycle.update(step)
        remaining -= step


def test_new_flower_sprouts_stem_then_leaves_then_bloom() -> None:
    lifecycle = FlowerLifecycle()
    flower = lifecycle.move_to(A)
    advance(lifecycle, 0.05)
    assert flower.pose.stem > 0
    assert flower.pose.bloom == 0
    assert flower.pose.leaf_unfurl == 0
    assert flower.pose.vitality == pytest.approx(1.0)

    advance(lifecycle, 0.25)
    assert flower.pose.leaf_unfurl > 0
    assert flower.pose.bloom == 0
    advance(lifecycle, 0.30)
    assert flower.pose.stem > flower.pose.bloom > 0
    assert flower.pose.leaf_unfurl > flower.pose.bloom


@pytest.mark.parametrize("warmup", [0.25, 0.55, 2.0])
def test_withering_preserves_pose_momentum_and_grown_tissue(warmup: float) -> None:
    lifecycle = FlowerLifecycle()
    flower = lifecycle.move_to(A)
    advance(lifecycle, warmup)
    interrupted = flower.pose
    velocities = (flower._stem.velocity, flower._bloom.velocity, flower._maturity.velocity)

    lifecycle.move_to(B)

    assert flower.active is False
    assert flower.pose == interrupted
    assert velocities == (
        flower._stem.velocity,
        flower._bloom.velocity,
        flower._maturity.velocity,
    )
    advance(lifecycle, 0.6)
    assert flower.pose.stem >= interrupted.stem
    assert flower.pose.maturity >= interrupted.maturity
    assert flower.pose.leaf_unfurl >= interrupted.leaf_unfurl
    assert flower.pose.vitality < interrupted.vitality
    assert flower.pose.collapse > 0


def test_exit_folds_before_retracting_without_an_opacity_fade() -> None:
    lifecycle = FlowerLifecycle()
    flower = lifecycle.move_to(A)
    advance(lifecycle, 2.0)
    lifecycle.move_to(B)
    poses = []
    for _ in range(120):
        lifecycle.update(1 / 60)
        poses.append(flower.pose)
        if flower.dead:
            break

    assert all(pose.opacity == 1.0 for pose in poses)
    assert any(0 < pose.petal_curl < 1 and pose.collapse == 0 for pose in poses)
    assert any(pose.collapse == 1 and pose.retraction == 0 for pose in poses)
    assert any(0 < pose.retraction < 1 for pose in poses)
    assert all(pose.collapse == 1 for pose in poses if pose.retraction > 0)
    assert [p.collapse for p in poses] == sorted(p.collapse for p in poses)
    assert flower.dead
    assert flower.pose.retraction == 1
    assert len(poses) / 60 == pytest.approx(1.0, abs=1 / 60)
    first_fold = next(i + 1 for i, pose in enumerate(poses) if pose.collapse == 1)
    assert first_fold / 60 == pytest.approx(0.85, abs=1 / 60)
    assert flower.pose.stem > 0.9
    assert flower not in lifecycle.flowers


@pytest.mark.parametrize("warmup", [0.25, 0.55, 2.0])
@pytest.mark.parametrize("departure", [0.1, 0.5, 0.9, 0.95])
def test_returning_reverses_same_flower_without_resetting_state(
    warmup: float,
    departure: float,
) -> None:
    lifecycle = FlowerLifecycle()
    flower = lifecycle.move_to(A)
    advance(lifecycle, warmup)
    lifecycle.move_to(B)
    advance(lifecycle, departure)
    withering_pose = flower.pose
    velocity = flower._vitality.velocity

    returned = lifecycle.move_to(A)

    assert returned is flower
    assert returned.active is True
    assert returned.pose == withering_pose
    assert returned._vitality.velocity == velocity
    advance(lifecycle, 0.5)
    assert returned.pose.vitality > withering_pose.vitality
    assert returned.pose.collapse < withering_pose.collapse or withering_pose.collapse == 0
    assert returned.pose.retraction == 0
    advance(lifecycle, 2.0)
    assert returned.pose.bloom > 0.99
    assert returned.pose.petal_curl == 0


def test_return_after_departure_has_finished_creates_a_new_bud() -> None:
    lifecycle = FlowerLifecycle()
    old = lifecycle.move_to(A)
    advance(lifecycle, 2.0)
    lifecycle.move_to(B)
    advance(lifecycle, 1.05)
    assert old.dead
    returned = lifecycle.move_to(A)
    assert returned is not old
    assert returned.identifier != old.identifier
    assert returned.pose.stem == returned.pose.bloom == 0


def test_repeated_reversals_keep_the_existing_instances() -> None:
    lifecycle = FlowerLifecycle()
    flowers = {}
    for cell in (A, B, A, B, A, B, A):
        flower = lifecycle.move_to(cell)
        if cell in flowers:
            assert flower is flowers[cell]
        flowers[cell] = flower
        advance(lifecycle, 0.1)
    assert len(lifecycle.flowers) == 2
    assert lifecycle.active is flowers[A]


def test_rapid_moves_keep_multiple_independent_flowers() -> None:
    lifecycle = FlowerLifecycle()
    lifecycle.move_to(A)
    advance(lifecycle, 0.08)
    lifecycle.move_to(B)
    advance(lifecycle, 0.08)
    lifecycle.move_to(C)
    assert [flower.cell for flower in lifecycle.flowers] == [A, B, C]
    assert lifecycle.active.cell == C
    advance(lifecycle, 3.0)
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
