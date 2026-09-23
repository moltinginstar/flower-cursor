"""Focus reports are ours, not the child's, until the child asks for them."""

from __future__ import annotations

import pytest

from flower_cursor.focus import FOCUS_IN, FOCUS_OUT, UNCONFIRMED_FOCUS, FocusFilter, is_typing
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


def test_an_unconfirmed_window_gives_up_its_focus_after_the_grace() -> None:
    focus = FocusFilter(now=100.0)
    assert focus.deadline == 100.0 + UNCONFIRMED_FOCUS
    assert not focus.expire(100.0 + UNCONFIRMED_FOCUS - 0.01)
    assert focus.focused
    assert focus.expire(100.0 + UNCONFIRMED_FOCUS)
    assert not focus.focused and focus.deadline is None
    # Once is enough; the guess is not given up twice.
    assert not focus.expire(1000.0)


@pytest.mark.parametrize("report", [FOCUS_IN, FOCUS_OUT])
def test_a_report_is_the_answer_and_ends_the_grace(report: bytes) -> None:
    focus = FocusFilter(now=0.0)
    focus.feed(report)
    assert focus.confirmed and focus.deadline is None
    assert not focus.expire(UNCONFIRMED_FOCUS * 2)
    assert focus.focused == (report == FOCUS_IN)


def test_typing_confirms_focus_before_the_grace_runs_out() -> None:
    focus = FocusFilter(now=0.0)
    assert not focus.typed()
    assert focus.confirmed and focus.focused
    assert not focus.expire(UNCONFIRMED_FOCUS * 2)
    assert focus.focused


def test_typing_brings_back_a_window_that_gave_up_its_guess() -> None:
    focus = FocusFilter(now=0.0)
    focus.expire(UNCONFIRMED_FOCUS)
    assert focus.typed()
    assert focus.focused and focus.confirmed


def test_typing_does_not_override_a_report() -> None:
    # Kitty reports every change once reporting is on, so after one report a
    # keystroke has nothing to add - even one that arrives via `send-text`.
    focus = FocusFilter(now=0.0)
    focus.feed(FOCUS_OUT)
    assert not focus.typed()
    assert not focus.focused


def test_no_grace_keeps_the_guess() -> None:
    focus = FocusFilter(grace=None)
    assert focus.deadline is None
    assert not focus.expire(1e9)
    assert focus.focused


@pytest.mark.parametrize(
    "data",
    [
        b"x",
        b"\r",
        b"\t",
        b"\x7f",
        b"\x03",
        b"\x1bx",  # Alt-x
        b"\x1b[200~pasted\x1b[201~",
        b"\x1b[?1;1Rq",  # a reply and a key in one read
    ],
)
def test_keys_count_as_typing(data: bytes) -> None:
    assert is_typing(data)


@pytest.mark.parametrize(
    "data",
    [
        b"",
        b"\x1b[?9;13R",  # our cursor report
        b"\x1b[?62;22c",  # device attributes
        b"\x1b]11;rgb:0000/0000/0000\x1b\\",  # a colour reply, ST
        b"\x1b]11;rgb:0000/0000/0000\x07",  # a colour reply, BEL
        b"\x1b_Gi=1;OK\x1b\\",  # a graphics acknowledgement
        b"\x1bP1+r544e=787465726d\x1b\\",  # XTGETTCAP
        b"\x1b[<35;10;5M",  # the pointer moving over an unfocused window
        b"\x1b[A\x1bOB",  # arrows, in both encodings
        b"\x1b",  # a lone Escape
        b"\x1b\x1b[A",
    ],
)
def test_replies_and_pointer_reports_are_not_typing(data: bytes) -> None:
    assert not is_typing(data)
