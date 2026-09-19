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

from .graphics_renderer import FlowerRenderer
from .lifecycle import CursorCell
from .output_arbiter import OutputArbiter
from .pty_runner import run
from .runtime import FlowerCursorRuntime


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
    runtime = FlowerCursorRuntime() if args.render else None
    output = sys.stdout.buffer

    def write_output(data: bytes) -> None:
        output.write(data)
        output.flush()

    output_arbiter = OutputArbiter(write_output)
    renderer = (
        FlowerRenderer(runtime.pose_library, output_arbiter.write) if runtime is not None else None
    )

    def on_cursor(cell: CursorCell) -> None:
        nonlocal current_cell, previous
        current_cell = cell
        if runtime is not None:
            runtime.move_to(cell)
        if cell == previous:
            return
        previous = cell
        if log_file is not None:
            log_file.write(f"{time.time():.6f} {cell.row} {cell.column}\n")
            log_file.flush()

    def on_tick(duration: float) -> None:
        if runtime is not None and renderer is not None and current_cell is not None:
            runtime.update(duration)
            rows, columns, width, height = struct.unpack(
                "HHHH", fcntl.ioctl(output.fileno(), termios.TIOCGWINSZ, b"\0" * 8)
            )
            cell_size = (
                (width // columns, height // rows)
                if rows and columns and width and height
                else None
            )
            renderer.render(runtime.visuals(), current_cell, duration, cell_size)
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
            on_output=renderer.observe_terminal_output if renderer is not None else None,
            output_arbiter=output_arbiter,
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


if __name__ == "__main__":
    raise SystemExit(main())
