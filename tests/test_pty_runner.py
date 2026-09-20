import os
import pty
import select
import subprocess
import sys
import time

from flower_cursor.cursor_visibility import CURSOR_HIDE, CURSOR_SHOW
from flower_cursor.kitty_graphics import transmit_png


def _run_graphics_child(
    chunks: list[bytes], *, query_between_chunks: bool = False
) -> tuple[bytes, int, int, bytes]:
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
                queries = output.count(b"\x1b[6n")
                for _ in range(queries - queries_seen):
                    os.write(master_fd, b"\x1b[1;1R")
                queries_seen = queries
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
        [sys.executable, "-c", runner_code], stdin=slave_fd, stdout=slave_fd,
        stderr=subprocess.PIPE, close_fds=True,
    )
    os.close(slave_fd)
    output = bytearray()
    queries_seen = 0
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
                while (start := output.find(b"\x1b[6n", start)) >= 0:
                    positions.append(start)
                    start += 4
                switch = output.index(b"\x1b[?1049h")
                for position in positions[queries_seen:]:
                    if output[max(0, position - 11):position] == b"CHILD_QUERY":
                        response = b"\x1b[9;9R"
                    elif position < switch:
                        response = b"\x1b[1;1R"
                    else:
                        response = b"\x1b[10;20R"
                    os.write(master_fd, response)
                queries_seen = len(positions)
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
    child_code = (
        "import os,time; os.write(1,b'\\x1b_Ga=T;'); time.sleep(0.15); os.write(1,b'AAAA\\x1b\\\\')"
    )
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
    transmit_png(b"x" * 4096, write=chunks.append)
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
    transmit_png(b"x" * 4096, write=chunks.append)
    output, returncode, queries_seen, stderr = _run_graphics_child(chunks)

    assert returncode == 0, stderr.decode()
    first = output.find(chunks[0])
    second = output.find(chunks[1])
    marker = output.find(b"FLOWER")
    assert output.startswith(CURSOR_HIDE)
    assert 0 <= first < second < marker
    assert output.rfind(CURSOR_SHOW) > marker
    assert queries_seen > 0
