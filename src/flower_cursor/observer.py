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
from .focus import FocusFilter
from .geometry import ViewportGeometry
from .graphics_renderer import FlowerRenderer
from .kitty_shell import exec_fallback, shell_command, user_shell
from .lifecycle import CursorCell
from .multi_cursor import MultiCursorReplyFilter, MultiCursorTracker
from .multi_runtime import MultiCursorFlowers
from .output_arbiter import OutputArbiter
from .pty_runner import run
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


def _grow(settings: Settings, command: list[str]) -> tuple[object, object, MultiCursorFlowers]:
    """Everything that draws, or the user's shell if none of it can be built.

    Configured as Kitty's `shell`, this wrapper is the only thing between the
    user and a usable window, so a broken install has to hand the shell over
    rather than leave an empty one behind.
    """
    try:
        from .layered import LayeredCompositor, LayeredRuntime

        manifest = settings.manifest
        named = settings.species_name
        chosen = {"species": named} if named is not None else {}
        return (
            LayeredRuntime(
                manifest,
                bee=settings.bee is not None,
                bee_interval=settings.bee if settings.bee is not None else 0.0,
                **chosen,
            ),
            LayeredCompositor(manifest, **chosen),
            MultiCursorFlowers(manifest, independent_limit=settings.unique_flowers),
        )
    except Exception:
        if kitty_shell_requested():
            exec_fallback(list(command))
        raise


def _open_buzz(volume: float):
    """The wingbeat audio, or None if this machine will not play it.

    Audio is an accessory to a cursor: a backend that cannot be imported,
    opened or started costs the user a word on stderr, never the window.
    """
    if volume <= 0:
        return None
    try:
        from .buzz import BeeBuzz

        candidate = BeeBuzz(volume=volume, report=lambda message: print(message, file=sys.stderr))
        return candidate if candidate.start() else None
    except Exception as error:
        print(f"flower-cursor: no bee audio ({error})", file=sys.stderr)
        return None


class _Window:
    """One wrapped window: what it knows, and what it does when told something.

    The PTY loop calls in from several directions - a cursor report, terminal
    output, a frame timer, a focus change - and each of them reads or moves
    the same handful of things. They are attributes here rather than a set of
    closures over one long function, so what a window is made of can be read
    without reading every callback that touches it.
    """

    def __init__(
        self,
        settings: Settings,
        *,
        runtime,
        compositor,
        flowers: MultiCursorFlowers | None,
        buzz,
        log_file,
    ) -> None:
        self.runtime = runtime
        self.flowers = flowers
        self.buzz = buzz
        self.log_file = log_file
        self.output = sys.stdout.buffer
        # Kitty only reports focus once it changes, and a window is focused
        # when it opens, so that is where this starts.
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
        self.renderer = (
            FlowerRenderer(
                runtime.pose_library,
                self.output_arbiter.write,
                compositor=compositor,
            )
            if runtime is not None
            else None
        )
        self.focus_filter = FocusFilter() if self.renderer is not None else None
        self._interval = settings.poll_interval
        self._tick_interval = settings.render_interval

    def write_output(self, data: bytes) -> None:
        self.output.write(data)
        self.output.flush()

    def on_cursor(self, cell: CursorCell) -> None:
        self.current_cell = cell
        self.tracker.main_cursor = cell
        # A reply still in flight when focus was lost, or when the caret was
        # handed to the child, must not call the withering flower back.
        if self.runtime is not None and self.drawing_caret:
            self.runtime.move_to(cell)
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
        if self.renderer is not None and self.renderer.observe_terminal_output(data):
            self.current_cell = self.previous = None
            if self.runtime is not None:
                self.runtime.reset()
            if self.flowers is not None:
                self.flowers.reset()
            return True
        return False

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
        if self.runtime is None or self.caret_is_ours():
            return
        if self.buzz is not None:
            self.buzz.mute()
        self.runtime.begin_withering()
        if self.flowers is not None:
            self.flowers.begin_withering()

    def caret_is_ours(self) -> bool:
        """Whether the caret is still the flower's to draw, or the child's now."""
        return not self.native_cursor.child_draws_caret

    @property
    def drawing_caret(self) -> bool:
        """Whether this window is drawing a caret at all: ours, and in view."""
        return self.focused and self.caret_is_ours()

    def _viewport(self) -> ViewportGeometry | None:
        rows, columns, width, height = struct.unpack(
            "HHHH", fcntl.ioctl(self.output.fileno(), termios.TIOCGWINSZ, b"\0" * 8)
        )
        if rows and columns:
            self.tracker.screen_size = (rows, columns)
        return ViewportGeometry.measured(rows, columns, width, height)

    def on_tick(self, duration: float) -> None:
        # A caret that has been gone longer than a redraw belongs to the child.
        if self.native_cursor.poll(time.monotonic()):
            self._caret_ownership_changed()
        if self.renderer is not None:
            self.renderer.flush_pending()
        suppression = self.tracker.take_suppression()
        if suppression:
            self.output_arbiter.write(suppression)
        if self.runtime is None or self.renderer is None or self.current_cell is None:
            return
        viewport = self._viewport()
        self.runtime.set_geometry(viewport, self.renderer.layout)
        self.runtime.update(duration)
        visuals = self.runtime.visuals()
        if self.flowers is not None:
            if self.drawing_caret:
                self.flowers.update(self.tracker.cells, duration)
            else:
                # Nothing is paired to a cursor any more, so the flowers left
                # behind only have to finish leaving.
                self.flowers.advance(duration)
            active = self.runtime.active
            visuals = self.flowers.visuals(visuals, active.flower.identifier if active else None)
        self.renderer.render(visuals, self.current_cell, duration, viewport=viewport)
        self.output.flush()
        if self.buzz is not None:
            self.buzz.update(self.runtime.bee, audible=self.drawing_caret)

    def on_focus(self, is_focused: bool) -> None:
        self.focused = is_focused
        # Kitty draws no caret in an inactive split, so neither do we. The
        # flowers leave the only way flowers ever leave - they wither - and the
        # pane keeps drawing until they have, then goes idle. Coming back needs
        # nothing: with the old flowers gone, the next cursor report buds a new
        # one and it grows in.
        if is_focused or self.runtime is None:
            return
        # The bee is still on its way out, but it is on its way out of a pane
        # nobody is looking at.
        if self.buzz is not None:
            self.buzz.mute()
        self.runtime.begin_withering()
        if self.flowers is not None:
            self.flowers.begin_withering()

    def wants_tick(self) -> bool:
        """Whether an unfocused window still has something left to draw."""
        if self.runtime is None:
            return False
        return self.runtime.animating or (self.flowers is not None and self.flowers.animating)

    def close_graphics(self) -> None:
        if self.renderer is not None:
            self.renderer.clear()
            self.output.flush()

    def run(self, command: list[str]) -> int:
        drawing = self.renderer is not None
        try:
            return run(
                command,
                on_cursor=(
                    self.on_cursor
                    if self.log_file is not None or self.runtime is not None
                    else None
                ),
                on_tick=self.on_tick if self.runtime is not None else None,
                on_close=self.close_graphics if drawing else None,
                on_output=self.on_output if drawing else None,
                output_arbiter=self.output_arbiter,
                stdin_filter=self.reply_filter.feed,
                focus_filter=self.focus_filter,
                on_focus=self.on_focus if drawing else None,
                on_visibility=self.on_visibility if drawing else None,
                wants_tick=self.wants_tick if drawing else None,
                wants_cursor=self.caret_is_ours if drawing else None,
                query_interval=self._interval,
                tick_interval=self._tick_interval,
                hide_cursor=drawing,
            )
        finally:
            if self.buzz is not None:
                self.buzz.close()
            self.close_graphics()
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
    runtime, compositor, flowers = _grow(settings, command)
    volume = settings.buzz
    if volume > 0 and settings.bee is None:
        # The buzz is the bee's; there is nothing here to make the sound.
        print("flower-cursor: no bee to buzz, staying silent", file=sys.stderr)
        volume = 0.0
    window = _Window(
        settings,
        runtime=runtime,
        compositor=compositor,
        flowers=flowers,
        buzz=_open_buzz(volume),
        log_file=settings.log.open("w") if settings.log is not None else None,
    )
    return window.run(command)


def shell_main() -> int:
    return main(sys.argv[1:])


if __name__ == "__main__":
    raise SystemExit(main())
