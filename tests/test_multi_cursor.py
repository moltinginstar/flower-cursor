"""Tracking Kitty's multiple-cursors protocol from the child's output stream."""

from __future__ import annotations

import pytest

from flower_cursor.lifecycle import CursorCell
from flower_cursor.multi_cursor import (
  CLEAR_ALL,
  MultiCursorReplyFilter,
  MultiCursorTracker,
)
from flower_cursor.vt_framer import AltScreenChange, MultipleCursors, ScreenErased


def tracker(rows: int = 10, columns: int = 20) -> MultiCursorTracker:
  result = MultiCursorTracker()
  result.screen_size = (rows, columns)
  return result


def feed(target: MultiCursorTracker, *commands: bytes) -> MultiCursorTracker:
  for command in commands:
    target.handle(MultipleCursors(parameters=command))
  return target


def cells(*pairs: tuple[int, int]) -> tuple[CursorCell, ...]:
  return tuple(CursorCell(row=row, column=column) for row, column in pairs)


def test_declared_points_become_cursors_in_screen_order() -> None:
  # The protocol is 1-based; a flower lands on the same cell Kitty would.
  target = feed(tracker(), b"2;2:5:6:1:2")
  assert target.cells == cells((0, 1), (4, 5))


def test_shape_zero_removes_only_the_named_points() -> None:
  target = feed(tracker(), b"2;2:1:1:2:2", b"0;2:1:1")
  assert target.cells == cells((1, 1))


def test_the_main_cursor_coordinate_type_follows_the_real_caret() -> None:
  target = tracker()
  target.main_cursor = CursorCell(row=3, column=7)
  feed(target, b"2;0")
  assert target.cells == cells((3, 7))


def test_a_command_before_the_caret_is_known_places_nothing() -> None:
  target = feed(tracker(), b"2;0")
  assert target.cells == ()


def test_a_region_fills_its_rectangle() -> None:
  target = feed(tracker(), b"1;4:2:3:3:4")
  assert target.cells == cells((1, 2), (1, 3), (2, 2), (2, 3))


def test_a_region_is_emptied_before_it_is_filled() -> None:
  target = feed(tracker(), b"2;2:1:1:1:2", b"1;4:1:1:1:1")
  assert target.cells == cells((0, 0), (0, 1))
  assert target.state_reply() == b"\x1b[>100;1:2:1:1;2:2:1:2 q"


def test_a_region_is_clipped_to_the_screen() -> None:
  target = feed(tracker(rows=2, columns=3), b"1;4:1:1:99:99")
  assert len(target.cells) == 6


def test_a_bare_region_means_the_whole_screen() -> None:
  target = feed(tracker(rows=2, columns=3), b"1;4")
  assert len(target.cells) == 6
  feed(target, b"0;4")
  assert target.cells == ()


def test_several_groups_apply_in_one_command() -> None:
  target = feed(tracker(), b"2;2:1:1;4:3:3:4:4")
  assert target.cells == cells((0, 0), (2, 2), (2, 3), (3, 2), (3, 3))


def test_points_outside_the_screen_are_discarded() -> None:
  # Kitty stores coordinates unsigned, so a zero wraps out of range.
  target = feed(tracker(rows=2, columns=2), b"2;2:0:1:1:0:9:9")
  assert target.cells == ()


@pytest.mark.parametrize(
  "command",
  [
    b"30;2:255:0:0",  # extra-cursor text color
    b"40;5:4",  # extra-cursor cursor color
    b"7;2:1:1",  # a shape Kitty does not recognize
    b"",  # CSI > SP q, the supported-shapes query
  ],
)
def test_commands_that_place_no_cursor_leave_the_set_alone(command: bytes) -> None:
  target = feed(tracker(), b"2;2:1:1")
  feed(target, command)
  assert target.cells == cells((0, 0))


def test_kittys_own_cursors_are_cleared_once_per_batch_of_commands() -> None:
  target = feed(tracker(), b"2;2:1:1", b"2;2:2:2")
  assert target.take_suppression() == CLEAR_ALL
  assert target.take_suppression() == b""


def test_a_command_that_places_nothing_asks_for_no_clearing() -> None:
  assert feed(tracker(), b"30;1").take_suppression() == b""


@pytest.mark.parametrize(
  "event",
  [
    ScreenErased(full_reset=False),
    ScreenErased(full_reset=True),
    AltScreenChange(active=True),
    AltScreenChange(active=False),
  ],
)
def test_kitty_dropping_its_cursors_drops_ours_with_nothing_left_to_clear(event) -> None:
  target = feed(tracker(), b"2;2:1:1")
  target.take_suppression()
  target.handle(event)
  assert target.cells == ()
  assert target.take_suppression() == b""


def test_the_state_reply_reports_an_empty_set_the_way_kitty_does() -> None:
  assert tracker().state_reply() == b"\x1b[>100 q"


def test_the_state_reply_collapses_shapes_kitty_cannot_name() -> None:
  target = feed(tracker(), b"29;2:1:1")
  assert target.state_reply() == b"\x1b[>100;29:2:1:1 q"


def test_the_state_query_is_answered_from_our_record_not_kittys() -> None:
  target = feed(tracker(), b"2;2:4:5", b"100")
  reply_filter = MultiCursorReplyFilter(target)
  # Kitty answers the query after we cleared its cursors, so it reports none.
  assert reply_filter.feed(b"\x1b[>100 q") == b"\x1b[>100;2:2:4:5 q"
  assert target.pending_state_queries == 0


def test_an_unrequested_report_is_passed_through_untouched() -> None:
  target = feed(tracker(), b"2;2:4:5")
  assert MultiCursorReplyFilter(target).feed(b"\x1b[>100 q") == b"\x1b[>100 q"


def test_a_reply_split_across_reads_is_still_replaced_whole() -> None:
  target = feed(tracker(), b"2;2:4:5", b"100")
  reply_filter = MultiCursorReplyFilter(target)
  assert reply_filter.feed(b"x\x1b[>10") == b"x"
  assert reply_filter.feed(b"0;1:2:3:4 ") == b""
  assert reply_filter.feed(b"qy") == b"\x1b[>100;2:2:4:5 qy"


def test_typing_is_never_held_back_waiting_for_a_reply() -> None:
  target = feed(tracker(), b"100")
  reply_filter = MultiCursorReplyFilter(target)
  assert reply_filter.feed(b"\x1b[A") == b"\x1b[A"
  assert reply_filter.feed(b"ls\r") == b"ls\r"


def test_only_as_many_reports_are_claimed_as_were_asked_for() -> None:
  target = feed(tracker(), b"2;2:4:5", b"100")
  reply_filter = MultiCursorReplyFilter(target)
  # The second report answers somebody else's query and must survive intact.
  replaced = reply_filter.feed(b"\x1b[>100 q\x1b[>100 q")
  assert replaced == b"\x1b[>100;2:2:4:5 q\x1b[>100 q"
  assert target.pending_state_queries == 0
