import pytest

from flower_cursor.vt_framer import (
    AltScreenChange,
    CursorVisibility,
    DeviceStatusQuery,
    FocusReporting,
    MultipleCursors,
    ScreenErased,
    VTFramer,
)


def _events(*chunks: bytes) -> list[object]:
    framer = VTFramer()
    seen: list[object] = []
    for chunk in chunks:
        framer.feed(chunk, seen.append)
    return seen


def _boundaries(data: bytes) -> list[bool]:
    """Report whether the stream is at a boundary after each byte of *data*."""
    framer = VTFramer()
    states: list[bool] = []
    for index in range(len(data)):
        framer.feed(data[index : index + 1])
        states.append(framer.at_boundary)
    return states


def test_plain_text_is_always_a_boundary() -> None:
    assert all(_boundaries(b"hello world\n"))


@pytest.mark.parametrize(
    "sequence",
    [
        b"\x1b[31m",  # CSI with parameters
        b"\x1b[?1049h",  # private mode set
        b"\x1b[>29;2:4:5 q",  # multiple cursors, intermediate before the final byte
        b"\x1b]0;a title\x07",  # OSC terminated by BEL
        b"\x1b]52;c;aGVsbG8=\x1b\\",  # OSC 52 terminated by ST
        b"\x1bP+q544e\x1b\\",  # DCS
        b"\x1b_Gi=1,a=T;AAAA\x1b\\",  # APC graphics
        b"\x1b(B",  # escape with an intermediate
        b"\xc3\xa9",  # two-byte UTF-8
        b"\xf0\x9f\x8c\xb8",  # four-byte UTF-8 (an emoji)
    ],
)
def test_only_the_final_byte_of_a_sequence_is_a_boundary(sequence: bytes) -> None:
    states = _boundaries(sequence)

    assert states[-1] is True
    assert not any(states[:-1]), f"{sequence!r} reported a boundary mid-sequence"


def test_boundaries_survive_arbitrary_chunking() -> None:
    stream = b"a\x1b[1;2Hb\x1b]0;t\x07\xf0\x9f\x8c\xb8\x1b_Gi=1;AA\x1b\\c"
    whole = VTFramer()
    whole.feed(stream)

    for split in range(len(stream) + 1):
        framer = VTFramer()
        framer.feed(stream[:split])
        framer.feed(stream[split:])
        assert framer.at_boundary == whole.at_boundary


def test_multipart_graphics_transfer_blocks_between_chunks() -> None:
    framer = VTFramer()
    framer.feed(b"\x1b_Gi=1,a=T,m=1;AAAA\x1b\\")
    assert framer.in_graphics_transfer
    assert not framer.at_boundary

    framer.feed(b"\x1b_Gm=1;BBBB\x1b\\")
    assert not framer.at_boundary

    framer.feed(b"\x1b_Gm=0;CCCC\x1b\\")
    assert framer.at_boundary


def test_single_graphics_command_does_not_block() -> None:
    framer = VTFramer()
    framer.feed(b"\x1b_Gi=1,a=T;AAAA\x1b\\")

    assert framer.at_boundary


def test_unfinished_transfer_can_be_abandoned_without_writing() -> None:
    framer = VTFramer()
    framer.feed(b"\x1b_Gi=1,a=T,m=1;AAAA\x1b\\")
    framer.reset_graphics_transfer()

    assert framer.at_boundary


def test_alternate_screen_switches_are_reported() -> None:
    assert _events(b"\x1b[?1049h") == [AltScreenChange(active=True)]
    assert _events(b"\x1b[?1049l") == [AltScreenChange(active=False)]
    assert _events(b"\x1b[?47h") == [AltScreenChange(active=True)]
    assert _events(b"\x1b[?7h") == []


def test_device_status_queries_are_reported_with_their_privacy() -> None:
    assert _events(b"\x1b[6n") == [DeviceStatusQuery(parameter=6, private=False)]
    assert _events(b"\x1b[?6n") == [DeviceStatusQuery(parameter=6, private=True)]


def test_multiple_cursors_sequences_are_reported() -> None:
    assert _events(b"\x1b[>29;2:4:5 q") == [MultipleCursors(parameters=b"29;2:4:5")]
    assert _events(b"\x1b[>0;4 q") == [MultipleCursors(parameters=b"0;4")]
    # A cursor-shape request (DECSCUSR) uses the same final byte without the marker.
    assert _events(b"\x1b[2 q") == []


def test_cancel_aborts_a_sequence_in_progress() -> None:
    framer = VTFramer()
    framer.feed(b"\x1b[31")
    assert not framer.at_boundary

    framer.feed(b"\x18")
    assert framer.at_boundary


def test_stray_escape_inside_a_string_starts_a_new_sequence() -> None:
    framer = VTFramer()
    framer.feed(b"\x1b]0;title\x1b[")

    assert not framer.at_boundary
    framer.feed(b"m")
    assert framer.at_boundary


def test_a_slow_transfer_is_never_truncated_by_time() -> None:
    """No amount of waiting mid-sequence may make the framer claim a boundary."""
    framer = VTFramer()
    framer.feed(b"\x1b_Gi=1,a=T,m=1;AAAA\x1b\\")

    for _ in range(1000):
        assert not framer.at_boundary

    framer.feed(b"\x1b_Gm=0;BB\x1b\\")
    assert framer.at_boundary


def test_screen_erasures_that_drop_extra_cursors_are_reported() -> None:
    assert _events(b"\x1b[2J") == [ScreenErased(full_reset=False)]
    assert _events(b"\x1b[3J") == [ScreenErased(full_reset=False)]
    assert _events(b"\x1b[22J") == [ScreenErased(full_reset=False)]
    assert _events(b"\x1bc") == [ScreenErased(full_reset=True), CursorVisibility(visible=True)]
    # A line-scoped erase leaves the extra cursors alone.
    assert _events(b"\x1b[0J") == []
    assert _events(b"\x1b[J") == []
    assert _events(b"\x1b[2K") == []
    # screen.c runs DECSED through the same erase as ED, and routes DECSTR
    # through the same reset as RIS, so both drop the extra cursors too.
    assert _events(b"\x1b[?2J") == [ScreenErased(full_reset=False)]
    assert _events(b"\x1b[?3J") == [ScreenErased(full_reset=False)]
    assert _events(b"\x1b[?0J") == []
    # DECSTR leaves the screen itself alone, so it is not a full reset.
    assert _events(b"\x1b[!p") == [
        ScreenErased(full_reset=False),
        CursorVisibility(visible=True),
    ]


def test_cursor_visibility_requests_are_reported() -> None:
    assert _events(b"\x1b[?25l") == [CursorVisibility(visible=False)]
    assert _events(b"\x1b[?25h") == [CursorVisibility(visible=True)]
    # Nothing else in the sequence hides it: a mode list says all of what it says.
    assert _events(b"\x1b[?1049;25l") == [
        AltScreenChange(active=False),
        CursorVisibility(visible=False),
    ]
    assert _events(b"\x1b[?12;25h") == [CursorVisibility(visible=True)]
    assert _events(b"\x1b[?1004;25h") == [
        FocusReporting(enabled=True),
        CursorVisibility(visible=True),
    ]
    # Split across reads, and the unmarked mode 25 that is not DECTCEM at all.
    assert _events(b"\x1b[?2", b"5l") == [CursorVisibility(visible=False)]
    assert _events(b"\x1b[25l") == []
    # A reset puts the caret back, because a visible caret is DECTCEM's default.
    assert _events(b"\x1b[?25l\x1bc") == [
        CursorVisibility(visible=False),
        ScreenErased(full_reset=True),
        CursorVisibility(visible=True),
    ]
