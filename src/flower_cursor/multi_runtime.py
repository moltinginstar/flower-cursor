"""Give every logical cursor its own flower, degrading to copies under load.

One `LayeredRuntime` drives one caret, so a window showing several cursors
needs several of them. They are kept here rather than inside `LayeredRuntime`
so the single-cursor path stays exactly what it was.

Beyond `independent_limit` simultaneous cursors the per-cursor runtimes are
dropped and the primary flower is stamped at every cursor instead: identical
copies, which is still a flower at every cursor rather than Kitty's own.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

from .bee import NoBee
from .garden import Garden
from .layered import LayeredRuntime
from .lifecycle import CursorCell
from .runtime import FlowerVisual
from .settings import DEFAULT_UNIQUE_FLOWERS as DEFAULT_INDEPENDENT_LIMIT

# Building a runtime parses the manifest, so a burst of new cursors is spread
# over a few ticks rather than stalling one of them.
_BUILDS_PER_TICK = 2
_COPIES = -1
# Retiring runtimes keep drawing while they wither, so they need slots of their
# own that no live runtime can collide with.
_FIRST_RETIRED_SLOT = -2


class MultiCursorFlowers:
  """Flowers for the extra cursors an application declared."""

  def __init__(
    self,
    manifest: Path,
    *,
    independent_limit: int = DEFAULT_INDEPENDENT_LIMIT,
    build: Callable[..., LayeredRuntime] = LayeredRuntime,
    garden: Garden | None = None,
  ) -> None:
    if independent_limit < 0:
      raise ValueError("independent_limit must be non-negative")
    self._manifest = manifest
    # The window's own garden, so every cursor's buds take their turn in
    # one cycle. Without one, each runtime grows the manifest's flower.
    self._garden = garden
    self._limit = independent_limit
    self._build = build
    self._runtimes: list[LayeredRuntime] = []
    self._retiring: dict[int, LayeredRuntime] = {}
    self._retired_slot = _FIRST_RETIRED_SLOT
    self._cells: tuple[CursorCell, ...] = ()
    self._identifiers: dict[tuple[int, int], int] = {}
    self._released: list[int] = []
    self._next_identifier = 1

  @property
  def degraded(self) -> bool:
    return len(self._cells) > self._limit

  def reset(self) -> None:
    self._runtimes.clear()
    self._retiring.clear()
    self._cells = ()

  @property
  def animating(self) -> bool:
    return any(runtime.animating for runtime in (*self._runtimes, *self._retiring.values()))

  def begin_withering(self) -> None:
    """Wither every extra flower, keeping it drawn until it is gone."""
    for runtime in self._runtimes:
      self._retire(runtime)
    self._runtimes.clear()

  def _retire(self, runtime: LayeredRuntime) -> None:
    runtime.begin_withering()
    self._retiring[self._retired_slot] = runtime
    self._retired_slot -= 1

  def advance(self, duration: float) -> None:
    """Carry withering flowers forward without pairing them to cursors."""
    self._advance_retiring(duration)

  def _advance_retiring(self, duration: float) -> None:
    for runtime in self._retiring.values():
      runtime.update(duration)
    self._retiring = {s: r for s, r in self._retiring.items() if r.animating}

  def update(self, cells: tuple[CursorCell, ...], duration: float) -> None:
    self._cells = cells
    self._advance_retiring(duration)
    if self.degraded:
      # The copies take over at the same cells, so these leave the way
      # every other flower leaves rather than blinking out.
      self.begin_withering()
      return
    # Cursors are paired to runtimes in screen order, so a block of them
    # moving together keeps each flower on the same logical cursor.
    while len(self._runtimes) > len(cells):
      self._retire(self._runtimes.pop())
    for _ in range(min(_BUILDS_PER_TICK, len(cells) - len(self._runtimes))):
      runtime = (
        self._build(self._manifest) if self._garden is None else self._build(self._manifest, garden=self._garden)
      )
      runtime.bee = NoBee()
      self._runtimes.append(runtime)
    for runtime, cell in zip(self._runtimes, cells):
      runtime.move_to(cell)
      runtime.update(duration)

  def visuals(self, primary: tuple[FlowerVisual, ...], stamp: int | None) -> tuple[FlowerVisual, ...]:
    """Renumber *primary*'s visuals and append one set per extra cursor.

    Identifiers have to be unique across every runtime because the renderer
    uses them as Kitty placement ids, and each runtime numbers its own
    flowers from one.
    """
    live: set[tuple[int, int]] = set()
    visuals = [self._renumber(0, visual, live) for visual in primary]
    if self.degraded:
      source = next((v for v in primary if v.identifier == stamp), None)
      if source is not None:
        for index, cell in enumerate(self._cells):
          visuals.append(
            self._renumber(
              _COPIES,
              replace(source, identifier=index, cell=cell, appearance=stamp),
              live,
            )
          )
    else:
      for slot, runtime in enumerate(self._runtimes, start=1):
        for visual in runtime.visuals():
          visuals.append(self._renumber(slot, visual, live))
    for slot, runtime in self._retiring.items():
      for visual in runtime.visuals():
        visuals.append(self._renumber(slot, visual, live))
    for key in set(self._identifiers) - live:
      self._released.append(self._identifiers.pop(key))
    return tuple(visuals)

  def _renumber(self, slot: int, visual: FlowerVisual, live: set[tuple[int, int]]) -> FlowerVisual:
    key = (slot, visual.identifier)
    live.add(key)
    identifier = self._identifiers.get(key)
    if identifier is None:
      identifier = self._released.pop() if self._released else self._next_identifier
      if identifier == self._next_identifier:
        self._next_identifier += 1
      self._identifiers[key] = identifier
    return replace(visual, identifier=identifier)
