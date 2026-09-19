"""Run a terminal child while observing Kitty's cursor-position reports."""

from __future__ import annotations

import math
import os
import pty
import selectors
import signal
import sys
import termios
import time
import tty
from collections.abc import Callable, Sequence

from .cursor_report import CursorReportParser
from .cursor_visibility import CURSOR_HIDE, CURSOR_SHOW, CursorVisibilityFilter
from .lifecycle import CursorCell
from .output_arbiter import OutputArbiter

_CPR_QUERY = b"\x1b[6n"


def _write_all(fd: int, data: bytes) -> None:
    view = memoryview(data)
    while view:
        view = view[os.write(fd, view) :]


def _sync_window_size(master_fd: int, stdin_fd: int) -> None:
    termios.tcsetwinsize(master_fd, termios.tcgetwinsize(stdin_fd))


def _terminate_child(child_pid: int) -> None:
    try:
        os.killpg(child_pid, signal.SIGHUP)
    except ProcessLookupError:
        pass


def run(
    command: Sequence[str],
    *,
    on_cursor: Callable[[CursorCell], None] | None = None,
    on_tick: Callable[[float], None] | None = None,
    on_close: Callable[[], None] | None = None,
    on_output: Callable[[bytes], None] | None = None,
    output_arbiter: OutputArbiter | None = None,
    query_interval: float = 1 / 30,
    tick_interval: float = 1 / 60,
    hide_cursor: bool = False,
) -> int:
    """Run *command* behind a PTY and report Kitty cursor cells to a callback."""
    if not command:
        raise ValueError("command must not be empty")
    if query_interval <= 0:
        raise ValueError("query_interval must be positive")
    if tick_interval <= 0:
        raise ValueError("tick_interval must be positive")
    stdin_fd = sys.stdin.fileno()
    stdout_fd = sys.stdout.fileno()
    if not os.isatty(stdin_fd) or not os.isatty(stdout_fd):
        raise OSError("flower cursor requires a TTY on stdin and stdout")
    old_terminal = termios.tcgetattr(stdin_fd)

    child_pid, master_fd = pty.fork()
    if child_pid == 0:
        os.execvp(command[0], list(command))

    parser = CursorReportParser()
    output_arbiter = output_arbiter or OutputArbiter(lambda data: _write_all(stdout_fd, data))
    selector = selectors.DefaultSelector()
    child_queries_pending = 0
    child_output_tail = b""
    cursor_visibility = CursorVisibilityFilter() if hide_cursor else None
    resize_pending = True
    now = time.monotonic()
    next_query = now
    next_tick = now
    last_tick = now
    resize_handler = signal.getsignal(signal.SIGWINCH)

    def request_resize(_signum: int, _frame: object) -> None:
        nonlocal resize_pending
        resize_pending = True

    signal.signal(signal.SIGWINCH, request_resize)
    try:
        tty.setraw(stdin_fd, termios.TCSAFLUSH)
        if cursor_visibility is not None:
            output_arbiter.write(CURSOR_HIDE)
        _sync_window_size(master_fd, stdin_fd)
        selector.register(stdin_fd, selectors.EVENT_READ, "stdin")
        selector.register(master_fd, selectors.EVENT_READ, "master")
        while True:
            now = time.monotonic()
            if output_arbiter.can_write:
                deadlines = [next_query]
                if on_tick is not None:
                    deadlines.append(next_tick)
            else:
                recovery_deadline = output_arbiter.recovery_deadline
                deadlines = [recovery_deadline] if recovery_deadline is not None else []
            timeout = max(0.0, min(deadlines) - now) if deadlines else None
            events = selector.select(timeout)
            for key, _mask in events:
                if key.data == "stdin":
                    data = os.read(stdin_fd, 4096)
                    if not data:
                        _terminate_child(child_pid)
                        _, status = os.waitpid(child_pid, 0)
                        return os.waitstatus_to_exitcode(status)
                    passthrough, reports = parser.feed(data)
                    if passthrough:
                        _write_all(master_fd, passthrough)
                    for report in reports:
                        if child_queries_pending:
                            _write_all(master_fd, report.raw)
                            child_queries_pending -= 1
                        if on_cursor is not None:
                            on_cursor(report.cell)
                else:
                    try:
                        raw_data = os.read(master_fd, 4096)
                    except OSError:
                        raw_data = b""
                    if not raw_data:
                        _, status = os.waitpid(child_pid, 0)
                        return os.waitstatus_to_exitcode(status)
                    data = (
                        cursor_visibility.feed(raw_data)
                        if cursor_visibility is not None
                        else raw_data
                    )
                    if data:
                        output_arbiter.feed_child(data)
                        if on_output is not None:
                            on_output(data)
                    child_output = child_output_tail + raw_data
                    child_queries_pending += child_output.count(_CPR_QUERY)
                    child_output_tail = child_output[-len(_CPR_QUERY) + 1 :]

            if resize_pending:
                _sync_window_size(master_fd, stdin_fd)
                resize_pending = False
            now = time.monotonic()
            output_arbiter.poll(now)
            if on_tick is not None and now >= next_tick and output_arbiter.can_write:
                on_tick(now - last_tick)
                last_tick = now
                missed_ticks = max(1, math.ceil((now - next_tick) / tick_interval))
                next_tick += missed_ticks * tick_interval
            if now >= next_query and output_arbiter.can_write:
                output_arbiter.write(_CPR_QUERY)
                next_query = now + query_interval
            waited_pid, status = os.waitpid(child_pid, os.WNOHANG)
            if waited_pid == child_pid:
                return os.waitstatus_to_exitcode(status)
    finally:
        if cursor_visibility is not None:
            pending = cursor_visibility.flush()
            if pending:
                output_arbiter.feed_child(pending)
        output_arbiter.finish_child()
        _terminate_child(child_pid)
        try:
            os.waitpid(child_pid, 0)
        except ChildProcessError:
            pass
        try:
            if on_close is not None:
                on_close()
        finally:
            if cursor_visibility is not None:
                output_arbiter.write(CURSOR_SHOW)
        selector.close()
        os.close(master_fd)
        signal.signal(signal.SIGWINCH, resize_handler)
        termios.tcsetattr(stdin_fd, termios.TCSAFLUSH, old_terminal)
