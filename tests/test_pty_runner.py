import fcntl
import os
import pty
import select
import struct
import subprocess
import sys
import termios
import time

from flower_cursor.cursor_visibility import CURSOR_HIDE, CURSOR_SHOW
from flower_cursor.kitty_graphics import transmit_png

# The wrapper polls with DECXCPR; an application asks with plain DSR. The two
# are distinct byte strings, which is what keeps their replies from crossing.
_OUR_QUERY = b"\x1b[?6n"
_CHILD_QUERY = b"\x1b[6n"


def _run_graphics_child(chunks: list[bytes], *, query_between_chunks: bool = False) -> tuple[bytes, int, int, bytes]:
  query_code = (
    "os.write(1,b'\\x1b[6n')\n"
    "assert select.select([0],[],[],2)[0], 'child cursor query stalled'\n"
    "assert b'R' in os.read(0,128)\n"
    "os.write(1,b'CHILD_QUERY_OK')\n"
    if query_between_chunks
    else ""
  )
  child_code = (
    "import os,time,select,tty\n"
    "tty.setraw(0)\n"
    f"chunks = {chunks!r}\n"
    "os.write(1, chunks[0][:100])\n"
    "time.sleep(0.15)\n"
    "os.write(1, chunks[0][100:])\n"
    f"{query_code}"
    "time.sleep(0.15)\n"
    "os.write(1, chunks[1])\n"
  )
  runner_code = f"""
import sys
from flower_cursor.output_arbiter import OutputArbiter
from flower_cursor.pty_runner import run

def write_output(data):
    sys.stdout.buffer.write(data)
    sys.stdout.buffer.flush()

arbiter = OutputArbiter(write_output)
seen_child = False
wrote_tick = False

def on_output(data):
    global seen_child
    seen_child = True

def on_tick(_duration):
    global wrote_tick
    if seen_child and not wrote_tick:
        arbiter.write(b"FLOWER")
        wrote_tick = True

raise SystemExit(run(
    [sys.executable, "-c", {child_code!r}],
    on_output=on_output,
    on_tick=on_tick,
    output_arbiter=arbiter,
    query_interval=0.01,
    tick_interval=0.01,
    hide_cursor=True,
))
"""
  master_fd, slave_fd = pty.openpty()
  process = subprocess.Popen(
    [sys.executable, "-c", runner_code],
    stdin=slave_fd,
    stdout=slave_fd,
    stderr=subprocess.PIPE,
    close_fds=True,
  )
  os.close(slave_fd)
  os.set_blocking(master_fd, False)
  output = bytearray()
  queries_seen = 0
  child_queries_seen = 0
  deadline = time.monotonic() + 5
  try:
    while time.monotonic() < deadline:
      readable, _, _ = select.select([master_fd], [], [], 0.05)
      if readable:
        try:
          data = os.read(master_fd, 65536)
        except OSError:
          break
        if not data:
          break
        output.extend(data)
        queries = output.count(_OUR_QUERY)
        for _ in range(queries - queries_seen):
          os.write(master_fd, b"\x1b[?1;1R")
        queries_seen = queries
        child_queries = output.count(_CHILD_QUERY)
        for _ in range(child_queries - child_queries_seen):
          os.write(master_fd, b"\x1b[1;1R")
        child_queries_seen = child_queries
      elif process.poll() is not None:
        break
    returncode = process.wait(timeout=5)
    stderr = process.stderr.read() if process.stderr is not None else b""
  finally:
    os.close(master_fd)

  return bytes(output), returncode, queries_seen, stderr


def test_cursor_reports_keep_query_order_and_discard_previous_screen_generation() -> None:
  child_code = (
    "import os,time,select,tty; tty.setraw(0); time.sleep(0.08); "
    "os.write(1,b'\\x1b[?1049h'); time.sleep(0.08); "
    "os.write(1,b'CHILD_QUERY\\x1b[6n'); "
    "assert select.select([0],[],[],2)[0]; "
    "answer=os.read(0,128); assert answer==b'\\x1b[9;9R',repr(answer); "
    "os.write(1,b'CHILD_OK'); time.sleep(0.05)"
  )
  runner_code = f"""
import sys
from flower_cursor.pty_runner import run
raise SystemExit(run(
    [sys.executable, '-c', {child_code!r}],
    on_output=lambda data: b'\\x1b[?1049h' in data,
    on_cursor=lambda cell: print(f'CURSOR={{cell.row}},{{cell.column}}', file=sys.stderr),
    query_interval=0.01,
))
"""
  master_fd, slave_fd = pty.openpty()
  process = subprocess.Popen(
    [sys.executable, "-c", runner_code],
    stdin=slave_fd,
    stdout=slave_fd,
    stderr=subprocess.PIPE,
    close_fds=True,
  )
  os.close(slave_fd)
  output = bytearray()
  queries_seen = 0
  child_queries_seen = 0
  deadline = time.monotonic() + 5
  try:
    while time.monotonic() < deadline:
      readable, _, _ = select.select([master_fd], [], [], 0.05)
      if readable:
        try:
          data = os.read(master_fd, 65536)
        except OSError:
          break
        if not data:
          break
        output.extend(data)
        if b"CHILD_QUERY" not in output:
          continue
        start = 0
        positions = []
        while (start := output.find(_OUR_QUERY, start)) >= 0:
          positions.append(start)
          start += len(_OUR_QUERY)
        switch = output.index(b"\x1b[?1049h")
        for position in positions[queries_seen:]:
          # Our own polls come back marked, so the child never sees them.
          response = b"\x1b[?1;1R" if position < switch else b"\x1b[?10;20R"
          os.write(master_fd, response)
        queries_seen = len(positions)
        child_queries = output.count(_CHILD_QUERY)
        for _ in range(child_queries - child_queries_seen):
          os.write(master_fd, b"\x1b[9;9R")
        child_queries_seen = child_queries
      elif process.poll() is not None:
        break
    returncode = process.wait(timeout=5)
    stderr = process.stderr.read() if process.stderr is not None else b""
  finally:
    if process.poll() is None:
      process.terminate()
      process.wait(timeout=5)
    os.close(master_fd)
  assert returncode == 0, stderr.decode()
  assert b"CHILD_OK" in output
  assert stderr.splitlines()
  assert set(stderr.splitlines()) == {b"CURSOR=9,19"}


def test_runner_does_not_spin_on_expired_deadlines_while_output_is_blocked() -> None:
  child_code = "import os,time; os.write(1,b'\\x1b_Ga=T;'); time.sleep(0.15); os.write(1,b'AAAA\\x1b\\\\')"
  runner_code = f"""
import selectors
import sys
from flower_cursor.pty_runner import run

calls = 0
base = selectors.DefaultSelector
class CountingSelector(base):
    def select(self, timeout=None):
        global calls
        calls += 1
        return super().select(timeout)
selectors.DefaultSelector = CountingSelector
code = run([sys.executable, "-c", {child_code!r}], query_interval=0.01)
print("SELECT_CALLS=" + str(calls), file=sys.stderr)
raise SystemExit(code)
"""
  master_fd, slave_fd = pty.openpty()
  process = subprocess.Popen(
    [sys.executable, "-c", runner_code],
    stdin=slave_fd,
    stdout=slave_fd,
    stderr=subprocess.PIPE,
    close_fds=True,
  )
  os.close(slave_fd)
  deadline = time.monotonic() + 5
  try:
    while process.poll() is None and time.monotonic() < deadline:
      readable, _, _ = select.select([master_fd], [], [], 0.05)
      if readable:
        try:
          os.read(master_fd, 65536)
        except OSError:
          break
    returncode = process.wait(timeout=5)
    stderr = process.stderr.read() if process.stderr is not None else b""
  finally:
    os.close(master_fd)

  assert returncode == 0, stderr.decode()
  calls = int(stderr.decode().strip().removeprefix("SELECT_CALLS="))
  assert calls < 100


def test_child_cursor_query_completes_without_aborting_a_multipart_upload() -> None:
  chunks: list[bytes] = []
  transmit_png(b"x" * 4096, image_id=3, write=chunks.append)
  output, returncode, _, stderr = _run_graphics_child(chunks, query_between_chunks=True)
  assert returncode == 0, stderr.decode()
  first = output.find(chunks[0])
  answered = output.find(b"CHILD_QUERY_OK")
  second = output.find(chunks[1])
  marker = output.find(b"FLOWER")
  assert 0 <= first < answered < second < marker
  assert b"\x1b_Gm=0,q=2;\x1b\\" not in output


def test_runner_waits_for_split_multipart_child_graphics_before_ticking() -> None:
  chunks: list[bytes] = []
  transmit_png(b"x" * 4096, image_id=3, write=chunks.append)
  output, returncode, queries_seen, stderr = _run_graphics_child(chunks)

  assert returncode == 0, stderr.decode()
  first = output.find(chunks[0])
  second = output.find(chunks[1])
  marker = output.find(b"FLOWER")
  assert output.startswith(CURSOR_HIDE)
  assert 0 <= first < second < marker
  assert output.rfind(CURSOR_SHOW) > marker
  assert queries_seen > 0


def test_a_child_that_asks_with_decxcpr_still_receives_its_own_reply() -> None:
  """Our poll and the child's use the same marked reply, so they must be counted."""
  child_code = (
    "import os,time,select,tty; tty.setraw(0); time.sleep(0.08); "
    "os.write(1,b'CHILD_QUERY\\x1b[?6n'); "
    "assert select.select([0],[],[],2)[0]; "
    "answer=os.read(0,128); assert answer==b'\\x1b[?9;9R',repr(answer); "
    "os.write(1,b'CHILD_OK'); time.sleep(0.05)"
  )
  runner_code = f"""
import sys
from flower_cursor.pty_runner import run
raise SystemExit(run(
    [sys.executable, '-c', {child_code!r}],
    on_cursor=lambda cell: print(f'CURSOR={{cell.row}},{{cell.column}}', file=sys.stderr),
    query_interval=0.01,
))
"""
  master_fd, slave_fd = pty.openpty()
  process = subprocess.Popen(
    [sys.executable, "-c", runner_code],
    stdin=slave_fd,
    stdout=slave_fd,
    stderr=subprocess.PIPE,
    close_fds=True,
  )
  os.close(slave_fd)
  output = bytearray()
  answered = 0
  deadline = time.monotonic() + 5
  try:
    while time.monotonic() < deadline:
      readable, _, _ = select.select([master_fd], [], [], 0.05)
      if readable:
        try:
          data = os.read(master_fd, 65536)
        except OSError:
          break
        if not data:
          break
        output.extend(data)
        start = 0
        positions = []
        while (start := output.find(_OUR_QUERY, start)) >= 0:
          positions.append(start)
          start += len(_OUR_QUERY)
        for position in positions[answered:]:
          # The child's query carries a marker; ours does not.
          child = output[max(0, position - 11) : position] == b"CHILD_QUERY"
          os.write(master_fd, b"\x1b[?9;9R" if child else b"\x1b[?1;1R")
        answered = len(positions)
      elif process.poll() is not None:
        break
    returncode = process.wait(timeout=5)
    stderr = process.stderr.read() if process.stderr is not None else b""
  finally:
    if process.poll() is None:
      process.terminate()
      process.wait(timeout=5)
    os.close(master_fd)

  assert returncode == 0, stderr.decode()
  assert b"CHILD_OK" in output
  # The child's reply went to the child; only our own polls became cursor cells.
  assert set(stderr.splitlines()) == {b"CURSOR=0,0"}


def test_the_child_pty_keeps_the_windows_pixel_dimensions() -> None:
  """A child learns its cell size from ws_xpixel/ws_ypixel; zeroing them breaks icat."""
  script = "import fcntl,struct,termios\nprint(struct.unpack('HHHH', fcntl.ioctl(0, termios.TIOCGWINSZ, bytes(8))))\n"
  runner_code = f"""
import sys
from flower_cursor.pty_runner import run
raise SystemExit(run([sys.executable, '-c', {script!r}], query_interval=0.05))
"""
  master_fd, slave_fd = pty.openpty()
  fcntl.ioctl(slave_fd, termios.TIOCSWINSZ, struct.pack("HHHH", 24, 80, 640, 384))
  process = subprocess.Popen(
    [sys.executable, "-c", runner_code],
    stdin=slave_fd,
    stdout=slave_fd,
    stderr=subprocess.PIPE,
    close_fds=True,
  )
  os.close(slave_fd)
  output = bytearray()
  deadline = time.monotonic() + 5
  try:
    while time.monotonic() < deadline:
      readable, _, _ = select.select([master_fd], [], [], 0.05)
      if readable:
        try:
          data = os.read(master_fd, 65536)
        except OSError:
          break
        if not data:
          break
        output.extend(data)
      elif process.poll() is not None:
        break
    process.wait(timeout=5)
  finally:
    if process.poll() is None:
      process.terminate()
      process.wait(timeout=5)
    os.close(master_fd)

  assert b"(24, 80, 640, 384)" in output, bytes(output[-300:])


def test_unfocused_window_ticks_until_its_flowers_have_gone_then_stops() -> None:
  # The wrapper does not know what "gone" means; it asks, and the answer here
  # is a fixed number of frames standing in for the wither.
  leaving_frames = 5
  child_code = "import time; time.sleep(1.0)"
  runner_code = f"""
import sys
from flower_cursor.focus import FocusFilter
from flower_cursor.pty_runner import run

state = {{'focused': True, 'left': {leaving_frames}}}

def on_focus(focused):
    state['focused'] = focused
    print('FOCUS', focused, file=sys.stderr, flush=True)

def wants_tick():
    return not state['focused'] and state['left'] > 0

def on_tick(_duration):
    if not state['focused'] and state['left'] > 0:
        state['left'] -= 1
        print('LEAVING', file=sys.stderr, flush=True)

raise SystemExit(run(
    [sys.executable, '-c', {child_code!r}],
    on_tick=on_tick,
    focus_filter=FocusFilter(),
    on_focus=on_focus,
    wants_tick=wants_tick,
    query_interval=0.01,
    tick_interval=0.01,
))
"""
  master_fd, slave_fd = pty.openpty()
  process = subprocess.Popen(
    [sys.executable, "-c", runner_code],
    stdin=slave_fd,
    stdout=slave_fd,
    stderr=subprocess.PIPE,
    close_fds=True,
  )
  os.close(slave_fd)
  os.set_blocking(master_fd, False)
  output = bytearray()
  queries_seen = 0
  queries_at_focus_out = None
  deadline = time.monotonic() + 5
  try:
    while time.monotonic() < deadline:
      readable, _, _ = select.select([master_fd], [], [], 0.02)
      if readable:
        try:
          data = os.read(master_fd, 65536)
        except OSError:
          break
        if not data:
          break
        output.extend(data)
        queries = output.count(_OUR_QUERY)
        for _ in range(queries - queries_seen):
          os.write(master_fd, b"\x1b[?1;1R")
        queries_seen = queries
      if queries_at_focus_out is None and queries_seen >= 3:
        os.write(master_fd, b"\x1b[O")
        queries_at_focus_out = queries_seen
      if process.poll() is not None:
        break
    returncode = process.wait(timeout=5)
    stderr = process.stderr.read() if process.stderr is not None else b""
  finally:
    os.close(master_fd)

  assert returncode == 0, stderr
  assert b"FOCUS False" in stderr
  # Exactly the frames it asked for: the wither plays out in full, and then
  # the window goes idle instead of drawing another second of nothing.
  assert stderr.count(b"LEAVING") == leaving_frames, stderr
  # A caret that is not drawn is not worth asking about, so the polling stops
  # at once rather than lasting as long as the animation does.
  assert queries_seen - queries_at_focus_out <= 3, (queries_seen, queries_at_focus_out)


def _run_with_grace(keys: list[tuple[float, bytes]]) -> tuple[bytes, int, int]:
  """Run a window with a short focus grace, typing *keys* at their times.

  Returns what it said on stderr, how many cursor queries it made, and how
  many of them came after it gave up its focus.
  """
  child_code = "import time; time.sleep(1.2)"
  runner_code = f"""
import sys
from flower_cursor.focus import FocusFilter
from flower_cursor.pty_runner import run

def on_focus(focused):
    print('FOCUS', focused, file=sys.stderr, flush=True)

raise SystemExit(run(
    [sys.executable, '-c', {child_code!r}],
    focus_filter=FocusFilter(grace=0.4),
    on_focus=on_focus,
    query_interval=0.02,
))
"""
  master_fd, slave_fd = pty.openpty()
  process = subprocess.Popen(
    [sys.executable, "-c", runner_code],
    stdin=slave_fd,
    stdout=slave_fd,
    stderr=subprocess.PIPE,
    close_fds=True,
  )
  os.close(slave_fd)
  os.set_blocking(master_fd, False)
  output = bytearray()
  queries_seen = 0
  started = time.monotonic()
  pending = sorted(keys)
  try:
    while time.monotonic() - started < 5:
      readable, _, _ = select.select([master_fd], [], [], 0.01)
      if readable:
        try:
          data = os.read(master_fd, 65536)
        except OSError:
          break
        if not data:
          break
        output.extend(data)
        queries = output.count(_OUR_QUERY)
        # Kitty answers every query, focused or not; the answers are
        # not typing.
        for _ in range(queries - queries_seen):
          os.write(master_fd, b"\x1b[?1;1R")
        queries_seen = queries
      while pending and time.monotonic() - started >= pending[0][0]:
        os.write(master_fd, pending.pop(0)[1])
      if process.poll() is not None:
        break
    process.wait(timeout=5)
    stderr = process.stderr.read() if process.stderr is not None else b""
  finally:
    os.close(master_fd)
  return stderr, queries_seen, output.count(_OUR_QUERY)


def test_a_window_nothing_confirms_stops_drawing_after_the_grace() -> None:
  stderr, queries, _ = _run_with_grace([])
  assert stderr.splitlines() == [b"FOCUS False"], stderr
  # 0.4 s of polling at 50 Hz, then none for the rest of the second.
  assert queries <= 30, queries


def test_typing_during_the_grace_keeps_the_window_drawing() -> None:
  stderr, queries, _ = _run_with_grace([(0.1, b"x")])
  assert b"FOCUS" not in stderr, stderr
  assert queries > 30, queries


def test_typing_after_the_grace_brings_the_window_back() -> None:
  stderr, _, _ = _run_with_grace([(0.7, b"x")])
  assert stderr.splitlines() == [b"FOCUS False", b"FOCUS True"], stderr
