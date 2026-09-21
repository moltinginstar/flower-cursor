"""Run a terminal child while observing Kitty's cursor-position reports."""

from __future__ import annotations

import fcntl
import math
import os
import pty
import selectors
import signal
import struct
import sys
import termios
import time
import tty
from collections import deque
from collections.abc import Callable, Sequence

from .cursor_report import CursorReportParser
from .cursor_visibility import CURSOR_HIDE, CURSOR_SHOW, CursorVisibilityFilter
from .focus import FOCUS_DISABLE, FOCUS_ENABLE, FocusFilter
from .lifecycle import CursorCell
from .output_arbiter import OutputArbiter
from .vt_framer import AltScreenChange, DeviceStatusQuery, Event, FocusReporting

# DECXCPR. Kitty answers it with a `?`-marked reply, which keeps our polling
# apart from an application's own DSR conversation instead of stealing it.
_CPR_QUERY = b"\x1b[?6n"
_CPR_PARAMETER = 6
# How long a partial escape sequence is held while it could still turn out to be
# a focus report. Short enough not to be felt on a lone Escape key.
_FOCUS_HOLD = 0.02
# struct winsize: ws_row, ws_col, ws_xpixel, ws_ypixel
_WINSIZE_ZERO = struct.pack("HHHH", 0, 0, 0, 0)


def _write_all(fd: int, data: bytes) -> None:
    view = memoryview(data)
    while view:
        view = view[os.write(fd, view) :]


def _sync_window_size(master_fd: int, stdin_fd: int) -> None:
    """Copy the window size to the child, pixel dimensions included.

    `termios.tcgetwinsize` reports only rows and columns. Dropping ws_xpixel
    and ws_ypixel leaves the child believing the terminal cannot report pixel
    geometry, which is how a child learns its cell size - without it `icat` and
    every other image-capable program refuses to draw.
    """
    size = fcntl.ioctl(stdin_fd, termios.TIOCGWINSZ, _WINSIZE_ZERO)
    fcntl.ioctl(master_fd, termios.TIOCSWINSZ, size)


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
    on_output: Callable[[bytes], bool | None] | None = None,
    output_arbiter: OutputArbiter | None = None,
    stdin_filter: Callable[[bytes], bytes] | None = None,
    focus_filter: FocusFilter | None = None,
    on_focus: Callable[[bool], None] | None = None,
    wants_tick: Callable[[], bool] | None = None,
    query_interval: float = 1 / 30,
    tick_interval: float = 1 / 60,
    hide_cursor: bool = False,
) -> int:
    """Run *command* behind a PTY and report Kitty cursor cells to a callback.

    With a *focus_filter*, an unfocused window stops polling for the caret
    immediately - Kitty does not draw one in an inactive split - but keeps
    ticking while *wants_tick* says something is still on its way off the
    screen, so what was drawn can leave the way it is meant to before the
    window falls idle.
    """
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
        # A failed exec must not fall through into the parent's loop, which is
        # what a bare execvp would do: the forked child would go on to render
        # flowers at a terminal it does not own.
        try:
            os.execvp(command[0], list(command))
        except OSError as error:
            print(f"flower-cursor: cannot run {command[0]}: {error}", file=sys.stderr)
        os._exit(127)

    parser = CursorReportParser()
    output_arbiter = output_arbiter or OutputArbiter(lambda data: _write_all(stdout_fd, data))
    selector = selectors.DefaultSelector()
    cursor_queries: deque[int | None] = deque()
    cursor_generation = 0
    cursor_visibility = CursorVisibilityFilter() if hide_cursor else None
    resize_pending = True
    now = time.monotonic()
    next_query = now
    next_tick = now
    last_tick = now
    focus_hold: float | None = None
    resize_handler = signal.getsignal(signal.SIGWINCH)

    def request_resize(_signum: int, _frame: object) -> None:
        nonlocal resize_pending
        resize_pending = True

    def on_child_sequence(event: Event) -> None:
        # The child can ask for a DECXCPR report too; its reply is marked the
        # same way ours is, so claim only as many replies as we asked for.
        if isinstance(event, DeviceStatusQuery):
            if event.private and event.parameter == _CPR_PARAMETER:
                cursor_queries.append(None)
        elif focus_filter is not None:
            # The child owns the mode for itself; Kitty has to keep reporting to
            # us either way, so re-assert it. An alternate-screen switch can
            # carry a `?1004l` in the same sequence, so it re-asserts too.
            if isinstance(event, FocusReporting):
                focus_filter.child_wants_reports = event.enabled
                pending_focus_reassert.append(FOCUS_ENABLE)
            elif isinstance(event, AltScreenChange):
                pending_focus_reassert.append(FOCUS_ENABLE)

    pending_focus_reassert: deque[bytes] = deque()
    output_arbiter.add_listener(on_child_sequence)
    signal.signal(signal.SIGWINCH, request_resize)
    try:
        tty.setraw(stdin_fd, termios.TCSAFLUSH)
        if cursor_visibility is not None:
            output_arbiter.write(CURSOR_HIDE)
        if focus_filter is not None:
            output_arbiter.write(FOCUS_ENABLE)
        _sync_window_size(master_fd, stdin_fd)
        selector.register(stdin_fd, selectors.EVENT_READ, "stdin")
        selector.register(master_fd, selectors.EVENT_READ, "master")
        while True:
            now = time.monotonic()
            focused = focus_filter is None or focus_filter.focused
            animating = focused or (wants_tick is not None and wants_tick())
            if output_arbiter.can_write:
                # The caret is only worth asking about while it is drawn, but
                # the frame timer outlives focus by however long the flowers
                # take to go; once they have, there is no timer at all and the
                # loop sleeps until the child or the user says otherwise.
                deadlines = [next_query] if focused else []
                if on_tick is not None and animating:
                    deadlines.append(next_tick)
                if focus_hold is not None:
                    deadlines.append(focus_hold)
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
                    if focus_filter is not None:
                        data, focus_changed = focus_filter.feed(data)
                        if focus_changed:
                            if focus_filter.focused:
                                # Time did not pass for the animation while it
                                # was not being drawn; resume from now instead
                                # of replaying the gap in one step.
                                now = time.monotonic()
                                last_tick = next_tick = now
                                next_query = now
                            if on_focus is not None:
                                on_focus(focus_filter.focused)
                        focus_hold = (
                            time.monotonic() + _FOCUS_HOLD if focus_filter.has_pending else None
                        )
                        if not data:
                            continue
                    passthrough, reports = parser.feed(data)
                    if stdin_filter is not None:
                        passthrough = stdin_filter(passthrough)
                    if passthrough:
                        _write_all(master_fd, passthrough)
                    for report in reports:
                        generation = cursor_queries.popleft() if cursor_queries else None
                        if generation is None:
                            _write_all(master_fd, report.raw)
                        elif generation == cursor_generation and on_cursor is not None:
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
                        if on_output is not None and on_output(data):
                            cursor_generation += 1

            if resize_pending:
                _sync_window_size(master_fd, stdin_fd)
                resize_pending = False
            now = time.monotonic()
            output_arbiter.poll(now)
            if focus_filter is not None:
                # A held partial is released once it is clear no more of it is
                # coming, so a lone Escape reaches the child a frame later
                # rather than waiting on a keystroke that may never arrive.
                if focus_hold is not None and now >= focus_hold:
                    held = focus_filter.flush()
                    focus_hold = None
                    if held:
                        _write_all(master_fd, held)
                while pending_focus_reassert and output_arbiter.can_write:
                    output_arbiter.write(pending_focus_reassert.popleft())
            focused = focus_filter is None or focus_filter.focused
            animating = focused or (wants_tick is not None and wants_tick())
            if not animating:
                waited_pid, status = os.waitpid(child_pid, os.WNOHANG)
                if waited_pid == child_pid:
                    return os.waitstatus_to_exitcode(status)
                continue
            if on_tick is not None and now >= next_tick and output_arbiter.can_write:
                on_tick(now - last_tick)
                last_tick = now
                missed_ticks = max(1, math.ceil((now - next_tick) / tick_interval))
                next_tick += missed_ticks * tick_interval
            if focused and now >= next_query and output_arbiter.can_write:
                if len(cursor_queries) < 256:
                    output_arbiter.write(_CPR_QUERY)
                    cursor_queries.append(cursor_generation)
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
            if focus_filter is not None:
                output_arbiter.write(FOCUS_DISABLE)
        selector.close()
        os.close(master_fd)
        signal.signal(signal.SIGWINCH, resize_handler)
        termios.tcsetattr(stdin_fd, termios.TCSAFLUSH, old_terminal)
