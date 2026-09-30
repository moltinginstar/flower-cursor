"""Spot the output that takes the flower's images off the screen.

Kitty drops every image on the screen being erased (ED 2/3/22 and DECSED all
reach `grman_clear` in `screen.c`), on a full reset (RIS), and keeps a
separate set per screen when an application switches to the alternate one.
`FlowerRenderer` follows those itself when it is handed the child's output;
this finds them for a wrapper whose renderer is in another process, so only
the sequences that matter cross over rather than everything the child writes.

The pattern is `FlowerRenderer.observe_terminal_output`'s, and the renderer is
fed exactly what matched, so it reaches the same state it would have from the
whole stream. DECSTR is deliberately absent: a soft reset drops the extra
cursors but leaves the images alone.
"""

from __future__ import annotations

import re

_RESETS = re.compile(rb"\x1b(?:c|\[(?:\??(?:22|2|3)J|\?([0-9;]+)([hl])))")
_SCREEN_MODES = frozenset((b"47", b"1047", b"1049"))
# Longer than any sequence above, so one split across two reads is still seen.
_TAIL = 128


class ScreenResets:
  """Pick the screen-changing sequences out of a child's output, read by read."""

  def __init__(self) -> None:
    self._tail = b""

  def feed(self, data: bytes) -> list[bytes]:
    previous = len(self._tail)
    combined = self._tail + data
    found = []
    for match in _RESETS.finditer(combined):
      if match.end() <= previous:
        continue
      modes = match.group(1)
      if modes is not None and not _SCREEN_MODES.intersection(modes.split(b";")):
        continue
      found.append(match.group())
    self._tail = combined[-_TAIL:]
    return found
