"""Interactive shedding using independently authored botanical layers."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageOps

from .lifecycle import CursorCell, FlowerInstance
from .pose_library import PoseLibrary
from .runtime import DEFAULT_ANIMATION_SPEED, FlowerVisual
from .shedding import SheddingFlower, petal_decay, petal_displacement


@dataclass(frozen=True, slots=True, kw_only=True)
class LayeredVisual(FlowerVisual):
    flower: SheddingFlower


class LayeredRuntime:
    def __init__(self, manifest: Path) -> None:
        self.pose_library = PoseLibrary.from_manifest(manifest)
        self.flowers: list[SheddingFlower] = []
        self.active: SheddingFlower | None = None
        self._next_identifier = 1

    def move_to(self, cell: CursorCell) -> None:
        if self.active is not None and self.active.flower.cell == cell:
            return
        if self.active is not None:
            self.active.begin_withering()
        returning = next(
            (f for f in self.flowers if f.flower.cell == cell and not f.flower.dead), None
        )
        if returning is None:
            returning = SheddingFlower(
                FlowerInstance.bud(self._next_identifier, cell), wall_time_idle=True
            )
            self._next_identifier += 1
            self.flowers.append(returning)
        else:
            returning.restore()
        self.active = returning

    def reset(self) -> None:
        self.flowers.clear()
        self.active = None

    def update(self, duration: float) -> None:
        if not math.isfinite(duration) or duration < 0:
            raise ValueError("duration must be finite and non-negative")
        for flower in self.flowers:
            flower.update(duration * DEFAULT_ANIMATION_SPEED, wall_duration=duration)
        self.flowers[:] = [f for f in self.flowers if not f.finished]

    def visuals(self) -> tuple[LayeredVisual, ...]:
        visuals = []
        for flower in self.flowers:
            samples = self.pose_library.sample_animated(flower.pose)
            visuals.append(
                LayeredVisual(
                    flower.flower.identifier,
                    flower.flower.cell,
                    flower.pose,
                    samples[0][0],
                    samples,
                    flower=flower,
                )
            )
        return tuple(visuals)


class LayeredCompositor:
    def __init__(self, manifest: Path, size: int = 256) -> None:
        self.library = PoseLibrary.from_manifest(manifest)
        data = json.loads(manifest.read_text())
        self.records = {p["name"]: p for p in data["poses"]}
        self.directory = manifest.parent
        self.right = data["projection_right"]
        self.down = data["projection_down"]
        self.size = size
        self._flights: dict[tuple[int, int], tuple | None] = {}

    @lru_cache(maxsize=256)
    def _image(self, path: Path) -> Image.Image:
        with Image.open(path) as image:
            result = image.convert("RGBa")
            return (
                result
                if result.size == (self.size, self.size)
                else result.resize((self.size, self.size), Image.Resampling.LANCZOS)
            )

    def _blend(self, samples, layer: str) -> Image.Image:
        result = None
        total = 0.0
        for asset, weight in samples:
            image = self._image(self.directory / self.records[asset.name]["layers"][layer])
            result = (
                image if result is None else Image.blend(result, image, weight / (total + weight))
            )
            total += weight
        if result is None:
            raise ValueError("cannot blend an empty pose sample")
        return result

    def _vector(self, samples, key: str, layer: str | None = None):
        values = [(self.records[a.name][key], w) for a, w in samples]
        if layer is not None:
            values = [(v[layer], w) for v, w in values]
        return tuple(
            sum(value[i] * weight for value, weight in values) for i in range(len(values[0][0]))
        )

    def _scale(self, image: Image.Image, anchor, amount: float) -> Image.Image:
        if amount <= 0.001:
            return Image.new("RGBa", image.size)
        if abs(amount - 1) < 1e-6:
            return image
        result = Image.new("RGBa", image.size)
        bounds = image.getchannel(3).getbbox()
        if bounds is None:
            return result
        x, y = (v * self.size for v in anchor)
        left = max(0, math.floor(x + (bounds[0] - x) * amount) - 2)
        top = max(0, math.floor(y + (bounds[1] - y) * amount) - 2)
        right = min(self.size, math.ceil(x + (bounds[2] - x) * amount) + 2)
        bottom = min(self.size, math.ceil(y + (bounds[3] - y) * amount) + 2)
        if right <= left or bottom <= top:
            return result
        inverse = 1 / amount
        patch = image.transform(
            (right - left, bottom - top),
            Image.Transform.AFFINE,
            (inverse, 0, (left - x) * inverse + x, 0, inverse, (top - y) * inverse + y),
            Image.Resampling.BICUBIC,
        )
        result.paste(patch, (left, top))
        return result

    def _capture(self, flight):
        samples = self.library.sample_animated(flight.pose)
        previous = self.library.sample_animated(flight.previous_pose)
        layer = f"petal_{flight.index}"
        image = self._scale(
            self._blend(samples, layer), self._vector(samples, "head"), flight.growth
        )
        bounds = image.getchannel(3).getbbox()
        if bounds is None:
            return None
        centers = []
        for state in (samples, previous):
            head = self._vector(state, "world_head")
            center = self._vector(state, "centers", layer)
            centers.append(tuple(h + (c - h) * flight.growth for h, c in zip(head, center)))
        velocity = tuple((a - b) / flight.sample_duration for a, b in zip(*centers))
        center = ((bounds[0] + bounds[2]) / 2, (bounds[1] + bounds[3]) / 2)
        return image.crop(bounds), center, velocity

    def _draw_flight(self, canvas: Image.Image, owner: int, flight) -> None:
        key = (owner, flight.identifier)
        if key not in self._flights:
            self._flights[key] = self._capture(flight)
        captured = self._flights[key]
        decay = petal_decay(flight.age)
        if captured is None or decay >= 1:
            return
        image, center, velocity = captured
        brown = Image.new("RGBA", image.size, (80, 48, 28, 255))
        brown.putalpha(image.getchannel(3))
        image = Image.blend(image, brown.convert("RGBa"), decay)
        cosine = math.cos(flight.age * 5)
        width = (
            image.width * (1 - decay) * math.sqrt(cosine * cosine + 0.04 * (1 - cosine * cosine))
        )
        height = image.height * (1 - decay)
        if min(width, height) < 0.3:
            return
        image = image.resize(
            (max(1, round(width)), max(1, round(height))), Image.Resampling.BICUBIC
        )
        if cosine < 0:
            image = ImageOps.mirror(image)
        image = image.rotate(
            math.degrees(flight.age * 0.8 * math.sin(flight.index)),
            resample=Image.Resampling.BICUBIC,
            expand=True,
        )
        displacement = petal_displacement(flight.index, flight.age, velocity)
        x = center[0] + self.size * sum(a * b for a, b in zip(self.right, displacement))
        y = center[1] + self.size * sum(a * b for a, b in zip(self.down, displacement))
        canvas.alpha_composite(
            image.convert("RGBA"), (round(x - image.width / 2), round(y - image.height / 2))
        )

    def frame(self, visual: FlowerVisual, duration: float = 1 / 60) -> Image.Image:
        if not isinstance(visual, LayeredVisual):
            raise TypeError("layered compositor requires a layered visual")
        canvas = Image.new("RGBA", (self.size, self.size))
        samples = visual.samples
        flower = visual.flower
        intact = min(flower.head.value, *(p.value for p in flower.petals))
        if not flower.flower.dead and intact >= 0.9999:
            canvas = self._blend(samples, "complete").convert("RGBA")
        elif not flower.flower.dead:
            canvas.alpha_composite(self._blend(samples, "body").convert("RGBA"))
            head = self._vector(samples, "head")
            layers = [("head", flower.head.value)]
            layers.extend((f"petal_{i}", p.value) for i, p in enumerate(flower.petals))
            layers.sort(
                key=lambda item: sum(
                    self.records[a.name]["depths"][item[0]] * w for a, w in samples
                ),
                reverse=True,
            )
            for layer, amount in layers:
                if amount > 0.001:
                    canvas.alpha_composite(
                        self._scale(self._blend(samples, layer), head, amount).convert("RGBA")
                    )
            if intact > 0.9:
                blend = min(1.0, (intact - 0.9) / 0.1)
                blend = blend * blend * (3 - 2 * blend)
                canvas = Image.blend(
                    canvas.convert("RGBa"), self._blend(samples, "complete"), blend
                ).convert("RGBA")
        for flight in flower.flights:
            self._draw_flight(canvas, visual.identifier, flight)
        live = {(visual.identifier, f.identifier) for f in flower.flights}
        self._flights = {
            k: v for k, v in self._flights.items() if k[0] != visual.identifier or k in live
        }
        return canvas

    def retain(self, identifiers: set[int]) -> None:
        self._flights = {k: v for k, v in self._flights.items() if k[0] in identifiers}
