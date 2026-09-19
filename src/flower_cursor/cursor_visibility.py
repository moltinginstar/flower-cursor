"""Keep Kitty's native cursor hidden while flower mode owns the visible caret."""

from __future__ import annotations

CURSOR_HIDE = b"\x1b[?25l"
CURSOR_SHOW = b"\x1b[?25h"


class CursorVisibilityFilter:
    """Replace child requests to show the native cursor with another hide request."""

    def __init__(self) -> None:
        self._pending = b""

    def feed(self, data: bytes) -> bytes:
        buffer = self._pending + data
        self._pending = b""
        output = bytearray()
        position = 0
        while True:
            match = buffer.find(CURSOR_SHOW, position)
            if match < 0:
                break
            output.extend(buffer[position:match])
            output.extend(CURSOR_HIDE)
            position = match + len(CURSOR_SHOW)

        tail = buffer[position:]
        for index in range(len(tail)):
            if CURSOR_SHOW.startswith(tail[index:]):
                output.extend(tail[:index])
                self._pending = tail[index:]
                return bytes(output)
        output.extend(tail)
        return bytes(output)

    def flush(self) -> bytes:
        pending = self._pending
        self._pending = b""
        return pending
