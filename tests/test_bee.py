import hashlib
import json
import math

import pytest

from flower_cursor.bee import COOLDOWN, DEPARTURE_TIME, IdleBee
from flower_cursor.bee_art import ASSETS, BeeCompositor
from flower_cursor.layered import BeeVisual, LayeredCompositor, LayeredRuntime
from flower_cursor.lifecycle import CursorCell
from flower_cursor.observer import DEFAULT_MANIFEST

A, B = CursorCell(8, 12), CursorCell(8, 22)


def advance(runtime, duration, step=1 / 60):
    for _ in range(round(duration / step)):
        runtime.update(step)


def test_visit_needs_ten_seconds_stationary_and_is_not_a_keypress_effect():
    bee = IdleBee()
    bee.move_to(A)
    advance(bee, 9.9)
    assert not bee.visible
    bee.move_to(A)
    advance(bee, 0.2)
    assert bee.visible and bee.cell == A
    for i in range(240):
        bee.move_to(A if i % 2 else B)
        bee.update(1 / 60)
    assert not bee.visible
    assert bee.generation == 1


@pytest.mark.parametrize("age", [0.2, 1.8, 3.6, 5.0])
def test_interruptions_preserve_position_velocity_pose_and_old_anchor(age):
    bee = IdleBee()
    bee.move_to(A)
    advance(bee, 10 + age)
    before = bee.pose
    velocity = (bee.vx, bee.vy)
    bee.move_to(B)
    assert bee.pose == before
    assert (bee.vx, bee.vy) == velocity
    assert bee.cell == A and bee.target == B
    bee.update(1 / 120)
    assert math.hypot(bee.x - before.x, bee.y - before.y) < 0.01
    advance(bee, DEPARTURE_TIME)
    assert not bee.visible


def test_bounded_visit_and_cooldown_even_if_target_never_moves():
    bee = IdleBee()
    bee.move_to(A)
    advance(bee, 22)
    assert not bee.visible and bee.cooldown > 30
    advance(bee, 30)
    assert not bee.visible
    advance(bee, 6)
    assert bee.visible and bee.generation == 2


def test_aborted_landing_never_attaches_to_moving_target():
    bee = IdleBee()
    bee.move_to(A)
    advance(bee, 12)
    assert bee.folded == 0
    bee.move_to(B)
    for i in range(100):
        bee.move_to(CursorCell(i % 12, i))
        bee.update(1 / 60)
        assert bee.folded == 0
        assert bee.cell in (A, None)
    advance(bee, 2)
    assert not bee.visible


def test_screen_reset_and_suspend_remove_visitor_without_replaying():
    bee = IdleBee()
    bee.move_to(A)
    advance(bee, 15)
    bee.reset()
    assert not bee.visible and bee.target is None
    bee.move_to(B)
    advance(bee, 10)
    assert not bee.visible
    advance(bee, COOLDOWN)
    assert bee.visible
    bee.update(60)
    assert not bee.visible and bee.target == B
    advance(bee, COOLDOWN + 0.1)
    assert bee.visible


@pytest.mark.parametrize("duration", [-1, float("inf"), float("nan")])
def test_invalid_clock_values(duration):
    with pytest.raises(ValueError):
        IdleBee().update(duration)


def test_artwork_is_portable_distinct_transparent_and_continuous():
    data = json.loads((ASSETS / "manifest.json").read_text())
    assert len(data["frames"]) == 16
    hashes = {hashlib.sha256((ASSETS / name).read_bytes()).hexdigest() for name in data["frames"]}
    assert len(hashes) >= 12
    for index in range(16):
        image = BeeCompositor.image(index)
        assert image.size == (192, 192)
        assert image.getchannel(3).getextrema() == (0, 255)
    bee = IdleBee()
    bee.move_to(A)
    advance(bee, 15)
    compositor = BeeCompositor()
    before = compositor.frame(bee.pose, 256)
    bee.move_to(B)
    assert compositor.frame(bee.pose, 256).tobytes() == before.tobytes()


def test_real_runtime_cleanup_independence_and_pixels(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    runtime = LayeredRuntime(DEFAULT_MANIFEST)
    control = LayeredRuntime(DEFAULT_MANIFEST)
    compositor = LayeredCompositor(DEFAULT_MANIFEST)
    runtime.move_to(A)
    control.move_to(A)
    advance(runtime, 14.5)
    advance(control, 14.5)
    visitors = [v for v in runtime.visuals() if isinstance(v, BeeVisual)]
    assert len(visitors) == 1
    assert compositor.frame(visitors[0]).getchannel("A").getbbox()
    flower = runtime.visuals()[0]
    control.bee.reset()
    assert compositor.frame(flower).tobytes() == compositor.frame(control.visuals()[0]).tobytes()
    before = compositor.frame(visitors[0]).tobytes()
    runtime.move_to(B)
    assert runtime.active.flower.cell == B
    visitor = next(v for v in runtime.visuals() if isinstance(v, BeeVisual))
    assert compositor.frame(visitor).tobytes() == before
    advance(runtime, 1)
    assert any(isinstance(v, BeeVisual) for v in runtime.visuals())
    identifiers = {v.identifier for v in runtime.visuals()}
    runtime.reset()
    assert runtime.visuals() == ()
    runtime.move_to(B)
    assert runtime.visuals()[0].identifier > max(identifiers)
    assert runtime.bee.cooldown == COOLDOWN


def test_flight_does_not_clip_canvas_before_vanishing():
    bee = IdleBee()
    bee.move_to(A)
    advance(bee, 10)
    compositor = BeeCompositor()
    for _ in range(720):
        bee.update(1 / 60)
        if bee.visible:
            bounds = compositor.frame(bee.pose, 256).getchannel("A").getbbox()
            if bounds is not None:
                assert bounds[0] > 0 and bounds[1] > 0
                assert bounds[2] < 256 and bounds[3] < 256
