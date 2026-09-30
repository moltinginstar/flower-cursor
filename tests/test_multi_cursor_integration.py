"""The whole multiple-cursors path, end to end through a real PTY."""

from __future__ import annotations

import os
import pty
import select
import subprocess
import sys
import time

from flower_cursor.multi_cursor import CLEAR_ALL

# What Kitty answers once its own extra cursors have been cleared.
_EMPTY_REPORT = b"\x1b[>100 q"

_CHILD = (
  "import os,time,select,tty\n"
  "tty.setraw(0)\n"
  "os.write(1, b'\\x1b[>2;2:3:4;2:5:6 q')\n"
  "time.sleep(0.3)\n"
  "os.write(1, b'\\x1b[>100 q')\n"
  "buffer = b''\n"
  "deadline = time.time() + 5\n"
  "while not buffer.endswith(b'q') and time.time() < deadline:\n"
  "    if select.select([0],[],[],0.2)[0]: buffer += os.read(0,256)\n"
  "os.write(1, b'REPLY[' + buffer.replace(b'\\x1b', b'ESC') + b']')\n"
)

_RUNNER = f"""
import sys
from flower_cursor.multi_cursor import MultiCursorReplyFilter, MultiCursorTracker
from flower_cursor.output_arbiter import OutputArbiter
from flower_cursor.pty_runner import run

def write_output(data):
    sys.stdout.buffer.write(data)
    sys.stdout.buffer.flush()

arbiter = OutputArbiter(write_output)
tracker = MultiCursorTracker()
tracker.screen_size = (24, 80)
arbiter.add_listener(tracker.handle)
reply_filter = MultiCursorReplyFilter(tracker)

def on_tick(_duration):
    suppression = tracker.take_suppression()
    if suppression:
        arbiter.write(suppression)

raise SystemExit(run(
    [sys.executable, "-c", {_CHILD!r}],
    on_tick=on_tick,
    output_arbiter=arbiter,
    stdin_filter=reply_filter.feed,
    query_interval=0.01,
    tick_interval=0.01,
))
"""


def _drive() -> bytes:
  """Play the terminal: run the wrapper on a PTY and answer its reports."""
  master_fd, slave_fd = pty.openpty()
  process = subprocess.Popen(
    [sys.executable, "-c", _RUNNER],
    stdin=slave_fd,
    stdout=slave_fd,
    stderr=subprocess.PIPE,
  )
  os.close(slave_fd)
  seen = bytearray()
  answered = False
  deadline = time.time() + 20
  try:
    while time.time() < deadline and b"REPLY[" not in seen:
      if not select.select([master_fd], [], [], 0.2)[0]:
        continue
      try:
        chunk = os.read(master_fd, 4096)
      except OSError:
        break
      if not chunk:
        break
      seen.extend(chunk)
      if not answered and _EMPTY_REPORT in seen:
        # Kitty replies to the query the child sent through us.
        answered = True
        os.write(master_fd, _EMPTY_REPORT)
  finally:
    process.terminate()
    process.wait(timeout=10)
    os.close(master_fd)
  return bytes(seen)


def test_kittys_cursors_are_cleared_and_its_report_answered_from_our_record() -> None:
  output = _drive()
  assert CLEAR_ALL in output, output[-400:]
  assert b"REPLY[ESC[>100;2:2:3:4;2:2:5:6 q]" in output, output[-400:]
