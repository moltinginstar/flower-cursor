"""Interactive shedding using independently authored botanical layers."""

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageOps

from .bee import DEFAULT_INTERVAL, BeePose, IdleBee, NoBee
from .bee_art import BeeCompositor
from .compositor import blend_samples
from .easing import require_duration, smoothstep
from .geometry import FlowerLayout, ViewportGeometry
from .lifecycle import CursorCell, FlowerInstance
from .pose_library import PoseAsset, PoseLibrary, manifest_data
from .runtime import DEFAULT_ANIMATION_SPEED, FlowerVisual
from .shedding import PetalFlight, SheddingFlower, petal_decay, petal_displacement
from .species import metadata_from_manifest

# One authored pose and how much of it a blend uses.
type Samples = tuple[tuple[PoseAsset, float], ...]


@dataclass(frozen=True, slots=True, kw_only=True)
class LayeredVisual(FlowerVisual):
    flower: SheddingFlower


@dataclass(frozen=True, slots=True, kw_only=True)
class BeeVisual(FlowerVisual):
    bee: BeePose


# A shed petal, captured once at the instant it detached and then only moved:
# re-blending it every frame would re-derive pixels that cannot change.
@dataclass(frozen=True, slots=True)
class _CapturedPetal:
    image: Image.Image
    center: tuple[float, float]
    velocity: tuple[float, float, float]


# Petals are identified per flower, so the owning flower's identifier is part
# of the key: two flowers shed a petal numbered 1 apiece.
type _FlightKey = tuple[int, int]


class LayeredRuntime:
    def __init__(
        self,
        manifest: Path,
        *,
        bee_rng: random.Random | None = None,
        bee: bool = True,
        bee_interval: float = DEFAULT_INTERVAL,
        species: str | None = None,
    ) -> None:
        data = manifest_data(manifest)
        self.metadata = metadata_from_manifest(data, selected_species=species)
        self.pose_library = PoseLibrary.from_manifest(manifest)
        self.flowers: list[SheddingFlower] = []
        self.active: SheddingFlower | None = None
        self._next_identifier = 1
        self.bee = (IdleBee if bee else NoBee)(bee_rng, interval=bee_interval)
        self._bee_identifier = 0
        self._bee_flower: SheddingFlower | None = None
        self._heads = {p["name"]: p["head"] for p in data["poses"]}
        self._viewport: ViewportGeometry | None = None
        self._layout = FlowerLayout(self.pose_library.root_anchor)

    def set_geometry(self, viewport: ViewportGeometry | None, layout: FlowerLayout) -> None:
        self._viewport = viewport
        self._layout = layout
        width, height = layout.size(viewport.cell_size) if viewport is not None else (0, 0)
        self.bee.set_geometry(
            viewport, (max(1, round(width * 0.20 * 1.75)), max(1, round(height * 0.20 * 1.75)))
        )
        support = self.metadata.landing_support_bounds
        if viewport is None or support is None:
            self.bee.set_landing_support_bounds(None)
        else:
            x_bounds = (support[0][0] * width, support[0][1] * width)
            y_bounds = (support[1][0] * height, support[1][1] * height)
            self.bee.set_landing_support_bounds((x_bounds, y_bounds))

    def move_to(self, cell: CursorCell) -> None:
        if self.active is not None and self.active.flower.cell == cell:
            return
        self.bee.move_to(cell)
        if self.active is not None:
            self.active.begin_withering()
        returning = next(
            (f for f in self.flowers if f.flower.cell == cell and not f.flower.dead), None
        )
        if returning is None:
            returning = SheddingFlower(
                FlowerInstance.bud(self._next_identifier, cell),
                wall_time_idle=True,
                group_count=len(self.metadata.shedding_groups),
                shedding_order=self.metadata.shedding_order,
            )
            self._next_identifier += 1
            self.flowers.append(returning)
        else:
            returning.restore()
        self.active = returning

    def reset(self) -> None:
        self.flowers.clear()
        self.active = None
        self.bee.reset()
        self._bee_flower = None

    @property
    def animating(self) -> bool:
        """Whether anything is still on screen and moving."""
        return bool(self.flowers) or self.bee.visible

    def begin_withering(self) -> None:
        """Send every flower away the way moving the caret off a cell does.

        Nothing replaces them: the caret is gone rather than moved, so the
        flowers wither, `update` empties the list as each one finishes, and the
        pane is left with nothing to draw. The bee flies off the way it does
        when the caret moves out from under it, which takes longer than the
        withering does - `animating` covers both, so the pane keeps drawing
        until the last of it has gone.
        """
        for flower in self.flowers:
            flower.begin_withering()
        # `move_to` returns early when the active flower already owns the cell,
        # so leaving a finished flower active would stop the pane ever
        # sprouting again once it is focused back.
        self.active = None
        # `_bee_flower` is what keeps the bee drawn, and only the bee's own
        # image is composited from it, so the flower it was visiting can go on
        # withering underneath while the bee leaves. `update` clears it once
        # the bee is off the screen.
        self.bee.leave()

    def update(self, duration: float) -> None:
        require_duration(duration)
        for flower in self.flowers:
            flower.update(duration * DEFAULT_ANIMATION_SPEED, wall_duration=duration)
        self.flowers[:] = [f for f in self.flowers if not f.finished]
        generation = self.bee.generation
        head = None
        if self.active is not None and self._viewport is not None:
            samples = self.pose_library.sample_animated(self.active.pose)
            local_head = tuple(
                sum(self._heads[a.name][i] * w for a, w in samples) for i in range(2)
            )
            head = self._layout.point(self.active.flower.cell, self._viewport.cell_size, local_head)
        self.bee.update(duration, head)
        if self.bee.generation != generation:
            self._bee_identifier = self._next_identifier
            self._next_identifier += 1
            self._bee_flower = self.active
        if not self.bee.visible:
            self._bee_flower = None

    def visuals(self) -> tuple[FlowerVisual, ...]:
        visuals: list[FlowerVisual] = []
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
        if self.bee.cell is not None and self._bee_flower is not None:
            pose = self._bee_flower.pose
            samples = self.pose_library.sample_animated(pose)
            visuals.append(
                BeeVisual(
                    self._bee_identifier,
                    self.bee.cell,
                    pose,
                    samples[0][0],
                    samples,
                    bee=self.bee.pose,
                )
            )
        return tuple(visuals)


class LayeredCompositor:
    def __init__(self, manifest: Path, size: int = 256, *, species: str | None = None) -> None:
        data = manifest_data(manifest)
        self.metadata = metadata_from_manifest(data, selected_species=species)
        self.library = PoseLibrary.from_manifest(manifest)
        self.records = {p["name"]: p for p in data["poses"]}
        self.directory = manifest.parent
        self.right = data["projection_right"]
        self.down = data["projection_down"]
        self.size = size
        self._flights: dict[_FlightKey, _CapturedPetal | None] = {}
        self._images: dict[Path, Image.Image] = {}
        self._bee = BeeCompositor()

    @property
    def canvas_scale(self) -> int:
        """How many canvas pixels this compositor draws per terminal pixel.

        The layered artwork is authored at 256px and supersampled down, so a
        renderer laying it out has to reserve twice the cells' own pixels or
        the frame is scaled twice.
        """
        return 2

    def _image(self, path: Path) -> Image.Image:
        # Kept per compositor rather than in an `lru_cache` on the method: that
        # cache is shared by every instance and keyed on `self`, so it both
        # splits one budget across compositors and keeps each of them alive for
        # the life of the process.
        cached = self._images.get(path)
        if cached is None:
            with Image.open(path) as image:
                result = image.convert("RGBa")
                cached = (
                    result
                    if result.size == (self.size, self.size)
                    else result.resize((self.size, self.size), Image.Resampling.LANCZOS)
                )
            self._images[path] = cached
        return cached

    def _blend(self, samples: Samples, layer: str) -> Image.Image:
        return blend_samples(
            samples,
            lambda asset: self._image(self.directory / self.records[asset.name]["layers"][layer]),
        )

    def _vector(self, samples: Samples, key: str, layer: str | None = None):
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

    def _capture(self, flight: PetalFlight) -> _CapturedPetal | None:
        samples = self.library.sample_animated(flight.pose)
        previous = self.library.sample_animated(flight.previous_pose)
        layer = self.metadata.shedding_groups[flight.index].layer
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
        return _CapturedPetal(image.crop(bounds), center, velocity)

    def _draw_flight(self, canvas: Image.Image, owner: int, flight: PetalFlight) -> None:
        key = (owner, flight.identifier)
        if key not in self._flights:
            self._flights[key] = self._capture(flight)
        captured = self._flights[key]
        decay = petal_decay(flight.age)
        if captured is None or decay >= 1:
            return
        image, center, velocity = captured.image, captured.center, captured.velocity
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
        if isinstance(visual, BeeVisual):
            return self._bee.frame(visual.bee)
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
            layers.extend(
                (group.layer, petal.value)
                for group, petal in zip(self.metadata.shedding_groups, flower.petals)
            )
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
                blend = smoothstep((intact - 0.9) / 0.1)
                canvas = Image.blend(
                    canvas.convert("RGBa"), self._blend(samples, "complete"), blend
                ).convert("RGBA")
        for flight in flower.flights:
            self._draw_flight(canvas, visual.identifier, flight)
        self._retire_landed_petals(visual.identifier, {f.identifier for f in flower.flights})
        return canvas

    def _retire_landed_petals(self, owner: int, in_flight: set[int]) -> None:
        """Drop this flower's petals that have finished falling.

        `retain` cannot do it: a flower that is still on screen keeps its
        identifier, so nothing there says which of its petals have landed.
        """
        self._flights = {
            key: captured
            for key, captured in self._flights.items()
            if key[0] != owner or key[1] in in_flight
        }

    def retain(self, identifiers: set[int]) -> None:
        """Drop every petal belonging to a flower that is no longer drawn."""
        self._flights = {k: v for k, v in self._flights.items() if k[0] in identifiers}
