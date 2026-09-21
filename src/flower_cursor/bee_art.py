"""Premultiplied interpolation of the separately authored Blender bee."""

from __future__ import annotations

import math
from functools import lru_cache
from pathlib import Path

from PIL import Image

from .bee import BeePose

ASSETS = Path(__file__).parent / "assets" / "bee"


class BeeCompositor:
    @staticmethod
    @lru_cache(maxsize=16)
    def image(index: int) -> Image.Image:
        with Image.open(ASSETS / f"bee_{index:02d}.png") as image:
            return image.convert("RGBa")

    def frame(self, pose: BeePose, size: int) -> Image.Image:
        canvas = Image.new("RGBA", (size, size))
        if pose.scale <= 0.001:
            return canvas
        phase = pose.wing_phase % 1 * 8
        lower = math.floor(phase)
        flight = Image.blend(self.image(lower), self.image((lower + 1) % 8), phase - lower)
        landing = pose.folded * 8
        lower = min(7, math.floor(landing))
        resting = Image.blend(self.image(7 + lower), self.image(8 + lower), landing - lower)
        image = Image.blend(flight, resting, pose.folded)
        extent = max(1, round(size * 0.20 * pose.scale))
        image = image.resize((extent, extent), Image.Resampling.LANCZOS)
        image = image.rotate(pose.bank, Image.Resampling.BICUBIC, expand=True)
        canvas.alpha_composite(
            image.convert("RGBA"),
            (round(pose.x * size - image.width / 2), round(pose.y * size - image.height / 2)),
        )
        return canvas
