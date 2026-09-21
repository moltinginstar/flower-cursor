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

from .geometry import ViewportGeometry
from .graphics_renderer import FlowerRenderer
from .lifecycle import CursorCell
from .multi_cursor import MultiCursorReplyFilter, MultiCursorTracker
from .multi_runtime import DEFAULT_INDEPENDENT_LIMIT, MultiCursorFlowers
from .output_arbiter import OutputArbiter
from .pty_runner import run

DEFAULT_MANIFEST = Path(__file__).with_name("assets") / "layered" / "manifest.json"


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
    parser.add_argument(
        "--shedding-manifest", type=Path, help="override the bundled layered flower assets"
    )
    parser.add_argument(
        "--independent-cursors",
        type=int,
        default=DEFAULT_INDEPENDENT_LIMIT,
        help="how many declared cursors get their own flower before copies are used",
    )
    parser.add_argument("command", nargs=argparse.REMAINDER)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    command = args.command
    if command[:1] == ["--"]:
        command = command[1:]
    if not command:
        command = [os.environ.get("SHELL", "sh")]

    log_file = args.log.open("w") if args.log is not None else None
    previous: CursorCell | None = None
    current_cell: CursorCell | None = None
    manifest = args.shedding_manifest or (DEFAULT_MANIFEST if args.render else None)
    if manifest is not None:
        from .layered import LayeredCompositor, LayeredRuntime

        runtime = LayeredRuntime(manifest)
        compositor = LayeredCompositor(manifest)
        flowers = MultiCursorFlowers(manifest, independent_limit=args.independent_cursors)
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
        if runtime is not None:
            runtime.move_to(cell)
        if cell == previous:
            return
        previous = cell
        if log_file is not None:
            log_file.write(f"{time.time():.6f} {cell.row} {cell.column}\n")
            log_file.flush()

    def on_output(data: bytes) -> bool:
        nonlocal current_cell, previous
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
                flowers.update(tracker.cells, duration)
                stamp = runtime.active.flower.identifier if runtime.active is not None else None
                visuals = flowers.visuals(visuals, stamp)
            renderer.render(visuals, current_cell, duration, viewport=viewport)
            output.flush()

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
