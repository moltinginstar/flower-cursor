"""Everything that turns a window's cursor into pixels, in this process.

A wrapper hands its drawing to one of two things with the same methods: this,
or `shared.RemoteDrawing`, which sends the same calls to the shared renderer -
where they arrive here. Nothing in this module reads the terminal; it is told
the cursor, the viewport and the screen resets, and writes graphics bytes.

Importing it is cheap. Pillow and the artwork are loaded when a
`LocalDrawing` is built, which a wrapper talking to the shared renderer never
does.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Protocol

from .geometry import ViewportGeometry
from .lifecycle import CursorCell


class Drawing(Protocol):
    """What a window asks of whatever draws for it."""

    def move_to(self, cell: CursorCell) -> None: ...

    def screen_reset(self, sequence: bytes) -> None: ...

    def wither(self) -> None: ...

    def tick(
        self,
        duration: float,
        cell: CursorCell | None,
        window_size: tuple[int, int, int, int] | None,
        drawing: bool,
        cells: tuple[CursorCell, ...],
    ) -> None: ...

    def wants_tick(self) -> bool: ...

    def close(self) -> None: ...


@dataclass(frozen=True, slots=True)
class Recipe:
    """What a window's drawing is built from, in a form that crosses a socket."""

    manifest: str
    species: str | None
    bee: float | None
    unique_flowers: int
    volume: float

    @classmethod
    def from_settings(cls, settings, volume: float) -> Recipe:
        return cls(
            # Absolute: the renderer does not share this window's directory.
            manifest=str(Path(settings.manifest).absolute()),
            species=settings.species_name,
            bee=settings.bee,
            unique_flowers=settings.unique_flowers,
            volume=volume,
        )

    def to_json(self) -> dict:
        return asdict(self)

    @classmethod
    def from_json(cls, data: dict) -> Recipe:
        return cls(
            manifest=str(data["manifest"]),
            species=None if data["species"] is None else str(data["species"]),
            bee=None if data["bee"] is None else float(data["bee"]),
            unique_flowers=int(data["unique_flowers"]),
            volume=float(data["volume"]),
        )


def _open_buzz(volume: float, report, open_device):
    """The wingbeat audio, or None if this machine will not play it.

    Audio is an accessory to a cursor: a backend that cannot be imported,
    opened or started costs the user a word in *report*, never the window.
    """
    if volume <= 0:
        return None
    try:
        from .buzz import BeeBuzz

        options = {} if open_device is None else {"open_device": open_device}
        candidate = BeeBuzz(volume=volume, report=report, **options)
        return candidate if candidate.start() else None
    except Exception as error:
        report(f"flower-cursor: no bee audio ({error})")
        return None


class LocalDrawing:
    """One window's flowers, bee, buzz and Kitty placements."""

    def __init__(
        self,
        recipe: Recipe,
        write: Callable[[bytes], object],
        *,
        report: Callable[[str], None],
        open_device=None,
        images: dict | None = None,
    ) -> None:
        from .graphics_renderer import FlowerRenderer
        from .layered import LayeredCompositor, LayeredRuntime
        from .multi_runtime import MultiCursorFlowers

        manifest = Path(recipe.manifest)
        chosen = {"species": recipe.species} if recipe.species is not None else {}
        self.runtime = LayeredRuntime(
            manifest,
            bee=recipe.bee is not None,
            bee_interval=recipe.bee if recipe.bee is not None else 0.0,
            **chosen,
        )
        compositor = LayeredCompositor(manifest, images=images, **chosen)
        self.flowers = MultiCursorFlowers(manifest, independent_limit=recipe.unique_flowers)
        self.renderer = FlowerRenderer(self.runtime.pose_library, write, compositor=compositor)
        self.buzz = _open_buzz(recipe.volume, report, open_device)

    def move_to(self, cell: CursorCell) -> None:
        self.runtime.move_to(cell)

    def screen_reset(self, sequence: bytes) -> None:
        """The child erased or switched the screen; the flowers on it are gone."""
        if self.renderer.observe_terminal_output(sequence):
            self.runtime.reset()
            self.flowers.reset()

    def wither(self) -> None:
        """Leave the way flowers leave: the bee goes quiet and everything wilts."""
        if self.buzz is not None:
            self.buzz.mute()
        self.runtime.begin_withering()
        self.flowers.begin_withering()

    def tick(
        self,
        duration: float,
        cell: CursorCell | None,
        window_size: tuple[int, int, int, int] | None,
        drawing: bool,
        cells: tuple[CursorCell, ...],
    ) -> None:
        self.renderer.flush_pending()
        if cell is None or window_size is None:
            return
        viewport = ViewportGeometry.measured(*window_size)
        self.runtime.set_geometry(viewport, self.renderer.layout)
        self.runtime.update(duration)
        visuals = self.runtime.visuals()
        if drawing:
            self.flowers.update(cells, duration)
        else:
            # Nothing is paired to a cursor any more, so the flowers left
            # behind only have to finish leaving.
            self.flowers.advance(duration)
        active = self.runtime.active
        visuals = self.flowers.visuals(visuals, active.flower.identifier if active else None)
        self.renderer.render(visuals, cell, duration, viewport=viewport)
        if self.buzz is not None:
            self.buzz.update(self.runtime.bee, audible=drawing)

    def wants_tick(self) -> bool:
        """Whether an unfocused window still has something left to draw, or to hear."""
        return (
            self.runtime.animating
            or self.flowers.animating
            # Each tick tells the buzz what to play, and it stops its device
            # only once a tick has seen it fall silent.
            or (self.buzz is not None and self.buzz.playing)
        )

    def close(self) -> None:
        """Take the images down, then let the buzz fade. Safe to call twice."""
        self.renderer.clear()
        if self.buzz is not None:
            self.buzz.close()
