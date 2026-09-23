from dataclasses import FrozenInstanceError

import pytest

from flower_cursor.geometry import FlowerLayout, ViewportGeometry, viewport_crop
from flower_cursor.lifecycle import CursorCell


@pytest.mark.parametrize(
    "dimensions",
    [
        (0, 80, 1280, 800),
        (24, 0, 1280, 800),
        (24, 80, 0, 800),
        (24, 80, 1280, 0),
        (24, 80, 79, 800),
        (24, 80, 1280, 23),
        (-1, 80, 1280, 800),
        (24, 80, -1, 800),
    ],
)
def test_unmeasured_or_invalid_viewport_is_not_fabricated(dimensions):
    assert ViewportGeometry.measured(*dimensions) is None
    with pytest.raises(ValueError):
        ViewportGeometry(*dimensions)


def test_measured_viewport_is_immutable_and_preserves_remainder_pixels():
    viewport = ViewportGeometry.measured(24, 80, 1283, 803)
    assert viewport == ViewportGeometry(24, 80, 1283, 803)
    assert viewport.cell_size == (16, 33)
    assert (viewport.width, viewport.height) == (1283, 803)
    with pytest.raises(FrozenInstanceError):
        viewport.width = 1280


@pytest.mark.parametrize("cell", [CursorCell(0, 0), CursorCell(0, 5), CursorCell(8, 12)])
@pytest.mark.parametrize("cell_size", [(1, 1), (8, 16), (16, 33), (22, 43)])
@pytest.mark.parametrize(
    "layout",
    [FlowerLayout((0.48, 0.45)), FlowerLayout((0.39, 0.8), 7, 4, 2, 3, 3)],
)
def test_shared_flower_transform_preserves_original_renderer_formula(cell, cell_size, layout):
    cw, ch = cell_size
    width = (layout.columns * cw - min(8, cw - 1)) * layout.canvas_scale
    height = (layout.rows * ch - min(2, ch - 1)) * layout.canvas_scale
    left = (cell.column - layout.anchor_columns) * cw
    left += round(layout.anchor_columns * cw - layout.root_anchor[0] * width)
    top = (cell.row - layout.anchor_rows) * ch + min(2, ch - 1)
    assert layout.size(cell_size) == (width, height)
    assert layout.origin(cell, cell_size) == (left, top)
    assert layout.point(cell, cell_size, (0.31, 0.27)) == pytest.approx(
        (left + 0.31 * width, top + 0.27 * height)
    )
    root = layout.point(cell, cell_size, layout.root_anchor)
    assert abs(root[0] - cell.column * cw) <= 0.5


def test_top_row_keeps_root_registration_and_leaves_the_overflow_above_the_viewport():
    layout = FlowerLayout((0.5, 0.45))
    assert layout.size((16, 33)) == (144, 128)
    assert layout.origin(CursorCell(0, 0), (16, 33)) == (-72, -31)
    # The head is placed where it would be on any other row, above the viewport.
    assert layout.point(CursorCell(0, 0), (16, 33), (0.75, 0.25)) == (36, 1)
    assert layout.point(CursorCell(0, 0), (16, 33), (0.75, 0.1)) == (36, -18.2)
    for row in (0, 1, 5):
        root = layout.point(CursorCell(row, 0), (16, 33), layout.root_anchor)
        assert root[1] - row * 33 == pytest.approx(26.6)


@pytest.mark.parametrize(
    "origin,size,expected",
    [
        ((20, 30), (50, 40), (0, 0, 50, 40)),
        ((-10, 30), (50, 40), (10, 0, 50, 40)),
        ((20, -10), (50, 40), (0, 10, 50, 40)),
        ((780, 30), (50, 40), (0, 0, 20, 40)),
        ((20, 580), (50, 40), (0, 0, 50, 20)),
        ((-10, -20), (900, 700), (10, 20, 810, 620)),
        ((-50, 30), (50, 40), None),
        ((800, 30), (50, 40), None),
        ((20, -40), (50, 40), None),
        ((20, 600), (50, 40), None),
        ((-49, 30), (50, 40), (49, 0, 50, 40)),
        ((799, 30), (50, 40), (0, 0, 1, 40)),
        ((20, -39), (50, 40), (0, 39, 50, 40)),
        ((20, 599), (50, 40), (0, 0, 50, 1)),
    ],
)
def test_viewport_crop_is_exact_half_open_intersection(origin, size, expected):
    assert viewport_crop(origin, size, ViewportGeometry(30, 100, 800, 600)) == expected
