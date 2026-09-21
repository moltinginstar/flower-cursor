"""Parse terminal cursor-position reports without consuming normal input."""

from __future__ import annotations

import re
from dataclasses import dataclass

from .lifecycle import CursorCell

# DECXCPR (CSI ? 6 n) replies carry a `?` marker that a plain DSR reply does
# not, so an application's own CSI r;c R reply can be left alone entirely.
_CPR = re.compile(rb"\x1b\[\?(\d+);(\d+)R")


@dataclass(frozen=True, slots=True)
class CursorReport:
    cell: CursorCell
    raw: bytes


def _is_cpr_prefix(value: bytes) -> bool:
    if value in (b"\x1b", b"\x1b["):
        return True
    if not value.startswith(b"\x1b[?"):
        return False
    body = value[3:]
    if body.endswith(b"R") or body.count(b";") > 1:
        return False
    return all(byte == ord(";") or ord("0") <= byte <= ord("9") for byte in body)


class CursorReportParser:
    """Incrementally split DECXCPR replies from bytes headed to the child PTY."""

    def __init__(self) -> None:
        self._pending = b""

    @property
    def has_pending(self) -> bool:
        """Whether bytes are being held back waiting for the rest of a reply."""
        return bool(self._pending)

    def flush(self) -> bytes:
        """Give up on a held partial and hand it over as ordinary input.

        A lone Escape looks like the start of a report, so it is held. Without
        this the byte would sit here until the next keystroke, which in
        observe-only mode - where no focus filter holds it first - makes Escape
        look dead in vim or readline.
        """
        pending, self._pending = self._pending, b""
        return pending

    def feed(self, data: bytes) -> tuple[bytes, tuple[CursorReport, ...]]:
        buffer = self._pending + data
        self._pending = b""
        output = bytearray()
        reports: list[CursorReport] = []
        position = 0
        for match in _CPR.finditer(buffer):
            output.extend(buffer[position : match.start()])
            row, column = (int(value) for value in match.groups())
            reports.append(
                CursorReport(
                    cell=CursorCell(row=max(0, row - 1), column=max(0, column - 1)),
                    raw=match.group(0),
                )
            )
            position = match.end()

        tail = buffer[position:]
        for index in range(len(tail)):
            if _is_cpr_prefix(tail[index:]):
                output.extend(tail[:index])
                self._pending = tail[index:]
                return bytes(output), tuple(reports)
        output.extend(tail)
        return bytes(output), tuple(reports)
