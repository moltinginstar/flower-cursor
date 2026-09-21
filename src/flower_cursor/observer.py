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
from .kitty_shell import exec_fallback, shell_command
from .lifecycle import CursorCell
from .multi_cursor import MultiCursorReplyFilter, MultiCursorTracker
from .multi_runtime import DEFAULT_INDEPENDENT_LIMIT, MultiCursorFlowers
from .output_arbiter import OutputArbiter
from .pty_runner import run

DEFAULT_MANIFEST = Path(__file__).with_name("assets") / "layered" / "manifest.json"
FLOWER_MANIFESTS = {
    "coral": DEFAULT_MANIFEST,
    "cornflower": DEFAULT_MANIFEST.parent.parent / "cornflower" / "manifest.json",
}


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
        "--flower", choices=tuple(FLOWER_MANIFESTS), help="select a bundled flower species"
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
    parser.add_argument("command", nargs=argparse.REMAINDER)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    command = args.command
    if command[:1] == ["--"]:
        command = command[1:]
    if not command:
        command = shell_command() if args.kitty_shell else [os.environ.get("SHELL", "sh")]

    log_file = args.log.open("w") if args.log is not None else None
    # Kitty only reports focus once it changes, and a window is focused when it
    # opens, so that is where this starts.
    focused = True
    previous: CursorCell | None = None
    current_cell: CursorCell | None = None
    manifest = (
        args.shedding_manifest
        or (FLOWER_MANIFESTS[args.flower] if args.flower is not None else None)
        or (DEFAULT_MANIFEST if args.render else None)
    )
    if manifest is not None:
        # Configured as Kitty's `shell`, this wrapper is the only thing between
        # the user and a usable window, so a broken install has to hand the
        # shell over rather than leave an empty one behind.
        try:
            from .layered import LayeredCompositor, LayeredRuntime

            selected_species = {"species": args.flower} if args.flower is not None else {}
            runtime = LayeredRuntime(manifest, **selected_species)
            compositor = LayeredCompositor(manifest, **selected_species)
            flowers = MultiCursorFlowers(manifest, independent_limit=args.independent_cursors)
        except Exception:
            if args.kitty_shell:
                exec_fallback(list(command))
            raise
    else:
        runtime = None
        compositor = None
        flowers = None
    output = sys.stdout.buffer

    def write_output(data: bytes) -> None:
        output.write(data)
        output.flush()

    output_arbiter = OutputArbiter(write_output)
    # Kitty draws the cursors an application declares and offers no way to hide
    # them, so we track them, clear Kitty's copy each tick, and answer the state
    # query from our own record.
    tracker = MultiCursorTracker()
    output_arbiter.add_listener(tracker.handle)
    reply_filter = MultiCursorReplyFilter(tracker)
    renderer = (
        FlowerRenderer(
            runtime.pose_library,
            output_arbiter.write,
            compositor=compositor,
            canvas_scale=2,
        )
        if runtime is not None
        else None
    )

    def on_cursor(cell: CursorCell) -> None:
        nonlocal current_cell, previous
        current_cell = cell
        tracker.main_cursor = cell
        # A reply still in flight when focus was lost must not call the
        # withering flower back to the caret.
        if runtime is not None and focused:
            runtime.move_to(cell)
        if cell == previous:
            return
        previous = cell
        if log_file is not None:
            log_file.write(f"{time.time():.6f} {cell.row} {cell.column}\n")
            log_file.flush()

    cwd_follower = CwdFollower()

    def on_output(data: bytes) -> bool:
        nonlocal current_cell, previous
        reported = cwd_follower.feed(data)
        if reported is not None:
            # Kitty reads the foreground process's directory when it cannot
            # trust the cursor position, and that process is us.
            try:
                os.chdir(reported)
            except OSError:
                pass
        if renderer is not None and renderer.observe_terminal_output(data):
            current_cell = previous = None
            if runtime is not None:
                runtime.reset()
            if flowers is not None:
                flowers.reset()
            return True
        return False

    def on_tick(duration: float) -> None:
        if renderer is not None:
            renderer.flush_pending()
        suppression = tracker.take_suppression()
        if suppression:
            output_arbiter.write(suppression)
        if runtime is not None and renderer is not None and current_cell is not None:
            rows, columns, width, height = struct.unpack(
                "HHHH", fcntl.ioctl(output.fileno(), termios.TIOCGWINSZ, b"\0" * 8)
            )
            if rows and columns:
                tracker.screen_size = (rows, columns)
            viewport = ViewportGeometry.measured(rows, columns, width, height)
            runtime.set_geometry(viewport, renderer.layout)
            runtime.update(duration)
            visuals = runtime.visuals()
            if flowers is not None:
                if focused:
                    flowers.update(tracker.cells, duration)
                else:
                    # Nothing is paired to a cursor any more, so the flowers
                    # left behind only have to finish leaving.
                    flowers.advance(duration)
                stamp = runtime.active.flower.identifier if runtime.active is not None else None
                visuals = flowers.visuals(visuals, stamp)
            renderer.render(visuals, current_cell, duration, viewport=viewport)
            output.flush()

    focus_filter = FocusFilter() if renderer is not None else None

    def on_focus(is_focused: bool) -> None:
        nonlocal focused
        focused = is_focused
        # Kitty draws no caret in an inactive split, so neither do we. The
        # flowers leave the only way flowers ever leave - they wither - and the
        # pane keeps drawing until they have, then goes idle. Coming back needs
        # nothing: with the old flowers gone, the next cursor report buds a new
        # one and it grows in.
        if is_focused or runtime is None:
            return
        runtime.begin_withering()
        if flowers is not None:
            flowers.begin_withering()

    def wants_tick() -> bool:
        """Whether an unfocused window still has something left to draw."""
        if runtime is None:
            return False
        return runtime.animating or (flowers is not None and flowers.animating)

    def close_graphics() -> None:
        if renderer is not None:
            renderer.clear()
            output.flush()

    try:
        return run(
            command,
            on_cursor=on_cursor if log_file is not None or runtime is not None else None,
            on_tick=on_tick if runtime is not None else None,
            on_close=close_graphics if renderer is not None else None,
            on_output=on_output if renderer is not None else None,
            output_arbiter=output_arbiter,
            stdin_filter=reply_filter.feed,
            focus_filter=focus_filter,
            on_focus=on_focus if renderer is not None else None,
            wants_tick=wants_tick if renderer is not None else None,
            query_interval=args.interval,
            tick_interval=args.render_interval,
            hide_cursor=renderer is not None,
        )
    finally:
        if renderer is not None:
            renderer.clear()
            output.flush()
        if log_file is not None:
            log_file.close()


def shell_main() -> int:
    return main(["--render", *sys.argv[1:]])


if __name__ == "__main__":
    raise SystemExit(main())
