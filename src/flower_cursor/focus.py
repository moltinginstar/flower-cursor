"""Draw nothing in a pane Kitty is not focusing.

Kitty gives every window in a tab except the active one `cursor_opacity = 0`
(`child-monitor.c`), so a native caret simply is not drawn in an inactive
split. Our flower is a graphics placement, which Kitty composites regardless of
which window has focus, so without this every pane shows a full bloom at once
and every pane pays to animate one.

Kitty reports focus to a window that has asked for mode 1004
(`screen.c:focus_changed`), and `tabs.py` calls that on split focus changes as
well as OS window ones - exactly the distinction we need. We hold the mode on
for the lifetime of the session; an application that turns it off for itself
only stops the reports being forwarded to it, and we re-assert the mode so
Kitty keeps telling us.
"""

from __future__ import annotations

FOCUS_ENABLE = b"\x1b[?1004h"
FOCUS_DISABLE = b"\x1b[?1004l"
FOCUS_IN = b"\x1b[I"
FOCUS_OUT = b"\x1b[O"

_REPORTS = (FOCUS_IN, FOCUS_OUT)


class FocusFilter:
    """Take Kitty's focus reports out of the input stream and track the state.

    The reports are ours: we asked for them, so they are removed before the
    child sees them unless the child asked for them too.
    """

    def __init__(self) -> None:
        self.focused = True
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
                    if self.child_wants_reports:
                        output.extend(report)
                    position = index + len(report)
                    break
            else:
                # A partial sequence is held back only while it could still
                # become a focus report. Anything else - a lone Escape, an
                # arrow key - goes straight through, because delaying a
                # keystroke to disambiguate it would be felt.
                if len(tail) < 3 and any(report.startswith(tail) for report in _REPORTS):
                    self._pending = bytes(tail)
                    return bytes(output), changed
                output.extend(buffer[index : index + 1])
                position = index + 1
        return bytes(output), changed

    @property
    def has_pending(self) -> bool:
        """True while a partial sequence is being held back."""
        return bool(self._pending)

    def flush(self) -> bytes:
        """Release a held partial sequence that never completed."""
        pending = self._pending
        self._pending = b""
        return pending
