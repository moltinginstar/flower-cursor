"""Blend nearby authored poses without moving their shared root anchor."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Protocol

from PIL import Image

from .runtime import FlowerVisual


class FrameCompositor(Protocol):
    def frame(self, visual: FlowerVisual, duration: float = 1 / 60) -> Image.Image: ...

    def retain(self, identifiers: set[int]) -> None: ...


class FlowerCompositor:
    def __init__(self, size: int = 128) -> None:
        self._size = size
        self._images: dict[Path, Image.Image] = {}
        self._previous: dict[int, Image.Image] = {}

    def frame(self, visual: FlowerVisual, duration: float = 1 / 60) -> Image.Image:
        samples = visual.samples or ((visual.asset, 1.0),)
        result = self._image(samples[0][0].path)
        accumulated = samples[0][1]
        for asset, weight in samples[1:]:
            result = Image.blend(result, self._image(asset.path), weight / (accumulated + weight))
            accumulated += weight
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
