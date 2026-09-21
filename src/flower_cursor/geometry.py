"""Shared native-pixel geometry for flowers and independent viewport visitors."""

from __future__ import annotations

from dataclasses import dataclass

from .lifecycle import CursorCell


@dataclass(frozen=True, slots=True)
class ViewportGeometry:
    rows: int
    columns: int
    width: int
    height: int

    def __post_init__(self) -> None:
        if min(self.rows, self.columns) <= 0 or min(self.cell_size) <= 0:
            raise ValueError("viewport requires measured positive cell and pixel dimensions")

    @property
    def cell_size(self) -> tuple[int, int]:
        return self.width // self.columns, self.height // self.rows

    @classmethod
    def measured(cls, rows: int, columns: int, width: int, height: int) -> ViewportGeometry | None:
        if min(rows, columns) <= 0 or width < columns or height < rows:
            return None
        return cls(rows, columns, width, height)


@dataclass(frozen=True, slots=True)
class FlowerLayout:
    root_anchor: tuple[float, float]
    columns: int = 5
    rows: int = 2
    anchor_columns: int = 3
    anchor_rows: int = 1
    canvas_scale: int = 2

    def size(self, cell_size: tuple[int, int]) -> tuple[int, int]:
        cw, ch = cell_size
        return (
            (self.columns * cw - min(8, cw - 1)) * self.canvas_scale,
            (self.rows * ch - min(2, ch - 1)) * self.canvas_scale,
        )

    def origin(self, cell: CursorCell, cell_size: tuple[int, int]) -> tuple[int, int]:
        cw, ch = cell_size
        width, _ = self.size(cell_size)
        offset = round(self.anchor_columns * cw - self.root_anchor[0] * width)
        return (
            (cell.column - self.anchor_columns) * cw + offset,
            max(0, cell.row - self.anchor_rows) * ch + min(2, ch - 1),
        )

    def point(
        self, cell: CursorCell, cell_size: tuple[int, int], point: tuple[float, float]
    ) -> tuple[float, float]:
        x, y = self.origin(cell, cell_size)
        width, height = self.size(cell_size)
        return x + point[0] * width, y + point[1] * height


def viewport_crop(
    origin: tuple[int, int], size: tuple[int, int], viewport: ViewportGeometry
) -> tuple[int, int, int, int] | None:
    x, y = origin
    width, height = size
    bounds = (
        max(0, -x),
        max(0, -y),
        min(width, viewport.width - x),
        min(height, viewport.height - y),
    )
    return bounds if bounds[0] < bounds[2] and bounds[1] < bounds[3] else None
