"""Mirror Kitty's multiple-cursors protocol so every logical cursor gets a flower.

Kitty 0.43+ lets an application declare extra cursors with
``CSI > SHAPE ; TYPE:COORDS ; ... SP q``. Kitty draws those itself, and nothing
in the protocol asks it not to: `DECTCEM` only gates the real caret
(``child-monitor.c``) and the extra-cursor color has no alpha channel
(``screen.c``), so there is no way to make them invisible. We therefore let
Kitty parse each command, keep our own copy of the resulting cursor set, and
clear Kitty's copy on the next render tick so only our flowers remain.

Owning the state means owning the query that reads it back: after we clear,
Kitty would answer an application's ``CSI > 100 SP q`` with an empty set. The
reply filter here swaps in our own state so the application still sees the
cursors it asked for.

The grammar is taken from Kitty's own parser rather than inferred:
``vt-parser.c:_parse_multi_cursors`` flattens the CSI parameters, treats the
first as the shape, and splits the rest into groups at every top-level ``;``;
``screen.c:screen_multi_cursor`` then reads each group as a coordinate type
followed by its coordinates.
"""

from __future__ import annotations

import re

from .lifecycle import CursorCell
from .vt_framer import AltScreenChange, Event, MultipleCursors, ScreenErased

# Shape 0 over a whole-screen region: the documented way to drop every extra
# cursor at once. Sent as two top-level parameters, which is one group.
CLEAR_ALL = b"\x1b[>0;4 q"

# Coordinate types (screen.c:screen_multi_cursor).
_COORD_MAIN_CURSOR = 0
_COORD_POINTS = 2
_COORD_REGIONS = 4
# Shapes that place a cursor. 29 means "whatever the terminal's default is".
_PLACING_SHAPES = frozenset({0, 1, 2, 3, 29})
_REMOVE = 0
_COLOR_SHAPES = frozenset({30, 40})
_STATE_QUERY_SHAPE = 100

# Kitty's reply to CSI > 100 SP q. It reports every cursor as a point, and
# collapses any shape above 3 back to 29.
_STATE_REPLY = re.compile(rb"\x1b\[>100[0-9;:]*\x20q")
_STATE_REPLY_PREFIX = re.compile(rb"\x1b(\[(>(1(0(0[0-9;:]*\x20?)?)?)?)?)?\Z")


def _parse_parameters(parameters: bytes) -> tuple[list[int], list[bool]]:
  """Flatten a CSI parameter list, marking which values followed a colon."""
  values: list[int] = []
  sub: list[bool] = []
  if not parameters:
    return values, sub
  for item in parameters.split(b";"):
    for offset, part in enumerate(item.split(b":")):
      values.append(int(part) if part.isdigit() else 0)
      sub.append(offset > 0)
  return values, sub


def _coordinate(value: int) -> int | None:
  """Convert a 1-based protocol coordinate, rejecting what Kitty discards.

  Kitty stores coordinates in an unsigned type, so a 0 wraps to a value no
  screen can contain and the cursor is silently dropped.
  """
  return value - 1 if value >= 1 else None


class MultiCursorTracker:
  """The set of extra cursors an application believes the terminal is showing."""

  def __init__(self) -> None:
    self._shapes: dict[CursorCell, int] = {}
    self.screen_size: tuple[int, int] = (24, 80)
    self.main_cursor: CursorCell | None = None
    self._kitty_holds_cursors = False
    self.pending_state_queries = 0

  @property
  def cells(self) -> tuple[CursorCell, ...]:
    return tuple(sorted(self._shapes, key=lambda cell: (cell.row, cell.column)))

  def __len__(self) -> int:
    return len(self._shapes)

  def handle(self, event: Event) -> None:
    """Consume one sequence the framer recognized in the child's output."""
    if isinstance(event, MultipleCursors):
      self._command(event.parameters)
    elif isinstance(event, (ScreenErased, AltScreenChange)):
      # Kitty drops its own extra cursors on each of these, so there is
      # nothing left for us to suppress either.
      self._shapes.clear()
      self._kitty_holds_cursors = False

  def take_suppression(self) -> bytes:
    """Return the sequence that hides Kitty's copy of the cursors, if due."""
    if not self._kitty_holds_cursors:
      return b""
    self._kitty_holds_cursors = False
    return CLEAR_ALL

  def state_reply(self) -> bytes:
    """Build the reply Kitty would have sent had we not cleared its state."""
    groups = b";".join(
      # Kitty reports anything above a beam as 29, the default shape.
      b"%d:2:%d:%d" % (min(self._shapes[cell], 29), cell.row + 1, cell.column + 1)
      for cell in self.cells
    )
    return b"\x1b[>100;" + groups + b" q" if groups else b"\x1b[>100 q"

  def _command(self, parameters: bytes) -> None:
    values, sub = _parse_parameters(parameters)
    if not values:
      return  # CSI > SP q asks which shapes exist; Kitty's answer is right.
    shape = values[0]
    groups: list[list[int]] = []
    for index in range(1, len(values)):
      if index == 1 or not sub[index]:
        groups.append([])
      groups[-1].append(values[index])
    if not groups:
      if shape == _STATE_QUERY_SHAPE:
        self.pending_state_queries += 1
      return
    if shape in _COLOR_SHAPES or shape not in _PLACING_SHAPES:
      # Colors only tint cursors we are replacing, and an unknown shape
      # is one Kitty ignores. Neither changes the cursor set.
      return
    self._kitty_holds_cursors = True
    for group in groups:
      self._apply(shape, group[0], group[1:])

  def _apply(self, shape: int, coord_type: int, coords: list[int]) -> None:
    rows, columns = self.screen_size
    if coord_type == _COORD_MAIN_CURSOR:
      if self.main_cursor is None:
        return
      coord_type = _COORD_POINTS
      coords = [self.main_cursor.row + 1, self.main_cursor.column + 1]
    if coord_type == _COORD_POINTS:
      for index in range(0, len(coords) - 1, 2):
        row, column = _coordinate(coords[index]), _coordinate(coords[index + 1])
        if row is None or column is None:
          continue
        cell = CursorCell(row=row, column=column)
        if shape == _REMOVE:
          self._shapes.pop(cell, None)
        elif row < rows and column < columns:
          self._shapes[cell] = shape
      return
    if coord_type != _COORD_REGIONS:
      return
    if len(coords) < 4:  # A region with no bounds means the whole screen.
      self._shapes.clear()
      if shape != _REMOVE:
        self._shapes = {CursorCell(row=row, column=column): shape for row in range(rows) for column in range(columns)}
      return
    regions = []
    for index in range(0, len(coords) - 3, 4):
      top, left = _coordinate(coords[index]), _coordinate(coords[index + 1])
      bottom, right = _coordinate(coords[index + 2]), _coordinate(coords[index + 3])
      if top is None or left is None or bottom is None or right is None:
        continue
      regions.append((top, left, min(bottom, rows - 1), min(right, columns - 1)))
    # Kitty empties every listed region first, then fills them.
    for cell in [
      cell for cell in self._shapes if any(t <= cell.row <= b and left <= cell.column <= r for t, left, b, r in regions)
    ]:
      del self._shapes[cell]
    if shape == _REMOVE:
      return
    for top, left, bottom, right in regions:
      for row in range(top, bottom + 1):
        for column in range(left, right + 1):
          self._shapes[CursorCell(row=row, column=column)] = shape


class MultiCursorReplyFilter:
  """Replace Kitty's emptied multiple-cursors report with the tracked one."""

  def __init__(self, tracker: MultiCursorTracker) -> None:
    self._tracker = tracker
    self._buffer = b""

  def feed(self, data: bytes) -> bytes:
    buffer = self._buffer + data
    self._buffer = b""
    if self._tracker.pending_state_queries <= 0:
      # Nothing is outstanding, so nothing here can be a reply to us, and
      # holding a partial match back would only delay the user's keys.
      return buffer
    output = bytearray()
    position = 0
    for match in _STATE_REPLY.finditer(buffer):
      if self._tracker.pending_state_queries <= 0:
        break
      output.extend(buffer[position : match.start()])
      output.extend(self._tracker.state_reply())
      self._tracker.pending_state_queries -= 1
      position = match.end()
    tail = buffer[position:]
    if self._tracker.pending_state_queries > 0:
      # A reply split across two reads has to be reassembled, which means
      # holding back anything that could still become one. While a query
      # is outstanding that can delay a bare Escape until the next key;
      # the window is the microseconds before Kitty answers, and losing
      # the tail of a real reply into the application would be worse.
      prefix = _STATE_REPLY_PREFIX.search(tail)
      if prefix is not None and prefix.group():
        self._buffer = prefix.group()
        tail = tail[: prefix.start()]
    output.extend(tail)
    return bytes(output)
