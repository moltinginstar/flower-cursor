"""Flowers only ever leave one way: by withering where they stand."""

from __future__ import annotations

import random
from pathlib import Path

from flower_cursor.geometry import ViewportGeometry
from flower_cursor.graphics_renderer import FlowerRenderer
from flower_cursor.layered import BeeVisual, LayeredCompositor, LayeredRuntime
from flower_cursor.lifecycle import CursorCell
from flower_cursor.multi_runtime import MultiCursorFlowers

MANIFEST = Path("src/flower_cursor/assets/anemone/manifest.json")
A = CursorCell(4, 8)
# Longer than the wither takes, so "still there" and "gone" are both decided.
GONE = 1.5


def bloomed(runtime: LayeredRuntime) -> LayeredRuntime:
  runtime.move_to(A)
  runtime.update(2)
  return runtime


def advance(runtime: LayeredRuntime | MultiCursorFlowers, seconds: float) -> None:
  for _ in range(round(seconds * 60)):
    runtime.update(1 / 60) if isinstance(runtime, LayeredRuntime) else runtime.advance(1 / 60)


def cells(count: int) -> tuple[CursorCell, ...]:
  return tuple(CursorCell(row=index, column=index) for index in range(count))


def settle(flowers: MultiCursorFlowers, targets: tuple[CursorCell, ...]) -> None:
  for _ in range(len(targets) + 1):
    flowers.update(targets, 1 / 60)


def test_losing_focus_leaves_the_flower_on_screen_to_wither() -> None:
  runtime = bloomed(LayeredRuntime(MANIFEST))
  runtime.begin_withering()
  assert runtime.visuals()
  assert runtime.animating


def test_the_withering_finishes_and_then_there_is_nothing_left_to_draw() -> None:
  runtime = bloomed(LayeredRuntime(MANIFEST))
  runtime.begin_withering()
  advance(runtime, GONE)
  assert runtime.visuals() == ()
  assert not runtime.animating


def test_the_caret_coming_back_sprouts_a_new_flower_rather_than_nothing() -> None:
  runtime = bloomed(LayeredRuntime(MANIFEST))
  runtime.begin_withering()
  advance(runtime, GONE)
  runtime.move_to(A)
  runtime.update(1 / 60)
  assert runtime.active is not None
  # A bud, not the bloom that was there before: it has to grow in.
  assert runtime.visuals()[0].pose.bloom < 0.5


def test_the_caret_coming_back_early_still_sprouts_past_the_withering_flower() -> None:
  runtime = bloomed(LayeredRuntime(MANIFEST))
  runtime.begin_withering()
  runtime.update(1 / 60)
  runtime.move_to(A)
  assert runtime.active is not None


def test_a_dropped_cursor_keeps_its_flower_until_it_has_withered() -> None:
  flowers = MultiCursorFlowers(MANIFEST, independent_limit=8)
  settle(flowers, cells(3))
  assert len(flowers.visuals((), None)) == 3
  flowers.update(cells(2), 1 / 60)
  assert len(flowers.visuals((), None)) == 3
  assert flowers.animating
  advance(flowers, GONE)
  assert len(flowers.visuals((), None)) == 2


def test_degrading_to_copies_withers_the_flowers_it_replaces() -> None:
  flowers = MultiCursorFlowers(MANIFEST, independent_limit=2)
  settle(flowers, cells(2))
  flowers.update(cells(3), 1 / 60)
  assert flowers.degraded
  # The copies come from the primary flower; what is left here is the two
  # independent flowers they replaced, still on their way out.
  assert len(flowers.visuals((), None)) == 2
  advance(flowers, GONE)
  assert flowers.visuals((), None) == ()


def test_withering_everything_leaves_each_flower_going_rather_than_gone() -> None:
  flowers = MultiCursorFlowers(MANIFEST, independent_limit=8)
  settle(flowers, cells(3))
  flowers.begin_withering()
  assert len(flowers.visuals((), None)) == 3
  assert flowers.animating
  advance(flowers, GONE)
  assert flowers.visuals((), None) == ()
  assert not flowers.animating


VIEWPORT = ViewportGeometry(30, 100, 1600, 990)
# The bee's departure is a second or so longer than the withering.
BEE_GONE = 4.0


def visited() -> LayeredRuntime:
  """A bloomed flower with the bee settled on it."""
  runtime = LayeredRuntime(MANIFEST, bee_rng=random.Random(31))
  renderer = FlowerRenderer(runtime.pose_library, lambda _: None, compositor=LayeredCompositor(MANIFEST))
  runtime.set_geometry(VIEWPORT, renderer.layout)
  runtime.move_to(A)
  # The wait between visits is randomized; pin it so these seconds are the
  # visit's rather than the wait's.
  runtime.bee.interval = runtime.bee.wait = 10.0
  advance(runtime, 10)
  assert any(isinstance(visual, BeeVisual) for visual in runtime.visuals())
  return runtime


def test_losing_focus_sends_the_bee_away_rather_than_deleting_it() -> None:
  runtime = visited()
  runtime.begin_withering()
  runtime.update(1 / 60)
  assert runtime.bee.departure is not None
  assert any(isinstance(visual, BeeVisual) for visual in runtime.visuals())


def test_the_pane_keeps_drawing_for_the_bee_after_the_flowers_have_gone() -> None:
  runtime = visited()
  runtime.begin_withering()
  advance(runtime, GONE)
  assert not [v for v in runtime.visuals() if not isinstance(v, BeeVisual)]
  assert runtime.bee.visible
  assert runtime.animating


def test_the_bee_leaves_under_its_own_power_and_then_the_pane_is_idle() -> None:
  runtime = visited()
  runtime.begin_withering()
  advance(runtime, BEE_GONE)
  assert not runtime.bee.visible
  assert runtime.visuals() == ()
  assert not runtime.animating
