"""Parse terminal cursor-position reports without consuming normal input."""

from __future__ import annotations

import re
from dataclasses import dataclass

from .lifecycle import CursorCell

_CPR = re.compile(rb"\x1b\[(\d+);(\d+)R")


@dataclass(frozen=True, slots=True)
class CursorReport:
    cell: CursorCell
    raw: bytes


def _is_cpr_prefix(value: bytes) -> bool:
    if value == b"\x1b":
        return True
    if not value.startswith(b"\x1b["):
        return False
    body = value[2:]
    if not body or body.endswith(b"R") or body.count(b";") > 1:
        return False
    return all(byte == ord(";") or ord("0") <= byte <= ord("9") for byte in body)


class CursorReportParser:
    """Incrementally split CPR replies from bytes headed to the child PTY."""

    def __init__(self) -> None:
        self._pending = b""

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
