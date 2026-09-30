"""Premultiplied interpolation of the separately authored Blender bee."""

from __future__ import annotations

import json
import math
from bisect import bisect_right
from functools import lru_cache
from pathlib import Path

from PIL import Image

from .bee import BeePose

ASSETS = Path(__file__).parent / "assets" / "bee"
HEADINGS = tuple(sorted(json.loads((ASSETS / "manifest.json").read_text())["headings"]))


class BeeCompositor:
  @staticmethod
  @lru_cache(maxsize=16 * len(HEADINGS))
  def image(index: int, heading: int = 0) -> Image.Image:
    name = f"bee_{index:02d}.png" if heading == 0 else f"bee_{index:02d}_{heading:+03d}.png"
    with Image.open(ASSETS / name) as image:
      return image.convert("RGBa")

  def _pose(self, pose: BeePose, heading: int) -> Image.Image:
    phase = pose.wing_phase % 1 * 8
    lower = math.floor(phase)
    flight = Image.blend(self.image(lower, heading), self.image((lower + 1) % 8, heading), phase - lower)
    landing = pose.folded * 8
    lower = min(7, math.floor(landing))
    resting = Image.blend(self.image(7 + lower, heading), self.image(8 + lower, heading), landing - lower)
    return Image.blend(flight, resting, min(1.0, pose.folded / 0.12))

  def frame(self, pose: BeePose) -> Image.Image:
    heading = (pose.heading - HEADINGS[0]) % 360 + HEADINGS[0]
    index = bisect_right(HEADINGS, heading) - 1
    lower = HEADINGS[index]
    upper = HEADINGS[(index + 1) % len(HEADINGS)]
    span = (upper - lower) % 360
    blend = (heading - lower) / span
    image = self._pose(pose, lower)
    if blend > 0:
      image = Image.blend(image, self._pose(pose, upper), blend)
    image = image.resize(pose.size, Image.Resampling.LANCZOS)
    return image.rotate(pose.bank, Image.Resampling.BICUBIC, expand=True).convert("RGBA")
