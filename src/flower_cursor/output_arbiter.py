"""Serialize child terminal output with cursor-renderer protocol writes."""

from __future__ import annotations

import time
from collections.abc import Callable

_GRAPHICS_START = b"\x1b_G"
_ST = b"\x1b\\"
_RECOVERY_FINAL = b"\x1b_Gm=0,q=2;\x1b\\"
_CONTINUATION_KEYS = {b"a", b"m", b"q"}


class OutputArbiter:
    """Keep external writes out of incomplete Kitty graphics transfers."""

    def __init__(
        self,
        write: Callable[[bytes], object],
        *,
        max_buffered_bytes: int = 64 * 1024,
        incomplete_timeout: float = 0.25,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if max_buffered_bytes < len(_GRAPHICS_START):
            raise ValueError("max_buffered_bytes is too small")
        if incomplete_timeout <= 0:
            raise ValueError("incomplete_timeout must be positive")
        self._write = write
        self._max_buffered_bytes = max_buffered_bytes
        self._incomplete_timeout = incomplete_timeout
        self._clock = clock
        self._pending = bytearray()
        self._multipart = False
        self._blocked_since: float | None = None

    @property
    def can_write(self) -> bool:
        return not self._multipart and not self._pending

    @property
    def recovery_deadline(self) -> float | None:
        if self._blocked_since is None:
            return None
        return self._blocked_since + self._incomplete_timeout

    def write(self, data: bytes) -> None:
        """Write runner-owned bytes when no child graphics sequence is active."""
        if not data:
            return
        if not self.can_write:
            raise RuntimeError("cannot write while child graphics output is incomplete")
        self._write(data)

    def feed_child(self, data: bytes) -> None:
        """Forward child bytes while preserving Kitty graphics framing."""
        if not data:
            return
        self._pending.extend(data)
        self._drain()
        if len(self._pending) > self._max_buffered_bytes:
            self._recover()
        if self.can_write:
            self._blocked_since = None
        else:
            self._mark_blocked()

    def poll(self, now: float | None = None) -> None:
        """Release a malformed sequence after the bounded recovery interval."""
        if self.can_write:
            return
        current = self._clock() if now is None else now
        if (
            self._blocked_since is not None
            and current - self._blocked_since >= self._incomplete_timeout
        ):
            self._recover()

    def finish_child(self) -> None:
        """Finish child forwarding before cleanup graphics and cursor restoration."""
        if not self.can_write:
            self._recover()

    def _drain(self) -> None:
        while self._pending:
            start = self._pending.find(_GRAPHICS_START)
            if start < 0:
                keep = _partial_prefix_length(self._pending)
                if keep:
                    if len(self._pending) > keep and not self._multipart:
                        self._blocked_since = None
                    self._emit(self._pending[:-keep])
                    del self._pending[:-keep]
                else:
                    self._emit(self._pending)
                    self._pending.clear()
                return
            if start:
                self._emit(self._pending[:start])
                del self._pending[:start]
                if not self._multipart:
                    self._blocked_since = None
            end = self._pending.find(_ST, len(_GRAPHICS_START))
            if end < 0:
                return
            end += len(_ST)
            command = bytes(self._pending[:end])
            del self._pending[:end]
            self._emit(command)
            if not self._multipart or _is_continuation(command):
                self._multipart = _is_multipart_start(command)
            self._blocked_since = self._clock() if self._multipart else None

    def _recover(self) -> None:
        pending = bytes(self._pending)
        multipart = self._multipart
        self._pending.clear()
        self._multipart = False
        self._blocked_since = None
        if pending:
            self._emit(pending)
            if not pending.endswith(_ST):
                self._emit(_ST)
        if multipart:
            self._emit(_RECOVERY_FINAL)

    def _mark_blocked(self) -> None:
        if not self.can_write and self._blocked_since is None:
            self._blocked_since = self._clock()

    def _emit(self, data: bytes | bytearray) -> None:
        if data:
            self._write(bytes(data))


def _partial_prefix_length(data: bytes | bytearray) -> int:
    for length in range(min(len(data), len(_GRAPHICS_START)), 0, -1):
        if bytes(data[-length:]) == _GRAPHICS_START[:length]:
            return length
    return 0


def _parameters(command: bytes) -> dict[bytes, bytes]:
    header_end = command.find(b";")
    if header_end < len(_GRAPHICS_START):
        return {}
    parameters: dict[bytes, bytes] = {}
    for item in command[len(_GRAPHICS_START) : header_end].split(b","):
        key, separator, value = item.partition(b"=")
        if separator:
            parameters[key] = value
    return parameters


def _parameter(command: bytes, key: bytes) -> bytes | None:
    return _parameters(command).get(key)


def _is_multipart_start(command: bytes) -> bool:
    return _parameter(command, b"m") == b"1"


def _is_continuation(command: bytes) -> bool:
    parameters = _parameters(command)
    if set(parameters) - _CONTINUATION_KEYS:
        return False
    if parameters.get(b"m") not in {b"0", b"1"}:
        return False
    return parameters.get(b"a", b"f") == b"f"
