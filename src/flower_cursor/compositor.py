"""Blend nearby authored poses without moving their shared root anchor."""

from __future__ import annotations

import math
from collections.abc import Callable, Iterable
from pathlib import Path
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
    time against the weight accumulated so far. Both compositors sample the
    same pose library and differ only in which image a sample resolves to,
    which is what `image_for` supplies.
    """
    result: Image.Image | None = None
    accumulated = 0.0
    for asset, weight in samples:
        image = image_for(asset)
        result = (
            image if result is None else Image.blend(result, image, weight / (accumulated + weight))
        )
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


class FlowerCompositor:
    def __init__(self, size: int = 128) -> None:
        self._size = size
        self._images: dict[Path, Image.Image] = {}
        self._previous: dict[int, Image.Image] = {}

    @property
    def canvas_scale(self) -> int:
        """One canvas pixel per terminal pixel: these poses are drawn as authored."""
        return 1

    def frame(self, visual: FlowerVisual, duration: float = 1 / 60) -> Image.Image:
        samples = visual.samples or ((visual.asset, 1.0),)
        result = blend_samples(samples, lambda asset: self._image(asset.path))
        if visual.pose.opacity < 1.0:
            result = result.point([round(value * visual.pose.opacity) for value in range(256)] * 4)
        previous = self._previous.get(visual.identifier)
        if previous is not None:
            amount = 1.0 - math.exp(-max(0.0, duration) / 0.008)
            result = Image.blend(previous, result, amount)
        self._previous[visual.identifier] = result
        return result.convert("RGBA")

    def retain(self, identifiers: set[int]) -> None:
        for identifier in set(self._previous) - identifiers:
            del self._previous[identifier]

    def _image(self, path: Path) -> Image.Image:
        if path not in self._images:
            with Image.open(path) as image:
                self._images[path] = image.convert("RGBa").resize(
                    (self._size, self._size), Image.Resampling.LANCZOS
                )
        return self._images[path]
