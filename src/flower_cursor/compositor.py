"""Blend nearby authored poses without moving their shared root anchor."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Protocol

from PIL import Image

from .pose_library import PoseAsset
from .runtime import FlowerVisual


def blend_samples(
  samples: Iterable[tuple[PoseAsset, float]],
  image_for: Callable[[PoseAsset], Image.Image],
) -> Image.Image:
  """Weighted blend of the authored poses a sample set names.

  Pillow only blends a pair at a time, so the weights are folded in one at a
  time against the weight accumulated so far. Which image a sample resolves
  to is the caller's business - the layered compositor blends one named layer
  at a time through this - which is what `image_for` supplies.
  """
  result: Image.Image | None = None
  accumulated = 0.0
  for asset, weight in samples:
    image = image_for(asset)
    result = image if result is None else Image.blend(result, image, weight / (accumulated + weight))
    accumulated += weight
  if result is None:
    raise ValueError("cannot blend an empty pose sample")
  return result


class FrameCompositor(Protocol):
  @property
  def canvas_scale(self) -> int:
    """Canvas pixels drawn per terminal pixel, for the renderer's layout."""
    ...

  def frame(self, visual: FlowerVisual, duration: float = 1 / 60) -> Image.Image: ...

  def retain(self, identifiers: set[int]) -> None: ...
