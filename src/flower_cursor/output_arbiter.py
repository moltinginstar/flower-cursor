"""Serialize child terminal output with cursor-renderer protocol writes."""

from __future__ import annotations

import time
from collections.abc import Callable

from .vt_framer import Event, VTFramer

# Only our own bookkeeping can wedge; a genuinely slow upload must never be cut
# short, so this is long enough that no real transfer reaches it.
_STUCK_TRANSFER_TIMEOUT = 5.0


class OutputArbiter:
    """Keep renderer writes out of the middle of the child's escape sequences.

    Child bytes are forwarded the moment they arrive - nothing is buffered and
    nothing is ever truncated. A `VTFramer` follows the stream and reports
    whether it currently sits at a sequence boundary; renderer writes are
    admitted only there.
    """

    def __init__(
        self,
        write: Callable[[bytes], object],
        *,
        stuck_transfer_timeout: float = _STUCK_TRANSFER_TIMEOUT,
        clock: Callable[[], float] = time.monotonic,
        on_event: Callable[[Event], object] | None = None,
    ) -> None:
        if stuck_transfer_timeout <= 0:
            raise ValueError("stuck_transfer_timeout must be positive")
        self._write = write
        self._stuck_transfer_timeout = stuck_transfer_timeout
        self._clock = clock
        self._listeners: list[Callable[[Event], object]] = []
        if on_event is not None:
            self._listeners.append(on_event)
        self._framer = VTFramer()
        self._transfer_since: float | None = None

    def add_listener(self, listener: Callable[[Event], object]) -> None:
        """Subscribe to the sequences the framer recognises in the child stream."""
        self._listeners.append(listener)

    @property
    def can_write(self) -> bool:
        return self._framer.at_boundary

    @property
    def recovery_deadline(self) -> float | None:
        """When to give up on an upload we believe is still in flight.

        Only a multipart transfer has a deadline. Sitting part-way through an
        escape sequence has none: the child owes us the rest of it, and writing
        before it arrives would corrupt it however long we wait.
        """
        if self._transfer_since is None:
            return None
        return self._transfer_since + self._stuck_transfer_timeout

    def write(self, data: bytes) -> None:
        """Write runner-owned bytes when the child's stream is at a boundary."""
        if not data:
            return
        if not self.can_write:
            raise RuntimeError("cannot write in the middle of a child escape sequence")
        self._write(data)

    def feed_child(self, data: bytes) -> None:
        """Forward child bytes immediately, tracking where its sequences end."""
        if not data:
            return
        self._framer.feed(data, self._dispatch)
        self._write(data)
        self._track_transfer()

    def poll(self, now: float | None = None) -> None:
        """Abandon an upload that never finished, without emitting anything."""
        if self._transfer_since is None:
            return
        current = self._clock() if now is None else now
        if current - self._transfer_since >= self._stuck_transfer_timeout:
            self._framer.reset_graphics_transfer()
            self._transfer_since = None

    def finish_child(self) -> None:
        """Allow cleanup writes once the child has stopped producing output.

        A child that died part-way through a sequence will never finish it, so
        the dangling sequence is cancelled with CAN - the abort the parser
        already defines - rather than left for the terminal to misread.
        """
        self._framer.reset_graphics_transfer()
        self._transfer_since = None
        if not self._framer.at_boundary:
            cancel = b"\x18"
            self._framer.feed(cancel)
            self._write(cancel)

    def _dispatch(self, event: Event) -> None:
        for listener in self._listeners:
            listener(event)

    def _track_transfer(self) -> None:
        if self._framer.in_graphics_transfer:
            if self._transfer_since is None:
                self._transfer_since = self._clock()
        else:
            self._transfer_since = None
