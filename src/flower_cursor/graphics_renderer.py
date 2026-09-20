"""Render blended lifecycle visuals as independent Kitty graphics placements."""

from __future__ import annotations

import io
import re
from collections.abc import Callable

from PIL import Image

from .compositor import FlowerCompositor, FrameCompositor
from .kitty_graphics import delete_placement, transmit_png
from .lifecycle import CursorCell
from .pose_library import PoseLibrary
from .runtime import FlowerVisual


class FlowerRenderer:
    """Keep Kitty placements synchronized with a flower runtime snapshot."""

    def __init__(
        self,
        pose_library: PoseLibrary,
        write: Callable[[bytes], object],
        *,
        columns: int = 5,
        rows: int = 2,
        anchor_columns: int = 3,
        anchor_rows: int = 1,
        z_index: int = 1,
        compositor: FrameCompositor | None = None,
        canvas_scale: int = 1,
    ) -> None:
        if columns < 1 or rows < 1:
            raise ValueError("columns and rows must be positive")
        if anchor_columns < 0 or anchor_rows < 0:
            raise ValueError("anchor offsets must be non-negative")
        self.pose_library = pose_library
        self._write = write
        self._columns = columns
        self._rows = rows
        self._anchor_columns = anchor_columns
        self._anchor_rows = anchor_rows
        self._z_index = z_index
        self._placements: dict[int, tuple[CursorCell, bytes, int]] = {}
        self._buffers = {False: self._placements, True: {}}
        self._alternate = False
        self._invalidated: set[bool] = set()
        if canvas_scale < 1:
            raise ValueError("canvas scale must be positive")
        self._canvas_scale = canvas_scale
        self._compositor = compositor if compositor is not None else FlowerCompositor()
        self._output_tail = b""
        self._cell_size = (8, 16)

    def observe_terminal_output(self, data: bytes) -> bool:
        previous_length = len(self._output_tail)
        combined = self._output_tail + data
        resets = re.finditer(rb"\x1b(?:c|\[(?:[23]J|\?([0-9;]+)([hl])))", combined)
        changed = False
        for match in resets:
            if match.end() <= previous_length:
                continue
            modes, operation = match.groups()
            if modes is not None:
                if not {b"47", b"1047", b"1049"}.intersection(modes.split(b";")):
                    continue
                self._alternate = operation == b"h"
            elif match.group() == b"\x1bc":
                self._alternate = False
                self._invalidated.update((False, True))
            self._placements = self._buffers[self._alternate]
            self._invalidated.add(self._alternate)
            changed = True
        self._output_tail = combined[-128:]
        return changed

    def flush_pending(self) -> None:
        if self._alternate in self._invalidated:
            self.clear()
            self._invalidated.discard(self._alternate)

    def render(
        self,
        visuals: tuple[FlowerVisual, ...],
        current_cell: CursorCell,
        duration: float = 1 / 60,
        cell_size: tuple[int, int] | None = None,
    ) -> None:
        self.flush_pending()
        geometry_changed = cell_size is not None and cell_size != self._cell_size
        if cell_size is not None and min(cell_size) > 0:
            self._cell_size = cell_size
        cell_width, cell_height = self._cell_size
        # Kitty subtracts X/Y offsets from a c/r rectangle's pixel dimensions.
        width = self._columns * cell_width - min(8, cell_width - 1)
        y_offset = min(2, cell_height - 1)
        height = self._rows * cell_height - y_offset
        width *= self._canvas_scale
        height *= self._canvas_scale
        root_x = self.pose_library.root_anchor[0] * width
        horizontal_offset = round(self._anchor_columns * cell_width - root_x)
        visible_ids = {visual.identifier for visual in visuals}
        for visual in visuals:
            frame = self._compositor.frame(visual, duration)
            frame = (
                frame.convert("RGBa")
                .resize((width, height), Image.Resampling.LANCZOS)
                .convert("RGBA")
            )
            left = (visual.cell.column - self._anchor_columns) * cell_width + horizontal_offset
            column, x_offset = divmod(max(0, left), cell_width)
            if left < 0:
                frame = frame.crop((-left, 0, width, height))
            target = CursorCell(max(0, visual.cell.row - self._anchor_rows), column)
            pixels = frame.tobytes()
            previous = self._placements.get(visual.identifier)
            if (
                not geometry_changed
                and previous is not None
                and previous[:2] == (visual.cell, pixels)
            ):
                continue
            image_base = 0x10000000 + visual.identifier * 2
            image_id = (
                image_base if previous is None or previous[2] != image_base else image_base + 1
            )
            buffer = io.BytesIO()
            frame.save(buffer, format="PNG", compress_level=1)
            self._write(b"\x1b[s")
            self._move_cursor(target)
            transmit_png(
                buffer.getvalue(),
                image_id=image_id,
                placement_id=visual.identifier,
                x_offset=x_offset,
                y_offset=y_offset,
                z_index=self._z_index,
                write=self._write,
            )
            if previous is not None:
                delete_placement(
                    image_id=previous[2],
                    placement_id=visual.identifier,
                    free_data=True,
                    write=self._write,
                )
            self._write(b"\x1b[u")
            self._placements[visual.identifier] = (visual.cell, pixels, image_id)

        for identifier in set(self._placements) - visible_ids:
            delete_placement(
                image_id=self._placements[identifier][2],
                placement_id=identifier,
                free_data=True,
                write=self._write,
            )
            del self._placements[identifier]
        self._compositor.retain(visible_ids)

    def clear(self) -> None:
        for identifier, (_, _, image_id) in tuple(self._placements.items()):
            delete_placement(
                image_id=image_id,
                placement_id=identifier,
                free_data=True,
                write=self._write,
            )
        self._placements.clear()
        self._compositor.retain(set())

    def _move_cursor(self, target: CursorCell) -> None:
        self._write(f"\x1b[{target.row + 1};{target.column + 1}H".encode("ascii"))
