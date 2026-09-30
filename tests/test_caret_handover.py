"""An application that draws its own caret gets the cell to itself."""

import os
import pty
import re
import select
import struct
import subprocess
import sys
import termios
import time

from flower_cursor.cursor_visibility import CURSOR_HIDE

# Grow a flower, hide the caret the way a program that draws its own does, then
# ask for it back with the caret bundled into a mode list, as a real sequence
# can be. `MARK` separates the three stretches in the recorded output, and
# `SETTLED` marks the point in the middle one by which the flowers that were on
# screen when the caret was handed over have had time to finish leaving.
_CHILD = """
import os, select, sys, time, tty
tty.setraw(0)
def pause(seconds):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if select.select([0], [], [], 0.05)[0] and b'q' in os.read(0, 256):
            raise SystemExit(0)
os.write(1, b'READY')
pause(1.0)
os.write(1, b'MARK\\x1b[?25l')
pause(2.0)
os.write(1, b'SETTLED')
pause(1.5)
os.write(1, b'MARK\\x1b[?12;25h')
pause(1.5)
"""
_RUNNER = f"""
import sys
from flower_cursor.observer import main
raise SystemExit(main(['--bee', 'off', '--', sys.executable, '-c', {_CHILD!r}]))
"""


def _drive() -> bytes:
  master, slave = pty.openpty()
  fcntl_size = struct.pack("HHHH", 30, 100, 1600, 990)
  import fcntl

  fcntl.ioctl(slave, termios.TIOCSWINSZ, fcntl_size)
  process = subprocess.Popen([sys.executable, "-c", _RUNNER], stdin=slave, stdout=slave, stderr=subprocess.PIPE)
  os.close(slave)
  output = bytearray()
  tail = b""
  started = time.monotonic()
  try:
    while time.monotonic() - started < 12:
      readable, _, _ = select.select([master], [], [], 0.05)
      if not readable:
        if process.poll() is not None:
          break
        continue
      try:
        chunk = os.read(master, 65536)
      except OSError:
        break
      if not chunk:
        break
      output.extend(chunk)
      queries = tail + chunk
      for _ in range(queries.count(b"\x1b[?6n")):
        os.write(master, b"\x1b[?9;13R")
      tail = queries[-4:]
    returncode = process.wait(timeout=5)
    assert returncode == 0, process.stderr.read().decode()
    return bytes(output)
  finally:
    if process.poll() is None:
      process.terminate()
      process.wait(timeout=5)
    os.close(master)


def _sections(output: bytes) -> tuple[bytes, bytes, bytes, bytes]:
  drawing, _, rest = output.partition(b"MARK")
  handed_over, _, rest = rest.partition(b"SETTLED")
  settled, _, returned = rest.partition(b"MARK")
  assert drawing and handed_over and returned, "the child never reached all three stretches"
  return drawing, handed_over, settled, returned


def _flower_transmissions(section: bytes) -> int:
  return len(re.findall(rb"\x1b_G[^;]*a=T[^;]*;", section))


def test_a_child_that_hides_the_caret_is_left_to_draw_its_own() -> None:
  drawing, handed_over, settled, returned = _sections(_drive())

  assert _flower_transmissions(drawing) > 0
  # The flowers that were on screen leave the way they do when a window loses
  # focus, so the stretch right after the hide still draws; once they are
  # gone the cell belongs to the child alone.
  assert _flower_transmissions(handed_over) > 0
  assert _flower_transmissions(settled) == 0
  # Nothing to draw a caret for is nothing to ask Kitty about either.
  assert settled.count(b"\x1b[?6n") == 0
  assert _flower_transmissions(returned) > 0
  assert returned.count(b"\x1b[?6n") > 0


def test_kitty_s_own_caret_stays_hidden_when_the_child_asks_for_it_back() -> None:
  output = _drive()
  *_, returned = _sections(output)

  assert output.startswith(CURSOR_HIDE)
  # The child's request reaches Kitty - it owns the mode for itself - and is
  # answered with our own, so only one caret is ever drawn.
  assert returned.startswith(b"\x1b[?12;25h")
  assert CURSOR_HIDE in returned
