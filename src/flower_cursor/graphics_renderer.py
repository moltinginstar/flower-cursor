"""Render lifecycle visuals as independent Kitty graphics placements."""

from __future__ import annotations

import math
from collections.abc import Callable

from .kitty_graphics import delete_placement, place_image, transmit_png
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
        self._placements: dict[int, tuple[CursorCell, str, int, int]] = {}
        self._png_cache: dict[str, bytes] = {}
        self._asset_image_ids: dict[str, int] = {}
        self._transmitted_assets: set[str] = set()

    def render(self, visuals: tuple[FlowerVisual, ...], current_cell: CursorCell) -> None:
        visible_ids = {visual.identifier for visual in visuals}
        for visual in visuals:
            previous = self._placements.get(visual.identifier)
            x_offset, y_offset = self._idle_offsets(visual)
            current = (visual.cell, str(visual.asset.path), x_offset, y_offset)
            if previous == current:
                continue
            if previous is not None and previous[:2] == current[:2]:
                self._reposition(visual, current_cell, x_offset, y_offset)
            else:
                self._place(visual, current_cell, x_offset, y_offset)
            self._placements[visual.identifier] = current

        for identifier in set(self._placements) - visible_ids:
            _, asset_key, _, _ = self._placements[identifier]
            delete_placement(
                image_id=self._asset_image_id(asset_key),
                placement_id=identifier,
                write=self._write,
            )
            del self._placements[identifier]

    def clear(self) -> None:
        for identifier, (_, asset_key, _, _) in tuple(self._placements.items()):
            delete_placement(
                image_id=self._asset_image_id(asset_key),
                placement_id=identifier,
                write=self._write,
            )
        self._placements.clear()

    def _place(
        self,
        visual: FlowerVisual,
        current_cell: CursorCell,
        x_offset: int,
        y_offset: int,
    ) -> None:
        target = CursorCell(
            row=max(0, visual.cell.row - self._anchor_rows),
            column=max(0, visual.cell.column - self._anchor_columns),
        )
        self._write(b"\x1b[s")
        self._move_cursor(current_cell, target)
        previous = self._placements.get(visual.identifier)
        asset_key = str(visual.asset.path)
        if previous is not None and previous[1] != asset_key:
            delete_placement(
                image_id=self._asset_image_id(previous[1]),
                placement_id=visual.identifier,
                write=self._write,
            )
        image_id = self._asset_image_id(asset_key)
        if asset_key in self._transmitted_assets:
            place_image(
                image_id=image_id,
                placement_id=visual.identifier,
                columns=self._columns,
                rows=self._rows,
                x_offset=x_offset,
                y_offset=y_offset,
                z_index=self._z_index,
                write=self._write,
            )
        else:
            png = self._png_cache.setdefault(asset_key, visual.asset.path.read_bytes())
            transmit_png(
                png,
                image_id=image_id,
                placement_id=visual.identifier,
                columns=self._columns,
                rows=self._rows,
                x_offset=x_offset,
                y_offset=y_offset,
                z_index=self._z_index,
                write=self._write,
            )
            self._transmitted_assets.add(asset_key)
        self._write(b"\x1b[u")

    def _reposition(
        self,
        visual: FlowerVisual,
        current_cell: CursorCell,
        x_offset: int,
        y_offset: int,
    ) -> None:
        target = CursorCell(
            row=max(0, visual.cell.row - self._anchor_rows),
            column=max(0, visual.cell.column - self._anchor_columns),
        )
        self._write(b"\x1b[s")
        self._move_cursor(current_cell, target)
        place_image(
            image_id=self._asset_image_id(str(visual.asset.path)),
            placement_id=visual.identifier,
            columns=self._columns,
            rows=self._rows,
            x_offset=x_offset,
            y_offset=y_offset,
            z_index=self._z_index,
            write=self._write,
        )
        self._write(b"\x1b[u")

    @staticmethod
    def _idle_offsets(visual: FlowerVisual) -> tuple[int, int]:
        horizontal = round(visual.pose.idle_sway * 18)
        vertical = round(math.sin(visual.pose.idle_phase) * 0.8 * visual.pose.vitality)
        return max(0, min(12, 8 + horizontal)), max(1, min(3, 2 + vertical))

    def _move_cursor(self, source: CursorCell, target: CursorCell) -> None:
        row_delta = target.row - source.row
        column_delta = target.column - source.column
        if row_delta < 0:
            self._write(f"\x1b[{-row_delta}A".encode("ascii"))
        elif row_delta > 0:
            self._write(f"\x1b[{row_delta}B".encode("ascii"))
        if column_delta < 0:
            self._write(f"\x1b[{-column_delta}D".encode("ascii"))
        elif column_delta > 0:
            self._write(f"\x1b[{column_delta}C".encode("ascii"))

    def _asset_image_id(self, asset_key: str) -> int:
        image_id = self._asset_image_ids.get(asset_key)
        if image_id is None:
            image_id = 1000 + len(self._asset_image_ids)
            self._asset_image_ids[asset_key] = image_id
        return image_id
