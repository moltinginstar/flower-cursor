"""Run a shell or editor under the cursor-observing PTY wrapper."""

from __future__ import annotations

import argparse
import fcntl
import os
import struct
import sys
import termios
import time

from .cursor_visibility import NativeCursorPolicy
from .cwd_follower import CwdFollower
from .drawing import LocalDrawing, Recipe
from .focus import FocusFilter
from .kitty_shell import exec_fallback, shell_command, user_shell
from .lifecycle import CursorCell
from .multi_cursor import MultiCursorReplyFilter, MultiCursorTracker
from .output_arbiter import OutputArbiter
from .pty_runner import run
from .screen_resets import ScreenResets
from .settings import (
    DEFAULT_MANIFEST,
    DEFAULT_SPECIES,
    FLOWER_CATALOGUE,
    FLOWER_DESCRIPTIONS,
    FLOWER_MANIFESTS,
    OFF,
    SETTINGS,
    FlowerSpec,
    Settings,
    flower_help,
    kitty_shell_requested,
    resolve,
)
from .shared import RemoteDrawing, connect, runtime_directory

# Everything a window is allowed to ask for lives in `settings`; these names
# are re-exported because the docs and the tests all learned them here.
__all__ = [
    "DEFAULT_MANIFEST",
    "DEFAULT_SPECIES",
    "FLOWER_CATALOGUE",
    "FLOWER_DESCRIPTIONS",
    "FLOWER_MANIFESTS",
    "OFF",
    "SETTINGS",
    "FlowerSpec",
    "Settings",
    "flower_help",
    "kitty_shell_requested",
    "main",
    "resolve",
    "shell_main",
]


def _version() -> str:
    """What this copy calls itself, however it was installed."""
    from importlib.metadata import PackageNotFoundError, version

    try:
        return version("flower-cursor")
    except PackageNotFoundError:
        return "unknown (running from a source tree)"


def build_parser() -> argparse.ArgumentParser:
    """The flags, every one of them generated from its `Setting`.

    Nothing here decides what a setting means or what it defaults to: a flag
    and its variable are one setting read in two places, and generating the
    flag from the same declaration the variable is read from is what keeps
    them saying the same thing.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    groups = {
        False: parser,
        True: parser.add_argument_group(
            "development",
            "Knobs for working on the cursor itself, or for trading animation "
            "smoothness against the CPU it costs.",
        ),
    }
    for setting in SETTINGS:
        groups[setting.development].add_argument(
            setting.flag,
            dest=setting.attribute,
            default=None,
            metavar=setting.metavar,
            help=setting.help,
        )
    parser.add_argument(
        "--check",
        action="store_true",
        help="report whether this machine is set up to grow flowers, then exit",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"flower-cursor {_version()}",
        help="print the version, then exit",
    )
    parser.add_argument("command", nargs=argparse.REMAINDER)
    return parser


def _given(args: argparse.Namespace) -> dict[str, str | None]:
    """The settings named on the command line, by the name they share."""
    return {setting.name: getattr(args, setting.attribute) for setting in SETTINGS}


def _resolve_command(args: argparse.Namespace) -> list[str]:
    """What to run inside the wrapper: what was asked for, or the user's shell."""
    command = args.command
    if command[:1] == ["--"]:
        command = command[1:]
    if command:
        return command
    # Started by Kitty as its `shell`, the shell we start has to be given the
    # integration Kitty would have set up itself; started by hand, it is
    # already inside an integrated shell and nesting a second one is noise.
    return shell_command() if kitty_shell_requested() else [user_shell()]


def _grow(recipe: Recipe, command: list[str], write, report):
    """Everything that draws, in this process, or the user's shell if none of it can be built.

    Configured as Kitty's `shell`, this wrapper is the only thing between the
    user and a usable window, so a broken install has to hand the shell over
    rather than leave an empty one behind.
    """
    try:
        return LocalDrawing(recipe, write, report=report)
    except Exception:
        if kitty_shell_requested():
            exec_fallback(list(command))
        raise


class _Window:
    """One wrapped window: what it knows, and what it does when told something.

    The PTY loop calls in from several directions - a cursor report, terminal
    output, a frame timer, a focus change - and each of them reads or moves
    the same handful of things. They are attributes here rather than a set of
    closures over one long function, so what a window is made of can be read
    without reading every callback that touches it.

    What draws is `drawing`: a `LocalDrawing` in this process, or a
    `RemoteDrawing` that has the shared renderer do the same work. Everything
    else here is the terminal's, and stays in the window.
    """

    def __init__(
        self,
        settings: Settings,
        *,
        recipe: Recipe,
        command: list[str],
        log_file,
        directory=None,
    ) -> None:
        self.log_file = log_file
        # Set while the child owns the terminal; see `report`.
        self._session = False
        self._held: list[str] = []
        self.output = sys.stdout.buffer
        # Kitty only reports focus once it changes, and nearly every window is
        # focused when it opens, so that is where this starts; `FocusFilter`
        # gives the guess up if nothing confirms it.
        self.focused = True
        self.previous: CursorCell | None = None
        self.current_cell: CursorCell | None = None
        self.output_arbiter = OutputArbiter(self.write_output)
        # Kitty draws the cursors an application declares and offers no way to
        # hide them, so we track them, clear Kitty's copy each tick, and answer
        # the state query from our own record.
        self.tracker = MultiCursorTracker()
        self.output_arbiter.add_listener(self.tracker.handle)
        # An application that draws its own caret hides the terminal's first,
        # and the flower is the terminal's. The extra cursors an application
        # declares are not: it asked for those to be drawn, so they keep their
        # flowers either way.
        self.native_cursor = NativeCursorPolicy()
        self.reply_filter = MultiCursorReplyFilter(self.tracker)
        self.cwd_follower = CwdFollower()
        self.screen_resets = ScreenResets()

        def take_over():
            # Mid-session, so never `_grow`: handing the window a fresh shell
            # would hang up the one the user is working in. A drawing that
            # cannot be built here leaves the window without a flower instead.
            return LocalDrawing(recipe, self.output_arbiter.write, report=self.report)

        accepted = connect(recipe, directory=directory)
        self.drawing = (
            RemoteDrawing(
                *accepted, self.output_arbiter.write, report=self.report, fallback=take_over
            )
            if accepted is not None
            else _grow(recipe, command, self.output_arbiter.write, self.report)
        )
        self.focus_filter = FocusFilter()
        self._interval = settings.poll_interval
        self._tick_interval = settings.render_interval

    def report(self, message: str) -> None:
        """Say something is wrong without drawing it over the child's screen.

        Before the child starts, stderr is the terminal and a line there is
        read like any other startup message. Once it is running, stderr is
        still the terminal, but a line written into the middle of someone's
        prompt, editor or graphics transfer is damage rather than a message,
        so it is held until the terminal is given back.
        """
        if self._session:
            self._held.append(message)
        else:
            print(message, file=sys.stderr)

    def write_output(self, data: bytes) -> None:
        self.output.write(data)
        self.output.flush()

    def on_cursor(self, cell: CursorCell) -> None:
        self.current_cell = cell
        self.tracker.main_cursor = cell
        # A reply still in flight when focus was lost, or when the caret was
        # handed to the child, must not call the withering flower back.
        if self.drawing_caret:
            self.drawing.move_to(cell)
        if cell == self.previous:
            return
        self.previous = cell
        if self.log_file is not None:
            self.log_file.write(f"{time.time():.6f} {cell.row} {cell.column}\n")
            self.log_file.flush()

    def on_output(self, data: bytes) -> bool:
        reported = self.cwd_follower.feed(data)
        if reported is not None:
            # Kitty reads the foreground process's directory when it cannot
            # trust the cursor position, and that process is us.
            try:
                os.chdir(reported)
            except OSError:
                pass
        resets = self.screen_resets.feed(data)
        if not resets:
            return False
        for sequence in resets:
            self.drawing.screen_reset(sequence)
        self.current_cell = self.previous = None
        return True

    def on_visibility(self, visible: bool) -> None:
        """Follow the child's DECTCEM requests, which say who draws the caret."""
        if self.native_cursor.child_set(visible, time.monotonic()):
            self._caret_ownership_changed()

    def _caret_ownership_changed(self) -> None:
        """Leave, or come back, now that the child has taken or dropped the caret.

        Handing the caret over is the same departure as losing focus: the
        flowers wither rather than blink out, the bee goes quiet, and the next
        cursor report after the caret comes back buds a new flower.
        """
        if not self.caret_is_ours():
            self.drawing.wither()

    def caret_is_ours(self) -> bool:
        """Whether the caret is still the flower's to draw, or the child's now."""
        return not self.native_cursor.child_draws_caret

    @property
    def drawing_caret(self) -> bool:
        """Whether this window is drawing a caret at all: ours, and in view."""
        return self.focused and self.caret_is_ours()

    def _window_size(self) -> tuple[int, int, int, int]:
        rows, columns, width, height = struct.unpack(
            "HHHH", fcntl.ioctl(self.output.fileno(), termios.TIOCGWINSZ, b"\0" * 8)
        )
        if rows and columns:
            self.tracker.screen_size = (rows, columns)
        return rows, columns, width, height

    def on_tick(self, duration: float) -> None:
        # A caret that has been gone longer than a redraw belongs to the child.
        if self.native_cursor.poll(time.monotonic()):
            self._caret_ownership_changed()
        suppression = self.tracker.take_suppression()
        if suppression:
            self.output_arbiter.write(suppression)
        cell = self.current_cell
        self.drawing.tick(
            duration,
            cell,
            self._window_size() if cell is not None else None,
            self.drawing_caret,
            tuple(self.tracker.cells) if self.drawing_caret else (),
        )
        self.output.flush()

    def on_focus(self, is_focused: bool) -> None:
        self.focused = is_focused
        # Kitty draws no caret in an inactive split, so neither do we. The
        # flowers leave the only way flowers ever leave - they wither - and the
        # pane keeps drawing until they have, then goes idle. Coming back needs
        # nothing: with the old flowers gone, the next cursor report buds a new
        # one and it grows in. The bee is still on its way out, but it is on
        # its way out of a pane nobody is looking at.
        if not is_focused:
            self.drawing.wither()

    def wants_tick(self) -> bool:
        """Whether an unfocused window still has something left to draw, or to hear."""
        return self.drawing.wants_tick()

    def close_graphics(self) -> None:
        self.drawing.close()
        self.output.flush()

    def run(self, command: list[str]) -> int:
        self._session = True
        try:
            return run(
                command,
                on_cursor=self.on_cursor,
                on_tick=self.on_tick,
                on_close=self.close_graphics,
                on_output=self.on_output,
                output_arbiter=self.output_arbiter,
                stdin_filter=self.reply_filter.feed,
                focus_filter=self.focus_filter,
                on_focus=self.on_focus,
                on_visibility=self.on_visibility,
                wants_tick=self.wants_tick,
                wants_cursor=self.caret_is_ours,
                query_interval=self._interval,
                tick_interval=self._tick_interval,
                hide_cursor=True,
            )
        finally:
            self._session = False
            self.close_graphics()
            for message in self._held:
                print(message, file=sys.stderr)
            self._held.clear()
            if self.log_file is not None:
                self.log_file.close()


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    given = _given(args)
    if args.check:
        # Answered before anything opens a PTY or draws: the point of asking is
        # that something is wrong, and a report that needs a working install to
        # print is no use to the person whose install is broken. It is given
        # the same settings a window would run with, so what it describes is
        # this invocation rather than an idealised one.
        from .check import run_check

        return run_check(resolve(given))
    settings = resolve(given)
    command = _resolve_command(args)
    volume = settings.buzz
    if volume > 0 and settings.bee is None:
        # The buzz is the bee's; there is nothing here to make the sound.
        print("flower-cursor: no bee to buzz, staying silent", file=sys.stderr)
        volume = 0.0
    window = _Window(
        settings,
        recipe=Recipe.from_settings(settings, volume),
        command=command,
        log_file=settings.log.open("w") if settings.log is not None else None,
        directory=runtime_directory(),
    )
    return window.run(command)


def shell_main() -> int:
    return main(sys.argv[1:])


if __name__ == "__main__":
    raise SystemExit(main())
