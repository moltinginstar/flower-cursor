"""Run a shell or editor under the cursor-observing PTY wrapper."""

from __future__ import annotations

import argparse
import fcntl
import os
import struct
import sys
import termios
import time
from pathlib import Path

from .cwd_follower import CwdFollower
from .focus import FocusFilter
from .geometry import ViewportGeometry
from .graphics_renderer import FlowerRenderer
from .kitty_shell import exec_fallback, shell_command, user_shell
from .lifecycle import CursorCell
from .multi_cursor import MultiCursorReplyFilter, MultiCursorTracker
from .multi_runtime import DEFAULT_INDEPENDENT_LIMIT, MultiCursorFlowers
from .output_arbiter import OutputArbiter
from .pty_runner import run
from .settings import (
    BEE_INTERVAL_DEFAULT,
    BEE_INTERVAL_VARIABLE,
    BEE_VARIABLE,
    BUZZ_DEFAULT,
    BUZZ_VARIABLE,
    DEFAULT_MANIFEST,
    DEFAULT_SPECIES,
    FLOWER_CATALOGUE,
    FLOWER_DESCRIPTIONS,
    FLOWER_MANIFESTS,
    SPECIES_VARIABLE,
    FlowerSpec,
    bee_enabled,
    bee_interval,
    buzz_volume,
    flower_help,
    selected_species,
)

# Everything a window is allowed to ask for lives in `settings`; these names are
# re-exported because `--flower`, the docs and the tests all learned them here.
__all__ = [
    "BEE_INTERVAL_DEFAULT",
    "BEE_INTERVAL_VARIABLE",
    "BEE_VARIABLE",
    "BUZZ_DEFAULT",
    "BUZZ_VARIABLE",
    "DEFAULT_MANIFEST",
    "DEFAULT_SPECIES",
    "FLOWER_CATALOGUE",
    "FLOWER_DESCRIPTIONS",
    "FLOWER_MANIFESTS",
    "SPECIES_VARIABLE",
    "FlowerSpec",
    "bee_enabled",
    "bee_interval",
    "buzz_volume",
    "main",
    "selected_species",
    "shell_main",
]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--log", type=Path, help="write changed cursor cells to this file")
    parser.add_argument(
        "--render",
        action="store_true",
        help="render authored flower poses around the observed cursor",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=1 / 30,
        help="seconds between Kitty cursor-position queries",
    )
    parser.add_argument(
        "--render-interval",
        type=float,
        default=1 / 60,
        help="seconds between independent animation/render ticks",
    )
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument(
        "--flower",
        choices=tuple(FLOWER_MANIFESTS),
        help=f"select a bundled flower species (default: {DEFAULT_SPECIES}; {flower_help()})",
    )
    selection.add_argument(
        "--shedding-manifest", type=Path, help="override the bundled layered flower assets"
    )
    parser.add_argument(
        "--independent-cursors",
        type=int,
        default=DEFAULT_INDEPENDENT_LIMIT,
        help="how many declared cursors get their own flower before copies are used",
    )
    parser.add_argument(
        "--kitty-shell",
        action="store_true",
        help="start the user's login shell with Kitty's shell integration nested inside",
    )
    parser.add_argument(
        "--buzz",
        nargs="?",
        type=float,
        const=BUZZ_DEFAULT,
        default=None,
        metavar="VOLUME",
        help=(
            "wingbeat audio while the bee is in flight, 0 to 1, where a bare --buzz means "
            f"{BUZZ_DEFAULT:g} and 0 is silence (default: off; {BUZZ_VARIABLE}=0.4 sets it "
            "for every window; needs the optional sound extra)"
        ),
    )
    parser.add_argument(
        "--bee-every",
        type=float,
        default=None,
        metavar="SECONDS",
        help=(
            "how long the caret stays still before a bee visits, on average; the wait is "
            "randomised around it and starts over whenever the caret moves or the bee leaves "
            f"(default: {BEE_INTERVAL_DEFAULT:g}; 0 means as soon as the caret stops; "
            f"{BEE_INTERVAL_VARIABLE}=180 sets it for every window)"
        ),
    )
    parser.add_argument(
        "--no-bee",
        action="store_true",
        help=(
            "grow the flower without its visitor "
            f"({BEE_VARIABLE}=off does the same for every window)"
        ),
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="report whether this machine is set up to grow flowers, then exit",
    )
    parser.add_argument("command", nargs=argparse.REMAINDER)
    return parser


def _resolve_command(args: argparse.Namespace) -> list[str]:
    """What to run inside the wrapper: what was asked for, or the user's shell."""
    command = args.command
    if command[:1] == ["--"]:
        command = command[1:]
    if command:
        return command
    return shell_command() if args.kitty_shell else [user_shell()]


def _chosen_manifest(args: argparse.Namespace) -> tuple[Path | None, str | None]:
    """The assets this window grows, and the species name that picked them."""
    species = selected_species(args.flower, overridden=args.shedding_manifest is not None)
    selected = species or (DEFAULT_SPECIES if args.render else None)
    manifest = args.shedding_manifest or (
        FLOWER_MANIFESTS[selected] if selected is not None else None
    )
    return manifest, species


def _grow(
    manifest: Path, species: str | None, args: argparse.Namespace, command: list[str]
) -> tuple[object, object, MultiCursorFlowers]:
    """Everything that draws, or the user's shell if none of it can be built.

    Configured as Kitty's `shell`, this wrapper is the only thing between the
    user and a usable window, so a broken install has to hand the shell over
    rather than leave an empty one behind.
    """
    try:
        from .layered import LayeredCompositor, LayeredRuntime

        chosen = {"species": species} if species is not None else {}
        return (
            LayeredRuntime(
                manifest,
                bee=bee_enabled(args.no_bee),
                bee_interval=bee_interval(args.bee_every),
                **chosen,
            ),
            LayeredCompositor(manifest, **chosen),
            MultiCursorFlowers(manifest, independent_limit=args.independent_cursors),
        )
    except Exception:
        if args.kitty_shell:
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
        args: argparse.Namespace,
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
        self._interval = args.interval
        self._tick_interval = args.render_interval

    def write_output(self, data: bytes) -> None:
        self.output.write(data)
        self.output.flush()

    def on_cursor(self, cell: CursorCell) -> None:
        self.current_cell = cell
        self.tracker.main_cursor = cell
        # A reply still in flight when focus was lost must not call the
        # withering flower back to the caret.
        if self.runtime is not None and self.focused:
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

    def _viewport(self) -> ViewportGeometry | None:
        rows, columns, width, height = struct.unpack(
            "HHHH", fcntl.ioctl(self.output.fileno(), termios.TIOCGWINSZ, b"\0" * 8)
        )
        if rows and columns:
            self.tracker.screen_size = (rows, columns)
        return ViewportGeometry.measured(rows, columns, width, height)

    def on_tick(self, duration: float) -> None:
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
            if self.focused:
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
            self.buzz.update(self.runtime.bee, audible=self.focused)

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
                wants_tick=self.wants_tick if drawing else None,
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
    if args.check:
        # Answered before anything opens a PTY or draws: the point of asking is
        # that something is wrong, and a report that needs a working install to
        # print is no use to the person whose install is broken.
        from .check import run_check

        return run_check()
    command = _resolve_command(args)
    manifest, species = _chosen_manifest(args)
    runtime = compositor = flowers = None
    if manifest is not None:
        runtime, compositor, flowers = _grow(manifest, species, args, command)
    # Read even when nothing will be drawn, so an unreadable setting is still
    # worth the line on stderr that says so.
    volume = buzz_volume(args.buzz)
    if volume > 0 and not bee_enabled(args.no_bee):
        # The buzz is the bee's; there is nothing here to make the sound.
        print("flower-cursor: no bee to buzz, staying silent", file=sys.stderr)
        volume = 0.0
    window = _Window(
        args,
        runtime=runtime,
        compositor=compositor,
        flowers=flowers,
        buzz=_open_buzz(volume) if runtime is not None else None,
        log_file=args.log.open("w") if args.log is not None else None,
    )
    return window.run(command)


def shell_main() -> int:
    return main(["--render", *sys.argv[1:]])


if __name__ == "__main__":
    raise SystemExit(main())
