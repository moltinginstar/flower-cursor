from flower_cursor.cursor_visibility import CURSOR_HIDE, CURSOR_SHOW, CursorVisibilityFilter


def test_child_show_cursor_requests_are_replaced() -> None:
    visibility = CursorVisibilityFilter()

    assert visibility.feed(b"before" + CURSOR_SHOW + b"after") == b"before" + CURSOR_HIDE + b"after"


def test_show_cursor_sequence_split_across_reads_is_replaced() -> None:
    visibility = CursorVisibilityFilter()

    assert visibility.feed(b"before\x1b[?2") == b"before"
    assert visibility.feed(b"5hafter") == CURSOR_HIDE + b"after"


def test_other_terminal_output_is_preserved() -> None:
    visibility = CursorVisibilityFilter()
    data = b"\x1b[2J\x1b[?25ltext"

    assert visibility.feed(data) == data
    assert visibility.flush() == b""
