"""Draw nothing in a pane Kitty is not focusing.

Kitty gives every window in a tab except the active one `cursor_opacity = 0`
(`child-monitor.c`), so a native caret simply is not drawn in an inactive
split. Our flower is a graphics placement, which Kitty composites regardless of
which window has focus, so without this every pane shows a full bloom at once
and every pane pays to animate one.

Kitty reports focus to a window that has asked for mode 1004
(`screen.c:focus_changed`), and `tabs.py` calls that on split focus changes as
well as OS window ones: exactly the distinction we need. We hold the mode on
for the lifetime of the session; an application that turns it off for itself
only stops the reports being forwarded to it, and we re-assert the mode so
Kitty keeps telling us.

What Kitty never tells us is where focus starts. `Window.focus_changed` and
`screen.c:focus_changed` both act only on a change, and a window's first one
happens as it is created (before its program can have asked for reports) or,
for a window opened behind another (`launch --keep-focus`, a session's other
tabs), not at all until it is visited. Nothing a program can send asks the
question. So a window starts out assuming it is focused, which is right for
nearly every window, and takes either of two things as the answer: a report,
which is exact, or the user typing, since Kitty sends keys only to the focused
window. With neither after `UNCONFIRMED_FOCUS` seconds it assumes it was opened
in the background and stops drawing until it hears otherwise.
"""

from __future__ import annotations

import time

FOCUS_ENABLE = b"\x1b[?1004h"
FOCUS_DISABLE = b"\x1b[?1004l"
FOCUS_IN = b"\x1b[I"
FOCUS_OUT = b"\x1b[O"

_REPORTS = (FOCUS_IN, FOCUS_OUT)

# How long a window that has been told nothing about its focus keeps assuming it
# has it. A background window draws this long before it goes quiet,
# and a new window left untouched this long withers until the next key.
UNCONFIRMED_FOCUS = 30.0

_CSI = 0x5B
_SS3 = 0x4F
# OSC, DCS, APC, PM and SOS all run to BEL or ST.
_STRINGS = frozenset(b"]P_^X")


def is_typing(data: bytes) -> bool:
  """Whether terminal input holds anything a person typed.

  Replies to queries (cursor reports, device attributes, colors, graphics
  acknowledgments) are always whole escape sequences, and so are mouse
  reports, which Kitty sends to a window under the pointer whether or not it
  is focused. A printable character, Enter, Tab, Backspace, a paste or an
  Alt-chord is none of those. Keys that arrive as sequences of their own,
  such as the arrows, are not counted; the first ordinary key is.
  """
  index, end = 0, len(data)
  while index < end:
    if data[index] != 0x1B:
      return True
    index += 1
    if index == end:
      return False
    introducer = data[index]
    if introducer == 0x1B:
      continue
    if introducer == _CSI:
      index += 1
      while index < end and not 0x40 <= data[index] <= 0x7E:
        index += 1
      index += 1
    elif introducer in _STRINGS:
      index += 1
      while index < end:
        if data[index] == 0x07:
          index += 1
          break
        if data[index] == 0x1B and index + 1 < end and data[index + 1] == 0x5C:
          index += 2
          break
        index += 1
    elif introducer == _SS3:
      index += 2
    else:
      # Escape and then a character is how Alt-chords are sent.
      return True
  return False


class FocusFilter:
  """Take Kitty's focus reports out of the input stream and track the state.

  The reports are ours: we asked for them, so they are removed before the
  child sees them unless the child asked for them too.
  """

  def __init__(self, *, grace: float | None = UNCONFIRMED_FOCUS, now: float | None = None) -> None:
    self.focused = True
    # Whether a report or a keystroke has said so, rather than the guess a
    # window opens with. Once one has, Kitty reports every change after it.
    self.confirmed = False
    start = time.monotonic() if now is None else now
    self.deadline: float | None = None if grace is None else start + grace
    # Set when the child turns mode 1004 on for itself. Until then the
    # reports are ours alone and are not forwarded.
    self.child_wants_reports = False
    self._pending = b""

  def feed(self, data: bytes) -> tuple[bytes, bool]:
    """Return (bytes for the child, whether focus changed)."""
    buffer = self._pending + data
    self._pending = b""
    output = bytearray()
    changed = False
    position = 0
    while position < len(buffer):
      index = buffer.find(b"\x1b", position)
      if index < 0:
        output.extend(buffer[position:])
        position = len(buffer)
        break
      output.extend(buffer[position:index])
      tail = buffer[index:]
      for report in _REPORTS:
        if tail.startswith(report):
          focused = report == FOCUS_IN
          changed = changed or focused != self.focused
          self.focused = focused
          self.confirmed = True
          self.deadline = None
          if self.child_wants_reports:
            output.extend(report)
          position = index + len(report)
          break
      else:
        # A partial sequence is held back only while it could still
        # become a focus report: a lone Escape waits for what follows
        # it, or for `flush`. Anything else (an arrow key) goes
        # straight through, because delaying a keystroke to
        # disambiguate it would be felt.
        if len(tail) < 3 and any(report.startswith(tail) for report in _REPORTS):
          self._pending = bytes(tail)
          return bytes(output), changed
        output.extend(buffer[index : index + 1])
        position = index + 1
    return bytes(output), changed

  def typed(self) -> bool:
    """The user typed here, so this is the focused window; return whether that changed."""
    if self.confirmed:
      return False
    self.confirmed = True
    self.deadline = None
    changed = not self.focused
    self.focused = True
    return changed

  def expire(self, now: float) -> bool:
    """Give up the opening guess once nothing has confirmed it; return whether that changed."""
    if self.deadline is None or now < self.deadline:
      return False
    self.deadline = None
    changed = self.focused
    self.focused = False
    return changed

  @property
  def has_pending(self) -> bool:
    """True while a partial sequence is being held back."""
    return bool(self._pending)

  def flush(self) -> bytes:
    """Release a held partial sequence that never completed."""
    pending = self._pending
    self._pending = b""
    return pending
