"""Render blended lifecycle visuals as independent Kitty graphics placements."""

from __future__ import annotations

import io
import math
import re
import secrets
from collections.abc import Callable

from PIL import Image

from .compositor import FrameCompositor
from .geometry import FlowerLayout, ViewportGeometry, viewport_crop
from .kitty_graphics import MAX_IMAGE_ID, delete_image, delete_placement, place_image, transmit_png
from .layered import BeeVisual
from .lifecycle import CursorCell
from .pose_library import PoseLibrary
from .runtime import FlowerVisual

# Two ids per flower, alternating so a replacement frame never reuses the id
# whose data is still on screen. The span bounds how far the allocator below can
# reach past the renderer's randomly chosen base.
_ID_SPAN = 1 << 20
# The filter that shrinks the supersampled canvas to the cells. BOX costs a
# third of LANCZOS, and side by side in Kitty I could not tell them apart.
DOWNSCALE = Image.Resampling.BOX


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
        canvas_scale: int | None = None,
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
        self._placements: dict[int, tuple[object, bytes, int]] = {}
        self._buffers = {False: self._placements, True: {}}
        self._alternate = False
        self._invalidated: set[bool] = set()
        self._compositor = compositor
        # Supersampling belongs to whoever draws the pixels: a compositor that
        # renders at twice the cells' own resolution is the only part that
        # knows so. An explicit scale still wins, for a caller laying out a
        # frame it will resize itself - which is the one case where a renderer
        # is built with no compositor at all, to read `layout` and nothing else.
        if canvas_scale is None:
            if compositor is None:
                raise ValueError("a renderer without a compositor needs an explicit canvas_scale")
            canvas_scale = compositor.canvas_scale
        if canvas_scale < 1:
            raise ValueError("canvas scale must be positive")
        self.layout = FlowerLayout(
            pose_library.root_anchor, columns, rows, anchor_columns, anchor_rows, canvas_scale
        )
        self._output_tail = b""
        self._cell_size = (8, 16)
        # Image ids are shared with every other program drawing into this window
        # and the protocol cannot report which are free. A base drawn per
        # renderer also keeps two flower-cursor wrappers in one window apart,
        # which a fixed base could not.
        self._image_base = 1 + secrets.randbelow(MAX_IMAGE_ID - _ID_SPAN)
        # Bases are handed out on demand and returned when a placement goes, so
        # a long session's ever-rising flower identifiers cannot alias.
        self._image_bases: dict[object, int] = {}
        self._released_bases: list[int] = []
        self._allocated_bases = 0
        # appearance -> (pixels, image id) for frames shared by many placements.
        self._shared: dict[object, tuple[bytes, int]] = {}

    def observe_terminal_output(self, data: bytes) -> bool:
        previous_length = len(self._output_tail)
        combined = self._output_tail + data
        # ED 2/3/22 all reach grman_clear in screen.c, and DECSED (`?`-marked)
        # runs the same erase. DECSTR is deliberately absent: a soft reset drops
        # the extra cursors but leaves the images alone.
        resets = re.finditer(rb"\x1b(?:c|\[(?:\??(?:22|2|3)J|\?([0-9;]+)([hl])))", combined)
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
        *,
        viewport: ViewportGeometry | None = None,
    ) -> None:
        if self._compositor is None:
            raise ValueError("this renderer was built for layout only and cannot draw")
        self.flush_pending()
        if viewport is not None:
            cell_size = viewport.cell_size
        geometry_changed = cell_size is not None and cell_size != self._cell_size
        if cell_size is not None and min(cell_size) > 0:
            self._cell_size = cell_size
        cell_width, cell_height = self._cell_size
        width, height = self.layout.size(self._cell_size)
        visible_ids: set[int] = set()
        # Visuals that declare the same appearance composite to the same frame;
        # copies of one flower are drawn once and placed many times. The pixels
        # are memoized with the frame: serializing an identical image once per
        # cursor costs a quarter of a frame at a screenful of them.
        drawn: dict[object, tuple[Image.Image, bytes]] = {}
        shared_seen: set[object] = set()
        retired: list[int] = []
        for visual in visuals:
            if isinstance(visual, BeeVisual):
                if viewport is None:
                    continue
                frame = self._compositor.frame(visual, duration)
                left = math.floor(visual.bee.x - frame.width / 2 + 0.5)
                top = math.floor(visual.bee.y - frame.height / 2 + 0.5)
                artwork = frame.getchannel("A").getbbox()
                if artwork is None:
                    continue
                frame = frame.crop(artwork)
                left += artwork[0]
                top += artwork[1]
                bounds = viewport_crop((left, top), frame.size, viewport)
                if bounds is None:
                    continue
                frame = frame.crop(bounds)
                left += bounds[0]
                top += bounds[1]
                anchor_left = min(left, viewport.columns * cell_width - 1)
                anchor_top = min(top, viewport.rows * cell_height - 1)
                if (anchor_left, anchor_top) != (left, top):
                    padded = Image.new(
                        "RGBA",
                        (frame.width + left - anchor_left, frame.height + top - anchor_top),
                    )
                    padded.alpha_composite(frame, (left - anchor_left, top - anchor_top))
                    frame = padded
                    left, top = anchor_left, anchor_top
                pixels = frame.tobytes()
                appearance = None
            else:
                memo = drawn.get(visual.appearance) if visual.appearance is not None else None
                if memo is None:
                    frame = self._compositor.frame(visual, duration)
                    frame = frame.convert("RGBa").resize((width, height), DOWNSCALE).convert("RGBA")
                    memo = (frame, frame.tobytes())
                    if visual.appearance is not None:
                        drawn[visual.appearance] = memo
                frame, pixels = memo
                left, top = self.layout.origin(visual.cell, self._cell_size)
                # Kitty places from a cell, so whatever lies past the top or left
                # edge is cut off here rather than moving the flower to fit.
                clipped = left < 0 or top < 0
                appearance = None if clipped else visual.appearance
                if clipped:
                    frame = frame.crop((max(0, -left), max(0, -top), width, height))
                    pixels = frame.tobytes()
                    left, top = max(0, left), max(0, top)
            column, x_offset = divmod(left, cell_width)
            row, y_offset = divmod(top, cell_height)
            target = CursorCell(row, column)
            placement = (target, x_offset, y_offset, frame.size)
            visible_ids.add(visual.identifier)
            previous = self._placements.get(visual.identifier)
            shared = None
            if appearance is not None:
                shared = self._shared.get(appearance)
                if shared is None or shared[0] != pixels or geometry_changed:
                    if shared is not None:
                        retired.append(shared[1])
                    shared = (pixels, self._next_shared_id(appearance, shared))
                    self._transmit(frame, image_id=shared[1], place=False)
                    self._shared[appearance] = shared
                shared_seen.add(appearance)
            if (
                not geometry_changed
                and previous is not None
                and previous[:2] == (placement, pixels)
                and (shared is None or previous[2] == shared[1])
            ):
                continue
            self._write(b"\x1b[s")
            self._move_cursor(target)
            if shared is not None:
                image_id = shared[1]
                place_image(
                    image_id=image_id,
                    placement_id=visual.identifier,
                    x_offset=x_offset,
                    y_offset=y_offset,
                    z_index=self._z_index,
                    write=self._write,
                )
            else:
                image_base = self._image_base_for(visual.identifier)
                image_id = (
                    image_base if previous is None or previous[2] != image_base else image_base + 1
                )
                self._transmit(
                    frame,
                    image_id=image_id,
                    placement_id=visual.identifier,
                    x_offset=x_offset,
                    y_offset=y_offset,
                    z_index=self._z_index + int(isinstance(visual, BeeVisual)),
                )
            if previous is not None and previous[2] not in retired:
                delete_placement(
                    image_id=previous[2],
                    placement_id=visual.identifier,
                    # Freeing the data of a shared image would erase every other
                    # copy; the retired image is deleted once, below.
                    free_data=shared is None and previous[2] not in self._shared_ids,
                    write=self._write,
                )
            self._write(b"\x1b[u")
            self._placements[visual.identifier] = (placement, pixels, image_id)

        for identifier in set(self._placements) - visible_ids:
            image_id = self._placements[identifier][2]
            if image_id not in retired:
                delete_placement(
                    image_id=image_id,
                    placement_id=identifier,
                    free_data=image_id not in self._shared_ids,
                    write=self._write,
                )
            del self._placements[identifier]
            self._release_image_base(identifier)
        for appearance in set(self._shared) - shared_seen:
            retired.append(self._shared.pop(appearance)[1])
            self._release_image_base(("shared", appearance))
        for image_id in retired:
            # One command removes the image and every placement still on it.
            delete_image(image_id=image_id, write=self._write)
        self._compositor.retain(visible_ids)

    def clear(self) -> None:
        for identifier, (_, _, image_id) in tuple(self._placements.items()):
            if image_id not in self._shared_ids:
                delete_placement(
                    image_id=image_id,
                    placement_id=identifier,
                    free_data=True,
                    write=self._write,
                )
            self._release_image_base(identifier)
        for appearance, (_, image_id) in tuple(self._shared.items()):
            delete_image(image_id=image_id, write=self._write)
            self._release_image_base(("shared", appearance))
        self._shared.clear()
        self._placements.clear()
        if self._compositor is not None:
            self._compositor.retain(set())

    @property
    def _shared_ids(self) -> set[int]:
        return {image_id for _, image_id in self._shared.values()}

    def _transmit(
        self,
        frame: Image.Image,
        *,
        image_id: int,
        placement_id: int | None = None,
        x_offset: int = 0,
        y_offset: int = 0,
        place: bool = True,
        z_index: int | None = None,
    ) -> None:
        buffer = io.BytesIO()
        frame.save(buffer, format="PNG", compress_level=1)
        transmit_png(
            buffer.getvalue(),
            image_id=image_id,
            placement_id=placement_id,
            x_offset=x_offset,
            y_offset=y_offset,
            place=place,
            z_index=self._z_index if z_index is None else z_index,
            write=self._write,
        )

    def _next_shared_id(self, appearance: object, previous: tuple[bytes, int] | None) -> int:
        # Alternate between two ids so a replacement frame is never transmitted
        # over the id whose pixels are still on screen.
        base = self._image_base_for(("shared", appearance))
        return base if previous is None or previous[1] != base else base + 1

    def _image_base_for(self, identifier: object) -> int:
        base = self._image_bases.get(identifier)
        if base is None:
            if self._released_bases:
                base = self._released_bases.pop()
            else:
                base = self._image_base + 2 * (self._allocated_bases % (_ID_SPAN // 2))
                self._allocated_bases += 1
            self._image_bases[identifier] = base
        return base

    def _release_image_base(self, identifier: object) -> None:
        base = self._image_bases.pop(identifier, None)
        if base is not None and base not in self._released_bases:
            self._released_bases.append(base)

    def _move_cursor(self, target: CursorCell) -> None:
        self._write(f"\x1b[{target.row + 1};{target.column + 1}H".encode("ascii"))
