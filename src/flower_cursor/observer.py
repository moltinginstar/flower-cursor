"""Run a shell or editor under the cursor-observing PTY wrapper."""

from __future__ import annotations

import argparse
import fcntl
import math
import os
import struct
import sys
import termios
import time
from dataclasses import dataclass
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

DEFAULT_SPECIES = "anemone"
DEFAULT_MANIFEST = Path(__file__).with_name("assets") / "layered" / "manifest.json"


@dataclass(frozen=True, slots=True)
class FlowerSpec:
    name: str
    description: str
    manifest: Path


FLOWER_CATALOGUE = (
    FlowerSpec("anemone", "Pink Japanese anemone", DEFAULT_MANIFEST),
    FlowerSpec(
        "cornflower",
        "Blue cornflower",
        DEFAULT_MANIFEST.parent.parent / "cornflower" / "manifest.json",
    ),
    FlowerSpec("daisy", "White daisy", DEFAULT_MANIFEST.parent.parent / "daisy" / "manifest.json"),
    FlowerSpec(
        "crocus", "Violet crocus", DEFAULT_MANIFEST.parent.parent / "crocus" / "manifest.json"
    ),
    FlowerSpec(
        "poppy", "Icelandic poppy", DEFAULT_MANIFEST.parent.parent / "poppy" / "manifest.json"
    ),
    FlowerSpec(
        "dahlia", "Ruby-red dahlia", DEFAULT_MANIFEST.parent.parent / "dahlia" / "manifest.json"
    ),
    FlowerSpec(
        "orchid",
        "Green-and-white slipper orchid",
        DEFAULT_MANIFEST.parent.parent / "orchid" / "manifest.json",
    ),
)
FLOWER_MANIFESTS = {flower.name: flower.manifest for flower in FLOWER_CATALOGUE}
FLOWER_DESCRIPTIONS = {flower.name: flower.description for flower in FLOWER_CATALOGUE}
_FLOWER_HELP = "; ".join(f"{flower.name}: {flower.description}" for flower in FLOWER_CATALOGUE)
# Kitty's `shell` option runs a program with no arguments of its own, so the
# species a user wants in every window has to arrive some other way.
SPECIES_VARIABLE = "FLOWER_CURSOR_SPECIES"
# Same reason: the buzz has to be switchable from a Kitty config that cannot
# pass arguments. It carries a volume rather than a flag, because a separate
# on/off switch alongside a volume is two ways to say silent that can disagree.
BUZZ_VARIABLE = "FLOWER_CURSOR_BUZZ"
# What a bare `--buzz` means. Zero is off, and off is the default.
BUZZ_DEFAULT = 1.0


def selected_species(argument: str | None, *, overridden: bool) -> str | None:
    """Which flower to grow, from the command line or the environment.

    An unreadable name is worth a word but not a dead window: every Kitty
    window runs this, so a typo in a config file falls back to the default
    flower rather than leaving the user with no shell at all.
    """
    if argument is not None or overridden:
        return argument
    species = os.environ.get(SPECIES_VARIABLE) or None
    if species is not None and species not in FLOWER_MANIFESTS:
        print(f"flower-cursor: unknown flower {species!r}, using the default", file=sys.stderr)
        return None
    return species


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
        help=f"select a bundled flower species (default: {DEFAULT_SPECIES}; {_FLOWER_HELP})",
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
        "--check",
        action="store_true",
        help="report whether this machine is set up to grow flowers, then exit",
    )
    parser.add_argument("command", nargs=argparse.REMAINDER)
    return parser


def buzz_volume(argument: float | None) -> float:
    """How loud the bee is, from the command line or the environment.

    Silent unless asked for, and zero is the same silence as never asking, so
    there is one number rather than a flag and a volume that can disagree. An
    unreadable value is worth a word but not a dead window, the same way an
    unknown flower name is.
    """
    value = argument
    if value is None:
        text = os.environ.get(BUZZ_VARIABLE, "").strip()
        if not text:
            return 0.0
        try:
            value = float(text)
        except ValueError:
            print(
                f"flower-cursor: unreadable {BUZZ_VARIABLE} {text!r}, staying silent",
                file=sys.stderr,
            )
            return 0.0
    if not math.isfinite(value) or not 0.0 <= value <= 1.0:
        print(
            f"flower-cursor: buzz volume {value!r} is outside 0 to 1, staying silent",
            file=sys.stderr,
        )
        return 0.0
    return value


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.check:
        # Answered before anything opens a PTY or draws: the point of asking is
        # that something is wrong, and a report that needs a working install to
        # print is no use to the person whose install is broken.
        from .check import run_check

        return run_check()
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
    species = selected_species(args.flower, overridden=args.shedding_manifest is not None)
    selected = species or (DEFAULT_SPECIES if args.render else None)
    manifest = args.shedding_manifest or (
        FLOWER_MANIFESTS[selected] if selected is not None else None
    )
    if manifest is not None:
        # Configured as Kitty's `shell`, this wrapper is the only thing between
        # the user and a usable window, so a broken install has to hand the
        # shell over rather than leave an empty one behind.
        try:
            from .layered import LayeredCompositor, LayeredRuntime

            chosen = {"species": species} if species not in (None, DEFAULT_SPECIES) else {}
            runtime = LayeredRuntime(manifest, **chosen)
            compositor = LayeredCompositor(manifest, **chosen)
            flowers = MultiCursorFlowers(manifest, independent_limit=args.independent_cursors)
        except Exception:
            if args.kitty_shell:
                exec_fallback(list(command))
            raise
    else:
        runtime = None
        compositor = None
        flowers = None
    buzz = None
    volume = buzz_volume(args.buzz)
    if runtime is not None and volume > 0:
        # Audio is an accessory to a cursor: a backend that cannot be imported,
        # opened or started costs the user a word on stderr, never the window.
        try:
            from .buzz import BeeBuzz

            candidate = BeeBuzz(
                volume=volume,
                report=lambda message: print(message, file=sys.stderr),
            )
            buzz = candidate if candidate.start() else None
        except Exception as error:
            print(f"flower-cursor: no bee audio ({error})", file=sys.stderr)
            buzz = None
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
            if buzz is not None:
                buzz.update(runtime.bee, audible=focused)

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
        # The bee is still on its way out, but it is on its way out of a pane
        # nobody is looking at.
        if buzz is not None:
            buzz.mute()
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
        if buzz is not None:
            buzz.close()
        if renderer is not None:
            renderer.clear()
            output.flush()
        if log_file is not None:
            log_file.close()


def shell_main() -> int:
    return main(["--render", *sys.argv[1:]])


if __name__ == "__main__":
    raise SystemExit(main())
