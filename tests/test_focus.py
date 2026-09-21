"""Focus reports are ours, not the child's, until the child asks for them."""

from __future__ import annotations

from flower_cursor.focus import FOCUS_IN, FOCUS_OUT, FocusFilter
from flower_cursor.vt_framer import AltScreenChange, FocusReporting, VTFramer


def events(data: bytes) -> list[object]:
    seen: list[object] = []
    VTFramer().feed(data, seen.append)
    return seen


def test_starts_focused_and_passes_ordinary_input_through() -> None:
    focus = FocusFilter()
    assert focus.focused
    assert focus.feed(b"ls -l\r") == (b"ls -l\r", False)


def test_reports_are_consumed_and_change_the_state() -> None:
    focus = FocusFilter()
    assert focus.feed(FOCUS_OUT) == (b"", True)
    assert not focus.focused
    assert focus.feed(FOCUS_IN) == (b"", True)
    assert focus.focused


def test_repeated_report_is_not_a_change() -> None:
    focus = FocusFilter()
    focus.feed(FOCUS_OUT)
    assert focus.feed(FOCUS_OUT) == (b"", False)


def test_report_split_across_reads_is_still_recognised() -> None:
    focus = FocusFilter()
    assert focus.feed(b"\x1b[") == (b"", False)
    assert focus.has_pending
    assert focus.feed(b"O") == (b"", True)
    assert not focus.focused
    assert not focus.has_pending


def test_a_lone_escape_is_held_only_until_it_is_flushed() -> None:
    focus = FocusFilter()
    assert focus.feed(b"\x1b") == (b"", False)
    assert focus.flush() == b"\x1b"
    assert not focus.has_pending


def test_other_escape_sequences_are_not_delayed() -> None:
    focus = FocusFilter()
    assert focus.feed(b"\x1b[A\x1b[B") == (b"\x1b[A\x1b[B", False)
    assert not focus.has_pending


def test_reports_reach_a_child_that_asked_for_them() -> None:
    focus = FocusFilter()
    focus.child_wants_reports = True
    assert focus.feed(b"a" + FOCUS_OUT + b"b") == (b"a" + FOCUS_OUT + b"b", True)


def test_framer_reports_the_childs_use_of_the_mode() -> None:
    assert events(b"\x1b[?1004h") == [FocusReporting(enabled=True)]
    assert events(b"\x1b[?1004l") == [FocusReporting(enabled=False)]


def test_a_bundled_screen_switch_still_reports_the_switch() -> None:
    assert events(b"\x1b[?1004;1049h") == [AltScreenChange(active=True)]
