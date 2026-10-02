import pytest

from flower_cursor.lifecycle import CursorCell, FlowerInstance

A = CursorCell(4, 8)


def advance(flower: FlowerInstance, duration: float) -> None:
  remaining = duration
  while remaining > 0:
    step = min(remaining, 1 / 60)
    flower.update(step)
    remaining -= step


def test_new_flower_sprouts_stem_then_leaves_then_bloom() -> None:
  flower = FlowerInstance.bud(1, A)
  advance(flower, 0.05)
  assert flower.pose.stem > 0
  assert flower.pose.bloom == 0
  assert flower.pose.leaf_unfurl == 0
  assert flower.pose.vitality == pytest.approx(1.0)

  advance(flower, 0.25)
  assert flower.pose.leaf_unfurl > 0
  assert flower.pose.bloom == 0
  advance(flower, 0.30)
  assert flower.pose.stem > flower.pose.bloom > 0
  assert flower.pose.leaf_unfurl > flower.pose.bloom


@pytest.mark.parametrize("warmup", [0.25, 0.55, 2.0])
def test_withering_preserves_pose_momentum_and_grown_tissue(warmup: float) -> None:
  flower = FlowerInstance.bud(1, A)
  advance(flower, warmup)
  interrupted = flower.pose
  velocities = (flower._stem.velocity, flower._bloom.velocity, flower._maturity.velocity)

  flower.begin_withering()

  assert flower.active is False
  assert flower.pose == interrupted
  assert velocities == (
    flower._stem.velocity,
    flower._bloom.velocity,
    flower._maturity.velocity,
  )
  advance(flower, 0.6)
  assert flower.pose.stem >= interrupted.stem
  assert flower.pose.maturity >= interrupted.maturity
  assert flower.pose.leaf_unfurl >= interrupted.leaf_unfurl
  assert flower.pose.vitality < interrupted.vitality
  assert flower.pose.collapse > 0


def test_exit_folds_before_retracting() -> None:
  flower = FlowerInstance.bud(1, A)
  advance(flower, 2.0)
  flower.begin_withering()
  poses = []
  for _ in range(120):
    flower.update(1 / 60)
    poses.append(flower.pose)
    if flower.dead:
      break

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


@pytest.mark.parametrize("warmup", [0.25, 0.55, 2.0])
@pytest.mark.parametrize("departure", [0.1, 0.5, 0.9, 0.95])
def test_returning_reverses_the_flower_without_resetting_state(
  warmup: float,
  departure: float,
) -> None:
  flower = FlowerInstance.bud(1, A)
  advance(flower, warmup)
  flower.begin_withering()
  advance(flower, departure)
  withering_pose = flower.pose
  velocity = flower._vitality.velocity

  flower.restore()

  assert flower.active is True
  assert flower.pose == withering_pose
  assert flower._vitality.velocity == velocity
  advance(flower, 0.5)
  assert flower.pose.vitality > withering_pose.vitality
  assert flower.pose.collapse < withering_pose.collapse or withering_pose.collapse == 0
  assert flower.pose.retraction == 0
  advance(flower, 2.0)
  assert flower.pose.bloom > 0.99
  assert flower.pose.petal_curl == 0


def test_negative_time_is_rejected() -> None:
  flower = FlowerInstance.bud(1, A)
  with pytest.raises(ValueError, match="non-negative"):
    flower.update(-0.01)
