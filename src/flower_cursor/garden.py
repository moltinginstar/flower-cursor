"""Which flower each new bloom is: one species for good, or a new one every time.

A window used to grow one species for as long as it was open. With `cycle` or
`random` every bud - each time the caret moves to a new cell, or the flowers
grow back - is picked afresh, so a single window shows several species at
once while the old flowers wither and the new one opens. A flower keeps the
species it budded as until it is gone: returning to a cell whose flower is
still withering restores that flower rather than picking another.

Nothing here touches Pillow. A species is its manifest's rules and poses;
its pixels are the compositor's business.
"""

from __future__ import annotations

import random
from collections.abc import Sequence
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from .manifest import manifest_data
from .pose_library import PoseLibrary
from .settings import CYCLE, FIXED, RANDOM
from .species import SpeciesMetadata, metadata_from_manifest


@dataclass(frozen=True, eq=False, slots=True)
class Species:
  """One flower's artwork as the runtime reads it: its rules and its poses."""

  manifest: Path
  name: str | None
  metadata: SpeciesMetadata
  pose_library: PoseLibrary
  heads: dict[str, tuple[float, float]]


@lru_cache(maxsize=16)
def load_species(manifest: Path, name: str | None = None) -> Species:
  """A species, parsed once per process however many windows grow it.

  Cached so that every flower of one species - across cursors, and across
  windows in the shared renderer - holds the same object, which is also
  what the compositor tells species apart by.
  """
  data = manifest_data(manifest)
  return Species(
    manifest=manifest,
    name=name,
    metadata=metadata_from_manifest(data, selected_species=name),
    pose_library=PoseLibrary.from_manifest(manifest),
    heads={pose["name"]: tuple(pose["head"]) for pose in data["poses"]},
  )


class Garden:
  """Hands each new bud its species.

  `cycle` walks the flowers in order from a random starting point, so two
  windows opened together do not show the same sequence. `random` never
  picks the species it picked last time, so every move is a visible change.
  A species is loaded the first time it is picked, not when the window opens.
  """

  def __init__(
    self,
    flowers: Sequence[tuple[Path, str | None]],
    order: str = FIXED,
    *,
    rng: random.Random | None = None,
  ) -> None:
    if not flowers:
      raise ValueError("a garden needs at least one flower")
    if order not in (FIXED, CYCLE, RANDOM):
      raise ValueError(f"unknown order {order!r}")
    if order == FIXED and len(flowers) != 1:
      raise ValueError("a fixed garden grows exactly one flower")
    self._flowers = tuple(flowers)
    self.order = order
    self._rng = random.Random() if rng is None else rng
    self._next = self._rng.randrange(len(self._flowers)) if order == CYCLE else 0
    self._last: int | None = None
    # Loaded eagerly: layout and the bee need a species before anything buds.
    self.first = load_species(*self._flowers[0])

  @classmethod
  def single(cls, manifest: Path, species: str | None = None) -> Garden:
    return cls(((manifest, species),))

  def bloom(self) -> Species:
    """The species the next new flower grows as."""
    count = len(self._flowers)
    if self.order == RANDOM and count > 1:
      index = self._rng.randrange(count - 1 if self._last is not None else count)
      if self._last is not None and index >= self._last:
        index += 1
    else:
      index = self._next
      self._next = (self._next + 1) % count
    self._last = index
    return load_species(*self._flowers[index])
