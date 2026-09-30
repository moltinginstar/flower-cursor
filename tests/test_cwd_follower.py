"""Following the shell's OSC 7 reports.

With the native cursor hidden, Kitty stops trusting the reported directory and
reads the foreground process's instead, so the wrapper has to hold the same
answer the shell is giving.
"""

import socket

from flower_cursor.cwd_follower import CwdFollower


def _osc7(path: str, host: str | None = None) -> bytes:
  host = socket.gethostname() if host is None else host
  return f"\x1b]7;file://{host}{path}\x1b\\".encode()


def test_a_reported_directory_is_returned_once() -> None:
  follower = CwdFollower()
  assert follower.feed(_osc7("/tmp")) == "/tmp"
  assert follower.feed(_osc7("/tmp")) is None


def test_the_bell_terminator_is_accepted() -> None:
  follower = CwdFollower()
  host = socket.gethostname()
  assert follower.feed(f"\x1b]7;file://{host}/var\x07".encode()) == "/var"


def test_a_report_split_across_reads_is_reassembled() -> None:
  """Output arrives in whatever sized pieces the PTY hands over."""
  whole = _osc7("/usr/local")
  for split in range(len(whole) + 1):
    follower = CwdFollower()
    first = follower.feed(whole[:split])
    second = follower.feed(whole[split:])
    assert (first or second) == "/usr/local"


def test_the_last_report_in_a_chunk_wins() -> None:
  follower = CwdFollower()
  assert follower.feed(_osc7("/one") + _osc7("/two")) == "/two"


def test_percent_escapes_are_decoded() -> None:
  follower = CwdFollower()
  assert follower.feed(_osc7("/tmp/a%20b")) == "/tmp/a b"


def test_a_directory_on_another_machine_is_ignored() -> None:
  """Over ssh the path names nothing here; Kitty draws the same line."""
  follower = CwdFollower()
  assert follower.feed(_osc7("/home/somebody", host="somewhere-else")) is None


def test_other_escape_codes_are_left_alone() -> None:
  follower = CwdFollower()
  assert follower.feed(b"\x1b]0;a title\x07\x1b[2Jhello") is None


def test_an_unterminated_report_cannot_grow_without_bound() -> None:
  follower = CwdFollower()
  for _ in range(64):
    assert follower.feed(b"\x1b]7;file://host/" + b"x" * 4096) is None
  assert len(follower._tail) <= 4096
