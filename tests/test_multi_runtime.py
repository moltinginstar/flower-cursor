"""One flower per declared cursor, and identical copies once that is too many."""

from __future__ import annotations

from pathlib import Path

from flower_cursor.layered import BeeVisual
from flower_cursor.lifecycle import CursorCell
from flower_cursor.multi_runtime import MultiCursorFlowers
from flower_cursor.runtime import FlowerVisual

MANIFEST = Path("src/flower_cursor/assets/layered/manifest.json")


def cells(count: int) -> tuple[CursorCell, ...]:
  return tuple(CursorCell(row=index, column=index) for index in range(count))


def settle(flowers: MultiCursorFlowers, targets: tuple[CursorCell, ...]) -> None:
  # Runtimes are built a couple per tick so a burst never stalls one frame.
  for _ in range(len(targets) + 1):
    flowers.update(targets, 1 / 60)


def test_every_declared_cursor_grows_its_own_flower() -> None:
  flowers = MultiCursorFlowers(MANIFEST, independent_limit=8)
  settle(flowers, cells(4))
  visuals = flowers.visuals((), None)
  assert {visual.cell for visual in visuals} == set(cells(4))
  assert not flowers.degraded


def test_flowers_are_built_over_several_ticks_rather_than_all_at_once() -> None:
  flowers = MultiCursorFlowers(MANIFEST, independent_limit=8)
  flowers.update(cells(6), 1 / 60)
  assert len(flowers.visuals((), None)) == 2


def test_a_cursor_that_moves_leaves_its_flower_behind_to_wither() -> None:
  flowers = MultiCursorFlowers(MANIFEST, independent_limit=8)
  settle(flowers, cells(3))
  before = {visual.cell: visual.identifier for visual in flowers.visuals((), None)}
  moved = tuple(CursorCell(row=cell.row + 1, column=cell.column) for cell in cells(3))
  flowers.update(moved, 1 / 60)
  after = {visual.cell: visual.identifier for visual in flowers.visuals((), None)}
  # The runtime that served each cursor keeps serving it: the flower it grew
  # stays where it was, under the identifier it already had, and a new one
  # buds at the cursor's new cell.
  assert all(after[cell] == identifier for cell, identifier in before.items())
  assert set(moved) <= set(after)


def test_identifiers_never_collide_between_runtimes() -> None:
  flowers = MultiCursorFlowers(MANIFEST, independent_limit=8)
  settle(flowers, cells(5))
  primary = (
    FlowerVisual(identifier=1, cell=CursorCell(9, 9), pose=None, asset=None),
    FlowerVisual(identifier=2, cell=CursorCell(9, 8), pose=None, asset=None),
  )
  visuals = flowers.visuals(primary, 1)
  identifiers = [visual.identifier for visual in visuals]
  assert len(identifiers) == len(set(identifiers))


def test_a_released_identifier_is_reused_rather_than_climbing_forever() -> None:
  flowers = MultiCursorFlowers(MANIFEST, independent_limit=8)
  settle(flowers, cells(3))
  first = {visual.identifier for visual in flowers.visuals((), None)}
  flowers.reset()
  flowers.visuals((), None)
  settle(flowers, cells(3))
  assert {visual.identifier for visual in flowers.visuals((), None)} <= first


def test_too_many_cursors_get_copies_of_the_one_flower() -> None:
  flowers = MultiCursorFlowers(MANIFEST, independent_limit=2)
  flowers.update(cells(40), 1 / 60)
  assert flowers.degraded
  primary = FlowerVisual(identifier=7, cell=CursorCell(0, 0), pose=None, asset=None)
  visuals = flowers.visuals((primary,), 7)
  copies = [visual for visual in visuals if visual.appearance is not None]
  assert len(copies) == 40
  assert {visual.cell for visual in copies} == set(cells(40))
  # One appearance for the lot, so the renderer composites a single frame.
  assert len({visual.appearance for visual in copies}) == 1
  assert len({visual.identifier for visual in visuals}) == 41


def test_copies_stop_once_the_cursors_fit_again() -> None:
  flowers = MultiCursorFlowers(MANIFEST, independent_limit=2)
  flowers.update(cells(40), 1 / 60)
  settle(flowers, cells(2))
  assert not flowers.degraded
  assert all(visual.appearance is None for visual in flowers.visuals((), None))


def test_the_visitor_belongs_to_the_real_caret_alone() -> None:
  flowers = MultiCursorFlowers(MANIFEST, independent_limit=8)
  for _ in range(400):  # far past the bee's idle delay
    flowers.update(cells(2), 1 / 30)
    assert not any(isinstance(v, BeeVisual) for v in flowers.visuals((), None))


def test_a_screen_reset_drops_every_extra_flower() -> None:
  flowers = MultiCursorFlowers(MANIFEST, independent_limit=8)
  settle(flowers, cells(3))
  flowers.reset()
  assert flowers.visuals((), None) == ()
