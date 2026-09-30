import hashlib
import json
import math
import random

import pytest

from flower_cursor.bee import (
  BeePose,
  IdleBee,
)
from flower_cursor.bee_art import ASSETS, BeeCompositor
from flower_cursor.geometry import FlowerLayout, ViewportGeometry
from flower_cursor.layered import BeeVisual, LayeredCompositor, LayeredRuntime
from flower_cursor.lifecycle import CursorCell
from flower_cursor.observer import DEFAULT_MANIFEST

A, B = CursorCell(8, 12), CursorCell(8, 22)
VIEWPORT = ViewportGeometry(30, 100, 1600, 990)
HEAD = (192.0, 250.0)


def advance(runtime, duration, step=1 / 60):
  while duration > 1e-10:
    dt = min(step, duration)
    if isinstance(runtime, IdleBee):
      runtime.update(dt, HEAD)
    else:
      runtime.update(dt)
    duration -= dt


def measured_bee(wait=10.0):
  bee = IdleBee(random.Random(9), interval=wait)
  bee.set_geometry(VIEWPORT, (50, 45))
  bee.move_to(A)
  # Every wait is randomised around the interval; these tests measure the
  # visit rather than the wait, so they pin the first one and read `wait`
  # for the ones the bee samples for itself.
  bee.wait = wait
  return bee


def test_visit_needs_the_whole_wait_and_is_not_a_keypress_effect():
  bee = measured_bee()
  advance(bee, bee.wait - 0.1)
  assert not bee.visible
  bee.move_to(A)
  advance(bee, 0.1)
  assert bee.visible and bee.cell == A
  assert bee.x - bee.radius > VIEWPORT.width
  assert bee.age == 0
  assert bee.pose.size == (50, 45)
  for i in range(240):
    bee.move_to(A if i % 2 else B)
    bee.update(1 / 60, HEAD)
  assert not bee.visible
  assert bee.generation == 1


@pytest.mark.parametrize("age", [0.2, 1.8, 3.6, 5.0])
def test_interruptions_preserve_position_velocity_pose_pixels_and_old_anchor(age):
  bee = measured_bee()
  advance(bee, 10 + age)
  before = bee.pose
  velocity = (bee.vx, bee.vy)
  compositor = BeeCompositor()
  pixels = compositor.frame(before).tobytes()
  bee.move_to(B)
  assert bee.pose == before
  assert (bee.vx, bee.vy) == velocity
  assert compositor.frame(bee.pose).tobytes() == pixels
  assert bee.cell == A and bee.target == B
  epsilon = 1e-6
  bee.update(epsilon, (1200, 800))
  assert (bee.x, bee.y) == pytest.approx((before.x + velocity[0] * epsilon, before.y + velocity[1] * epsilon), abs=1e-7)
  assert bee.head == HEAD
  advance(bee, 4)
  assert not bee.visible
  assert bee.offscreen


def test_natural_departure_removes_only_after_entire_sprite_is_offscreen():
  bee = measured_bee()
  advance(bee, 10)
  compositor = BeeCompositor()
  seen_inside = seen_departure = False
  for _ in range(15 * 120):
    previous = bee.pose
    bee.update(1 / 120, HEAD)
    seen_inside |= 0 < bee.x < VIEWPORT.width
    seen_departure |= bee.departure is not None
    assert bee.size == (50, 45)
    if not bee.visible:
      frame = compositor.frame(bee.pose)
      assert bee.offscreen
      assert (
        round(bee.x - frame.width / 2) + frame.width <= 0
        or round(bee.y - frame.height / 2) + frame.height <= 0
        or round(bee.x - frame.width / 2) >= VIEWPORT.width
        or round(bee.y - frame.height / 2) >= VIEWPORT.height
      )
      assert previous != bee.pose
      assert bee.idle <= 1 / 120
      break
  else:
    pytest.fail("natural visit never left the viewport")
  assert seen_inside and seen_departure
  advance(bee, bee.wait - 0.1)
  assert not bee.visible
  advance(bee, 0.2)
  assert bee.visible and bee.generation == 2


def test_aborted_landing_never_attaches_to_moving_target():
  bee = measured_bee()
  advance(bee, 12)
  assert bee.folded == 0
  bee.move_to(B)
  for i in range(240):
    bee.move_to(CursorCell(i % 12, i))
    bee.update(1 / 60, (1400, 800))
    assert bee.folded == 0
    assert bee.cell in (A, None)
    assert bee.head == HEAD
  assert not bee.visible


def test_screen_reset_and_suspend_remove_visitor_without_replaying():
  bee = measured_bee()
  advance(bee, 15)
  bee.reset()
  assert not bee.visible and bee.target is None
  assert (bee.vx, bee.vy) == (0, 0)
  bee.move_to(B)
  advance(bee, bee.wait - 0.1)
  assert not bee.visible
  advance(bee, 0.2)
  assert bee.visible
  bee.update(60, HEAD)
  assert not bee.visible and bee.target == B
  assert bee.idle == 0
  advance(bee, bee.wait - 0.1)
  assert not bee.visible
  advance(bee, 0.2)
  assert bee.visible


def test_missing_geometry_or_head_inhibits_visit_and_geometry_loss_restarts_the_wait():
  bee = IdleBee(interval=10.0)
  bee.move_to(A)
  bee.wait = 10.0
  advance(bee, 20)
  assert not bee.visible
  bee.set_geometry(VIEWPORT, (50, 45))
  for _ in range(20):
    bee.update(1)
  assert not bee.visible
  bee.update(0, HEAD)
  assert bee.visible and bee.generation == 1
  bee.set_geometry(None, (1, 1))
  assert not bee.visible and bee.target == A
  assert bee.idle == 0
  bee.set_geometry(VIEWPORT, (60, 55))
  advance(bee, bee.wait - 0.1)
  assert not bee.visible
  advance(bee, 0.2)
  assert bee.visible and bee.size == (60, 55)


@pytest.mark.parametrize("departing", [False, True])
def test_resize_retains_pose_velocity_age_and_visit_size(departing):
  bee = measured_bee()
  advance(bee, 13)
  if departing:
    bee.move_to(B)
    advance(bee, 0.1)
  before = bee.pose
  state = (bee.vx, bee.vy, bee.age, bee.departure, bee.generation)
  resized = ViewportGeometry(20, 60, 720, 480)
  bee.set_geometry(resized, (36, 32))
  assert bee.pose == before
  assert (bee.vx, bee.vy, bee.age, bee.departure, bee.generation) == state
  for _ in range(12 * 60):
    bee.update(1 / 60, (200, 100))
    assert bee.size == before.size
    if not bee.visible:
      break
  assert not bee.visible and bee.offscreen
  advance(bee, bee.wait + 0.1)
  assert bee.visible and bee.size == (36, 32)
  assert bee.offscreen


@pytest.mark.parametrize("duration", [-1, float("inf"), float("nan")])
def test_invalid_clock_values(duration):
  with pytest.raises(ValueError):
    IdleBee().update(duration)


def test_curved_approach_accelerates_brakes_and_only_drifts_during_hover():
  bee = measured_bee()
  advance(bee, 10)
  start = (bee.x, bee.y)
  assert (bee.vx, bee.vy) == (0, 0)
  samples = []
  while bee.age < bee.hover_time - 1e-10:
    bee.update(min(1 / 120, bee.hover_time - bee.age), HEAD)
    samples.append((bee.x, bee.y, math.hypot(bee.vx, bee.vy)))
  assert all(a[0] > b[0] for a, b in zip(samples, samples[1:]))
  assert samples[29][2] < max(s[2] for s in samples) / 2
  assert samples[-1][2] < max(s[2] for s in samples) / 20
  end = (bee.x, bee.y)
  deviation = max(
    abs((end[0] - start[0]) * (y - start[1]) - (end[1] - start[1]) * (x - start[0])) / math.dist(start, end)
    for x, y, _ in samples
  )
  assert deviation > 35
  hover_start = (bee.x, bee.y)
  previous = hover_start
  while bee.age < bee.landing_time - 1e-10:
    bee.update(min(1 / 120, bee.landing_time - bee.age), HEAD)
    assert bee.x < previous[0] and bee.y >= previous[1] - 1e-10
    assert math.hypot(bee.vx, bee.vy) < 45
    assert bee.folded == pytest.approx(0, abs=1e-12)
    previous = (bee.x, bee.y)
  assert math.dist(hover_start, previous) < 16


def test_touchdown_precedes_wing_tuck_and_takeoff_releases_before_departing():
  bee = measured_bee()
  advance(bee, 10)
  advance(bee, bee.landing_time)
  assert bee.folded == pytest.approx(0, abs=1e-12)
  advance(bee, bee.touchdown_time - bee.landing_time)
  assert (bee.x, bee.y) == pytest.approx(bee.landing, abs=1e-9)
  assert (bee.vx, bee.vy) == pytest.approx((0, 0), abs=1e-9)
  assert bee.folded == pytest.approx(0.35)
  advance(bee, bee.approach_time - bee.touchdown_time)
  assert bee.folded == pytest.approx(1)
  assert (bee.x, bee.y) == pytest.approx(bee.landing, abs=1e-9)
  before = (bee.x, bee.y)
  bee.leave()
  advance(bee, 0.25)
  assert bee.folded == 0
  assert bee.y < before[1] - 10
  assert abs(bee.x - before[0]) < before[1] - bee.y
  advance(bee, 2.5)
  assert not bee.visible and bee.offscreen


@pytest.mark.parametrize("boundary", ["_midpoint_time", "hover_time", "landing_time", "touchdown_time"])
def test_flight_segments_join_with_continuous_position_velocity_and_acceleration(boundary):
  bee = measured_bee()
  advance(bee, 10)
  boundary = getattr(bee, boundary)
  epsilon = 1e-7
  bee.update(boundary / 2, HEAD)
  bee.update(boundary / 2 - epsilon, HEAD)
  position = (bee.x, bee.y)
  velocity = (bee.vx, bee.vy)
  acceleration = (bee.ax, bee.ay)
  bee.update(2 * epsilon, HEAD)
  assert (bee.x, bee.y) == pytest.approx(tuple(p + v * 2 * epsilon for p, v in zip(position, velocity)), abs=1e-7)
  assert (bee.vx, bee.vy) == pytest.approx(tuple(v + a * 2 * epsilon for v, a in zip(velocity, acceleration)), abs=1e-5)
  assert (bee.ax, bee.ay) == pytest.approx(acceleration, abs=0.01)


@pytest.mark.parametrize("age", [0.2, 1.0, 2.6, 3.2, 4.0])
def test_interruptions_also_preserve_acceleration_and_do_not_reset_departure(age):
  bee = measured_bee()
  advance(bee, 10 + age)
  state = (bee.x, bee.y, bee.vx, bee.vy, bee.ax, bee.ay)
  bee.leave()
  assert (bee.x, bee.y, bee.vx, bee.vy, bee.ax, bee.ay) == state
  epsilon = 1e-6
  bee.update(epsilon, (1400, 800))
  assert (bee.vx, bee.vy) == pytest.approx((state[2] + state[4] * epsilon, state[3] + state[5] * epsilon), abs=1e-6)
  before = bee.pose
  departure = bee.departure
  bee.move_to(B)
  bee.move_to(A)
  assert bee.pose == before and bee.departure == departure


@pytest.mark.parametrize("departing", [False, True])
def test_resize_replans_without_a_position_velocity_or_acceleration_jump(departing):
  bee = measured_bee()
  advance(bee, 11)
  if departing:
    bee.leave()
    advance(bee, 0.1)
  state = (bee.x, bee.y, bee.vx, bee.vy, bee.ax, bee.ay)
  bee.set_geometry(ViewportGeometry(20, 60, 720, 480), (36, 32))
  bee.update(0, (200, 100))
  assert (bee.x, bee.y, bee.vx, bee.vy, bee.ax, bee.ay) == state
  epsilon = 1e-6
  bee.update(epsilon, (200, 100))
  assert (bee.x, bee.y) == pytest.approx((state[0] + state[2] * epsilon, state[1] + state[3] * epsilon), abs=1e-7)
  assert (bee.vx, bee.vy) == pytest.approx((state[2] + state[4] * epsilon, state[3] + state[5] * epsilon), abs=1e-6)


def test_analytic_flight_is_independent_of_render_cadence():
  bees = [measured_bee() for _ in range(3)]
  steps = (1 / 30, 1 / 60, 1 / 120)
  for bee in bees:
    advance(bee, 10)
  for duration in (1.5, 1.0, 1.5):
    for bee, step in zip(bees, steps):
      advance(bee, duration, step)
    expected = (bees[0].x, bees[0].y, bees[0].vx, bees[0].vy)
    for bee in bees[1:]:
      assert (bee.x, bee.y, bee.vx, bee.vy) == pytest.approx(expected, abs=1e-8)
  for bee in bees:
    bee.leave()
  for bee, step in zip(bees, steps):
    advance(bee, 0.5, step)
  for bee in bees[1:]:
    assert (bee.x, bee.y, bee.vx, bee.vy) == pytest.approx((bees[0].x, bees[0].y, bees[0].vx, bees[0].vy), abs=1e-8)


def test_heading_defaults_to_authored_neutral_and_bank_stays_velocity_aligned():
  assert BeePose(0, 0, (50, 45), 0, 0, 0).heading == 0
  bee = measured_bee()
  advance(bee, 10)
  climb = descent = False
  for _ in range(12 * 120):
    before = bee.pose.heading
    bee.update(1 / 120, HEAD)
    pose = bee.pose
    assert abs(pose.bank) <= 7
    assert abs(pose.heading - before) <= 4.5 + 1e-8
    assert pose.bank * bee.vy >= 0
    climb |= pose.bank < -0.2
    descent |= pose.bank > 0.2
  assert climb and descent


@pytest.mark.parametrize("head", [(100, 75), (1500, 75), (200, 900)])
def test_approach_arc_does_not_disappear_above_or_below_viewport(head):
  bee = measured_bee()
  advance(bee, 9.9)
  bee.update(0.1, head)
  for _ in range(4 * 120):
    bee.update(1 / 120, head)
    assert bee.y >= bee.radius - 1e-8
    assert bee.y <= VIEWPORT.height - bee.radius


def test_artwork_is_portable_distinct_transparent_and_continuous():
  data = json.loads((ASSETS / "manifest.json").read_text())
  assert len(data["frames"]) == 16 * len(data["headings"])
  assert 0 in data["headings"]
  hashes = {hashlib.sha256((ASSETS / name).read_bytes()).hexdigest() for name in data["frames"]}
  assert len(hashes) >= 12
  for index in range(16):
    image = BeeCompositor.image(index)
    assert image.size == (192, 192)
    assert image.getchannel(3).getextrema() == (0, 255)
  bee = measured_bee()
  advance(bee, 15)
  compositor = BeeCompositor()
  before = compositor.frame(bee.pose)
  bee.move_to(B)
  assert compositor.frame(bee.pose).tobytes() == before.tobytes()


def test_real_runtime_cleanup_independence_and_pixels(monkeypatch, tmp_path):
  monkeypatch.chdir(tmp_path)
  runtime = LayeredRuntime(DEFAULT_MANIFEST, bee_interval=10.0)
  control = LayeredRuntime(DEFAULT_MANIFEST, bee_interval=10.0)
  compositor = LayeredCompositor(DEFAULT_MANIFEST)
  runtime.set_geometry(VIEWPORT, FlowerLayout(runtime.pose_library.root_anchor))
  runtime.move_to(A)
  control.move_to(A)
  runtime.bee.wait = control.bee.wait = 10.0
  advance(runtime, 14.5)
  advance(control, 14.5)
  visitors = [v for v in runtime.visuals() if isinstance(v, BeeVisual)]
  assert len(visitors) == 1
  assert not control.bee.visible
  assert compositor.frame(visitors[0]).getchannel("A").getbbox()
  flower = runtime.visuals()[0]
  assert compositor.frame(flower).tobytes() == compositor.frame(control.visuals()[0]).tobytes()
  before = compositor.frame(visitors[0]).tobytes()
  runtime.move_to(B)
  assert runtime.active.flower.cell == B
  visitor = next(v for v in runtime.visuals() if isinstance(v, BeeVisual))
  assert compositor.frame(visitor).tobytes() == before
  advance(runtime, 0.1)
  assert any(isinstance(v, BeeVisual) for v in runtime.visuals())
  identifiers = {v.identifier for v in runtime.visuals()}
  runtime.reset()
  assert runtime.visuals() == ()
  runtime.move_to(B)
  assert runtime.visuals()[0].identifier > max(identifiers)
  assert runtime.bee.idle == 0


def test_flight_has_transparent_padding_in_its_independent_rotated_bitmap():
  bee = measured_bee()
  advance(bee, 10)
  compositor = BeeCompositor()
  for _ in range(900):
    bee.update(1 / 60, HEAD)
    if bee.visible:
      frame = compositor.frame(bee.pose)
      bounds = frame.getchannel("A").getbbox()
      assert bounds is not None
      assert min(bounds[:2]) > 0
      assert bounds[2] < frame.width and bounds[3] < frame.height
      assert frame.width <= math.ceil(2 * bee.radius)
      assert frame.height <= math.ceil(2 * bee.radius)
