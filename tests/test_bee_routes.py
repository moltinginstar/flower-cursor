import math
import random

import pytest

from flower_cursor.bee import APPROACH_TIME, INTERVAL_JITTER, REST_CONTACT, IdleBee
from flower_cursor.geometry import ViewportGeometry
from flower_cursor.lifecycle import CursorCell

CELL = CursorCell(8, 12)


def visitor(seed=9, width=1600, height=990, head=None, size=(50, 45)):
  bee = IdleBee(random.Random(seed))
  bee.set_geometry(ViewportGeometry(30, 100, width, height), size)
  bee.move_to(CELL)
  bee.idle = bee.wait
  bee.update(0, head or (width * 0.4, height * 0.55))
  assert bee.visible
  return bee


def advance(bee, duration, cadence=1 / 60, head=None):
  while duration > 1e-10:
    dt = min(cadence, duration)
    bee.update(dt, head or bee.head)
    duration -= dt


def state(bee):
  return bee.x, bee.y, bee.vx, bee.vy, bee.ax, bee.ay


def angle_error(left, right):
  return (left - right + 180) % 360 - 180


def outside_edges(bee):
  assert bee.viewport is not None
  return (
    bee.x + bee.radius < 0,
    bee.x - bee.radius > bee.viewport.width,
    bee.y + bee.radius < 0,
    bee.y - bee.radius > bee.viewport.height,
  )


def test_seed_repeats_whole_visit_but_default_generators_are_fresh():
  first, second = visitor(81), visitor(81)
  assert first.rng is not second.rng
  assert first.rng.getstate() == second.rng.getstate()
  assert first.pose == second.pose
  for duration in (0.2, 1.6, 1.1, 1.4):
    advance(first, duration)
    advance(second, duration)
    assert state(first) == state(second)
    assert first.pose == second.pose
  first.leave()
  second.leave()
  assert first._exit == second._exit
  advance(first, 1.1)
  advance(second, 1.1)
  assert first.pose == second.pose
  assert IdleBee().rng.getstate() != IdleBee().rng.getstate()


def test_all_source_and_independent_exit_edges_points_and_timings_vary():
  sources, exits, pairs, starts, endpoints, timing, offsets, bends = (set() for _ in range(8))
  for seed in range(128):
    bee = visitor(seed)
    assert sum(outside_edges(bee)) == 1
    sources.add(bee._source[0])
    starts.add((bee.x, bee.y))
    offsets.add(bee.landing_offset)
    bends.add(bee._bend)
    assert 0.92 * APPROACH_TIME <= bee.approach_time <= 1.12 * APPROACH_TIME
    assert 4.8 <= bee.rest_time <= 7.2
    timing.add(bee.approach_time)
    bee.leave()
    assert bee._exit is not None
    assert 1.6 <= bee.departure_time <= 2.3
    exits.add(bee._exit[0])
    pairs.add((bee._source[0], bee._exit[0]))
    endpoints.add(bee._edge_point(bee._exit))
  assert sources == exits == {0, 1, 2, 3}
  assert len(pairs) == 16
  assert all(len(values) == 128 for values in (starts, endpoints, timing, offsets, bends))


def test_rng_is_not_consumed_by_frames_head_tracking_resize_or_repeated_leave():
  bee = visitor()
  sampled = bee.rng.getstate()
  advance(bee, 0.4, cadence=1 / 144)
  bee.update(0, (300, 200))
  bee.set_geometry(ViewportGeometry(20, 60, 720, 480), (36, 32))
  advance(bee, 0.4, head=(300, 200))
  assert bee.rng.getstate() == sampled
  bee.leave()
  sampled = bee.rng.getstate()
  exit_point = bee._exit
  bee.leave()
  bee.move_to(CursorCell(2, 3))
  bee.set_geometry(ViewportGeometry(20, 60, 900, 600), (44, 40))
  advance(bee, 0.4)
  assert bee.rng.getstate() == sampled
  assert bee._exit == exit_point


@pytest.mark.parametrize("seed", range(32))
@pytest.mark.parametrize("extent", [(1600, 990), (2400, 360), (320, 1400), (160, 120)])
@pytest.mark.parametrize("fraction", [(0.05, 0.1), (0.95, 0.1), (0.05, 0.9), (0.95, 0.9), (0.4, 0.55)])
def test_routes_land_face_travel_and_leave_at_all_aspects(seed, extent, fraction):
  head = tuple(dimension * coordinate for dimension, coordinate in zip(extent, fraction))
  bee = visitor(seed, *extent, head=head)
  original_size = bee.size
  saw_inside = False
  while bee.age < bee.approach_time + bee.rest_time + 2.5:
    heading = bee.pose.heading
    bee.update(1 / 60, bee.head)
    assert all(math.isfinite(value) for value in state(bee))
    assert bee.size == original_size
    assert abs(bee.pose.heading - heading) <= 9 + 1e-8
    saw_inside |= not bee.offscreen
    speed = math.hypot(bee.vx, bee.vy)
    if speed > 20 and (bee.departure is not None or bee.age < bee.touchdown_time):
      travel = math.degrees(math.atan2(-bee.vy, -bee.vx))
      assert abs(angle_error(bee.pose.heading, travel)) < 90, (
        seed,
        extent,
        bee.age,
        bee.departure,
        speed,
        bee.pose.heading,
        travel,
      )
    if bee.touchdown_time <= bee.age <= bee.approach_time + bee.rest_time:
      assert (bee.x, bee.y) == pytest.approx(bee.landing, abs=1e-8)
      assert (bee.vx, bee.vy) == pytest.approx((0, 0), abs=1e-8)
      assert angle_error(bee.pose.heading, 0) == pytest.approx(0, abs=1e-7)
    if not bee.visible:
      assert bee.offscreen
      assert outside_edges(bee)[bee._exit[0]]
      low, high = (bee.interval * factor for factor in INTERVAL_JITTER)
      assert bee.idle <= 1 / 60 and low <= bee.wait <= high
      break
  else:
    pytest.fail("visit exceeded its sampled timing budget")
  assert saw_inside


@pytest.mark.parametrize("seed", range(8))
@pytest.mark.parametrize("size", [(50, 45), (23, 21)])
def test_landing_variation_stays_in_small_bloom_contact_ellipse(seed, size):
  bee = visitor(seed, size=size)
  advance(bee, bee.approach_time)
  contact = (
    bee.x + (REST_CONTACT[0] - 0.5) * size[0],
    bee.y + (REST_CONTACT[1] - 0.5) * size[1],
  )
  assert contact == pytest.approx(tuple(h + d for h, d in zip(bee.head, bee.landing_offset)))
  dx, dy = bee.landing_offset
  assert (dx / (size[0] / 50)) ** 2 + (dy / (0.4 * size[1] / 45)) ** 2 <= 1
  assert bee.folded == pytest.approx(1)


@pytest.mark.parametrize("seed", [0, 1, 3, 8, 41])
def test_sampled_leg_boundaries_are_c2_including_final_alignment_and_departure(seed):
  bee = visitor(seed)
  for departure in (False, True):
    if departure:
      advance(bee, 0.7)
      bee.leave()
    for previous, following in zip(bee._legs, bee._legs[1:]):
      assert previous.sample(previous.duration) == pytest.approx(following.sample(0), abs=1e-8)


@pytest.mark.parametrize("seed", [0, 1, 3, 8, 41])
@pytest.mark.parametrize("age", [0.02, 0.7, 2.6, 3.1, 4.5])
def test_seeded_interruptions_and_resize_retain_full_kinematics_and_heading(seed, age):
  bee = visitor(seed)
  advance(bee, age)
  before, pose = state(bee), bee.pose
  bee.leave()
  bee.set_geometry(ViewportGeometry(20, 40, 540, 720), (36, 32))
  bee.update(0, (90, 150))
  assert state(bee) == before
  assert bee.pose == pose
  epsilon = 1e-7
  bee.update(epsilon, (90, 150))
  assert (bee.x, bee.y) == pytest.approx((before[0] + before[2] * epsilon, before[1] + before[3] * epsilon), abs=1e-8)
  assert (bee.vx, bee.vy) == pytest.approx((before[2] + before[4] * epsilon, before[3] + before[5] * epsilon), abs=1e-7)
  assert abs(bee.pose.heading - pose.heading) <= 540 * epsilon + 1e-8
  advance(bee, 3)
  assert not bee.visible and bee.offscreen


@pytest.mark.parametrize("seed", range(16))
@pytest.mark.parametrize("age", [0.2, 1.0, 2.6, 3.2])
def test_interrupted_departures_do_not_fly_backwards(seed, age):
  bee = visitor(seed)
  advance(bee, age)
  bee.leave()
  for _ in range(3 * 120):
    before = bee.pose.heading
    bee.update(1 / 120, bee.head)
    assert abs(bee.pose.heading - before) <= 4.5 + 1e-8
    if not bee.visible:
      break
    if math.hypot(bee.vx, bee.vy) > 20:
      travel = math.degrees(math.atan2(-bee.vy, -bee.vx))
      assert abs(angle_error(bee.pose.heading, travel)) < 90
  else:
    pytest.fail("interrupted visitor never left")


@pytest.mark.parametrize("seed", [0, 1, 3, 8, 41])
def test_seeded_routes_are_cadence_independent_through_arrival_and_departure(seed):
  bees = [visitor(seed) for _ in range(4)]
  cadences = [1 / 30, 1 / 60, 1 / 144, 0.037]
  for duration in (0.41, 0.83, 1.11, 1.7):
    for bee, cadence in zip(bees, cadences):
      advance(bee, duration, cadence)
    for bee in bees[1:]:
      assert state(bee) == pytest.approx(state(bees[0]), abs=1e-7)
      assert angle_error(bee.pose.heading, bees[0].pose.heading) == pytest.approx(0, abs=2)
      assert bee.rng.getstate() == bees[0].rng.getstate()
  for bee in bees:
    bee.leave()
  for bee, cadence in zip(bees, cadences):
    advance(bee, 0.8, cadence)
  for bee in bees[1:]:
    assert state(bee) == pytest.approx(state(bees[0]), abs=1e-7)
    assert angle_error(bee.pose.heading, bees[0].pose.heading) == pytest.approx(0, abs=2)
    assert bee._exit == bees[0]._exit
