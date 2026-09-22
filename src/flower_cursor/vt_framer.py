"""Track VT sequence boundaries in a terminal output stream.

The renderer injects Kitty graphics commands into the same stream the child
writes to. Injecting in the middle of any escape sequence - or between the
bytes of a UTF-8 character - corrupts it, so the runtime needs to know when the
stream is at a boundary. This is a framer, not an emulator: it recognises where
sequences begin and end, and leaves their meaning to Kitty. The state machine
follows the DEC VT500-series parser described at https://vt100.net/emu/dec_ansi_parser.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum, auto

_ESC = 0x1B
_BEL = 0x07
_CAN = 0x18
_SUB = 0x1A

# A control string's meaning lives in its opening bytes; only those are kept.
_MAX_INSPECTED_PREFIX = 256


class _State(Enum):
    GROUND = auto()
    ESCAPE = auto()
    ESCAPE_INTERMEDIATE = auto()
    CSI_ENTRY = auto()
    CSI_PARAM = auto()
    CSI_INTERMEDIATE = auto()
    CSI_IGNORE = auto()
    DCS_ENTRY = auto()
    DCS_PARAM = auto()
    DCS_INTERMEDIATE = auto()
    DCS_PASSTHROUGH = auto()
    DCS_IGNORE = auto()
    STRING = auto()


@dataclass(frozen=True, slots=True)
class AltScreenChange:
    """The child switched between the main and alternate screens."""

    active: bool


@dataclass(frozen=True, slots=True)
class FocusReporting:
    """The child turned Kitty's focus reporting (mode 1004) on or off."""

    enabled: bool


@dataclass(frozen=True, slots=True)
class CursorVisibility:
    """The child turned the text cursor (DECTCEM, mode 25) on or off."""

    visible: bool


@dataclass(frozen=True, slots=True)
class DeviceStatusQuery:
    """The child asked the terminal for a status report."""

    parameter: int
    private: bool


@dataclass(frozen=True, slots=True)
class ScreenErased:
    """The child erased the screen (ED) or reset the terminal (RIS).

    Kitty drops every extra cursor on these, so anything mirroring the
    multiple-cursors protocol has to drop them too.
    """

    full_reset: bool


@dataclass(frozen=True, slots=True)
class MultipleCursors:
    """The child used Kitty's multiple cursors protocol (CSI > ... SP q)."""

    parameters: bytes


Event = (
    AltScreenChange
    | CursorVisibility
    | DeviceStatusQuery
    | FocusReporting
    | MultipleCursors
    | ScreenErased
)

_ALT_SCREEN_MODES = {47, 1047, 1049}
_CURSOR_VISIBILITY_MODE = 25
_FOCUS_REPORTING_MODE = 1004
# ED parameters that clear a screen region large enough for Kitty to drop the
# extra cursors: the whole screen, the scrollback, and both together.
_ERASING_ED_PARAMETERS = {2, 3, 22}


def _utf8_continuation_count(byte: int) -> int:
    if byte < 0xC0:
        return 0
    if byte < 0xE0:
        return 1
    if byte < 0xF0:
        return 2
    if byte < 0xF8:
        return 3
    return 0


class VTFramer:
    """Follow a terminal output stream and report safe injection points."""

    def __init__(self) -> None:
        self._state = _State.GROUND
        self._utf8_pending = 0
        self._collected = bytearray()
        self._private = 0
        self._intermediates = bytearray()
        self._string_escape = False
        self._graphics_transfer = False
        # One sequence can carry several modes, and `_consume` reports one
        # event per byte; anything a sequence says beyond its first event waits
        # here until `feed` has somewhere to report it.
        self._extra_events: deque[Event] = deque()

    @property
    def at_boundary(self) -> bool:
        """True when nothing would be split by writing bytes right now."""
        return (
            self._state is _State.GROUND and not self._utf8_pending and not self._graphics_transfer
        )

    @property
    def in_graphics_transfer(self) -> bool:
        """True while the child is part-way through a multipart image upload."""
        return self._graphics_transfer

    def reset_graphics_transfer(self) -> None:
        """Forget an unfinished upload without emitting anything into the stream."""
        self._graphics_transfer = False

    def feed(self, data: bytes, on_event: Callable[[Event], object] | None = None) -> None:
        """Advance the parser over *data*, reporting events as they dispatch."""
        for byte in data:
            event = self._consume(byte)
            if on_event is None:
                self._extra_events.clear()
                continue
            if event is not None:
                on_event(event)
            while self._extra_events:
                on_event(self._extra_events.popleft())

    def _consume(self, byte: int) -> Event | None:
        # CAN and SUB abort whatever is in progress, from any state.
        if byte in (_CAN, _SUB):
            self._enter_ground()
            return None

        if self._state in (_State.STRING, _State.DCS_PASSTHROUGH):
            return self._consume_string(byte)

        if byte == _ESC:
            self._begin_escape()
            return None

        handler = _HANDLERS[self._state]
        return handler(self, byte)

    def _consume_string(self, byte: int) -> Event | None:
        if self._string_escape:
            self._string_escape = False
            if byte == 0x5C:  # ST
                return self._finish_string()
            # A stray ESC abandons the string and starts a new sequence.
            self._enter_ground()
            self._begin_escape()
            return None
        if byte == _ESC:
            self._string_escape = True
            return None
        if byte == _BEL:
            return self._finish_string()
        if len(self._collected) < _MAX_INSPECTED_PREFIX:
            self._collected.append(byte)
        return None

    def _ground(self, byte: int) -> Event | None:
        if self._utf8_pending:
            self._utf8_pending -= 1
            return None
        if byte >= 0x80:
            self._utf8_pending = _utf8_continuation_count(byte)
        return None

    def _escape(self, byte: int) -> Event | None:
        if byte == 0x5B:  # [
            self._state = _State.CSI_ENTRY
        elif byte == 0x50:  # P
            self._state = _State.DCS_ENTRY
        elif byte in (0x58, 0x5E, 0x5F, 0x5D):  # X, ^, _, ]
            self._state = _State.STRING
        elif 0x20 <= byte <= 0x2F:
            self._intermediates.append(byte)
            self._state = _State.ESCAPE_INTERMEDIATE
        elif 0x30 <= byte <= 0x7E:
            self._enter_ground()
            if byte == 0x63:  # c - RIS
                # A reset restores every mode's default, and DECTCEM's default
                # is a visible caret.
                self._extra_events.append(CursorVisibility(visible=True))
                return ScreenErased(full_reset=True)
        return None

    def _escape_intermediate(self, byte: int) -> Event | None:
        if 0x20 <= byte <= 0x2F:
            self._intermediates.append(byte)
        elif 0x30 <= byte <= 0x7E:
            self._enter_ground()
        return None

    def _csi_entry(self, byte: int) -> Event | None:
        if 0x3C <= byte <= 0x3F:
            self._private = byte
            self._state = _State.CSI_PARAM
        elif 0x30 <= byte <= 0x3B:
            self._collected.append(byte)
            self._state = _State.CSI_PARAM
        elif 0x20 <= byte <= 0x2F:
            self._intermediates.append(byte)
            self._state = _State.CSI_INTERMEDIATE
        elif 0x40 <= byte <= 0x7E:
            return self._dispatch_csi(byte)
        return None

    def _csi_param(self, byte: int) -> Event | None:
        if 0x30 <= byte <= 0x3B:
            self._collected.append(byte)
        elif 0x3C <= byte <= 0x3F:
            self._state = _State.CSI_IGNORE
        elif 0x20 <= byte <= 0x2F:
            self._intermediates.append(byte)
            self._state = _State.CSI_INTERMEDIATE
        elif 0x40 <= byte <= 0x7E:
            return self._dispatch_csi(byte)
        return None

    def _csi_intermediate(self, byte: int) -> Event | None:
        if 0x20 <= byte <= 0x2F:
            self._intermediates.append(byte)
        elif 0x30 <= byte <= 0x3F:
            self._state = _State.CSI_IGNORE
        elif 0x40 <= byte <= 0x7E:
            return self._dispatch_csi(byte)
        return None

    def _csi_ignore(self, byte: int) -> Event | None:
        if 0x40 <= byte <= 0x7E:
            self._enter_ground()
        return None

    def _dcs_entry(self, byte: int) -> Event | None:
        if 0x3C <= byte <= 0x3F:
            self._private = byte
            self._state = _State.DCS_PARAM
        elif 0x30 <= byte <= 0x3B:
            self._state = _State.DCS_PARAM
        elif 0x20 <= byte <= 0x2F:
            self._state = _State.DCS_INTERMEDIATE
        elif 0x40 <= byte <= 0x7E:
            self._state = _State.DCS_PASSTHROUGH
        return None

    def _dcs_param(self, byte: int) -> Event | None:
        if 0x30 <= byte <= 0x3B:
            return None
        if 0x3C <= byte <= 0x3F:
            self._state = _State.DCS_IGNORE
        elif 0x20 <= byte <= 0x2F:
            self._state = _State.DCS_INTERMEDIATE
        elif 0x40 <= byte <= 0x7E:
            self._state = _State.DCS_PASSTHROUGH
        return None

    def _dcs_intermediate(self, byte: int) -> Event | None:
        if 0x20 <= byte <= 0x2F:
            return None
        if 0x30 <= byte <= 0x3F:
            self._state = _State.DCS_IGNORE
        elif 0x40 <= byte <= 0x7E:
            self._state = _State.DCS_PASSTHROUGH
        return None

    def _dcs_ignore(self, byte: int) -> Event | None:
        # A DCS string still runs to its terminator even when ignored.
        self._state = _State.DCS_PASSTHROUGH
        return self._consume_string(byte)

    def _dispatch_csi(self, final: int) -> Event | None:
        parameters = bytes(self._collected)
        private = self._private
        intermediates = bytes(self._intermediates)
        self._enter_ground()

        if final in (0x68, 0x6C) and private == 0x3F:  # h / l with ?
            values = _numeric_parameters(parameters)
            if _CURSOR_VISIBILITY_MODE in values:
                # Whether the child wants a caret is independent of everything
                # else a mode sequence can say, so it is reported alongside
                # rather than instead: `?25;1049l` means both.
                self._extra_events.append(CursorVisibility(visible=final == 0x68))
            # Of the rest only one event can be reported, so the screen switch
            # wins: it decides whether anything is drawn at all. Focus reporting
            # is re-asserted on that event too, which is what a bundled
            # `?1004;1049l` would otherwise cost us.
            for value in values:
                if value in _ALT_SCREEN_MODES:
                    return AltScreenChange(active=final == 0x68)
            if _FOCUS_REPORTING_MODE in values:
                return FocusReporting(enabled=final == 0x68)
            return None
        if final == 0x6E and not intermediates:  # n
            values = _numeric_parameters(parameters)
            if values:
                return DeviceStatusQuery(parameter=values[0], private=private == 0x3F)
            return None
        if final == 0x71 and private == 0x3E and intermediates == b" ":  # > ... SP q
            return MultipleCursors(parameters=parameters)
        # DECSED (`?`-marked) runs the same erase as ED in screen.c, so the
        # private marker is deliberately not checked here.
        if final == 0x4A and private in (0, 0x3F) and not intermediates:  # J
            values = _numeric_parameters(parameters)
            if values and values[0] in _ERASING_ED_PARAMETERS:
                return ScreenErased(full_reset=False)
        if final == 0x70 and not private and intermediates == b"!":  # ! p - DECSTR
            # A soft reset drops the extra cursors exactly as RIS does; it
            # leaves the screen alone, so it is not reported as a full reset.
            # DECTCEM is one of the modes it puts back, so the caret returns.
            self._extra_events.append(CursorVisibility(visible=True))
            return ScreenErased(full_reset=False)
        return None

    def _finish_string(self) -> Event | None:
        content = bytes(self._collected)
        self._enter_ground()
        if content.startswith(b"G"):
            self._update_graphics_transfer(content)
        return None

    def _update_graphics_transfer(self, content: bytes) -> None:
        header = content[1 : content.find(b";") if b";" in content else len(content)]
        more = None
        for item in header.split(b","):
            key, separator, value = item.partition(b"=")
            if separator and key == b"m":
                more = value
        # Only chunked transfers carry m; a command without it is self-contained.
        if more is not None:
            self._graphics_transfer = more == b"1"

    def _begin_escape(self) -> None:
        self._collected.clear()
        self._intermediates.clear()
        self._private = 0
        self._string_escape = False
        self._utf8_pending = 0
        self._state = _State.ESCAPE

    def _enter_ground(self) -> None:
        self._collected.clear()
        self._intermediates.clear()
        self._private = 0
        self._string_escape = False
        self._state = _State.GROUND


def _numeric_parameters(parameters: bytes) -> tuple[int, ...]:
    values: list[int] = []
    for item in parameters.split(b";"):
        digits = item.split(b":")[0]
        if digits.isdigit():
            values.append(int(digits))
    return tuple(values)


_HANDLERS: dict[_State, Callable[[VTFramer, int], Event | None]] = {
    _State.GROUND: VTFramer._ground,
    _State.ESCAPE: VTFramer._escape,
    _State.ESCAPE_INTERMEDIATE: VTFramer._escape_intermediate,
    _State.CSI_ENTRY: VTFramer._csi_entry,
    _State.CSI_PARAM: VTFramer._csi_param,
    _State.CSI_INTERMEDIATE: VTFramer._csi_intermediate,
    _State.CSI_IGNORE: VTFramer._csi_ignore,
    _State.DCS_ENTRY: VTFramer._dcs_entry,
    _State.DCS_PARAM: VTFramer._dcs_param,
    _State.DCS_INTERMEDIATE: VTFramer._dcs_intermediate,
    _State.DCS_IGNORE: VTFramer._dcs_ignore,
}
